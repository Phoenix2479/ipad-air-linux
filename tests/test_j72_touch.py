"""Compile the Stage D resident touch driver against the C2, C1 and Stage B stubs.

On top of the earlier fakes, the fake N1 queues contact frames and holds ATN
low while any are pending. The harness raises the interrupt, runs queued
recovery work, reads the debugfs stats and writes stop, then checks the
input events and the safe final state. It establishes software behavior only.
"""

import errno
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_j72_touch_fw as c2  # noqa: E402
import test_j72_touch_id as c1  # noqa: E402
import test_j72_touch_power as power  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_touch.c'
BUILT = ROOT / 'source/build/j72-touch-d3-20261030/j72_touch_d3.ko'
NAME = 'j72_touch'
ALLOWED_IMPORTS = tuple(sorted(set(c2.ALLOWED_IMPORTS) | {
    'mutex_init_generic', 'timer_init_key', 'input_alloc_absinfo', 'cancel_delayed_work_sync', 'debugfs_create_dir',
    'debugfs_create_file_full', 'delayed_work_timer_fn', 'disable_irq_nosync',
    'enable_irq', 'free_irq', 'gpiod_to_irq',
    'input_allocate_device', 'input_event', 'input_free_device',
    'input_mt_get_slot_by_key', 'input_mt_init_slots', 'input_mt_report_slot_state',
    'input_mt_sync_frame', 'input_register_device', 'input_set_abs_params',
    'input_set_capability', 'input_unregister_device', 'queue_delayed_work_on',
    'request_threaded_irq', 'seq_lseek', 'seq_printf', 'seq_read', 'simple_open',
    'single_open', 'single_release', 'system_percpu_wq', '___ratelimit',
    'mutex_lock', 'mutex_unlock', 'pinctrl_get', 'pinctrl_lookup_state', 'pinctrl_put',
    'pinctrl_register_mappings', 'pinctrl_select_state', 'pinctrl_unregister_mappings',
}))

