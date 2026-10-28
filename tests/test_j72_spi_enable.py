"""Host control-flow tests of the actual enable helper and copied PM tail.

These stubs establish no MMIO semantics, physical safety, or clock behavior.
"""

import errno
import itertools
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_j72_spi_divider as divider_tests
import test_j72_spi_node as node_tests


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_spi_enable.c'
BASELINE = ROOT / 'probe/j72_spi_divider.c'
extract_function = divider_tests.extract_function
STUBS = (divider_tests.STUBS.replace('j72_spi_divider', 'j72_spi_enable')
         .replace('reads[6]', 'reads[8]').replace('read_count >= 6', 'read_count >= 8'))
LIFECYCLE_SETUP = divider_tests.LIFECYCLE_SETUP + r'''
    /* Preflight jumps are outside this tail but its labels remain unchanged. */
    if (0) { goto region; goto unlock; goto nodes; }
'''
HARNESS = r'''
int main(int argc, char **argv)
{
    int result;
    if (argc != 10 && argc != 16)
        return 2;
    for (int i = 0; i < 8; i++)
        reads[i] = (u32)strtoul(argv[i + 2], NULL, 0);
    if (!strcmp(argv[1], "operation") && argc == 10) {
        result = enable_experiment(mmio);
    } else if (!strcmp(argv[1], "lifecycle") && argc == 16) {
        attach_result = atoi(argv[10]);
        active_result = atoi(argv[11]);
        get_result = atoi(argv[12]);
        put_result = atoi(argv[13]);
        suspended_result = atoi(argv[14]);
        detach_left = atoi(argv[15]);
        result = lifecycle();
    } else {
        return 2;
    }
    printf("RESULT %d\n", result);
    return 0;
}
'''


