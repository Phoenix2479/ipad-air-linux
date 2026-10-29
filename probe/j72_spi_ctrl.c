// SPDX-License-Identifier: GPL-2.0-only
/* J72 SPI2 Samsung SPI v1 controller, bounded byte-wide PIO. */
#include <linux/bitfield.h>
#include <linux/io.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_runtime.h>
#include <linux/sizes.h>
#include <linux/spi/spi.h>
#include <linux/utsname.h>

#define J72_SPI_BASE		0x20a088000ULL
#define J72_SPI_SIZE		0x4000
/* Bootloader-reported nclk (clock id 308); the rate is not measured. */
#define J72_SPI_NCLK_HZ		60000000UL

#define S8000_CTRL		0x00
#define S8000_CTRL_ENABLE	0x0d
#define S8000_CFG		0x04
#define S8000_CFG_BASE		0x4018
#define S8000_CFG_CPHA		BIT(1)
#define S8000_CFG_CPOL		BIT(2)
#define S8000_CFG_PIO		BIT(5)
#define S8000_STATUS		0x08
#define S8000_STATUS_CLEAR	0x40000f
#define S8000_TX_LEVEL		GENMASK(10, 6)
#define S8000_RX_LEVEL		GENMASK(15, 11)
#define S8000_PIN		0x0c
#define S8000_PIN_CS		BIT(1)
#define S8000_TXDATA		0x10
#define S8000_RXDATA		0x20
#define S8000_CLKDIV		0x30
#define S8000_RXCNT		0x34
#define S8000_WORD_DELAY	0x38
#define S8000_TXCNT		0x4c
#define S8000_FIFO_DEPTH	16
#define S8000_DIV_MAX		2047
#define S8000_XFER_TIMEOUT_US	1000000

struct s8000_spi {
	void __iomem *base;
};

/* RUN, DMA/IRQ modes and counts off; no SmartIO or DMA descriptor is used. */
static int s8000_spi_idle(struct s8000_spi *spi, struct device *dev)
{
	u32 ctrl, cfg, status;

	ctrl = readl(spi->base + S8000_CTRL);
	cfg = readl(spi->base + S8000_CFG);
	status = readl(spi->base + S8000_STATUS);
	dev_info(dev, "SPI inherited ctrl=%#x cfg=%#x status=%#x\n",
		 ctrl, cfg, status);
	writel(0, spi->base + S8000_CTRL);
	writel(S8000_CFG_BASE, spi->base + S8000_CFG);
	writel(0, spi->base + S8000_RXCNT);
	writel(0, spi->base + S8000_TXCNT);
	writel(S8000_PIN_CS, spi->base + S8000_PIN);
	writel(S8000_STATUS_CLEAR, spi->base + S8000_STATUS);
	/* Complete posted writes before publishing children or unbinding. */
	ctrl = readl(spi->base + S8000_CTRL);
	if (ctrl) {
		dev_err(dev, "SPI failed to stop: ctrl=%#x\n", ctrl);
		return -EIO;
	}
	return 0;
}

/* Runs after the controller is unregistered and before runtime PM is disabled. */
static void s8000_spi_release(void *data)
{
	struct device *dev = data;
	struct spi_controller *ctlr = dev_get_drvdata(dev);

	s8000_spi_idle(spi_controller_get_devdata(ctlr), dev);
	pm_runtime_put_sync(dev);
}

static void s8000_spi_set_cs(struct spi_device *device, bool high)
{
	struct s8000_spi *spi = spi_controller_get_devdata(device->controller);

	/* Descriptor-backed chip selects belong to the SPI core. */
	if (!spi_get_csgpiod(device, 0))
		writel(high ? S8000_PIN_CS : 0, spi->base + S8000_PIN);
}

static size_t s8000_spi_max_transfer_size(struct spi_device *device)
{
	/* Bound both FIFO work and worst-case wall time for malformed clients. */
	return SZ_64K;
}

