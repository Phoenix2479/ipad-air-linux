"""Compile the Stage C2 firmware boot module against the C1 and Stage B stubs.

The fake N1 here understands the upload framing, ACKs, register writes,
execute and the E2/E3/E6/E7 report exchange. It checks each upload's byte
swap and checksums against the files the stub firmware loader returns, so a
framing slip fails the run. It establishes software behavior only.
"""

import errno
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_j72_touch_id as c1  # noqa: E402
import test_j72_touch_power as power  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_touch_fw.c'
BUILT = ROOT / 'source/build/j72-touch-fw-20261029/j72_touch_fw.ko'
NAME = 'j72_touch_fw'
ALLOWED_IMPORTS = tuple(sorted(set(c1.ALLOWED_IMPORTS) | {
    '__const_udelay', '__kmalloc_large_noprof', 'jiffies', 'kmemdup_noprof',
    'memchr_inv', 'memcmp', 'memcpy', 'release_firmware',
    'request_firmware_direct',
}))

FW_STUBS = r'''
#define max_t(type, a, b) ((type)(a) > (type)(b) ? (type)(a) : (type)(b))
#define msecs_to_jiffies(ms) ((unsigned long)(ms))
#define time_before(a, b) ((long)((a) - (b)) < 0)
#define MODULE_FIRMWARE(x)
typedef int16_t s16;

struct firmware { size_t size; const u8 *data; };

static unsigned char fw_image[57016], cal_image[1024];
static int files_held;
static bool fw_executed, blob_pending, reg_pending, control_response;
static u32 read_address;

static unsigned int get_unaligned_le16(const unsigned char *p) { return p[0] | p[1] << 8; }
static u32 get_unaligned_le32(const unsigned char *p)
{ return p[0] | p[1] << 8 | p[2] << 16 | (u32)p[3] << 24; }
static void put_unaligned_le32(u32 v, unsigned char *p)
{ p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static u32 get32(const unsigned char *p)
{ return (u32)p[2] << 24 | (u32)p[3] << 16 | (u32)p[0] << 8 | p[1]; }
static void put32(unsigned char *p, u32 v)
{ p[0] = v >> 8; p[1] = v; p[2] = v >> 24; p[3] = v >> 16; }
static u32 sum(const unsigned char *p, size_t n)
{ u32 s = 0; while (n--) s += *p++; return s; }

static void udelay(unsigned int us) { printf("EVENT udelay %u\n", us); }

static void *kmemdup(const void *src, size_t len, int flags)
{
    void *p;

    if (fault_once("kmemdup"))
        return NULL;
    allocations++;
    p = malloc(len);
    memcpy(p, src, len);
    return p;
}

static const void *memchr_inv(const void *p, int c, size_t n)
{
    const unsigned char *s = p;

    for (size_t i = 0; i < n; i++)
        if (s[i] != (unsigned char)c)
            return s + i;
    return NULL;
}

static void build_images(void)
{
    size_t payload = sizeof(fw_image) - 16, i;

    fw_image[0] = 0x18; fw_image[1] = 0xe1; fw_image[2] = 0x30; fw_image[3] = 0x01;
    fw_image[4] = (payload / 4) >> 8; fw_image[5] = payload / 4;
    for (i = 0; i < payload; i++)
        fw_image[12 + i] = (unsigned char)(i * 13 + 5);
    fw_image[10] = sum(fw_image + 4, 6) >> 8;
    fw_image[11] = sum(fw_image + 4, 6);
    put32(fw_image + 12 + payload, sum(fw_image + 12, payload));
    for (i = 0; i < sizeof(cal_image); i++)
        cal_image[i] = (unsigned char)(i * 7 + 3);
}

static int request_firmware_direct(const struct firmware **out, const char *name,
                                   struct device *dev)
{
    struct firmware *fw;
    bool is_fw = !strcmp(name, "apple/j72-n1.bin");

    printf("EVENT request_firmware %s\n", name);
    *out = NULL;
    if (dev != &ctlr.dev)
        violation("firmware requested for an unexpected device");
    if (fault_once(is_fw ? "fw_missing" : "cal_missing"))
        return -ENOENT;
    fw = calloc(1, sizeof(*fw));
    fw->data = is_fw ? fw_image : cal_image;
    fw->size = is_fw ? sizeof(fw_image) : sizeof(cal_image);
    if (is_fw && fault("fw_short"))
        fw->size -= 4;
    if (!is_fw && fault("cal_short"))
        fw->size = 512;
    files_held++;
    *out = fw;
    return 0;
}

static void release_firmware(const struct firmware *fw)
{
    if (!fw)
        return;
    files_held--;
    free((void *)fw);
}

static void check_upload(const unsigned char *tx, unsigned int len)
{
    u32 address = get32(tx + 6);
    unsigned int words = tx[4] << 8 | tx[5];
    const unsigned char *src;
    unsigned int i, length = len - 16;

    printf("EVENT upload 0x%x %u\n", address, length);
    if (words * 4 != length || (unsigned int)(tx[10] << 8 | tx[11]) != (sum(tx + 4, 6) & 0xffff))
        violation("upload header mismatch");
    if (!address) {
        if (len != sizeof(fw_image) || memcmp(tx, fw_image, len))
            violation("firmware was not sent verbatim");
        return;
    }
    if (address >= 0x10009600 && address < 0x10009680) {
        src = NULL;
    } else if (address >= 0x10009000 && address + length <= 0x10009400) {
        src = cal_image + (address - 0x10009000);
    } else {
        violation("upload to an unexpected address");
        return;
    }
    for (i = 0; i < length; i += 2) {
        unsigned char hi = src ? src[i + 1] : 0, lo = src ? src[i] : 0;

        if (tx[12 + i] != hi || tx[13 + i] != lo) {
            violation("upload payload is not halfword-swapped source data");
            return;
        }
    }
    if (get32(tx + 12 + length) != (src ? sum(src, length) : 0))
        violation("upload trailer checksum mismatch");
}

static const unsigned char report_d1[] = { 0x50 };
static const unsigned char report_d3[] = { 1, 15, 20, 0x12, 0x34 };
static const unsigned char report_d0[] = { 1, 2, 3, 4, 5, 6, 7, 8 };
static const unsigned char report_a1[] = { 9, 8, 7, 6, 5, 4 };
static unsigned char report_d9[16];

static const unsigned char *report(unsigned char id, unsigned int *length)
{
    switch (id) {
    case 0xd1: *length = sizeof(report_d1); return report_d1;
    case 0xd3: *length = sizeof(report_d3); return report_d3;
    case 0xd0: *length = sizeof(report_d0); return report_d0;
    case 0xa1: *length = sizeof(report_a1); return report_a1;
    case 0xd9: *length = sizeof(report_d9); return report_d9;
    }
    *length = 0;
    return NULL;
}

static void n1_reply(const unsigned char *tx, unsigned char *rx, unsigned int len)
{
    bool running = pins_mmio[15] & P_DATA;
    bool selected = !(pins_mmio[79] & P_DATA);
    unsigned int i, length;
    const unsigned char *data;

    if (!running || !selected || fault("hbpp_absent"))
        return;
    if (len > 16 && tx[0] == 0x18 && tx[1] == 0xe1 && tx[2] == 0x30 && tx[3] == 0x01) {
        check_upload(tx, len);
        blob_pending = !fault_once("blob_nak") && !fault("blob_nak_always");
        return;
    }
    if (len == 2 && tx[0] == 0x1a && tx[1] == 0xa1) {
        if (blob_pending) { rx[0] = 0x4b; rx[1] = 0xc1; }
        else if (reg_pending) { rx[0] = fault("reg_nak") ? 0 : 0x4a; rx[1] = 0xd1; }
        blob_pending = reg_pending = false;
        return;
    }
    if (len == 2 && tx[0] == 0x1f && tx[1] == 0x01) {
        printf("EVENT calibration_request\n");
        return;
    }
    if (!fw_executed && len == 16 && tx[0] == 0x1a && tx[1] == 0xa1) {
        for (i = 0; i < len; i += 2) { rx[i] = 0x18; rx[i + 1] = 0xe1; }
        return;
    }
    if (len == 8 && tx[0] == 0x1c && tx[1] == 0x73) {
        read_address = get32(tx + 2);
        return;
    }
    if (len == 8 && tx[0] == 0x1a) {
        put32(rx + 2, read_address == 0x10008ffc ?
              (fault("version_zero") ? 0 : n1_version) : 1);
        return;
    }
    if (len == 16 && tx[0] == 0x1e && tx[1] == 0x33) {
        printf("EVENT regwrite 0x%x %u\n", get32(tx + 2), get32(tx + 10));
        reg_pending = true;
        return;
    }
    if (len == 12 && tx[0] == 0x1d && tx[1] == 0x53) {
        printf("EVENT execute\n");
        fw_executed = !fault("execute_dead");
        return;
    }
    if (!fw_executed || (len == 16 && tx[0] == 0xee))
        return;
    if (len == 16 && (tx[0] == 0xe2 || tx[0] == 0xe3)) {
        control_response = !control_response;
        if (control_response)
            return;
        rx[0] = tx[0];
        if (tx[0] == 0xe2) {
            rx[2] = 1; rx[3] = 0x00; rx[4] = 0x02;
        } else {
            report(tx[1], &length);
            rx[2] = length ? 0 : 1; rx[3] = length; rx[4] = 0;
        }
        rx[14] = sum(rx, 14); rx[15] = sum(rx, 14) >> 8;
        return;
    }
    if ((tx[0] == 0xe6 || tx[0] == 0xe7) && tx[2] == 1) {
        data = report(tx[1], &length);
        rx[0] = tx[0];
        if (data)
            memcpy(rx + 3, data, length);
        rx[len - 2] = sum(rx, len - 2) & 0xff;
        rx[len - 1] = sum(rx, len - 2) >> 8;
        if (tx[1] == 0xd3 && fault("bad_checksum"))
            rx[3] ^= 1;
    }
}

static void fw_report(void)
{
    if (files_held)
        printf("LEAK firmware files=%d\n", files_held);
}
'''


