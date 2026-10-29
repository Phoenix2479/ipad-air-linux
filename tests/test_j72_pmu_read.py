"""Compile the actual register-read helper against host stubs.

These tests establish read order and error handling only, not PMIC behavior.
"""

import errno
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_j72_spi_divider import extract_function


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_pmu_read.c'
BUILT = ROOT / 'source/build/j72-pmu-read-20261029/j72_pmu_read.ko'
ALLOWED_IMPORTS = (
    '__stack_chk_fail', '_printk', 'dev_get_regmap', 'i2c_find_device_by_fwnode',
    'init_uts_ns', 'mutex_lock', 'mutex_unlock', 'of_device_is_available',
    'of_device_is_compatible', 'of_find_compatible_node',
    'of_machine_compatible_match', 'of_node_get', 'of_node_put',
    'of_property_read_variable_u32_array', 'put_device', 'regmap_get_val_bytes',
    'regmap_read', 'strcmp',
)

STUBS = r'''
#include <stdio.h>
#include <stdlib.h>

#define ARRAY_SIZE(x) ((int)(sizeof(x) / sizeof((x)[0])))

struct regmap { int unused; };
static int fail_at = -1, error, read_count;

static int regmap_read(struct regmap *map, unsigned int reg, unsigned int *val)
{
    (void)map;
    if (read_count >= 3)
        abort();
    printf("EVENT read 0x%x\n", reg);
    if (read_count++ == fail_at)
        return error;
    *val = 0x100 + reg;
    return 0;
}
'''

HARNESS = r'''
int main(int argc, char **argv)
{
    struct regmap map;
    unsigned int values[3] = {0, 0, 0};
    int result;

    if (argc != 3)
        return 2;
    fail_at = atoi(argv[1]);
    error = atoi(argv[2]);
    result = read_registers(&map, values);
    printf("RESULT %d %u %u %u\n", result, values[0], values[1], values[2]);
    return 0;
}
'''


class PmuReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        table = re.search(r'^struct pmu_reg \{.*?^\};\n\n'
                          r'static const struct pmu_reg pmu_regs\[\] = \{.*?^\};',
                          cls.source, re.MULTILINE | re.DOTALL)
        helper = extract_function(
            cls.source,
            'static int read_registers(struct regmap *map, unsigned int *values)')
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-pmu-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'pmu.c'
        cls.binary = Path(cls.directory.name) / 'pmu'
        source.write_text(STUBS + '\n' + table.group() + '\n' + helper +
                          '\n' + HARNESS)
        result = subprocess.run(
            ['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', str(source),
             '-o', str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')

    def run_fixture(self, fail_at=-1, error=0):
        result = subprocess.run([str(self.binary), str(fail_at), str(error)],
                                check=True, capture_output=True, text=True)
        lines = result.stdout.splitlines()
        events = [line.removeprefix('EVENT ') for line in lines
                  if line.startswith('EVENT ')]
        code, *values = map(int, next(
            line for line in lines if line.startswith('RESULT ')).split()[1:])
        return code, events, values

    def test_reads_each_register_once_in_order(self):
        code, events, values = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(events, ['read 0x84', 'read 0x85', 'read 0x398'])
        self.assertEqual(values, [0x184, 0x185, 0x498])

    def test_first_error_is_returned_and_stops_reads(self):
        for index in range(3):
            for error in (-errno.EIO, -errno.ENXIO):
                with self.subTest(index=index, error=error):
                    code, events, values = self.run_fixture(index, error)
                    self.assertEqual(code, error)
                    self.assertEqual(len(events), index + 1)
                    self.assertEqual(values[index:], [0] * (3 - index))

    def test_module_has_no_write_capable_api(self):
        self.assertNotRegex(
            self.source,
            r'\b(regmap_write|regmap_update_bits\w*|regmap_bulk_write|'
            r'regmap_raw_write|regmap_multi_reg_write\w*|regmap_field_write|'
            r'write[bwlq]|i2c_transfer|i2c_smbus_write\w*|'
            r'i2c_master_send)\b')
        self.assertEqual(self.source.count('regmap_read('), 1)

    @unittest.skipUnless(BUILT.exists(), 'local build receipt is not present')
    def test_built_module_imports_only_read_symbols(self):
        imports = subprocess.run(['nm', '-u', str(BUILT)], capture_output=True,
                                 text=True, check=True).stdout.split()[1::2]
        self.assertEqual(sorted(imports), sorted(ALLOWED_IMPORTS))

    def test_preflight_and_makefile(self):
        self.assertIn('of_machine_is_compatible("apple,j72")', self.source)
        self.assertIn('strcmp(init_utsname()->release, "7.0.12-j72baseline")',
                      self.source)
        self.assertEqual((ROOT / 'probe/Makefile').read_text().splitlines().count(
            'obj-m += j72_pmu_read.o'), 1)


if __name__ == '__main__':
    unittest.main()