D_STUBS = r'''
#define __user
#define THIS_MODULE NULL
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define clamp_t(type, v, lo, hi) ((type)(v) < (type)(lo) ? (type)(lo) : (type)(v) > (type)(hi) ? (type)(hi) : (type)(v))
#define pr_warn(...) printf("LOG " __VA_ARGS__)
#define pr_err_ratelimited(...) printf("LOG " __VA_ARGS__)
#define DECLARE_BITMAP(name, bits) unsigned long name[(bits) / 64]
#define IRQ_HANDLED 1
#define IRQF_ONESHOT 0x1
#define IRQF_NO_AUTOEN 0x2
#define IRQF_TRIGGER_LOW 0x8
#define BUS_SPI 0x1c
#define EV_KEY 1
#define BTN_TOUCH 0x14a
#define ABS_MT_TOUCH_MAJOR 0x30
#define ABS_MT_TOUCH_MINOR 0x31
#define ABS_MT_ORIENTATION 0x34
#define ABS_MT_POSITION_X 0x35
#define ABS_MT_POSITION_Y 0x36
#define ABS_MT_PRESSURE 0x3a
#define MT_TOOL_FINGER 0
#define INPUT_MT_DIRECT 0x2
#define INPUT_MT_DROP_UNUSED 0x4
typedef long ssize_t;
typedef long loff_t;
typedef int irqreturn_t;

struct mutex { int held; };
static void mutex_init(struct mutex *m) { m->held = 0; }
static void mutex_lock(struct mutex *m)
{ if (m->held) violation("mutex taken twice"); m->held = 1; }
static void mutex_unlock(struct mutex *m)
{ if (!m->held) violation("mutex released unheld"); m->held = 0; }

static void bitmap_zero(unsigned long *map, unsigned int bits) { memset(map, 0, bits / 8); }
static bool test_and_set_bit(unsigned int bit, unsigned long *map)
{
    bool old = map[bit / 64] & (1UL << (bit % 64));

    map[bit / 64] |= 1UL << (bit % 64);
    return old;
}

struct work_struct { int unused; };
struct delayed_work { struct work_struct work; void (*fn)(struct work_struct *); bool queued; };
#define INIT_DELAYED_WORK(w, f) ((w)->fn = (f), (w)->queued = false)
#define to_delayed_work(w) container_of(w, struct delayed_work, work)
static struct delayed_work *pending_work;
static bool schedule_delayed_work(struct delayed_work *w, unsigned long delay)
{
    printf("EVENT schedule_recovery %lu\n", delay);
    w->queued = true;
    pending_work = w;
    return true;
}
static bool cancel_delayed_work_sync(struct delayed_work *w)
{
    printf("EVENT cancel_recovery\n");
    w->queued = false;
    return true;
}

static irqreturn_t (*irq_thread)(int, void *);
static void *irq_data;
static int irq_depth, irq_requested_count;
static bool irq_started;

/* pinctrl-apple-gpio: set_type and unmask write MODE, startup clears GRP, mask writes 7. */
static void pin111_mode(unsigned int mode, bool group0)
{
    pins_mmio[111] = (pins_mmio[111] & ~P_MODE) | (mode << 1);
    if (group0)
        pins_mmio[111] &= ~(7u << 16);
}
static int gpiod_to_irq(const struct gpio_desc *desc)
{
    printf("EVENT gpiod_to_irq %u\n", desc->hwnum);
    if (desc->hwnum != 111 || !desc->requested)
        violation("IRQ for a pin other than the requested ATN");
    return fault("no_irq") ? -ENXIO : 77;
}
static int request_threaded_irq(unsigned int irq, void *primary,
                                irqreturn_t (*thread)(int, void *),
                                unsigned long flags, const char *name, void *data)
{
    printf("EVENT request_irq %u 0x%lx %s\n", irq, flags, name);
    if (primary || flags != (IRQF_ONESHOT | IRQF_NO_AUTOEN | IRQF_TRIGGER_LOW))
        violation("unexpected IRQ request");
    if (fault_once("request_irq"))
        return -EBUSY;
    pin111_mode(3, false);
    irq_thread = thread;
    irq_data = data;
    irq_depth = 1;
    irq_requested_count++;
    return 0;
}
static void enable_irq(unsigned int irq)
{
    printf("EVENT enable_irq\n");
    if (irq_depth <= 0)
        violation("unbalanced enable_irq");
    irq_depth--;
    if (!irq_depth) {
        pin111_mode(3, !irq_started);
        irq_started = true;
    }
}
static void disable_irq_nosync(unsigned int irq)
{
    printf("EVENT disable_irq\n");
    irq_depth++;
    if (irq_started)
        pin111_mode(7, false);
}
static void free_irq(unsigned int irq, void *data)
{
    printf("EVENT free_irq\n");
    if (data != irq_data || !irq_requested_count)
        violation("freeing an IRQ that was not requested");
    irq_requested_count--;
    irq_thread = NULL;
    if (irq_started)
        pin111_mode(7, false);
    irq_started = false;
}

struct input_id { unsigned int bustype; };
struct input_dev {
    const char *name, *phys;
    struct input_id id;
    struct { struct device *parent; } dev;
    int registered, slots, slot;
    int keys[2];
    int abs_max[0x40], abs_res[0x40];
};
static int inputs_alive;
static struct input_dev *input_allocate_device(void)
{
    if (fault_once("input_alloc"))
        return NULL;
    inputs_alive++;
    return calloc(1, sizeof(struct input_dev));
}
static void input_free_device(struct input_dev *dev)
{
    printf("EVENT input_free\n");
    if (dev->registered)
        violation("freeing a registered input device");
    inputs_alive--;
    free(dev);
}
static void input_set_capability(struct input_dev *dev, unsigned int type, unsigned int code)
{ dev->keys[0] = code; }
static void input_set_abs_params(struct input_dev *dev, unsigned int axis, int min, int max,
                                 int fuzz, int flat)
{ dev->abs_max[axis] = max; }
static void input_abs_set_res(struct input_dev *dev, unsigned int axis, int res)
{ dev->abs_res[axis] = res; }
static int input_mt_init_slots(struct input_dev *dev, unsigned int num, unsigned int flags)
{ dev->slots = num; return fault_once("mt_slots") ? -ENOMEM : 0; }
static int input_register_device(struct input_dev *dev)
{
    printf("EVENT input_register %s %s x=%d y=%d slots=%d\n", dev->name, dev->phys,
           dev->abs_max[ABS_MT_POSITION_X], dev->abs_max[ABS_MT_POSITION_Y], dev->slots);
    if (fault_once("input_register"))
        return -EINVAL;
    dev->registered = 1;
    return 0;
}
static void input_unregister_device(struct input_dev *dev)
{
    printf("EVENT input_unregister\n");
    if (irq_requested_count)
        violation("input device removed while the IRQ is live");
    inputs_alive--;
    free(dev);
}
static int input_mt_get_slot_by_key(struct input_dev *dev, int key) { return key % 32; }
static void input_mt_slot(struct input_dev *dev, int slot) { dev->slot = slot; }
static bool input_mt_report_slot_state(struct input_dev *dev, unsigned int tool, bool active)
{
    if (active)
        printf("EVENT input_slot %d down\n", dev->slot);
    return active;
}
static void input_report_abs(struct input_dev *dev, unsigned int code, int value)
{
    if (!dev->registered)
        violation("event on an unregistered input device");
    if (code == ABS_MT_POSITION_X || code == ABS_MT_POSITION_Y)
        printf("EVENT input_%s %d\n", code == ABS_MT_POSITION_X ? "x" : "y", value);
}
static void input_mt_sync_frame(struct input_dev *dev) {}
static void input_sync(struct input_dev *dev) { printf("EVENT input_sync\n"); }

struct pinctrl_map { const char *dev_name, *name, *ctrl, *group, *function; };
#define PIN_MAP_MUX_GROUP(dev, state, pctl, grp, func) { dev, state, pctl, grp, func }
struct pinctrl { int unused; };
struct pinctrl_state { const char *name, *function; };
static struct pinctrl sync_handle;
static struct pinctrl_state pin_states[2];
static const struct pinctrl_map *pin_maps;
static int maps_registered, pinctrl_handles;

static int pinctrl_register_mappings(const struct pinctrl_map *m, unsigned int n)
{
    printf("EVENT pinctrl_map %s %s %s %s %s\n", m[0].dev_name, m[0].ctrl, m[0].group,
           m[0].function, m[1].function);
    if (fault_once("pinctrl_map"))
        return -ENOMEM;
    pin_maps = m;
    maps_registered++;
    return 0;
}
static void pinctrl_unregister_mappings(const struct pinctrl_map *m)
{
    printf("EVENT pinctrl_unmap\n");
    if (pinctrl_handles)
        violation("mapping removed while a handle is held");
    maps_registered--;
}
static struct pinctrl *pinctrl_get(struct device *dev)
{
    printf("EVENT pinctrl_get %s\n", dev->name);
    if (!maps_registered || strcmp(dev->name, pin_maps[0].dev_name))
        violation("pinctrl handle for an unmapped device");
    if (gpio_descs[3].requested)
        violation("pin 119 muxed while its GPIO line is requested");
    if (fault_once("pinctrl_get"))
        return ERR_PTR(-ENODEV);
    pinctrl_handles++;
    return &sync_handle;
}
static struct pinctrl_state *pinctrl_lookup_state(struct pinctrl *p, const char *name)
{
    for (int i = 0; i < 2; i++)
        if (!strcmp(pin_maps[i].name, name)) {
            pin_states[i].name = name;
            pin_states[i].function = pin_maps[i].function;
            return &pin_states[i];
        }
    return ERR_PTR(-ENODEV);
}
static int pinctrl_select_state(struct pinctrl *p, struct pinctrl_state *st)
{
    unsigned int periph = !strcmp(st->function, "periph1");

    printf("EVENT pinctrl_select %s\n", st->name);
    if (fault_once(periph ? "sync_select" : "sync_off_select"))
        return -EINVAL;
    pins_mmio[119] = (pins_mmio[119] & ~P_PERIPH) | (periph << 5) | P_INPUT_ENABLE;
    return 0;
}
static void pinctrl_put(struct pinctrl *p)
{
    printf("EVENT pinctrl_put\n");
    pinctrl_handles--;
}

struct dentry { int unused; };
static struct dentry debugfs_dir;
struct file { void *private_data; };
struct inode { void *i_private; };
struct file_operations {
    void *owner;
    int (*open)(struct inode *, struct file *);
    ssize_t (*write)(struct file *, const char *, size_t, loff_t *);
    ssize_t (*read)(struct file *, char *, size_t, loff_t *);
    long (*llseek)(struct file *, long, int);
    int (*release)(struct inode *, struct file *);
};
static int simple_open(struct inode *inode, struct file *file)
{ file->private_data = inode->i_private; return 0; }
struct seq_file { void *private; };
static void seq_printf(struct seq_file *s, const char *fmt, ...)
{
    va_list ap;

    printf("STATS ");
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}
static int (*stats_show_fn)(struct seq_file *, void *);
#define DEFINE_SHOW_ATTRIBUTE(name) \
    static const struct file_operations name##_fops = { .owner = NULL }; \
    static int name##_register(void) { stats_show_fn = name##_show; return 0; }
static const struct file_operations *stop_ops;
static void *debugfs_private;
static struct dentry *debugfs_create_dir(const char *name, struct dentry *parent)
{
    printf("EVENT debugfs_dir %s\n", name);
    return &debugfs_dir;
}
static struct dentry *debugfs_create_file(const char *name, unsigned int mode,
                                          struct dentry *parent, void *data,
                                          const struct file_operations *fops)
{
    printf("EVENT debugfs_file %s %o\n", name, mode);
    if (!strcmp(name, "stop"))
        stop_ops = fops;
    debugfs_private = data;
    return &debugfs_dir;
}
'''

