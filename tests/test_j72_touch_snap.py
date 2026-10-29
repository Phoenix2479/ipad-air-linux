"""Compile the whole touch snapshot module against recording host stubs.

The stubs define no MMIO, regmap, GPIO, pinctrl or IRQ write, so any write
call fails to compile. These tests establish access order, refusals and
unwinding only, not hardware behavior.
"""

import errno
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_touch_snap.c'
BUILT = ROOT / 'source/build/j72-touch-snap-20261029/j72_touch_snap.ko'
ALLOWED_IMPORTS = (
    '__ioremap_prot', '__of_parse_phandle_with_args', '__pm_runtime_disable',
    '__pm_runtime_resume', '__pm_runtime_suspend', '__release_region',
    '__request_region', '__stack_chk_fail', '_printk', 'alt_cb_patch_nops',
    'arm64_use_ng_mappings', 'dev_get_regmap', 'i2c_find_device_by_fwnode',
    'init_uts_ns', 'iomem_resource', 'iounmap', 'memset', 'mutex_lock',
    'mutex_unlock', 'of_address_to_resource', 'of_count_phandle_with_args',
    'of_device_is_available', 'of_device_is_compatible',
    'of_find_compatible_node', 'of_find_device_by_node',
    'of_find_node_opts_by_path', 'of_genpd_add_device', 'of_get_parent',
    'of_machine_compatible_match', 'of_node_get', 'of_node_put',
    'of_property_count_elems_of_size', 'of_property_read_variable_u32_array',
    'platform_device_register_full', 'platform_device_unregister',
    'pm_genpd_remove_device', 'pm_runtime_enable', 'put_device',
    'regmap_get_val_bytes', 'regmap_read', 'strcmp', 'syscon_node_to_regmap',
)
PWM0 = '/soc/power-management@20e000000/power-controller@20070'

