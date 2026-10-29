"""Compile the Stage C1 touch identity module against the Stage B stubs.

The Stage B stubs model the pins, PMIC and PWM. This file adds an SPI
controller, a descriptor chip select on pin 79 and a fake N1 that answers the
HBPP NOP only after reset is released. They establish software behavior only.
"""

import errno
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_j72_touch_power as power  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_touch_id.c'
BUILT = ROOT / 'source/build/j72-touch-id-20261029/j72_touch_id.ko'
NAME = 'j72_touch_id'
ALLOWED_IMPORTS = tuple(sorted(set(power.ALLOWED_IMPORTS) | {
    '__kmalloc_cache_noprof', 'gpiod_toggle_active_low',
    'kfree', 'kmalloc_caches', 'of_find_spi_controller_by_node',
    'spi_add_device', 'spi_alloc_device', 'spi_sync', 'spi_unregister_device',
    'sized_strscpy',
}))

SPI_STUBS = r'''
typedef uint8_t u8;
typedef uint16_t u16;
#define SPI_MODE_0 0
#define GFP_KERNEL 0

struct spi_controller {
    struct device dev;
    unsigned int num_chipselect;
    struct gpio_desc **cs_gpiods;
    bool use_gpio_descriptors;
};
struct spi_device {
    struct device dev;
    struct spi_controller *controller;
    unsigned int max_speed_hz, mode, bits_per_word, chip_select, cs_index_mask;
    char modalias[32];
    struct gpio_desc *cs_gpiod;
};
struct spi_transfer {
    const void *tx_buf;
    void *rx_buf;
    unsigned int len, bits_per_word, effective_speed_hz;
};

static struct device_driver ctrl_driver = { "j72-spi-ctrl" };
static struct platform_device spi_pdev = {
    { .name = "20a088000.spi-status", .driver = &ctrl_driver } };
static struct spi_controller ctlr = {
    .dev = { .name = "spi0", .parent = &spi_pdev.dev },
    .num_chipselect = 1, .use_gpio_descriptors = true };
static int ctlr_refs, spi_devices, allocations, transfers;
static u32 n1_version = 0x434d11a0;

static void put_unaligned_le16(unsigned int v, unsigned char *p)
{ p[0] = v; p[1] = v >> 8; }
static void put_unaligned_be16(unsigned int v, unsigned char *p)
{ p[0] = v >> 8; p[1] = v; }
static unsigned int get_unaligned_be16(const unsigned char *p)
{ return p[0] << 8 | p[1]; }

static void *kzalloc(size_t size, int flags)
{
    if (fault_once("kzalloc"))
        return NULL;
    allocations++;
    return calloc(1, size);
}

static void kfree(const void *p)
{
    if (p)
        allocations--;
    free((void *)p);
}

static void strscpy(char *dst, const char *src, size_t size)
{
    snprintf(dst, size, "%s", src);
}

static struct spi_controller *of_find_spi_controller_by_node(struct device_node *np)
{
    printf("EVENT ctlr_find %s\n", np->path);
    if (fault_once("ctlr_find"))
        return NULL;
    ctlr_refs++;
    return &ctlr;
}

static void spi_controller_put(struct spi_controller *c)
{
    printf("EVENT ctlr_put\n");
    if (--ctlr_refs < 0)
        violation("controller reference underflow");
}

static struct spi_device *spi_alloc_device(struct spi_controller *c)
{
    struct spi_device *spi;

    printf("EVENT spi_alloc\n");
    if (fault_once("spi_alloc"))
        return NULL;
    spi = calloc(1, sizeof(*spi));
    spi->controller = c;
    spi->dev.name = "spi0.0";
    ctlr_refs++;
    spi_devices++;
    return spi;
}

static void spi_dev_put(struct spi_device *spi)
{
    printf("EVENT spi_put\n");
    ctlr_refs--;
    spi_devices--;
    free(spi);
}

static void spi_set_chipselect(struct spi_device *spi, int idx, int cs)
{ spi->chip_select = cs; }
static void spi_set_csgpiod(struct spi_device *spi, int idx, struct gpio_desc *d)
{ spi->cs_gpiod = d; }

static void gpiod_toggle_active_low(struct gpio_desc *desc)
{
    printf("EVENT gpio_toggle %u\n", desc->hwnum);
    desc->active_low = !desc->active_low;
}

/*
 * spi_set_cs() semantics: the descriptor is only driven for chip selects in
 * cs_index_mask, and gpiolib inverts the logical value for an active-low line.
 */
static void set_cs(struct spi_device *spi, bool activate)
{
    struct gpio_desc *d = spi->cs_gpiod;
    unsigned int level = d->active_low ? !activate : activate;

    if (!(spi->cs_index_mask & 1))
        return;

    if (d->hwnum != 79 || !d->requested)
        violation("chip select is not the requested pin 79");
    if (level && !rails_up())
        violation("pin 79 driven high while a rail is off");
    pins_mmio[79] = (pins_mmio[79] & ~P_DATA) | level;
    printf("EVENT cs %u\n", level);
}

static int spi_add_device(struct spi_device *spi)
{
    printf("EVENT spi_add %s %u %u %u %u\n", spi->modalias, spi->max_speed_hz,
           spi->mode, spi->bits_per_word, spi->chip_select);
    if (!spi->cs_gpiod || !spi->cs_gpiod->active_low)
        violation("device added without an active-low descriptor chip select");
    if (fault_once("spi_add"))
        return fault_err;
    set_cs(spi, false);
    live_spi = spi;
    return 0;
}

static void spi_unregister_device(struct spi_device *spi)
{
    printf("EVENT spi_unregister\n");
    if ((pins_mmio[15] & P_DATA))
        violation("SPI device removed with reset released");
    live_spi = NULL;
    spi_dev_put(spi);
}

static void n1_reply(const unsigned char *tx, unsigned char *rx, unsigned int len)
{
    bool running = pins_mmio[15] & P_DATA;
    bool selected = !(pins_mmio[79] & P_DATA);
    unsigned int i;

    if (!running || !selected || fault("hbpp_absent"))
        return;
    if (len == 16 && tx[0] == 0x1a && tx[1] == 0xa1) {
        for (i = 0; i < len; i += 2) { rx[i] = 0x18; rx[i + 1] = 0xe1; }
    } else if (len == 8 && tx[0] == 0x1a) {
        u32 v = fault("version_zero") ? 0 : fault("version_ones") ? 0xffffffff : n1_version;

        rx[2] = v >> 8; rx[3] = v; rx[4] = v >> 24; rx[5] = v >> 16;
    }
}

static int spi_sync_transfer(struct spi_device *spi, struct spi_transfer *t, int n)
{
    const unsigned char *tx = t->tx_buf;
    char name[16];
    unsigned int i;

    printf("EVENT xfer %u", t->len);
    for (i = 0; i < t->len; i++)
        printf(" %02x", tx[i]);
    printf("\n");
    if (spi != live_spi || n != 1 || t->bits_per_word != 8 || !t->rx_buf)
        violation("transfer outside a registered device");
    if (!rails_up() || !pwm_started)
        violation("transfer without rails and clock");
    snprintf(name, sizeof(name), "xfer%d", ++transfers);
    if (fault_once(name))
        return fault_err;
    set_cs(spi, true);
    n1_reply(tx, t->rx_buf, t->len);
    set_cs(spi, false);
    t->effective_speed_hz = 12000000;
    return 0;
}

static void spi_report(void)
{
    struct gpio_desc *d = &gpio_descs[1];

    if (ctlr_refs || spi_devices || live_spi || allocations || d->active_low)
        printf("LEAK spi ctlr=%d devices=%d allocations=%d active_low=%d\n",
               ctlr_refs, spi_devices, allocations, d->active_low);
    if (released_after_power)
        printf("STATE reset_released_with_power\n");
}
'''


