# Dump every J72 pin's config word (read-only)

`j72_pin_dump` reads the 200 config words of the A7 GPIO block at
`0x20e300000 + 4 * pin` and logs each one decoded: mode, data, peripheral
function, pull, input enable, interrupt group and lock. It maps one page
under the pinctrl device lock, as the touch modules' `read_pins()` does, and
writes nothing. Its imports contain no write function.

It exists to find the pad that carries PWM channel 2, the N1's 32 kHz
`grape-clk`. Neither the ADT nor iOS's kernel names that pad (see
[TOUCH.md](TOUCH.md), "What iOS does after discovery"). Pins in a
peripheral function that no known device accounts for are candidates.

The module always fails its init with `-ECANCELED` after logging, so the
kernel frees it, the loader reports `LOADER_RC=1`, and it can run again on the
same boot: once idle and once while a touch build has the PWM running.

## Reading the result

Known peripheral pins so far: 80 and 81 (i2c0), and SPI2's clock, MOSI and
MISO, which must be muxed for C1 and C2 to have worked. With `j72_touch_d2`
loaded, 119 also reads `periph=1`. Anything else in a peripheral function is
a lead, not proof. Before any pin is switched, its choice must be backed by
evidence and reviewed, because muxing a pad that something else drives can
make two outputs fight.

## Device run, 30 October

Fresh boot of `7.0.12-j72baseline`, `j72_spi_ctrl` bound. The dump ran
twice: idle (`logs/pin-dump-idle-20261030.log`), then with `j72_touch_d2`
resident, rails on, PWM channel 2 at 366/366 `ctrl=0x4001` and the N1
firmware running (`logs/pin-dump-running-20261030.log`). Both runs read 200
pins and returned `LOADER_RC=1` as designed.

Only three words changed between the runs, all pins the touch driver sets:

| Pin | Idle | Running | Why |
| --- | --- | --- | --- |
| 15 | `0x00072602` | `0x00072603` | reset released |
| 111 | `0x00070381` | `0x00000387` | ATN requested as an IRQ |
| 119 | `0x00072602` | `0x00072623` | display sync in periph1 |

Starting the PWM moved no pad. In both runs 50 pins sit in periph1 (51 with
119). They break down as follows:

- **Named by the ADT:** 32 (uart1 tx), 44 (uart5 tx), 64 (uart4 tx), 122
  (uart0 tx), 169 (uart6 tx), 80/81 (i2c0), 100/101 (i2c2), 171/172 (i2c3).
- **Inferred from the SPI chip selects** (spi2 cs0 = 79, spi3 cs0 = 99):
  76-78 and 96-98.
- **Inferred as UART receive or flow-control lines** next to the named tx
  pins: 33, 35, 37, 41, 43, 65, 123, 170.
- **Four-pin runs, probably audio MCA/I2S buses (a guess):** 84-87, 128-131,
  133-136, 147-150.
- **Not accounted for:** 1, 120, 121, 124, 125, 126, 162, 192, 193.

The ADT's `multi-touch` node gives `function-clock_enable` as `pwmC` on the
PWM channel node (`default-hz` 32768, `reg` 2), with no pin. The camera's
clocks, by contrast, are listed as GPIO 89/93 (`0x02000101`), and those two
pins are not in a peripheral function at the moment.

What this means (inference): Apple's iBSS/iBEC set the pads before m1n1 and
Linux run, and iOS's kernel writes no pad on the PWM path. So the pad state
Linux inherits is most likely the one iOS runs with, and the "clock never
reaches a pad" hypothesis is weaker than it was. No pin is switched on this
evidence. The unaccounted pins stay leads only.

## Build and test

```sh
python3 -m unittest tests.test_j72_pin_dump -v
```

The receipt is in `source/build/j72-pin-dump-20261030/` (`4a9b230d...`).
Run it with `tools/probe_once.py`; the only device check is that the pin
controller is bound.
