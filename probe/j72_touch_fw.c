// SPDX-License-Identifier: GPL-2.0-only
/*
 * One-shot J72 touch Stage C2: the Stage C1 power-up and identity read, then
 * apple_n1's boot_stage=2. It uploads the J72 prox calibration, this unit's
 * MtCl calibration and the C1F15,1 firmware, calibrates, executes and reads
 * the report descriptors. It requests no interrupt and registers no input
 * device. Firmware and calibration bytes are never logged.
 */
#define N1_BUFFER 65536
#define J72_TOUCH_ID_LIB
#include "j72_touch_id.c"

#include <linux/firmware.h>
#include <linux/jiffies.h>

#define N1_FIRMWARE "apple/j72-n1.bin"
#define N1_CALIBRATION "apple/j72-n1-cal.bin"
#define N1_FW_SIZE 57016
#define N1_CAL_SIZE 1024
#define N1_CAL_ADDR 0x10009000
#define N1_PROX_SIZE 128
#define N1_PROX_ADDR 0x10009600
#define N1_CHUNK 0x3f0
#define N1_RETRIES 5
#define N1_REPORT_MAX 512
#define N1_FRAME_MAX 4096
#define N1_CONST_CAL2 0x434d11a0

struct touch_fw {
	const struct firmware *fw, *cal;
	u8 *prox;
	unsigned long last_wake;
	bool wake_valid;
	u16 max_packet;
	s16 x_min, y_min, x_max, y_max;
};

static int n1_validate_firmware(const struct firmware *fw)
{
	size_t payload;

	if (fw->size != N1_FW_SIZE || memcmp(fw->data, "\x18\xe1\x30\x01", 4))
		return -EINVAL;
	payload = get_unaligned_be16(fw->data + 4) * 4;
	if (payload + 16 != fw->size || n1_get32(fw->data + 6) ||
	    get_unaligned_be16(fw->data + 10) != (n1_sum(fw->data + 4, 6) & 0xffff) ||
	    n1_get32(fw->data + 12 + payload) != n1_sum(fw->data + 12, payload))
		return -EBADMSG;
	return 0;
}

/* Both files load from the controller device before anything is powered. */
static int load_files(struct touch_fw *f, struct device *dev)
{
	int ret;

	ret = request_firmware_direct(&f->fw, N1_FIRMWARE, dev);
	if (ret)
		return ret;
	ret = n1_validate_firmware(f->fw);
	if (ret)
		return ret;
	ret = request_firmware_direct(&f->cal, N1_CALIBRATION, dev);
	if (ret)
		return ret;
	if (f->cal->size != N1_CAL_SIZE ||
	    !memchr_inv(f->cal->data, 0, N1_CAL_SIZE) ||
	    !memchr_inv(f->cal->data, 0xff, N1_CAL_SIZE))
		return -EINVAL;
	/* iOS 12 sends J72's all-zero 128-byte prox-calibration before MtCl. */
	f->prox = kzalloc(N1_PROX_SIZE, GFP_KERNEL);
	if (!f->prox)
		return -ENOMEM;
	pr_info(KBUILD_MODNAME ": loaded %zu-byte firmware and %zu-byte calibration\n",
		f->fw->size, f->cal->size);
	return 0;
}

static int n1_ack(struct touch_id *id, u16 *reply)
{
	int ret;

	id->tx[0] = 0x1a;
	id->tx[1] = 0xa1;
	ret = n1_xfer(id, "ack", 2);
	if (!ret)
		*reply = get_unaligned_be16(id->rx);
	return ret;
}

static int n1_send_blob(struct touch_id *id, size_t len)
{
	u16 reply = 0;
	int ret = -EIO, attempt;
	u8 *packet;

	/* The ACK reuses tx[0:2], so keep the packet for a bounded retry. */
	packet = kmemdup(id->tx, len, GFP_KERNEL);
	if (!packet)
		return -ENOMEM;
	for (attempt = 0; attempt < N1_RETRIES; attempt++) {
		memcpy(id->tx, packet, len);
		ret = n1_xfer(id, NULL, len);
		if (!ret)
			ret = n1_ack(id, &reply);
		if (!ret && reply == 0x4bc1)
			break;
		if (!ret) {
			pr_err(KBUILD_MODNAME ": upload %zu bytes attempt %d/%u: ACK=%04x expected=4bc1\n",
			       len, attempt + 1, N1_RETRIES, reply);
			ret = -EPROTO;
		}
		usleep_range(1000, 2000);
	}
	kfree(packet);
	return ret;
}

