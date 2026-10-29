// SPDX-License-Identifier: GPL-2.0-only
/*
 * One-shot J72 touch Stage C1: the Stage B power and clock sequence, then the
 * N1 bootloader identity exchange from apple_n1's boot_stage=1. It sends the
 * wake word and HBPP NOP, releases reset, checks the HBPP reply and reads the
 * controller version. It loads no firmware and registers no driver.
 */
#define J72_TOUCH_POWER_LIB
#include "j72_touch_power.c"

#include <linux/slab.h>
#include <linux/spi/spi.h>
#include <linux/unaligned.h>

#define SPI_PATH "/soc/spi-status@20a088000"
#define SPI_DRIVER "j72-spi-ctrl"
/* iOS programs an 83 ns period: divider 5 from the reported 60 MHz input. */
#define N1_SPEED_HZ 12000000
#define N1_PACKET 16
#ifndef N1_BUFFER
#define N1_BUFFER N1_PACKET
#endif
#define N1_VERSION 0x10008ffc

struct touch_id {
	struct device_node *spi_np;
	struct spi_controller *ctlr;
	struct spi_device *spi;
	bool cs_active_low;
	u8 *tx, *rx;
	u32 version;
};

static u32 n1_sum(const u8 *data, size_t len)
{
	u32 sum = 0;

	while (len--)
		sum += *data++;
	return sum;
}

/* Apple bootloader's 32-bit encoding: high byte of low halfword first. */
static void n1_put32(u8 *p, u32 value)
{
	p[0] = value >> 8;
	p[1] = value;
	p[2] = value >> 24;
	p[3] = value >> 16;
}

static u32 n1_get32(const u8 *p)
{
	return (u32)p[2] << 24 | (u32)p[3] << 16 | (u32)p[0] << 8 | p[1];
}

static int n1_xfer(struct touch_id *id, const char *phase, size_t len)
{
	struct spi_transfer transfer = {
		.tx_buf = id->tx,
		.rx_buf = id->rx,
		.len = len,
		.bits_per_word = 8,
	};
	int ret;

	if (!len || len > N1_BUFFER)
		return -EMSGSIZE;
	memset(id->rx, 0, len);
	ret = spi_sync_transfer(id->spi, &transfer, 1);
	/* A NULL phase carries firmware or calibration: never log its bytes. */
	if (phase)
		pr_info(KBUILD_MODNAME ": %s tx=%*ph rx=%*ph ret=%d hz=%u\n", phase,
			(int)min_t(size_t, len, 32), id->tx,
			(int)min_t(size_t, len, 32), id->rx, ret,
			transfer.effective_speed_hz);
	else if (ret)
		pr_err(KBUILD_MODNAME ": %zu-byte upload transfer failed: %d\n",
		       len, ret);
	return ret;
}

static int find_controller(struct touch_id *id)
{
	struct device *parent;

	id->spi_np = of_find_node_by_path(SPI_PATH);
	if (!id->spi_np || !of_device_is_compatible(id->spi_np,
						    "apple,j72-spi-status"))
		return -ENODEV;
	id->ctlr = of_find_spi_controller_by_node(id->spi_np);
	if (!id->ctlr)
		return -ENODEV;
	parent = id->ctlr->dev.parent;
	/* A cs-gpios table would overwrite the descriptor this module sets. */
	if (!parent || !parent->driver || strcmp(parent->driver->name, SPI_DRIVER) ||
	    id->ctlr->num_chipselect != 1 || id->ctlr->cs_gpiods ||
	    !id->ctlr->use_gpio_descriptors)
		return -EINVAL;
	id->tx = kzalloc(N1_BUFFER, GFP_KERNEL);
	id->rx = kzalloc(N1_BUFFER, GFP_KERNEL);
	if (!id->tx || !id->rx)
		return -ENOMEM;
	return 0;
}

/*
 * Pin 79 is SPI2 chip select 0 (active-low) as well as enable_cs. As the
 * device's descriptor chip select, the SPI core drives it low around each
 * message and never calls the controller's internal-CS hook.
 */
static int add_device(struct touch_power *tp, struct touch_id *id)
{
	struct spi_device *spi;
	int ret;

	spi = spi_alloc_device(id->ctlr);
	if (!spi)
		return -ENOMEM;
	spi->max_speed_hz = N1_SPEED_HZ;
	spi->mode = SPI_MODE_0;
	spi->bits_per_word = 8;
	spi_set_chipselect(spi, 0, 0);
	/* spi_new_device() sets this; without it spi_set_cs() skips pin 79. */
	spi->cs_index_mask = BIT(0);
	strscpy(spi->modalias, "j72-touch-id", sizeof(spi->modalias));
	gpiod_toggle_active_low(tp->gpio[PIN_ENABLE_CS]);
	id->cs_active_low = true;
	spi_set_csgpiod(spi, 0, tp->gpio[PIN_ENABLE_CS]);
	ret = spi_add_device(spi);
	if (ret) {
		spi_dev_put(spi);
		return ret;
	}
	id->spi = spi;
	return verify_pin(tp, PIN_ENABLE_CS, 1);
}

static int n1_boot_nop(struct touch_id *id, const char *phase)
{
	int i;

	put_unaligned_le16(0xa11a, id->tx);
	for (i = 2; i < N1_PACKET; i += 2)
		put_unaligned_le16(0xe118, id->tx + i);
	return n1_xfer(id, phase, N1_PACKET);
}