def compile_host(cls, source_text, stem):
    cls.directory = tempfile.TemporaryDirectory(prefix='j72-enable-')
    cls.addClassCleanup(cls.directory.cleanup)
    source = Path(cls.directory.name) / f'{stem}.c'
    cls.binary = Path(cls.directory.name) / stem
    source.write_text(source_text)
    result = subprocess.run(
        ['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', str(source),
         '-o', str(cls.binary)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')


class EnableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        cls.baseline = BASELINE.read_text()
        cls.helper = extract_function(
            cls.source, 'static int enable_experiment(void __iomem *base)')
        cls.init = extract_function(
            cls.source, 'static int __init j72_spi_enable_init(void)')
        tail = cls.init[cls.init.index('\tret = genpd_dev_pm_attach(dev);'):]
        defines = '\n'.join(line for line in cls.source.splitlines()
                            if line.startswith('#define ') and '"' not in line)
        compile_host(cls, STUBS + '\n' + defines + '\n' + cls.helper + '\n' +
                     LIFECYCLE_SETUP + tail + '\n' + HARNESS, 'enable')

    def run_fixture(self, initial=(0, 8, 0, 1), readbacks=(120, 13, 0, 1), pm=None):
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

    def test_exact_successful_twelve_access_order(self):
        code, events, log = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(events, [
            'read 0 0', 'read 4 8', 'read 8 0', 'read 48 1',
            'write 48 120', 'read 48 120', 'write 0 13', 'read 0 13',
            'write 0 0', 'read 0 0', 'write 48 1', 'read 48 1'])
        self.assertEqual(sum(e.startswith('read') for e in events), 8)
        self.assertEqual(sum(e.startswith('write') for e in events), 4)
        self.assertIn('initial CLKCFG=0x00000000 CFG=0x00000008 '
                      'STATUS=0x00000000 DIV=0x00000001', log)
        self.assertIn('stage=readbacks DIV test=0x00000078 CLKCFG '
                      'enable=0x0000000d disable=0x00000000 DIV restore=0x00000001', log)

    def test_each_unexpected_initial_field_has_zero_writes(self):
        expected = (0, 8, 0, 1)
        for index in range(4):
            for value in (0, 1, 8, 13, 120, 0x80000000, 0xffffffff):
                if value == expected[index]:
                    continue
                with self.subTest(index=index, value=value):
                    initial = list(expected)
                    initial[index] = value
                    code, events, _ = self.run_fixture(initial)
                    self.assertEqual(code, -errno.EIO if value == 0xffffffff
                                     else -errno.EBUSY)
                    self.assertEqual(events, [f'read {offset} {observed}'
                                             for offset, observed in
                                             zip((0, 4, 8, 48), initial)])

    def test_all_ones_has_priority_over_other_initial_deviations(self):
        for index in range(4):
            initial = [1, 0, 1, 120]
            initial[index] = 0xffffffff
            code, events, _ = self.run_fixture(initial)
            self.assertEqual(code, -errno.EIO)
            self.assertEqual(len(events), 4)
            self.assertFalse(any(e.startswith('write') for e in events))

    def test_bad_div_read_only_restores_div_without_clk_writes(self):
        for observed, restored in itertools.product((0, 1, 119, 0xffffffff),
                                                    (0, 1, 120, 0xffffffff)):
            with self.subTest(observed=observed, restored=restored):
                # The restore is the next read on this shorter branch.
                code, events, log = self.run_fixture(
                    readbacks=(observed, restored, 0, 0))
                self.assertEqual(code, -errno.EIO)
                self.assertEqual(events[4:], [
                    'write 48 120', f'read 48 {observed}',
                    'write 48 1', f'read 48 {restored}'])
                self.assertIn(f'stage=div_test DIV test=0x{observed:08x} '
                              f'restore=0x{restored:08x} enable_skipped=1', log)

    def test_every_returning_enable_read_immediately_disables(self):
        for enabled, disabled, restored in itertools.product(
                (0, 1, 13, 0xffffffff), (0, 1, 13, 0xffffffff),
                (0, 1, 120, 0xffffffff)):
            with self.subTest(enabled=enabled, disabled=disabled, restored=restored):
                code, events, log = self.run_fixture(
                    readbacks=(120, enabled, disabled, restored))
                self.assertEqual(events[4:10], [
                    'write 48 120', 'read 48 120', 'write 0 13',
                    f'read 0 {enabled}', 'write 0 0', f'read 0 {disabled}'])
                # Even a log between these accesses would delay the disable.
                self.assertIn(f'EVENT read 0 {enabled}\nEVENT write 0 0\n'
                              f'EVENT read 0 {disabled}\n', log)
                if disabled:
                    self.assertEqual(len(events), 10)
                    self.assertIn('disable_unconfirmed=1 div_restore_skipped=1', log)
                    self.assertNotIn('DIV restore=', log)
                else:
                    self.assertEqual(events[10:], ['write 48 1', f'read 48 {restored}'])
                    self.assertIn(f'DIV restore=0x{restored:08x}', log)
                self.assertEqual(code, 0 if (enabled, disabled, restored) ==
                                 (13, 0, 1) else -errno.EIO)

    def test_preflight_and_pm_cleanup_copy_are_exact(self):
        expected_init = extract_function(
            self.baseline, 'static int __init j72_spi_divider_init(void)')
        expected_init = (expected_init.replace('j72_spi_divider_init',
                                               'j72_spi_enable_init')
                         .replace('divider_experiment(base)', 'enable_experiment(base)'))
        self.assertEqual(self.init, expected_init)
        marker = 'static bool power_node('
        self.assertEqual(self.source[:self.source.index(marker)],
                         self.baseline[:self.baseline.index(marker)].replace(
                             '#define TEST_DIV 120\n',
                             '#define TEST_DIV 120\n#define TEST_CLKCFG 0xd\n'))
        signature = 'static bool power_node(struct device_node *np, const char *path, u32 offset)'
        self.assertEqual(extract_function(self.source, signature),
                         extract_function(self.baseline, signature))

    def test_only_the_bounded_module_operation_exists(self):
        self.assertEqual(self.source.count('readl('), 9)  # Includes alternate restore branch.
        self.assertEqual(self.source.count('writel('), 5)
        self.assertNotRegex(self.source, r'\b(readb|readw|readq|writeb|writew|writeq|'
                            r'module_param\w*|MODULE_ALIAS|MODULE_DEVICE_TABLE|'
                            r'module_platform_driver|platform_driver_register|'
                            r'request_irq|timer_setup|INIT_WORK|schedule_work|'
                            r'udelay|mdelay|msleep|usleep_range)\s*\(')
        self.assertNotRegex(self.helper, r'\b(for|while|do)\b')
        self.assertIn('module_init(j72_spi_enable_init);', self.source)
        self.assertIn('module_exit(j72_spi_enable_exit);', self.source)
        self.assertEqual((ROOT / 'probe/Makefile').read_text().splitlines().count(
            'obj-m += j72_spi_enable.o'), 1)

    def test_all_returning_operation_branches_run_full_pm_cleanup(self):
        cleanup_events = [
            'put', 'disable', 'suspended', 'detach', 'unmap', 'region',
            'unlock', 'device-put', 'node-put 3', 'node-put 1',
            'node-put 2', 'node-put 0']
        cases = [((0, 8, 0, 1), (120, 13, 0, 1), 0),
                 ((1, 8, 0, 1), (120, 13, 0, 1), -errno.EBUSY),
                 ((0, 8, 0, 0xffffffff), (120, 13, 0, 1), -errno.EIO)]
        cases += [((0, 8, 0, 1), values, -errno.EIO) for values in (
            (0xffffffff, 1, 0, 0), (0, 0xffffffff, 0, 0),
            (120, 0, 0, 1), (120, 0xffffffff, 0, 1),
            (120, 13, 13, 1), (120, 0xffffffff, 0xffffffff, 1),
            (120, 13, 0, 120), (120, 13, 0, 0xffffffff))]
        for initial, readbacks, expected in cases:
            with self.subTest(initial=initial, readbacks=readbacks):
                code, events, log = self.run_fixture(
                    initial, readbacks, pm=(1, 0, 0, 0, 0, 0))
                self.assertEqual(code, expected)
                self.assertEqual(events[:4], ['attach', 'active', 'enable', 'get'])
                self.assertEqual(events[-12:], cleanup_events)
                self.assertIn(f'operation={expected}\n', log)
                self.assertIn('cleanup=0 detached=1', log)

    def test_cleanup_errors_remain_separate_and_operation_error_wins(self):
        for initial, readbacks, operation in (
                ((0, 8, 0, 1), (120, 13, 0, 1), 0),
                ((1, 8, 0, 1), (120, 13, 0, 1), -errno.EBUSY),
                ((0, 8, 0, 1), (0xffffffff, 1, 0, 0), -errno.EIO),
                ((0, 8, 0, 1), (120, 0xffffffff, 0, 1), -errno.EIO),
                ((0, 8, 0, 1), (120, 13, 0xffffffff, 1), -errno.EIO),
                ((0, 8, 0, 1), (120, 13, 0, 0xffffffff), -errno.EIO)):
            for put, suspended, detached, cleanup in (
                    (-11, 0, 0, -11), (0, -22, 0, -22),
                    (0, 0, 1, -errno.EBUSY), (1, 0, 0, 0),
                    (-11, -22, 1, -11)):
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

    def test_pm_setup_failures_never_run_mmio(self):
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


class EnablePowerNodeTests(node_tests.PowerNodeTests):
    """Reuse every baseline OF case without changing its module globals."""

    @classmethod
    def setUpClass(cls):
        signature = 'static bool power_node(struct device_node *np, const char *path, u32 offset)'
        helper = extract_function(PROBE.read_text(), signature)
        compile_host(cls, node_tests.STUBS + '\n' + helper + '\n' +
                     node_tests.HARNESS, 'power_node')


if __name__ == '__main__':
    unittest.main()