STUBS = r'''
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
typedef uint64_t u64;
#define __iomem
#define __init
#define __exit
#define KBUILD_MODNAME "j72_touch_snap"
#define BIT(n) (1UL << (n))
#define GENMASK(h, l) ((~0UL << (l)) & (~0UL >> (63 - (h))))
#define FIELD_GET(mask, reg) (((reg) & (mask)) >> __builtin_ctzl(mask))
#define ARRAY_SIZE(x) ((int)(sizeof(x) / sizeof((x)[0])))
#define ERR_PTR(e) ((void *)(long)(e))
#define PTR_ERR(p) ((int)(long)(p))
#define IS_ERR(p) ((unsigned long)(p) >= (unsigned long)-4095)
#define PLATFORM_DEVID_NONE (-1)
#define pr_info(...) printf("LOG " __VA_ARGS__)
#define pr_err(...) printf("LOG " __VA_ARGS__)
#define module_init(fn) int (*module_init_ref)(void) = fn
#define module_exit(fn) void (*module_exit_ref)(void) = fn
#define MODULE_LICENSE(x)
#define MODULE_DESCRIPTION(x)
#define for_each_compatible_node(dn, type, compatible) \
    for (dn = of_find_compatible_node(NULL, type, compatible); dn; \
         dn = of_find_compatible_node(dn, type, compatible))

static const char *fault_at = "none";
static int fault_err;

static bool fault(const char *name)
{
    if (strcmp(fault_at, name))
        return false;
    printf("FAULT_HIT %s\n", name);
    return true;
}

static void violation(const char *what) { printf("VIOLATION %s\n", what); }

struct device_node {
    const char *path;
    const char *compat[3];
    bool available;
    u32 reg[2];
    int nreg;
    u64 base, size;
    struct device_node *parent, *dep;
    int refs;
};
struct of_phandle_args { struct device_node *np; int args_count; u32 args[16]; };
struct resource { u64 start, end; };
struct device { const char *name; const void *driver; int refs, locked; void *pm_domain; };
struct platform_device { struct device dev; };
struct i2c_client { struct device dev; };
struct regmap { const char *name; int val_bytes; };
struct new_utsname { const char *release; };

enum { PMGR, PWM0, SIO_P, GPIO_PS, PINS, PMIC, PMIC2, NNODES };
static struct device_node nodes[NNODES];
static struct new_utsname uts = { "7.0.12-j72baseline" };
static int bound;
static struct platform_device pins_dev = { { "20e300000.pinctrl", &bound, 0, 0, NULL } };
static struct i2c_client pmic_client = { { "0-003c", &bound, 0, 0, NULL } };
static struct platform_device snap_dev = { { "j72_touch_snap", NULL, 0, 0, NULL } };
static struct regmap pmgr_map = { "pmgr", 4 }, pmic_map = { "pmic", 1 };
static int genpd, registered, region, rpm_active, pmgr_active;
static int pmgr_reads, pmic_reads;
static unsigned int pmgr_on_value = 0xff;
static u32 pins_mmio[0x400], pwm_mmio[0x400];
static bool pins_mapped, pwm_mapped;

static struct device_node *get(struct device_node *np)
{
    if (np)
        np->refs++;
    return np;
}

static struct device_node *of_node_get(struct device_node *np) { return get(np); }
static void of_node_put(struct device_node *np)
{
    if (np && --np->refs < 0)
        violation("node reference underflow");
}

static struct device_node *of_find_node_by_path(const char *path)
{
    for (int i = 0; i < NNODES; i++)
        if (!strcmp(nodes[i].path, path))
            return get(&nodes[i]);
    return NULL;
}

static int of_device_is_compatible(const struct device_node *np, const char *compat)
{
    for (int i = 0; i < 3; i++)
        if (np->compat[i] && !strcmp(np->compat[i], compat))
            return 1;
    return 0;
}

static struct device_node *of_find_compatible_node(struct device_node *from,
                                                   const char *type, const char *compat)
{
    int i = from ? (int)(from - nodes) + 1 : 0;

    (void)type;
    of_node_put(from);
    for (; i < NNODES; i++)
        if (of_device_is_compatible(&nodes[i], compat))
            return get(&nodes[i]);
    return NULL;
}

static bool of_device_is_available(const struct device_node *np) { return np->available; }
static int of_machine_is_compatible(const char *compat)
{ return !fault("machine") && !strcmp(compat, "apple,j72"); }
static struct new_utsname *init_utsname(void) { return &uts; }

static int of_property_count_u32_elems(const struct device_node *np, const char *name)
{ return strcmp(name, "reg") || !np->nreg ? -EINVAL : np->nreg; }

static int of_property_read_u32_array(const struct device_node *np, const char *name,
                                      u32 *out, size_t count)
{
    if (strcmp(name, "reg") || (int)count > np->nreg)
        return -EINVAL;
    memcpy(out, np->reg, count * sizeof(*out));
    return 0;
}

static int of_property_read_u32(const struct device_node *np, const char *name, u32 *out)
{ return of_property_read_u32_array(np, name, out, 1); }

static int of_count_phandle_with_args(const struct device_node *np, const char *list,
                                      const char *cells)
{ return !strcmp(list, "power-domains") && !strcmp(cells, "#power-domain-cells") &&
         np->dep ? 1 : -ENOENT; }

static int of_parse_phandle_with_args(const struct device_node *np, const char *list,
                                      const char *cells, int index,
                                      struct of_phandle_args *out)
{
    if (of_count_phandle_with_args(np, list, cells) != 1 || index)
        return -ENOENT;
    out->np = get(np->dep);
    out->args_count = 0;
    return 0;
}

static struct device_node *of_get_parent(const struct device_node *np) { return get(np->parent); }

static int of_address_to_resource(struct device_node *np, int index, struct resource *res)
{
    if (index || !np->size)
        return -EINVAL;
    res->start = np->base;
    res->end = np->base + np->size - 1;
    return 0;
}

static u64 resource_size(const struct resource *res) { return res->end - res->start + 1; }

static struct platform_device *of_find_device_by_node(struct device_node *np)
{
    if (np != &nodes[PINS])
        violation("device lookup for an unexpected node");
    if (fault("pins_dev"))
        return NULL;
    pins_dev.dev.refs++;
    return &pins_dev;
}

static struct i2c_client *of_find_i2c_device_by_node(struct device_node *np)
{
    if (np != &nodes[PMIC])
        violation("I2C lookup for an unexpected node");
    if (fault("pmic_client"))
        return NULL;
    pmic_client.dev.refs++;
    return &pmic_client;
}

static void put_device(struct device *dev)
{
    if (dev && --dev->refs < 0)
        violation("device reference underflow");
}

static void device_lock(struct device *dev)
{
    printf("EVENT lock %s\n", dev->name);
    dev->locked++;
}

static void device_unlock(struct device *dev)
{
    printf("EVENT unlock %s\n", dev->name);
    dev->locked--;
}

static struct regmap *syscon_node_to_regmap(struct device_node *np)
{
    if (np != &nodes[PMGR])
        violation("syscon lookup for an unexpected node");
    return fault("syscon") ? ERR_PTR(fault_err) : &pmgr_map;
}

static struct regmap *dev_get_regmap(struct device *dev, const char *name)
{
    if (dev != &pmic_client.dev || name)
        violation("unexpected regmap lookup");
    return &pmic_map;
}

static int regmap_get_val_bytes(struct regmap *map) { return map->val_bytes; }

static int regmap_read(struct regmap *map, unsigned int reg, unsigned int *val)
{
    static const char *const pmgr_steps[] = { "pmgr_before", "pmgr_on", "pmgr_after" };
    static const unsigned int pmic_values[] = { 0x00, 0x11, 0x14 };
    char step[16];

    printf("EVENT read %s 0x%x\n", map->name, reg);
    if (map == &pmgr_map) {
        if (reg != 0x20070 || pmgr_reads >= 3) {
            violation("unexpected PMGR read");
            return -EINVAL;
        }
        if (fault(pmgr_steps[pmgr_reads++]))
            return fault_err;
        *val = pmgr_reads == 2 ? pmgr_on_value : pmgr_reads == 1 ? 0x300 : 0;
        if (pmgr_reads == 2)
            pmgr_active = rpm_active && ((*val >> 4) & 0xf) == 0xf;
        return 0;
    }
    if (!pmic_client.dev.locked || !pmic_client.dev.driver || pmic_reads >= 3)
        violation("PMIC read without a locked, bound driver");
    snprintf(step, sizeof(step), "pmic%d", pmic_reads);
    if (fault(step))
        return fault_err;
    *val = pmic_values[pmic_reads++];
    return 0;
}

static void *ioremap(u64 addr, size_t size)
{
    printf("EVENT ioremap 0x%llx 0x%zx\n", (unsigned long long)addr, size);
    if (addr == 0x20e300000ULL && size == 0x1000) {
        if (!pins_dev.dev.locked || !pins_dev.dev.driver)
            violation("pin block mapped without a locked, bound driver");
        if (fault("pins_ioremap"))
            return NULL;
        pins_mapped = true;
        return pins_mmio;
    }
    if (addr == 0x20a118000ULL && size == 0x1000) {
        if (!region || !rpm_active || !pmgr_active)
            violation("PWM mapped before ps_pwm0 was confirmed on");
        if (fault("pwm_ioremap"))
            return NULL;
        pwm_mapped = true;
        return pwm_mmio;
    }
    violation("unexpected ioremap");
    return NULL;
}

static void iounmap(volatile void *addr)
{
    if (addr == pins_mmio && pins_mapped) {
        printf("EVENT iounmap 0x20e300000\n");
        pins_mapped = false;
    } else if (addr == pwm_mmio && pwm_mapped) {
        printf("EVENT iounmap 0x20a118000\n");
        pwm_mapped = false;
    } else {
        violation("unmapping an unknown mapping");
    }
}

static u32 readl(const volatile void *addr)
{
    const volatile unsigned char *p = addr;
    long offset;

    if (pins_mapped && p >= (unsigned char *)pins_mmio &&
        p < (unsigned char *)pins_mmio + 0x1000) {
        offset = p - (unsigned char *)pins_mmio;
        if (!pins_dev.dev.locked || !pins_dev.dev.driver)
            violation("pin read without a locked, bound driver");
        printf("EVENT readl 0x%llx\n", 0x20e300000ULL + offset);
        return pins_mmio[offset / 4];
    }
    if (pwm_mapped && p >= (unsigned char *)pwm_mmio &&
        p < (unsigned char *)pwm_mmio + 0x1000) {
        offset = p - (unsigned char *)pwm_mmio;
        if (!region || !rpm_active || !pmgr_active)
            violation("PWM read while ps_pwm0 is not confirmed on");
        printf("EVENT readl 0x%llx\n", 0x20a118000ULL + offset);
        return pwm_mmio[offset / 4];
    }
    violation("read outside a mapping");
    return 0;
}

static struct resource *request_mem_region(u64 start, u64 size, const char *name)
{
    static struct resource res;

    printf("EVENT request_region 0x%llx 0x%llx %s\n", (unsigned long long)start,
           (unsigned long long)size, name);
    if (fault("region"))
        return NULL;
    region = 1;
    return &res;
}

static void release_mem_region(u64 start, u64 size)
{
    printf("EVENT release_region 0x%llx 0x%llx\n", (unsigned long long)start,
           (unsigned long long)size);
    region = 0;
}

static struct platform_device *platform_device_register_simple(
    const char *name, int id, const struct resource *res, unsigned int num)
{
    printf("EVENT register %s %d %d %u\n", name, id, res != NULL, num);
    if (fault("register"))
        return ERR_PTR(fault_err);
    registered = 1;
    return &snap_dev;
}

static void platform_device_unregister(struct platform_device *pdev)
{
    printf("EVENT unregister %s\n", pdev->dev.name);
    if (pdev->dev.pm_domain)
        violation("device freed while genpd still lists it");
    registered = 0;
}

static int of_genpd_add_device(const struct of_phandle_args *args, struct device *dev)
{
    printf("EVENT genpd_add %s %s\n", args->np->path, dev->name);
    if (args->np != &nodes[PWM0] || args->args_count || dev != &snap_dev.dev)
        violation("unexpected genpd attach");
    if (fault("genpd_add"))
        return fault_err;
    dev->pm_domain = &genpd;
    genpd = 1;
    return 0;
}

static int pm_genpd_remove_device(struct device *dev)
{
    printf("EVENT genpd_remove %s\n", dev->name);
    if (fault("genpd_remove"))
        return fault_err;
    dev->pm_domain = NULL;
    genpd = 0;
    return 0;
}

static void pm_runtime_enable(struct device *dev) { printf("EVENT rpm_enable %s\n", dev->name); }
static void pm_runtime_disable(struct device *dev) { printf("EVENT rpm_disable %s\n", dev->name); }

static int pm_runtime_resume_and_get(struct device *dev)
{
    printf("EVENT rpm_get %s\n", dev->name);
    if (!dev->pm_domain)
        violation("resume without a genpd");
    if (fault("rpm_get"))
        return fault_err;
    rpm_active = 1;
    return 0;
}

static int pm_runtime_put_sync_suspend(struct device *dev)
{
    printf("EVENT rpm_put %s\n", dev->name);
    if (pwm_mapped)
        violation("ps_pwm0 released while PWM is mapped");
    rpm_active = 0;
    return fault("rpm_put") ? fault_err : 0;
}
'''

