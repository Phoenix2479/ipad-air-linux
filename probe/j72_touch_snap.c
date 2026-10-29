// SPDX-License-Identifier: GPL-2.0
#include <linux/bitfield.h>
#include <linux/device.h>
#include <linux/i2c.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/mfd/syscon.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/pm_domain.h>
#include <linux/pm_runtime.h>
#include <linux/regmap.h>
#include <linux/utsname.h>

#define PMGR_PATH "/soc/power-management@20e000000"
#define PWM0_PATH PMGR_PATH "/power-controller@20070"
#define SIO_P_PATH PMGR_PATH "/power-controller@20110"
#define PINS_PATH "/soc/pinctrl@20e300000"
#define PMGR_BASE 0x20e000000ULL
#define PMGR_SIZE 0x24000
#define PMGR_PWM0 0x20070
#define PS_ACTUAL GENMASK(7, 4)
#define PS_ACTIVE 0xf
#define PINS_BASE 0x20e300000ULL
#define PINS_SIZE 0x100000
#define PINS_MAP 0x1000
#define PWM_BASE 0x20a118000ULL
#define PWM_SIZE 0x1000
#define PWM_WORDS 9
#define PMIC_ADDR 0x3c

/* Field layout from drivers/pinctrl/pinctrl-apple-gpio.c. */
#define REG_GPIO(x) (4 * (x))
#define REG_GPIOx_DATA BIT(0)
#define REG_GPIOx_MODE GENMASK(3, 1)
#define REG_GPIOx_PERIPH GENMASK(6, 5)
#define REG_GPIOx_INPUT_ENABLE BIT(9)
#define REG_GPIOx_LOCK BIT(21)

struct snap_reg {
	const char *name;
	unsigned int reg;
};

static const struct snap_reg pins[] = {
	{ "reset", 15 },
	{ "enable_cs", 79 },
	{ "atn", 111 },
	{ "sync", 119 },
};

static const struct snap_reg pmic_regs[] = {
	{ "ldo14_en", 0x0084 },
	{ "ldo18_en", 0x0085 },
	{ "ldo14_vset", 0x0398 },
};

enum {
	HELD_REGION = BIT(0),
	HELD_PDEV = BIT(1),
	HELD_GENPD = BIT(2),
	HELD_RPM = BIT(3),
	HELD_REF = BIT(4),
	HELD_PWM = BIT(5),
};

struct snap {
	struct device_node *pwm0, *pmgr, *pins_np;
	struct platform_device *pins_dev, *pdev;
	struct device *pmic;
	struct regmap *pmgr_map;
	void __iomem *pwm;
	unsigned int held;
	u32 pin[ARRAY_SIZE(pins)];
	unsigned int pmic_val[ARRAY_SIZE(pmic_regs)];
	unsigned int pmgr_before, pmgr_on, pmgr_after;
	u32 pwm_val[PWM_WORDS];
};

static bool power_node(struct device_node *np, const char *path, u32 offset)
{
	struct device_node *expected = of_find_node_by_path(path);
	u32 reg[2];
	bool valid;

	valid = expected && expected == np && of_device_is_available(np) &&
		of_device_is_compatible(np, "apple,s5l8960x-pmgr-pwrstate") &&
		of_property_count_u32_elems(np, "reg") == 2 &&
		!of_property_read_u32_array(np, "reg", reg, 2) &&
		reg[0] == offset && reg[1] == 4;
	of_node_put(expected);
	return valid;
}

static struct device_node *find_pmic(void)
{
	struct device_node *np = NULL, *it;
	u32 addr;

	for_each_compatible_node(it, NULL, "apple,i2c-pmic") {
		if (!of_device_is_available(it))
			continue;
		if (np) {
			of_node_put(it);
			of_node_put(np);
			return ERR_PTR(-EINVAL);
		}
		np = of_node_get(it);
	}
	if (!np)
		return ERR_PTR(-ENODEV);
	if (!of_device_is_compatible(np, "apple,anya-pmic") ||
	    of_property_read_u32(np, "reg", &addr) || addr != PMIC_ADDR) {
		of_node_put(np);
		return ERR_PTR(-EINVAL);
	}
	return np;
}