/* N1 shared Z2 calibration download, at most N1_CHUNK bytes per packet. */
static int n1_upload(struct touch_id *id, u32 address, const u8 *data, size_t size)
{
	size_t offset, length, i;
	int ret;

	for (offset = 0; offset < size; offset += length) {
		length = min_t(size_t, size - offset, N1_CHUNK);
		put_unaligned_le16(0xe118, id->tx);
		put_unaligned_le16(0x0130, id->tx + 2);
		put_unaligned_be16(length / 4, id->tx + 4);
		n1_put32(id->tx + 6, address + offset);
		put_unaligned_be16(n1_sum(id->tx + 4, 6), id->tx + 10);
		pr_info(KBUILD_MODNAME ": upload 0x%08zx %zu bytes header=%*ph\n",
			address + offset, length, 12, id->tx);
		for (i = 0; i < length; i += 2) {
			id->tx[12 + i] = data[offset + i + 1];
			id->tx[13 + i] = data[offset + i];
		}
		n1_put32(id->tx + 12 + length, n1_sum(data + offset, length));
		ret = n1_send_blob(id, length + 16);
		if (ret)
			return ret;
	}
	return 0;
}

static int n1_write_register(struct touch_id *id, u32 address, u32 value)
{
	u16 reply = 0;
	int ret;

	put_unaligned_le16(0x331e, id->tx);
	n1_put32(id->tx + 2, address);
	n1_put32(id->tx + 6, 0xffffffff);
	n1_put32(id->tx + 10, value);
	put_unaligned_be16(n1_sum(id->tx + 2, 12), id->tx + 14);
	ret = n1_xfer(id, "register_write", 16);
	if (!ret)
		ret = n1_ack(id, &reply);
	if (!ret && reply != 0x4ad1) {
		pr_err(KBUILD_MODNAME ": register 0x%08x ACK=%04x expected=4ad1\n",
		       address, reply);
		ret = -EPROTO;
	}
	return ret;
}

static int n1_calibrate(struct touch_id *id, u32 version)
{
	const struct { u32 address, value; } registers[] = {
		{ 0x10003060, 6089 },
		{ 0x1000305c, 0x20 },
		{ 0x10003058, 6 },
		{ 0x10003000, version == N1_CONST_CAL2 ? 2 : 3 },
		{ 0x10003518, 1 },
	};
	u32 apu;
	u16 reply;
	int i, ret;

	pr_info(KBUILD_MODNAME ": calibrating controller 0x%08x, const-cal=%u\n",
		version, version == N1_CONST_CAL2 ? 2 : 3);
	for (i = 0; i < ARRAY_SIZE(registers); i++) {
		ret = n1_write_register(id, registers[i].address, registers[i].value);
		if (ret)
			return ret;
	}
	ret = n1_read_register(id, 0x10003800, &apu);
	if (ret)
		return ret;
	pr_info(KBUILD_MODNAME ": SPI_APU_EN=0x%08x\n", apu);
	put_unaligned_le16(0x011f, id->tx);
	ret = n1_xfer(id, "calibration_request", 2);
	if (ret)
		return ret;
	msleep(65);
	/* Apple's N1 path checks transport only; do not invent a success word. */
	ret = n1_ack(id, &reply);
	if (!ret)
		pr_info(KBUILD_MODNAME ": calibration transport complete, status 0x%04x\n",
			reply);
	return ret;
}

static int n1_execute(struct touch_id *id)
{
	int ret;

	put_unaligned_le16(0x531d, id->tx);
	n1_put32(id->tx + 2, 0x10003400);
	put_unaligned_le32(0x100, id->tx + 6);
	put_unaligned_be16(n1_sum(id->tx + 2, 8), id->tx + 10);
	ret = n1_xfer(id, "execute", 12);
	if (!ret) {
		msleep(40);
		/* bootloadTimerFiredHandler waits another 2 ms before discovery. */
		usleep_range(2000, 3000);
	}
	return ret;
}

static int n1_wake(struct touch_fw *f, struct touch_id *id)
{
	int ret;

	/* N1 suppresses duplicate wake packets within 100 ms. */
	if (f->wake_valid && time_before(jiffies, f->last_wake + msecs_to_jiffies(100)))
		return 0;
	memset(id->tx, 0, 16);
	id->tx[0] = 0xee;
	put_unaligned_le16(0xee, id->tx + 14);
	ret = n1_xfer(id, "runtime_wake", 16);
	if (!ret) {
		f->last_wake = jiffies;
		f->wake_valid = true;
		usleep_range(2000, 3000);
	}
	return ret;
}

