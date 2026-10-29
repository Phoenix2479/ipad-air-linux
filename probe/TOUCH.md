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

## Open questions for the device run

- Whether scanning needs display sync (pin 119 periph mode). If the IRQ count
  stays at zero while you touch the screen, that is the first suspect.
- The axis orientation relative to the display.
- The ADT gives ATN interrupt type 0. Level-low is this driver's choice.
