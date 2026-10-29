#!/usr/bin/env python3
"""Check, upload and load one reviewed J72 probe module over USB serial.

Each phase is explicit. `check` only reads device state. `upload` copies the
loader and module to /tmp and verifies their device-side SHA-256. `load`
makes the single finit_module call and only runs when `--load` is given.
"""

import argparse
import base64
import hashlib
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session import PROJECT, SerialShell, serial_path  # noqa: E402

RELEASE = '7.0.12-j72baseline'
LOADER = PROJECT / 'source/build/j72-spi-status-fix-20261028/load-once'
LOADER_SHA = '42d8b563bbeea2b9c482cca6ab9d31dfafa870cf9e4dba4ccd19cba5f6d4379c'
CHUNK = 24576


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def device_sha(shell, remote):
    out = shell.run(f'busybox sha256sum {remote} 2>/dev/null || true')
    match = re.search(r'\b([0-9a-f]{64})\s+' + re.escape(remote) + r'\s*$', out, re.M)
    return match.group(1) if match else ''


PMU_COMMAND = (
    "for d in /sys/bus/i2c/devices/*; do "
    "  c=$(tr '\\000' ' ' < $d/of_node/compatible 2>/dev/null); "
    "  case \"$c\" in *apple,i2c-pmic*) l=$(busybox readlink $d/driver); "
    "    printf 'PMIC=%s driver=%s\\n' \"${d##*/}\" \"${l##*/}\";; esac; "
    "done; printf 'RTC='; cat /sys/class/rtc/rtc0/time 2>/dev/null || echo failed; "
    "printf 'I2C_OPEN='; ls -l /proc/[0-9]*/fd 2>/dev/null | grep -c /dev/i2c-; "
    "echo I2C_DMESG; dmesg | grep -i -E 'i2c|pmic' | tail -n 20")


def pmu_problems(out, fields):
    problems = []
    pmics = [line for line in out.splitlines() if line.startswith('PMIC=')]
    if len(pmics) != 1 or not pmics[0].endswith('driver=simple-mfd-i2c'):
        problems.append('expected exactly one PMIC bound to simple-mfd-i2c')
    if fields.get('RTC', 'failed').strip() in ('', 'failed'):
        problems.append('PMIC RTC read failed')
    if fields.get('I2C_OPEN', '').strip() != '0':
        problems.append('a process has /dev/i2c-* open')
    if re.search(r'(?i)i2c.*(timeout|timed out|error|fail)', out.split('I2C_DMESG', 1)[-1]):
        problems.append('kernel log reports I2C errors')
    return problems


SPI_CTRL_COMMAND = (
    "d=/sys/bus/platform/devices/20a088000.spi-status; "
    "printf 'SPI_DEV='; test -e $d && echo yes || echo no; "
    "l=$(busybox readlink $d/driver 2>/dev/null); printf 'SPI_DRIVER=%s\\n' \"${l##*/}\"; "
    "printf 'SPI2_GENPD='; grep -E '^spi2[[:space:]]' "
    "/sys/kernel/debug/pm_genpd/pm_genpd_summary 2>/dev/null || echo missing; "
    "printf 'CTRL_DRIVER='; test -e /sys/bus/platform/drivers/j72-spi-ctrl "
    "&& echo yes || echo no")


def spi_ctrl_problems(out, fields):
    problems = []
    if fields.get('SPI_DEV', '').strip() != 'yes':
        problems.append('platform device 20a088000.spi-status is missing')
    if fields.get('SPI_DRIVER', 'missing').strip():
        problems.append('20a088000.spi-status already has a driver')
    if not re.fullmatch(r'spi2\s+off\S*(\s.*)?', fields.get('SPI2_GENPD', '').strip()):
        problems.append('genpd summary does not show spi2 off')
    if fields.get('CTRL_DRIVER', '').strip() != 'no':
        problems.append('j72-spi-ctrl driver is already registered')
    return problems


MODULE_CHECKS = {
    'j72_pmu_read': (PMU_COMMAND, pmu_problems),
    'j72_spi_ctrl': (SPI_CTRL_COMMAND, spi_ctrl_problems),
}


def check(shell, name):
    if name not in MODULE_CHECKS:
        return '', [f'no device checks are defined for {name}']
    command, module_problems = MODULE_CHECKS[name]
    out = shell.run(
        "printf 'KERNEL='; uname -r; printf 'COMPAT='; tr '\\000' ' ' "
        "< /proc/device-tree/compatible; echo; printf 'TAINT='; "
        "cat /proc/sys/kernel/tainted; printf 'LOADED='; "
        f"test -e /sys/module/{name} && echo yes || echo no; " + command)
    print(out)
    fields = dict(line.split('=', 1) for line in out.splitlines() if '=' in line
                  and not line.startswith('PMIC='))
    problems = []
    if fields.get('KERNEL', '').strip() != RELEASE:
        problems.append('kernel release is not ' + RELEASE)
    if 'apple,j72' not in fields.get('COMPAT', ''):
        problems.append('board is not apple,j72')
    if fields.get('LOADED', '').strip() != 'no':
        problems.append(f'{name} is already loaded')
    return out, problems + module_problems(out, fields)


def upload(shell, local, remote):
    expected = sha256(local)
    if device_sha(shell, remote) == expected:
        return expected
    encoded = base64.b64encode(local.read_bytes()).decode()
    shell.run(f'rm -f {remote}.b64')
    for start in range(0, len(encoded), CHUNK):
        part = encoded[start:start + CHUNK]
        lines = '\n'.join(part[i:i + 512] for i in range(0, len(part), 512))
        shell.run(f"cat >> {remote}.b64 <<'J72_PART'\n{lines}\nJ72_PART", timeout=60)
    shell.run(f'busybox base64 -d {remote}.b64 > {remote} && rm {remote}.b64 '
              f'&& chmod 0755 {remote}')
    actual = device_sha(shell, remote)
    if actual != expected:
        raise RuntimeError(f'{remote} hash mismatch: device {actual}, host {expected}')
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('module', type=Path)
    parser.add_argument('--sha256', required=True, help='expected host module hash')
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--load', action='store_true', help='make the single load call')
    args = parser.parse_args()

    name = args.module.stem
    if sha256(args.module) != args.sha256:
        sys.exit('host module hash does not match --sha256')
    if sha256(LOADER) != LOADER_SHA:
        sys.exit('host loader hash does not match the reviewed loader')

    shell = SerialShell(serial_path())
    log = args.log.open('a')
    log.write(f'\n== {time.strftime("%Y-%m-%dT%H:%M:%S%z")} {name}\n')
    try:
        out, problems = check(shell, name)
        log.write('CHECK\n' + out + '\n')
        if problems:
            log.write('REFUSED ' + '; '.join(problems) + '\n')
            sys.exit('refused: ' + '; '.join(problems))
        log.write('LOADER ' + upload(shell, LOADER, '/tmp/j72-load-once') + '\n')
        log.write('MODULE ' + upload(shell, args.module, f'/tmp/{name}.ko') + '\n')
        if not args.load:
            print('checks and uploads passed; rerun with --load for the single call')
            return
        out = shell.run(f'/tmp/j72-load-once /tmp/{name}.ko; printf "\\nLOADER_RC=%s\\n" "$?"; '
                        f'dmesg | grep {name}; printf "TAINT="; '
                        'cat /proc/sys/kernel/tainted; echo FULL_DMESG; dmesg',
                        timeout=60)
        log.write('LOAD\n' + out + '\n')
        print(out)
    finally:
        log.close()
        shell.close()


if __name__ == '__main__':
    main()