FRAME_N1 = r'''
static unsigned char frames[8][256];
static unsigned int frame_lengths[8], frame_head, frame_tail;
static bool corrupt_next;

static void set_atn(void)
{
    if (frame_head != frame_tail)
        pins_mmio[111] &= ~P_DATA;
    else
        pins_mmio[111] |= P_DATA;
}

/* A classic 0x44 report with one 28-byte finger. */
static void queue_contact(unsigned char id, unsigned char state, int x, int y)
{
    unsigned char *r = frames[frame_tail % 8];
    unsigned char *f = r + 22;

    memset(r, 0, 256);
    r[0] = 0x44; r[2] = 22; r[16] = 1; r[17] = 28;
    f[0] = id; f[1] = state;
    f[4] = x; f[5] = x >> 8; f[6] = y; f[7] = y >> 8;
    f[12] = 100; f[14] = 80; f[26] = 50;
    frame_lengths[frame_tail % 8] = 22 + 28;
    frame_tail++;
    set_atn();
}

static unsigned char expected_sequence = 1;

static void power_off_n1(void)
{
    frame_head = frame_tail;
    pins_mmio[111] |= P_DATA;
}

static void frame_reply(const unsigned char *tx, unsigned char *rx, unsigned int len)
{
    unsigned int n = frame_head != frame_tail ? frame_lengths[frame_head % 8] : 0;
    unsigned int payload = n + 2, wire = payload + 5, s;

    if (tx[1] != expected_sequence)
        violation("frame request with the wrong sequence");
    s = sum(tx, 14);
    if ((tx[2] == 0 && (tx[14] | tx[15] << 8) != (s & 0xffff)) ||
        (tx[2] == 1 && (tx[len - 2] | tx[len - 1] << 8) != (s & 0xffff)))
        violation("frame request checksum mismatch");
    if (tx[2] == 0) {
        rx[0] = 0xe1;
        rx[1] = n ? payload : 0; rx[2] = n ? payload >> 8 : 0;
        s = sum(rx, 14); rx[14] = s; rx[15] = s >> 8;
        if (fault("drain_corrupt"))
            rx[14] ^= 1;
        return;
    }
    if (!n || len != wire)
        return;
    rx[0] = 0xeb;
    rx[2] = payload; rx[3] = payload >> 8;
    rx[4] = (unsigned char)(0x100 - (sum(rx, 4) & 0xff));
    memcpy(rx + 5, frames[frame_head % 8], n);
    s = sum(rx + 5, n);
    rx[wire - 2] = s; rx[wire - 1] = s >> 8;
    if (corrupt_next) {
        rx[wire - 2] ^= 1;
        corrupt_next = false;
        return;
    }
    frame_head++;
    expected_sequence = expected_sequence == 1 ? 2 : 1;
    set_atn();
}
'''

