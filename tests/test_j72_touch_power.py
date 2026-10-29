"""Compile the Stage B touch power module against recording host stubs.

The stubs model the pinctrl registers, the PMIC bytes and the PWM words, so
the tests can check the order of every access, the masks, the readbacks and
the safe unwind. They establish software behavior only, not hardware state.
"""

import errno
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'probe/j72_touch_power.c'
BUILT = ROOT / 'source/build/j72-touch-power-20261029/j72_touch_power.ko'
ALLOWED_IMPORTS = (
    '__ioremap_prot', '__of_parse_phandle_with_args', '__pm_runtime_disable',
    '__pm_runtime_resume', '__pm_runtime_suspend', '__release_region',
    '__request_region', '__stack_chk_fail', '_printk', 'alt_cb_patch_nops',
    'arm64_use_ng_mappings', 'dev_get_regmap', 'gpio_device_find_by_label',
    'gpio_device_get_chip', 'gpio_device_put', 'gpiochip_free_own_desc',
    'gpiochip_request_own_desc', 'gpiod_direction_output_raw',
    'gpiod_get_raw_value', 'i2c_find_device_by_fwnode', 'init_uts_ns',
    'iomem_resource', 'iounmap', 'memset', 'msleep', 'mutex_lock',
    'mutex_unlock', 'of_address_to_resource', 'of_count_phandle_with_args',
    'of_device_is_available', 'of_device_is_compatible',
    'of_find_compatible_node', 'of_find_device_by_node',
    'of_find_node_opts_by_path', 'of_genpd_add_device', 'of_get_parent',
    'of_machine_compatible_match', 'of_node_get', 'of_node_put',
    'of_property_count_elems_of_size', 'of_property_read_variable_u32_array',
    'platform_device_register_full', 'platform_device_unregister',
    'pm_genpd_remove_device', 'pm_runtime_enable', 'put_device',
    'regmap_get_val_bytes', 'regmap_read', 'regmap_update_bits_base',
    'strcmp', 'syscon_node_to_regmap', 'usleep_range_state',
)
PWM0 = '/soc/power-management@20e000000/power-controller@20070'
PINS = '20e300000.pinctrl'
PMIC = '0-003c'

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
#define KBUILD_MODNAME "j72_touch_power"
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

enum gpio_lookup_flags { GPIO_LOOKUP_FLAGS_DEFAULT = 0 };
enum gpiod_flags { GPIOD_ASIS = 0 };

/* Independent copies of the pinctrl field positions. */
#define P_DATA BIT(0)
#define P_MODE GENMASK(3, 1)
#define P_PERIPH GENMASK(6, 5)
#define P_INPUT_ENABLE BIT(9)
#define P_LOCK BIT(21)
#define P_OUT 1

static const char *fault_at = "none";
static int fault_err;

static bool fault(const char *name)
{
    if (strcmp(fault_at, name))
        return false;
    printf("FAULT_HIT %s\n", name);
    return true;
}

static bool fault_once(const char *name)
{
    if (strcmp(fault_at, name))
        return false;
    fault_at = "none";
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
struct gpio_device;
struct gpio_desc { struct gpio_device *gdev; unsigned int hwnum; bool requested; };
struct gpio_chip { struct gpio_device *gdev; };
struct gpio_device { const char *label; struct gpio_chip *chip; int refs; };

enum { PMGR, PWM0, SIO_P, GPIO_PS, PINS_N, PMIC_N, PMIC2, NNODES };
static struct device_node nodes[NNODES];
static struct new_utsname uts = { "7.0.12-j72baseline" };
static int bound;
static struct platform_device pins_dev = {
    { .name = "20e300000.pinctrl", .driver = &bound } };
static struct i2c_client pmic_client = { { .name = "0-003c", .driver = &bound } };
static struct platform_device power_dev = { { .name = "j72_touch_power" } };
static struct regmap pmgr_map = { "pmgr", 4 }, pmic_map = { "pmic", 1 };
static int genpd, registered, region, rpm_active, pmgr_active;
static int pmgr_reads, pdev_genpd;
static unsigned int pmgr_before_value = 0x200;
static unsigned int pmgr_on_override = 0xffffffff;
static unsigned int pmgr_after_override = 0x200;
static int force_atn_low;
static u32 pins_mmio[0x400], pwm_mmio[0x400];
static bool pins_mapped, pwm_mapped, pwm_started, pwm_stopping, pwm_delayed;
static bool pin_driven[4];
static int low79_verifies;
static unsigned char pmic_bytes[0x400] = {
    [0x0084] = 0x00, [0x0085] = 0x11, [0x0398] = 0x14,
};
static int pmic_read_count[0x400], pmic_update_count[0x400];

static struct gpio_chip gpio_chip;
static struct gpio_device gpio_dev;
static struct gpio_desc gpio_descs[4] = {
    { .hwnum = 15 }, { .hwnum = 79 }, { .hwnum = 111 }, { .hwnum = 119 },
};
static int gpio_held;

static const unsigned int stub_pins[4] = { 15, 79, 111, 119 };

static int pin_index(unsigned int pin)
{
    for (int i = 0; i < 4; i++)
        if (stub_pins[i] == pin)
            return i;
    return -1;
}

static bool rails_up(void)
{
    return (pmic_bytes[0x84] & 0x04) && (pmic_bytes[0x85] & 0x02);
}

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
    if (np != &nodes[PINS_N])
        violation("device lookup for an unexpected node");
    if (fault_once("pins_dev"))
        return NULL;
    pins_dev.dev.refs++;
    return &pins_dev;
}

