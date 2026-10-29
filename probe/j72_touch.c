// SPDX-License-Identifier: GPL-2.0-only
/*
 * Resident J72 touch driver, Stage D: the Stage C2 firmware boot, then the
 * apple_n1 runtime. A threaded interrupt on ATN reads frames, parses classic
 * contact reports and feeds a multitouch input device. It is level-low, not
 * apple_n1's falling edge, so an edge that arrives while the line is masked
 * cannot leave ATN low with no interrupt pending.
 *
 * Writing to /sys/kernel/debug/<module>/stop tears everything down to the
 * safe rerun state, so a corrected build can load on the same boot.
 */
#define J72_TOUCH_FW_LIB
#include "j72_touch_fw.c"

#include <linux/bitmap.h>
#include <linux/debugfs.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include <linux/interrupt.h>
#include <linux/seq_file.h>
#include <linux/workqueue.h>

#define N1_SLOTS 32
#define N1_TRACE_FRAMES 16

struct touch {
	struct touch_power tp;
	struct touch_id id;
	struct touch_fw f;
	struct input_dev *input;
	struct dentry *debugfs;
	/* Serializes the IRQ thread, recovery and stop. */
	struct mutex lock;
	/* Makes a second write to stop a no-op. */
	struct mutex stop_lock;
	struct delayed_work recovery;
	int irq;
	u8 sequence;
	bool started, running, stopped, irq_requested, irq_masked;
	unsigned int recovery_attempts, irq_errors, traced;
	unsigned long irqs, frames, empty_frames, contact_frames, errors;
};

static struct touch touch;

static int atn_asserted(struct touch *t)
{
	int value = gpiod_get_raw_value(t->tp.gpio[PIN_ATN]);

	return value < 0 ? value : !value;
}

static void trace_contacts(struct touch *t, const u8 *report, size_t offset,
			   size_t stride, size_t count)
{
	const u8 *finger;
	size_t i;

	if (t->traced >= N1_TRACE_FRAMES)
		return;
	t->traced++;
	for (i = 0; i < count; i++) {
		finger = report + offset + i * stride;
		pr_info(KBUILD_MODNAME ": frame %u contact id=%u state=%u x=%d y=%d\n",
			t->traced, finger[0], finger[1],
			(s16)get_unaligned_le16(finger + 4),
			(s16)get_unaligned_le16(finger + 6));
	}
	if (!count)
		pr_info(KBUILD_MODNAME ": frame %u type=0x%02x no contacts\n",
			t->traced, report[0]);
}

static int parse_contacts(struct touch *t, const u8 *report, size_t length)
{
	DECLARE_BITMAP(ids, 256);
	size_t offset, stride, count, i;
	const u8 *finger;
	int slot, x, y;
	bool active;

	if (!length)
		return -EPROTO;
	/* Classic reports dispatched by MTClassicPacketParser; no HID wrapper. */
	if (report[0] != 0x43 && report[0] != 0x44 && report[0] != 0x45)
		return 0;
	if (length < 22)
		return -EPROTO;
	stride = report[17];
	count = report[16];
	offset = report[2] + get_unaligned_le16(report + 14);
	if (report[2] < 22 || offset > length || count > N1_SLOTS ||
	    (count && (stride < 20 || stride > 64 || count > (length - offset) / stride)))
		return -EPROTO;
	bitmap_zero(ids, 256);
	/* Validate the entire report before changing input slots. */
	for (i = 0; i < count; i++) {
		finger = report + offset + i * stride;
		if (test_and_set_bit(finger[0], ids))
			return -EPROTO;
	}
	trace_contacts(t, report, offset, stride, count);
	t->contact_frames++;
	for (i = 0; i < count; i++) {
		finger = report + offset + i * stride;
		slot = input_mt_get_slot_by_key(t->input, finger[0]);
		if (slot < 0)
			continue;
		active = finger[1] == 3 || finger[1] == 4;
		input_mt_slot(t->input, slot);
		if (!input_mt_report_slot_state(t->input, MT_TOOL_FINGER, active))
			continue;
		x = (s16)get_unaligned_le16(finger + 4);
		y = (s16)get_unaligned_le16(finger + 6);
		input_report_abs(t->input, ABS_MT_POSITION_X,
				 clamp_t(int, x, t->f.x_min, t->f.x_max) - t->f.x_min);
		input_report_abs(t->input, ABS_MT_POSITION_Y,
				 clamp_t(int, y, t->f.y_min, t->f.y_max) - t->f.y_min);
		input_report_abs(t->input, ABS_MT_TOUCH_MAJOR,
				 get_unaligned_le16(finger + 12));
		input_report_abs(t->input, ABS_MT_TOUCH_MINOR,
				 get_unaligned_le16(finger + 14));
		input_report_abs(t->input, ABS_MT_ORIENTATION,
				 (s16)get_unaligned_le16(finger + 16));
		input_report_abs(t->input, ABS_MT_PRESSURE,
				 stride >= 28 ? get_unaligned_le16(finger + 26) : 0);
	}
	input_mt_sync_frame(t->input);
	input_sync(t->input);
	return 0;
}