static int preflight(struct snap *s)
{
	struct of_phandle_args dep = {};
	struct i2c_client *client;
	struct device_node *np;
	struct resource res;
	bool valid;

	if (!of_machine_is_compatible("apple,j72") ||
	    strcmp(init_utsname()->release, "7.0.12-j72baseline"))
		return -ENODEV;
	s->pwm0 = of_find_node_by_path(PWM0_PATH);
	if (!power_node(s->pwm0, PWM0_PATH, PMGR_PWM0) ||
	    of_count_phandle_with_args(s->pwm0, "power-domains",
				      "#power-domain-cells") != 1 ||
	    of_parse_phandle_with_args(s->pwm0, "power-domains",
				       "#power-domain-cells", 0, &dep))
		return -EINVAL;
	valid = !dep.args_count && power_node(dep.np, SIO_P_PATH, 0x20110);
	of_node_put(dep.np);
	if (!valid)
		return -EINVAL;
	s->pmgr = of_get_parent(s->pwm0);
	if (!s->pmgr || !of_device_is_compatible(s->pmgr, "syscon") ||
	    of_address_to_resource(s->pmgr, 0, &res) ||
	    res.start != PMGR_BASE || resource_size(&res) != PMGR_SIZE)
		return -EINVAL;
	s->pmgr_map = syscon_node_to_regmap(s->pmgr);
	if (IS_ERR(s->pmgr_map))
		return PTR_ERR(s->pmgr_map);
	if (regmap_get_val_bytes(s->pmgr_map) != 4)
		return -EINVAL;
	s->pins_np = of_find_node_by_path(PINS_PATH);
	if (!s->pins_np || !of_device_is_available(s->pins_np) ||
	    !of_device_is_compatible(s->pins_np, "apple,s5l8960x-pinctrl") ||
	    of_address_to_resource(s->pins_np, 0, &res) ||
	    res.start != PINS_BASE || resource_size(&res) != PINS_SIZE)
		return -EINVAL;
	s->pins_dev = of_find_device_by_node(s->pins_np);
	if (!s->pins_dev)
		return -ENODEV;
	np = find_pmic();
	if (IS_ERR(np))
		return PTR_ERR(np);
	client = of_find_i2c_device_by_node(np);
	of_node_put(np);
	if (!client)
		return -ENODEV;
	s->pmic = &client->dev;
	return 0;
}

static int read_pins(struct snap *s)
{
	struct device *dev = &s->pins_dev->dev;
	void __iomem *base = NULL;
	int i, ret = -ENODEV;

	/* The bound pinctrl driver keeps ps_gpio on and owns this region. */
	device_lock(dev);
	if (dev->driver) {
		base = ioremap(PINS_BASE, PINS_MAP);
		ret = base ? 0 : -ENOMEM;
	}
	if (base) {
		for (i = 0; i < ARRAY_SIZE(pins); i++)
			s->pin[i] = readl(base + REG_GPIO(pins[i].reg));
		iounmap(base);
	}
	device_unlock(dev);
	return ret;
}

static int read_pmic(struct snap *s)
{
	struct regmap *map;
	int i, ret = -ENODEV;

	/* Keep the PMIC driver bound while its regmap is in use. */
	device_lock(s->pmic);
	map = s->pmic->driver ? dev_get_regmap(s->pmic, NULL) : NULL;
	if (map && regmap_get_val_bytes(map) == 1) {
		for (i = 0; i < ARRAY_SIZE(pmic_regs); i++) {
			ret = regmap_read(map, pmic_regs[i].reg, &s->pmic_val[i]);
			if (ret)
				break;
		}
	}
	device_unlock(s->pmic);
	return ret;
}

static int snapshot(struct snap *s)
{
	int i, ret;

	ret = read_pins(s);
	if (!ret)
		ret = regmap_read(s->pmgr_map, PMGR_PWM0, &s->pmgr_before);
	if (!ret)
		ret = read_pmic(s);
	if (ret)
		return ret;
	pr_info(KBUILD_MODNAME
		": gpio%u=0x%08x gpio%u=0x%08x gpio%u=0x%08x gpio%u=0x%08x pmgr[0x%05x]=0x%08x %s[0x%04x]=0x%02x %s[0x%04x]=0x%02x %s[0x%04x]=0x%02x\n",
		pins[0].reg, s->pin[0], pins[1].reg, s->pin[1],
		pins[2].reg, s->pin[2], pins[3].reg, s->pin[3],
		PMGR_PWM0, s->pmgr_before,
		pmic_regs[0].name, pmic_regs[0].reg, s->pmic_val[0],
		pmic_regs[1].name, pmic_regs[1].reg, s->pmic_val[1],
		pmic_regs[2].name, pmic_regs[2].reg, s->pmic_val[2]);
	for (i = 0; i < ARRAY_SIZE(pins); i++)
		pr_info(KBUILD_MODNAME
			": gpio%u %s mode=%lu data=%lu periph=%lu input_enable=%lu lock=%lu\n",
			pins[i].reg, pins[i].name,
			FIELD_GET(REG_GPIOx_MODE, s->pin[i]),
			FIELD_GET(REG_GPIOx_DATA, s->pin[i]),
			FIELD_GET(REG_GPIOx_PERIPH, s->pin[i]),
			FIELD_GET(REG_GPIOx_INPUT_ENABLE, s->pin[i]),
			FIELD_GET(REG_GPIOx_LOCK, s->pin[i]));
	return 0;
}