static struct i2c_client *of_find_i2c_device_by_node(struct device_node *np)
{
    if (np != &nodes[PMIC_N])
        violation("I2C lookup for an unexpected node");
    if (fault_once("pmic_client"))
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
    return fault_once("syscon") ? ERR_PTR(fault_err) : &pmgr_map;
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
    printf("EVENT read %s 0x%x\n", map->name, reg);
    if (map == &pmgr_map) {
        if (reg != 0x20070 || pmgr_reads >= 4) {
            violation("unexpected PMGR read");
            return -EINVAL;
        }
        pmgr_reads++;
        if (pmgr_reads == 1) {
            if (fault_once("pmgr_before"))
                return fault_err;
            *val = pmgr_before_value;
        } else if (pmgr_reads == 2) {
            if (fault_once("pmgr_on"))
                return fault_err;
            *val = pmgr_on_override != 0xffffffff ? pmgr_on_override :
                   (rpm_active ? 0x2ff : 0x200);
            pmgr_active = rpm_active && ((*val >> 4) & 0xf) == 0xf;
        } else if (pwm_delayed && !pwm_stopping) {
            if (fault_once("pmgr_late"))
                return fault_err;
            *val = rpm_active ? 0x2ff : 0x200;
        } else {
            if (fault_once("pmgr_after"))
                return fault_err;
            *val = pmgr_after_override;
        }
        return 0;
    }
    if (map == &pmic_map) {
        if (!pmic_client.dev.locked || !pmic_client.dev.driver)
            violation("PMIC read without a locked, bound driver");
        if (reg >= 0x400) {
            violation("PMIC register out of range");
            return -EINVAL;
        }
        pmic_read_count[reg]++;
        if (reg == 0x84) {
            if (pmic_read_count[reg] == 1 && fault_once("pmic_read84_pre"))
                return fault_err;
            if (pmic_read_count[reg] == 2 && fault_once("pmic_read84_on"))
                return fault_err;
            if (pmic_read_count[reg] == 3 && fault_once("pmic_off_read84"))
                return fault_err;
        } else if (reg == 0x85) {
            if (pmic_read_count[reg] == 1 && fault_once("pmic_read85_pre"))
                return fault_err;
            if (pmic_read_count[reg] == 2 && fault_once("pmic_read85_on"))
                return fault_err;
            if (pmic_read_count[reg] == 3 && fault_once("pmic_off_read85"))
                return fault_err;
        } else if (reg == 0x398) {
            if (fault_once("pmic_read398"))
                return fault_err;
        }
        *val = pmic_bytes[reg];
        return 0;
    }
    violation("read from an unknown regmap");
    return -EINVAL;
}

static int regmap_update_bits(struct regmap *map, unsigned int reg,
                              unsigned int mask, unsigned int value)
{
    printf("EVENT update %s 0x%x 0x%x 0x%x\n", map->name, reg, mask, value);
    if (map != &pmic_map) {
        violation("update through an unknown regmap");
        return -EINVAL;
    }
    if (!pmic_client.dev.locked || !pmic_client.dev.driver)
        violation("PMIC update without a locked, bound driver");
    if (reg >= 0x400) {
        violation("PMIC register out of range");
        return -EINVAL;
    }
    pmic_update_count[reg]++;
    if (reg == 0x84) {
        if (pmic_update_count[reg] == 1 && fault_once("pmic_update84"))
            return fault_err;
        if (pmic_update_count[reg] == 2 && fault_once("pmic_off_update84"))
            return fault_err;
    } else if (reg == 0x85) {
        if (pmic_update_count[reg] == 1 && fault_once("pmic_update85"))
            return fault_err;
        if (pmic_update_count[reg] == 2 && fault_once("pmic_off_update85"))
            return fault_err;
    }
    pmic_bytes[reg] = (pmic_bytes[reg] & ~mask) | (value & mask);
    return 0;
}

