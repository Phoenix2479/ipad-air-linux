"""Host regression tests for the probe's actual OF preflight helper."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest


PROBE = Path(__file__).resolve().parents[1] / 'probe/j72_spi_status.c'

# API contracts: include/linux/of.h (full_name, count/read error semantics)
# and drivers/of/base.c (availability, compatible list, referenced path lookup).
# The fixture registry models only the exact absolute paths used by this probe.
STUBS = r'''
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

typedef uint32_t u32;
struct device_node {
    const char *full_name, *path, *status, *compatible;
    const unsigned char *reg;
    int reg_len, refs;
    bool reg_present, remove_reg_before_read;
};
static struct device_node node, other;
static int lookups, ref_gets, ref_puts;

const char *of_node_full_name(const struct device_node *np)
{
    return np ? np->full_name : "<no-node>";
}

struct device_node *of_find_node_by_path(const char *path)
{
    struct device_node *nodes[] = { &node, &other };
    lookups++;
    for (unsigned int i = 0; i < sizeof(nodes) / sizeof(nodes[0]); i++) {
        if (!strcmp(path, nodes[i]->path)) {
            nodes[i]->refs++;
            ref_gets++;
            return nodes[i];
        }
    }
    return NULL;
}

void of_node_put(struct device_node *np)
{
    if (np) {
        np->refs--;
        ref_puts++;
    }
}

bool of_device_is_available(const struct device_node *np)
{
    return np && (!np->status || !strcmp(np->status, "okay") ||
                  !strcmp(np->status, "ok"));
}

int of_device_is_compatible(const struct device_node *np, const char *compat)
{
    int index = 0;
    if (!np || !np->compatible)
        return 0;
    for (const char *p = np->compatible; *p; p += strlen(p) + 1, index++)
        if (!strcasecmp(p, compat))
            return INT32_MAX / 2 - (index << 2);
    return 0;
}

int of_property_count_u32_elems(const struct device_node *np, const char *name)
{
    if (strcmp(name, "reg") || !np->reg_present)
        return -EINVAL;
    if (!np->reg)
        return -ENODATA;
    if (np->reg_len % 4)
        return -EINVAL;
    return np->reg_len / 4;
}

int of_property_read_u32_array(const struct device_node *np, const char *name,
                               u32 *out, size_t count)
{
    /* Model a property removed between count and read by OF reconfiguration. */
    if (strcmp(name, "reg") || !np->reg_present || np->remove_reg_before_read)
        return -EINVAL;
    if (!np->reg)
        return -ENODATA;
    if ((size_t)np->reg_len < count * 4)
        return -EOVERFLOW;
    for (size_t i = 0; i < count; i++) {
        const unsigned char *p = np->reg + i * 4;
        out[i] = (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3];
    }
    return 0;
}
'''

HARNESS = r'''
int main(int argc, char **argv)
{
    const char *path = "/soc/power-management@20e000000/power-controller@200a8";
    unsigned char reg[] = {0, 2, 0, 0xa8, 0, 0, 0, 4, 0, 0, 0, 0};
    struct device_node *np = &node;
    u32 offset = 0x200a8;
    bool accepted;

    if (argc != 2)
        return 2;
    node = (struct device_node) {
        .full_name = "power-controller@200a8", .path = path,
        .compatible = "apple,s5l8960x-pmgr-pwrstate\0apple,pmgr-pwrstate\0",
        .reg = reg, .reg_len = 8, .reg_present = true, .refs = 1,
    };
    other = node;
    other.path = "/different-parent/power-controller@200a8";

    if (!strcmp(argv[1], "valid")) {}
    else if (!strcmp(argv[1], "dependency")) {
        path = node.path = "/soc/power-management@20e000000/power-controller@20110";
        node.full_name = "power-controller@20110";
        reg[2] = 1; reg[3] = 0x10; offset = 0x20110;
    }
    else if (!strcmp(argv[1], "same-name-other-node")) np = &other;
    else if (!strcmp(argv[1], "other-parent-path")) path = other.path;
    else if (!strcmp(argv[1], "unresolved")) path = "/missing/power-controller@200a8";
    else if (!strcmp(argv[1], "null-node")) np = NULL;
    else if (!strcmp(argv[1], "okay")) node.status = "okay";
    else if (!strcmp(argv[1], "ok")) node.status = "ok";
    else if (!strcmp(argv[1], "disabled")) node.status = "disabled";
    else if (!strcmp(argv[1], "reserved")) node.status = "reserved";
    else if (!strcmp(argv[1], "wrong-compatible")) node.compatible = "apple,pmgr-pwrstate\0";
    else if (!strcmp(argv[1], "missing-compatible")) node.compatible = NULL;
    else if (!strcmp(argv[1], "compatible-second"))
        node.compatible = "apple,pmgr-pwrstate\0apple,s5l8960x-pmgr-pwrstate\0";
    else if (!strcmp(argv[1], "missing-reg")) node.reg_present = false;
    else if (!strcmp(argv[1], "reg-no-value")) node.reg = NULL;
    else if (!strcmp(argv[1], "reg-empty")) node.reg_len = 0;
    else if (!strcmp(argv[1], "reg-one-cell")) node.reg_len = 4;
    else if (!strcmp(argv[1], "reg-three-cells")) node.reg_len = 12;
    else if (!strcmp(argv[1], "reg-malformed")) node.reg_len = 7;
    else if (!strcmp(argv[1], "reg-read-error")) node.remove_reg_before_read = true;
    else if (!strcmp(argv[1], "wrong-offset")) reg[3]++;
    else if (!strcmp(argv[1], "wrong-expected-offset")) offset++;
    else if (!strcmp(argv[1], "wrong-size")) reg[7] = 8;
    else return 2;

    accepted = power_node(np, path, offset);
    printf("%d %d %d %d %d %d\n", accepted, lookups, ref_gets, ref_puts, node.refs, other.refs);
    return 0;
}
'''


class PowerNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        match = re.search(r'^static bool power_node\([^\n]*\)\n\{.*?^\}',
                          PROBE.read_text(), re.MULTILINE | re.DOTALL)
        if match is None:
            raise AssertionError('power_node helper not found in probe source')
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-power-node-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'power_node.c'
        cls.binary = Path(cls.directory.name) / 'power_node'
        source.write_text(STUBS + '\n' + match.group() + '\n' + HARNESS)
        result = subprocess.run(
            ['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', str(source),
             '-o', str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')

    def check_fixture(self, fixture, accepted, resolved=True):
        result = subprocess.run([str(self.binary), fixture], check=True,
                                capture_output=True, text=True)
        actual, lookups, gets, puts, refs, other_refs = map(int, result.stdout.split())
        self.assertEqual(actual, int(accepted), f'{fixture}: C harness: {result.stdout.strip()}')
        self.assertEqual(lookups, 1, fixture)
        self.assertEqual((gets, puts), (int(resolved), int(resolved)), fixture)
        self.assertEqual((refs, other_refs), (1, 1), fixture)

    def test_accepts_modern_node(self):
        for fixture in ('valid', 'dependency', 'okay', 'ok', 'compatible-second'):
            with self.subTest(fixture=fixture):
                self.check_fixture(fixture, True)

    def test_refuses_different_same_named_node(self):
        for fixture in ('same-name-other-node', 'other-parent-path', 'null-node'):
            with self.subTest(fixture=fixture):
                self.check_fixture(fixture, False)

    def test_refuses_unresolved_path(self):
        self.check_fixture('unresolved', False, resolved=False)

    def test_refuses_unavailable_or_wrong_compatible(self):
        for fixture in ('disabled', 'reserved', 'wrong-compatible', 'missing-compatible'):
            with self.subTest(fixture=fixture):
                self.check_fixture(fixture, False)

    def test_refuses_wrong_or_malformed_reg(self):
        for fixture in ('missing-reg', 'reg-no-value', 'reg-empty', 'reg-one-cell',
                        'reg-three-cells', 'reg-malformed', 'reg-read-error',
                        'wrong-offset', 'wrong-expected-offset', 'wrong-size'):
            with self.subTest(fixture=fixture):
                self.check_fixture(fixture, False)


if __name__ == '__main__':
    unittest.main()