/* Returns 1 for a consumed frame, 0 for an empty controller, or an errno. */
static int read_frame(struct touch *t, bool report_events)
{
	struct touch_id *id = &t->id;
	u16 length, payload_length;
	size_t wire_length;
	int ret;

	memset(id->tx, 0, 16);
	id->tx[0] = 0xeb;
	id->tx[1] = t->sequence;
	put_unaligned_le16(n1_sum(id->tx, 14), id->tx + 14);
	ret = n1_xfer(id, NULL, 16);
	if (ret)
		return ret;
	if ((id->rx[0] & 0xf0) != 0xe0 ||
	    get_unaligned_le16(id->rx + 14) != (u16)n1_sum(id->rx, 14))
		return -EBADMSG;
	length = get_unaligned_le16(id->rx + 1);
	if (!length) {
		t->empty_frames++;
		return 0;
	}
	wire_length = length + 5;
	if (length < 2 || wire_length > N1_FRAME_MAX)
		return -EMSGSIZE;
	memset(id->tx, 0, max_t(size_t, 16, wire_length));
	id->tx[0] = 0xeb;
	id->tx[1] = t->sequence;
	id->tx[2] = 1;
	put_unaligned_le16(n1_sum(id->tx, 14), id->tx + wire_length - 2);
	ret = n1_xfer(id, NULL, wire_length);
	if (ret)
		return ret;
	if ((id->rx[0] != 0xea && id->rx[0] != 0xeb) || (n1_sum(id->rx, 5) & 0xff))
		return -EBADMSG;
	payload_length = get_unaligned_le16(id->rx + 2);
	if (payload_length < 2 || payload_length > length ||
	    get_unaligned_le16(id->rx + wire_length - 2) !=
	    (u16)n1_sum(id->rx + 5, payload_length - 2))
		return -EBADMSG;
	t->sequence = t->sequence == 1 ? 2 : 1;
	t->frames++;
	if (report_events && payload_length > 2) {
		ret = parse_contacts(t, id->rx + 5, payload_length - 2);
		if (ret)
			return ret;
	}
	return 1;
}

static void release_contacts(struct touch *t)
{
	int i;

	if (!t->input)
		return;
	for (i = 0; i < N1_SLOTS; i++) {
		input_mt_slot(t->input, i);
		input_mt_report_slot_state(t->input, MT_TOOL_FINGER, false);
	}
	input_mt_sync_frame(t->input);
	input_sync(t->input);
}

static int irq_frame(struct touch *t)
{
	int attempt, ret, asserted;

	/* A failed read may leave ATN asserted with no new falling edge. */
	for (attempt = 0; attempt < 3; attempt++) {
		ret = read_frame(t, true);
		if (ret > 0)
			return ret;
		if (!ret) {
			asserted = atn_asserted(t);
			if (!asserted)
				return 0;
			ret = asserted < 0 ? asserted : -EPROTO;
		}
		usleep_range(1000, 2000);
	}
	return ret;
}

static int drain(struct touch *t, bool report_events)
{
	int i, ret = 0;

	for (i = 0; i < 8; i++) {
		ret = read_frame(t, report_events);
		if (ret <= 0)
			break;
	}
	if (ret < 0 || i == 8)
		return ret < 0 ? ret : -EOVERFLOW;
	ret = atn_asserted(t);
	return ret > 0 ? -EBUSY : ret;
}