static void *ioremap(u64 addr, size_t size)
{
    printf("EVENT ioremap 0x%llx 0x%zx\n", (unsigned long long)addr, size);
    if (addr == 0x20e300000ULL && size == 0x1000) {
        if (!pins_dev.dev.locked || !pins_dev.dev.driver)
            violation("pin block mapped without a locked, bound driver");
        if (fault_once("pins_ioremap"))
            return NULL;
        pins_mapped = true;
        return pins_mmio;
    }
    if (addr == 0x20a118000ULL && size == 0x1000) {
        if (!region || !rpm_active || !pmgr_active)
            violation("PWM mapped before ps_pwm0 was confirmed on");
        if (fault_once("pwm_ioremap"))
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
        int index;
        u32 value;

        offset = p - (unsigned char *)pins_mmio;
        if (!pins_dev.dev.locked || !pins_dev.dev.driver)
            violation("pin read without a locked, bound driver");
        printf("EVENT readl 0x%llx\n", 0x20e300000ULL + offset);
        value = pins_mmio[offset / 4];
        index = pin_index(offset / 4);
        if (index < 0)
            violation("read of an unexpected pin");
        if (index == 0 && pin_driven[0] && fault_once("verify15"))
            value ^= P_DATA;
        if (index == 1 && pin_driven[1] && !(pins_mmio[79] & P_DATA)) {
            low79_verifies++;
            if (low79_verifies == 1 && fault_once("verify79_low"))
                value ^= P_DATA;
            if (low79_verifies == 2 && fault_once("verify79_off"))
                value ^= P_DATA;
        }
        if (index == 1 && pin_driven[1] && (pins_mmio[79] & P_DATA) &&
            fault_once("verify79_high"))
            value ^= P_DATA;
        if (index == 3 && pin_driven[3] && fault_once("verify119"))
            value ^= P_DATA;
        return value;
    }
    if (pwm_mapped && p >= (unsigned char *)pwm_mmio &&
        p < (unsigned char *)pwm_mmio + 0x1000) {
        u32 value;

        offset = p - (unsigned char *)pwm_mmio;
        if (!region || !rpm_active || !pmgr_active)
            violation("PWM read while ps_pwm0 is not confirmed on");
        printf("EVENT readl 0x%llx\n", 0x20a118000ULL + offset);
        value = pwm_mmio[offset / 4];
        /* The update bit is a self-clearing strobe once the channel runs. */
        if (offset == 0x20 && pwm_started && !pwm_stopping)
            value &= ~0x2;
        if (offset == 0x10 && pwm_started && fault_once("pwm_high"))
            value++;
        if (offset == 0x14 && pwm_started && fault_once("pwm_low"))
            value++;
        if (offset == 0x20 && pwm_started && !pwm_stopping &&
            fault_once("pwm_ctrl"))
            value++;
        if (offset == 0x10 && pwm_delayed && fault_once("pwm_late_high"))
            value++;
        if (offset == 0x14 && pwm_delayed && fault_once("pwm_late_low"))
            value++;
        if (offset == 0x20 && pwm_delayed && !pwm_stopping &&
            fault_once("pwm_late_ctrl"))
            value++;
        if (offset == 0x20 && pwm_stopping && fault_once("pwm_stop"))
            value = 0x4003;
        return value;
    }
    violation("read outside a mapping");
    return 0;
}

static void writel(u32 value, volatile void *addr)
{
    const volatile unsigned char *p = addr;
    long offset;

    if (!pwm_mapped) {
        violation("PWM write outside the PWM mapping");
        return;
    }
    offset = p - (unsigned char *)pwm_mmio;
    printf("EVENT writel 0x%llx 0x%x\n", 0x20a118000ULL + offset, value);
    if (!region || !rpm_active || !pmgr_active)
        violation("PWM write while ps_pwm0 is not confirmed on");
    if (offset != 0x10 && offset != 0x14 && offset != 0x20) {
        violation("write to an unexpected PWM word");
        return;
    }
    if (offset == 0x20 && value == 0x4003)
        pwm_started = true;
    if (offset == 0x20 && value == 0x4000)
        pwm_stopping = true;
    pwm_mmio[offset / 4] = value;
}

static struct resource *request_mem_region(u64 start, u64 size, const char *name)
{
    static struct resource res;

    printf("EVENT request_region 0x%llx 0x%llx %s\n", (unsigned long long)start,
           (unsigned long long)size, name);
    if (fault_once("region"))
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
    if (fault_once("pdev"))
        return ERR_PTR(fault_err);
    registered = 1;
    return &power_dev;
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
    if (args->np != &nodes[PWM0] || args->args_count || dev != &power_dev.dev)
        violation("unexpected genpd attach");
    if (fault_once("genpd_add"))
        return fault_err;
    dev->pm_domain = &pdev_genpd;
    genpd = 1;
    return 0;
}

static int pm_genpd_remove_device(struct device *dev)
{
    printf("EVENT genpd_remove %s\n", dev->name);
    if (fault_once("genpd_remove"))
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
    if (fault_once("rpm_get"))
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
    if (fault_once("rpm_put"))
        return fault_err;
    return 0;
}

static struct gpio_device *gpio_device_find_by_label(const char *label)
{
    printf("EVENT gpio_find %s\n", label);
    if (strcmp(label, "20e300000.pinctrl")) {
        violation("GPIO lookup with an unexpected label");
        return NULL;
    }
    if (fault_once("gpio_find"))
        return NULL;
    gpio_dev.refs++;
    return &gpio_dev;
}

static struct gpio_chip *gpio_device_get_chip(struct gpio_device *gdev)
{
    if (gdev != &gpio_dev)
        violation("chip lookup for an unexpected device");
    return fault_once("gpio_chip") ? NULL : gdev->chip;
}

static void gpio_device_put(struct gpio_device *gdev)
{
    printf("EVENT gpio_put %s\n", gdev->label);
    if (--gdev->refs < 0)
        violation("GPIO device reference underflow");
}

static struct gpio_desc *gpiochip_request_own_desc(struct gpio_chip *gc,
                                                   unsigned int hwnum,
                                                   const char *label,
                                                   enum gpio_lookup_flags lflags,
                                                   enum gpiod_flags dflags)
{
    int index = pin_index(hwnum);
    const char *name = NULL;

    printf("EVENT gpio_request %u %s %d\n", hwnum, label, dflags);
    if (gc != &gpio_chip || index < 0 || lflags || dflags != GPIOD_ASIS)
        violation("unexpected own-descriptor request");
    if (hwnum == 15)
        name = "gpio_req15";
    else if (hwnum == 79)
        name = "gpio_req79";
    else if (hwnum == 111)
        name = "gpio_req111";
    else
        name = "gpio_req119";
    if (fault_once(name)) {
        printf("EVENT gpio_denied %u\n", hwnum);
        return ERR_PTR(fault_err);
    }
    if (gpio_descs[index].requested)
        violation("GPIO requested twice");
    gpio_descs[index].gdev = &gpio_dev;
    gpio_descs[index].requested = true;
    gpio_held++;
    return &gpio_descs[index];
}