static int read_pwm(struct snap *s)
{
	struct of_phandle_args pd = { .np = s->pwm0 };
	int i, ret;

	if (!request_mem_region(PWM_BASE, PWM_SIZE, KBUILD_MODNAME))
		return -EBUSY;
	s->held |= HELD_REGION;
	s->pdev = platform_device_register_simple(KBUILD_MODNAME,
						  PLATFORM_DEVID_NONE, NULL, 0);
	if (IS_ERR(s->pdev))
		return PTR_ERR(s->pdev);
	s->held |= HELD_PDEV;
	ret = of_genpd_add_device(&pd, &s->pdev->dev);
	if (ret)
		return ret;
	s->held |= HELD_GENPD;
	pm_runtime_enable(&s->pdev->dev);
	s->held |= HELD_RPM;
	ret = pm_runtime_resume_and_get(&s->pdev->dev);
	if (ret)
		return ret;
	s->held |= HELD_REF;
	ret = regmap_read(s->pmgr_map, PMGR_PWM0, &s->pmgr_on);
	if (ret)
		return ret;
	if (FIELD_GET(PS_ACTUAL, s->pmgr_on) != PS_ACTIVE)
		return -EIO;
	s->pwm = ioremap(PWM_BASE, PWM_SIZE);
	if (!s->pwm)
		return -ENOMEM;
	s->held |= HELD_PWM;
	for (i = 0; i < PWM_WORDS; i++)
		s->pwm_val[i] = readl(s->pwm + 4 * i);
	return 0;
}

static int release_pwm(struct snap *s)
{
	struct device *dev = s->held & HELD_PDEV ? &s->pdev->dev : NULL;
	int ret = 0, err;

	if (s->held & HELD_PWM)
		iounmap(s->pwm);
	/* The put drops the reference even when suspending fails. */
	if (s->held & HELD_REF) {
		err = pm_runtime_put_sync_suspend(dev);
		if (err < 0 && !ret)
			ret = err;
	}
	if (s->held & HELD_RPM)
		pm_runtime_disable(dev);
	if (s->held & HELD_GENPD) {
		err = pm_genpd_remove_device(dev);
		if (err && !ret)
			ret = err;
		if (!err)
			s->held &= ~HELD_GENPD;
	}
	/* Freeing a device that genpd still lists would leave it a dangling pointer. */
	if ((s->held & (HELD_PDEV | HELD_GENPD)) == HELD_PDEV)
		platform_device_unregister(s->pdev);
	if (s->held & HELD_REGION)
		release_mem_region(PWM_BASE, PWM_SIZE);
	err = regmap_read(s->pmgr_map, PMGR_PWM0, &s->pmgr_after);
	if (err && !ret)
		ret = err;
	return ret;
}

static void put_refs(struct snap *s)
{
	put_device(s->pmic);
	if (s->pins_dev)
		put_device(&s->pins_dev->dev);
	of_node_put(s->pins_np);
	of_node_put(s->pmgr);
	of_node_put(s->pwm0);
}

static int __init j72_touch_snap_init(void)
{
	struct snap s = {};
	int ret, cleanup;

	ret = preflight(&s);
	if (!ret)
		ret = snapshot(&s);
	if (!ret) {
		ret = read_pwm(&s);
		if (!ret)
			pr_info(KBUILD_MODNAME
				": pmgr_on=0x%08x pwm[0x00..0x20]=%08x %08x %08x %08x %08x %08x %08x %08x %08x\n",
				s.pmgr_on, s.pwm_val[0], s.pwm_val[1],
				s.pwm_val[2], s.pwm_val[3], s.pwm_val[4],
				s.pwm_val[5], s.pwm_val[6], s.pwm_val[7],
				s.pwm_val[8]);
		cleanup = release_pwm(&s);
		pr_info(KBUILD_MODNAME ": cleanup=%d pmgr_on=0x%08x pmgr_after=0x%08x\n",
			cleanup, s.pmgr_on, s.pmgr_after);
		if (!ret)
			ret = cleanup;
	}
	put_refs(&s);
	if (ret)
		pr_err(KBUILD_MODNAME ": refused/failed: %d\n", ret);
	return ret;
}

static void __exit j72_touch_snap_exit(void) {}
module_init(j72_touch_snap_init);
module_exit(j72_touch_snap_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("One-shot J72 touch pin, rail and PWM snapshot; turns ps_pwm0 on and off");