static int s8000_spi_transfer_one(struct spi_controller *ctlr,
				  struct spi_device *device,
				  struct spi_transfer *transfer)
{
	struct s8000_spi *spi = spi_controller_get_devdata(ctlr);
	const u8 *tx = transfer->tx_buf;
	u8 *rx = transfer->rx_buf;
	unsigned long rate = J72_SPI_NCLK_HZ;
	unsigned int sent = 0, received = 0, iterations = 0;
	u32 speed = transfer->speed_hz ?: device->max_speed_hz;
	u32 divisor, cfg, status, tx_level, rx_level;
	u64 word_delay_cycles;
	ktime_t deadline;
	int word_delay_ns, ret = 0;

	if (!transfer->len)
		return 0;
	if (transfer->bits_per_word != 8 || !speed || transfer->len > SZ_64K)
		return -EINVAL;
	divisor = DIV_ROUND_UP(rate, speed);
	if (!divisor || divisor > S8000_DIV_MAX)
		return -EINVAL;
	transfer->effective_speed_hz = rate / divisor;
	/* The SPI core merges the device minimum into transfer->word_delay. */
	word_delay_ns = spi_delay_to_ns(&transfer->word_delay, transfer);
	if (word_delay_ns < 0)
		return word_delay_ns;
	/* WORD_DELAY counts input-clock cycles, not divided SCK cycles. */
	word_delay_cycles = DIV_ROUND_UP_ULL((u64)rate * word_delay_ns,
					     NSEC_PER_SEC);
	if (word_delay_cycles > U32_MAX)
		return -EINVAL;
	cfg = S8000_CFG_BASE;
	if (device->mode & SPI_CPHA)
		cfg |= S8000_CFG_CPHA;
	if (device->mode & SPI_CPOL)
		cfg |= S8000_CFG_CPOL;

	/* spi-version 1 has FIFO counts in STATUS, unlike the M1 controller. */
	writel(0, spi->base + S8000_CTRL);
	writel(divisor, spi->base + S8000_CLKDIV);
	writel(word_delay_cycles, spi->base + S8000_WORD_DELAY);
	writel(S8000_STATUS_CLEAR, spi->base + S8000_STATUS);
	writel(cfg, spi->base + S8000_CFG);
	writel(S8000_CTRL_ENABLE, spi->base + S8000_CTRL);
	writel(transfer->len, spi->base + S8000_RXCNT);
	writel(transfer->len, spi->base + S8000_TXCNT);
	writel(cfg | S8000_CFG_PIO, spi->base + S8000_CFG);
	deadline = ktime_add_us(ktime_get(), S8000_XFER_TIMEOUT_US);

	while (received < transfer->len) {
		status = readl(spi->base + S8000_STATUS);
		tx_level = FIELD_GET(S8000_TX_LEVEL, status);
		rx_level = FIELD_GET(S8000_RX_LEVEL, status);
		if (tx_level > S8000_FIFO_DEPTH || rx_level > S8000_FIFO_DEPTH ||
		    rx_level > transfer->len - received ||
		    rx_level > sent - received) {
			dev_err(&device->dev,
				"invalid PIO FIFO: tx=%u rx=%u status=%#x\n",
				sent, received, status);
			ret = -EIO;
			break;
		}
		writel(status, spi->base + S8000_STATUS);
		while (rx_level--) {
			u8 value = readl(spi->base + S8000_RXDATA);

			if (rx)
				rx[received] = value;
			received++;
		}
		/* Never queue more than one RX FIFO's worth of unconsumed data. */
		while (sent < transfer->len && tx_level < S8000_FIFO_DEPTH &&
		       sent - received < S8000_FIFO_DEPTH) {
			writel(tx ? tx[sent] : 0xff, spi->base + S8000_TXDATA);
			sent++;
			tx_level++;
		}
		if (ktime_compare(ktime_get(), deadline) >= 0) {
			dev_err(&device->dev,
				"PIO timeout: tx %u/%u rx %u/%u status=%#x\n",
				sent, transfer->len, received, transfer->len, status);
			ret = -ETIMEDOUT;
			break;
		}
		if (!(++iterations & 0xff))
			cond_resched();
		cpu_relax();
	}
	writel(cfg, spi->base + S8000_CFG);
	writel(0, spi->base + S8000_CTRL);
	writel(S8000_STATUS_CLEAR, spi->base + S8000_STATUS);
	readl(spi->base + S8000_CTRL);
	return ret;
}

