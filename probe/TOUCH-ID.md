# Read the J72 N1 bootloader identity (Stage C1)

This one-shot module is the first stage that talks to the touch controller.
It runs the verified [Stage B](TOUCH-POWER.md) power and clock sequence, then
the identity exchange that `apple_n1` runs with `boot_stage=1`. It sends the
wake word and the HBPP NOP, releases reset, checks the HBPP reply and reads
the controller version at `0x10008ffc`. It loads no firmware or calibration,
requests no interrupt and registers no driver or input device.

`j72_touch_id.c` includes `j72_touch_power.c` with `J72_TOUCH_POWER_LIB`
defined, so the power, clock, pin and teardown code is the Stage B code
itself and not a copy.

## Chip select

iOS describes pin 79 as both `function-enable_cs` (`0x00000101`, active-high)
and `function-spi_cs0` (`0x00010001`, active-low). It is an ordinary
active-low SPI chip select that idles high once the rails are up, which is
why Stage B drives it high only after the rails.

The module creates the SPI device itself with `spi_alloc_device()` and gives
it pin 79 as its descriptor chip select, marked active-low, with
`cs_index_mask = BIT(0)` as `spi_new_device()` would set. Without the mask,
`spi_for_each_valid_cs` skips the descriptor and pin 79 never moves. The SPI
core then
drives 79 low around each message and high after it. Because the device has a
descriptor chip select, `spi_set_cs()` in 7.0.12 never calls
`s8000_spi_set_cs()`, so `j72_spi_ctrl` writes no internal CS during a
transfer and needs no change. `SPI_NO_CS` alone would not have worked: with
no descriptor, the core still calls the controller's `set_cs` hook
(`drivers/spi/spi.c:1104`).

The module refuses before touching any hardware unless `/soc/spi-status@20a088000`
has a controller bound to `j72-spi-ctrl` with one chip select, no `cs-gpios`
table and descriptor chip selects enabled. A `cs-gpios` table would overwrite
the descriptor in `spi_add_device()`.

## Sequence

1. Find the controller and allocate two 16-byte heap buffers.
2. Stage B preflight. Pin 79 may now read either iBoot's high
   (`0x00072203`) or the low (`0x00072202`) that a finished run leaves, so a
   failed C1 load can be retried without a restart.
3. Stage B up to the clock: pins low, ps_pwm0 on, rails on, 79 high, PWM
   channel 2 at 366/366 with `0x4003`, then 15 ms of ATN samples.
4. Add the SPI device: mode 0, 8 bits, 12 MHz (divider 5 from the reported
   60 MHz input, the 83 ns period iOS uses), modalias `j72-touch-id`. Verify
   79 still reads high.
5. Send the wake word `19 c1`, wait 10 ms, send the 16-byte HBPP NOP
   (`1a a1` then `18 e1` seven times).
6. Drive reset (15) high and verify it, wait 15 ms and sample ATN.
7. Send the NOP again. Both of the first two big-endian words must be HBPP
   words (`18e1`, `1aa1`, `1f01`, `4879`, `4969`, `4ad1`, `4bc1`), or the run
   fails with `HBPP not detected`.
8. Read register `0x10008ffc` (request `1c 73 8f fc 10 00 01 9b`, then
   `1a a1 18 e1 18 e1 18 e1`). Zero or all ones fails the run.
9. Teardown: reset low, remove the SPI device, restore 79's polarity flag,
   then the Stage B teardown (PWM stop, ps_pwm0 off, 79 low, rails off),
   200 ms, and a final pin check.

Every transfer is logged with its full transmit and receive bytes. None of
them carries firmware or calibration data.

## Known deviation from iOS

iOS selects display-sync "mode 3" on pin 119, which the GPIO decode maps to
peripheral function 1, just before it
sends the NOP and releases reset. C1 leaves 119 low on the assumption that
the N1 boot ROM does not need display sync to answer HBPP. That assumption
is unproven. If the run fails with `HBPP not detected`, treat the missing
display-sync step as the first suspect before the wiring, the clock or the
chip.

## Expected log

