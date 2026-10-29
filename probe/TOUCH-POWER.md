# Power the J72 touch rails and clock without releasing reset

This one-shot module is Stage B of the touch power design. It drives the four
touch pins to their safe levels, turns ps_pwm0 on, starts the 32 kHz PWM
channel 2 clock and enables the two touch rails. Touch reset stays asserted
throughout. The module makes no SPI transfer and loads no touch firmware. It
registers no driver, alias, parameter, interrupt or writable interface.

It refuses before touching hardware unless all of these hold:

- The machine is `apple,j72` and the kernel release is `7.0.12-j72baseline`.
- The ps_pwm0, ps_sio_p, syscon and pinctrl nodes pass the same checks as
  [the snapshot](TOUCH-SNAP.md), including the 32-bit syscon regmap and the
  bound pinctrl device.
- Exactly one available node is `apple,i2c-pmic`, and it is also
  `apple,anya-pmic` with `reg = <0x3c>` and a bound I2C device whose regmap
  uses 8-bit values.
- The raw pin words still match the measured snapshot: `15 = 0x00072602`,
  `79 = 0x00072203`, `111 = 0x00070381` and `119 = 0x00072602`.
- The PMIC reads `0x0084 = 0x00`, `0x0085 = 0x11` and `0x0398 = 0x14`.
- PMGR `0x20070` has actual state bits 7:4 clear, and no one owns
  `0x20a118000` size `0x1000`.

`tools/probe_once.py` also refuses before upload. It requires the PMU checks
used for [the PMU read](PMU.md), `pwm0` shown as `off-N` with no device or
child listed under it, `sio_p` and `gpio` shown as `on`, no `/proc/iomem` line
for `20a118`, no `j72_touch_power` platform device, an empty
`/sys/bus/spi/devices`, all four pins unclaimed in `pinmux-pins`, and no
debugfs `gpio` line for pins 15, 79, 111 or 119.

## Operations

All hardware accesses happen in this order. The first failure stops the
sequence and runs the teardown for everything already done.

1. Read the four raw pin words through a read-only mapping of the pinctrl
   block, then read PMGR and the three PMIC bytes. Compare all of them with
   the required boot state.
2. Claim the PWM region. Request pins 15, 79, 111 and 119 from the
   `20e300000.pinctrl` chip with `GPIOD_ASIS`. Requesting writes no register.
3. Drive 15 low and read the raw word back. Require mode 1, data 0, periph 0
   and lock 0 before any other pin write. Then drive 79 low and verify, then
   119 low and verify. A mismatch aborts immediately.
4. Wait 5 ms and sample ATN. Pin 111 stays an input; the module never calls
   `gpiod_direction_input` on it and never drives it.
5. Register a platform device named `j72_touch_power`, add it to the ps_pwm0
   genpd, enable runtime PM and take a reference. Read PMGR `0x20070`; require
   bits 7:4 to read `0xf` before mapping the PWM block.
6. Map `0x20a118000` size `0x1000` and read the nine words at `0x00` through
   `0x20`. Abort if a channel control word has either of bits 0x3 set. A
   `0x4000` stop flag is not running.
7. Update PMIC `0x0084` with mask `0x04` and value `0x04`, expect `0x04`.
   Then update `0x0085` with mask `0x02` and value `0x02`, expect `0x13`.
   The shared ldo15/16 bits `0x03` in `0x0084` are never touched.
8. Poll ATN for 5 ms, drive 79 high, then write PWM `0x10 = 366`,
   `0x14 = 366` and `0x20 = 0x4003`. Log all three readbacks immediately,
   wait 5 ms, re-read them, re-read PMGR `0x20070` and log everything. Require
   the cycles words to match exactly both times. Require the control word to
   match `0x4001` after masking off the self-clearing update bit `0x2`, both
   immediately and late. Any other bit fails the run.
9. Poll ATN for 15 ms, then hold 100 ms while sampling ATN. Reset stays low.
10. Tear down in order: drive 15 low, write PWM `0x20 = 0x4000` and log its
    readback, unmap, drop the runtime PM reference with a synchronous suspend,
    disable runtime PM, remove the device from genpd, unregister it, release
    the region and read PMGR `0x20070` again, requiring bits 7:4 to be clear.
    Then drive 79 low and verify, and finally clear `0x0085` bit `0x02` and
    `0x0084` bit `0x04`, expecting `0x11` and `0x00`.
