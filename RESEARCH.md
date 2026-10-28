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

## 27 October resume

The builder now mounts the durable project directory. The pinned source archive
was reconstructed into a Git index on the VM's Linux filesystem. Its tree hash
`2e8bddd86192e520b13704583cb04264ee3d4cca` matches the tree recorded for Hoolock
commit `dfa4d42081312dd34ec33ee8d23174309f683188`. The baseline build recipe and
its deliberate compiler/config differences are documented in [BUILD-J72.md](BUILD-J72.md).

The matching Pomme v0.1.0 `kernel-4k-modules.tar.gz` and
`images-4k-initramfs.cpio.gz` were downloaded and verified against the saved
release hashes. The original kernel's evdev module can therefore be tested
without loading J71b modules or replacing the working kernel.

### Static A7 controller evidence

The saved iOS 12.4 J72 kernelcache includes `com.apple.driver.AppleSamsungSPI`.
Its embedded executable header starts at unslid virtual address
`0xfffffff007d66078`; its text starts at `0xfffffff007d66148`. The controller
constructor at `0xfffffff007d66178` assigns vtable `0xfffffff0074124d8`.
Vtable slots `+0x648` and `+0x650` resolve to `0xfffffff007d689f4` and
`0xfffffff007d68a00`. Those methods read and write a 32-bit value at a caller
supplied offset from the object's mapped register base at `+0xf8`.

The executable contains register-write call paths for offsets `0x00`, `0x04`,
`0x08`, `0x0c`, `0x10`, `0x30`, `0x34`, `0x38`, and `0x4c`. For example, the
calls at `0xfffffff007d66ef4` and `0xfffffff007d66f1c` select offsets `0x34` and
`0x4c`; the `0x4c` path is conditional. These offsets resemble the legacy Apple
SPI path in the A10 reference. They do not match the corresponding register
layout in Linux's `spi-s3c64xx.c`.

This is static evidence from a generic firmware input. It does not prove which
paths iOS executes on this physical controller, the input clock rate, or safe
Linux register values. The name `spi-1,samsung` does not justify binding the
Linux Samsung driver. The legacy Apple driver is a candidate for further
comparison, not validated A7 support.

The local disassembly was produced with:

```sh
ipsw macho disass \
  source/firmware-inputs/16G77__iPad4,2/kernelcache.release.iPad4,1_2_3 \
  --vaddr 0xfffffff007d66148 --count 3534 --quiet
```

No SPI register access or touch power operation was performed on the iPad during
this static inspection. The first live gate remains a verified kernel/input
baseline followed by a reviewed, bounded controller experiment.

### Live input baseline

The packaged `run.sh` subsequently completed a fresh DFU boot into the original
kernel and desktop. Loading the matching published evdev module from RAM added
`event0` to the `gpio-keys` handlers and created `/dev/input/event0`. Kernel taint
remained zero, desktop processes remained live, and Omarchy IPC returned `ok`.
The physical Volume Up test also passed: Linux recorded `KEY_VOLUMEUP` code 115
with press value 1 and release value 0, each followed by `SYN_REPORT`. There are
still no SPI devices or touchscreen input device. GPIO button input is verified;
the SPI touchscreen remains separate work.

### Touch profile and remaining dependencies

The iOS 12.4 prelink dictionary contains the personality
`AppleMultitouchSPI - C1,J72` in `AppleMultitouchSPIC1F15`. It matches
`multi-touch,j72`, selects `AppleMultitouchN1SPI`, and merges personality
`C1F15,1`. This is evidence to investigate the N1/C1 firmware path before
assuming the Z2 protocol used by the A10 reference. Its configuration includes
firmware execution address `0x10003400`, calibration download address
`0x10009000`, and reset-deassert delay 15. These are touch-controller memory
addresses and profile values, not instructions to access the A7 MMIO bus.

The saved `hKernelFWExtractor/src/patcher.c` only iterates a hardcoded list of
newer PMP firmware kexts. It never searches the multitouch kexts. Its empty A7
output therefore does not establish that touch firmware is missing.

A read of the running kernel's standard debug summaries found `spi2` and `pwm0`
powered off, their parent `sio_p` on, only the 24 MHz `clkref` represented in the
clock framework, and no PWM device registered. The firmware tree identifies
SPI2 gate 76 and clock ID 308. The iOS SPI start code requests its provider's
`nclk` rate. No SPI or PWM power change was made during this inspection. The
live summaries are saved locally in `.omx/work/j72-spi/live-dependencies.log`.

## Live metadata and matching firmware recovered

m1n1 preserves its live Apple device tree in a `phram` reserved-memory region.
The matching original-kernel `mtd` and `phram` modules exposed that region through
`/dev/mtd1ro`. Its 114,688-byte read matched the device-side SHA-256. The modules
could not be unloaded because the original kernel disables module unloading;
they remain only until that Linux boot ends. No flash or internal-storage read
was involved.

The live tree contains 1,024 nonzero bytes of J72 touch calibration. The private
copy is `source/research/j72-touch-calibration.bin`; its contents are not tracked
or published. The clock-frequency table has a 60 MHz entry at index 52. The A7
iOS IO driver subtracts `0x100` from clock IDs and labels that entry `nclk`, so
SPI2 clock ID 308 maps to the bootloader-reported 60 MHz rate. This is not a
physical frequency measurement. The touch PWM function targets `grape-clk`,
channel 2, with `default-hz = 32768`. It must not inherit the A10 touch clock setup.

