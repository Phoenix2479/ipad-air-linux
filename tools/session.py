#!/usr/bin/env python3
"""Start or stop the temporary J72 desktop through its USB serial shell."""

import argparse
import base64
import glob
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import termios
import time
import tty
import uuid


PROJECT = Path(__file__).resolve().parents[1]
STATE = PROJECT / '.host-session.json'
LOGS = PROJECT / 'logs'
# The original desktop kernel and the touch diagnostic baseline.
RELEASES = ('7.0.12-gdfa4d4208131-dirty', '7.0.12-j72baseline')


class SerialShell:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(self.fd)
        attrs = termios.tcgetattr(self.fd)
        attrs[2] |= termios.CLOCAL | termios.CREAD
        attrs[2] &= ~termios.HUPCL
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        self.pending = b''

    def close(self):
        os.close(self.fd)

    def run(self, command, timeout=30):
        marker = '__J72_' + uuid.uuid4().hex + '__'
        text = command + "\nprintf '\\n" + marker + ":%s\\n' \"$?\"\n"
        outgoing = text.encode()
        while outgoing:
            select.select([], [self.fd], [], 5)
            outgoing = outgoing[os.write(self.fd, outgoing):]
        pattern = re.compile(rb'\r?\n' + marker.encode() + rb':(\d+)\r?\n')
        data = self.pending
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            match = pattern.search(data)
            if match:
                self.pending = data[match.end():]
                output = data[:match.start()].decode(errors='replace')
                with (LOGS / 'serial.log').open('a') as log:
                    log.write(output + '\n')
                if int(match.group(1)):
                    raise RuntimeError(output[-4000:])
                return output
            ready, _, _ = select.select([self.fd], [], [], 1)
            if ready:
                chunk = os.read(self.fd, 65536)
                if not chunk:
                    raise RuntimeError('USB serial device disconnected')
                data += chunk
        with (LOGS / 'serial.log').open('ab') as log:
            log.write(data)
        raise TimeoutError('Timed out waiting for the iPad shell; see logs/serial.log')

    def upload_runtime(self):
        encoded = base64.b64encode((PROJECT / 'tools/desktop-init.sh').read_bytes()).decode()
        chunks = '\n'.join(encoded[i:i + 512] for i in range(0, len(encoded), 512))
        self.run("cat > /tmp/j72-desktop.b64 <<'J72_RUNTIME'\n" + chunks +
                 "\nJ72_RUNTIME\nbusybox base64 -d /tmp/j72-desktop.b64 > /tmp/j72-desktop.sh")


def serial_path(wait=0):
    deadline = time.monotonic() + wait
    while True:
        paths = glob.glob('/dev/cu.usbmodempomme*')
        if len(paths) == 1:
            return paths[0]
        if len(paths) > 1:
            raise RuntimeError('More than one Linux iDevice is connected')
        if time.monotonic() >= deadline:
            raise RuntimeError('No J72 Linux USB serial device is connected')
        time.sleep(1)


def usb_address():
    ports = subprocess.check_output(['networksetup', '-listallhardwareports'], text=True)
    match = re.search(r'Hardware Port: pomme USB bring-up gadget\nDevice: (\S+)', ports)
    if not match:
        raise RuntimeError('The Linux USB network interface was not found')
    interface = match.group(1)
    for _ in range(30):
        result = subprocess.run(['ipconfig', 'getifaddr', interface], text=True, capture_output=True)
        address = result.stdout.strip()
        if address:
            host = ipaddress.IPv4Address(address)
            if host.is_link_local:
                parts = address.split('.')
                last = int(parts[-1])
                parts[-1] = str(last + 1 if last < 254 else last - 1)
                return interface, address, '.'.join(parts), 16
            if address == '10.0.0.1':
                return interface, address, '10.0.0.2', 24
            raise RuntimeError(f'Unexpected USB IPv4 address {address}')
        time.sleep(1)
    raise RuntimeError('The Mac has not assigned a link-local USB address yet')


def server_alive(pid):
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], text=True, capture_output=True)
    return (str(PROJECT / 'tools/nbd_image.py') in result.stdout
            and str(PROJECT / 'rootfs.img') in result.stdout)


def start():
    image = PROJECT / 'rootfs.img'
    if not image.is_file() or image.stat().st_size != 5 * 1024 ** 3:
        raise RuntimeError('Expected the prepared 5 GiB rootfs.img')
    path = serial_path(wait=90)
    shell = SerialShell(path)
    try:
        identity = shell.run('cat /proc/device-tree/model; uname -r')
        if ('Apple iPad Air (Cellular)' not in identity or
                not any(release in identity for release in RELEASES)):
            raise RuntimeError('The connected Linux system does not match this J72 bundle')
        interface, host, device, prefix = usb_address()
        shell.run(f'ip addr replace {device}/{prefix} dev usb0')
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        pid = state.get('nbd_pid', 0)
        if not pid or not server_alive(pid):
            log = (LOGS / 'nbd.log').open('ab')
            server = subprocess.Popen(
                [sys.executable, str(PROJECT / 'tools/nbd_image.py'), str(image),
                 '--bind', host, '--port', '10810'],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            log.close()
            time.sleep(0.5)
            if server.poll() is not None:
                raise RuntimeError('NBD server failed; inspect logs/nbd.log')
            pid = server.pid
        STATE.write_text(json.dumps({'nbd_pid': pid, 'interface': interface,
                                     'host': host, 'device': device}, indent=2) + '\n')
        shell.upload_runtime()
        print('Starting the J72 desktop; runtime logs stay in iPad RAM.', flush=True)
        output = shell.run(f'sh /tmp/j72-desktop.sh start {host} 10810', timeout=240)
        if 'J72_DESKTOP_READY' not in output:
            raise RuntimeError('Desktop did not report readiness')
        print('J72 desktop ready. The Mac root image is exported read-only.', flush=True)
    finally:
        shell.close()


def stop():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    paths = glob.glob('/dev/cu.usbmodempomme*')
    if paths:
        shell = SerialShell(serial_path())
        try:
            shell.upload_runtime()
            output = shell.run('sh /tmp/j72-desktop.sh stop', timeout=60)
            if 'J72_DESKTOP_STOPPED' not in output:
                raise RuntimeError('Device cleanup did not complete; NBD server kept running')
        finally:
            shell.close()
    pid = state.get('nbd_pid', 0)
    if pid and server_alive(pid):
        os.kill(pid, signal.SIGTERM)
    STATE.unlink(missing_ok=True)
    print('Desktop stopped. The iPad remains in its Linux RAM session.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop'])
    args = parser.parse_args()
    LOGS.mkdir(exist_ok=True)
    try:
        {'start': start, 'stop': stop}[args.action]()
    except (OSError, RuntimeError, TimeoutError) as error:
        print(f'Error: {error}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
