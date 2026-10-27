# Inspect J72 SPI2 status

This is an experimental, manually loaded kernel module for the separate
`7.0.12-j72baseline` kernel. It validates the J72 board, descriptor, resource,
power-domain provider and idle device state before explicitly attaching the
device to genpd. It reads STATUS at SPI2 offset `0x08` once, then releases its PM
reference, detaches and unmaps the resource.

It registers no platform driver or automatic module alias. It configures no
SPI controller, pin, interrupt, DMA channel, reset, touch supply or PWM output.
It sends no transfers and loads no touch firmware. Zero status is inconclusive;
all ones is an error. A kernel hang or abort remains a possible experimental
result. Keep the original boot payload available for recovery.

## Build with the matching baseline

Build the kernel in [BUILD-J72.md](../BUILD-J72.md) first. Copy the module's C
file and Makefile into a directory on the builder's Linux filesystem, then use
that directory as `M`:

```sh
make -C /home/arundinakar.guest/j72-linux-tar \
  O=/home/arundinakar.guest/j72-baseline/kernel \
  M=/home/arundinakar.guest/j72-spi-status-module ARCH=arm64 modules
```

Copy `s5l8960x-j72-spi-status.dts` into the kernel source's
`arch/arm64/boot/dts/apple/` directory and compile it:

```sh
make -C /home/arundinakar.guest/j72-linux-tar \
  O=/home/arundinakar.guest/j72-baseline/kernel ARCH=arm64 \
  apple/s5l8960x-j72-spi-status.dtb
```

The descriptor includes the original J72 tree and adds one unbound inspection
node. The module and descriptor must be inspected together. The original
`run.sh` uses the original payload and does not run this experiment.

## Interpret the result

After a fresh experimental boot, verify the model and exact kernel release
through USB serial before uploading and loading the checksum-verified module
from RAM. The kernel log reports `j72_spi_status: STATUS=...`, followed by a
separate cleanup result. A successful `insmod` alone is insufficient evidence.
Inspect the genpd summary after cleanup. Report its observed state without
claiming that bookkeeping guarantees physical power-off.

This external GPL module is expected to set the kernel's out-of-tree taint.
The baseline disables module unloading, so a successful insertion cannot be
repeated by unloading and reloading it. Reboot for another test. There is no
automatic next operation after the snapshot.

Compilation, exact release matching, descriptor checks, kernel style checks,
and independent source review passed on 27 October. Hardware execution is
pending. Touch remains unimplemented.
