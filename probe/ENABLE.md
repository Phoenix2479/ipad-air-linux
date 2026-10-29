# Inspect J72 clock-control enable and disable readback

This separate one-shot module observes CLKCFG after an enable write and a stop
write. It retains the reviewed board, resource, ownership, genpd and PM lifetime
checks from the measured divider probe. It registers no driver or automatic
alias and has no parameters or retry loop.

This is an isolated register experiment at the observed CFG value. It does not
replay the full iOS transfer initialization or establish physical signal behavior.

## Required state and restoration order

The module reads CLKCFG, CFG, STATUS and DIV and requires exactly `0`, `8`, `0`
and `1`. An all-ones reading returns `-EIO`. Any other mismatch returns
`-EBUSY` before a register write.

The operation follows this sequence:

1. Write DIV `120` and read it once. If the read mismatches, restore and read
   the original DIV `1`, then fail without a CLKCFG write.
2. Write CLKCFG `0xd` and read once.
3. After every returning enable read, write the original CLKCFG `0` and read
   once. This stop attempt also follows a mismatched enable read.
4. Restore DIV `1` only if the disable read is exactly zero. Otherwise skip the
   faster-divider restoration, report the incomplete restoration and fail.
5. Require DIV `120`, enabled CLKCFG `0xd`, disabled CLKCFG `0` and restored
   DIV `1` for an operation success. Report PM cleanup separately.

An MMIO access can fail to return and prevent later restoration. The module
writes only DIV `0x30` and CLKCFG `0x00`. It makes no CFG, STATUS, PIN, count or
FIFO-data-register write and requests no transfer. Touch GPIO, reset, PWM, power
rails and firmware are outside this experiment.

## Build and load only a reviewed artifact

Build the matching diagnostic kernel and copy the `j72_spi_*.c` module files
and Makefile to the builder's Linux filesystem, as described in
[README.md](README.md). The additional output is `j72_spi_enable.ko`.

Verify the live board, exact `7.0.12-j72baseline` release, expected taint and
idle unbound node. Verify the uploaded module and loader hashes. The recorded
test used the unchanged single-syscall loader:

```sh
/tmp/j72-load-once /tmp/j72_spi_enable.ko
```

The artifact, source snapshot, tests and receipt are saved locally in
`source/build/j72-spi-enable-20261028/`. Interpret the recorded mismatch below
before changing the acceptance check or running another hardware test.

## Recorded result on 28 October

Initial values matched `0/8/0/1`. The device reported:

```text
DIV test=0x00000078
CLKCFG enable=0x00000001 disable=0x00000000
DIV restore=0x00000001
operation=-5
cleanup=0 detached=1
```

The operation returned `-EIO` because enabled readback `0x1` differed from the
strict `0xd` expectation. Disable and divider restoration matched. Linux stayed
responsive, taint remained `4096`, the node was unbound and genpd reported SPI2
`off-0`. The failed module initialization retained no enable module.

The newer Linux Apple SPI driver names control bits `0x4` and `0x8` as FIFO
reset controls and bit `0x1` as RUN. That is a reference for interpreting the
missing bits, not an established A7 register contract. The CTRL write may have
included FIFO-reset commands even though no FIFO data register was written.

Do not count this strict-check failure as a passing clock test or infer a
measured clock rate. Confirm the A7 control-bit/readback behavior before revising
the expected value or issuing transfers. No retry followed this observation.

## 29 October source follow-up

The matching A7 driver writes CTRL `0`, `0` and cached `0xd`. It never reads
CTRL, and the newer Linux driver never reads it either. The strict `0xd`
expectation came from the written command, not from either driver. Any revised
acceptance check must be justified as a bits-2/3 strobe hypothesis. It must not
be presented as an iOS-confirmed readback value. See [RESEARCH.md](../RESEARCH.md).