static void gpiochip_free_own_desc(struct gpio_desc *desc)
{
    if (!desc || !desc->requested)
        violation("freeing an unrequested GPIO");
    printf("EVENT gpio_free %u\n", desc->hwnum);
    desc->requested = false;
    gpio_held--;
}

static int gpiod_direction_output_raw(struct gpio_desc *desc, int value)
{
    int index = pin_index(desc->hwnum);
    const char *name;

    printf("EVENT gpio_dir_out %u %d\n", desc->hwnum, value);
    if (!desc->requested)
        violation("direction call on an unrequested GPIO");
    if (desc->hwnum == 15 && value)
        violation("pin 15 driven high");
    if (desc->hwnum == 79 && value && !rails_up())
        violation("pin 79 driven high while a rail is off");
    if (desc->hwnum == 111) {
        violation("ATN pin driven");
        return -EINVAL;
    }
    if (desc->hwnum == 15)
        name = "dir15";
    else if (desc->hwnum == 79)
        name = value ? "dir79_high" : "dir79";
    else
        name = "dir119";
    if (fault_once(name))
        return fault_err;
    pins_mmio[desc->hwnum] =
        (pins_mmio[desc->hwnum] & ~(P_DATA | P_MODE | P_PERIPH)) |
        (P_OUT << 1) | !!value;
    pin_driven[index] = true;
    return 0;
}

static int gpiod_get_raw_value(const struct gpio_desc *desc)
{
    printf("EVENT gpio_get %u\n", desc->hwnum);
    if (desc->hwnum != 111)
        violation("reading a pin other than ATN");
    if (!desc->requested)
        violation("reading an unrequested GPIO");
    if (fault_once("atn"))
        return fault_err;
    if (force_atn_low)
        return 0;
    return !!(pins_mmio[111] & P_DATA);
}

static void msleep(unsigned int ms) { printf("EVENT msleep %u\n", ms); }
static void usleep_range(unsigned long min, unsigned long max)
{
    printf("EVENT usleep %lu %lu\n", min, max);
    if (min == 5000 && max == 6000 && pwm_started)
        pwm_delayed = true;
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
    nodes[PINS_N] = (struct device_node){
        .path = "/soc/pinctrl@20e300000",
        .compat = { "apple,s5l8960x-pinctrl", "apple,pinctrl" },
        .available = true, .base = 0x20e300000ULL, .size = 0x100000 };
    nodes[PMIC_N] = (struct device_node){
        .path = "/soc/i2c@20a110000/pmic@3c",
        .compat = { "apple,i2c-pmic", "apple,anya-pmic" },
        .available = true, .reg = { 0x3c }, .nreg = 1 };
    nodes[PMIC2] = (struct device_node){
        .path = "/soc/i2c@20a114000/pmic@3c",
        .compat = { "apple,i2c-pmic", "apple,anya-pmic" },
        .available = false, .reg = { 0x3c }, .nreg = 1 };
    gpio_dev.label = "20e300000.pinctrl";
    gpio_dev.chip = &gpio_chip;
    gpio_chip.gdev = &gpio_dev;
}

