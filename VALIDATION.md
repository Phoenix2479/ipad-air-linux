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
`source/build/j72-baseline-20261027/`. The new kernel has not been booted.
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
