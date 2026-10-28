// SPDX-License-Identifier: GPL-2.0
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/module.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/pm_domain.h>
#include <linux/pm_runtime.h>
#include <linux/utsname.h>

#define SPI_BASE 0x20a088000ULL
#define SPI_SIZE 0x4000
#define CLKCFG 0x00
#define CFG 0x04
#define STATUS 0x08
#define DIV 0x30
#define TEST_DIV 120
#define PMGR_PATH "/soc/power-management@20e000000"

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

/* A7: ceil(nclk * period_ns / 1e9), using the reported inputs in
 * controller-proof.json. This experiment establishes no clock rate or safety.
 * Readback semantics are unproven; any MMIO access may hang.
 */
static int divider_experiment(void __iomem *base)
{
	u32 clkcfg = readl(base + CLKCFG);
	u32 cfg = readl(base + CFG);
	u32 status = readl(base + STATUS);
	u32 original = readl(base + DIV);
	u32 observed, restored;

	pr_info(KBUILD_MODNAME ": initial CLKCFG=0x%08x CFG=0x%08x STATUS=0x%08x DIV=0x%08x\n",
		clkcfg, cfg, status, original);
	if (clkcfg == ~0U || cfg == ~0U || status == ~0U || original == ~0U)
		return -EIO;
	/* Match the observed initial state; this is not a legal-range claim. */
	if (clkcfg || cfg != 0x8 || status || original != 1)
		return -EBUSY;

	writel(TEST_DIV, base + DIV);
	observed = readl(base + DIV);
	/* Restore after every returning test read, including a mismatch. */
	writel(original, base + DIV);
	restored = readl(base + DIV);
	pr_info(KBUILD_MODNAME ": DIV test=0x%08x restore=0x%08x\n",
		observed, restored);
	return observed == TEST_DIV && restored == original ? 0 : -EIO;
}

static int __init j72_spi_divider_init(void)
{
	struct device_node *np = NULL, *it, *parent = NULL;
	struct of_phandle_args pd = {}, dep = {};
	struct platform_device *pdev = NULL;
	struct resource res, pmgr;
	struct device *dev;
	void __iomem *base;
	int ret = -EINVAL, cleanup = 0, err;

	if (!of_machine_is_compatible("apple,j72") ||
	    strcmp(init_utsname()->release, "7.0.12-j72baseline"))
		return -ENODEV;
	for_each_compatible_node(it, NULL, "apple,j72-spi-status") {
		if (!of_device_is_available(it))
			continue;
		if (np) {
			of_node_put(it);
			goto nodes;
		}
		np = of_node_get(it);
	}
	if (!np || of_get_child_count(np) ||
	    of_property_count_u32_elems(np, "reg") != 4 ||
	    of_address_to_resource(np, 0, &res) ||
	    res.start != SPI_BASE || resource_size(&res) != SPI_SIZE ||
	    of_count_phandle_with_args(np, "power-domains",
				      "#power-domain-cells") != 1)
		goto nodes;
	ret = of_parse_phandle_with_args(np, "power-domains",
				       "#power-domain-cells", 0, &pd);
	if (ret)
		goto nodes;
	ret = -EINVAL;
	if (pd.args_count || !power_node(pd.np,
		PMGR_PATH "/power-controller@200a8", 0x200a8))
		goto nodes;
	parent = of_get_parent(pd.np);
	if (!parent || of_address_to_resource(parent, 0, &pmgr) ||
	    pmgr.start != 0x20e000000ULL || resource_size(&pmgr) != 0x24000 ||
	    of_count_phandle_with_args(pd.np, "power-domains",
				      "#power-domain-cells") != 1)
		goto nodes;
	/* ps_sio_p is the provider's dependency, not its DT parent. */
	ret = of_parse_phandle_with_args(pd.np, "power-domains",
				       "#power-domain-cells", 0, &dep);
	if (ret)
		goto nodes;
	ret = -EINVAL;
	if (dep.args_count || !power_node(dep.np,
		PMGR_PATH "/power-controller@20110", 0x20110))
		goto nodes;
	pdev = of_find_device_by_node(np);
	if (!pdev) {
		ret = -ENODEV;
		goto nodes;
	}
	dev = &pdev->dev;
	/* Serialize binding of the validated, otherwise unbound device. */
	device_lock(dev);
	ret = -EBUSY;
	if (dev->driver || dev->pm_domain || dev->power.disable_depth != 1 ||
	    !pm_runtime_status_suspended(dev) || dev->power.runtime_error ||
	    atomic_read(&dev->power.usage_count) ||
	    atomic_read(&dev->power.child_count))
		goto unlock;
	if (!request_mem_region(SPI_BASE, SPI_SIZE, KBUILD_MODNAME))
		goto unlock;
	base = ioremap(SPI_BASE, SPI_SIZE);
	if (!base) {
		ret = -ENOMEM;
		goto region;
	}
	ret = genpd_dev_pm_attach(dev);
	if (ret != 1) {
		if (!ret)
			ret = -ENODEV;
		if (dev->pm_domain)
			goto detach;
		goto unmap;
	}
	ret = pm_runtime_set_active(dev);
	if (ret)
		goto detach;
	pm_runtime_enable(dev);
	ret = pm_runtime_resume_and_get(dev);
	if (ret < 0)
		goto disable;
	ret = divider_experiment(base);
	pr_info(KBUILD_MODNAME ": operation=%d\n", ret);
	/* The put consumes the reference even when suspension fails. */
	cleanup = pm_runtime_put_sync_suspend(dev);
	if (cleanup > 0)
		cleanup = 0;
disable:
	pm_runtime_disable(dev);
detach:
	/* Restore PM bookkeeping; this does not prove physical power-off. */
	err = pm_runtime_set_suspended(dev);
	if (err && !cleanup)
		cleanup = err;
	dev_pm_domain_detach(dev, true);
	if (dev->pm_domain && !cleanup)
		cleanup = -EBUSY;
	pr_info(KBUILD_MODNAME ": cleanup=%d detached=%d\n",
		cleanup, !dev->pm_domain);
	if (!ret && cleanup)
		ret = cleanup;
unmap:
	iounmap(base);
region:
	release_mem_region(SPI_BASE, SPI_SIZE);
unlock:
	device_unlock(dev);
	put_device(dev);
nodes:
	of_node_put(dep.np);
	of_node_put(parent);
	of_node_put(pd.np);
	of_node_put(np);
	if (ret)
		pr_err(KBUILD_MODNAME ": refused/failed: %d\n", ret);
	return ret;
}

static void __exit j72_spi_divider_exit(void) {}
module_init(j72_spi_divider_init);
module_exit(j72_spi_divider_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("One-shot J72 SPI2 disabled-controller divider experiment; MMIO may hang");
