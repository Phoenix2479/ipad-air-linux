#!/bin/sh
set -eu

root=/mnt/hostroot
state=/tmp/j72-session
omarchy=/home/tablet/.local/share/omarchy-ipad
mkdir -p "$state"

mounted() { grep -q " $1 " /proc/mounts; }
chroot_run() { busybox chroot "$root" "$@"; }

check_device() {
    [ "$(cat /proc/device-tree/model)" = 'Apple iPad Air (Cellular)' ]
    case "$(uname -r)" in
        7.0.12-gdfa4d4208131-dirty | 7.0.12-j72baseline) ;;
        *) return 1 ;;
    esac
}

running() {
    [ -f "$state/$1.pid" ] || return 1
    pid=$(cat "$state/$1.pid")
    kill -0 "$pid" 2>/dev/null
}

bind_mount() {
    mounted "$2" || mount -o bind "$1" "$2"
}

shell_command() {
    chroot_run /usr/bin/env HOME=/tmp/tablet-live USER=root \
        PATH=/usr/local/bin:/usr/bin LANG=C.UTF-8 \
        XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-0 \
        OMARCHY_PATH="$omarchy" OMARCHY_SHELL_IPC_TIMEOUT=10s \
        /usr/local/bin/omarchy-shell "$@"
}

start_desktop() {
    host=$1
    port=$2
    mkdir -p "$root"
    if ! mounted "$root"; then
        [ "$(cat /sys/block/nbd0/size)" = 0 ] || {
            echo '/dev/nbd0 is already in use; refusing to replace it' >&2
            return 1
        }
        busybox nbd-client -b 4096 "$host" "$port" /dev/nbd0 > "$state/nbd.log" 2>&1 &
        count=0
        while [ "$(cat /sys/block/nbd0/size)" = 0 ]; do
            count=$((count + 1))
            [ "$count" -lt 20 ] || { cat "$state/nbd.log"; return 1; }
            sleep 1
        done
        : > "$state/nbd-owned"
        echo 128 > /sys/block/nbd0/queue/max_sectors_kb
        mount -t ext4 -o ro,noload /dev/nbd0 "$root"
    fi
    [ -x "$root/usr/bin/labwc" ]
    [ -f "$root$omarchy/shell/shell.qml" ]

    mkdir -p /dev/shm
    mounted /dev/shm || mount -t tmpfs -o mode=1777 tmpfs /dev/shm
    [ -e /dev/fd ] || ln -s /proc/self/fd /dev/fd
    [ -e /dev/stdin ] || ln -s /proc/self/fd/0 /dev/stdin
    [ -e /dev/stdout ] || ln -s /proc/self/fd/1 /dev/stdout
    [ -e /dev/stderr ] || ln -s /proc/self/fd/2 /dev/stderr
    bind_mount /dev "$root/dev"
    bind_mount /dev/pts "$root/dev/pts"
    bind_mount /dev/shm "$root/dev/shm"
    bind_mount /proc "$root/proc"
    bind_mount /sys "$root/sys"
    mounted "$root/run" || mount -t tmpfs -o mode=0755 tmpfs "$root/run"
    mounted "$root/tmp" || mount -t tmpfs -o mode=1777 tmpfs "$root/tmp"
    mkdir -p "$root/run/user/0" "$root/tmp/labwc-empty" "$root/tmp/tablet-live"
    chmod 700 "$root/run/user/0"

    if ! running seatd; then
        busybox chroot "$root" /usr/bin/seatd -l info > "$root/tmp/seatd.log" 2>&1 &
        echo $! > "$state/seatd.pid"
    fi
    if ! running labwc; then
        busybox chroot "$root" /usr/bin/env HOME=/tmp LANG=C.UTF-8 \
            XDG_RUNTIME_DIR=/run/user/0 XDG_CONFIG_HOME=/tmp/labwc-empty \
            XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=labwc \
            WLR_BACKENDS=drm WLR_DRM_DEVICES=/dev/dri/card0 WLR_RENDERER=pixman \
            WLR_LIBINPUT_NO_DEVICES=1 LIBSEAT_BACKEND=seatd LABWC_UPDATE_ACTIVATION_ENV=0 \
            /usr/bin/labwc -C /tmp/labwc-empty > "$root/tmp/labwc.log" 2>&1 &
        echo $! > "$state/labwc.pid"
    fi
    count=0
    while [ ! -S "$root/run/user/0/wayland-0" ]; do
        count=$((count + 1))
        [ "$count" -lt 30 ] || { tail -30 "$root/tmp/labwc.log"; return 1; }
        sleep 1
    done
    chroot_run /usr/bin/env PATH=/usr/local/bin:/usr/bin XDG_RUNTIME_DIR=/run/user/0 \
        WAYLAND_DISPLAY=wayland-0 /usr/local/libexec/arch-ipad-scale-output \
        > "$root/tmp/display.log" 2>&1
    if [ ! -f "$root/tmp/tablet-live/.local/state/omarchy/current/theme.name" ]; then
        busybox chroot "$root" /usr/bin/env HOME=/tmp/tablet-live USER=root \
            PATH=/usr/local/bin:/usr/bin LANG=C.UTF-8 OMARCHY_PATH="$omarchy" \
            /usr/local/libexec/arch-ipad-first-theme > "$root/tmp/first-theme.log" 2>&1
    fi
    if ! running shell; then
        busybox chroot "$root" /usr/bin/env HOME=/tmp/tablet-live USER=root \
            PATH=/usr/local/bin:/usr/bin LANG=C.UTF-8 XDG_RUNTIME_DIR=/run/user/0 \
            XDG_CONFIG_HOME=/tmp/tablet-live/.config XDG_CACHE_HOME=/tmp/tablet-live/.cache \
            WAYLAND_DISPLAY=wayland-0 OMARCHY_PATH="$omarchy" QT_QUICK_BACKEND=software \
            QT_QPA_PLATFORM=wayland LIBGL_ALWAYS_SOFTWARE=1 \
            /usr/bin/dbus-run-session -- /usr/bin/qs -p "$omarchy/shell/shell.qml" \
            > "$root/tmp/qs.log" 2>&1 &
        echo $! > "$state/shell.pid"
    fi
    count=0
    until shell_command shell ping > "$state/ping.log" 2>&1; do
        count=$((count + 1))
        [ "$count" -lt 20 ] || { tail -30 "$root/tmp/qs.log"; return 1; }
        sleep 1
    done
    if ! running foot; then
        busybox chroot "$root" /usr/bin/env HOME=/tmp LANG=C.UTF-8 XDG_RUNTIME_DIR=/run/user/0 \
            WAYLAND_DISPLAY=wayland-0 XDG_CONFIG_HOME=/tmp/labwc-empty TERM=xterm-256color \
            /usr/bin/foot > "$root/tmp/foot.log" 2>&1 &
        echo $! > "$state/foot.pid"
    fi
    echo J72_DESKTOP_READY
}