def patch_stubs():
    s = power.STUBS.replace('"j72_touch_power"', f'"{NAME}"')
    edits = [
        ('struct device { const char *name; const void *driver;',
         'struct device_driver { const char *name; };\n'
         'struct device { const char *name; const struct device_driver *driver; '
         'struct device *parent;'),
        ('static int bound;', 'static struct device_driver bound = { "bound" };'),
        ('struct gpio_desc { struct gpio_device *gdev; unsigned int hwnum; bool requested; };',
         'struct gpio_desc { struct gpio_device *gdev; unsigned int hwnum; '
         'bool requested, active_low; };'),
        ('enum { PMGR, PWM0, SIO_P, GPIO_PS, PINS_N, PMIC_N, PMIC2, NNODES };',
         'enum { PMGR, PWM0, SIO_P, GPIO_PS, PINS_N, PMIC_N, PMIC2, SPI_N, NNODES };'),
        ('    if (desc->hwnum == 15 && value)\n        violation("pin 15 driven high");',
         '    if (desc->hwnum == 15 && value && !(rails_up() && pwm_started && live_spi))\n'
         '        violation("pin 15 released before power, clock and SPI");\n'
         '    if (desc->hwnum == 15 && value)\n'
         '        released_after_power = true;'),
    ]
    for old, new in edits:
        if s.count(old) != 1:
            raise AssertionError(f'stub anchor changed: {old}')
        s = s.replace(old, new)
    # The SPI stubs need live_spi and pwm_started before the GPIO stubs use them.
    s = s.replace('static int gpio_held;',
                  'static int gpio_held;\nstruct spi_device;\n'
                  'static struct spi_device *live_spi;\n'
                  'static bool released_after_power;', 1)
    return s


