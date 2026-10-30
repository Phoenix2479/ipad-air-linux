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

### A7 driver never reads the control register

On 29 October a read-only scan listed all 51 register accesses in the matching
A7 `AppleSamsungSPI` driver. The driver has one 32-bit read accessor and one
32-bit write accessor. Both use the mapped base at object offset `0xf8`. The
remaining `0xf8` uses are vtable property calls with string keys, not MMIO.

CLKCFG/CTRL at `0x00` has three writes and no reads. Setup writes `0`. The stop
path before provider shutdown writes `0`. The virtual program routine at
`0xfffffff007d67ca8` writes the cached `0xd` last, after DIV, IDLE, STATUS and
CFG. The A7 driver never writes `0x1` or `0xc` alone. The newer Linux driver
also only writes CTRL: FIFO resets `0xc`, then RUN `0x1`, then `0`.

The strict `0xd` readback check in `j72_spi_enable` therefore had no source
basis. Readback `0x1` fits self-clearing reset bits 2 and 3 with a latched RUN
bit 0. That remains an inference. Neither driver establishes the A7 readback
contract. It does not establish a clock output, pin activity or FIFO reset
either. The access list, script and structured result are in
`.omx/work/j72-spi-ctrl-20261029/`.

### PWM channel registers and time conversion

The `AppleS5L8920XPWM` configure method at `0xfffffff007de0520` derives every
offset from channel `c`. It writes its first value to `c*8`, its second value
to `c*8+4`, and a control word to `0x18+c*4`. Channel 2 therefore uses `0x10`,
`0x14` and `0x20`. The control word is `0x4003`, or `0x4403` when descriptor
word 1 is nonzero. A nonzero fifth argument adds `0x204`. Before the first
channel turns on, the method calls provider slots `0x568(1,0,0)` and then
`0x560(1,0)`. The disable method at `0xfffffff007ddfd48` writes control
`0x4000` or `0`. After the last channel turns off, it calls `0x560(0,0)` and
then `0x568(0,0,0)`.

The `AppleARMPWM` caller at `0xfffffff007d585ec` reads a rate from PWM slot
`0x540`. Each of its two time arguments becomes `floor(rate * t / 10^9)`. The
constant `0x0044b82fa09b5a53`, applied after a 9-bit shift and before an
11-bit shift, matched exact division by `10^9` in 200,000 random cases. The
two arguments are therefore nanoseconds converted to input-clock cycles.

The touch clock values were resolved on 29 October. The full trace is in
`.omx/work/j72-pwm-timing-20261029/result.json`. Spot checks here confirmed
the arithmetic at `0xfffffff007d57834`, the live ADT rate and the PWM node.

- **Register roles.** The setup at `0xfffffff007d577f8` computes period as
  `(rate + hz/2) / hz`. High time is `(period * duty + 2^31) >> 32`, and low
  time is `period - high`. Channel register `c*8` takes the high time and
  `c*8+4` takes the low time, both in input-clock cycles.
- **Input rate.** Slot `0x540` requests `nclk`. The PWM node has no
  `clock-ids`, so the request falls back to the platform fixed frequency.
  That is the live `/cpus/cpu0` value of 24,000,000 Hz.
- **Touch request.** The touch enable near `0xfffffff007e958dc` passes no
  frequency or duty. The `pwmC` dispatch then uses `default-hz` 32,768 and a
  50% duty. That gives period 732 and both times 366, or about 32,787 Hz. The
  iPad 5 uses the same 24 MHz / 732 divider.
- **Write order.** iOS enables clock gate 69 (`ps_pwm0`, PMGR `0x20070`),
  then writes `0x10 = 366`, `0x14 = 366` and `0x20 = 0x4003`. It does not
  wait. Disabling writes `0x20 = 0x4000` and then gates the clock.
- **Base address.** The PWM block is `/arm-io/pwm` at `0x20a118000`, size
  `0x1000`.

Control-bit meanings are inferences. The low two bits appear to run the
output. Bit 14 stays set while stopped. `0x204` selects a one-shot pulse.
Still unresolved are the exact hardware period, `high + low` or two cycles
more, and channel 2's pin routing. No PWM operation was performed.