int main(int argc, char **argv)
{
    int result;

    if (argc != 5)
        return 2;
    fault_at = argv[1];
    fault_err = atoi(argv[2]);
    pmgr_on_override = (unsigned int)strtoul(argv[3], NULL, 0);
    force_atn_low = atoi(argv[4]);
    setup_nodes();
    pins_mmio[15] = 0x00072602;
    pins_mmio[79] = 0x00072203;
    pins_mmio[111] = 0x00070381;
    pins_mmio[119] = 0x00072602;
    NODE_FAULTS
    result = j72_touch_power_init();
    printf("RESULT %d\n", result);
    printf("STATE gpio15=0x%08x gpio79=0x%08x gpio111=0x%08x "
           "gpio119=0x%08x pmic84=0x%02x pmic85=0x%02x pmic398=0x%02x\n",
           pins_mmio[15], pins_mmio[79], pins_mmio[111], pins_mmio[119],
           pmic_bytes[0x84], pmic_bytes[0x85], pmic_bytes[0x398]);
    printf("STATE registered=%d genpd=%d region=%d rpm=%d gpio_refs=%d "
           "gpio_held=%d\n", registered, genpd, region, rpm_active,
           gpio_dev.refs, gpio_held);
    for (int i = 0; i < NNODES; i++)
        if (nodes[i].refs)
            printf("LEAK node %s %d\n", nodes[i].path, nodes[i].refs);
    if (pins_dev.dev.refs || pmic_client.dev.refs || pins_dev.dev.locked ||
        pmic_client.dev.locked || pins_mapped || pwm_mapped || rpm_active ||
        region || gpio_dev.refs || gpio_held)
        printf("LEAK device, lock, mapping, power, region or GPIO\n");
    return 0;
}
'''

# Each preflight fault mutates the fake device tree or the boot state.
NODE_FAULTS = {
    'node_release': 'uts.release = "7.0.12"',
    'node_pwm0_missing': 'nodes[PWM0].path = "/gone"',
    'node_pwm0_unavailable': 'nodes[PWM0].available = false',
    'node_pwm0_compat': 'nodes[PWM0].compat[0] = "apple,t8103-pmgr-pwrstate"',
    'node_pwm0_reg': 'nodes[PWM0].reg[0] = 0x20078',
    'node_pwm0_reg_size': 'nodes[PWM0].reg[1] = 8',
    'node_pwm0_no_dep': 'nodes[PWM0].dep = NULL',
    'node_pwm0_dep_wrong': 'nodes[PWM0].dep = &nodes[GPIO_PS]',
    'node_pmgr_not_syscon': 'nodes[PMGR].compat[1] = "simple-bus"',
    'node_pmgr_res': 'nodes[PMGR].size = 0x1000',
    'node_pmgr_val_bytes': 'pmgr_map.val_bytes = 1',
    'node_pins_missing': 'nodes[PINS_N].path = "/gone"',
    'node_pins_unavailable': 'nodes[PINS_N].available = false',
    'node_pins_compat': 'nodes[PINS_N].compat[0] = "apple,t7000-pinctrl"',
    'node_pins_res': 'nodes[PINS_N].base = 0x20f300000ULL',
    'node_pmic_none': 'nodes[PMIC_N].compat[0] = "apple,other"',
    'node_pmic_two': 'nodes[PMIC2].available = true',
    'node_pmic_compat': 'nodes[PMIC_N].compat[1] = "apple,other-pmic"',
    'node_pmic_reg': 'nodes[PMIC_N].reg[0] = 0x3d',
    'node_pins_unbound': 'pins_dev.dev.driver = NULL',
    'node_pmic_unbound': 'pmic_client.dev.driver = NULL',
    'node_pmic_val_bytes': 'pmic_map.val_bytes = 2',
    'node_pmgr_after': 'pmgr_after_override = 0x2ff',
    'boot15': 'pins_mmio[15] = 0x00072603',
    'boot79': 'pins_mmio[79] = 0x00072201',
    'rerun79': 'pins_mmio[79] = 0x00072202',
    'rerun111': 'pins_mmio[111] = 0x0000038f',
    'boot111': 'pins_mmio[111] = 0x00070380',
    'boot119': 'pins_mmio[119] = 0x00072603',
    'boot84': 'pmic_bytes[0x84] = 0x04',
    'boot85': 'pmic_bytes[0x85] = 0x13',
    'boot398': 'pmic_bytes[0x398] = 0x15',
    'boot_pmgr': 'pmgr_before_value = 0x2ff',
}


def pin_read(pin):
    return ['lock ' + PINS, 'ioremap 0x20e300000 0x1000',
            f'readl 0x{0x20e300000 + 4 * pin:x}', 'iounmap 0x20e300000',
            'unlock ' + PINS]


PREFLIGHT = (
    ['lock ' + PINS, 'ioremap 0x20e300000 0x1000',
     'readl 0x20e30003c', 'readl 0x20e30013c', 'readl 0x20e3001bc',
     'readl 0x20e3001dc', 'iounmap 0x20e300000', 'unlock ' + PINS,
     'read pmgr 0x20070', 'lock ' + PMIC, 'read pmic 0x84',
     'read pmic 0x85', 'read pmic 0x398', 'unlock ' + PMIC,
     'request_region 0x20a118000 0x1000 j72_touch_power',
     'gpio_find ' + PINS, 'gpio_request 15 j72_touch_power 0',
     'gpio_request 79 j72_touch_power 0',
     'gpio_request 111 j72_touch_power 0',
     'gpio_request 119 j72_touch_power 0'])
PWM_READS = [f'readl 0x{0x20a118000 + 4 * i:x}' for i in range(9)]
RAILS_ON = (
    ['lock ' + PMIC, 'update pmic 0x84 0x4 0x4', 'read pmic 0x84',
     'unlock ' + PMIC,
     'lock ' + PMIC, 'update pmic 0x85 0x2 0x2', 'read pmic 0x85',
     'unlock ' + PMIC])
RAILS_OFF = (
    ['lock ' + PMIC, 'update pmic 0x85 0x2 0x0', 'read pmic 0x85',
     'unlock ' + PMIC,
     'lock ' + PMIC, 'update pmic 0x84 0x4 0x0', 'read pmic 0x84',
     'unlock ' + PMIC])
SUCCESS = (
    PREFLIGHT +
    ['gpio_dir_out 15 0'] + pin_read(15) +
    ['gpio_dir_out 79 0'] + pin_read(79) +
    ['gpio_dir_out 119 0'] + pin_read(119) +
    ['usleep 5000 6000', 'gpio_get 111',
     'register j72_touch_power -1 0 0',
     f'genpd_add {PWM0} j72_touch_power',
     'rpm_enable j72_touch_power', 'rpm_get j72_touch_power',
     'read pmgr 0x20070', 'ioremap 0x20a118000 0x1000'] + PWM_READS +
    RAILS_ON +
    [x for _ in range(5) for x in ('gpio_get 111', 'usleep 1000 1500')] +
    ['gpio_dir_out 79 1'] + pin_read(79) +
    ['writel 0x20a118010 0x16e', 'writel 0x20a118014 0x16e',
     'writel 0x20a118020 0x4003', 'readl 0x20a118010',
     'readl 0x20a118014', 'readl 0x20a118020',
     'usleep 5000 6000', 'readl 0x20a118010', 'readl 0x20a118014',
     'readl 0x20a118020', 'read pmgr 0x20070'] +
    [x for _ in range(15) for x in ('gpio_get 111', 'usleep 1000 1500')] +
    ['gpio_get 111', 'msleep 100', 'gpio_get 111',
     'gpio_dir_out 15 0'] + pin_read(15) +
    ['writel 0x20a118020 0x4000', 'readl 0x20a118020',
     'iounmap 0x20a118000', 'rpm_put j72_touch_power',
     'rpm_disable j72_touch_power', 'genpd_remove j72_touch_power',
     'unregister j72_touch_power', 'release_region 0x20a118000 0x1000',
     'read pmgr 0x20070', 'gpio_dir_out 79 0'] + pin_read(79) +
    RAILS_OFF +
    ['gpio_get 111', 'msleep 200'] + pin_read(15) + pin_read(79) +
    pin_read(119) +
    ['gpio_get 111',
     'gpio_free 119', 'gpio_free 111', 'gpio_free 79', 'gpio_free 15',
     'gpio_put ' + PINS])

SAFE_FINAL = {'gpio15': 0x00072602, 'gpio79': 0x00072202,
              'gpio111': 0x00070381, 'gpio119': 0x00072602,
              'pmic84': 0x00, 'pmic85': 0x11, 'pmic398': 0x14}


class TouchPowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text()
        module = '\n'.join(line for line in cls.source.splitlines()
                           if not line.startswith('#include'))
        faults = '\n'.join(f'    if (fault("{name}")) {{ {body}; }}'
                           for name, body in NODE_FAULTS.items())
        cls.directory = tempfile.TemporaryDirectory(prefix='j72-touch-power-')
        cls.addClassCleanup(cls.directory.cleanup)
        source = Path(cls.directory.name) / 'power.c'
        cls.binary = Path(cls.directory.name) / 'power'
        source.write_text(STUBS + '\n' + module + '\n' +
                          HARNESS.replace('    NODE_FAULTS', faults))
        result = subprocess.run(
            ['cc', '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-parameter',
             '-Werror', str(source), '-o', str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(f'host compilation failed:\n{result.stdout}{result.stderr}')

    def run_fixture(self, fault='none', error=0, pmgr_on=0xffffffff,
                    atn_low=0, leaks_ok=False):
        result = subprocess.run(
            [str(self.binary), fault, str(error), hex(pmgr_on), str(atn_low)],
            check=True, capture_output=True, text=True)
        lines = result.stdout.splitlines()
        self.assertEqual([line for line in lines if line.startswith('VIOLATION')], [])
        if fault != 'none':
            self.assertIn(f'FAULT_HIT {fault}', lines)
        if not leaks_ok:
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

    def check_safety(self, events, lines):
        r84 = r85 = False
        for event in events:
            if event == 'gpio_dir_out 15 1':
                self.fail('pin 15 was driven high')
            if event.startswith('gpio_dir_out 111'):
                self.fail('ATN pin was driven')
            if event == 'gpio_dir_out 79 1':
                self.assertTrue(r84 and r85,
                                'pin 79 went high while a rail was off')
            if event == 'update pmic 0x84 0x4 0x4':
                r84 = True
            elif event == 'update pmic 0x84 0x4 0x0':
                r84 = False
            elif event == 'update pmic 0x85 0x2 0x2':
                r85 = True
            elif event == 'update pmic 0x85 0x2 0x0':
                r85 = False
        return r84, r85

    def test_success_order_and_state(self):
        code, events, _, lines = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(events, SUCCESS)
        self.assertEqual(self.state(lines), SAFE_FINAL)
        self.check_safety(events, lines)

    def test_success_logs_masks_readbacks_and_no_transfer(self):
        _, events, logs, lines = self.run_fixture()
        text = '\n'.join(logs)
        for needle in (
                'gpio15 reset raw=0x00072602 mode=1 data=0 periph=0',
                'gpio79 enable_cs raw=0x00072203 mode=1 data=1 periph=0',
                'gpio111 atn raw=0x00070381 mode=0 data=1 periph=0',
                'gpio119 sync raw=0x00072602 mode=1 data=0 periph=0',
                'ldo14_en[0x0084]=0x00', 'ldo18_en[0x0085]=0x11',
                'ldo14_vset[0x0398]=0x14',
                'pmic[0x0084] mask=0x04 value=0x04 read=0x04',
                'pmic[0x0085] mask=0x02 value=0x02 read=0x13',
                'pmic[0x0085] mask=0x02 value=0x00 read=0x11',
                'pmic[0x0084] mask=0x04 value=0x00 read=0x00',
                'pwm readback high=0x0000016e low=0x0000016e '
                'ctrl=0x00004001 want=0x0000016e/0x0000016e/0x00004001',
                'pwm late high=0x0000016e low=0x0000016e '
                'ctrl=0x00004001 pmgr=0x000002ff',
                'pwm high=366 low=366 ctrl=0x4001; reset still asserted',
                'pwm stop readback ctrl=0x00004000 want=0x00004000',
                'stage B complete; no transfer made',
                'gpio79 enable_cs raw=0x00072202 mode=1 data=0 periph=0'):
            self.assertIn(needle, text)
        self.assertEqual(text.count('stage B complete; no transfer made'), 1)
        self.assertEqual(text.count('atn phase='), 25)
        self.assertEqual(self.state(lines), SAFE_FINAL)
        self.check_safety(events, lines)

    def test_atn_low_is_logged_and_does_not_fail(self):
        code, events, logs, _ = self.run_fixture(atn_low=1)
        self.assertEqual(code, 0)
        self.assertIn('atn phase=pins_low value=0', '\n'.join(logs))
        self.assertGreaterEqual('\n'.join(logs).count('value=0'), 20)

    def test_preflight_refusals_touch_no_hardware(self):
        initial = {**SAFE_FINAL, 'gpio79': 0x00072203}
        mutations = {
            'boot15': ('gpio15', 0x00072603),
            'boot79': ('gpio79', 0x00072201),
            'boot111': ('gpio111', 0x00070380),
            'boot119': ('gpio119', 0x00072603),
            'boot84': ('pmic84', 0x04),
            'boot85': ('pmic85', 0x13),
            'boot398': ('pmic398', 0x15),
        }
        for fault in ['machine', *NODE_FAULTS]:
            if fault in ('node_pmgr_after', 'rerun79', 'rerun111'):
                continue
            with self.subTest(fault=fault):
                code, events, _, lines = self.run_fixture(fault, -errno.ENXIO)
                self.assertNotEqual(code, 0)
                self.assertFalse(any(e.startswith(('gpio_dir_out', 'gpio_request',
                                                   'register', 'genpd_add', 'rpm_',
                                                   'writel', 'update pmic'))
                                     for e in events))
                expected = dict(initial)
                if fault in mutations:
                    key, value = mutations[fault]
                    expected[key] = value
                self.assertEqual(self.state(lines), expected)
                self.check_safety(events, lines)

    def test_boot_state_mismatches_refuse_before_any_gpio_or_region(self):
        for fault, key, value in (
                ('boot15', 'gpio15', 0x00072603),
                ('boot79', 'gpio79', 0x00072201),
                ('boot111', 'gpio111', 0x00070380),
                ('boot119', 'gpio119', 0x00072603),
                ('boot84', 'pmic84', 0x04),
                ('boot85', 'pmic85', 0x13),
                ('boot398', 'pmic398', 0x15),
                ('boot_pmgr', 'pmgr', 0x2ff)):
            with self.subTest(fault=fault):
                code, events, _, lines = self.run_fixture(fault)
                self.assertNotEqual(code, 0)
                self.assertEqual(events, PREFLIGHT[:14])
                self.assertFalse(any(e.startswith(('gpio_request', 'gpio_dir_out',
                                                   'register', 'update pmic'))
                                     for e in events))
                if key != 'pmgr':
                    self.assertEqual(self.state(lines)[key], value)

    def test_rerun_accepts_pin_79_left_low_by_a_previous_run(self):
        code, events, _, lines = self.run_fixture('rerun79')
        self.assertEqual(code, 0)
        self.assertEqual(events, SUCCESS)
        self.assertEqual(self.state(lines), SAFE_FINAL)

    def test_rerun_accepts_pin_111_left_by_a_freed_irq(self):
        code, events, _, _ = self.run_fixture('rerun111')
        self.assertEqual(code, 0)
        self.assertEqual(events, SUCCESS)

    def test_pwm0_must_be_off_before_the_run(self):
        code, events, _, _ = self.run_fixture('boot_pmgr')
        self.assertNotEqual(code, 0)
        self.assertNotIn('ioremap 0x20a118000 0x1000', events)
        code, events, _, _ = self.run_fixture(pmgr_on=0x200)
        self.assertNotEqual(code, 0)
        self.assertIn('read pmgr 0x20070', events)
        self.assertNotIn('ioremap 0x20a118000 0x1000', events)
        self.assertNotIn('writel 0x20a118010 0x16e', events)

    def test_every_operation_fault_unwinds_without_leaks(self):
        faults = [
            'region', 'gpio_find', 'gpio_chip', 'gpio_req15', 'gpio_req79',
            'gpio_req111', 'gpio_req119', 'dir15', 'dir79', 'dir119',
            'verify15', 'verify79_low', 'verify119', 'atn', 'pdev',
            'genpd_add', 'rpm_get', 'pmgr_on', 'pwm_ioremap',
            'pmic_update84', 'pmic_read84_on', 'pmic_update85',
            'pmic_read85_on', 'pwm_high', 'pwm_low', 'pwm_ctrl',
            'pwm_late_high', 'pwm_late_low', 'pwm_late_ctrl', 'pmgr_late',
            'verify79_high',
        ]
        for fault in faults:
            with self.subTest(fault=fault):
                code, events, _, lines = self.run_fixture(fault, -errno.ENXIO)
                self.assertNotEqual(code, 0)
                self.check_safety(events, lines)
                state = self.state(lines)
                self.assertEqual(state['pmic84'] & 0x04, 0)
                self.assertEqual(state['pmic85'] & 0x02, 0)
                if ('gpio_request 79 j72_touch_power 0' in events and
                        'gpio_denied 79' not in events):
                    self.assertEqual(state['gpio79'] & 1, 0)
                if fault != 'region':
                    self.assertIn('release_region 0x20a118000 0x1000', events)
                for pin in (15, 79, 111, 119):
                    if (f'gpio_request {pin} j72_touch_power 0' in events and
                            f'gpio_denied {pin}' not in events):
                        self.assertIn(f'gpio_free {pin}', events)

    def test_readback_mismatch_aborts_before_the_next_unsafe_step(self):
        code, events, _, _ = self.run_fixture('verify15', -errno.ENXIO)
        self.assertEqual(code, -errno.EIO)
        self.assertNotIn('gpio_dir_out 79 1', events)
        self.assertNotIn('gpio_dir_out 119 0', events)
        code, events, _, _ = self.run_fixture('verify79_low', -errno.ENXIO)
        self.assertEqual(code, -errno.EIO)
        self.assertNotIn('gpio_dir_out 79 1', events)
        self.assertNotIn('gpio_dir_out 119 0', events)
        code, events, _, _ = self.run_fixture('pwm_high', -errno.ENXIO)
        self.assertEqual(code, -errno.EIO)
        self.assertNotIn('gpio_get 111', events[events.index('writel 0x20a118020 0x4003') + 1:])
        for late in ('pwm_late_high', 'pwm_late_low', 'pwm_late_ctrl'):
            code, events, _, _ = self.run_fixture(late, -errno.ENXIO)
            self.assertEqual(code, -errno.EIO)
            self.assertNotIn('gpio_get 111',
                             events[events.index('writel 0x20a118020 0x4003') + 1:])

    def test_teardown_failures_still_attempt_every_step(self):
        for fault, error in (('rpm_put', -errno.EBUSY),
                             ('pmgr_after', -errno.EIO),
                             ('verify79_off', -errno.ENXIO),
                             ('pmic_off_update85', -errno.ENXIO),
                             ('pmic_off_read85', -errno.ENXIO),
                             ('pmic_off_update84', -errno.ENXIO),
                             ('pmic_off_read84', -errno.ENXIO)):
            with self.subTest(fault=fault):
                code, events, _, lines = self.run_fixture(fault, error)
                self.assertNotEqual(code, 0)
                self.assertIn('gpio_dir_out 79 0', events)
                if fault in ('pmic_off_update85', 'pmic_off_read85'):
                    self.assertIn('update pmic 0x84 0x4 0x0', events)
                if fault in ('pmic_off_update84', 'pmic_off_read84'):
                    self.assertIn('update pmic 0x85 0x2 0x0', events)
                if fault not in ('pmic_off_update85', 'pmic_off_read85',
                                 'pmic_off_update84', 'pmic_off_read84'):
                    self.assertEqual(self.state(lines)['pmic84'] & 0x04, 0)
                    self.assertEqual(self.state(lines)['pmic85'] & 0x02, 0)
                self.check_safety(events, lines)

    def test_genpd_removal_failure_keeps_the_device_instead_of_freeing_it(self):
        code, events, _, lines = self.run_fixture('genpd_remove', -errno.EAGAIN,
                                                  leaks_ok=True)
        self.assertEqual(code, -errno.EAGAIN)
        self.assertNotIn('unregister j72_touch_power', events)
        self.assertTrue(any(line.startswith('STATE registered=1 genpd=1')
                            for line in lines))
        self.assertEqual([line for line in lines if line.startswith('LEAK')], [])
        self.assertEqual(self.state(lines)['gpio79'] & 1, 0)
        self.check_safety(events, lines)

    def test_sleeps_meet_the_minimums(self):
        _, events, _, _ = self.run_fixture()
        index = events.index('usleep 5000 6000')
        self.assertEqual(events[index - 1], 'unlock ' + PINS)
        self.assertEqual(events[index + 1], 'gpio_get 111')
        self.assertEqual(events.count('usleep 1000 1500'), 20)
        self.assertEqual(events.count('usleep 5000 6000'), 2)
        self.assertEqual(events.count('msleep 100'), 1)
        self.assertEqual(events.count('msleep 200'), 1)
        self.assertNotIn('msleep 1', events)
        self.assertNotIn('msleep 5', events)

    def test_source_has_no_write_rail_or_transfer_api_outside_the_sequence(self):
        code = re.sub(r'/\*.*?\*/|//[^\n]*', '', self.source, flags=re.DOTALL)
        self.assertNotRegex(
            code,
            r'\b(iowrite\w*|regmap_write\w*|regmap_bulk_write|regmap_raw_write|'
            r'regmap_multi_reg_write\w*|regmap_field_write|regmap_set_bits|'
            r'regmap_clear_bits|gpiod_direction_input|gpiod_set_\w+|'
            r'gpio_request\w*|gpiod_request\w*|pinctrl_\w+|request_irq|'
            r'request_threaded_irq|devm_request\w*irq|spi_\w+|module_param\w*|'
            r'i2c_transfer|i2c_smbus_\w+|i2c_master_send)\b')
        self.assertEqual(code.count('writel('), 4)
        self.assertEqual(code.count('gpiod_direction_output_raw('), 1)
        self.assertEqual(code.count('gpiod_get_raw_value('), 1)
        self.assertEqual(code.count('gpiochip_request_own_desc('), 1)
        self.assertEqual(code.count('gpiochip_free_own_desc('), 1)
        self.assertNotIn('0x07, 0x07', code)
        self.assertNotIn('0x03, 0x03', code)
        self.assertIn('0x04, 0x04, 0x04', code)
        self.assertIn('0x02, 0x02, 0x13', code)
        self.assertIn('0x02, 0x00, 0x11', code)
        self.assertIn('0x04, 0x00, 0x00', code)
        self.assertIn('gpiod_direction_output_raw(tp->gpio[index], value)', code)
        self.assertNotIn('gpiod_direction_input', code)
        self.assertNotIn('gpiod_set_raw_value', code)

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
            'obj-m += j72_touch_power.o'), 1)


if __name__ == '__main__':
    unittest.main()