SETTING_STATE = r'''
static unsigned char report_bf[4], report_af[1], report_9d[8];
static unsigned char last_cmd, last_id, last_status;
'''

SETTING_CASES = r'''    case 0xbf: *length = sizeof(report_bf); return report_bf;
    case 0xaf: *length = sizeof(report_af); return report_af;
    case 0x9d: *length = sizeof(report_9d); return report_9d;
'''

SET_N1 = r'''    if (len == 16 && tx[0] == 0xe4) {
        unsigned char *target = (unsigned char *)report(tx[1], &length);
        unsigned int s = sum(tx, 14);

        if ((tx[14] | tx[15] << 8) != (s & 0xffff))
            violation("set request checksum mismatch");
        if (!target || tx[2] != length)
            violation("set request length differs from the report length");
        last_cmd = tx[0]; last_id = tx[1];
        last_status = fault("set_refused") ? 2 : 0;
        if (target && !last_status)
            memcpy(target, tx + 3, length);
        printf("EVENT set %02x", tx[1]);
        for (i = 0; i < tx[2]; i++)
            printf(" %02x", tx[3 + i]);
        printf("\n");
        return;
    }
    if (len == 16 && tx[0] == 0xe1) {
        if (tx[14] != 0xe1 || tx[15])
            violation("status request checksum mismatch");
        rx[0] = 0xe1; rx[6] = last_cmd; rx[7] = last_id; rx[8] = last_status;
        rx[14] = sum(rx, 14); rx[15] = sum(rx, 14) >> 8;
        return;
    }
'''