static int s8000_spi_probe(struct platform_device *pdev)
{
	struct spi_controller *ctlr;
	struct s8000_spi *spi;
	struct resource *res;
	int ret;

	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!of_machine_is_compatible("apple,j72") ||
	    strcmp(init_utsname()->release, "7.0.12-j72baseline") ||
	    !res || res->start != J72_SPI_BASE ||
	    resource_size(res) != J72_SPI_SIZE) {
		dev_err(&pdev->dev, "refused: not J72 SPI2 on 7.0.12-j72baseline\n");
		return -ENODEV;
	}
	ctlr = devm_spi_alloc_host(&pdev->dev, sizeof(*spi));
	if (!ctlr)
		return -ENOMEM;
	spi = spi_controller_get_devdata(ctlr);
	spi->base = devm_ioremap_resource(&pdev->dev, res);
	if (IS_ERR(spi->base))
		return PTR_ERR(spi->base);
	platform_set_drvdata(pdev, ctlr);
	ret = devm_pm_runtime_enable(&pdev->dev);
	if (ret)
		return ret;
	ret = pm_runtime_resume_and_get(&pdev->dev);
	if (ret < 0)
		return ret;
	/* The reference keeps the genpd domain on for as long as the driver is bound. */
	ret = devm_add_action_or_reset(&pdev->dev, s8000_spi_release, &pdev->dev);
	if (ret)
		return ret;

	ctlr->bus_num = -1;
	ctlr->num_chipselect = 1;
	ctlr->mode_bits = SPI_CPHA | SPI_CPOL;
	ctlr->bits_per_word_mask = SPI_BPW_MASK(8);
	ctlr->min_speed_hz = DIV_ROUND_UP(J72_SPI_NCLK_HZ, S8000_DIV_MAX);
	ctlr->max_speed_hz = J72_SPI_NCLK_HZ;
	ctlr->set_cs = s8000_spi_set_cs;
	ctlr->transfer_one = s8000_spi_transfer_one;
	ctlr->max_transfer_size = s8000_spi_max_transfer_size;
	ctlr->use_gpio_descriptors = true;
	ret = s8000_spi_idle(spi, &pdev->dev);
	if (ret)
		return ret;
	/* No controller IRQ enable bits are set: every PIO transfer is bounded. */
	ret = devm_spi_register_controller(&pdev->dev, ctlr);
	if (ret)
		return ret;
	dev_info(&pdev->dev, "Samsung SPI v1 PIO idle, input %lu Hz (reported), bus %d\n",
		 J72_SPI_NCLK_HZ, ctlr->bus_num);
	return 0;
}

static void s8000_spi_shutdown(struct platform_device *pdev)
{
	struct spi_controller *ctlr = platform_get_drvdata(pdev);
	int ret;

	ret = spi_controller_suspend(ctlr);
	if (!ret)
		ret = s8000_spi_idle(spi_controller_get_devdata(ctlr), &pdev->dev);
	if (ret)
		dev_err(&pdev->dev, "SPI shutdown failed: %d\n", ret);
}

static const struct of_device_id s8000_spi_match[] = {
	{ .compatible = "apple,j72-spi-status" },
	{ }
};

static struct platform_driver s8000_spi_driver = {
	.probe = s8000_spi_probe,
	.shutdown = s8000_spi_shutdown,
	.driver = {
		.name = "j72-spi-ctrl",
		.of_match_table = s8000_spi_match,
	},
};
module_platform_driver(s8000_spi_driver);
MODULE_DESCRIPTION("J72 SPI2 Samsung SPI v1 PIO controller");
MODULE_LICENSE("GPL");