HARNESS = r'''
static void setup_nodes(void)
{
    struct device_node *pmgr = &nodes[PMGR], *sio_p = &nodes[SIO_P];

    nodes[PMGR] = (struct device_node){
        .path = "/soc/power-management@20e000000",
        .compat = { "apple,s5l8960x-pmgr", "syscon", "simple-mfd" },
        .available = true, .base = 0x20e000000ULL, .size = 0x24000 };
    nodes[PWM0] = (struct device_node){
        .path = "/soc/power-management@20e000000/power-controller@20070",
        .compat = { "apple,s5l8960x-pmgr-pwrstate", "apple,pmgr-pwrstate" },
        .available = true, .reg = { 0x20070, 4 }, .nreg = 2, .parent = pmgr,
        .dep = sio_p };
    nodes[SIO_P] = (struct device_node){
        .path = "/soc/power-management@20e000000/power-controller@20110",
        .compat = { "apple,s5l8960x-pmgr-pwrstate", "apple,pmgr-pwrstate" },
        .available = true, .reg = { 0x20110, 4 }, .nreg = 2, .parent = pmgr };
    nodes[GPIO_PS] = (struct device_node){
        .path = "/soc/power-management@20e000000/power-controller@20040",
        .compat = { "apple,s5l8960x-pmgr-pwrstate", "apple,pmgr-pwrstate" },
        .available = true, .reg = { 0x20040, 4 }, .nreg = 2, .parent = pmgr };
    nodes[PINS] = (struct device_node){
        .path = "/soc/pinctrl@20e300000",
        .compat = { "apple,s5l8960x-pinctrl", "apple,pinctrl" },
        .available = true, .base = 0x20e300000ULL, .size = 0x100000 };
    nodes[PMIC] = (struct device_node){
        .path = "/soc/i2c@20a110000/pmic@3c",
        .compat = { "apple,i2c-pmic", "apple,anya-pmic" },
        .available = true, .reg = { 0x3c }, .nreg = 1 };
    nodes[PMIC2] = (struct device_node){
        .path = "/soc/i2c@20a114000/pmic@3c",
        .compat = { "apple,i2c-pmic", "apple,anya-pmic" },
        .available = false, .reg = { 0x3c }, .nreg = 1 };
}

int main(int argc, char **argv)
{
    int result;

    if (argc != 4)
        return 2;
    fault_at = argv[1];
    fault_err = atoi(argv[2]);
    pmgr_on_value = (unsigned int)strtoul(argv[3], NULL, 0);
    setup_nodes();
    pins_mmio[15] = 0x00200203;
    pins_mmio[79] = 0x0000006e;
    pins_mmio[111] = 0x00000211;
    pins_mmio[119] = 0x00000040;
    for (int i = 0; i < 9; i++)
        pwm_mmio[i] = 0xa0000000u + i;
    NODE_FAULTS
    result = j72_touch_snap_init();
    printf("RESULT %d\n", result);
    for (int i = 0; i < NNODES; i++)
        if (nodes[i].refs)
            printf("LEAK node %s %d\n", nodes[i].path, nodes[i].refs);
    if (pins_dev.dev.refs || pmic_client.dev.refs || pins_dev.dev.locked ||
        pmic_client.dev.locked || pins_mapped || pwm_mapped || rpm_active || region)
        printf("LEAK device, lock, mapping, power or region\n");
    printf("STATE registered=%d genpd=%d\n", registered, genpd);
    return 0;
}
'''

