# Build the J72 diagnostic baseline

This recipe compiles the Hoolock source used by the working J72 boot. It uses
the restored aarch64 Lima VM's GCC 13.3 rather than Pomme's x86-hosted GCC 15.3
toolchain. It disables debug information and uses the separate release name
`7.0.12-j72baseline`. It does not reproduce the published kernel byte for byte.

The recipe keeps the J72 device tree unchanged. It adds no SPI controller,
touchscreen, power sequence, firmware loader, or internal-storage operation.
Compilation does not establish that a new image boots or that touch works.

## Prepare the source

The `ipad-j72-builder` Lima VM mounts this project at its absolute host path.
Keep the kernel source and build output on the VM's Linux filesystem, which
preserves case-sensitive filenames. The VM has the compiler, make, bc, bison,
flex, libssl-dev, libelf-dev, kmod, cpio, zstd, and device-tree-compiler installed.

The source pin is Hoolock Linux
`dfa4d42081312dd34ec33ee8d23174309f683188`. The corresponding Git tree is
`2e8bddd86192e520b13704583cb04264ee3d4cca`, recorded by the
[upstream commit API](https://api.github.com/repos/HoolockLinux/linux/git/commits/dfa4d42081312dd34ec33ee8d23174309f683188).

This session downloaded that commit's archive to
`source/build/linux-dfa4d420.tar.gz` and extracted it in the VM to
`/home/arundinakar.guest/j72-linux-tar`. A Git index created with `git init` and
`git add -f -A` produced the exact upstream tree hash. The forced add matters
because the archive contains tracked files that match Linux's ignore patterns.
The archive-derived source has no original Git HEAD; the tree hash verifies its
contents. A normal checkout of the pinned commit also satisfies this check.

The recipe expects these additional, ignored local inputs:

- `source/pomme/`, checked out at v0.1.0, commit
  `8d58827a56880e5679921494511a87c9e385ae47`.
- `source/research/config-settled-4k.aarch64`, the published settled 4 KB config.
- `source/build/images-4k-initramfs.cpio.gz`, downloaded from Pomme v0.1.0.

The script checks the config, the Pomme backlight patch, and the initramfs against
their published SHA-256 values before compiling. It permits only that exact
backlight patch in the indexed kernel source. No other tracked source changes
are accepted by this baseline recipe.

## Compile

Run from the Mac:

```sh
limactl shell ipad-j72-builder -- bash \
  /Users/arundinakar/iPad-Air-Linux/tools/build-j72-baseline.sh \
  /home/arundinakar.guest/j72-linux-tar \
  /home/arundinakar.guest/j72-baseline
```

The script builds the 4 KB kernel, J72 DTB, and modules. It stages those modules,
including `evdev`, in a copy of the published netboot initramfs and rebuilds the
kernel with that archive embedded. It checks the new module release and requires
the generated J72 DTB to be byte-identical to the working DTB.

The `artifacts/` directory contains the compressed kernel, embedded initramfs
archive, DTB, exact configs, config diff, source patch, compiler version, release
name, and checksums. The kernel file is a gzip-compressed raw arm64 Image. It is
not an assembled m1n1 Mach-O payload and is not consumed by `run.sh`.

The build uses a separate directory from `boot/` and the working root image.
Use the existing `run.sh` for the known-good hardware boot.

On 27 October the recipe completed successfully. Its exported checksums, 4 KB
image header, matching DTB, initramfs `/init`, and matching evdev module passed
inspection. The local export is `source/build/j72-baseline-20261027/`.
The compiler emitted one warning about upstream Btrfs `discard_state`.
Independent review could not run with the account's configured reviewer model.
On 28 October this kernel booted on the physical J72 with the separate SPI
inspection DTB. USB serial verified its model and `7.0.12-j72baseline` release.
This establishes a bootable diagnostic baseline. It does not validate desktop
replay on this build or a working touchscreen. See [VALIDATION.md](VALIDATION.md).

## Gate the first controller experiment

The baseline must compile and the generated DTB must match before introducing a
controller change. Independently review the A7 register variant, PMGR dependency,
controller clock, pin state, and the exact operations performed by a probe.
Keep the touch child absent during the controller experiment. A successful
controller probe alone does not validate a touch reset, firmware, or calibration
sequence.