def patch_stubs():
    s = c1.patch_stubs().replace(f'"{c1.NAME}"', f'"{NAME}"')
    edits = [
        ('static void msleep(unsigned int ms) { printf("EVENT msleep %u\\n", ms); }',
         'static unsigned long jiffies = 1000;\n'
         'static void msleep(unsigned int ms) { printf("EVENT msleep %u\\n", ms); '
         'jiffies += ms; }'),
        ('    printf("EVENT usleep %lu %lu\\n", min, max);',
         '    printf("EVENT usleep %lu %lu\\n", min, max);\n    jiffies += min / 1000;'),
    ]
    for old, new in edits:
        if s.count(old) != 1:
            raise AssertionError(f'stub anchor changed: {old}')
        s = s.replace(old, new)
    return s


def spi_stubs():
    s = c1.SPI_STUBS
    start = s.index('static void n1_reply(')
    end = s.index('static int spi_sync_transfer(')
    s = s[:start] + s[end:]
    edits = [
        ('    for (i = 0; i < t->len; i++)\n        printf(" %02x", tx[i]);',
         '    for (i = 0; i < t->len && i < 32; i++)\n        printf(" %02x", tx[i]);'),
    ]
    for old, new in edits:
        if s.count(old) != 1:
            raise AssertionError(f'SPI stub anchor changed: {old}')
        s = s.replace(old, new)
    # The fake N1 must exist before spi_sync_transfer calls it.
    return s.replace('static int spi_sync_transfer(',
                     'static void n1_reply(const unsigned char *, unsigned char *, unsigned int);\n'
                     'static int spi_sync_transfer(', 1)