static int n1_check_control(struct touch_id *id, u8 command, u8 report, size_t len)
{
	u16 received = get_unaligned_le16(id->rx + len - 2);
	u16 calculated = n1_sum(id->rx, len - 2);

	if (id->rx[0] == command && received == calculated)
		return 0;
	pr_err(KBUILD_MODNAME
	       ": control %02x/%02x mismatch: echo=%02x checksum=%04x calculated=%04x\n",
	       command, report, id->rx[0], received, calculated);
	return -EBADMSG;
}

static int n1_command16(struct touch_fw *f, struct touch_id *id, u8 command,
			u8 argument)
{
	int ret;

	ret = n1_wake(f, id);
	if (ret)
		return ret;
	memset(id->tx, 0, 16);
	id->tx[0] = command;
	id->tx[1] = argument;
	put_unaligned_le16(n1_sum(id->tx, 14), id->tx + 14);
	ret = n1_xfer(id, "control_request", 16);
	if (ret)
		return ret;
	/* E2 repeats immediately; E3 has the configured control-read delay. */
	if (command == 0xe3)
		udelay(25);
	ret = n1_xfer(id, "control_response", 16);
	if (ret)
		return ret;
	return n1_check_control(id, command, argument, 16);
}

static int n1_get_report_once(struct touch_fw *f, struct touch_id *id, u8 report,
			      u8 *data, size_t minimum, u16 *out_length)
{
	size_t transfer_length;
	u16 length, checksum;
	u8 command;
	int ret;

	ret = n1_command16(f, id, 0xe3, report);
	if (ret)
		return ret;
	length = get_unaligned_le16(id->rx + 3);
	if (id->rx[2] || length < minimum || length > N1_REPORT_MAX) {
		pr_err(KBUILD_MODNAME ": report %02x info: status=%02x length=%u expected=%zu..%u\n",
		       report, id->rx[2], length, minimum, N1_REPORT_MAX);
		return -EPROTO;
	}
	ret = n1_wake(f, id);
	if (ret)
		return ret;
	command = length < 12 ? 0xe6 : 0xe7;
	transfer_length = length < 12 ? 16 : length + 5;
	memset(id->tx, 0, max_t(size_t, 16, transfer_length));
	id->tx[0] = command;
	id->tx[1] = report;
	put_unaligned_le16(length, id->tx + 3);
	checksum = n1_sum(id->tx, 5);
	put_unaligned_le16(checksum, id->tx + 14);
	ret = n1_xfer(id, "report_request", 16);
	if (ret)
		return ret;
	udelay(25);
	id->tx[2] = 1;
	/* Stage two keeps stage one's checksum, despite changing byte 2. */
	if (command == 0xe7) {
		id->tx[14] = 0;
		id->tx[15] = 0;
		put_unaligned_le16(checksum, id->tx + transfer_length - 2);
	}
	ret = n1_xfer(id, "report_response", transfer_length);
	if (ret)
		return ret;
	ret = n1_check_control(id, command, report, transfer_length);
	if (ret)
		return ret;
	memcpy(data, id->rx + 3, length);
	*out_length = length;
	pr_info(KBUILD_MODNAME ": report %02x length=%u data=%*ph\n", report,
		length, (int)min_t(size_t, length, 32), data);
	return 0;
}

static int n1_get_report(struct touch_fw *f, struct touch_id *id, u8 report,
			 u8 *data, size_t minimum)
{
	u16 length;
	int ret = -EIO, attempt;

	/* iOS delays every GET by 2 ms and retries corrupt replies. */
	for (attempt = 0; attempt < N1_RETRIES; attempt++) {
		usleep_range(2000, 3000);
		ret = n1_get_report_once(f, id, report, data, minimum, &length);
		if (ret != -EBADMSG)
			return ret;
		if (attempt + 1 < N1_RETRIES)
			usleep_range(1000, 2000);
	}
	return ret;
}

