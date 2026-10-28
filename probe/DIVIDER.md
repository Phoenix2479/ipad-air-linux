# Check the J72 SPI divider without enabling the controller

This separate one-shot module checks register write/readback while CLKCFG is
zero. It uses the same board, resource, provider, idle-state and PM lifetime
checks as `j72_spi_status`. It registers no driver, alias or writable interface.

## Build and load the reviewed artifact

Build the matching `7.0.12-j72baseline` kernel first. Copy the `j72_spi_*.c`
module files and Makefile to the builder's Linux filesystem. Build them as described in
[the status probe instructions](README.md). The additional output is
`j72_spi_divider.ko`.

Verify the actual device model, kernel release, expected taint and unbound
inspection node before a test. Verify both uploaded artifact hashes against
their host copies. Use the single-call loader from [load-once.c](load-once.c).
For the tested revision, the iPad command was:

```sh
/tmp/j72-load-once /tmp/j72_spi_divider_r2.ko
```

The corrected revision and receipt are saved locally in
`source/build/j72-spi-divider-r2-20261028/`. This module has no parameters and
does not change its acceptance criteria from a device observation.

## Require the exact reviewed initial state

After attachment and PM resume, the module reads four 32-bit values:

| Register | Offset | Required value |
| --- | --- | --- |
| CLKCFG | `0x00` | `0` |
| CFG | `0x04` | `8` |
| STATUS | `0x08` | `0` |
| DIV | `0x30` | `1` |

An all-ones read returns `-EIO`. Any other mismatch returns `-EBUSY` before a
register write. These exact values came from the first hardware observation
and a separate A7 source review. They are an experimental restriction, not a
general legal range or proof of reset state.

With all checks satisfied, the module writes only DIV. It writes `120`, reads
the register once, restores the saved `1` and reads once more. A returning test
read always leads to the restoration attempt, including after a mismatch.
Either readback mismatch returns `-EIO`. The existing PM cleanup runs after
every returning operation and reports its result separately.

The test value comes from the A7 driver's divider calculation with the
bootloader-reported 60 MHz input and a candidate 2,000 ns period. Neither input
frequency nor an output waveform has been measured. The module writes no
controller enable, CFG, STATUS, PIN, count or FIFO register. It performs no
GPIO, reset, PWM, touch-rail, firmware or SPI transfer operation.

An MMIO access can hang and prevent restoration. Do not retry automatically.
A successful insertion remains loaded because the baseline disables unloading.
Use a fresh boot for another run of the same successful experiment.

## Recorded result on 28 October

The initial all-zero version read CLKCFG `0`, CFG `8`, STATUS `0` and DIV `1`.
It refused before a DIV write and completed cleanup. The observed state was
then checked against the matching A7 code. A separately built and reviewed
revision accepted only that exact state.

The revised module logged:

```text
DIV test=0x00000078 restore=0x00000001
operation=0
cleanup=0 detached=1
```

The loader returned zero, the serial shell remained responsive and the node
was unbound afterward. Genpd reported SPI2 and PWM0 `off-0`, with SIO_P `on`.
Taint remained the expected out-of-tree value, `4096`.

This establishes a responding divider register with an observed restoration.
It does not establish physical clock rate, transfer readiness, physical
power-off or working touch. The next gate needs an A7-specific controller and
transfer design. See [VALIDATION.md](../VALIDATION.md) for the evidence and limits.