# Each preflight fault mutates the fake device tree or devices before init.
NODE_FAULTS = {
    'release': 'uts.release = "7.0.12"',
    'pwm0_missing': 'nodes[PWM0].path = "/gone"',
    'pwm0_unavailable': 'nodes[PWM0].available = false',
    'pwm0_compat': 'nodes[PWM0].compat[0] = "apple,t8103-pmgr-pwrstate"',
    'pwm0_reg': 'nodes[PWM0].reg[0] = 0x20078',
    'pwm0_reg_size': 'nodes[PWM0].reg[1] = 8',
    'pwm0_no_dep': 'nodes[PWM0].dep = NULL',
    'pwm0_dep_wrong': 'nodes[PWM0].dep = &nodes[GPIO_PS]',
    'pmgr_not_syscon': 'nodes[PMGR].compat[1] = "simple-bus"',
    'pmgr_res': 'nodes[PMGR].size = 0x1000',
    'pmgr_val_bytes': 'pmgr_map.val_bytes = 1',
    'pins_missing': 'nodes[PINS].path = "/gone"',
    'pins_unavailable': 'nodes[PINS].available = false',
    'pins_compat': 'nodes[PINS].compat[0] = "apple,t7000-pinctrl"',
    'pins_res': 'nodes[PINS].base = 0x20f300000ULL',
    'pmic_none': 'nodes[PMIC].compat[0] = "apple,other"',
    'pmic_two': 'nodes[PMIC2].available = true',
    'pmic_compat': 'nodes[PMIC].compat[1] = "apple,other-pmic"',
    'pmic_reg': 'nodes[PMIC].reg[0] = 0x3d',
    'pins_unbound': 'pins_dev.dev.driver = NULL',
    'pmic_unbound': 'pmic_client.dev.driver = NULL',
    'pmic_val_bytes': 'pmic_map.val_bytes = 2',
}