stop_pid() {
    name=$1
    needle=$2
    if running "$name"; then
        pid=$(cat "$state/$name.pid")
        if busybox tr '\000' ' ' 2>/dev/null < "/proc/$pid/cmdline" | grep -q "$needle"; then
            kill "$pid"
            count=0
            while [ -f "/proc/$pid/cmdline" ] && \
                busybox tr '\000' ' ' 2>/dev/null < "/proc/$pid/cmdline" | grep -q "$needle"; do
                count=$((count + 1))
                [ "$count" -lt 10 ] || return 1
                sleep 1
            done
        fi
    fi
    rm -f "$state/$name.pid"
}

stop_remaining() {
    for entry in /proc/[0-9]*; do
        process_root=$(busybox readlink "$entry/root" 2>/dev/null || true)
        if [ "$process_root" = "$root" ]; then
            kill "${entry##*/}" 2>/dev/null || true
        fi
    done
    count=0
    while :; do
        remaining=0
        for entry in /proc/[0-9]*; do
            process_root=$(busybox readlink "$entry/root" 2>/dev/null || true)
            [ "$process_root" != "$root" ] || remaining=1
        done
        [ "$remaining" = 1 ] || return 0
        count=$((count + 1))
        [ "$count" -lt 10 ] || return 1
        sleep 1
    done
}

stop_desktop() {
    if mounted "$root" && [ ! -f "$state/nbd-owned" ]; then
        echo 'The mounted root was not created by this launcher; refusing cleanup' >&2
        return 1
    fi
    if mounted "$root"; then
        chroot_run /usr/bin/env XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-0 \
            /usr/bin/qs kill -p "$omarchy/shell/shell.qml" >/dev/null 2>&1 || true
        sleep 1
        stop_pid shell dbus-run-session
        stop_pid foot foot
        stop_pid labwc labwc
        stop_pid seatd seatd
        stop_remaining
        for suffix in dev/pts dev/shm dev proc sys run tmp; do
            mounted "$root/$suffix" && umount "$root/$suffix"
        done
        umount "$root"
    fi
    if [ -f "$state/nbd-owned" ]; then
        busybox nbd-client -d /dev/nbd0
        rm -f "$state/nbd-owned"
    fi
    echo J72_DESKTOP_STOPPED
}

check_device
case "${1:-}" in
    start) start_desktop "$2" "$3" ;;
    stop) stop_desktop ;;
    *) echo 'usage: desktop-init.sh start HOST PORT | stop' >&2; exit 2 ;;
esac