```text
j72_touch_id: wake tx=19 c1 rx=.. .. ret=0 hz=12000000
j72_touch_id: boot_nop tx=1a a1 18 e1 ... rx=... ret=0 hz=12000000
j72_touch_id: gpio15 reset raw=0x00072603 mode=1 data=1 periph=0 input_enable=1 lock=0
j72_touch_id: atn phase=reset_released value=.
j72_touch_id: hbpp tx=1a a1 18 e1 ... rx=<two HBPP words> ... ret=0 hz=12000000
j72_touch_id: HBPP detected; N1 controller 0x........
j72_touch_id: stage C1 complete; no firmware loaded
j72_touch_id: final version=0x........ complete=1 pmgr_before=... cleanup=0
```

`apple_n1` selects const-cal 2 for version `0x434d11a0` and const-cal 3
for any other value, so the version decides a later Stage C2 setting. Any
value other than zero or all ones is a pass for C1.

## Device run

Run 1 (`logs/probe-j72-touch-id-20261029.log`, module `a5a0932b...`) passed
on 29 October. It ran on the same boot as the Stage B run 3, after loading
`j72_spi_ctrl` (`9169c3c3...`), with pin 79 in the rerun state `0x00072202`.

```text
wake tx=19 c1 rx=00 00 ret=0 hz=12000000
boot_nop tx=1a a1 18 e1 ... rx=00 (all 16 bytes) ret=0
gpio15 reset raw=0x00072603 mode=1 data=1
atn phase=reset_released value=0
hbpp tx=1a a1 18 e1 ... rx=1f 01 48 79 48 79 48 79 48 79 48 79 48 79 48 79 ret=0
register_request tx=1c 73 8f fc 10 00 01 9b rx=48 79 48 79 48 79 48 79 ret=0
register_result tx=1a a1 18 e1 18 e1 18 e1 rx=4c 39 15 c1 43 4d 01 66 ret=0
HBPP detected; N1 controller 0x434d15c1
atn phase=identified value=1
stage C1 complete; no firmware loaded
final version=0x434d15c1 complete=1 pmgr_before=0x00000200 pmgr_on=0x000002ff
      pmgr_after=0x00000200 gpio15=0x00072602 gpio79=0x00072202 gpio119=0x00072602 cleanup=0
```

The loader returned 0. Afterwards pwm0 was off, all four pins were
unclaimed, there was no SPI device and no `20a118` region, and there was no
oops or SError; taint was 4096.

What this shows:

- SPI2, the ported controller, the 12 MHz setting and pin 79 as an
  active-low chip select all work. Before reset was released the N1 returned
  only zeros, and right after it returned HBPP words.
- The N1 boot ROM answers HBPP without display sync, so leaving pin 119 low
  is fine for identity. Firmware execution may still need it.
- ATN went low when reset was released and high again after the version
  read.
- The controller version is `0x434d15c1`. `apple_n1` selects const-cal 3
  for any version other than `0x434d11a0`, so Stage C2 uses const-cal 3.
- The PWM block read `0x10 = 0x16e`, `0x14 = 0x16e` and `0x20 = 0x4000`
  before this run's writes. Those are the values the Stage B run left, so the
  channel registers keep their contents while ps_pwm0 is off. The all-zero
  block in the earlier runs was the state iBoot leaves.

## Build and test

Copy `j72_touch_power.c`, `j72_touch_id.c` and the Makefile to the builder
and build as described in [the status probe instructions](README.md).

```sh
python3 -m unittest tests.test_j72_touch_id -v
python3 -m unittest discover -s tests -v
```

The C1 tests compile both sources against the Stage B stubs plus an SPI
controller, a gpiolib-style active-low chip select and a fake N1 that only
answers once reset is released. They check the full event order, that every
transfer happens with the rails, clock and device up, that reset is released
only after the device exists, that the device is removed before the clock and
rails go off, that every failure (HBPP absent, bad version, a failed transfer
at each step, a failed device add) unwinds to the Stage B safe state, and
that the built module imports only the allowed symbols.

The receipt is in `source/build/j72-touch-id-20261029/`.

## Load

On a fresh boot, load `j72_spi_ctrl` first, then this module, each through
`tools/probe_once.py`. The runner refuses `j72_touch_id` unless spi0 exists,
`20a088000.spi-status` is bound to `j72-spi-ctrl`, spi2 is on, there is no
SPI device, and every Stage B check passes.

A successful load stays resident because unloading is disabled. A failed load
is freed by the kernel and, after a clean teardown, leaves a state the runner
and the preflight both accept, so a corrected module can be loaded without a
restart.
