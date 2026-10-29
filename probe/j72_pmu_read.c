// SPDX-License-Identifier: GPL-2.0
#include <linux/device.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/regmap.h>
#include <linux/utsname.h>

#define PMIC_ADDR 0x3c

struct pmu_reg {
	unsigned int reg;
	const char *label;
};

static const struct pmu_reg pmu_regs[] = {
	{ 0x0084, "ldo14_en" },
	{ 0x0085, "ldo18_en" },
	{ 0x0398, "ldo14_vset" },
};

static int read_registers(struct regmap *map, unsigned int *values)
{
	int i, ret;

	for (i = 0; i < ARRAY_SIZE(pmu_regs); i++) {
		ret = regmap_read(map, pmu_regs[i].reg, &values[i]);
		if (ret)
			return ret;
	}
	return 0;
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

static int __init j72_pmu_read_init(void)
{
	unsigned int values[ARRAY_SIZE(pmu_regs)];
	struct i2c_client *client;
	struct device_node *np;
	struct regmap *map;
	struct device *dev;
	int ret;

	if (!of_machine_is_compatible("apple,j72") ||
	    strcmp(init_utsname()->release, "7.0.12-j72baseline"))
		return -ENODEV;
	np = find_pmic();
	if (IS_ERR(np)) {
		ret = PTR_ERR(np);
		goto out;
	}
	client = of_find_i2c_device_by_node(np);
	of_node_put(np);
	if (!client) {
		ret = -ENODEV;
		goto out;
	}
	dev = &client->dev;
	/* Keep the PMIC driver bound while its regmap is in use. */
	device_lock(dev);
	map = dev->driver ? dev_get_regmap(dev, NULL) : NULL;
	if (!map || regmap_get_val_bytes(map) != 1) {
		ret = -ENODEV;
		goto unlock;
	}
	ret = read_registers(map, values);
	if (ret)
		goto unlock;
	pr_info(KBUILD_MODNAME ": %s[0x%04x]=0x%02x %s[0x%04x]=0x%02x %s[0x%04x]=0x%02x\n",
		pmu_regs[0].label, pmu_regs[0].reg, values[0],
		pmu_regs[1].label, pmu_regs[1].reg, values[1],
		pmu_regs[2].label, pmu_regs[2].reg, values[2]);
	pr_info(KBUILD_MODNAME ": ldo14_en=%d shared=0x%x ldo18_en=%d ldo14_field=0x%02x ldo14_mV_inferred=%u\n",
		!!(values[0] & 0x04), values[0] & 0x03, !!(values[1] & 0x02),
		values[2] & 0x1f, 5000 + 50 * (values[2] & 0x1f));
unlock:
	device_unlock(dev);
	put_device(dev);
out:
	if (ret)
		pr_err(KBUILD_MODNAME ": refused/failed: %d\n", ret);
	return ret;
}

static void __exit j72_pmu_read_exit(void) {}
module_init(j72_pmu_read_init);
module_exit(j72_pmu_read_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("One-shot read-only J72 D2089 touch rail register snapshot");