/* apple_n1 n1_discover(), with the J71t surface identity logged, not enforced. */
static int n1_discover(struct touch_fw *f, struct touch_id *id)
{
	u8 family, sensor[5], descriptor[N1_REPORT_MAX];
	s16 x_min, y_min, x_max, y_max;
	u32 width, height;
	u16 max_packet;
	int ret = -EIO, attempt;

	for (attempt = 0; attempt < 4; attempt++) {
		ret = n1_command16(f, id, 0xe2, 0);
		if (!ret)
			break;
		usleep_range(3000, 4000);
	}
	if (ret)
		return ret;
	max_packet = get_unaligned_le16(id->rx + 3);
	pr_info(KBUILD_MODNAME ": E2 interface=%u max-packet=%u\n", id->rx[2],
		max_packet);
	if (id->rx[2] != 1 || max_packet < 16 || max_packet > N1_FRAME_MAX)
		return -EPROTO;
	ret = n1_get_report(f, id, 0xd1, descriptor, 1);
	if (ret)
		return ret;
	family = descriptor[0];
	ret = n1_get_report(f, id, 0xd3, descriptor, sizeof(sensor));
	if (ret)
		return ret;
	memcpy(sensor, descriptor, sizeof(sensor));
	if (sensor[0] != 1)
		return -EPROTO;
	ret = n1_get_report(f, id, 0xd0, descriptor, 8);
	if (ret)
		return ret;
	ret = n1_get_report(f, id, 0xa1, descriptor, 6);
	if (ret)
		return ret;
	ret = n1_get_report(f, id, 0xd9, descriptor, 16);
	if (ret)
		return ret;
	width = get_unaligned_le32(descriptor);
	height = get_unaligned_le32(descriptor + 4);
	x_min = (s16)get_unaligned_le16(descriptor + 8);
	y_min = (s16)get_unaligned_le16(descriptor + 10);
	x_max = (s16)get_unaligned_le16(descriptor + 12);
	y_max = (s16)get_unaligned_le16(descriptor + 14);
	pr_info(KBUILD_MODNAME
		": N1 ready: max packet %u, family %u, sensor %ux%u, FW 0x%04x, surface %ux%u%s, x=%d..%d y=%d..%d\n",
		max_packet, family, sensor[1], sensor[2],
		get_unaligned_be16(sensor + 3), width, height,
		width == 14745 && height == 19657 ? " (J71t)" : " (differs from J71t)",
		x_min, x_max, y_min, y_max);
	if (x_min >= x_max || y_min >= y_max)
		return -EPROTO;
	f->max_packet = max_packet;
	f->x_min = x_min;
	f->y_min = y_min;
	f->x_max = x_max;
	f->y_max = y_max;
	return 0;
}

static int boot_firmware(struct touch_fw *f, struct touch_id *id)
{
	u32 version;
	int ret;

	ret = n1_upload(id, N1_PROX_ADDR, f->prox, N1_PROX_SIZE);
	if (ret)
		return ret;
	ret = n1_upload(id, N1_CAL_ADDR, f->cal->data, N1_CAL_SIZE);
	if (ret)
		return ret;
	pr_info(KBUILD_MODNAME ": firmware upload begin\n");
	memcpy(id->tx, f->fw->data, f->fw->size);
	ret = n1_send_blob(id, f->fw->size);
	if (ret)
		return ret;
	pr_info(KBUILD_MODNAME ": uploaded prox, MtCl and %zu-byte C1F15,1 firmware\n",
		f->fw->size);
	/* Apple's calibration stage reads the version after the image upload. */
	ret = n1_read_register(id, N1_VERSION, &version);
	if (ret)
		return ret;
	ret = n1_calibrate(id, version);
	if (ret)
		return ret;
	ret = n1_execute(id);
	if (ret)
		return ret;
	return n1_discover(f, id);
}

static void put_files(struct touch_fw *f)
{
	kfree(f->prox);
	release_firmware(f->cal);
	release_firmware(f->fw);
}

#ifndef J72_TOUCH_FW_LIB
static int __init j72_touch_fw_init(void)
{
	struct touch_power tp = {};
	struct touch_id id = {};
	struct touch_fw f = {};
	bool started, complete = false;
	int ret, cleanup, err, check;

	ret = find_controller(&id);
	if (!ret)
		ret = load_files(&f, &id.ctlr->dev);
	if (ret) {
		put_files(&f);
		put_id(&id);
		pr_err(KBUILD_MODNAME ": refused before power-up: %d\n", ret);
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
	if (!ret)
		ret = boot_firmware(&f, &id);
	if (!ret) {
		complete = true;
		pr_info(KBUILD_MODNAME ": stage C2 complete; descriptors read, no input device\n");
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
			": final version=0x%08x complete=%d pmgr_after=0x%08x gpio15=0x%08x gpio79=0x%08x gpio119=0x%08x cleanup=%d\n",
			id.version, complete, tp.pmgr_after,
			tp.pin_after[PIN_RESET], tp.pin_after[PIN_ENABLE_CS],
			tp.pin_after[PIN_SYNC], cleanup);
	}
	if (!ret)
		ret = cleanup;
	release_gpios(&tp);
	put_refs(&tp);
	put_files(&f);
	put_id(&id);
	if (ret)
		pr_err(KBUILD_MODNAME ": refused/failed: %d\n", ret);
	return ret;
}

static void __exit j72_touch_fw_exit(void) {}
module_init(j72_touch_fw_init);
module_exit(j72_touch_fw_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("One-shot J72 touch Stage C2 N1 firmware boot and descriptor read");
MODULE_FIRMWARE(N1_FIRMWARE);
MODULE_FIRMWARE(N1_CALIBRATION);
#endif
