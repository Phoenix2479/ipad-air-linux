# Recorded validation

## Verified on the physical J72

- USB serial `uname -a` reported:
  `Linux pomme 7.0.12-gdfa4d4208131-dirty ... aarch64 Linux`.
- `/proc/device-tree/model` reported `Apple iPad Air (Cellular)`.
- `/proc/cmdline` reported `console=tty0 loglevel=7 rdinit=/init`.
- DRM detected a connected 1536 x 2048 simpledrm output.
- USB NCM networking and a USB serial root shell worked.
- A read-only host NBD image mounted and its Alpine executables ran in a chroot.
- A disposable 16 MB host image passed an NBD write/unmount/read-back test.
- The large rootfs extraction over writable USB NBD stalled; it was discarded.
  The final root image was populated in a local Linux VM instead.
- The final root image passed `e2fsck -fn` with exit code 0.
- The published Omarchy A archive matched its receipt SHA-256 and expanded byte
  and member counts. Its path audit found no absolute/traversal member names or
  descendants beneath absolute symlinks.
- `labwc`, `foot`, QuickShell wallpaper, panel, and menu rendered on the iPad.
- `omarchy-shell shell ping` returned `ok`; menu IPC opened the root menu.
- The project's native-panel scaling helper set the output scale to 2.
- The saved start command reached `J72_DESKTOP_READY`.
- The saved stop command closed the desktop, unmounted the chroot, reset NBD
  capacity to zero, and stopped the host NBD server. PID tracking and private
  D-Bus cleanup were corrected after these real tests exposed failures.

## Host checks

- Python syntax compilation passed for the launcher and NBD server.
- Shell syntax checks passed for the boot/runtime wrappers.
- NBD local protocol tests verified reads, write refusal in read-only mode, and
  bounds-safe replies. The shipped server only exports read-only images.
- Boot payload checksums are stored in `boot/SHA256SUMS`.
- `rootfs.img` still matched `rootfs.sha256` after the desktop replay tests.

## Limits

This is not an upstream-supported J72 Omarchy release. Native touch, wireless,
audio, internal storage, suspend/resume, charging policy, and long-duration
stability are unverified or unavailable. The session uses root and temporary
configuration over a dedicated USB link. No iPad storage install was attempted.

The original Hoolock cold boot succeeded. On 27 October the exact packaged
`run.sh` wrapper also completed a fresh physical recovery/DFU boot and reached
desktop readiness. Desktop replay was tested repeatedly on the earlier live
boot. Independent typed code review could not run because the configured
reviewer model was unavailable to this account. The new diagnostic build
artifacts have not been published.

## 27 October build baseline

- Restored the stopped Lima builder and replaced its missing temporary share
  with the durable project directory.
- Verified the extracted Hoolock source against the pinned commit's full Git
  tree hash, `2e8bddd86192e520b13704583cb04264ee3d4cca`.
- Compiled the 4 KB kernel and modules successfully with native GCC 13.3.
  This build uses release `7.0.12-j72baseline` and omits debug information.
  Compiler-dependent config differences are saved in `config.diff`.
- Confirmed the arm64 image's header declares 4 KB pages and the generated J72
  DTB is byte-identical to the known-good DTB.
- Confirmed the assembled initramfs contains `/init`, 353 newly built compressed
  modules, and `evdev` under the new kernel release. The evdev vermagic matches
  `7.0.12-j72baseline`.
- Verified all exported artifact checksums and rechecked the original boot
  manifest. The original boot payload remains unchanged.
- The build recipe passed Bash syntax and ShellCheck checks. Wrong-host and
  wrong-source checks refused execution before compilation.
- GCC reported one upstream Btrfs `discard_state` warning. The baseline preserves
  that source. Independent code review remains unavailable because the
  configured `gpt-5.5` reviewer model is unsupported by this account.

Artifacts and `validation.json` are saved locally in
`source/build/j72-baseline-20261027/`. The new kernel was unbooted at the end of
27 October. The 28 October results below record its first hardware boot.
Neither the static A7 firmware inspection nor this compilation validates a live
SPI or touch driver. See [BUILD-J72.md](BUILD-J72.md) for the recipe and probe gate.

## 27 October live input baseline

- `run.sh` entered recovery, detected manual DFU entry, completed gaster's four
  stages, sent the cached boot chain, and reached `J72 desktop ready`.
- The serial shell reported the expected J72 model and original
  `7.0.12-gdfa4d4208131-dirty` kernel. This boot used the existing payload.
- The SHA-verified published 4 KB evdev module was uploaded into iPad RAM. Its
  device-side SHA-256 matched the host copy before loading.
