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

The original Hoolock cold boot succeeded. The exact packaged `run.sh` wrapper has
been syntax checked and preserves that command, but has not been run through a
second physical DFU boot. Desktop replay was tested repeatedly on the live boot.
Independent typed code review could not run because the configured reviewer
model was unavailable to this account. The artifact has not been published.