static void mask_irq(struct touch *t)
{
	if (!t->irq_masked) {
		disable_irq_nosync(t->irq);
		t->irq_masked = true;
	}
}

/* Called with the lock held and the IRQ masked. Never resets or reloads. */
static int rearm(struct touch *t)
{
	int ret;

	t->f.wake_valid = false;
	ret = n1_wake(&t->f, &t->id);
	if (!ret)
		ret = drain(t, true);
	if (ret) {
		pr_warn(KBUILD_MODNAME ": runtime drain failed %d; trying discovery once\n", ret);
		release_contacts(t);
		t->sequence = 1;
		ret = n1_discover(&t->f, &t->id);
		if (!ret)
			ret = drain(t, true);
	}
	if (ret)
		return ret;
	t->irq_errors = 0;
	if (t->irq_masked) {
		t->irq_masked = false;
		enable_irq(t->irq);
	}
	pr_info(KBUILD_MODNAME ": runtime wake/drain complete; IRQ rearmed\n");
	return 0;
}

static void queue_recovery(struct touch *t)
{
	if (t->recovery_attempts < 3)
		schedule_delayed_work(&t->recovery, msecs_to_jiffies(1000));
	else
		pr_err(KBUILD_MODNAME ": recovery budget exhausted; write stop and reload\n");
}

static void recovery_work(struct work_struct *work)
{
	struct touch *t = container_of(to_delayed_work(work), struct touch, recovery);
	int ret;

	mutex_lock(&t->lock);
	if (t->running && t->recovery_attempts < 3) {
		t->recovery_attempts++;
		ret = rearm(t);
		pr_info(KBUILD_MODNAME ": recovery %u/3 result %d\n",
			t->recovery_attempts, ret);
		if (ret)
			queue_recovery(t);
	}
	mutex_unlock(&t->lock);
}

static irqreturn_t touch_irq(int irq, void *data)
{
	struct touch *t = data;
	int ret = 0, i, asserted;

	mutex_lock(&t->lock);
	if (!t->running || t->irq_masked)
		goto out;
	t->irqs++;
	/* A stuck ATN cannot monopolize the IRQ thread. */
	for (i = 0; i < 8; i++) {
		ret = irq_frame(t);
		if (ret <= 0)
			break;
		asserted = atn_asserted(t);
		if (asserted < 0) {
			ret = asserted;
			break;
		}
		if (!asserted)
			break;
	}
	if (ret < 0 || i == 8) {
		release_contacts(t);
		t->errors++;
		t->irq_errors++;
		pr_err_ratelimited(KBUILD_MODNAME ": frame/ATN error %d (count %u); IRQ masked\n",
				   ret < 0 ? ret : -EOVERFLOW, t->irq_errors);
		mask_irq(t);
		queue_recovery(t);
	} else {
		t->irq_errors = 0;
	}
out:
	mutex_unlock(&t->lock);
	return IRQ_HANDLED;
}

