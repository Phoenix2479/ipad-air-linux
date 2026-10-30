# Resident J72 touchscreen driver (Stage D)

`j72_touch` runs the [Stage C2](TOUCH-FW.md) boot (power, clock, identity,
prox and MtCl calibration, firmware, execute, descriptors) and then stays
loaded as the touchscreen. It follows `apple_n1`'s runtime path:

- An "Apple N1 Touchscreen" multitouch input device with 32 slots, X and Y
  in hundredths of a millimetre, ranges taken from the D9 edges.
- A threaded, one-shot, level-low interrupt on ATN (pin 111). The initial
  drain runs before the interrupt is requested, and the interrupt is enabled
  only after it. `apple_n1` uses a falling edge, but an edge that arrives
  between the last ATN check and the unmask is lost, leaving ATN low with no
  interrupt pending. With a level trigger the line fires as soon as it is
  unmasked while ATN is low, and the one-shot thread keeps it masked while
  frames are read, so it cannot storm.
- Frame reads with the `eb` request and response, both checksums and the 1/2
  sequence toggle, and classic `0x43`/`0x44`/`0x45` contact reports. A
  report is validated in full before any slot changes. States 3 and 4 are
  touching.
- On a frame or ATN error the IRQ is masked, contacts are released and a
  recovery runs after 1 s: wake and drain, then one rediscovery. It never
  resets or reloads firmware. Three failed recoveries stop retrying.

Requesting the interrupt makes the pin driver write pin 111's mode field.
iBoot leaves 111 at `0x00070381` (group 7, mode 0). Startup moves it to group
0 with mode 3 (level low), masking writes mode 7 ("input, IRQ off"), and
`free_irq` leaves group 0, mode 7: `0x0000038f` with ATN high. The pin stays
a pulled-up input and is never driven. The preflight accepts either value,
and `stop` checks that 111 is in one of them.

The firmware and calibration are released as soon as the boot finishes.

## Debugfs

- `/sys/kernel/debug/j72_touch/stats` shows `running`, `masked`, `irqs`,
  `frames`, `empty`, `contact_frames`, `errors`, `recovery` and the ATN
  level.
- Writing anything to `/sys/kernel/debug/j72_touch/stop` frees the IRQ,
  removes the input device, puts reset low, removes the SPI device and runs
  the Stage B teardown, ending in the same rerun state as the earlier stages.
  A second write does nothing.

Module unloading is disabled, so a corrected build must use a new module
name. After `stop`, the runner and the preflight accept the next one on the
same boot.

If the debugfs directory or the `stop` file cannot be created, init fails
and powers everything off, so the driver never runs without a way to stop
it.

The first 16 contact frames are logged with each contact's id, state and raw
x/y. Touch positions are not private. No firmware or calibration byte is
logged.

## Build and test

Build `j72_touch_power.c`, `j72_touch_id.c`, `j72_touch_fw.c` and
`j72_touch.c` together.

```sh
python3 -m unittest tests.test_j72_touch -v
```

The fake N1 queues contact frames and holds ATN low while any are pending.
The harness raises the interrupt, runs queued recovery work, reads `stats`
and writes `stop` twice. The tests check the input range, the IRQ flags and
order, the events for touch, move and lift, clamping to the edges,
recovery after three corrupt frames, the unwind from every input or IRQ
failure, and the safe final state. Mutations that skip `enable_irq`, free the
IRQ after the input device, drop the edge offset or skip masking all fail.

The receipt is in `source/build/j72-touch-20261029/`.

## Display sync

`j72_touch` muxes pin 119 to peripheral function 1 where iOS does, between
the wake word and the boot NOP. The pin controller's mux is strict, so the
driver frees its GPIO line for 119, registers a runtime mapping (`sync-on` =
`periph1`, `sync-off` = `gpio`) for its ps_pwm0 platform device and selects
`sync-on`. Teardown follows `apple_n1`'s order: reset low, then `sync-off`,
then the mapping is dropped, the GPIO line is requested again and 119 is
driven low and verified, and only then do the clock and rails go off.

## Device runs

All on the Stage B boot, after `j72_spi_ctrl`, C1 and C2.

1. The first `j72_touch` build (`aba6e1ee...`) booted the N1 but failed the
   initial drain with `-74`. A trace build (`8cbeb040...`) showed why: the
   frame request reply said 14 bytes were waiting (`e1 0e 00`), the frame
   read cleanly (`ea 01 0e 00 ...`, a type `0x50` report), but the next
   request still said 14 bytes and its response was all zeros. The command
   history in the reply bytes suggests the length field is not a fresh
   "frame pending" signal; ATN is.