def patch_harness():
    h = c1.patch_harness()
    edits = [
        ('    result = j72_touch_id_init();\n    spi_report();',
         '    build_images();\n'
         '    put_unaligned_le32(14745, report_d9);\n'
         '    put_unaligned_le32(fault("surface_other") ? 15000 : 19657, report_d9 + 4);\n'
         '    report_d9[8] = 0x9c; report_d9[9] = 0xff; report_d9[10] = 0x9c; report_d9[11] = 0xff;\n'
         '    report_d9[12] = fault("bad_edges") ? 0x9c : 0x10; report_d9[13] = fault("bad_edges") ? 0xff : 0x27;\n'
         '    report_d9[14] = 0x10; report_d9[15] = 0x27;\n'
         '    if (fault("const_cal2")) n1_version = 0x434d11a0;\n'
         '    else n1_version = 0x434d15c1;\n'
         '    result = j72_touch_fw_init();\n    spi_report();\n    fw_report();'),
    ]
    for old, new in edits:
        if h.count(old) != 1:
            raise AssertionError(f'harness anchor changed: {old}')
        h = h.replace(old, new)
    return h


B = [e.replace('j72_touch_power', NAME) for e in power.SUCCESS]
UPLOADS = ['upload 0x10009600 128', 'upload 0x10009000 1008',
           'upload 0x100093f0 16', 'upload 0x0 57000']
