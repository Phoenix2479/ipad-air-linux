// SPDX-License-Identifier: GPL-2.0
/*
 * Stage B of the J72 touch power sequence: power the rails, enable the PWM
 * clock and start the 32 kHz clock. Reset GPIO 15 stays asserted and no SPI
 * transfer is made. The module refuses unless the measured Stage B0 state is
 * still present, and it returns to the rails-off state on the way out.
 */

#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/gpio/consumer.h>
#include <linux/gpio/driver.h>
#include <linux/gpio/machine.h>
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
#define PINS_LABEL "20e300000.pinctrl"
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

/* PWM channel 2 on this block: high at 0x10, low at 0x14, control at 0x20. */
#define PWM_HIGH 0x10
#define PWM_LOW 0x14
#define PWM_CTRL 0x20
#define PWM_CTRL_FIRST 6
#define PWM_CYCLES 366
/*
 * Control bits match apple,s5l-fpwm (pwm-apple.c) except that UPDATE is bit 1
 * on this block: 0 enable, 1 update, 2 mode, 9 trigger, 10 invert and
 * 14 output enable. iOS writes enable|update|output-enable (0x4003) and never
 * reads the word back. The update strobe clears itself, so the steady state is
 * 0x4001. Bit 0x1 must stay set and bit 0x4000 must stay set; no other bit may
 * appear. Stop writes output-enable alone (0x4000), which is also what the
 * device read back.
 */
#define PWM_CTRL_ENABLE 0x0001
#define PWM_CTRL_UPDATE 0x0002
#define PWM_CTRL_OUTPUT_ENABLE 0x4000
#define PWM_START (PWM_CTRL_ENABLE | PWM_CTRL_UPDATE | PWM_CTRL_OUTPUT_ENABLE)
#define PWM_RUNNING (PWM_CTRL_ENABLE | PWM_CTRL_OUTPUT_ENABLE)
#define PWM_STOP PWM_CTRL_OUTPUT_ENABLE
/* A channel is running if either of the two run bits is already set. */
#define PWM_RUN_MASK (PWM_CTRL_ENABLE | PWM_CTRL_UPDATE)

/* Field layout from drivers/pinctrl/pinctrl-apple-gpio.c. */
#define REG_GPIO(x) (4 * (x))
#define REG_GPIOx_DATA BIT(0)
#define REG_GPIOx_MODE GENMASK(3, 1)
#define REG_GPIOx_PERIPH GENMASK(6, 5)
#define REG_GPIOx_INPUT_ENABLE BIT(9)
#define REG_GPIOx_LOCK BIT(21)
#define REG_GPIOx_OUT 1

struct touch_pin {
	const char *name;
	unsigned int reg;
	u32 boot, rerun;
};

/*
 * A finished run leaves 79 low instead of iBoot's high. Once the Stage D
 * interrupt has run, freeing it leaves 111 in group 0 with "input, IRQ off"
 * (mode 7) instead of iBoot's mode 0. All of these are safe.
 */
static const struct touch_pin pins[] = {
	{ "reset", 15, 0x00072602, 0x00072602 },
	{ "enable_cs", 79, 0x00072203, 0x00072202 },
	{ "atn", 111, 0x00070381, 0x0000038f },
	{ "sync", 119, 0x00072602, 0x00072602 },
};

enum {
	PIN_RESET,
	PIN_ENABLE_CS,
	PIN_ATN,
	PIN_SYNC,
	PIN_COUNT
};

struct touch_reg {
	const char *name;
	unsigned int reg;
	unsigned int boot;
};

static const struct touch_reg pmic_regs[] = {
	{ "ldo14_en", 0x0084, 0x00 },
	{ "ldo18_en", 0x0085, 0x11 },
	{ "ldo14_vset", 0x0398, 0x14 },
};

enum {
	HELD_REGION = BIT(0),
	HELD_PDEV = BIT(2),
	HELD_GENPD = BIT(3),
	HELD_RPM = BIT(4),
	HELD_REF = BIT(5),
	HELD_PWM = BIT(6),
	HELD_PWM_CFG = BIT(7),
	HELD_LDO14 = BIT(8),
	HELD_LDO18 = BIT(9),
};