def patch_harness():
    h = power.HARNESS
    edits = [
        ('    gpio_dev.label = "20e300000.pinctrl";',
         '    nodes[SPI_N] = (struct device_node){\n'
         '        .path = "/soc/spi-status@20a088000",\n'
         '        .compat = { "apple,j72-spi-status" }, .available = true };\n'
         '    gpio_dev.label = "20e300000.pinctrl";'),
        ('    result = j72_touch_power_init();',
         '    result = j72_touch_id_init();\n    spi_report();'),
    ]
    for old, new in edits:
        if h.count(old) != 1:
            raise AssertionError(f'harness anchor changed: {old}')
        h = h.replace(old, new)
    return h


ID_FAULTS = {
    'node_spi_missing': 'nodes[SPI_N].path = "/gone"',
    'node_spi_compat': 'nodes[SPI_N].compat[0] = "apple,s5l8960x-spi"',
    'ctlr_unbound': 'spi_pdev.dev.driver = NULL',
    'ctlr_driver': 'ctrl_driver.name = "spi-apple"',
    'ctlr_cs_table': 'ctlr.cs_gpiods = (struct gpio_desc **)&ctlr',
    'ctlr_two_cs': 'ctlr.num_chipselect = 2',
}


def named(events):
    return [e.replace('j72_touch_power', NAME) for e in events]


PREFLIGHT = named(power.PREFLIGHT)
B = named(power.SUCCESS)
HOLD = B.index('msleep 100') - 1
TEARDOWN = B.index('gpio_dir_out 15 0', HOLD)
SPI_FIND = ['ctlr_find /soc/spi-status@20a088000']
WAKE = 'xfer 2 19 c1'
NOP = 'xfer 16 1a a1' + ' 18 e1' * 7
REG_REQUEST = 'xfer 8 1c 73 8f fc 10 00 01 9b'
REG_RESULT = 'xfer 8 1a a1 18 e1 18 e1 18 e1'
IDENTIFY = (
    ['spi_alloc', 'gpio_toggle 79', 'spi_add j72-touch-id 12000000 0 8 0',
     'cs 1'] + power.pin_read(79) +
    [WAKE, 'cs 0', 'cs 1', 'usleep 10000 12000', NOP, 'cs 0', 'cs 1',
     'gpio_dir_out 15 1'] + power.pin_read(15) +
    ['usleep 15000 17000', 'gpio_get 111', NOP, 'cs 0', 'cs 1',
     REG_REQUEST, 'cs 0', 'cs 1', REG_RESULT, 'cs 0', 'cs 1', 'gpio_get 111'])
STOP = (['gpio_dir_out 15 0'] + power.pin_read(15) +
        ['spi_unregister', 'spi_put', 'gpio_toggle 79'])