static int start_input(struct touch *t)
{
	struct input_dev *input;
	int ret;

	input = input_allocate_device();
	if (!input)
		return -ENOMEM;
	input->name = "Apple N1 Touchscreen";
	input->phys = "spi0.0/input0";
	input->id.bustype = BUS_SPI;
	input->dev.parent = &t->id.spi->dev;
	input_set_capability(input, EV_KEY, BTN_TOUCH);
	input_set_abs_params(input, ABS_MT_POSITION_X, 0, t->f.x_max - t->f.x_min, 0, 0);
	input_set_abs_params(input, ABS_MT_POSITION_Y, 0, t->f.y_max - t->f.y_min, 0, 0);
	input_set_abs_params(input, ABS_MT_TOUCH_MAJOR, 0, 65535, 0, 0);
	input_set_abs_params(input, ABS_MT_TOUCH_MINOR, 0, 65535, 0, 0);
	input_set_abs_params(input, ABS_MT_ORIENTATION, -32768, 32767, 0, 0);
	input_set_abs_params(input, ABS_MT_PRESSURE, 0, 65535, 0, 0);
	/* Classic N1 coordinates and ellipse axes are hundredths of a millimetre. */
	input_abs_set_res(input, ABS_MT_POSITION_X, 100);
	input_abs_set_res(input, ABS_MT_POSITION_Y, 100);
	input_abs_set_res(input, ABS_MT_TOUCH_MAJOR, 100);
	input_abs_set_res(input, ABS_MT_TOUCH_MINOR, 100);
	ret = input_mt_init_slots(input, N1_SLOTS, INPUT_MT_DIRECT | INPUT_MT_DROP_UNUSED);
	if (!ret)
		ret = input_register_device(input);
	if (ret) {
		input_free_device(input);
		return ret;
	}
	t->input = input;

	/* Drain before requesting the IRQ, so a failure leaves pin 111 untouched. */
	t->sequence = 1;
	ret = drain(t, false);
	if (ret) {
		pr_err(KBUILD_MODNAME ": initial drain failed: %d\n", ret);
		return ret;
	}
	t->irq = gpiod_to_irq(t->tp.gpio[PIN_ATN]);
	if (t->irq < 0)
		return t->irq;
	ret = request_threaded_irq(t->irq, NULL, touch_irq,
				   IRQF_ONESHOT | IRQF_NO_AUTOEN | IRQF_TRIGGER_LOW,
				   KBUILD_MODNAME, t);
	if (ret)
		return ret;
	t->irq_requested = true;
	mutex_lock(&t->lock);
	t->running = true;
	t->irq_masked = false;
	mutex_unlock(&t->lock);
	enable_irq(t->irq);
	pr_info(KBUILD_MODNAME ": touchscreen reporting enabled on irq %d, %dx%d\n",
		t->irq, t->f.x_max - t->f.x_min, t->f.y_max - t->f.y_min);
	return 0;
}

/* After free_irq, 111 must be an input in one of the preflight's two states. */
static int check_atn_mode(struct touch *t)
{
	u32 reg;
	int ret;

	ret = read_pin(&t->tp, pins[PIN_ATN].reg, &reg);
	if (ret)
		return ret;
	log_pin(PIN_ATN, reg);
	return reg == pins[PIN_ATN].boot || reg == pins[PIN_ATN].rerun ? 0 : -EIO;
}

/* Every resource is checked before release, so this runs once for any state. */
static int touch_shutdown(struct touch *t)
{
	int cleanup, err, check;

	mutex_lock(&t->lock);
	t->running = false;
	mutex_unlock(&t->lock);
	cancel_delayed_work_sync(&t->recovery);
	if (t->irq_requested) {
		free_irq(t->irq, t);
		t->irq_requested = false;
	}
	if (t->input) {
		release_contacts(t);
		input_unregister_device(t->input);
		t->input = NULL;
	}
	cleanup = stop_device(&t->tp, &t->id);
	err = power_down(&t->tp);
	if (err && !cleanup)
		cleanup = err;
	if (t->started) {
		err = sample_atn(&t->tp, "after_off");
		msleep(200);
		check = verify_pin(&t->tp, PIN_RESET, 0);
		if (check && !err)
			err = check;
		check = verify_pin(&t->tp, PIN_ENABLE_CS, 0);
		if (check && !err)
			err = check;
		check = verify_pin(&t->tp, PIN_SYNC, 0);
		if (check && !err)
			err = check;
		check = sample_atn(&t->tp, "final");
		if (check && !err)
			err = check;
		check = check_atn_mode(t);
		if (check && !err)
			err = check;
		if (err && !cleanup)
			cleanup = err;
		pr_info(KBUILD_MODNAME
			": stopped irqs=%lu frames=%lu contact_frames=%lu errors=%lu gpio15=0x%08x gpio79=0x%08x gpio119=0x%08x cleanup=%d\n",
			t->irqs, t->frames, t->contact_frames, t->errors,
			t->tp.pin_after[PIN_RESET], t->tp.pin_after[PIN_ENABLE_CS],
			t->tp.pin_after[PIN_SYNC], cleanup);
		t->started = false;
	}
	release_gpios(&t->tp);
	put_refs(&t->tp);
	put_files(&t->f);
	put_id(&t->id);
	memset(&t->tp, 0, sizeof(t->tp));
	memset(&t->id, 0, sizeof(t->id));
	memset(&t->f, 0, sizeof(t->f));
	return cleanup;
}

