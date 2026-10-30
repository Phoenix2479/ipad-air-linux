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

## Build and test

```sh
python3 -m unittest tests.test_j72_pin_dump -v
```

The receipt is in `source/build/j72-pin-dump-20261030/` (`4a9b230d...`).
Run it with `tools/probe_once.py`; the only device check is that the pin
controller is bound.