PIN_EVENTS = ['lock 20e300000.pinctrl', 'ioremap 0x20e300000 0x1000',
              'readl 0x20e30003c', 'readl 0x20e30013c', 'readl 0x20e3001bc',
              'readl 0x20e3001dc', 'iounmap 0x20e300000', 'unlock 20e300000.pinctrl']
PMIC_EVENTS = ['lock 0-003c', 'read pmic 0x84', 'read pmic 0x85', 'read pmic 0x398',
               'unlock 0-003c']
SNAPSHOT = PIN_EVENTS + ['read pmgr 0x20070'] + PMIC_EVENTS
PMGR_READ = 'read pmgr 0x20070'
# (fault name, events of the acquiring call, events that undo it)
STEPS = (
    ('region', ['request_region 0x20a118000 0x1000 j72_touch_snap'],
     ['release_region 0x20a118000 0x1000']),
    ('register', ['register j72_touch_snap -1 0 0'], ['unregister j72_touch_snap']),
    ('genpd_add', [f'genpd_add {PWM0} j72_touch_snap'], ['genpd_remove j72_touch_snap']),
    (None, ['rpm_enable j72_touch_snap'], ['rpm_disable j72_touch_snap']),
    ('rpm_get', ['rpm_get j72_touch_snap'], ['rpm_put j72_touch_snap']),
    ('pmgr_on', [PMGR_READ], []),
    ('pwm_ioremap', ['ioremap 0x20a118000 0x1000'], ['iounmap 0x20a118000']),
)
PWM_READS = [f'readl 0x{0x20a118000 + 4 * i:x}' for i in range(9)]
STATE_CHANGES = ('request_region', 'register', 'genpd_add', 'rpm_')