- Loading that module registered `/dev/input/event0` for `gpio-keys`. Kernel
  taint remained zero. The SPI device bus remained empty, and no touchscreen
  input device registered.
- Direct process checks found seatd, labwc, the QuickShell D-Bus session, and
  foot running. Omarchy shell IPC returned `ok` after the module load.

The physical Volume Up test then passed. The capture contains 96 bytes: an
`EV_KEY` event for `KEY_VOLUMEUP` code 115 with value 1, `SYN_REPORT`, the same key
with value 0, and another `SYN_REPORT`. The four-event reader completed and
exited. This verifies the physical button press and release through GPIO and
evdev. It does not validate the touchscreen. The decoded evidence is saved
locally in `.omx/work/j72-touch/button-events.json`.

## Touch prerequisites and experimental probe preparation

- Read the live Apple device tree through a read-only MTD node backed by
  bootloader-reserved RAM. The host and device hashes match.
- Recovered 1,024 bytes of nonzero per-device calibration privately.
- Verified the A7 clock-ID lookup and name table against iOS code. SPI2's
  bootloader-reported input rate is 60 MHz; the touch PWM target is channel 2
  at 32,768 Hz. Neither frequency has been measured electrically.
- Verified the downloaded 16G77 OS image against its BuildManifest SHA-1 and
  extracted the matching J72 constructed Z2 firmware resource. Header and
  payload checksum checks passed.
- The kernelcache inspector passes nine unit tests and parses the actual input
  into 15 segments, 179 bundle identifiers and one J72 personality. Independent
  review passed after correcting XML key references, scalar structure, identifier
  types and section ownership.
- Compiled the status-only module and experimental J72 DTB against
  `7.0.12-j72baseline`. Module vermagic matches that release. The descriptor's
  SPI2 resource and power-domain phandle match the intended nodes.
- Kernel `checkpatch.pl` reports zero errors and zero warnings for the module.
  Independent source review passes the explicitly limited experiment contract.
- Reconstructed the original known-good m1n1 payload byte for byte before using
  the same assembly recipe with the new baseline and inspection DTB. Original
  boot artifacts remain unchanged.

The experimental payload and receipt are under `source/build/j72-spi-status/`.
The original desktop was stopped cleanly and the iPad returned to recovery.
At the end of 27 October the boot process was waiting for manual DFU entry.
No touch power, reset, PWM or SPI transfer had been attempted.

## 28 October diagnostic boot and SPI status

- The user ran the experimental boot command and entered physical DFU. The Mac
  completed gaster's four stages and sent the cached iBoot chain and payload.
- USB serial verified `Apple iPad Air (Cellular)`, `apple,j72` and
  `7.0.12-j72baseline`. Initial kernel taint was zero. The captured boot log had
  no panic, oops or unhandled exception. Existing PMIC cell-count warnings and
  the failed legacy `g_multi` configuration remain in the log; the configured
  ACM and NCM gadget subsequently started.
- The initial module passed its device-side hash check but returned `-EINVAL`.
  The helper compared `of_node_full_name()` with an absolute path. In this
  kernel, flattened nodes store a basename in that member. It therefore refused
  before platform-device lookup, resource reservation or domain attachment.
- One BusyBox `insmod` command invoked the failed initialization twice through
  its `finit_module` and `init_module` fallback. Neither reached the status read.
  The failed external module load set the expected out-of-tree taint, `4096`.
- The corrected helper resolves the exact absolute path and compares node
  identity, with balanced lookup references. Five host test groups cover 23
  fixtures, including valid basenames, wrong identities and malformed metadata.
  All 14 repository tests passed. The rebuilt module matches the original
  baseline module's vermagic and passes checkpatch with zero errors or warnings.
- A static aarch64 loader makes one `finit_module` syscall without fallback.
  Seven stubbed cases covered success, four syscall errors and refusals before
  the syscall for a missing argument or file. Independent source
  review found no blocker for the corrected bounded experiment on the still-live
  kernel, subject to fresh identity, taint and idle-state checks.
- Both corrected artifacts passed device-side SHA-256 checks. One loader call
  returned zero and logged `STATUS=0x00000000 INCONCLUSIVE` and
  `cleanup=0 detached=1`. The serial shell remained responsive. Taint stayed
  `4096`, the inspection node was unbound and genpd reported SPI2 and PWM0
  `off-0`, with SIO_P `on`.

The result establishes a bootable diagnostic kernel and completion of one
bounded status-read operation. Zero status does not prove a responding SPI
controller or valid transfers. Reported domain state and detach bookkeeping do
not prove physical power-off. No SPI controller configuration, touch-rail write,
reset, PWM setup, transfer or touch firmware upload occurred.

