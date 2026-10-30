"""Compile the read-only pin dump against host stubs and check what it touches."""

import errno
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_pin_dump.c'
BUILT = ROOT / 'source/build/j72-pin-dump-20261030/j72_pin_dump.ko'
ALLOWED_IMPORTS = {
    '__ioremap_prot', '__stack_chk_fail', '_printk', 'arm64_use_ng_mappings',
    'init_uts_ns', 'iounmap', 'mutex_lock', 'mutex_unlock', 'of_address_to_resource',
    'of_device_is_compatible', 'of_find_device_by_node', 'of_find_node_opts_by_path',
    'of_machine_compatible_match', 'of_node_put', 'put_device', 'strcmp',
}

STUBS = r'''
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define __iomem
#define __init
#define __exit
#define KBUILD_MODNAME "j72_pin_dump"
#define BIT(n) (1UL << (n))
#define GENMASK(h, l) ((~0UL << (l)) & (~0UL >> (63 - (h))))
#define FIELD_GET(mask, reg) (((reg) & (mask)) >> __builtin_ctzl(mask))
#define pr_info(...) printf("LOG " __VA_ARGS__)
#define pr_err(...) printf("LOG " __VA_ARGS__)
#define module_init(fn) int (*module_init_ref)(void) = fn
#define module_exit(fn) void (*module_exit_ref)(void) = fn
#define MODULE_LICENSE(x)
#define MODULE_DESCRIPTION(x)
static const char *fault_at = "none";
static bool fault(const char *n) { return !strcmp(fault_at, n); }
struct device_node { const char *path, *compat; u64 base, size; int refs; };
struct resource { u64 start, end; };
struct device { const void *driver; int locked, refs; };
struct platform_device { struct device dev; };
struct new_utsname { const char *release; };
static struct new_utsname uts = { "7.0.12-j72baseline" };
static struct new_utsname *init_utsname(void) { return &uts; }
static int bound;
static struct device_node pins_np = { "/soc/pinctrl@20e300000", "apple,s5l8960x-pinctrl",
                                      0x20e300000ULL, 0x100000, 0 };
static struct platform_device pins_dev = { { &bound, 0, 0 } };
static u32 mmio[200];
static int mapped, reads, outside;
static bool of_machine_is_compatible(const char *c) { return !fault("machine") && !strcmp(c, "apple,j72"); }
static struct device_node *of_find_node_by_path(const char *p)
{ if (fault("no_node") || strcmp(p, pins_np.path)) return NULL; pins_np.refs++; return &pins_np; }
static void of_node_put(struct device_node *np) { if (np) np->refs--; }
static int of_device_is_compatible(const struct device_node *np, const char *c)
{ return !fault("compat") && !strcmp(np->compat, c); }
static int of_address_to_resource(struct device_node *np, int i, struct resource *r)
{ r->start = fault("base") ? 0x20f300000ULL : np->base; r->end = r->start + np->size - 1; return 0; }
static u64 resource_size(const struct resource *r) { return r->end - r->start + 1; }
static struct platform_device *of_find_device_by_node(struct device_node *np)
{ if (fault("no_dev")) return NULL; pins_dev.dev.refs++; return &pins_dev; }
static void put_device(struct device *d) { d->refs--; }
static void device_lock(struct device *d) { d->locked++; }
static void device_unlock(struct device *d) { d->locked--; }
static void *ioremap(u64 addr, size_t size)
{
    printf("EVENT ioremap 0x%llx 0x%zx\n", (unsigned long long)addr, size);
    if (!pins_dev.dev.locked) printf("VIOLATION map without the pinctrl lock\n");
    mapped++;
    return mmio;
}
static void iounmap(void *p) { mapped--; }
static u32 readl(const void *p)
{
    long off = (const char *)p - (const char *)mmio;
    if (off < 0 || off >= (long)sizeof(mmio) || off % 4) outside++;
    reads++;
    return mmio[off / 4];
}
'''

HARNESS = r'''
int main(int argc, char **argv)
{
    fault_at = argv[1];
    if (fault("unbound")) pins_dev.dev.driver = NULL;
    for (int i = 0; i < 200; i++) mmio[i] = 0x00072602;
    mmio[80] = 0x00070223; mmio[150] = 0x00070241;
    printf("RESULT %d\n", j72_pin_dump_init());
    printf("STATE reads=%d outside=%d mapped=%d locked=%d refs=%d noderefs=%d\n", reads, outside,
           mapped, pins_dev.dev.locked, pins_dev.dev.refs, pins_np.refs);
    return 0;
}
'''


class PinDumpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        module = '\n'.join(l for l in cls.source.splitlines() if not l.startswith('#include'))
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-pin-dump-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'dump.c'
        cls.binary = Path(cls.directory.name) / 'dump'
        source.write_text(STUBS + module + HARNESS)
        result = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                                 '-Wno-unused-parameter', '-Wno-format', str(source),
                                 '-o', str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def run_fixture(self, fault='none'):
        out = subprocess.run([str(self.binary), fault], capture_output=True, text=True,
                             check=True).stdout.splitlines()
        self.assertEqual([l for l in out if l.startswith('VIOLATION')], [])
        return out

    def test_reads_all_200_words_once_and_unloads_by_design(self):
        out = self.run_fixture()
        self.assertIn('RESULT -%d' % errno.ECANCELED, out)
        self.assertIn('STATE reads=200 outside=0 mapped=0 locked=0 refs=0 noderefs=0', out)
        self.assertIn('EVENT ioremap 0x20e300000 0x1000', out)
        logs = [l for l in out if l.startswith('LOG ')]
        self.assertEqual(len([l for l in logs if ': pin ' in l]), 200)
        self.assertIn('LOG j72_pin_dump: pin  80 raw=0x00070223 mode=1 data=1 periph=1 pull=0 '
                      'ie=1 grp=7 lock=0 PERIPH', out)
        self.assertIn('LOG j72_pin_dump: pin 150 raw=0x00070241 mode=0 data=1 periph=2 pull=0 '
                      'ie=1 grp=7 lock=0 PERIPH', out)
        self.assertIn('2 in a peripheral function', '\n'.join(logs))

    def test_refusals_read_nothing_and_leak_nothing(self):
        for fault in ('machine', 'no_node', 'compat', 'base', 'no_dev', 'unbound'):
            with self.subTest(fault=fault):
                out = self.run_fixture(fault)
                self.assertNotIn('RESULT 0', out)
                self.assertNotIn('RESULT -%d' % errno.ECANCELED, out)
                state = next(l for l in out if l.startswith('STATE'))
                self.assertIn('reads=0 ', state)
                self.assertIn('mapped=0 locked=0 refs=0 noderefs=0', state)

    def test_source_never_writes_hardware(self):
        for banned in ('writel', 'regmap_write', 'regmap_update', 'gpiod_', 'pinctrl_select',
                       'iowrite', '__raw_write'):
            self.assertNotIn(banned, self.source)
        self.assertEqual(re.findall(r'ioremap\([^)]*\)', self.source), ['ioremap(PINS_BASE, PINS_MAP)'])

    @unittest.skipUnless(BUILT.exists(), 'built module receipt not present')
    def test_built_module_imports_only_read_symbols(self):
        imports = set((BUILT.parent / 'imports.txt').read_text().split())
        self.assertEqual(sorted(imports - ALLOWED_IMPORTS), [])


if __name__ == '__main__':
    unittest.main()
