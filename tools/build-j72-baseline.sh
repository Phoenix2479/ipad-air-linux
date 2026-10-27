#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 ]]; then
    echo "Usage: $0 KERNEL_SOURCE BUILD_DIRECTORY" >&2
    exit 2
fi
[[ $(uname -s) == Linux && $(uname -m) == aarch64 ]] || {
    echo 'Run this recipe in the aarch64 Linux builder VM.' >&2
    exit 1
}
bundle=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
source_dir=$(cd -- "$1" && pwd -P)
mkdir -p -- "$2"
build_dir=$(cd -- "$2" && pwd -P)
[[ $source_dir != "$build_dir" ]] || { echo 'Use a separate build directory.' >&2; exit 1; }
expected_tree=2e8bddd86192e520b13704583cb04264ee3d4cca
[[ $(git -C "$source_dir" write-tree) == "$expected_tree" ]] || {
    echo 'The source index does not match Hoolock commit dfa4d4208131.' >&2
    exit 1
}
patch_file="$bundle/source/pomme/patches/kernel/0001-backlight-apple_pmic_bl-add-default-case-to-get_brightness.patch"
config_file="$bundle/source/research/config-settled-4k.aarch64"
archive="$bundle/source/build/images-4k-initramfs.cpio.gz"
printf '%s  %s\n' \
    62bacc42af655162b2e9754d09eb598fe617403cbec6ea74ac5d73d4eb2c2a9a "$patch_file" \
    e1279fe355501b3d93dca17b45f130112b72355007dd357f2e45dd51398faad5 "$config_file" \
    a74c1699c9eace242774e27f72072a996665cb5596fde4b3fa9a976b4250dbc2 "$archive" | sha256sum -c -

backlight=drivers/video/backlight/apple_pmic_bl.c
git -C "$source_dir" diff --quiet -- . ":(exclude)$backlight" || {
    echo 'Unexpected kernel source modifications.' >&2
    exit 1
}
expected_file=$(mktemp "$build_dir/backlight.XXXXXX")
git -C "$source_dir" show ":$backlight" > "$expected_file"
patch --silent "$expected_file" "$patch_file"
if ! cmp -s "$expected_file" "$source_dir/$backlight"; then
    git -C "$source_dir" diff --quiet -- "$backlight" || {
        echo 'Unexpected backlight source modifications.' >&2
        exit 1
    }
    git -C "$source_dir" apply "$patch_file"
fi
cmp "$expected_file" "$source_dir/$backlight"
rm -- "$expected_file"

export KBUILD_BUILD_TIMESTAMP='@1781222400'
export KBUILD_BUILD_VERSION=1 KBUILD_BUILD_USER=j72 KBUILD_BUILD_HOST=builder
export LOCALVERSION='' LC_ALL=C
make_kernel() { make -C "$source_dir" O="$build_dir/kernel" ARCH=arm64 "$@"; }
mkdir -p "$build_dir/kernel" "$build_dir/artifacts"
cp "$config_file" "$build_dir/kernel/.config"
"$source_dir/scripts/config" --file "$build_dir/kernel/.config" \
    --set-str LOCALVERSION '-j72baseline' --disable LOCALVERSION_AUTO \
    --disable DEBUG_INFO --disable DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT \
    --disable DEBUG_INFO_DWARF4 --disable DEBUG_INFO_DWARF5 --enable DEBUG_INFO_NONE
make_kernel olddefconfig
grep -qx 'CONFIG_ARM64_4K_PAGES=y' "$build_dir/kernel/.config"
grep -qx 'CONFIG_INPUT_EVDEV=m' "$build_dir/kernel/.config"
cp "$build_dir/kernel/.config" "$build_dir/artifacts/config-bare"
make_kernel -j"${JOBS:-4}" Image apple/s5l8960x-j72.dtb modules
release=$(make_kernel -s kernelrelease | tail -n 1)
[[ $release == 7.0.12-j72baseline ]] || {
    echo "Unexpected kernel release: $release" >&2
    exit 1
}

stage=$(mktemp -d "$build_dir/initramfs.XXXXXX")
(cd "$stage"; gzip -dc "$archive" | cpio -idm --no-absolute-filenames --no-preserve-owner --quiet)
make_kernel INSTALL_MOD_PATH="$stage" INSTALL_MOD_STRIP=1 modules_install
depmod -b "$stage" "$release"
evdev="$stage/lib/modules/$release/kernel/drivers/input/evdev.ko.zst"
[[ $(modinfo -F vermagic "$evdev") == "$release "* ]]
(cd "$stage"; find . -print0 | sort -z | cpio --null -o -H newc -R 0:0 --quiet --reproducible) \
    | gzip -9n > "$build_dir/artifacts/initramfs.cpio.gz"
gzip -t "$build_dir/artifacts/initramfs.cpio.gz"
"$source_dir/scripts/config" --file "$build_dir/kernel/.config" \
    --set-str INITRAMFS_SOURCE "$build_dir/artifacts/initramfs.cpio.gz"
make_kernel olddefconfig
make_kernel -j"${JOBS:-4}" Image
gzip -9nc "$build_dir/kernel/arch/arm64/boot/Image" > "$build_dir/artifacts/Image.initramfs.gz"
cp "$build_dir/kernel/arch/arm64/boot/dts/apple/s5l8960x-j72.dtb" "$build_dir/artifacts/"
cmp "$bundle/boot/s5l8960x-j72.dtb" "$build_dir/artifacts/s5l8960x-j72.dtb"
cp "$build_dir/kernel/.config" "$build_dir/artifacts/config-initramfs"
printf '%s\n' "$release" > "$build_dir/artifacts/kernel.release"
gcc --version > "$build_dir/artifacts/compiler.txt"
git -C "$source_dir" diff --binary > "$build_dir/artifacts/source.patch"
diff -u "$config_file" "$build_dir/artifacts/config-initramfs" > "$build_dir/artifacts/config.diff" || [[ $? == 1 ]]
file "$build_dir/kernel/arch/arm64/boot/Image" | tee "$build_dir/artifacts/image-type.txt"
grep -q '4K pages' "$build_dir/artifacts/image-type.txt"
(cd "$build_dir/artifacts"; sha256sum Image.initramfs.gz initramfs.cpio.gz s5l8960x-j72.dtb config-bare config-initramfs > SHA256SUMS)
echo "J72_BASELINE_BUILT: $build_dir/artifacts (compilation only; not hardware validation)"