Corrected module, loader and hashes are in
`source/build/j72-spi-status-fix-20261028/`. Captures, regression evidence,
build logs and independent review are in `.omx/work/j72-touch-20261028/`.
The iPad remains in the diagnostic Linux shell. Desktop replay on this kernel
and native touch remain unverified. The next gate is an A7-specific comparison
of controller status, clocks and power behavior with matching iOS code.

## 28 October disabled-controller divider readback

The matching A7 driver selects SPI version 1, requests `nclk`, calculates a
divider from the requested period and writes the divider before CFG and CLKCFG.
The live ADT reports SPI version 1 and a 60 MHz input. This is source and
bootloader metadata evidence, not a measured physical rate.

An isolated module retained the reviewed status probe's preflight and PM
lifetime. The initial version required all four initial registers to be zero.
Its first run observed CLKCFG `0`, CFG `8`, STATUS `0` and DIV `1`. It refused
with `-EBUSY` before any DIV write, reported successful cleanup and left no
divider module, driver binding or PM attachment.

The A7 source permits divider `1` for version 1 and includes bit 3 in its CFG
construction. A separate architecture review accepted a revision restricted
to exactly the observed four values. The source, tests and rebuilt binary then
passed independent code review. All 29 repository tests passed, including
unexpected-state refusals, access ordering, readback mismatches and cleanup
failures. Checkpatch reported zero errors and zero warnings. Both source
snapshots and build receipts remain separate.

After fresh device checks and device-side module and loader SHA-256 checks,
one single-syscall load completed. Its log records DIV readback `120` followed
by restored readback `1`, `operation=0` and `cleanup=0 detached=1`. The shell
remained responsive. Taint stayed `4096`, the inspection node was unbound and
runtime status was `unsupported`. Genpd reported SPI2 and PWM0 `off-0`, with
SIO_P `on`. The successful status and divider modules remain loaded in RAM.

The test made exactly two SPI register writes, both to DIV `0x30`. It made no
controller-enable, CFG, STATUS, PIN, count or FIFO write, GPIO/reset operation,
PWM change, touch-rail change, firmware upload or transfer. Register readback
and restoration do not prove physical clock rate, power-off or touch support.

Revised artifacts are in `source/build/j72-spi-divider-r2-20261028/`. Source
evidence, review reports, both hardware captures and structured results are in
`.omx/work/j72-spi-next-20261028/`. The next gate is an A7-specific controller
enable and transfer design with separate board-level power/reset/clock evidence
before slave communication.

## 28 October clock-control enable and disable observation

The matching A7 driver caches CLKCFG `0xd` for normal programming and writes
zero during setup and before provider shutdown. Its normal transfer setup also
programs IDLE, STATUS and CFG. A separately reviewed isolated test preserved the
observed CFG `8` and made no transfer request or configuration write.

The new `j72_spi_enable` module copied the measured probe's validation and PM
lifetime. It required the exact initial `0/8/0/1` state, proved DIV `120` before
an enable write, attempted CLKCFG stop after every returning enable read and
restored DIV `1` only after stop readback was zero. All 44 repository tests
passed, including refusals, access order, enable/disable mismatches, incomplete
restoration and PM cleanup errors. The matching module passed checkpatch with
zero errors or warnings. Independent design and source/build review passed the
bounded experiment. Measured status and divider sources remained unchanged.

After fresh identity, taint, ownership and device-side SHA-256 checks, one load
observed DIV `120`, enabled CLKCFG `1`, disabled CLKCFG `0` and restored DIV `1`.
The strict expected enabled word was `0xd`, so the module returned `-EIO` and
the single-call loader exited `1`. Cleanup reported zero and detachment. Linux
stayed responsive with taint `4096`. The node was unbound, runtime status was
`unsupported`, SPI2/PWM0 were reported `off-0` and SIO_P was `on`. The failed
enable module was not retained. No retry followed the mismatch.

This records a changed control readback and successful stop/divider restoration,
not a passing full-word enable check. No physical clock or pins were measured.
The newer Linux Apple SPI driver labels bits `0x4` and `0x8` as FIFO-reset
controls and `0x1` as RUN. That suggests an interpretation of the A7 readback,
but the A7 bit semantics still need verification. No FIFO data register, CFG,
STATUS, PIN or count was written, and no touch GPIO/reset, PWM, rail or firmware
operation occurred. The CTRL command itself may include FIFO-reset effects.

Artifacts are in `source/build/j72-spi-enable-20261028/`. Captures, structured
result, A7 instruction evidence, newer-driver reference and review reports are
in `.omx/work/j72-spi-enable-20261028/`. See [probe/ENABLE.md](probe/ENABLE.md).
The next gate is interpretation of the A7 control bits before any revised
acceptance check or further hardware action.