2. With the drain gated on ATN (`d080b94c...`) the driver came up: ATN was
   asserted once for that frame and released after it, then reporting was
   enabled on IRQ 52 (`Apple-GPIO 111 Level`). Touching the screen gave
   `irqs=0 frames=0`, and ATN stayed high.
3. `stop` returned `cleanup=0`, all four pins unclaimed, and pin 111 at
   `0x0000038f`, which the preflight now accepts.
4. `j72_touch_d2` (`aabd2f20...`, the same source with display sync) came up
   with pin 119 at `periph=1 data=1`. No boot frame arrived this time. Touching
   the screen again gave `irqs=0 frames=0` with ATN high.

So the N1 firmware boots and answers every control request but reports no
touches, with or without display sync. The remaining J72 difference from
the iPad 5 is the 32 kHz clock: the iPad 5 uses a PMGR TCLK register, J72
uses PWM channel 2 (`grape-clk`), and no pad for that output is named in the
ADT, so whether the clock reaches the N1 is unconfirmed.

5. Both builds were stopped for the night. `j72_touch_d2` stop returned
   `cleanup=0`, pin 111 `0x0000038f`, all four pins unclaimed and no IRQ.

## What iOS does after discovery

A static read of the 16G77 kernelcache (29 October; addresses are kernel
virtual addresses, notes in `.omx/work/j72-touch-silent-20261029/`) found:

- **No pad setup for the touch clock.** `AppleS5L8920XPWM` (code at
  `0xfffffff007ddfac8`) only touches its own registers and turns on clock
  gate 69. `grape-clk` setup (`0x7d57510`) looks for an optional
  `function-enable` property that J72 does not have, and `pwmC`
  (`0x7d57d34`) only starts channel 2 at 32,768 Hz, 50% duty. So the pad that
  carries channel 2 is either hard-wired or set by iBoot, which is encrypted
  here.
- **No start command.** After execute, iOS marks the firmware present, waits
  2 ms, caches device properties with GET requests only, runs DefaultReports
  (`0x7e8b5d8`), which has nothing to send because neither the C1,J72
  personality nor `J72.mtprops` lists any, and reports ready. The interrupt is
  enabled before the boot, and frames are read only when ATN fires.
- **Display sync matches.** Power-on passes mode 3, which becomes pin mode 2:
  `cfg & ~0x260 | 0x220` (`0x858db2c`), peripheral function 1 plus input
  enable, set once before the boot NOP. That is what `j72_touch_d2` does
  (`0x00072623` read back).
- **Calibration registers match** `apple_n1` (`0x7e9be60`-`0x7e9bf10`).
  6089 x 32,768 Hz is about 199.5 MHz, so the N1's frequency-locked loop
  appears to use the 32 kHz input as its reference. That reading is an
  inference: with no reference clock the loop would not lock, and scanning
  could stall while SPI still works, which fits what we see.
- iOS sets ATN to falling edge. This driver uses level-low, which cannot be
  the cause: the raw ATN level never dropped during touches.

Not determined: which pad carries PWM channel 2 and whether iBoot sets it.

## What iOS userspace sends (30 October)

A static read of the 16G77 root filesystem's MultitouchSupport and of
`AppleMultitouchSPI.kext/PlugIns/MultitouchHID.plugin` (notes in
`.omx/work/j72-mt-userspace-20261030/` and
`.omx/work/j72-touch-setreport-20261030/`) found that userspace does send
set-reports after the kernel boots the firmware:

- **The plugin's start-up.** `MultitouchHIDClass::start` creates the device
  and calls `MTDeviceStart`, which sends nothing on SPI. The dispatch source
  then calls user-client selector 0 with `[1]`.
  `MTSimpleHIDManager::setDeviceDefaultProperties` sets touch mode 0 and
  orientation 1.
- **Touch mode 0** (`MTDeviceSetTouchMode`, `0x19b728f58`) sends report
  `0xBF` with detectable mask `0x819d` and suppressing mask `0x0009`. It then
  sends report `0xAF` with `00`. Orientation sends `0x9D`, then touch mode
  again.
- **`0xC8`, "enable reporting of all inputs",** is gated on `parser-options`
  bit `0x20`. J72 has `0x10`, so it is not sent.
