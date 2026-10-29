"""Compile the actual idle, unbind, shutdown and PIO helpers against MMIO stubs.

The FIFO model is a host loopback. These tests establish register order and
error handling only, not controller semantics, the input clock or signals.
"""

import errno
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_j72_spi_divider import extract_function


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_spi_ctrl.c'
BUILT = ROOT / 'source/build/j72-spi-ctrl-20261029/j72_spi_ctrl.ko'
ALLOWED_IMPORTS = (
    '__devm_add_action', '__devm_spi_alloc_controller',
    '__platform_driver_register', '__pm_runtime_idle', '__pm_runtime_resume',
    '__stack_chk_fail', '_dev_err', '_dev_info', 'alt_cb_patch_nops',
    'devm_ioremap_resource', 'devm_pm_runtime_enable',
    'devm_spi_register_controller', 'init_uts_ns', 'ktime_get',
    'of_machine_compatible_match', 'platform_driver_unregister',
    'platform_get_resource', 'spi_controller_suspend', 'spi_delay_to_ns',
    'strcmp',
)
IDLE_WRITES = ['W 0x00 0x0', 'W 0x04 0x4018', 'W 0x34 0x0', 'W 0x4c 0x0',
               'W 0x0c 0x2', 'W 0x08 0x40000f']
FINAL = ['W 0x00 0x0', 'W 0x08 0x40000f', 'R 0x00 0x0']

STUBS = r'''
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t ktime_t;
#define __iomem
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((~0U << (l)) & (~0U >> (31 - (h))))
#define FIELD_GET(mask, reg) (((reg) & (mask)) >> __builtin_ctz(mask))
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define DIV_ROUND_UP_ULL(n, d) DIV_ROUND_UP((u64)(n), (u64)(d))
#define NSEC_PER_SEC 1000000000L
#define U32_MAX 0xffffffffU
#define SZ_64K 0x10000
#define SPI_CPHA 0x1
#define SPI_CPOL 0x2
#define dev_info(dev, ...) ((void)(dev), printf("LOG " __VA_ARGS__))
#define dev_err(dev, ...) ((void)(dev), printf("LOG " __VA_ARGS__))

struct device { void *drvdata; };
struct spi_delay { u32 value; };
struct spi_transfer {
    const void *tx_buf;
    void *rx_buf;
    unsigned int len;
    u32 speed_hz;
    u32 effective_speed_hz;
    u8 bits_per_word;
    struct spi_delay word_delay;
};
struct spi_controller { void *devdata; };
struct spi_device { struct device dev; u32 max_speed_hz; u32 mode; };

static unsigned char mmio[0x50];
static u32 ctrl_reads[2], status_value;
static unsigned int ctrl_count, accesses, hw_sent, hw_read;
static int loopback, delay_result, suspend_result;
static u8 hw_fifo[0x10000];
static ktime_t now, step;

static void *spi_controller_get_devdata(struct spi_controller *ctlr)
{ return ctlr->devdata; }
static void *dev_get_drvdata(struct device *dev) { return dev->drvdata; }
static int spi_delay_to_ns(struct spi_delay *delay, struct spi_transfer *xfer)
{ (void)xfer; return delay_result ? delay_result : (int)delay->value; }
static int pm_runtime_put_sync(struct device *dev)
{ (void)dev; printf("EVENT put\n"); return 0; }
static int spi_controller_suspend(struct spi_controller *ctlr)
{ (void)ctlr; printf("EVENT suspend\n"); return suspend_result; }
static ktime_t ktime_get(void) { return now += step; }
static ktime_t ktime_add_us(ktime_t t, u64 us) { return t + (ktime_t)us * 1000; }
static int ktime_compare(ktime_t a, ktime_t b) { return a < b ? -1 : a > b; }
static void cond_resched(void) {}
static void cpu_relax(void) {}

static u32 readl(void *address)
{
    long offset = (unsigned char *)address - mmio;
    unsigned int pending = hw_sent - hw_read;
    u32 value = 0;

    if (++accesses > 100000)
        abort();
    if (offset == 0x00)
        value = ctrl_count < 2 ? ctrl_reads[ctrl_count++] : 0;
    else if (offset == 0x04)
        value = 8;
    else if (offset == 0x08)
        value = loopback ? (pending > 16 ? 16 : pending) << 11 : status_value;
    else if (offset == 0x20 && hw_read < hw_sent)
        value = hw_fifo[hw_read++];
    printf("EVENT R 0x%02lx 0x%x\n", offset, value);
    return value;
}

static void writel(u32 value, void *address)
{
    long offset = (unsigned char *)address - mmio;

    if (++accesses > 100000)
        abort();
    if (offset == 0x10)
        hw_fifo[hw_sent++ & 0xffff] = (u8)value;
    printf("EVENT W 0x%02lx 0x%x\n", offset, value);
}
'''