static bool n1_hbpp_word(u16 word)
{
	switch (word) {
	case 0x18e1: case 0x1aa1: case 0x1f01: case 0x4879:
	case 0x4969: case 0x4ad1: case 0x4bc1:
		return true;
	default:
		return false;
	}
}

static int n1_read_register(struct touch_id *id, u32 address, u32 *value)
{
	static const u8 result_command[] = {
		0x1a, 0xa1, 0x18, 0xe1, 0x18, 0xe1, 0x18, 0xe1,
	};
	int ret;

	put_unaligned_le16(0x731c, id->tx);
	n1_put32(id->tx + 2, address);
	put_unaligned_be16(n1_sum(id->tx + 2, 4), id->tx + 6);
	ret = n1_xfer(id, "register_request", 8);
	if (ret)
		return ret;
	memcpy(id->tx, result_command, sizeof(result_command));
	ret = n1_xfer(id, "register_result", sizeof(result_command));
	if (!ret)
		*value = n1_get32(id->rx + 2);
	return ret;
}

/* apple_n1 n1_power_on() after the clock, then n1_check_hbpp() and the version. */
static int identify(struct touch_power *tp, struct touch_id *id)
{
	int ret;

	put_unaligned_le16(0xc119, id->tx);
	ret = n1_xfer(id, "wake", 2);
	if (ret)
		return ret;
	usleep_range(10000, 12000);
#ifdef J72_TOUCH_SYNC_HOOK
	/* iOS selects display sync here, between the wake word and the NOP. */
	ret = J72_TOUCH_SYNC_HOOK(tp);
	if (ret)
		return ret;
#endif
	ret = n1_boot_nop(id, "boot_nop");
	if (ret)
		return ret;
	ret = drive_pin(tp, PIN_RESET, 1);
	if (ret)
		return ret;
	usleep_range(15000, 17000);
	ret = sample_atn(tp, "reset_released");
	if (ret)
		return ret;
	ret = n1_boot_nop(id, "hbpp");
	if (ret)
		return ret;
	if (!n1_hbpp_word(get_unaligned_be16(id->rx)) ||
	    !n1_hbpp_word(get_unaligned_be16(id->rx + 2))) {
		pr_err(KBUILD_MODNAME ": HBPP not detected\n");
		return -ENODEV;
	}
	ret = n1_read_register(id, N1_VERSION, &id->version);
	if (ret)
		return ret;
	pr_info(KBUILD_MODNAME ": HBPP detected; N1 controller 0x%08x\n",
		id->version);
	if (!id->version || id->version == 0xffffffff)
		return -ENODEV;
	return sample_atn(tp, "identified");
}

/* Reset goes back first; the device must be gone before 79 is driven low. */
static int stop_device(struct touch_power *tp, struct touch_id *id)
{
	int ret = 0;

	if (tp->gpio[PIN_RESET])
		ret = drive_pin(tp, PIN_RESET, 0);
	if (id->spi) {
		spi_unregister_device(id->spi);
		id->spi = NULL;
	}
	if (id->cs_active_low) {
		gpiod_toggle_active_low(tp->gpio[PIN_ENABLE_CS]);
		id->cs_active_low = false;
	}
	return ret;
}

static void put_id(struct touch_id *id)
{
	kfree(id->rx);
	kfree(id->tx);
	if (id->ctlr)
		spi_controller_put(id->ctlr);
	of_node_put(id->spi_np);
}

#ifndef J72_TOUCH_ID_LIB
static int __init j72_touch_id_init(void)
{
	struct touch_power tp = {};
	struct touch_id id = {};
	bool started, complete = false;
	int ret, cleanup, err, check;

	ret = find_controller(&id);
	if (ret) {
		put_id(&id);
		pr_err(KBUILD_MODNAME ": refused: no idle " SPI_DRIVER " controller: %d\n", ret);
		return ret;
	}
	ret = preflight(&tp);
	started = !ret;
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
	if (!ret)
		ret = add_device(&tp, &id);
	if (!ret)
		ret = identify(&tp, &id);
	if (!ret) {
		complete = true;
		pr_info(KBUILD_MODNAME ": stage C1 complete; no firmware loaded\n");
	}
	cleanup = stop_device(&tp, &id);
	err = power_down(&tp);
	if (err && !cleanup)
		cleanup = err;
	if (started) {
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
			": final version=0x%08x complete=%d pmgr_before=0x%08x pmgr_on=0x%08x pmgr_after=0x%08x gpio15=0x%08x gpio79=0x%08x gpio119=0x%08x cleanup=%d\n",
			id.version, complete, tp.pmgr_before, tp.pmgr_on,
			tp.pmgr_after, tp.pin_after[PIN_RESET],
			tp.pin_after[PIN_ENABLE_CS], tp.pin_after[PIN_SYNC],
			cleanup);
	}
	if (!ret)
		ret = cleanup;
	release_gpios(&tp);
	put_refs(&tp);
	put_id(&id);
	if (ret)
		pr_err(KBUILD_MODNAME ": refused/failed: %d\n", ret);
	return ret;
}

static void __exit j72_touch_id_exit(void) {}
module_init(j72_touch_id_init);
module_exit(j72_touch_id_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("One-shot J72 touch Stage C1 N1 bootloader identity read");
#endif