- **The SPI framing** (kernel N1 slot `0x6b0`): a 2 ms sleep, an `E3` length
  check, a wake, a 16-byte `E4 id len data... sum16`, then an `E1` status
  read whose byte 8 is the result.

`j72_touch_d3` (`86b370cd...`) sends that sequence after discovery and reads
the reports before and after. On the device:

| Report | Before | After | Result |
| --- | --- | --- | --- |
| `0xBF` | `95 01 00 00` | `9d 81 09 00` | accepted, both writes |
| `0xAF` | `00` | `00` | accepted, both writes |
| `0x9D` | status `02` | status `02` | not supported, not written (iOS would skip it too) |

ATN stayed high after the settings, the drain found nothing, and touching
the screen gave `irqs=0 frames=0`. `stop` returned `cleanup=0` with all four
pins unclaimed. The firmware's default detection mask (`0x0195`) is already
non-zero, so the missing set-reports were not what kept the N1 silent. The
32 kHz clock is the leading suspect again.

## Frame poll: the N1 is not sensing (30 October)

`j72_touch_d4` (`f5d2437c...`) and `j72_touch_d5` (`2eff9870...`) add a
debugfs `poll` file. Writing N reads the frame-request header every 20 ms
whatever ATN says, logs header changes, and reads any frame longer than
J72's 14-byte stale status. d5 also sends the `0xEE` wake, rate-limited to
one per 100 ms, before each read.

- **Without wakes (d4):** after two leftover replies, every header read as all
  zeros: 1000 reads over about 25 s while the screen was being touched,
  0 frames, ATN high.
- **With wakes (d5), idle:** each wake is followed by one `e1 00 00` header
  (awake, nothing pending), then zeros again within 20 ms. The N1 drops into
  idle almost at once and answers only after a host wake.
- **With wakes (d5), touched for about 25 s:** 1000 reads, 414 header
  changes, every one either the idle `e1 00 00 ...` reply or zeros, and
  0 frames.

So the firmware runs and the SPI link works, but the N1 produces no touch
data at all. The fault is in sensing, not in the ATN line or the host's
frame reads. iOS's kernel sends no command we lack: user-client selector 0
only enables frame delivery to that client, selector 4 power state 2
repeats the boot-time power functions (J72 has only `power_ana` = pmuL 525
and `power_ldo` = pmuL 529, both on), and there is no hidden scan-enable
command (notes in `.omx/work/j72-kext-start-20261030/`). iBoot sets the same
pads for Linux as for iOS ([PIN-DUMP.md](PIN-DUMP.md)), and our PWM writes
match iOS's.

## Next session

On 30 October the iPad ran a fresh `7.0.12-j72baseline` boot with
`j72_spi_ctrl` bound. `j72_touch_d2` through `j72_touch_d5` were all loaded and
stopped (`cleanup=0`, pins 15/79/119 low, 111 at `0x0000038f`, nothing
claimed), so the next touch build on that boot needs a new module name. The
battery drains while Linux runs (no charger driver), so check the boot first.

1. **Check the iPad first.** Over serial, read `uname -r`, uptime and the
   pinmux-pins lines for 15/79/111/119. If it rebooted, boot the diagnostic
   kernel ([README](README.md)) and load `j72_spi_ctrl` before anything else.
2. **Clock-off comparison.** Load a build that leaves PWM channel 2
   stopped and compare the boot byte for byte with a normal run: HBPP,
   version, uploads, calibration reply, execute, discovery reports and the
   d5 idle poll. If nothing changes, the N1 never sees our 32 kHz clock and
   its frequency-locked loop has no reference, which would explain an N1
   that runs but never scans. If something changes, the clock does arrive
   and the cause is elsewhere (analog supply levels, calibration data).
3. **Check other A7 sources** for the grape-clk pad: iPad mini 2/3
   (J85/J86/J87, same A7) device trees, and m1n1's s5l8960x pin tables.
4. **If a candidate pad is found,** add it to the touch driver as a second
   runtime mapping (like display sync), drive it only while the rails and
   clock are up, restore it in teardown, review it, and show the evidence
   before loading it under a new name.

## Open questions for the device run

- Whether scanning needs display sync (pin 119 periph mode). If the IRQ count
  stays at zero while you touch the screen, that is the first suspect.
- The axis orientation relative to the display.
- The ADT gives ATN interrupt type 0. Level-low is this driver's choice.