SUCCESS = (SPI_FIND + B[:HOLD] + IDENTIFY + STOP + B[TEARDOWN:] + ['ctlr_put'])


class TouchIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        sources = [power.PROBE.read_text(), cls.source]
        module = '#define J72_TOUCH_POWER_LIB\n' + '\n'.join(line for text in sources for line in text.splitlines()
                           if not line.startswith('#include'))
        faults = {**power.NODE_FAULTS, **ID_FAULTS}
        body = '\n'.join(f'    if (fault("{name}")) {{ {code}; }}'
                         for name, code in faults.items())
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-touch-id-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'id.c'
        cls.binary = Path(cls.directory.name) / 'id'
        source.write_text(patch_stubs() + SPI_STUBS + '\n' + module + '\n' +
                          patch_harness().replace('    NODE_FAULTS', body))
        result = subprocess.run(
            ['cc', '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-parameter',
             '-Wno-format', '-Werror', str(source), '-o', str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')

    def run_fixture(self, fault='none', error=0, atn_low=0):
        result = subprocess.run(
            [str(self.binary), fault, str(error), hex(0xffffffff), str(atn_low)],
            check=True, capture_output=True, text=True)
        lines = result.stdout.splitlines()
        self.assertEqual([line for line in lines if line.startswith('VIOLATION')], [])
        if fault != 'none':
            self.assertIn(f'FAULT_HIT {fault}', lines)
        self.assertEqual([line for line in lines if line.startswith('LEAK')], [])
        self.assertIn('STATE registered=0 genpd=0 region=0 rpm=0 '
                      'gpio_refs=0 gpio_held=0', lines)
        events = [line.removeprefix('EVENT ') for line in lines if line.startswith('EVENT ')]
        logs = [line.removeprefix('LOG ') for line in lines if line.startswith('LOG ')]
        code = int(next(line for line in lines if line.startswith('RESULT ')).split()[1])
        return code, events, logs, lines

    def state(self, lines):
        line = next(line for line in lines if line.startswith('STATE gpio'))
        return {key: int(value, 0) for key, value in
                (item.split('=', 1) for item in line.split()[1:])}

    def check_order(self, events):
        rails = clock = device = reset = False
        for event in events:
            if event == 'update pmic 0x85 0x2 0x2':
                rails = True
            elif event == 'update pmic 0x85 0x2 0x0':
                self.assertFalse(device or reset, 'rail off with the device live')
                rails = False
            elif event == 'writel 0x20a118020 0x4003':
                clock = True
            elif event == 'writel 0x20a118020 0x4000':
                self.assertFalse(device or reset, 'clock stopped with the device live')
                clock = False
            elif event.startswith('spi_add'):
                self.assertTrue(rails and clock)
                device = True
            elif event in ('spi_unregister', 'spi_put'):
                self.assertFalse(reset, 'device removed with reset released')
                device = False
            elif event == 'gpio_dir_out 15 1':
                self.assertTrue(rails and clock and device)
                reset = True
            elif event == 'gpio_dir_out 15 0':
                reset = False
            elif event.startswith('xfer'):
                self.assertTrue(rails and clock and device)
            elif event == 'gpio_dir_out 79 1':
                self.assertTrue(rails)
        self.assertFalse(rails or clock or device or reset)

    def test_success_order_and_state(self):
        code, events, logs, lines = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(events, SUCCESS)
        self.assertEqual(self.state(lines), power.SAFE_FINAL)
        self.check_order(events)
        text = '\n'.join(logs)
        self.assertIn('HBPP detected; N1 controller 0x434d11a0', text)
        self.assertIn('stage C1 complete; no firmware loaded', text)
        self.assertRegex(text, r'final version=0x434d11a0 complete=1 .* cleanup=0')

    def test_rerun_state_is_accepted(self):
        code, events, _, _ = self.run_fixture('rerun79')
        self.assertEqual(code, 0)
        self.assertEqual(events, SUCCESS)

    def test_controller_refusals_touch_no_hardware(self):
        for fault in ['ctlr_find', 'kzalloc', *ID_FAULTS]:
            with self.subTest(fault=fault):
                code, events, logs, lines = self.run_fixture(fault, -errno.ENOMEM)
                self.assertNotEqual(code, 0)
                self.assertFalse(any(e.startswith(('lock', 'read', 'gpio', 'register',
                                                   'request_region', 'writel',
                                                   'update', 'spi', 'xfer'))
                                     for e in events), events)
                self.assertIn('refused: no idle j72-spi-ctrl controller',
                              '\n'.join(logs))

    def test_stage_b_preflight_refusals_still_hold(self):
        for fault in ['machine', 'boot15', 'boot79', 'boot84', 'boot_pmgr',
                      'node_pmic_two']:
            with self.subTest(fault=fault):
                code, events, _, _ = self.run_fixture(fault, -errno.ENXIO)
                self.assertNotEqual(code, 0)
                self.assertFalse(any(e.startswith(('gpio_dir_out', 'update pmic',
                                                   'writel', 'spi', 'xfer'))
                                     for e in events))
                self.assertEqual(events[-1], 'ctlr_put')

    def test_n1_failures_unwind_to_the_safe_state(self):
        for fault, error in (('hbpp_absent', 0), ('version_zero', 0),
                             ('version_ones', 0), ('spi_alloc', 0),
                             ('spi_add', -errno.EIO), ('xfer1', -errno.ETIMEDOUT),
                             ('xfer2', -errno.ETIMEDOUT), ('xfer3', -errno.EIO),
                             ('xfer4', -errno.EIO), ('xfer5', -errno.EIO)):
            with self.subTest(fault=fault):
                code, events, logs, lines = self.run_fixture(fault, error)
                self.assertNotEqual(code, 0)
                self.assertEqual(self.state(lines), power.SAFE_FINAL)
                self.check_order(events)
                self.assertRegex('\n'.join(logs), r'final version=\S+ complete=0 .* cleanup=0')

    def test_hbpp_absent_never_reads_the_version(self):
        _, events, logs, _ = self.run_fixture('hbpp_absent')
        self.assertNotIn(REG_REQUEST, events)
        self.assertIn('HBPP not detected', '\n'.join(logs))

    def test_every_stage_b_fault_still_unwinds(self):
        for fault in ['region', 'gpio_find', 'gpio_chip', 'gpio_req15', 'gpio_req79',
                      'gpio_req111', 'gpio_req119', 'dir15', 'dir79', 'dir119',
                      'verify15', 'verify79_low', 'verify119', 'atn', 'pdev',
                      'genpd_add', 'rpm_get', 'pmgr_on', 'pwm_ioremap',
                      'pmic_update84', 'pmic_read84_on', 'pmic_update85',
                      'pmic_read85_on', 'pwm_high', 'pwm_ctrl', 'pwm_late_ctrl',
                      'pmgr_late', 'verify79_high']:
            with self.subTest(fault=fault):
                code, events, _, lines = self.run_fixture(fault, -errno.EIO)
                self.assertNotEqual(code, 0)
                self.assertFalse(any(e.startswith(('spi_add', 'xfer')) for e in events))
                self.check_order(events)

    def test_source_reuses_stage_b_and_makes_no_firmware_or_irq_call(self):
        self.assertIn('#define J72_TOUCH_POWER_LIB\n#include "j72_touch_power.c"',
                      self.source)
        for banned in ('request_firmware', 'request_irq', 'request_threaded_irq',
                       'input_', 'regmap_write', 'pinctrl_', 'of_overlay'):
            self.assertNotIn(banned, self.source)
        self.assertEqual(len(re.findall(r'drive_pin\(tp, PIN_RESET, 1\)', self.source)), 1)

    @unittest.skipUnless(BUILT.exists(), 'built module receipt not present')
    def test_built_module_imports_only_allowed_symbols(self):
        imports = (BUILT.parent / 'imports.txt').read_text().split()
        self.assertEqual(sorted(set(imports) - set(ALLOWED_IMPORTS)), [])

    def test_makefile_builds_the_module(self):
        self.assertIn('obj-m += j72_touch_id.o', (ROOT / 'probe/Makefile').read_text())


if __name__ == '__main__':
    unittest.main()
