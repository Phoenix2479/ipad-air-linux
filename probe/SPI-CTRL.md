# Bind a PIO SPI controller driver to J72 SPI2

This module ports the iPad 5 "Samsung SPI v1" driver (`spi-apple-s8000.c` in
Orchard's `0003` patch) to the `7.0.12-j72baseline` kernel. The A7 iOS driver
uses the same register map, as [RESEARCH.md](../RESEARCH.md) records. The port
keeps the upstream bounded PIO transfer path and idle sequence. It drops the
clock, IRQ, DMA, SmartIO, autosuspend parameter and kexec panic.

## Binding to the existing node

The running tree already has the unbound platform device
`20a088000.spi-status` from `/soc/spi-status@20a088000` in
[s5l8960x-j72-spi-status.dts](s5l8960x-j72-spi-status.dts). The iPad cannot be
rebooted onto a new tree, so the module registers platform driver
`j72-spi-ctrl` whose only OF match is `apple,j72-spi-status`. The driver core
binds it to that device and attaches the `ps_spi2` power domain before probe.
The module has no device table, so nothing loads it automatically.

Probe returns `-ENODEV` with no MMIO unless the machine is `apple,j72`, the
release is `7.0.12-j72baseline` and the resource is `0x20a088000` with size
`0x4000`. It then maps the resource, enables runtime PM and takes a runtime PM
reference. The driver holds that reference while it is bound.

## Register accesses

Probe and unbind run the same idle sequence:

1. Read CTRL `0x00`, CFG `0x04` and STATUS `0x08`, and log them.
2. Write CTRL `0`, CFG `0x4018`, RXCNT `0x34` `0`, TXCNT `0x4c` `0`, PIN `0x0c`
   `0x2` (chip select high) and STATUS `0x40000f`, in that order.
3. Read CTRL. Probe fails with `-EIO` unless it reads `0`.

After a successful idle, probe registers the controller and logs
`Samsung SPI v1 PIO idle, input 60000000 Hz (reported), bus N`. The node has no
children, so the SPI core creates no device and no transfer runs. The core
still creates devices for child nodes that a later overlay adds. A probe
failure after the PM reference was taken runs the unbind sequence below.

Unbinding through sysfs unregisters the controller first. The idle sequence
then runs again, and only after it the driver drops its PM reference with
`pm_runtime_put_sync`. Then runtime PM is disabled and the mapping released.
Last, the driver core detaches the power domain with power-off requested. This
order comes from devres release order in the 7.0.12 source, not from a run.
Shutdown stops the SPI message queue and runs the idle sequence. It does not
drop the reference.

A transfer, which this module never starts on its own, writes CTRL `0`,
CLKDIV, WORD_DELAY, STATUS clear, CFG, CTRL `0xd`, RXCNT, TXCNT and CFG with
PIO `0x20`. It then polls STATUS for at most one second, and finishes with
CFG, CTRL `0` and STATUS clear on every exit. The divider is
`ceil(60 MHz / speed)`. Speeds needing a divider above 2047 are refused.

## What this does not establish

Binding sends no SPI transfer and talks to no slave. It does not show that the
controller can clock data, that chip select or SCK move, or that the N1 touch
controller responds. The 60 MHz input rate is the bootloader-reported `nclk`
for clock id 308. It is not measured, so every computed SPI rate is nominal.
Earlier one-shot probes read CTRL `0x1` after writing `0xd`. The idle check
requires `0` only after writing `0`.

## Build and load

Build as described in [the status probe instructions](README.md), using a new
module directory and `j72_spi_ctrl.ko` as the output. Verify
`modinfo -F vermagic` starts with `7.0.12-j72baseline`.
`tools/probe_once.py` refuses before upload unless the
`20a088000.spi-status` device exists with no driver link, the genpd summary
shows `spi2` off and no `j72-spi-ctrl` driver is registered. Load with the
single-call loader from [load-once.c](load-once.c):

```sh
/tmp/j72-load-once /tmp/j72_spi_ctrl.ko
```

The module stays loaded because the baseline disables unloading. To return the
controller to idle and release the domain, unbind it:

```sh
echo 20a088000.spi-status > /sys/bus/platform/drivers/j72-spi-ctrl/unbind
```

Writing the same name to `bind` would run probe again.

The build receipt in `source/build/j72-spi-ctrl-20261029/` stores the module's
undefined symbols in `imports.txt`. `tests/test_j72_spi_ctrl.py` compares them
with an allowlist and host-compiles the idle, unbind, shutdown and transfer
functions against MMIO stubs.

## Recorded result

Not yet run.