11. Wait 200 ms, re-read pins 15, 79 and 119 and sample ATN. Free all four
    descriptors and drop every reference.

If genpd removal fails, the module keeps the platform device registered, as
the snapshot module does. The leftover device then makes the runner refuse
later loads.

The safety invariants are:

- Pin 15 is never driven high.
- Pin 79 is never driven high while either rail is off.
- Pin 111 is only read.
- Pin 79 is left low after the run. Freeing a descriptor does not restore
  iBoot's high level.

The teardown attempts every step even after an error. The module returns 0
only if the full sequence and the teardown succeeded.

## Log

The log has the boot snapshot, one decoded line per pin read, the PWM words,
one ATN line per sample, the rail updates with their readbacks, and a final
summary:

```text
j72_touch_power: gpio15 reset raw=0x........ mode=1 data=0 periph=0 input_enable=.. lock=0
j72_touch_power: ldo14_en[0x0084]=0x00 ldo18_en[0x0085]=0x11 ldo14_vset[0x0398]=0x14
j72_touch_power: pmic[0x0084] mask=0x04 value=0x04 read=0x04
j72_touch_power: pmic[0x0085] mask=0x02 value=0x02 read=0x13
j72_touch_power: pwm readback high=0x0000016e low=0x0000016e ctrl=0x00004001 want=0x0000016e/0x0000016e/0x00004001
j72_touch_power: pwm late high=0x0000016e low=0x0000016e ctrl=0x00004001 pmgr=0x000002ff
j72_touch_power: pwm high=366 low=366 ctrl=0x4001; reset still asserted
j72_touch_power: pwm stop readback ctrl=0x00004000 want=0x00004000
j72_touch_power: stage B complete; no transfer made
j72_touch_power: final pmgr_before=0x........ pmgr_on=0x........ pmgr_after=0x........ gpio15=0x........ gpio79=0x........ gpio119=0x........ cleanup=0
```

The two PWM lines are diagnostic. If a run fails at `pwm_start`, they name
which word or bit differs and whether it changes after 5 ms. The late line
also shows PMGR `0x20070` while channel 2 is configured, which separates a
clock/power problem from a register problem. The stop line records the control
word read back after `0x4000` is written.

The control word uses the `apple,s5l-fpwm` layout from
`drivers/pwm/pwm-apple.c` with UPDATE moved from bit 5 to bit 1: bit 0
enable, bit 1 update, bit 2 mode, bit 9 trigger, bit 10 invert and bit 14
output enable. iOS writes enable, update and output enable (`0x4003`) and
never reads the word back. The update bit is a self-clearing strobe, so the
steady state is enable plus output enable (`0x4001`). Both measured
readbacks, immediate and after 5 ms, showed exactly `0x4001`, and the stop
write read back exactly `0x4000`.

The pin fields use the `REG_GPIOx_DATA` (bit 0), `REG_GPIOx_MODE` (bits 3:1),
`REG_GPIOx_PERIPH` (bits 6:5), `REG_GPIOx_INPUT_ENABLE` (bit 9) and
`REG_GPIOx_LOCK` (bit 21) definitions in
`drivers/pinctrl/pinctrl-apple-gpio.c`. The final raw words must show 15, 79
and 119 as mode 1 with data 0. Pin 111 must still be mode 0, and its ATN
samples are informational.

## Build and test

Build as described in [the status probe instructions](README.md), using the
module directory `/home/arundinakar.guest/j72-touch-power-module` and
`j72_touch_power.ko` as the output. Check that `modinfo -F vermagic` starts
with `7.0.12-j72baseline`.

```sh
python3 -m unittest tests.test_j72_touch_power -v
python3 -m unittest discover -s tests -v
```

The first command compiles the whole module against stubs that record every
pin, PMIC, PWM, GPIO, genpd and runtime PM access. It checks the exact event
order, injects a fault at each step, checks the readback mismatches and
masks, and checks the safety invariants. The second also runs the built
module's import allowlist and the runner's preflight checks.

The build receipt in `source/build/j72-touch-power-20261029/` stores the
module, its source, `vermagic.txt`, `imports.txt`, `symvers-check.txt`,
`checkpatch.txt`, `sha256.txt` and `unittest.log`. The 52 imports are OF and
device lookups, GPIO descriptor calls, regmap reads and updates, region
calls, genpd add/remove, runtime PM, logging, sleeps and the standard string
and memory helpers. The allowlist has no `spi_*`, `pinctrl_*`,
`regmap_write`, `i2c_transfer` or IRQ symbol.