MAIN_D = r'''
    result = j72_touch_init();
    stats_register();
    printf("RESULT_INIT %d\n", result);
    if (!result) {
        struct inode inode = { debugfs_private };
        struct file file;
        struct seq_file seq = { debugfs_private };
        ssize_t written;

        if (irq_depth)
            violation("IRQ not enabled after a successful init");
        SCENARIO
        stats_show_fn(&seq, NULL);
        stop_ops->open(&inode, &file);
        written = stop_ops->write(&file, "1", 1, NULL);
        printf("STOP %ld\n", written);
        written = stop_ops->write(&file, "1", 1, NULL);
        printf("STOP_AGAIN %ld\n", written);
    }
    spi_report();
    fw_report();
    if (irq_requested_count || inputs_alive || maps_registered || pinctrl_handles)
        printf("LEAK irq=%d inputs=%d maps=%d pinctrl=%d\n", irq_requested_count,
               inputs_alive, maps_registered, pinctrl_handles);
'''

SCENARIOS = {
    'none': '',
    'touch': ('queue_contact(5, 3, 1000, 2000); irq_thread(77, irq_data);\n'
              '        queue_contact(5, 4, 1100, 2100); irq_thread(77, irq_data);\n'
              '        queue_contact(5, 1, 1100, 2100); irq_thread(77, irq_data);'),
    'clamp': 'queue_contact(1, 3, -5000, 30000); irq_thread(77, irq_data);',
    'recovery': ('frame_reply_corrupt_hold = 3; queue_contact(2, 3, 500, 600);\n'
                 '        irq_thread(77, irq_data);\n'
                 '        if (pending_work && pending_work->queued) '
                 '{ pending_work->queued = false; pending_work->fn(&pending_work->work); }\n'
                 '        queue_contact(2, 3, 700, 800); irq_thread(77, irq_data);'),
}


def patch_stubs():
    s = c2.patch_stubs().replace(f'"{c2.NAME}"', f'"{NAME}"')
    # An unpowered N1 drops its frames, and the pull-up takes ATN high.
    old = '    pmic_update_count[reg]++;\n'
    if s.count(old) != 1:
        raise AssertionError('PMIC update stub anchor changed')
    s = s.replace(old, old + '    if (reg == 0x85 && !(value & 0x02))\n'
                              '        power_off_n1();\n')
    s = s.replace('static int regmap_update_bits(',
                  'static void power_off_n1(void);\nstatic int regmap_update_bits(', 1)
    return s.replace('#include <string.h>', '#include <string.h>\n#include <stdarg.h>', 1)


def fw_stubs():
    s = c2.FW_STUBS
    old = '    if (len == 16 && (tx[0] == 0xe2 || tx[0] == 0xe3)) {'
    if s.count(old) != 1:
        raise AssertionError('fake N1 anchor changed')
    hook = ('    if (tx[0] == 0xeb) {\n'
            '        if (frame_reply_corrupt_hold && tx[2] == 1) {\n'
            '            frame_reply_corrupt_hold--;\n'
            '            corrupt_next = true;\n'
            '        }\n'
            '        frame_reply(tx, rx, len);\n'
            '        return;\n'
            '    }\n')
    s = s.replace(old, hook + SET_N1 + old)
    case = '    case 0xd9: *length = sizeof(report_d9); return report_d9;\n'
    if s.count(case) != 1:
        raise AssertionError('fake N1 report anchor changed')
    s = s.replace(case, case + SETTING_CASES)
    s = s.replace('static const unsigned char *report(unsigned char id',
                  SETTING_STATE + 'static const unsigned char *report(unsigned char id', 1)
    execute ='        fw_executed = !fault("execute_dead");\n'
    if s.count(execute) != 1:
        raise AssertionError('fake N1 execute anchor changed')
    s = s.replace(execute, execute + '        if (fault("drain_corrupt") || fault("boot_frame"))\n'
                                      '            queue_contact(9, 1, 0, 0);\n')
    return s.replace('static void n1_reply(const unsigned char *tx',
                     'static int frame_reply_corrupt_hold;\n' + FRAME_N1 +
                     'static void n1_reply(const unsigned char *tx', 1)


