# J72 hardware work to resume

The verified desktop runs from the Mac over USB. Native touch remains unported.

The pinned kernel has `CONFIG_TOUCHSCREEN_APPLE_Z2=y` and `CONFIG_SPI_APPLE=y`, but
its A7 DTS has no SPI controller node. `apple_z2.c` only matches J293 and J493 Mac
Touch Bars. Adding a J72 compatible alone does not supply power, clock, firmware,
or controller support.

The generic Apple iOS 12.4 J72 device tree was parsed into JSON. Its touch node is
under `arm-io/spi2/multi-touch`, with SPI MMIO `0x20a088000`, size `0x4000`, AIC IRQ
138, CS GPIO 79, reset GPIO 15, touch IRQ GPIO 111, display sync GPIO 119, PMU power
functions 525 and 529, a PWM clock function, and a `syscfg/MtCl/0x400` calibration
reference. These descriptions have not been validated as Linux register or
power-sequencing operations.

The old A7 SPI controller reports `spi-1,samsung`. Do not bind the current M1 SPI
driver or copy A10 J172 register setup without checking the A7 register variant.
The A7 PMGR DTS already defines `ps_spi2`. The controller input clock rate is not
established by this experiment.

The iOS 12.4 kernel cache contains AppleMultitouchSPI, AppleMultitouchZ2SPI, and
AppleMultitouchSPIC1F15 strings. Hoolock's current hKernelFWExtractor returned no
firmware files for that cache. No per-device calibration was extracted.

The 4 KB kernel config has `CONFIG_INPUT_EVDEV=m`. The small initramfs only carries
USB/NBD modules, so the matching evdev module is another prerequisite for evdev
tests. Do not load the Omarchy image's 7.3 J71b modules into the running 7.0.12 A7
kernel. Pomme v0.1.0 publishes matching 4 KB modules separately.

Useful reference work is Pauli1Go's A10 iPad 7 touch implementation. Its SPI, SIO,
power, firmware, and calibration logic is device-specific evidence, not proven A7
support. The generic kernel and driver inputs from this session are saved under
`source/research/`; the extracted Apple firmware inputs are under
`source/firmware-inputs/`.

First step on resume: read the checkpoint and establish a buildable baseline and
a safe first A7 SPI probe gate before another device boot. Lima's stopped
`ipad-j72-builder` VM is available for Linux builds. It shares the temporary
working directory used during the experiment; update that mount to the durable
bundle before relying on it after a Mac restart.
