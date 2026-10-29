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

Build the kernel in [BUILD-J72.md](../BUILD-J72.md) first. Copy
the `j72_spi_*.c` module files and `Makefile` into a directory on the builder's
Linux filesystem, then use that directory as `M`:

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

Compile `load-once.c` on the same aarch64 Linux builder:

```sh
cc -static -Os -Wall -Wextra -Werror \
  /Users/arundinakar/iPad-Air-Linux/probe/load-once.c \
  -o /home/arundinakar.guest/j72-load-once
```

Upload the loader and module to iPad RAM and verify both device-side SHA-256
hashes against the host files. Load the module with:

```sh
/tmp/j72-load-once /tmp/j72_spi_status.ko
```

The loader makes one `finit_module` syscall with no parameters or force flags.
It exits on failure without a retry. BusyBox 1.37.0 `insmod` can invoke module
initialization again after a failed first load. Use the single-call loader for
this experiment.

## Boot the diagnostic kernel

`run.sh` boots the original desktop kernel. These modules only load on
`7.0.12-j72baseline`, so use the experimental payload instead. Check its hash
against `source/build/j72-spi-status/receipt.json` (`12657342...`), then run:

```sh
cd ~/iPad-Air-Linux/source/remote_boot
./remoteboot.sh boot \
  ~/iPad-Air-Linux/source/build/j72-spi-status/m1n1-j72-spi-status.macho \
  ~/iPad-Air-Linux/boot/m1n1-monitor-stub.macho
```

Enter DFU manually when prompted, using the sequence in the top-level README. Do
not start `tools/session.py`; the desktop has not been verified on this kernel.

## Interpret the result

After a fresh experimental boot, verify the model and exact kernel release
through USB serial before uploading and loading the checksum-verified module
from RAM. The kernel log reports `j72_spi_status: STATUS=...`, followed by a
separate cleanup result. A successful loader exit alone is insufficient evidence.
Inspect the genpd summary after cleanup. Report its observed state without
claiming that bookkeeping guarantees physical power-off.

This external GPL module is expected to set the kernel's out-of-tree taint.
The baseline disables module unloading, so a successful insertion cannot be
repeated by unloading and reloading it. Reboot for another test. There is no
automatic next operation after the snapshot.

On 28 October the experimental kernel booted on the J72. The first module
refused its preflight because it compared a node's stored name with an absolute
path. The corrected helper resolves the exact path, compares node identity and
releases the lookup reference. Host regression tests, compilation, kernel style
checks and independent source review passed.

One corrected load through `load-once` returned zero and reported
`STATUS=0x00000000 INCONCLUSIVE`, followed by `cleanup=0 detached=1`. The serial
shell remained responsive. The inspection node stayed unbound and genpd reported
SPI2 `off-0` afterward. This does not prove controller communication, physical
power-off or touchscreen operation. See [VALIDATION.md](../VALIDATION.md) for the
recorded limits. Touch remains unimplemented.

The separately reviewed [divider experiment](DIVIDER.md) records the next
controller test. The [clock-control experiment](ENABLE.md) records the later
enable/disable readback. Building the modules does not load them automatically.
The [PMU read experiment](PMU.md) reads the D2089 touch rail registers without any write.
The [SPI controller driver](SPI-CTRL.md) binds a PIO-only Samsung SPI v1 driver to the inspection node and leaves SPI2 idle.
The [touch snapshot](TOUCH-SNAP.md) reads the touch pins, rails and PWM block, turning only ps_pwm0 on and then off.
The [touch power Stage B](TOUCH-POWER.md) powers the rails and starts the 32 kHz clock while reset stays asserted, with no transfer.
The [touch identity Stage C1](TOUCH-ID.md) reuses Stage B, releases reset and reads the N1 bootloader version over SPI, with no firmware.
The [touch firmware Stage C2](TOUCH-FW.md) adds the calibration and firmware uploads, execute and the report descriptor read.
The [resident touchscreen driver](TOUCH.md) keeps the N1 running with an ATN interrupt and a multitouch input device.
