"""Compile the actual divider operation and PM lifecycle tail with host stubs.

These tests establish control flow only, not MMIO semantics or silicon safety.
"""

import errno
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import test_j72_spi_node as node_tests


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_spi_divider.c'
BASELINE = ROOT / 'probe/j72_spi_status.c'


def extract_function(source, signature):
    match = re.search(r'^' + re.escape(signature) + r'\n\{.*?^\}',
                      source, re.MULTILINE | re.DOTALL)
    if match is None:
        raise AssertionError(f'function not found: {signature}')
    return match.group()


STUBS = r'''
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
#define __iomem
#define KBUILD_MODNAME "j72_spi_divider"
#define pr_info(...) printf(__VA_ARGS__)
#define pr_err(...) printf(__VA_ARGS__)

static unsigned char mmio[0x40];
static u32 reads[6];
static unsigned int read_count;

static u32 readl(void *address)
{
    if (read_count >= 6)
        abort();
    u32 value = reads[read_count++];
    printf("EVENT read %td %u\n", (unsigned char *)address - mmio, value);
    return value;
}

static void writel(u32 value, void *address)
{
    printf("EVENT write %td %u\n", (unsigned char *)address - mmio, value);
}

struct device { bool pm_domain; };
struct device_node { int unused; };
struct of_phandle_args { struct device_node *np; };
static struct device_node nodes[4];
static int attach_result, active_result, get_result, put_result;
static int suspended_result, detach_left;

static int genpd_dev_pm_attach(struct device *dev)
{
    printf("EVENT attach\n");
    dev->pm_domain = attach_result == 1;
    return attach_result;
}

static int pm_runtime_set_active(struct device *dev)
{ (void)dev; printf("EVENT active\n"); return active_result; }
static void pm_runtime_enable(struct device *dev)
{ (void)dev; printf("EVENT enable\n"); }
static int pm_runtime_resume_and_get(struct device *dev)
{ (void)dev; printf("EVENT get\n"); return get_result; }
static int pm_runtime_put_sync_suspend(struct device *dev)
{ (void)dev; printf("EVENT put\n"); return put_result; }
static void pm_runtime_disable(struct device *dev)
{ (void)dev; printf("EVENT disable\n"); }
static int pm_runtime_set_suspended(struct device *dev)
{ (void)dev; printf("EVENT suspended\n"); return suspended_result; }
static void dev_pm_domain_detach(struct device *dev, bool power_off)
{
    if (!power_off)
        abort();
    printf("EVENT detach\n");
    dev->pm_domain = detach_left;
}
static void iounmap(void *base)
{ if (base != mmio) abort(); printf("EVENT unmap\n"); }
static void release_mem_region(unsigned long long base, unsigned int size)
{ (void)base; (void)size; printf("EVENT region\n"); }
static void device_unlock(struct device *dev)
{ (void)dev; printf("EVENT unlock\n"); }
static void put_device(struct device *dev)
{ (void)dev; printf("EVENT device-put\n"); }
static void of_node_put(struct device_node *np)
{ printf("EVENT node-put %td\n", np - nodes); }
'''

LIFECYCLE_SETUP = r'''
static int lifecycle(void)
{
    struct device device = {0}, *dev = &device;
    struct device_node *np = &nodes[0], *parent = &nodes[1];
    struct of_phandle_args pd = { &nodes[2] }, dep = { &nodes[3] };
    void *base = mmio;
    int ret = -EINVAL, cleanup = 0, err;
'''

HARNESS = r'''
int main(int argc, char **argv)
{
    int result;
    if (argc != 8 && argc != 14)
        return 2;
    for (int i = 0; i < 6; i++)
        reads[i] = (u32)strtoul(argv[i + 2], NULL, 0);
    if (!strcmp(argv[1], "operation") && argc == 8) {
        result = divider_experiment(mmio);
    } else if (!strcmp(argv[1], "lifecycle") && argc == 14) {
        attach_result = atoi(argv[8]);
        active_result = atoi(argv[9]);
        get_result = atoi(argv[10]);
        put_result = atoi(argv[11]);
        suspended_result = atoi(argv[12]);
        detach_left = atoi(argv[13]);
        result = lifecycle();
    } else {
        return 2;
    }
    printf("RESULT %d\n", result);
    return 0;
}
'''


class DividerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        cls.baseline = BASELINE.read_text()
        cls.helper = extract_function(
            cls.source, 'static int divider_experiment(void __iomem *base)')
        cls.init = extract_function(
            cls.source, 'static int __init j72_spi_divider_init(void)')
        # Compile all actual control flow from explicit attachment through return.
        # Only the preceding DT/ownership/mapping preflight is replaced by setup.
        tail = cls.init[cls.init.index('\tret = genpd_dev_pm_attach(dev);'):]
        defines = '\n'.join(line for line in cls.source.splitlines()
                            if line.startswith('#define ') and '"' not in line)
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-divider-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'divider.c'
        cls.binary = Path(cls.directory.name) / 'divider'
        source.write_text(STUBS + '\n' + defines + '\n' + cls.helper + '\n' +
                          LIFECYCLE_SETUP + tail + '\n' + HARNESS)
        # Preflight gotos to region/unlock/nodes are outside the extracted tail.
        result = subprocess.run(
            ['cc', '-std=c99', '-Wall', '-Wextra', '-Werror',
             '-Wno-unused-label', str(source),
             '-o', str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')

    def run_fixture(self, initial=(0, 8, 0, 1), readbacks=(120, 1), pm=None):
        args = ['operation' if pm is None else 'lifecycle', *initial, *readbacks]
        if pm is not None:
            args.extend(pm)
        result = subprocess.run([str(self.binary), *map(str, args)], check=True,
                                capture_output=True, text=True)
        lines = result.stdout.splitlines()
        events = [line.removeprefix('EVENT ') for line in lines
                  if line.startswith('EVENT ')]
        code = int(next(line.removeprefix('RESULT ') for line in lines
                        if line.startswith('RESULT ')))
        return code, events, result.stdout

    def test_observed_state_exact_eight_access_order(self):
        code, events, log = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(events, [
            'read 0 0', 'read 4 8', 'read 8 0', 'read 48 1',
            'write 48 120', 'read 48 120', 'write 48 1', 'read 48 1'])
        self.assertIn('initial CLKCFG=0x00000000 CFG=0x00000008 '
                      'STATUS=0x00000000 DIV=0x00000001', log)
        self.assertIn('DIV test=0x00000078 restore=0x00000001', log)

    def test_each_unexpected_initial_input_refuses_without_writes(self):
        expected = (0, 8, 0, 1)
        for index in range(4):
            for value in (0, 1, 8, 120, 0x80000000, 0xffffffff):
                if value == expected[index]:
                    continue
                with self.subTest(index=index, value=value):
                    initial = list(expected)
                    initial[index] = value
                    code, events, _ = self.run_fixture(initial)
                    self.assertEqual(code, -errno.EIO if value == 0xffffffff
                                     else -errno.EBUSY)
                    self.assertEqual(events, [f'read {offset} {value}'
                                             for offset, value in
                                             zip((0, 4, 8, 48), initial)])

    def test_all_ones_takes_priority_over_busy(self):
        code, events, _ = self.run_fixture((1, 0, 0, 0xffffffff))
        self.assertEqual(code, -errno.EIO)
        self.assertEqual(len(events), 4)
        self.assertFalse(any(event.startswith('write') for event in events))

    def test_test_read_mismatch_always_restores(self):
        for observed in (0, 1, 0xffffffff):
            with self.subTest(observed=observed):
                code, events, log = self.run_fixture(readbacks=(observed, 1))
                self.assertEqual(code, -errno.EIO)
                self.assertEqual(events[4:], [
                    'write 48 120', f'read 48 {observed}',
                    'write 48 1', 'read 48 1'])
                self.assertIn(f'DIV test=0x{observed:08x} restore=0x00000001', log)

    def test_restore_read_mismatch_returns_error_without_retry(self):
        for restored in (0, 120, 0xffffffff):
            with self.subTest(restored=restored):
                code, events, log = self.run_fixture(readbacks=(120, restored))
                self.assertEqual(code, -errno.EIO)
                self.assertEqual(events[4:], [
                    'write 48 120', 'read 48 120',
                    'write 48 1', f'read 48 {restored}'])
                self.assertIn(f'DIV test=0x00000078 restore=0x{restored:08x}', log)

    def test_copied_cleanup_and_preflight_are_byte_identical(self):
        baseline_init = extract_function(
            self.baseline, 'static int __init j72_spi_status_init(void)')
        cleanup_marker = '\t/* The put consumes the reference'
        self.assertEqual(self.init[self.init.index(cleanup_marker):],
                         baseline_init[baseline_init.index(cleanup_marker):])
        before_operation = baseline_init[:baseline_init.index(
            '\tstatus = readl(base + STATUS);')]
        before_operation = before_operation.replace('j72_spi_status_init',
                                                    'j72_spi_divider_init')
        before_operation = before_operation.replace('\tu32 status;\n', '')
        self.assertEqual(self.init[:self.init.index(
            '\tret = divider_experiment(base);')], before_operation)
        signature = 'static bool power_node(struct device_node *np, const char *path, u32 offset)'
        self.assertEqual(extract_function(self.source, signature),
                         extract_function(self.baseline, signature))

    def test_module_has_only_the_bounded_operation(self):
        self.assertEqual(self.source.count('readl('), 6)
        self.assertEqual(self.source.count('writel('), 2)
        self.assertNotRegex(self.source, r'\b(readb|readw|readq|writeb|writew|writeq|'
                            r'module_param\w*|MODULE_ALIAS|MODULE_DEVICE_TABLE|'
                            r'module_platform_driver|request_irq|timer_setup)\s*\(')
        self.assertIn('module_init(j72_spi_divider_init);', self.source)
        self.assertIn('module_exit(j72_spi_divider_exit);', self.source)
        self.assertEqual((ROOT / 'probe/Makefile').read_text().splitlines().count(
            'obj-m += j72_spi_divider.o'), 1)

    def test_returning_operations_always_run_full_cleanup(self):
        cleanup_events = [
            'put', 'disable', 'suspended', 'detach', 'unmap', 'region',
            'unlock', 'device-put', 'node-put 3', 'node-put 1',
            'node-put 2', 'node-put 0']
        for initial, readbacks, expected in (
                ((0, 8, 0, 1), (120, 1), 0),
                ((1, 8, 0, 1), (120, 1), -errno.EBUSY),
                ((0, 8, 0, 0xffffffff), (120, 1), -errno.EIO),
                ((0, 8, 0, 1), (0xffffffff, 1), -errno.EIO),
                ((0, 8, 0, 1), (120, 0), -errno.EIO)):
            with self.subTest(initial=initial, readbacks=readbacks):
                code, events, log = self.run_fixture(
                    initial, readbacks, pm=(1, 0, 0, 0, 0, 0))
                self.assertEqual(code, expected)
                self.assertEqual(events[:4], ['attach', 'active', 'enable', 'get'])
                self.assertEqual(events[-12:], cleanup_events)
                self.assertIn(f'operation={expected}\n', log)
                self.assertIn('cleanup=0 detached=1', log)

    def test_cleanup_error_propagation_and_separate_operation_result(self):
        for initial, readbacks, put, suspended, detached, operation, cleanup in (
                ((0, 8, 0, 1), (120, 1), -11, 0, 0, 0, -11),
                ((0, 8, 0, 1), (120, 1), 0, -22, 0, 0, -22),
                ((0, 8, 0, 1), (120, 1), 0, 0, 1, 0, -errno.EBUSY),
                ((0, 8, 0, 1), (120, 1), 1, 0, 0, 0, 0),
                ((0, 8, 0, 1), (120, 1), -11, -22, 1, 0, -11),
                ((0, 8, 0, 1), (0, 1), -11, -22, 1, -errno.EIO, -11),
                ((0, 8, 0, 120), (120, 1), -11, 0, 0, -errno.EBUSY, -11)):
            with self.subTest(initial=initial, readbacks=readbacks, put=put,
                              suspended=suspended, detached=detached):
                code, events, log = self.run_fixture(
                    initial, readbacks, pm=(1, 0, 0, put, suspended, detached))
                self.assertEqual(code, operation or cleanup)
                self.assertIn(f'operation={operation}\n', log)
                self.assertIn(f'cleanup={cleanup} detached={int(not detached)}', log)
                self.assertEqual(events.count('put'), 1)
                self.assertEqual(events[-8:], [
                    'unmap', 'region', 'unlock', 'device-put',
                    'node-put 3', 'node-put 1', 'node-put 2', 'node-put 0'])

    def test_pm_setup_failures_do_not_run_operation(self):
        for pm, expected, pm_events in (
                ((0, 0, 0, 0, 0, 0), -errno.ENODEV, ['attach']),
                ((-11, 0, 0, 0, 0, 0), -11, ['attach']),
                ((1, -22, 0, 0, 0, 0), -22,
                 ['attach', 'active', 'suspended', 'detach']),
                ((1, 0, -11, 0, -22, 0), -11,
                 ['attach', 'active', 'enable', 'get', 'disable', 'suspended', 'detach'])):
            with self.subTest(pm=pm):
                code, events, log = self.run_fixture(pm=pm)
                self.assertEqual(code, expected)
                self.assertEqual(events, pm_events + [
                    'unmap', 'region', 'unlock', 'device-put',
                    'node-put 3', 'node-put 1', 'node-put 2', 'node-put 0'])
                self.assertNotIn('operation=', log)


class DividerPowerNodeTests(node_tests.PowerNodeTests):
    """Run the unchanged baseline OF fixtures against the new source copy."""

    @classmethod
    def setUpClass(cls):
        # The existing fixture compiler reads this module global during setup.
        # Restore it immediately so the baseline's own discovery tests stay intact.
        original = node_tests.PROBE
        try:
            node_tests.PROBE = PROBE
            super().setUpClass()
        finally:
            node_tests.PROBE = original


if __name__ == '__main__':
    unittest.main()