HARNESS = r'''
int main(int argc, char **argv)
{
    struct s8000_spi spi = { .base = mmio };
    struct spi_controller ctlr = { .devdata = &spi };
    struct device dev = { .drvdata = &ctlr };
    static u8 tx[0x10001], rx[0x10001];
    long a[10];
    int result;

    if (argc < 2 || argc > 12)
        return 2;
    for (int i = 2; i < argc; i++)
        a[i - 2] = strtol(argv[i], NULL, 0);
    if (!strcmp(argv[1], "idle") && argc == 4) {
        ctrl_reads[0] = a[0];
        ctrl_reads[1] = a[1];
        result = s8000_spi_idle(&spi, &dev);
    } else if (!strcmp(argv[1], "release") && argc == 3) {
        ctrl_reads[1] = a[0];
        s8000_spi_release(&dev);
        result = 0;
    } else if (!strcmp(argv[1], "shutdown") && argc == 4) {
        suspend_result = a[0];
        ctrl_reads[1] = a[1];
        s8000_spi_shutdown(&dev);
        result = 0;
    } else if (!strcmp(argv[1], "xfer") && argc == 12) {
        struct spi_device device = { .max_speed_hz = 1000000, .mode = a[2] };
        struct spi_transfer xfer = {
            .len = a[0], .speed_hz = a[1], .bits_per_word = a[9],
            .tx_buf = a[7] ? tx : NULL, .rx_buf = a[8] ? rx : NULL,
        };

        if (a[3] < 0)
            delay_result = a[3];
        else
            xfer.word_delay.value = a[3];
        loopback = !a[4];
        status_value = a[5];
        step = a[6];
        for (int i = 0; i < 0x10001; i++)
            tx[i] = (u8)(i * 7 + 1);
        result = s8000_spi_transfer_one(&ctlr, &device, &xfer);
        printf("SPEED %u\nRX", xfer.effective_speed_hz);
        for (unsigned int i = 0; a[8] && i < xfer.len && i < 64; i++)
            printf(" %02x", rx[i]);
        printf("\n");
    } else {
        return 2;
    }
    printf("RESULT %d\n", result);
    return 0;
}
'''


def shutdown_stub(source):
    # The shutdown helper takes a platform_device; the stub maps it to a device.
    return extract_function(
        source, 'static void s8000_spi_shutdown(struct platform_device *pdev)').replace(
        'struct platform_device *pdev', 'struct device *pdev').replace(
        'platform_get_drvdata(pdev)', 'dev_get_drvdata(pdev)').replace(
        '&pdev->dev', 'pdev')


class SpiCtrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        defines = '\n'.join(line for line in cls.source.splitlines()
                            if line.startswith('#define S8000_') or
                            line.startswith('#define J72_SPI_NCLK_HZ'))
        struct = re.search(r'^struct s8000_spi \{.*?^\};', cls.source,
                           re.MULTILINE | re.DOTALL).group()
        helpers = [extract_function(cls.source, signature) for signature in (
            'static int s8000_spi_idle(struct s8000_spi *spi, struct device *dev)',
            'static void s8000_spi_release(void *data)',
        )]
        transfer = re.search(r'^static int s8000_spi_transfer_one\(.*?^\}',
                             cls.source, re.MULTILINE | re.DOTALL).group()
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-spi-ctrl-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'ctrl.c'
        cls.binary = Path(cls.directory.name) / 'ctrl'
        source.write_text('\n'.join([STUBS, defines, struct, *helpers,
                                     shutdown_stub(cls.source), transfer, HARNESS]))
        result = subprocess.run(
            ['cc', '-std=gnu99', '-Wall', '-Wextra', '-Werror',
             '-Wno-unused-parameter', str(source), '-o', str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')

    def run_fixture(self, *args):
        result = subprocess.run([str(self.binary), *map(str, args)], check=True,
                                capture_output=True, text=True)
        lines = result.stdout.splitlines()
        events = [line.removeprefix('EVENT ') for line in lines
                  if line.startswith('EVENT ')]
        code = int(next(line for line in lines if line.startswith('RESULT ')).split()[1])
        return code, events, result.stdout

    def xfer(self, length=4, speed=12000000, mode=0, word_delay=0, fixed=None,
             step=1000, tx=1, rx=1, bits=8):
        code, events, log = self.run_fixture(
            'xfer', length, speed, mode, word_delay, int(fixed is not None),
            fixed or 0, step, tx, rx, bits)
        speed_hz = int(re.search(r'^SPEED (\d+)$', log, re.M).group(1))
        received = bytes.fromhex(re.search(r'^RX(.*)$', log, re.M).group(1))
        return code, events, log, speed_hz, received

    def test_idle_reads_then_writes_in_order_and_accepts_ctrl_zero(self):
        code, events, log = self.run_fixture('idle', 1, 0)
        self.assertEqual(code, 0)
        self.assertEqual(events, ['R 0x00 0x1', 'R 0x04 0x8', 'R 0x08 0x0',
                                  *IDLE_WRITES, 'R 0x00 0x0'])
        self.assertIn('SPI inherited ctrl=0x1 cfg=0x8 status=0\n', log)

    def test_idle_refuses_unless_ctrl_reads_back_zero(self):
        for final in (1, 0xd, 0xffffffff):
            with self.subTest(final=final):
                code, events, log = self.run_fixture('idle', 0, final)
                self.assertEqual(code, -errno.EIO)
                self.assertEqual(events[3:], [*IDLE_WRITES, f'R 0x00 {final:#x}'])
                self.assertIn(f'SPI failed to stop: ctrl={final:#x}', log)

    def test_unbind_idles_before_dropping_the_pm_reference(self):
        for final in (0, 1):
            with self.subTest(final=final):
                _, events, _ = self.run_fixture('release', final)
                self.assertEqual(events[3:], [*IDLE_WRITES, f'R 0x00 {final:#x}', 'put'])

    def test_shutdown_stops_the_queue_before_idling(self):
        _, events, log = self.run_fixture('shutdown', 0, 0)
        self.assertEqual(events[0], 'suspend')
        self.assertEqual(events[4:], [*IDLE_WRITES, 'R 0x00 0x0'])
        self.assertNotIn('shutdown failed', log)
        _, events, log = self.run_fixture('shutdown', -errno.EBUSY, 0)
        self.assertEqual(events, ['suspend'])
        self.assertIn(f'SPI shutdown failed: {-errno.EBUSY}', log)
        _, _, log = self.run_fixture('shutdown', 0, 1)
        self.assertIn(f'SPI shutdown failed: {-errno.EIO}', log)

    def test_transfer_programs_registers_in_upstream_order(self):
        for mode, cfg in ((0, 0x4018), (1, 0x401a), (2, 0x401c), (3, 0x401e)):
            with self.subTest(mode=mode):
                code, events, _, speed, received = self.xfer(
                    length=40, mode=mode, word_delay=100)
                self.assertEqual(code, 0)
                self.assertEqual(speed, 12000000)
                self.assertEqual(events[:9], [
                    'W 0x00 0x0', 'W 0x30 0x5', 'W 0x38 0x6', 'W 0x08 0x40000f',
                    f'W 0x04 {cfg:#x}', 'W 0x00 0xd', 'W 0x34 0x28', 'W 0x4c 0x28',
                    f'W 0x04 {cfg | 0x20:#x}'])
                self.assertEqual(events[-4:], [f'W 0x04 {cfg:#x}', *FINAL])
                self.assertEqual(received, bytes((i * 7 + 1) & 0xff for i in range(40)))

    def test_fifo_never_holds_more_than_one_rx_fifo_of_data(self):
        _, events, _, _, _ = self.xfer(length=40, rx=0, tx=0)
        sent = received = 0
        for event in events:
            if event.startswith('W 0x10 '):
                self.assertEqual(event, 'W 0x10 0xff')
                sent += 1
            elif event.startswith('R 0x20 '):
                received += 1
            self.assertLessEqual(sent - received, 16)
        self.assertEqual((sent, received), (40, 40))

    def test_divider_is_ceiling_of_60_mhz_and_bounded(self):
        for speed, divisor in ((60000000, 1), (12000000, 5), (10000000, 6),
                               (7000000, 9), (29312, 2047)):
            with self.subTest(speed=speed):
                code, events, _, effective, _ = self.xfer(speed=speed)
                self.assertEqual(code, 0)
                self.assertEqual(events[1], f'W 0x30 {divisor:#x}')
                self.assertEqual(effective, 60000000 // divisor)
        for speed in (29311, 1):
            with self.subTest(speed=speed):
                code, events, _, _, _ = self.xfer(speed=speed)
                self.assertEqual((code, events), (-errno.EINVAL, []))

    def test_invalid_arguments_touch_no_register(self):
        for kwargs, expected in (({'length': 0}, 0), ({'bits': 16}, -errno.EINVAL),
                                 ({'length': 0x10001}, -errno.EINVAL),
                                 ({'word_delay': -errno.EINVAL}, -errno.EINVAL)):
            with self.subTest(**kwargs):
                code, events, _, _, _ = self.xfer(**kwargs)
                self.assertEqual((code, events), (expected, []))

    def test_invalid_fifo_levels_stop_with_eio(self):
        for status in (17 << 6, 17 << 11, 1 << 11):
            with self.subTest(status=hex(status)):
                code, events, log, _, _ = self.xfer(fixed=status)
                self.assertEqual(code, -errno.EIO)
                self.assertEqual(events[9:], [f'R 0x08 {status:#x}', 'W 0x04 0x4018',
                                              *FINAL])
                self.assertIn(f'invalid PIO FIFO: tx=0 rx=0 status={status:#x}', log)

    def test_stalled_fifo_times_out_after_one_second(self):
        code, events, log, _, _ = self.xfer(length=40, fixed=0, step=300000000)
        self.assertEqual(code, -errno.ETIMEDOUT)
        self.assertEqual(sum(e == 'R 0x08 0x0' for e in events), 4)
        self.assertEqual(sum(e.startswith('W 0x10 ') for e in events), 16)
        self.assertEqual(events[-4:], ['W 0x04 0x4018', *FINAL])
        self.assertIn('PIO timeout: tx 16/40 rx 0/40 status=0', log)

    def test_binds_only_the_existing_inspection_node(self):
        self.assertEqual(re.findall(r'\.compatible\s*=\s*"([^"]+)"', self.source),
                         ['apple,j72-spi-status'])
        self.assertIn('.name = "j72-spi-ctrl",', self.source)
        self.assertIn('of_machine_is_compatible("apple,j72")', self.source)
        self.assertIn('strcmp(init_utsname()->release, "7.0.12-j72baseline")',
                      self.source)
        self.assertIn('#define J72_SPI_BASE\t\t0x20a088000ULL', self.source)
        self.assertIn('#define J72_SPI_SIZE\t\t0x4000', self.source)
        self.assertNotRegex(self.source, r'\b(module_param\w*|MODULE_DEVICE_TABLE|'
                            r'MODULE_ALIAS|request_irq|\w*_irq\w*|dma_\w+|clk_\w+|'
                            r'kexec\w*|panic|sio_\w+)\b')
        self.assertEqual((ROOT / 'probe/Makefile').read_text().splitlines().count(
            'obj-m += j72_spi_ctrl.o'), 1)

    @unittest.skipUnless(BUILT.exists(), 'local build receipt is not present')
    def test_built_module_imports_match_allowlist(self):
        imports = subprocess.run(['nm', '-u', str(BUILT)], capture_output=True,
                                 text=True, check=True).stdout.split()[1::2]
        self.assertEqual(sorted(imports), sorted(ALLOWED_IMPORTS))


if __name__ == '__main__':
    unittest.main()