The iOS class metadata confirms that `AppleMultitouchN1SPI` inherits from
`AppleMultitouchZ2SPI`. The matching Apple 16G77 OS image's
`usr/share/firmware/multitouch/J72.mtprops` supplies profile `C1F15,1` and declares
`PreconstructedBootloadPacketType = Z2`. It contains 57,016 bytes of constructed
firmware, version `0x0210.bin`, with a 57,000-byte payload. The OS image matches
the BuildManifest SHA-1; the packet header and payload checksums also pass.
These assets have been recovered and verified, not uploaded to the controller.

`tools/inspect-j72-kernel.py` makes the kernelcache profile inspection repeatable.
It parses the monolithic Mach-O section table and prelink XML references, emits
metadata, and does not execute or extract code. Nine tests and an actual
kernelcache parse pass. Independent review found no remaining actionable parser
defect after four fixes.

The first new hardware experiment is documented in [probe/README.md](probe/README.md).
An automatic platform-driver approach was rejected because domain attachment
can power hardware before its probe callback and can retry deferred probing.
The replacement is an explicitly loaded, one-shot module with all descriptor
checks before direct genpd attachment and one STATUS read. It compiles against
the baseline kernel, passes kernel style checks, and has an independent review
pass for that bounded experiment. The 28 October observation below records its
first hardware result. No touchscreen activation follows automatically.

## 28 October controller gate and Cascadia comparison

The diagnostic `7.0.12-j72baseline` kernel booted on the physical J72. The first
module refused an incorrect node-name comparison before power-domain attachment.
After correcting that check and replacing BusyBox's retrying module-load path
with a single `finit_module` call, the probe completed one status read. It logged
`STATUS=0x00000000 INCONCLUSIVE` and `cleanup=0 detached=1`. Linux remained
responsive. This does not establish working controller transfers. Full evidence
and limits are in [VALIDATION.md](VALIDATION.md).

Cascadia's A5 P105 digitizer also uses `AppleMultitouchN1SPI`. Its recorded live
FLL value and nine other scalar fields match our parsed J72 personality,
including the firmware and calibration addresses, clock-enable address and
reset delay. The [P105 boot record](https://github.com/rzxvx/Project-Cascadia/blob/e8f3e045a5e38bde5a5a79e1cc72ae22020d63ac/docs/research/p105-z2-boot.md)
documents firmware and calibration upload, N1 calibration and post-boot scan
commands. It also records a missing power rail that prevented scanning after a
successful upload.

P105 uses profile `C1F14,1`; J72 uses `C1F15,1`. The shared class and metadata
justify comparing the protocol implementations. They do not validate P105's
GPIOs, PMU bits, controller settings or scan commands on J72. The next step is
to verify the A7 controller and N1 defaults against matching iOS code before
configuring the controller or sending touch firmware.

### A7 register bank responds

The matching iOS driver reads the `spi-version` property and selects version-1
FIFO fields with 16 entries. It requests the `nclk` value and caches it at object
offset `0x168`. Its calculation at `0xfffffff007d66a00` uses the ceiling of
`nclk * period_ns / 1,000,000,000`. Its programming routine writes DIV `0x30`
before IDLE `0x38`, STATUS `0x08`, CFG `0x04` and CLKCFG `0x00`.

The first disabled-controller probe observed CLKCFG `0`, CFG `8`, STATUS `0`
and DIV `1` and refused its all-zero acceptance check without a DIV write.
A separately reviewed revision required those exact observed values. It wrote
DIV `120`, read back `120`, restored `1` and read back `1`. Operation and cleanup
returned zero. The shell remained responsive. This is evidence that the divider
register responds. It does not validate output frequency or SPI transfers.
The bounded implementation and procedure are in [probe/DIVIDER.md](probe/DIVIDER.md).

### PWM function spelling and remaining work

The raw J72 clock-enable function word is `43 6d 77 70`, numerically
`0x70776d43` when read little-endian. The ADT parser formats that token as
`pwmC`; Cascadia's notes show the same raw bytes as `Cmwp`. The spelling
difference does not establish a different function.

The A7 `AppleARMPWM` dispatch compares that numeric token. Its
`AppleS5L8920XPWM` channel method writes three offsets for channel 2,
`0x10`, `0x14` and `0x20`. Caller argument meanings, frequency conversion and
board-level sequencing still need verification. No PWM operation was performed.

### Clock-control readback differs from the written command

The bounded enable probe required initial CLKCFG `0`, CFG `8`, STATUS `0` and
DIV `1`. It proved DIV `120`, wrote the A7 driver's CLKCFG command `0xd`, read
back `1`, wrote stop `0`, read `0`, restored DIV `1` and read `1`. Its strict
full-word comparison returned `-EIO`; cleanup and restoration passed. The iPad
remained responsive and no retry followed.

The newer [Linux Apple SPI driver](https://github.com/HoolockLinux/linux/blob/dfa4d42081312dd34ec33ee8d23174309f683188/drivers/spi/spi-apple.c)
defines CTRL RUN as bit 0, TX reset as bit 2 and RX reset as bit 3. These
definitions suggest that the missing `0xc` may be command bits rather than an
enable failure. This is an inference for the A7, not a verified register
contract. The newer binding does not establish A7 support. No transfer or FIFO
data write occurred, but the CTRL command may have reset internal FIFOs.

The next source investigation must resolve those A7 control-bit semantics and
readback expectations before modifying the test or starting communication.
The implementation and recorded limits are in [probe/ENABLE.md](probe/ENABLE.md).