### OrchardLinux touch work on newer iPads

On 29 October `shaynelegault/omarchy-a` was a fork of `OrchardLinux/omarchy-a`.
It was zero commits ahead and eight behind. Its three commits were already
upstream. Upstream adds a backlight helper that selects the single
`/sys/class/backlight` entry instead of the J71b path. The `cli` submodule
now builds Z2 firmware from preconstructed boot packets into a `Z2FW` container.

`OrchardLinux/hardware` carries `apple_z2` patches and
`docs/bring-up/touch-calibration.md`. Touch is reported working on J71b
(iPad 6, A10). That driver sends per-unit calibration as HBPP blob packets. The
`hbpp-version` property selects the length encoding. The same tree adds an Apple
PMIC touch-supply regulator driver for D111. Orchard's `devices/j72b` is the
iPad 6 Cellular, not this J72 iPad Air. No Orchard file found here supports the
A7 `spi-1,samsung` controller. The Z2 protocol and calibration format are the
reusable parts. SPI, PWM and power sequencing still need A7 evidence.

The J72 generic tree lists one touch calibration reference,
`multi-touch-calibration = syscfg/MtCl/0x400`. That matches the 1,024 bytes
recovered privately. Neither saved J72 tree JSON contains an `hbpp-version`
property, so the HBPP length encoding for J72 is unknown.

## 29 October iPad 5 N1 reference