struct touch_power {
	struct device_node *pwm0, *pmgr, *pins_np;
	struct platform_device *pins_dev, *pdev;
	struct device *pmic;
	struct regmap *pmgr_map;
	struct gpio_device *gdev;
	struct gpio_desc *gpio[PIN_COUNT];
	void __iomem *pwm;
	bool pmgr_powered;
	unsigned int ngpio;
	unsigned int held;
	u32 pin_before[PIN_COUNT], pin_after[PIN_COUNT];
	unsigned int pmic_before[ARRAY_SIZE(pmic_regs)];
	unsigned int pmgr_before, pmgr_on, pmgr_after;
	u32 pwm_before[PWM_WORDS];
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

static int read_pin(struct touch_power *tp, unsigned int pin, u32 *out)
{
	struct device *dev = &tp->pins_dev->dev;
	void __iomem *base = NULL;
	int ret = -ENODEV;

	device_lock(dev);
	if (dev->driver) {
		base = ioremap(PINS_BASE, PINS_MAP);
		ret = base ? 0 : -ENOMEM;
	}
	if (base) {
		*out = readl(base + REG_GPIO(pin));
		iounmap(base);
	}
	device_unlock(dev);
	return ret;
}

static int read_pins(struct touch_power *tp)
{
	struct device *dev = &tp->pins_dev->dev;
	void __iomem *base = NULL;
	int i, ret = -ENODEV;

	device_lock(dev);
	if (dev->driver) {
		base = ioremap(PINS_BASE, PINS_MAP);
		ret = base ? 0 : -ENOMEM;
	}
	if (base) {
		for (i = 0; i < PIN_COUNT; i++)
			tp->pin_before[i] = readl(base + REG_GPIO(pins[i].reg));
		iounmap(base);
	}
	device_unlock(dev);
	return ret;
}

static int read_pmic(struct touch_power *tp)
{
	struct regmap *map;
	int i, ret = -ENODEV;

	device_lock(tp->pmic);
	map = tp->pmic->driver ? dev_get_regmap(tp->pmic, NULL) : NULL;
	if (map && regmap_get_val_bytes(map) == 1) {
		ret = 0;
		for (i = 0; i < ARRAY_SIZE(pmic_regs); i++) {
			ret = regmap_read(map, pmic_regs[i].reg,
					  &tp->pmic_before[i]);
			if (ret)
				break;
		}
	}
	device_unlock(tp->pmic);
	return ret;
}

static void log_pin(unsigned int index, u32 reg)
{
	pr_info(KBUILD_MODNAME
		": gpio%u %s raw=0x%08x mode=%lu data=%lu periph=%lu input_enable=%lu lock=%lu\n",
		pins[index].reg, pins[index].name, reg,
		FIELD_GET(REG_GPIOx_MODE, reg),
		FIELD_GET(REG_GPIOx_DATA, reg),
		FIELD_GET(REG_GPIOx_PERIPH, reg),
		FIELD_GET(REG_GPIOx_INPUT_ENABLE, reg),
		FIELD_GET(REG_GPIOx_LOCK, reg));
}

static int check_boot_state(struct touch_power *tp)
{
	const struct touch_reg *r;
	int i;

	for (i = 0; i < PIN_COUNT; i++) {
		log_pin(i, tp->pin_before[i]);
		if (tp->pin_before[i] != pins[i].boot &&
		    tp->pin_before[i] != pins[i].rerun)
			return -EINVAL;
	}
	for (i = 0; i < ARRAY_SIZE(pmic_regs); i++) {
		r = &pmic_regs[i];
		pr_info(KBUILD_MODNAME ": %s[0x%04x]=0x%02x\n", r->name,
			r->reg, tp->pmic_before[i]);
		if (tp->pmic_before[i] != r->boot)
			return -EINVAL;
	}
	if (FIELD_GET(PS_ACTUAL, tp->pmgr_before))
		return -EINVAL;
	return 0;
}

static int request_gpios(struct touch_power *tp)
{
	struct gpio_chip *chip;
	struct gpio_desc *desc;
	int i, ret;

	tp->gdev = gpio_device_find_by_label(PINS_LABEL);
	if (!tp->gdev)
		return -ENODEV;
	chip = gpio_device_get_chip(tp->gdev);
	if (!chip)
		return -ENODEV;
	for (i = 0; i < PIN_COUNT; i++) {
		desc = gpiochip_request_own_desc(chip, pins[i].reg,
						 KBUILD_MODNAME, 0, GPIOD_ASIS);
		if (IS_ERR(desc)) {
			ret = PTR_ERR(desc);
			return ret;
		}
		tp->gpio[i] = desc;
		tp->ngpio++;
	}
	return 0;
}

static int preflight(struct touch_power *tp)
{
	struct of_phandle_args dep = {};
	struct i2c_client *client;
	struct device_node *np;
	struct resource res;
	int ret;
	bool valid;

	if (!of_machine_is_compatible("apple,j72") ||
	    strcmp(init_utsname()->release, "7.0.12-j72baseline"))
		return -ENODEV;
	tp->pwm0 = of_find_node_by_path(PWM0_PATH);
	if (!power_node(tp->pwm0, PWM0_PATH, PMGR_PWM0) ||
	    of_count_phandle_with_args(tp->pwm0, "power-domains",
				      "#power-domain-cells") != 1 ||
	    of_parse_phandle_with_args(tp->pwm0, "power-domains",
				       "#power-domain-cells", 0, &dep))
		return -EINVAL;
	valid = !dep.args_count && power_node(dep.np, SIO_P_PATH, 0x20110);
	of_node_put(dep.np);
	if (!valid)
		return -EINVAL;
	tp->pmgr = of_get_parent(tp->pwm0);
	if (!tp->pmgr || !of_device_is_compatible(tp->pmgr, "syscon") ||
	    of_address_to_resource(tp->pmgr, 0, &res) ||
	    res.start != PMGR_BASE || resource_size(&res) != PMGR_SIZE)
		return -EINVAL;
	tp->pmgr_map = syscon_node_to_regmap(tp->pmgr);
	if (IS_ERR(tp->pmgr_map))
		return PTR_ERR(tp->pmgr_map);
	if (regmap_get_val_bytes(tp->pmgr_map) != 4)
		return -EINVAL;
	tp->pins_np = of_find_node_by_path(PINS_PATH);
	if (!tp->pins_np || !of_device_is_available(tp->pins_np) ||
	    !of_device_is_compatible(tp->pins_np, "apple,s5l8960x-pinctrl") ||
	    of_address_to_resource(tp->pins_np, 0, &res) ||
	    res.start != PINS_BASE || resource_size(&res) != PINS_SIZE)
		return -EINVAL;
	tp->pins_dev = of_find_device_by_node(tp->pins_np);
	if (!tp->pins_dev)
		return -ENODEV;
	np = find_pmic();
	if (IS_ERR(np))
		return PTR_ERR(np);
	client = of_find_i2c_device_by_node(np);
	of_node_put(np);
	if (!client)
		return -ENODEV;
	tp->pmic = &client->dev;
	ret = read_pins(tp);
	if (ret)
		return ret;
	ret = regmap_read(tp->pmgr_map, PMGR_PWM0, &tp->pmgr_before);
	if (ret)
		return ret;
	ret = read_pmic(tp);
	if (ret)
		return ret;
	ret = check_boot_state(tp);
	if (ret)
		return ret;
	if (!request_mem_region(PWM_BASE, PWM_SIZE, KBUILD_MODNAME))
		return -EBUSY;
	tp->held |= HELD_REGION;
	return request_gpios(tp);
}

static int verify_pin(struct touch_power *tp, unsigned int index,
		      unsigned int data)
{
	u32 reg;
	int ret;

	ret = read_pin(tp, pins[index].reg, &reg);
	if (ret)
		return ret;
	tp->pin_after[index] = reg;
	log_pin(index, reg);
	if (FIELD_GET(REG_GPIOx_MODE, reg) != REG_GPIOx_OUT ||
	    FIELD_GET(REG_GPIOx_DATA, reg) != data ||
	    FIELD_GET(REG_GPIOx_PERIPH, reg) != 0 ||
	    FIELD_GET(REG_GPIOx_LOCK, reg) != 0)
		return -EIO;
	return 0;
}

static int drive_pin(struct touch_power *tp, unsigned int index,
		     unsigned int value)
{
	int ret;

	ret = gpiod_direction_output_raw(tp->gpio[index], value);
	if (ret)
		return ret;
	return verify_pin(tp, index, value);
}

static int drive_pins_low(struct touch_power *tp)
{
	int ret;

	ret = drive_pin(tp, PIN_RESET, 0);
	if (ret)
		return ret;
	ret = drive_pin(tp, PIN_ENABLE_CS, 0);
	if (ret)
		return ret;
	return drive_pin(tp, PIN_SYNC, 0);
}

static int sample_atn(struct touch_power *tp, const char *phase)
{
	int value = gpiod_get_raw_value(tp->gpio[PIN_ATN]);

	if (value < 0)
		return value;
	pr_info(KBUILD_MODNAME ": atn phase=%s value=%d\n", phase, value);
	return 0;
}

static int poll_atn(struct touch_power *tp, const char *phase,
		    unsigned int count)
{
	unsigned int i;
	int ret;

	for (i = 0; i < count; i++) {
		ret = sample_atn(tp, phase);
		if (ret)
			return ret;
		usleep_range(1000, 1500);
	}
	return 0;
}

static int pwm_on(struct touch_power *tp)
{
	struct of_phandle_args pd = { .np = tp->pwm0 };
	int i, ret;

	tp->pdev = platform_device_register_simple(KBUILD_MODNAME,
						   PLATFORM_DEVID_NONE, NULL, 0);
	if (IS_ERR(tp->pdev))
		return PTR_ERR(tp->pdev);
	tp->held |= HELD_PDEV;
	ret = of_genpd_add_device(&pd, &tp->pdev->dev);
	if (ret)
		return ret;
	tp->held |= HELD_GENPD;
	pm_runtime_enable(&tp->pdev->dev);
	tp->held |= HELD_RPM;
	ret = pm_runtime_resume_and_get(&tp->pdev->dev);
	if (ret)
		return ret;
	tp->held |= HELD_REF;
	tp->pmgr_powered = true;
	ret = regmap_read(tp->pmgr_map, PMGR_PWM0, &tp->pmgr_on);
	if (ret)
		return ret;
	if (FIELD_GET(PS_ACTUAL, tp->pmgr_on) != PS_ACTIVE)
		return -EIO;
	tp->pwm = ioremap(PWM_BASE, PWM_SIZE);
	if (!tp->pwm)
		return -ENOMEM;
	tp->held |= HELD_PWM;
	for (i = 0; i < PWM_WORDS; i++)
		tp->pwm_before[i] = readl(tp->pwm + 4 * i);
	pr_info(KBUILD_MODNAME
		": pmgr_on=0x%08x pwm[0x00..0x20]=%08x %08x %08x %08x %08x %08x %08x %08x %08x\n",
		tp->pmgr_on, tp->pwm_before[0], tp->pwm_before[1],
		tp->pwm_before[2], tp->pwm_before[3], tp->pwm_before[4],
		tp->pwm_before[5], tp->pwm_before[6], tp->pwm_before[7],
		tp->pwm_before[8]);
	for (i = PWM_CTRL_FIRST; i < PWM_WORDS; i++)
		if (tp->pwm_before[i] & PWM_RUN_MASK)
			return -EBUSY;
	return 0;
}

static int pmic_update(struct touch_power *tp, unsigned int reg,
		       unsigned int mask, unsigned int value,
		       unsigned int expect)
{
	struct regmap *map;
	unsigned int val = 0;
	int ret = -ENODEV;

	device_lock(tp->pmic);
	map = tp->pmic->driver ? dev_get_regmap(tp->pmic, NULL) : NULL;
	if (map && regmap_get_val_bytes(map) == 1) {
		ret = regmap_update_bits(map, reg, mask, value);
		if (!ret) {
			ret = regmap_read(map, reg, &val);
			if (!ret && val != expect)
				ret = -EIO;
		}
	}
	device_unlock(tp->pmic);
	if (!ret)
		pr_info(KBUILD_MODNAME
			": pmic[0x%04x] mask=0x%02x value=0x%02x read=0x%02x\n",
			reg, mask, value, val);
	return ret;
}

static int rails_on(struct touch_power *tp)
{
	int ret;

	tp->held |= HELD_LDO14;
	ret = pmic_update(tp, 0x84, 0x04, 0x04, 0x04);
	if (ret)
		return ret;
	tp->held |= HELD_LDO18;
	return pmic_update(tp, 0x85, 0x02, 0x02, 0x13);
}

static int pwm_start(struct touch_power *tp)
{
	u32 high, low, ctrl, late_high, late_low, late_ctrl, pmgr;
	int ret;

	tp->held |= HELD_PWM_CFG;
	writel(PWM_CYCLES, tp->pwm + PWM_HIGH);
	writel(PWM_CYCLES, tp->pwm + PWM_LOW);
	writel(PWM_START, tp->pwm + PWM_CTRL);

	high = readl(tp->pwm + PWM_HIGH);
	low = readl(tp->pwm + PWM_LOW);
	ctrl = readl(tp->pwm + PWM_CTRL);
	pr_info(KBUILD_MODNAME
		": pwm readback high=0x%08x low=0x%08x ctrl=0x%08x want=0x%08x/0x%08x/0x%08x\n",
		high, low, ctrl, PWM_CYCLES, PWM_CYCLES, PWM_RUNNING);

	usleep_range(5000, 6000);
	late_high = readl(tp->pwm + PWM_HIGH);
	late_low = readl(tp->pwm + PWM_LOW);
	late_ctrl = readl(tp->pwm + PWM_CTRL);
	ret = regmap_read(tp->pmgr_map, PMGR_PWM0, &pmgr);
	if (ret)
		return ret;
	pr_info(KBUILD_MODNAME
		": pwm late high=0x%08x low=0x%08x ctrl=0x%08x pmgr=0x%08x\n",
		late_high, late_low, late_ctrl, pmgr);

	if (high != PWM_CYCLES || low != PWM_CYCLES ||
	    (ctrl & ~PWM_CTRL_UPDATE) != PWM_RUNNING ||
	    late_high != PWM_CYCLES || late_low != PWM_CYCLES ||
	    (late_ctrl & ~PWM_CTRL_UPDATE) != PWM_RUNNING)
		return -EIO;
	pr_info(KBUILD_MODNAME
		": pwm high=%u low=%u ctrl=0x%04x; reset still asserted\n",
		PWM_CYCLES, PWM_CYCLES, ctrl);
	return 0;
}

static int stop_pwm(struct touch_power *tp)
{
	u32 ctrl;

	if (!(tp->held & HELD_PWM_CFG))
		return 0;
	writel(PWM_STOP, tp->pwm + PWM_CTRL);
	ctrl = readl(tp->pwm + PWM_CTRL);
	pr_info(KBUILD_MODNAME
		": pwm stop readback ctrl=0x%08x want=0x%08x\n",
		ctrl, PWM_STOP);
	if (ctrl != PWM_STOP)
		return -EIO;
	return 0;
}

static int release_pwm(struct touch_power *tp)
{
	struct device *dev = tp->held & HELD_PDEV ? &tp->pdev->dev : NULL;
	int ret = 0, err;

	if (tp->held & HELD_PWM) {
		iounmap(tp->pwm);
		tp->pwm = NULL;
		tp->held &= ~HELD_PWM;
	}
	/* The put drops the reference even when suspending fails. */
	if (tp->held & HELD_REF) {
		err = pm_runtime_put_sync_suspend(dev);
		if (err < 0 && !ret)
			ret = err;
		tp->held &= ~HELD_REF;
	}
	if (tp->held & HELD_RPM) {
		pm_runtime_disable(dev);
		tp->held &= ~HELD_RPM;
	}
	if (tp->held & HELD_GENPD) {
		err = pm_genpd_remove_device(dev);
		if (err && !ret)
			ret = err;
		if (!err)
			tp->held &= ~HELD_GENPD;
	}
	/* Freeing a device that genpd still lists would leave it dangling. */
	if ((tp->held & (HELD_PDEV | HELD_GENPD)) == HELD_PDEV) {
		platform_device_unregister(tp->pdev);
		tp->held &= ~HELD_PDEV;
	}
	if (tp->held & HELD_REGION) {
		release_mem_region(PWM_BASE, PWM_SIZE);
		tp->held &= ~HELD_REGION;
	}
	if (tp->pmgr_powered) {
		err = regmap_read(tp->pmgr_map, PMGR_PWM0, &tp->pmgr_after);
		if (err && !ret)
			ret = err;
		if (!err && FIELD_GET(PS_ACTUAL, tp->pmgr_after) && !ret)
			ret = -EIO;
	}
	return ret;
}

static int rails_off(struct touch_power *tp)
{
	int ret = 0, err;

	/* ldo18 first, then ldo14, matching the reverse of power-up. */
	if (tp->held & HELD_LDO18) {
		err = pmic_update(tp, 0x85, 0x02, 0x00, 0x11);
		if (!err)
			tp->held &= ~HELD_LDO18;
		if (err && !ret)
			ret = err;
	}
	if (tp->held & HELD_LDO14) {
		err = pmic_update(tp, 0x84, 0x04, 0x00, 0x00);
		if (!err)
			tp->held &= ~HELD_LDO14;
		if (err && !ret)
			ret = err;
	}
	return ret;
}

static int power_down(struct touch_power *tp)
{
	int ret = 0, err;

	if (tp->gpio[PIN_RESET]) {
		err = drive_pin(tp, PIN_RESET, 0);
		if (err && !ret)
			ret = err;
	}
	err = stop_pwm(tp);
	if (err && !ret)
		ret = err;
	err = release_pwm(tp);
	if (err && !ret)
		ret = err;
	/* 79 goes low before the rails, and it stays low after the free. */
	if (tp->gpio[PIN_ENABLE_CS]) {
		err = drive_pin(tp, PIN_ENABLE_CS, 0);
		if (err && !ret)
			ret = err;
	}
	err = rails_off(tp);
	if (err && !ret)
		ret = err;
	return ret;
}

static void release_gpios(struct touch_power *tp)
{
	int i;

	for (i = tp->ngpio - 1; i >= 0; i--)
		gpiochip_free_own_desc(tp->gpio[i]);
	tp->ngpio = 0;
	if (tp->gdev) {
		gpio_device_put(tp->gdev);
		tp->gdev = NULL;
	}
}

static void put_refs(struct touch_power *tp)
{
	put_device(tp->pmic);
	if (tp->pins_dev)
		put_device(&tp->pins_dev->dev);
	of_node_put(tp->pins_np);
	of_node_put(tp->pmgr);
	of_node_put(tp->pwm0);
}

#ifndef J72_TOUCH_POWER_LIB
static int __init j72_touch_power_init(void)
{
	struct touch_power tp = {};
	bool complete = false;
	int ret, cleanup, err, check;

	ret = preflight(&tp);
	if (!ret)
		ret = drive_pins_low(&tp);
	if (!ret) {
		usleep_range(5000, 6000);
		ret = sample_atn(&tp, "pins_low");
	}
	if (!ret)
		ret = pwm_on(&tp);
	if (!ret)
		ret = rails_on(&tp);
	if (!ret)
		ret = poll_atn(&tp, "rails_on", 5);
	if (!ret)
		ret = drive_pin(&tp, PIN_ENABLE_CS, 1);
	if (!ret)
		ret = pwm_start(&tp);
	if (!ret)
		ret = poll_atn(&tp, "pwm_on", 15);
	if (!ret) {
		ret = sample_atn(&tp, "hold_begin");
		msleep(100);
		if (!ret)
			ret = sample_atn(&tp, "hold_end");
	}
	if (!ret) {
		complete = true;
		pr_info(KBUILD_MODNAME ": stage B complete; no transfer made\n");
	}
	cleanup = power_down(&tp);
	if (complete) {
		err = sample_atn(&tp, "after_off");
		msleep(200);
		check = verify_pin(&tp, PIN_RESET, 0);
		if (check && !err)
			err = check;
		check = verify_pin(&tp, PIN_ENABLE_CS, 0);
		if (check && !err)
			err = check;
		check = verify_pin(&tp, PIN_SYNC, 0);
		if (check && !err)
			err = check;
		check = sample_atn(&tp, "final");
		if (check && !err)
			err = check;
		if (err && !cleanup)
			cleanup = err;
		pr_info(KBUILD_MODNAME
			": final pmgr_before=0x%08x pmgr_on=0x%08x pmgr_after=0x%08x gpio15=0x%08x gpio79=0x%08x gpio119=0x%08x cleanup=%d\n",
			tp.pmgr_before, tp.pmgr_on, tp.pmgr_after,
			tp.pin_after[PIN_RESET], tp.pin_after[PIN_ENABLE_CS],
			tp.pin_after[PIN_SYNC], cleanup);
	}
	if (!ret)
		ret = cleanup;
	release_gpios(&tp);
	put_refs(&tp);
	if (ret)
		pr_err(KBUILD_MODNAME ": refused/failed: %d\n", ret);
	return ret;
}

static void __exit j72_touch_power_exit(void) {}
module_init(j72_touch_power_init);
module_exit(j72_touch_power_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("One-shot J72 touch Stage B power and clock sequence; reset stays asserted");
#endif