static ssize_t stop_write(struct file *file, const char __user *buf,
			  size_t count, loff_t *ppos)
{
	struct touch *t = file->private_data;
	int ret = 0;

	mutex_lock(&t->stop_lock);
	if (!t->stopped) {
		t->stopped = true;
		ret = touch_shutdown(t);
	}
	mutex_unlock(&t->stop_lock);
	return ret ? ret : count;
}

static const struct file_operations stop_fops = {
	.owner = THIS_MODULE,
	.open = simple_open,
	.write = stop_write,
};

static int stats_show(struct seq_file *s, void *unused)
{
	struct touch *t = s->private;

	mutex_lock(&t->lock);
	seq_printf(s, "running=%d masked=%d irqs=%lu frames=%lu empty=%lu contact_frames=%lu errors=%lu recovery=%u atn=%d\n",
		   t->running, t->irq_masked, t->irqs, t->frames, t->empty_frames,
		   t->contact_frames, t->errors, t->recovery_attempts,
		   t->running ? atn_asserted(t) : -1);
	mutex_unlock(&t->lock);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(stats);

static int __init j72_touch_init(void)
{
	struct touch *t = &touch;
	int ret, cleanup;

	mutex_init(&t->lock);
	mutex_init(&t->stop_lock);
	INIT_DELAYED_WORK(&t->recovery, recovery_work);
	ret = find_controller(&t->id);
	if (!ret)
		ret = load_files(&t->f, &t->id.ctlr->dev);
	if (ret) {
		put_files(&t->f);
		put_id(&t->id);
		pr_err(KBUILD_MODNAME ": refused before power-up: %d\n", ret);
		return ret;
	}
	ret = preflight(&t->tp);
	t->started = !ret;
	if (!ret)
		ret = drive_pins_low(&t->tp);
	if (!ret) {
		usleep_range(5000, 6000);
		ret = sample_atn(&t->tp, "pins_low");
	}
	if (!ret)
		ret = pwm_on(&t->tp);
	if (!ret)
		ret = rails_on(&t->tp);
	if (!ret)
		ret = poll_atn(&t->tp, "rails_on", 5);
	if (!ret)
		ret = drive_pin(&t->tp, PIN_ENABLE_CS, 1);
	if (!ret)
		ret = pwm_start(&t->tp);
	if (!ret)
		ret = poll_atn(&t->tp, "pwm_on", 15);
	if (!ret)
		ret = add_device(&t->tp, &t->id);
	if (!ret)
		ret = identify(&t->tp, &t->id);
	if (!ret)
		ret = boot_firmware(&t->f, &t->id);
	if (!ret) {
		/* The files are only needed for the boot; recovery never reloads. */
		put_files(&t->f);
		t->f.fw = NULL;
		t->f.cal = NULL;
		t->f.prox = NULL;
		ret = start_input(t);
	}
	if (!ret) {
		/* Without the stop file there would be no way to power off. */
		t->debugfs = debugfs_create_dir(KBUILD_MODNAME, NULL);
		if (IS_ERR(t->debugfs) ||
		    IS_ERR(debugfs_create_file("stop", 0200, t->debugfs, t, &stop_fops)))
			ret = -ENOMEM;
	}
	if (!ret) {
		debugfs_create_file("stats", 0400, t->debugfs, t, &stats_fops);
		pr_info(KBUILD_MODNAME ": stage D running; write /sys/kernel/debug/%s/stop to power off\n",
			KBUILD_MODNAME);
		return 0;
	}
	cleanup = touch_shutdown(t);
	pr_err(KBUILD_MODNAME ": refused/failed: %d (cleanup %d)\n", ret, cleanup);
	return ret;
}

static void __exit j72_touch_exit(void) {}
module_init(j72_touch_init);
module_exit(j72_touch_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Resident J72 N1 touchscreen driver (Stage D)");
MODULE_FIRMWARE(N1_FIRMWARE);
MODULE_FIRMWARE(N1_CALIBRATION);
