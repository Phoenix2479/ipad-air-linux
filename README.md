# iPad Air J72 Linux desktop experiment

This bundle reproduces the tethered session verified on the connected iPad Air
Cellular, `iPad4,2` / J72 / A1475, with an Apple A7 processor and iOS 12.5.8.

Linux 7.0.12 boots through Hoolock's iBoot/m1n1 path. Omarchy A's published Arch
Linux ARM userspace runs from `rootfs.img` on the Mac over USB NBD. The root image
is exported read-only. Runtime configuration, theme state, and logs on the iPad
are held in RAM. No Linux installation or partition change was performed on the
iPad.

Verified on the iPad display:

- `labwc` with Pixman software rendering at 1536 x 2048.
- `foot` terminal.
- Omarchy A QuickShell wallpaper, top panel, and menu.
- Menu activation through the shell's USB/Wayland IPC path.

Native touch is not working. Wi-Fi, Bluetooth, audio, internal storage, and
battery/charging services have not been enabled. This is a temporary root-user
bench session. It depends on the Mac and its USB cable; configuration and app
state in RAM disappear when the session ends.

## Start after an iPad restart

Keep only the target iPad connected to the Mac mini's USB-A port, then run:

```sh
cd ~/iPad-Air-Linux
./run.sh
```

The script verifies the device and boot payload, enters recovery when starting
from normal iOS, and waits for physical DFU entry. For the manual A7 sequence,
hold Top alone for 3 seconds, hold Top + Home for 8 seconds, then release Top
while continuing to hold Home until DFU is detected. A successful DFU screen is
black. The boot tool then sends the cached RAM boot images and starts the desktop.

The complete `run.sh` recovery, DFU, boot, and desktop sequence was verified on
the real iPad on 27 October 2026. The packaged desktop start/stop commands have
also been tested. Close `screen` or other serial clients before using them.

## Restart or stop only the desktop

If Linux is already running:

```sh
~/iPad-Air-Linux/start-desktop.sh
```

To close the temporary desktop, release its mounts, and stop the Mac's NBD server:

```sh
~/iPad-Air-Linux/stop-desktop.sh
```

Stopping closes applications in this temporary chroot. It leaves the iPad in its
Linux RAM session. To return to iOS, hold Top + Home until the Apple logo appears,
then release both. Keep the Mac and cable available until the desktop stop
command finishes; the desktop's executable files are being read from the Mac.

## Files and provenance

- `boot/` contains the successful J72 m1n1 payload, monitor stub, device tree,
  command line, and SHA-256 manifest.
- `source/remote_boot/` is Hoolock's booter at
  `e254a58b7d7c68c9e34a407a0beb0aab4861a6b6`, with its pinned dependencies and J72
  boot cache.
- The kernel, loader, and J72 DTB came from Pomme v0.1.0's 4 KB-page artifacts,
  built from Hoolock Linux `dfa4d42081312dd34ec33ee8d23174309f683188`.
- `source/rootfs.tar.zst` and `source/rootfs.json` came from OrchardLinux's
  Omarchy A v0.1.0 release. Its embedded receipt calls the build
  `preview-40208233937`, profile `j71b`. The archive SHA-256 is
  `8ff6897909d2fa3d894f7c87264e223000649f6be513414b9a0fef307adf3e46`.
- `rootfs.img` is the extracted userspace image. `rootfs.sha256` records its
  prepared contents. It remains read-only during the tethered session.
- `evidence/j72-final-menu.jpg` is the captured Wayland frame at 2x tablet scale.
- `logs/` contains the replay launcher's host and USB serial logs.

The Mac currently has the required Python, libimobiledevice, libirecovery, ipsw,
GNU make, and compiled boot tools. Rosetta was installed for the earlier
checkra1n tests; the successful iBoot route uses native host tools.

Upstream references:

- https://github.com/HoolockLinux/docs/blob/master/tutorials/SETUP_iBoot.md
- https://github.com/HoolockLinux/remote_boot
- https://github.com/OrchardLinux/omarchy-a
- https://github.com/HoolockLinux/docs/blob/master/features/A7.md

## Next hardware milestone

The booted kernel has `apple_z2` enabled, but its J72 device tree contains no SPI
touch device and that driver only matches Mac Touch Bar devices. Apple's J72
firmware tree places multitouch on SPI2 with GPIO 79 chip select, GPIO 15 reset,
GPIO 111 interrupt, GPIO 119 display sync, PMU power controls, and a PWM clock.
Those functions, firmware loading, and per-device calibration need an A7 port
before this can become a touch-operated tablet.