def expected_events(failing=None):
    """Events for a run whose setup fails at step index `failing`, or succeeds."""
    acquired = STEPS if failing is None else STEPS[:failing]
    events = list(SNAPSHOT)
    for _, acquire, _ in acquired:
        events += acquire
    if failing is None:
        events += PWM_READS
    else:
        events += STEPS[failing][1]
    for _, _, undo in reversed(acquired):
        events += undo
    return events + [PMGR_READ]


class TouchSnapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        module = '\n'.join(line for line in cls.source.splitlines()
                           if not line.startswith('#include'))
        faults = '\n'.join(f'    if (fault("{name}")) {{ {body}; }}'
                           for name, body in NODE_FAULTS.items())
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-touch-snap-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'snap.c'
        cls.binary = Path(cls.directory.name) / 'snap'
        source.write_text(STUBS + '\n' + module + '\n' +
                          HARNESS.replace('    NODE_FAULTS', faults))
        result = subprocess.run(
            ['cc', '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-parameter',
             '-Werror', str(source), '-o', str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')

    def run_fixture(self, fault='none', error=0, pmgr_on=0xff, leaks_ok=False):
        result = subprocess.run([str(self.binary), fault, str(error), hex(pmgr_on)],
                                check=True, capture_output=True, text=True)
        lines = result.stdout.splitlines()
        self.assertEqual([line for line in lines if line.startswith('VIOLATION')], [])
        if fault != 'none':
            self.assertIn(f'FAULT_HIT {fault}', lines)
        if not leaks_ok:
            self.assertEqual([line for line in lines if line.startswith('LEAK')], [])
            self.assertIn('STATE registered=0 genpd=0', lines)
        events = [line.removeprefix('EVENT ') for line in lines if line.startswith('EVENT ')]
        logs = [line.removeprefix('LOG ') for line in lines if line.startswith('LOG ')]
        code = int(next(line for line in lines if line.startswith('RESULT ')).split()[1])
        return code, events, logs, lines

    def test_success_reads_once_toggles_pwm0_and_tears_down_in_reverse(self):
        code, events, logs, _ = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(events, expected_events())

    def test_success_logs_raw_values_decoded_pins_and_pwm_words(self):
        _, _, logs, _ = self.run_fixture(pmgr_on=0x100000ff)
        self.assertEqual(logs, [
            'j72_touch_snap: gpio15=0x00200203 gpio79=0x0000006e gpio111=0x00000211 '
            'gpio119=0x00000040 pmgr[0x20070]=0x00000300 ldo14_en[0x0084]=0x00 '
            'ldo18_en[0x0085]=0x11 ldo14_vset[0x0398]=0x14',
            'j72_touch_snap: gpio15 reset mode=1 data=1 periph=0 input_enable=1 lock=1',
            'j72_touch_snap: gpio79 enable_cs mode=7 data=0 periph=3 input_enable=0 lock=0',
            'j72_touch_snap: gpio111 atn mode=0 data=1 periph=0 input_enable=1 lock=0',
            'j72_touch_snap: gpio119 sync mode=0 data=0 periph=2 input_enable=0 lock=0',
            'j72_touch_snap: pmgr_on=0x100000ff pwm[0x00..0x20]=a0000000 a0000001 '
            'a0000002 a0000003 a0000004 a0000005 a0000006 a0000007 a0000008',
            'j72_touch_snap: cleanup=0 pmgr_on=0x100000ff pmgr_after=0x00000000'])

    def test_preflight_refusals_touch_no_hardware(self):
        codes = {'machine': -errno.ENODEV, 'release': -errno.ENODEV,
                 'syscon': -errno.ENXIO, 'pins_dev': -errno.ENODEV,
                 'pmic_none': -errno.ENODEV, 'pmic_client': -errno.ENODEV,
                 'pins_unbound': -errno.ENODEV}
        for fault in ['machine', 'syscon', 'pins_dev', 'pmic_client', *NODE_FAULTS]:
            if fault in ('pmic_unbound', 'pmic_val_bytes'):
                continue
            with self.subTest(fault=fault):
                code, events, logs, _ = self.run_fixture(fault, -errno.ENXIO)
                self.assertEqual(code, codes.get(fault, -errno.EINVAL))
                expected = (['lock 20e300000.pinctrl', 'unlock 20e300000.pinctrl']
                            if fault == 'pins_unbound' else [])
                self.assertEqual(events, expected)
                self.assertEqual(logs, [f'j72_touch_snap: refused/failed: {code}'])

    def test_snapshot_failures_stop_before_any_state_change(self):
        for fault, error, events in (
                ('pins_ioremap', -errno.ENOMEM, PIN_EVENTS[:2] + PIN_EVENTS[-1:]),
                ('pmgr_before', -errno.EIO, PIN_EVENTS + [PMGR_READ]),
                ('pmic_unbound', -errno.ENODEV,
                 PIN_EVENTS + [PMGR_READ, 'lock 0-003c', 'unlock 0-003c']),
                ('pmic_val_bytes', -errno.ENODEV,
                 PIN_EVENTS + [PMGR_READ, 'lock 0-003c', 'unlock 0-003c']),
                ('pmic0', -errno.EIO, SNAPSHOT[:-3] + ['unlock 0-003c']),
                ('pmic1', -errno.ENXIO, SNAPSHOT[:-2] + ['unlock 0-003c']),
                ('pmic2', -errno.EIO, SNAPSHOT)):
            with self.subTest(fault=fault):
                code, actual, logs, _ = self.run_fixture(fault, error)
                self.assertEqual(code, error)
                self.assertEqual(actual, events)
                self.assertFalse(any(e.startswith(STATE_CHANGES) for e in actual))
                self.assertEqual(logs, [f'j72_touch_snap: refused/failed: {error}'])

    def test_every_setup_failure_unwinds_what_was_acquired_in_reverse(self):
        for index, (fault, _, _) in enumerate(STEPS):
            if fault is None:
                continue
            with self.subTest(fault=fault):
                code, events, logs, _ = self.run_fixture(fault, -errno.ENXIO)
                expected = {'region': -errno.EBUSY, 'pwm_ioremap': -errno.ENOMEM}
                self.assertEqual(code, expected.get(fault, -errno.ENXIO))
                self.assertEqual(events, expected_events(index))
                self.assertFalse(any(e.startswith('readl 0x20a1') for e in events))
                self.assertIn('cleanup=0 ', logs[-2])

    def test_pwm_is_read_only_when_pmgr_reports_pwm0_active(self):
        for pmgr_on in (0x0, 0xf, 0x4f, 0xef, 0x7f0f):
            with self.subTest(pmgr_on=hex(pmgr_on)):
                code, events, _, _ = self.run_fixture(pmgr_on=pmgr_on)
                self.assertEqual(code, -errno.EIO)
                self.assertEqual(events, expected_events(5))
                self.assertNotIn('ioremap 0x20a118000 0x1000', events)
        code, events, _, _ = self.run_fixture(pmgr_on=0xf0)
        self.assertEqual(code, 0)
        self.assertEqual(events, expected_events())

    def test_teardown_failures_still_run_every_later_step(self):
        for fault, error, expected_code in (('rpm_put', -errno.EBUSY, -errno.EBUSY),
                                            ('rpm_put', 1, 0),
                                            ('pmgr_after', -errno.EIO, -errno.EIO)):
            with self.subTest(fault=fault, error=error):
                code, events, logs, _ = self.run_fixture(fault, error)
                self.assertEqual(code, expected_code)
                self.assertEqual(events, expected_events())
                self.assertIn(f'cleanup={expected_code} ', logs[-1 if not code else -2])

    def test_genpd_removal_failure_keeps_the_device_instead_of_freeing_it(self):
        code, events, logs, lines = self.run_fixture('genpd_remove', -errno.EAGAIN,
                                                     leaks_ok=True)
        self.assertEqual(code, -errno.EAGAIN)
        expected = expected_events()
        expected.remove('unregister j72_touch_snap')
        self.assertEqual(events, expected)
        self.assertIn('STATE registered=1 genpd=1', lines)
        self.assertEqual([line for line in lines if line.startswith('LEAK')], [])
        self.assertIn(f'cleanup={-errno.EAGAIN} ', logs[-2])

    def test_source_has_no_write_gpio_pinctrl_irq_or_spi_api(self):
        code = re.sub(r'/\*.*?\*/|//[^\n]*', '', self.source, flags=re.DOTALL)
        self.assertNotRegex(
            code,
            r'\b(write[bwlq]\w*|iowrite\w*|regmap_write\w*|regmap_update_bits\w*|'
            r'regmap_bulk_write|regmap_raw_write|regmap_multi_reg_write\w*|'
            r'regmap_field_write|regmap_set_bits|regmap_clear_bits|gpiod_\w+|'
            r'gpio_request\w*|gpiochip_request\w*|pinctrl_\w+|request_irq|'
            r'request_threaded_irq|devm_request\w*irq|spi_\w+|module_param\w*|'
            r'i2c_transfer|i2c_smbus_\w+|i2c_master_send)\b')
        self.assertEqual(code.count('regmap_read('), 4)
        self.assertEqual(code.count('readl('), 2)

    @unittest.skipUnless(BUILT.exists(), 'local build receipt is not present')
    def test_built_module_imports_only_allowed_symbols(self):
        imports = subprocess.run(['nm', '-u', str(BUILT)], capture_output=True,
                                 text=True, check=True).stdout.split()[1::2]
        self.assertEqual(sorted(imports), sorted(ALLOWED_IMPORTS))

    def test_preflight_and_makefile(self):
        self.assertIn('of_machine_is_compatible("apple,j72")', self.source)
        self.assertIn('strcmp(init_utsname()->release, "7.0.12-j72baseline")',
                      self.source)
        self.assertEqual((ROOT / 'probe/Makefile').read_text().splitlines().count(
            'obj-m += j72_touch_snap.o'), 1)


if __name__ == '__main__':
    unittest.main()