## Load

Run `tools/probe_once.py` without `--load` to check the device, upload the
loader and module, and compare their device-side SHA-256 with the host
copies. Then load it with the single-call loader from
[load-once.c](load-once.c):

```sh
/tmp/j72-load-once /tmp/j72_touch_power.ko
```

A successful insertion stays loaded because the baseline disables unloading.
Reboot for another run. Keep the original boot payload available.

The after state must show `0x84 = 0x00`, `0x85 = 0x11`, `0x398 = 0x14`,
PMGR bits 7:4 clear, pins 15/79/119 output low, 111 input high, all four pins
unrequested, `pwm0` off with no device, no `20a118` line in `/proc/iomem`, and
no oops, SError, PMGR error or I2C error.

## Device run

Three runs are recorded on `7.0.12-j72baseline`, each after a fresh boot.

Run 1 (`logs/probe-j72-touch-power-20261029.log`) used the first build. The
preflight matched the measured boot state, the PMIC read back `0x04` and
`0x13`, ATN read high once at `pins_low` and then low four times under
`rails_on`, and pin 79 read `0x00072203` after it was driven high. The module
then failed at `pwm_start` with `refused/failed: -5` (EIO). That build read
`0x10` first and returned on the first mismatch, so the log proves only that
at least one of `0x10`, `0x14` and `0x20` did not read back the written value.
It also logged all nine PWM words as zero before the writes while PMGR
`0x20070` read `0x000002ff`; that is still unexplained.

Run 2 (`logs/probe-j72-touch-power-run2-20261029.log`) used the instrumented
build. The preflight passed after the runner was taught to mount debugfs and
to ignore the echoed command text when looking for PMIC lines. The measured
words were:

```text
pwm readback high=0x0000016e low=0x0000016e ctrl=0x00004001 want=0x0000016e/0x0000016e/0x00004003
pwm late high=0x0000016e low=0x0000016e ctrl=0x00004001 pmgr=0x000002ff
pwm stop readback ctrl=0x00004000 want=0x00004000
```

Both cycles words read 366 and both control reads showed `0x4001`, so the
written `0x4003` loses only the update bit and keeps enable and output
enable. The run still reported `refused/failed: -5` because that build
required `0x4003` exactly. Teardown completed: pin 15 low, pin 79 low,
`0x0085 = 0x11`, `0x0084 = 0x00`, PMGR bits 7:4 clear. All four pins were
unrequested, `pwm0` was off with no device, there was no `20a118` line in
`/proc/iomem`, and there was no oops or SError; taint was 4096.

Run 3 (`logs/probe-j72-touch-power-run3-20261029.log`) used the current build
and passed end to end. Both control readbacks logged `0x4001`, the cycles
read 366, and the stop readback was exactly `0x4000`. The module logged
`stage B complete; no transfer made` and finished with
`final pmgr_before=0x00000200 pmgr_on=0x000002ff pmgr_after=0x00000200
gpio15=0x00072602 gpio79=0x00072202 gpio119=0x00072602 cleanup=0`. ATN read
low throughout the rail and PWM phases and read high again at `after_off` and
`final`. The after state showed `pwm0` off with no child, no `20a118` line in
`/proc/iomem`, all four pins unrequested, and no oops or SError; taint was
4096.

The control-word contract is now verified on the device: write `0x4003`,
require `0x4001` after masking only the self-clearing update bit, and stop
with `0x4000`. Do not mask another bit without a measured extra bit. The
all-zero pre-write block appeared in all three runs while PMGR read
`0x000002ff`, including the passing run 3. All-zero may simply be the reset
value of an unconfigured block, but that is unproven.

## Stage C

Stage B does not release reset and makes no transfer, so it cannot establish
that the controller or touch firmware works. Stage C must first stop the
ported controller from driving its internal CS: `probe/j72_spi_ctrl.c` writes
`S8000_PIN_CS` whenever `spi_get_csgpiod()` is NULL. Use `SPI_NO_CS` on the
touch SPI device and drive 79 manually, add a `cs-gpios` entry for 79, or
change the driver to skip the internal CS on J72.