def harness(scenario):
    h = c2.patch_harness()
    old = '    result = j72_touch_fw_init();\n    spi_report();\n    fw_report();'
    if h.count(old) != 1:
        raise AssertionError('harness anchor changed')
    return h.replace(old, MAIN_D.replace('SCENARIO', SCENARIOS[scenario]))


class TouchDriverTests(unittest.TestCase):
    check_order = c1.TouchIdTests.check_order
    binaries = {}

    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        sources = [power.PROBE.read_text(), c1.PROBE.read_text(),
                   c2.PROBE.read_text(), cls.source]
        module = ('#define N1_BUFFER 65536\n#define J72_TOUCH_POWER_LIB\n'
                  '#define J72_TOUCH_ID_LIB\n#define J72_TOUCH_FW_LIB\n'
                  'struct touch_power;\nstatic int sync_on(struct touch_power *tp);\n'
                  '#define J72_TOUCH_SYNC_HOOK sync_on\n' +
                  '\n'.join(line for text in sources for line in text.splitlines()
                            if not line.startswith(('#include', '#define J72_TOUCH',
                                                    '#define N1_BUFFER'))))
        faults = {**power.NODE_FAULTS, **c1.ID_FAULTS}
        body = '\n'.join(f'    if (fault("{name}")) {{ {code}; }}'
                         for name, code in faults.items())
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-touch-')
        cls.addClassCleanup(cls.directory.cleanup)
        for scenario in SCENARIOS:
            source = Path(cls.directory.name) / f'{scenario}.c'
            binary = Path(cls.directory.name) / scenario
            source.write_text(patch_stubs() + c2.spi_stubs() + fw_stubs() + D_STUBS +
                              '\n' + module + '\n' +
                              harness(scenario).replace('    NODE_FAULTS', body))
            result = subprocess.run(
                ['cc', '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-parameter',
                 '-Wno-unused-function', '-Wno-format', '-Wno-unused-variable',
                 '-Werror', str(source), '-o', str(binary)],
                capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')
            cls.binaries[scenario] = binary

    def run_fixture(self, scenario='none', fault='none', error=0):
        result = subprocess.run(
            [str(self.binaries[scenario]), fault, str(error), hex(0xffffffff), '0'],
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
        init = int(next(line for line in lines if line.startswith('RESULT_INIT ')).split()[1])
        return init, events, logs, lines

    def safe(self, lines, irq_ran=True):
        expected = dict(power.SAFE_FINAL, gpio111=0x0000038f if irq_ran else 0x00070381)
        self.assertEqual(c1.TouchIdTests.state(self, lines), expected)

    def test_init_registers_input_and_irq_then_stop_restores_the_safe_state(self):
        init, events, logs, lines = self.run_fixture()
        self.assertEqual(init, 0)
        self.safe(lines)
        self.check_order(events)
        self.assertIn('input_register Apple N1 Touchscreen spi0.0/input0 '
                      'x=10100 y=10100 slots=32', events)
        self.assertIn('request_irq 77 0xb j72_touch', events)
        self.assertLess(events.index('request_irq 77 0xb j72_touch'), events.index('enable_irq'))
        self.assertLess(events.index('free_irq'), events.index('input_unregister'))
        self.assertLess(events.index('input_unregister'), events.index('spi_unregister'))
        self.assertIn('debugfs_file stop 200', events)
        self.assertIn('STOP 1', lines)
        self.assertIn('STOP_AGAIN 1', lines)
        self.assertEqual(events.count('free_irq'), 1)
        self.assertIn('touchscreen reporting enabled on irq 77, 10100x10100', '\n'.join(logs))

    def test_contacts_become_input_events(self):
        init, events, logs, lines = self.run_fixture('touch')
        self.assertEqual(init, 0)
        self.safe(lines)
        self.assertTrue(c2.subsequence(
            ['input_slot 5 down', 'input_x 1100', 'input_y 2100', 'input_sync',
             'input_slot 5 down', 'input_x 1200', 'input_y 2200', 'input_sync',
             'input_sync'], events), events)
        self.assertEqual(events.count('input_slot 5 down'), 2)
        text = '\n'.join(logs)
        self.assertIn('frame 1 contact id=5 state=3 x=1000 y=2000', text)
        stats = next(line for line in lines if line.startswith('STATS '))
        self.assertIn('irqs=3', stats)
        self.assertIn('contact_frames=3', stats)
        self.assertIn('errors=0', stats)

    def test_positions_are_clamped_to_the_discovered_edges(self):
        _, events, _, _ = self.run_fixture('clamp')
        self.assertIn('input_x 0', events)
        self.assertIn('input_y 10100', events)

    def test_frame_errors_mask_the_irq_and_recovery_rearms_it(self):
        init, events, logs, lines = self.run_fixture('recovery')
        self.assertEqual(init, 0)
        self.safe(lines)
        self.assertLess(events.index('disable_irq'), events.index('schedule_recovery 1000'))
        after = events[events.index('schedule_recovery 1000'):]
        self.assertIn('enable_irq', after)
        text = '\n'.join(logs)
        self.assertIn('IRQ masked', text)
        self.assertIn('recovery 1/3 result 0', text)
        self.assertIn('input_x 600', events)
        self.assertIn('input_x 800', events)

    def test_input_and_irq_failures_unwind_to_the_safe_state(self):
        for fault in ('input_alloc', 'mt_slots', 'input_register', 'no_irq', 'request_irq',
                      'drain_corrupt'):
            with self.subTest(fault=fault):
                init, events, logs, lines = self.run_fixture('none', fault)
                self.assertNotEqual(init, 0)
                self.safe(lines, irq_ran=False)
                self.check_order(events)
                self.assertNotIn('enable_irq', events)
                self.assertRegex('\n'.join(logs), r'stopped .* cleanup=0')

    def test_initial_drain_runs_before_the_irq_is_requested(self):
        _, events, _, _ = self.run_fixture('none', 'drain_corrupt')
        self.assertNotIn('request_irq 77 0xb j72_touch', events)
        _, events, _, _ = self.run_fixture()
        self.assertLess(events.index('input_register Apple N1 Touchscreen spi0.0/input0 '
                                     'x=10100 y=10100 slots=32'),
                        events.index('request_irq 77 0xb j72_touch'))

    def test_display_sync_follows_the_ios_order_and_is_restored(self):
        init, events, logs, lines = self.run_fixture('touch')
        self.assertEqual(init, 0)
        self.safe(lines)
        self.assertIn('pinctrl_map j72_touch 20e300000.pinctrl PIN119 periph1 gpio', events)
        self.assertTrue(c2.subsequence(
            ['xfer 2 19 c1', 'usleep 10000 12000', 'gpio_free 119',
             'pinctrl_get j72_touch', 'pinctrl_select sync-on', 'xfer 16 1a a1 18 e1 18 e1',
             'gpio_dir_out 15 1'], [e[:25] if e.startswith('xfer 16') else e for e in events]),
            events)
        self.assertIn('gpio119 sync raw=0x00072622 mode=1 data=0 periph=1', '\n'.join(logs))
        stop = events[events.index('free_irq'):]
        self.assertTrue(c2.subsequence(
            ['gpio_dir_out 15 0', 'spi_unregister', 'pinctrl_select sync-off', 'pinctrl_put',
             'pinctrl_unmap', 'gpio_request 119 j72_touch 0', 'gpio_dir_out 119 0',
             'writel 0x20a118020 0x4000', 'update pmic 0x85 0x2 0x0'], stop), stop)

    def test_sync_failures_unwind_and_restore_pin_119(self):
        for fault in ('pinctrl_map', 'pinctrl_get', 'sync_select'):
            with self.subTest(fault=fault):
                init, events, logs, lines = self.run_fixture('none', fault)
                self.assertNotEqual(init, 0)
                self.safe(lines, irq_ran=False)
                self.check_order(events)
                self.assertNotIn('gpio_dir_out 15 1', events)
                self.assertRegex('\n'.join(logs), r'stopped .* cleanup=0')

    def test_a_pending_boot_frame_is_drained_without_events(self):
        init, events, logs, lines = self.run_fixture('none', 'boot_frame')
        self.assertEqual(init, 0)
        self.safe(lines)
        text = '\n'.join(logs)
        self.assertIn('drain 0 atn_asserted=1', text)
        self.assertIn('drain 1 atn_asserted=0', text)
        self.assertNotIn('input_slot', ' '.join(events))
        stats = next(line for line in lines if line.startswith('STATS '))
        self.assertIn('frames=1', stats)

    def test_drain_stops_when_atn_is_high_without_reading(self):
        _, events, logs, _ = self.run_fixture()
        self.assertIn('drain 0 atn_asserted=0', '\n'.join(logs))
        before_irq = events[:events.index('request_irq 77 0xb j72_touch')]
        self.assertFalse([e for e in before_irq if e.startswith('xfer 16 eb')])

    def test_stop_logs_pin_111_in_its_post_irq_state(self):
        _, _, logs, _ = self.run_fixture('touch')
        self.assertIn('gpio111 atn raw=0x0000038f mode=7', '\n'.join(logs))

    def test_boot_failures_still_unwind(self):
        for fault in ('hbpp_absent', 'execute_dead', 'fw_missing', 'boot84'):
            with self.subTest(fault=fault):
                init, events, _, lines = self.run_fixture('none', fault)
                self.assertNotEqual(init, 0)
                self.assertFalse(any(e.startswith(('input_register', 'request_irq'))
                                     for e in events))
                if fault != 'fw_missing':
                    self.check_order(events)

    def test_firmware_files_are_released_after_boot(self):
        _, events, _, lines = self.run_fixture()
        self.assertNotIn('LEAK firmware', '\n'.join(lines))
        self.assertLess(events.index('execute'), events.index('input_register Apple N1 Touchscreen '
                                                             'spi0.0/input0 x=10100 y=10100 slots=32'))

    def test_ios_settings_are_written_after_boot_and_before_input(self):
        init, events, logs, _ = self.run_fixture()
        self.assertEqual(init, 0)
        sets = [event for event in events if event.startswith('set ')]
        self.assertEqual(sets, ['set bf 9d 81 09 00', 'set af 00',
                                'set 9d 01 00 00 00 00 00 00 00',
                                'set bf 9d 81 09 00', 'set af 00'])
        self.assertLess(events.index('execute'), events.index(sets[0]))
        self.assertLess(events.index(sets[-1]),
                        next(i for i, e in enumerate(events) if e.startswith('input_register')))
        self.assertIn('iOS settings written 5/5', '\n'.join(logs))
        reads = re.findall(r'settings (\w+): report (\w\w)', '\n'.join(logs))
        self.assertEqual(reads, [(phase, r) for phase in ('before', 'after')
                                 for r in ('bf', 'af', '9d')])
        self.assertNotIn('read failed', '\n'.join(logs))

    def test_refused_settings_are_logged_and_input_still_starts(self):
        init, events, logs, lines = self.run_fixture(fault='set_refused')
        self.assertEqual(init, 0)
        text = '\n'.join(logs)
        self.assertIn('iOS settings written 0/5', text)
        self.assertIn('report bf write status=02 last=e4/bf', text)
        self.safe(lines)

    def test_source_never_drives_atn_or_logs_frame_transfers(self):
        self.assertNotIn('gpiod_direction_output_raw(t->tp.gpio[PIN_ATN]', self.source)
        self.assertNotRegex(self.source, r'n1_xfer\(id, "')
        self.assertIn('N1_TRACE_XFERS 24', self.source)
        self.assertIn('#define J72_TOUCH_FW_LIB\n#include "j72_touch_fw.c"', self.source)

    @unittest.skipUnless(BUILT.exists(), 'built module receipt not present')
    def test_built_module_imports_only_allowed_symbols(self):
        imports = (BUILT.parent / 'imports.txt').read_text().split()
        self.assertEqual(sorted(set(imports) - set(ALLOWED_IMPORTS)), [])

    def test_makefile_builds_the_module(self):
        self.assertIn('obj-m += j72_touch.o', (ROOT / 'probe/Makefile').read_text())


if __name__ == '__main__':
    unittest.main()