REGWRITES = ['regwrite 0x10003060 6089', 'regwrite 0x1000305c 32',
             'regwrite 0x10003058 6', 'regwrite 0x10003000 3',
             'regwrite 0x10003518 1']


def subsequence(needles, events):
    it = iter(events)
    return all(any(n == e for e in it) for n in needles)


class TouchFwTests(unittest.TestCase):
    check_order = c1.TouchIdTests.check_order

    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        sources = [power.PROBE.read_text(), c1.PROBE.read_text(), cls.source]
        module = ('#define N1_BUFFER 65536\n#define J72_TOUCH_POWER_LIB\n'
                  '#define J72_TOUCH_ID_LIB\n' +
                  '\n'.join(line for text in sources for line in text.splitlines()
                            if not line.startswith(('#include', '#define J72_TOUCH',
                                                    '#define N1_BUFFER'))))
        faults = {**power.NODE_FAULTS, **c1.ID_FAULTS}
        body = '\n'.join(f'    if (fault("{name}")) {{ {code}; }}'
                         for name, code in faults.items())
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-touch-fw-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'fw.c'
        cls.binary = Path(cls.directory.name) / 'fw'
        source.write_text(patch_stubs() + spi_stubs() + FW_STUBS + '\n' + module + '\n' +
                          patch_harness().replace('    NODE_FAULTS', body))
        result = subprocess.run(
            ['cc', '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-parameter',
             '-Wno-unused-function', '-Wno-format', '-Werror', str(source),
             '-o', str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')

    def run_fixture(self, fault='none', error=0):
        result = subprocess.run(
            [str(self.binary), fault, str(error), hex(0xffffffff), '0'],
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

    def test_success_boots_firmware_and_reads_descriptors(self):
        code, events, logs, lines = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(c1.TouchIdTests.state(self, lines), power.SAFE_FINAL)
        self.check_order(events)
        self.assertTrue(subsequence(
            ['request_firmware apple/j72-n1.bin', 'request_firmware apple/j72-n1-cal.bin',
             'gpio_request 15 j72_touch_fw 0', 'gpio_dir_out 15 1'] + UPLOADS +
            REGWRITES + ['calibration_request', 'msleep 65', 'execute', 'msleep 40'],
            events), events)
        text = '\n'.join(logs)
        self.assertIn('HBPP detected; N1 controller 0x434d15c1', text)
        self.assertIn('calibrating controller 0x434d15c1, const-cal=3', text)
        self.assertRegex(text, r'N1 ready: max packet 512, family 80, sensor 15x20, '
                               r'FW 0x1234, surface 14745x19657 \(J71t\), x=-100..10000 y=-100..10000')
        self.assertIn('stage C2 complete', text)
        self.assertRegex(text, r'final version=0x434d15c1 complete=1 .* cleanup=0')

    def test_reports_are_read_in_reference_order(self):
        _, events, logs, _ = self.run_fixture()
        reports = re.findall(r'report (\w\w) length=', '\n'.join(logs))
        self.assertEqual(reports, ['d1', 'd3', 'd0', 'a1', 'd9'])
        self.assertIn('udelay 25', events)

    def test_const_cal_2_for_the_special_version(self):
        _, events, logs, _ = self.run_fixture('const_cal2')
        self.assertIn('regwrite 0x10003000 2', events)
        self.assertIn('const-cal=2', '\n'.join(logs))

    def test_firmware_and_calibration_bytes_never_reach_the_log(self):
        _, _, logs, lines = self.run_fixture()
        text = '\n'.join(logs)
        cal = bytes((i * 7 + 3) & 0xff for i in range(1024))
        fw = bytes((i * 13 + 5) & 0xff for i in range(64))
        for blob in (cal, bytes(b for i in range(0, 1024, 2) for b in (cal[i + 1], cal[i])), fw):
            for start in range(0, len(blob) - 6, 7):
                window = ' '.join(f'{b:02x}' for b in blob[start:start + 6])
                self.assertNotIn(window, text)

    def test_a_file_problem_refuses_before_any_hardware(self):
        for fault in ('fw_missing', 'fw_short', 'cal_missing', 'cal_short'):
            with self.subTest(fault=fault):
                code, events, logs, _ = self.run_fixture(fault)
                self.assertNotEqual(code, 0)
                self.assertFalse(any(e.startswith(('lock', 'gpio', 'register', 'writel',
                                                   'update', 'spi', 'xfer'))
                                     for e in events), events)
                self.assertIn('refused before power-up', '\n'.join(logs))

    def test_n1_failures_unwind_to_the_safe_state(self):
        for fault in ('hbpp_absent', 'blob_nak_always', 'reg_nak', 'execute_dead',
                      'bad_checksum', 'bad_edges', 'version_zero', 'kmemdup'):
            with self.subTest(fault=fault):
                code, events, logs, lines = self.run_fixture(fault)
                self.assertNotEqual(code, 0)
                self.assertEqual(c1.TouchIdTests.state(self, lines), power.SAFE_FINAL)
                self.check_order(events)
                self.assertRegex('\n'.join(logs), r'final version=\S+ complete=0 .* cleanup=0')

    def test_one_nak_is_retried(self):
        code, events, _, _ = self.run_fixture('blob_nak')
        self.assertEqual(code, 0)
        self.assertEqual(events.count('upload 0x10009600 128'), 2)

    def test_upload_nak_is_bounded(self):
        _, events, _, _ = self.run_fixture('blob_nak_always')
        self.assertEqual(events.count('upload 0x10009600 128'), 5)
        self.assertNotIn('upload 0x10009000 1008', events)

    def test_other_surface_is_logged_not_refused(self):
        code, _, logs, _ = self.run_fixture('surface_other')
        self.assertEqual(code, 0)
        self.assertIn('surface 14745x15000 (differs from J71t)', '\n'.join(logs))

    def test_transfer_faults_at_every_step_unwind(self):
        # Upload transfers are retried like the reference, so some faults recover.
        for step in range(1, 54):
            fault = f'xfer{step}'
            with self.subTest(fault=fault):
                code, events, logs, lines = self.run_fixture(fault, -errno.EIO)
                self.assertRegex('\n'.join(logs), r'cleanup=0')
                self.assertEqual(c1.TouchIdTests.state(self, lines), power.SAFE_FINAL)
                self.check_order(events)

    def test_source_registers_no_irq_input_or_overlay(self):
        for banned in ('request_irq', 'request_threaded_irq', 'input_', 'regmap_write',
                       'pinctrl_', 'of_overlay', 'request_firmware(', 'firmware_request_nowarn'):
            self.assertNotIn(banned, self.source)
        self.assertIn('#define J72_TOUCH_ID_LIB\n#include "j72_touch_id.c"', self.source)

    @unittest.skipUnless(BUILT.exists(), 'built module receipt not present')
    def test_built_module_imports_only_allowed_symbols(self):
        imports = (BUILT.parent / 'imports.txt').read_text().split()
        self.assertEqual(sorted(set(imports) - set(ALLOWED_IMPORTS)), [])

    def test_makefile_builds_the_module(self):
        self.assertIn('obj-m += j72_touch_fw.o', (ROOT / 'probe/Makefile').read_text())


if __name__ == '__main__':
    unittest.main()
