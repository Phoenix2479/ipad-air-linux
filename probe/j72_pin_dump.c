// SPDX-License-Identifier: GPL-2.0-only
/*
 * Read-only J72 pin snapshot: logs all 200 pin config words of the A7 GPIO
 * block, decoded, to find the pad that carries PWM channel 2 (grape-clk).
 * It reads through a temporary mapping under the pinctrl device lock and
 * writes nothing. It always fails its init after logging, so the kernel
 * frees it and it can run again on the same boot.
 */
#include <linux/bitfield.h>
#include <linux/device.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/utsname.h>

#define PINS_PATH "/soc/pinctrl@20e300000"
#define PINS_BASE 0x20e300000ULL
#define PINS_SIZE 0x100000
#define PINS_MAP 0x1000
#define NPINS 200

#define REG_GPIOx_DATA BIT(0)
#define REG_GPIOx_MODE GENMASK(3, 1)
#define REG_GPIOx_PERIPH GENMASK(6, 5)
#define REG_GPIOx_PULL GENMASK(8, 7)
#define REG_GPIOx_INPUT_ENABLE BIT(9)
#define REG_GPIOx_GRP GENMASK(18, 16)
#define REG_GPIOx_LOCK BIT(21)

static u32 words[NPINS];

static int read_words(struct device *dev)
{
	void __iomem *base = NULL;
	int i, ret = -ENODEV;

	device_lock(dev);
	if (dev->driver) {
		base = ioremap(PINS_BASE, PINS_MAP);
		ret = base ? 0 : -ENOMEM;
	}
	if (base) {
		for (i = 0; i < NPINS; i++)
			words[i] = readl(base + 4 * i);
		iounmap(base);
	}
	device_unlock(dev);
	return ret;
}

static int __init j72_pin_dump_init(void)
{
	struct platform_device *pdev = NULL;
	struct device_node *np;
	struct resource res;
	unsigned int periph = 0;
	int i, ret = -ENODEV;

	if (!of_machine_is_compatible("apple,j72") ||
	    strcmp(init_utsname()->release, "7.0.12-j72baseline"))
		goto out;
	np = of_find_node_by_path(PINS_PATH);
	if (np && of_device_is_compatible(np, "apple,s5l8960x-pinctrl") &&
	    !of_address_to_resource(np, 0, &res) && res.start == PINS_BASE &&
	    resource_size(&res) == PINS_SIZE)
		pdev = of_find_device_by_node(np);
	of_node_put(np);
	if (!pdev)
		goto out;
	ret = read_words(&pdev->dev);
	put_device(&pdev->dev);
	if (ret)
		goto out;
	for (i = 0; i < NPINS; i++) {
		u32 w = words[i];

		if (FIELD_GET(REG_GPIOx_PERIPH, w))
			periph++;
		pr_info(KBUILD_MODNAME
			": pin %3d raw=0x%08x mode=%lu data=%lu periph=%lu pull=%lu ie=%lu grp=%lu lock=%lu%s\n",
			i, w, FIELD_GET(REG_GPIOx_MODE, w),
			FIELD_GET(REG_GPIOx_DATA, w),
			FIELD_GET(REG_GPIOx_PERIPH, w),
			FIELD_GET(REG_GPIOx_PULL, w),
			FIELD_GET(REG_GPIOx_INPUT_ENABLE, w),
			FIELD_GET(REG_GPIOx_GRP, w),
			FIELD_GET(REG_GPIOx_LOCK, w),
			FIELD_GET(REG_GPIOx_PERIPH, w) ? " PERIPH" : "");
	}
	pr_info(KBUILD_MODNAME ": %d pins read, %u in a peripheral function; unloading by design\n",
		NPINS, periph);
	return -ECANCELED;
out:
	pr_err(KBUILD_MODNAME ": refused: %d\n", ret);
	return ret;
}

static void __exit j72_pin_dump_exit(void) {}
module_init(j72_pin_dump_init);
module_exit(j72_pin_dump_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Read-only J72 pin config snapshot; always unloads itself");