OrchardLinux/hardware pull request 6 carries the iPad 5 (J71t, A9) port from
[brkosebay/omarchy-ipad5](https://github.com/brkosebay/omarchy-ipad5). That
project reports working multitouch. Orchard lists the PR as unverified. The
patches read here come from `brkosebay/hardware`
`f764d95f70616adae483a97aaa7dae9f18cc8114`, `kernel/patches/j71t/0003`, `0004`
and `0006`. Local copies are in `source/upstream/orchard-pr6/`. Orchard's
`hardware` tree was read at `f3f14c91940e3d9ec83db6980abb2e8857fad439` and
`cli` at `d6676044d5e1d7b1bd5a162b3e130ecf0578b832`. Nothing from these trees
has been built or run.

These references target Linux 7.3-rc1. Hoolock `6831bc701a` reports 7.3-rc1,
Orchard's base is v7.3-rc1, and the PR's patches apply to `6831bc701a`. The
J72 baseline is 7.0.12 at `dfa4d420`. Each reused driver must be ported to
7.0.12, and its single-load test must be rerun on that kernel. Moving the
whole build to 7.3-rc1 would require validating the boot and desktop again.

The iPad 5 uses touch profile `C1F15,1`, the same as J72. Orchard's converter
test uses calibration address `0x10009000` and size `0x400`, matching J72. The
driver class is N1, as on J72.

### Same SPI controller family

`spi-apple-s8000.c` drives the "Samsung SPI v1" controller with a 16-entry
FIFO. Its register map matches the offsets found in the A7 iOS driver:

| Offset | S8000 Linux name | A7 iOS observation |
|---|---|---|
| `0x00` | CTRL, enable `0x0d` | writes `0`, `0`, cached `0xd` |
| `0x04` | CFG, base `0x4018`, PIO `BIT(5)` | ORs `0x20` and `0x40` into CFG |
| `0x08` | STATUS, clear `0x40000f` | write and read |
| `0x0c` | PIN, CS `BIT(1)` | write and read |
| `0x10` / `0x20` | TX data / RX data | `0xff` write / read |
| `0x30` | CLKDIV, maximum 2047 | DIV |
| `0x34` / `0x4c` | RXCNT / TXCNT | writes |
| `0x38` | WORD_DELAY, input-clock cycles | "IDLE" write |

A PIO transfer writes CTRL `0`, then CLKDIV, WORD_DELAY, STATUS clear and CFG.
Then come CTRL `0x0d`, both counts and CFG with PIO, then a STATUS poll loop.
The driver never checks a CTRL readback of `0xd`. It requires CTRL `0` after
stopping. The A10 driver in Orchard's `0012` patch also defines enable as
RUN plus both FIFO resets, and its CTRL reads only flush posted writes. Both
drivers support reading `0x1` after `0xd` as self-clearing reset bits.

### Touch power order and J72 resources

`apple_n1.c` starts with reset asserted and LDO off, then:

1. Wait 5 ms, then enable the `vdd` analog regulator and the LDO.
2. Wait 5 ms, then enable the 32,768 Hz touch clock.
3. Wait 15 ms, then send the 2-byte wake word `0xc119`.
4. Wait 10 ms, then select the display-sync pin state.
5. Send a 16-byte HBPP NOP, release reset, and wait 15 ms.

Power-off reverses this order.

| `apple_n1` resource | J72 ADT function | J72 work needed |
|---|---|---|
| `vdd` regulator | `power_ana`, `pmuL` 525 | A7 PMU register mapping |
| `ldo` (GPIO on J71t) | `power_ldo`, `pmuL` 529 | A7 PMU register mapping |
| touch clock | `clock_enable`, `pwmC` channel 2 | PWM period/high-time mapping |
| `reset` | GPIO 15 | none |
| `attn` IRQ | GPIO 111 | none |
| display sync | GPIO 119 | pin state |
| chip select | GPIO 79 | none |

The J71t clock is a PMGR TCLK register at `0x78000` that divides 24 MHz by 732.
J72 instead uses PWM channel 2, so that clock driver does not apply. The J71t
regulator maps `pmuL` `0x21f` to D2365 register `0x031c` bit 0. The A7 PMU
mapping for 525 and 529 must come from the A7 iOS PMU driver. It must not be
copied from D2365.

### D2089 touch power rails

The J72 PMU is a Dialog D2089 (`pmu,d2089`). In iOS, its `pmuL` functions are
`AppleD2089PMULDOFunction` objects. The token compare is at
`0xfffffff008686b60`. The 32-bit argument packs the LDO index in byte 0 and
options in byte 1, with a voltage in the upper half. Options bit 1 selects
on/off control without a voltage change. `AppleD2089PMU::_setLDO` at
`0xfffffff008689838` indexes a 22-entry, 12-byte table at
`0xfffffff00715b8a8`. It performs a one-byte read-modify-write on the
entry's enable register.

| ADT function | Argument | LDO | Enable register | Mask |
|---|---|---|---|---|
| `power_ana` | 525 = `0x020d` | index `0x0d`, ldo14 | `0x0084` | `0x04` |
| `power_ldo` | 529 = `0x0211` | index `0x11`, ldo18 | `0x0085` | `0x02` |

Ldo14 shares voltage register `0x0398` with ldo15 and ldo16. The iOS table
reads as 5,000 mV plus 50 mV steps. Review judged that range implausible
for this LDO, so the unit or base is unresolved. These touch arguments do
not write it. For
indexes `0x0d` to `0x0f`, iOS sets register `0x84` bits `0x03` whenever any
of bits `0x1c` is set, and clears them otherwise. Ldo18 is a switch with no
voltage field. The iPad 5 uses the same options byte in `pmuL` `0x21f`.

The Linux DTB already describes this PMIC as `pmic@3c` on I2C, with
`apple,anya-pmic` and `apple,i2c-pmic`. A touch regulator can therefore use
the parent register map, as Orchard's touch-supply regulator does. Hoolock's `simple-mfd-i2c` match for `apple,i2c-pmic` (commit `6fbefd3035`)
uses 16-bit register addresses and 8-bit values, which matches the iOS
accesses. Registers `0x84`, `0x85` and `0x398` may be readable through
regmap debugfs without a new module, if the kernel enables it. The ldo14
output voltage, the meaning of bits 0 and 1, and the cached flag remain
unverified. No PMU register was read or written.
Evidence is in `.omx/work/j72-pmu-rails-20261029/result.json`.

### iPad 7 bring-up method

[Pauli1Go/HoolockLinux-bringup-docs `docs/touch.md`](https://github.com/Pauli1Go/HoolockLinux-bringup-docs/blob/main/docs/touch.md)
explains how J172 touch was brought up. That work compared the ADT, firmware
records and iOS SPI, transport and multitouch drivers. It used passive iOS RAM
captures that added no SPI transactions, and passive PMGR and PLL reads. Each
fresh-boot test stopped at the first failed step. The measured J172 SPI input
clock was 55,384,615 Hz, derived from PLL state instead of a bootloader value.
J72's 60 MHz `nclk` therefore still needs a register-level check before
transfers. J172 required SmartIO DMA for its firmware upload. The iPad 5 S8000
driver uses bounded PIO only, so a first A7 port may not need DMA.

### A7 N1 boot protocol matches the iPad 5 driver

A read-only comparison of the 16G77 kernelcache with `apple_n1.c` found the
same N1 boot protocol. Scripts and packet checks are in
`.omx/work/j72-n1-protocol-20261029/`. Spot checks here confirmed one
`0xc119` constant site at `0xfffffff007e9ae10`. They also confirmed the
firmware validation and the calibration hash.

The shared Z2SPI code follows the same power-on order as `apple_n1`:

1. `power_ana`, then `power_ldo`.
2. Wait 5 ms, assert `enable_cs`, and start the `pwmC` clock.
3. Wait 15 ms, send the wake word `19 c1`, and wait 10 ms.
4. Select display-sync mode 3.
5. Send the HBPP NOP, release reset, and wait the personality's 15 ms.

Uploads follow the same order and framing. The J72 firmware packet (57,016
bytes, sha256 `98f73566...998d`) is identical to the J71t packet. The
calibration length field counts 4-byte words. Register calibration, read,
write and execute commands use the same values in the same order. SPI uses
mode 0 with CFG base `0x4018`. iOS sets an 83 ns period, which gives divider
5 and about 12 MHz from the reported 60 MHz `nclk`. The iPad 5 runs at about
10.26 MHz.

J72 differs in four ways:

- iOS sends the 128-byte, all-zero `prox-calibration` to `0x10009600` before
  `MtCl`. `apple_n1` never sends it.
- iOS drives `enable_cs` on GPIO 79, which is also SPI2's chip select.
- iOS keeps the device off for at least 200 ms between power cycles.
- The code default reset delay is 2 ms. The J72 personality sets 15 ms, as
  `apple_n1` does.

Porting `apple_n1` also needs these J72 pieces:

- An optional LDO regulator in place of the J71t LDO GPIO.
- A `pwmC` clock provider, using the PWM values above.
- An A7 pin state for display sync on GPIO 119.
- Chip-select idle handling on GPIO 79.
- An optional prox-calibration upload.
- Confirmation that J72 has the same D9 panel geometry.

The A7 GPIO flag decoding for reset (GPIO 15) and the touch IRQ (GPIO 111)
also remains open.

### iOS post-boot touch path (29 October)

A static read of the 16G77 kernelcache found no GPIO or pinmux write in the
PWM driver or the `pwmC` path, and no command after discovery: DefaultReports
has nothing to send for C1,J72, frames are read only on ATN, and display sync
is set once to peripheral function 1 before the boot NOP. The five N1
calibration register writes match `apple_n1`. On Linux the J72 N1 boots,
executes and answers every descriptor request but never raises ATN for
touches, with or without display sync. The open suspect is whether PWM
channel 2's 32 kHz output reaches a pad. See `probe/TOUCH.md`, "What iOS does
after discovery" and "Next session".

### OrchardLinux advances by 30 October

OrchardLinux/hardware `ae1457e` and omarchy-a `a95abba` add iPad 5 (J71t)
support from Berkay Kösebay's port, iPhone 6 (N61, A8) touch, iPad Air 2
(J81/J82, A8X), NFS root boot over USB, recovery and installer v2, and a USB
port reset after checkm8 in the CLI.

The iPhone 6 is the closest touch reference with working evdev contacts, but
like the iPad 5 it takes its 32 kHz touch clock from a PMGR register
(`apple,t7000-touch-clock` at PMGR `0x1e000`), not a PWM. Its touch analog
supply comes from the display PMU (`chestnut`). No upstream tree describes
the A7 PWM block or a pad for it, so the J72 `grape-clk` path is still
unmapped. In the J72 ADT the only pin-routed clocks are the camera's
`function-sensor_extclk` (GPIO 89 and 93, flags `0x02000101`, the same
peripheral-function form as display sync); the touch clock has no such entry.
