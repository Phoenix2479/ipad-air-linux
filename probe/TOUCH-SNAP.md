# Snapshot the J72 touch pins, rails and PWM block

This one-shot module is Stage B0 of the touch power design. It reads the four
touch pins, the ps_pwm0 power state, the three PMIC touch rail registers and
the PWM block, so that the first PWM access happens alone, before any pin or
rail is driven. It registers no driver, alias, parameter, interrupt or
writable interface. It requests no GPIO and makes no pinctrl call.

It refuses before touching hardware unless all of these hold:

- The machine is `apple,j72` and the kernel release is `7.0.12-j72baseline`.
- `/soc/power-management@20e000000/power-controller@20070` (ps_pwm0) is
  available, is `apple,s5l8960x-pmgr-pwrstate` with `reg = <0x20070 4>`, and
  has one `power-domains` entry. That entry is ps_sio_p at
  `power-controller@20110`, which is validated the same way.
- The parent of ps_pwm0 is a `syscon` at `0x20e000000` with size `0x24000`.
  Its syscon regmap has 32-bit values.
- `/soc/pinctrl@20e300000` is available, is `apple,s5l8960x-pinctrl`, covers
  `0x20e300000` with size `0x100000`, and has a platform device.
- Exactly one available node is `apple,i2c-pmic`. That node is also
  `apple,anya-pmic` with `reg = <0x3c>` and has an I2C device.

`tools/probe_once.py` also refuses before upload. It requires the PMU checks
used for [the PMU read](PMU.md), `pwm0` shown as `off-N` in the genpd summary
with no device or child listed under it, `sio_p` and `gpio` shown as `on`, no
`/proc/iomem` line for `20a118`, and no `j72_touch_snap` platform device.

## Operations

All hardware accesses happen in this order. The first failure stops the
sequence and, once step 4 has started, runs the teardown.

1. With the pinctrl device locked and a driver bound to it, map `0x1000`
   bytes at `0x20e300000` without requesting the region, because pinctrl owns
   it. Read the 32-bit registers of pins 15, 79, 111 and 119 at offsets
   `0x3c`, `0x13c`, `0x1bc` and `0x1dc`. Unmap. The bound driver keeps the
   gpio power domain on.
2. Read PMGR `0x20070` through the syscon regmap.
3. With the PMIC device locked, a driver bound and an 8-bit regmap, read
   `0x0084`, `0x0085` and `0x0398`.
4. Claim `0x20a118000` size `0x1000` and fail with `-EBUSY` if something
   already holds it. Register the platform device `j72_touch_snap`, add it to
   the ps_pwm0 genpd, enable runtime PM and take a runtime PM reference.
5. Read PMGR `0x20070` again. Unless the actual-state field, bits 7:4, reads
   `0xf`, fail with `-EIO` and skip the PWM block.
6. Map the PWM region and read the nine words at `0x00` through `0x20` once
   each.
7. Tear down in reverse, attempting every step even after a failure: unmap,
   drop the runtime PM reference with a synchronous suspend, disable runtime
   PM, remove the device from genpd, unregister the device and release the
   region. Then read PMGR `0x20070` a third time.

If genpd removal fails, the module keeps the platform device registered.
Freeing it would leave genpd with a dangling pointer. The leftover
`j72_touch_snap` device then makes the runner refuse later loads.

The log has one raw line, then one decoded line per pin, then the PWM line,
then the cleanup line:

```text
j72_touch_snap: gpio15=0x........ gpio79=... gpio111=... gpio119=... pmgr[0x20070]=0x........ ldo14_en[0x0084]=0x.. ldo18_en[0x0085]=0x.. ldo14_vset[0x0398]=0x..
j72_touch_snap: gpio15 reset mode=N data=N periph=N input_enable=N lock=N
j72_touch_snap: pmgr_on=0x........ pwm[0x00..0x20]=........ (nine words)
j72_touch_snap: cleanup=<first teardown error> pmgr_on=0x........ pmgr_after=0x........
```

The pin fields use the `REG_GPIOx_DATA` (bit 0), `REG_GPIOx_MODE` (bits 3:1),
`REG_GPIOx_PERIPH` (bits 6:5), `REG_GPIOx_INPUT_ENABLE` (bit 9) and
`REG_GPIOx_LOCK` (bit 21) definitions in
`drivers/pinctrl/pinctrl-apple-gpio.c`. Mode 1 is output. Mode 7 is input
with its interrupt off. The module returns 0 only if every step, the teardown
included, succeeded.

## The one state change

The only state the module changes is ps_pwm0, which it turns on and then off.
The pmgr-pwrstate driver makes the PMGR writes. On power-on it sets the
target state to `0xf`, polls for the actual state and then sets the
auto-enable bit (28). On power-off it clears the auto-enable bit and sets the
target state to 0. ps_sio_p must already be on. The module writes no MMIO,
regmap, PMIC, GPIO or PWM register.

## What this does not establish

The pin words are register settings. For an input pin, the data bit is the
pad level at the moment of the read. A PWM read that returns does not show
that the PWM block works, which pad carries channel 2, or what its control
bits mean. Genpd bookkeeping does not prove physical power-off. `pmgr_after`
is the only evidence, and it is informational. The module does not show that
the touch controller is powered or responds, or that any later write is safe.

## Build and load

Build as described in [the status probe instructions](README.md), using the
new module directory `/home/arundinakar.guest/j72-touch-snap-module` and
`j72_touch_snap.ko` as the output. Check that `modinfo -F vermagic` starts
with `7.0.12-j72baseline`. Run `tools/probe_once.py` without `--load` to check
the device and upload the module. Confirm that its device-side SHA-256
matches the host copy. Then load it with the single-call loader from
[load-once.c](load-once.c):

```sh
/tmp/j72-load-once /tmp/j72_touch_snap.ko
```

A successful insertion stays loaded because the baseline disables unloading.
Reboot for another run.

The build receipt in `source/build/j72-touch-snap-20261029/` stores the
module's undefined symbols in `imports.txt`. `tests/test_j72_touch_snap.py`
compares them with an allowlist of OF and device lookups, locking, logging,
`ioremap`/`iounmap`, the region calls, platform device registration, genpd
add and remove, runtime PM, `syscon_node_to_regmap` and `regmap_read`. The
allowlist has no write, GPIO, pinctrl, IRQ or SPI symbol.

## Recorded result

Not yet run.
