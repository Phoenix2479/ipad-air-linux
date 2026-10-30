#!/usr/bin/env python3
"""Check, upload and load one reviewed J72 probe module over USB serial.

Each phase is explicit. `check` mounts debugfs if needed and only reads
device state. `upload` copies the loader and module to /tmp and verifies their
device-side SHA-256. `load` makes the single finit_module call and only runs
when `--load` is given.
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
    pmics = [line for line in out.splitlines()
             if re.fullmatch(r'PMIC=\S+ driver=\S+', line.strip())]
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
    "&& echo yes || echo no; "
    "printf 'SPI_RPM=%s\\n' \"$(cat $d/power/runtime_status 2>/dev/null)\"; "
    "printf 'SPI_ATTACHED='; grep -c '20a088000.spi-status' "
    "/sys/kernel/debug/pm_genpd/pm_genpd_summary; "
    "printf 'SPI_IOMEM='; grep -c -i '20a08' /proc/iomem; "
    "printf 'SPI_MASTERS=%s\\n' \"$(ls /sys/class/spi_master/ 2>/dev/null)\"; "
    "printf 'SPI_DEVICES=%s\\n' \"$(ls /sys/bus/spi/devices/ 2>/dev/null)\"")

# Captured before and after the load so the two snapshots can be compared.
SPI_CTRL_EVIDENCE = (
    "d=/sys/bus/platform/devices/20a088000.spi-status; "
    "l=$(busybox readlink $d/driver 2>/dev/null); echo \"EV_DRIVER=${l##*/}\"; "
    "echo \"EV_RPM=$(cat $d/power/runtime_status 2>/dev/null)\"; "
    "echo \"EV_MASTERS=$(ls /sys/class/spi_master/ 2>/dev/null)\"; "
    "for m in /sys/class/spi_master/*; do test -e $m || continue; "
    "o=$(busybox readlink $m/of_node); echo \"EV_OF_NODE=${m##*/} ${o##*/}\"; done; "
    "echo \"EV_SPI_DEVICES=$(ls /sys/bus/spi/devices/ 2>/dev/null)\"; "
    "echo EV_IOMEM; grep -i '20a08' /proc/iomem; "
    "echo EV_GENPD; cat /sys/kernel/debug/pm_genpd/pm_genpd_summary; true")


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
    if fields.get('SPI_RPM', '').strip() != 'unsupported':
        problems.append('20a088000.spi-status runtime PM is not "unsupported"')
    if fields.get('SPI_ATTACHED', '').strip() != '0':
        problems.append('20a088000.spi-status is still attached to a genpd')
    if fields.get('SPI_IOMEM', '').strip() != '0':
        problems.append('a region at 0x20a08xxxx is already claimed')
    if fields.get('SPI_MASTERS', 'x').strip() or fields.get('SPI_DEVICES', 'x').strip():
        problems.append('an SPI controller or device already exists')
    return problems


GENPD = '/sys/kernel/debug/pm_genpd/pm_genpd_summary'

# Under a domain line, genpd indents its attached devices and its children.
TOUCH_SNAP_COMMAND = (
    f"printf 'PWM0_GENPD='; grep -E '^pwm0[[:space:]]' {GENPD} || echo missing; "
    "printf 'PWM0_LISTED='; busybox awk '/^pwm0[[:space:]]/{p=1;next} !/^ /{p=0} "
    f"p{{n++}} END{{print n+0}}' {GENPD}; "
    f"printf 'SIO_P_GENPD='; grep -E '^sio_p[[:space:]]' {GENPD} || echo missing; "
    f"printf 'GPIO_GENPD='; grep -E '^gpio[[:space:]]' {GENPD} || echo missing; "
    "printf 'PWM_IOMEM='; grep -c -i '20a118' /proc/iomem; "
    "printf 'SNAP_DEV='; test -e /sys/bus/platform/devices/j72_touch_snap "
    "&& echo yes || echo no; " + PMU_COMMAND)

TOUCH_SNAP_EVIDENCE = (
    f"echo EV_GENPD; cat {GENPD}; "
    "echo EV_IOMEM; grep -i '20a1' /proc/iomem; "
    "echo EV_GPIO; cat /sys/kernel/debug/gpio 2>/dev/null; "
    "echo EV_PINMUX; grep -E '^pin (15|79|111|119) ' "
    "/sys/kernel/debug/pinctrl/20e300000.pinctrl/pinmux-pins 2>/dev/null; "
    "printf 'EV_RTC='; cat /sys/class/rtc/rtc0/time 2>/dev/null || echo failed; "
    "printf 'EV_TAINT='; cat /proc/sys/kernel/tainted; true")


def touch_snap_problems(out, fields):
    problems = pmu_problems(out, fields)
    if not re.fullmatch(r'pwm0\s+off-\d+(\s.*)?', fields.get('PWM0_GENPD', '').strip()):
        problems.append('genpd summary does not show pwm0 off')
    if fields.get('PWM0_LISTED', '').strip() != '0':
        problems.append('genpd lists a device or child under pwm0')
    if not re.fullmatch(r'sio_p\s+on(\s.*)?', fields.get('SIO_P_GENPD', '').strip()):
        problems.append('genpd summary does not show sio_p on')
    if not re.fullmatch(r'gpio\s+on(\s.*)?', fields.get('GPIO_GENPD', '').strip()):
        problems.append('genpd summary does not show gpio on')
    if fields.get('PWM_IOMEM', '').strip() != '0':
        problems.append('a region at 0x20a118xxx is already claimed')
    if fields.get('SNAP_DEV', '').strip() != 'no':
        problems.append('platform device j72_touch_snap already exists')
    return problems


TOUCH_POWER_COMMAND = (
    f"printf 'PWM0_GENPD='; grep -E '^pwm0[[:space:]]' {GENPD} || echo missing; "
    "printf 'PWM0_LISTED='; busybox awk '/^pwm0[[:space:]]/{p=1;next} !/^ /{p=0} "
    f"p{{n++}} END{{print n+0}}' {GENPD}; "
    f"printf 'SIO_P_GENPD='; grep -E '^sio_p[[:space:]]' {GENPD} || echo missing; "
    f"printf 'GPIO_GENPD='; grep -E '^gpio[[:space:]]' {GENPD} || echo missing; "
    "printf 'PWM_IOMEM='; grep -c -i '20a118' /proc/iomem; "
    "printf 'POWER_DEV='; test -e /sys/bus/platform/devices/j72_touch_power "
    "&& echo yes || echo no; "
    "printf 'SPI_DEVICES=%s\\n' \"$(ls /sys/bus/spi/devices/ 2>/dev/null)\"; "
    "printf 'PINMUX_UNCLAIMED='; grep -c -E '^pin (15|79|111|119) .*: UNCLAIMED$' "
    "/sys/kernel/debug/pinctrl/20e300000.pinctrl/pinmux-pins 2>/dev/null; "
    "printf 'GPIO_LINES='; grep -c -E '^ gpio-(15|79|111|119) ' "
    "/sys/kernel/debug/gpio 2>/dev/null; " + PMU_COMMAND)

TOUCH_POWER_EVIDENCE = (
    f"echo EV_GENPD; cat {GENPD}; "
    "echo EV_IOMEM; grep -i '20a1' /proc/iomem; "
    "echo EV_GPIO; cat /sys/kernel/debug/gpio 2>/dev/null; "
    "echo EV_PINMUX; grep -E '^pin (15|79|111|119) ' "
    "/sys/kernel/debug/pinctrl/20e300000.pinctrl/pinmux-pins 2>/dev/null; "
    "printf 'EV_RTC='; cat /sys/class/rtc/rtc0/time 2>/dev/null || echo failed; "
    "printf 'EV_TAINT='; cat /proc/sys/kernel/tainted; "
    "echo EV_DMESG; dmesg | grep -i -E "
    "'j72_touch_power|pmgr|i2c.*(timeout|timed out|error|fail)|oops|SError' "
    "| tail -n 40; true")


def touch_power_problems(out, fields):
    problems = pmu_problems(out, fields)
    if not re.fullmatch(r'pwm0\s+off-\d+(\s.*)?', fields.get('PWM0_GENPD', '').strip()):
        problems.append('genpd summary does not show pwm0 off')
    if fields.get('PWM0_LISTED', '').strip() != '0':
        problems.append('genpd lists a device or child under pwm0')
    if not re.fullmatch(r'sio_p\s+on(\s.*)?', fields.get('SIO_P_GENPD', '').strip()):
        problems.append('genpd summary does not show sio_p on')
    if not re.fullmatch(r'gpio\s+on(\s.*)?', fields.get('GPIO_GENPD', '').strip()):
        problems.append('genpd summary does not show gpio on')
    if fields.get('PWM_IOMEM', '').strip() != '0':
        problems.append('a region at 0x20a118xxx is already claimed')
    if fields.get('POWER_DEV', '').strip() != 'no':
        problems.append('platform device j72_touch_power already exists')
    if fields.get('SPI_DEVICES', 'x').strip():
        problems.append('an SPI device already exists')
    if fields.get('PINMUX_UNCLAIMED', '').strip() != '4':
        problems.append('pins 15/79/111/119 are not all unclaimed')
    if fields.get('GPIO_LINES', '').strip() != '0':
        problems.append('a GPIO line for pin 15/79/111/119 is already requested')
    return problems


TOUCH_ID_COMMAND = (
    TOUCH_POWER_COMMAND.replace('j72_touch_power', 'j72_touch_id') +
    "; d=/sys/bus/platform/devices/20a088000.spi-status; "
    "l=$(busybox readlink $d/driver 2>/dev/null); printf 'SPI_DRIVER=%s\\n' \"${l##*/}\"; "
    "printf 'SPI_MASTERS=%s\\n' \"$(ls /sys/class/spi_master/ 2>/dev/null)\"; "
    "printf 'SPI2_GENPD='; grep -E '^spi2[[:space:]]' "
    f"{GENPD} 2>/dev/null || echo missing")

TOUCH_ID_EVIDENCE = (
    TOUCH_POWER_EVIDENCE.replace('j72_touch_power', 'j72_touch_id|spi0')
    .replace('; true', '') +
    "; echo \"EV_SPI_DEVICES=$(ls /sys/bus/spi/devices/ 2>/dev/null)\"; true")


def touch_id_problems(out, fields):
    problems = [p.replace('j72_touch_power', 'j72_touch_id')
                for p in touch_power_problems(out, fields)]
    if fields.get('SPI_DRIVER', '').strip() != 'j72-spi-ctrl':
        problems.append('20a088000.spi-status is not bound to j72-spi-ctrl')
    if fields.get('SPI_MASTERS', '').strip() != 'spi0':
        problems.append('expected exactly one SPI controller, spi0')
    if not re.fullmatch(r'spi2\s+on(\s.*)?', fields.get('SPI2_GENPD', '').strip()):
        problems.append('genpd summary does not show spi2 on')
    return problems


TOUCH_FW_COMMAND = TOUCH_ID_COMMAND.replace('j72_touch_id', 'j72_touch_fw')
TOUCH_FW_EVIDENCE = TOUCH_ID_EVIDENCE.replace('j72_touch_id', 'j72_touch_fw')


def touch_fw_problems(out, fields):
    return [p.replace('j72_touch_id', 'j72_touch_fw') for p in touch_id_problems(out, fields)]


TOUCH_COMMAND = TOUCH_ID_COMMAND.replace('j72_touch_id', 'j72_touch')
TOUCH_EVIDENCE = (
    TOUCH_ID_EVIDENCE.replace('j72_touch_id', 'j72_touch').replace('; true', '') +
    "; echo EV_IRQ; grep -E 'j72_touch|Apple-GPIO' /proc/interrupts; "
    "echo EV_INPUT; cat /proc/bus/input/devices; "
    "printf 'EV_STATS='; cat /sys/kernel/debug/j72_touch/stats 2>/dev/null || echo none; true")


def touch_problems(out, fields):
    return [p.replace('j72_touch_id', 'j72_touch') for p in touch_id_problems(out, fields)]


# Unloading is disabled, so a corrected driver is the same source under a new name.
def renamed_touch(name):
    def problems(out, fields):
        return [p.replace('j72_touch', name) for p in touch_problems(out, fields)]
    return TOUCH_COMMAND.replace('j72_touch', name), problems


# Private files go to the device's RAM filesystem only, never into the log.
FIRMWARE_DIR = PROJECT / 'source/build/j72-touch-firmware-20261029'
TOUCH_FILES = (
    (FIRMWARE_DIR / 'apple/j72-n1.bin', '/lib/firmware/apple/j72-n1.bin'),
    (FIRMWARE_DIR / 'apple/j72-n1-cal.bin', '/lib/firmware/apple/j72-n1-cal.bin'),
)
FIRMWARE = {'j72_touch_fw': TOUCH_FILES, 'j72_touch': TOUCH_FILES,
            'j72_touch_d2': TOUCH_FILES}


PIN_DUMP_COMMAND = (
    "printf 'PINCTRL_DRIVER='; l=$(busybox readlink "
    "/sys/bus/platform/devices/20e300000.pinctrl/driver 2>/dev/null); echo \"${l##*/}\"")


def pin_dump_problems(out, fields):
    if not fields.get('PINCTRL_DRIVER', '').strip():
        return ['20e300000.pinctrl has no driver']
    return []


MODULE_CHECKS = {
    'j72_pmu_read': (PMU_COMMAND, pmu_problems),
    'j72_spi_ctrl': (SPI_CTRL_COMMAND, spi_ctrl_problems),
    'j72_touch_snap': (TOUCH_SNAP_COMMAND, touch_snap_problems),
    'j72_touch_power': (TOUCH_POWER_COMMAND, touch_power_problems),
    'j72_touch_id': (TOUCH_ID_COMMAND, touch_id_problems),
    'j72_touch_fw': (TOUCH_FW_COMMAND, touch_fw_problems),
    'j72_touch': (TOUCH_COMMAND, touch_problems),
    'j72_touch_d2': renamed_touch('j72_touch_d2'),
    'j72_pin_dump': (PIN_DUMP_COMMAND, pin_dump_problems),
}

EVIDENCE = {'j72_spi_ctrl': SPI_CTRL_EVIDENCE, 'j72_touch_snap': TOUCH_SNAP_EVIDENCE,
            'j72_touch_power': TOUCH_POWER_EVIDENCE, 'j72_touch_id': TOUCH_ID_EVIDENCE,
            'j72_touch_fw': TOUCH_FW_EVIDENCE, 'j72_touch': TOUCH_EVIDENCE,
            'j72_touch_d2': TOUCH_EVIDENCE.replace('j72_touch', 'j72_touch_d2')}


def check(shell, name):
    if name not in MODULE_CHECKS:
        return '', [f'no device checks are defined for {name}']
    command, module_problems = MODULE_CHECKS[name]
    out = shell.run(
        "mount | grep -q ' /sys/kernel/debug ' || "
        "mount -t debugfs none /sys/kernel/debug; "
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


def upload(shell, local, remote, mode='0755'):
    expected = sha256(local)
    if device_sha(shell, remote) == expected:
        return expected
    encoded = base64.b64encode(local.read_bytes()).decode()
    shell.run(f'rm -f {remote}.b64')
    for start in range(0, len(encoded), CHUNK):
        part = encoded[start:start + CHUNK]
        lines = '\n'.join(part[i:i + 512] for i in range(0, len(part), 512))
        shell.run(f"cat >> {remote}.b64 <<'J72_PART'\n{lines}\nJ72_PART", timeout=60,
                  log=False)
    shell.run(f'busybox base64 -d {remote}.b64 > {remote} && rm {remote}.b64 '
              f'&& chmod {mode} {remote}')
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
        for local, remote in FIRMWARE.get(name, ()):
            shell.run(f'busybox mkdir -p {remote.rsplit("/", 1)[0]}')
            log.write(f'FILE {remote} ' + upload(shell, local, remote, '0600') + '\n')
        log.write('MODULE ' + upload(shell, args.module, f'/tmp/{name}.ko') + '\n')
        if not args.load:
            print('checks and uploads passed; rerun with --load for the single call')
            return
        evidence = EVIDENCE.get(name)
        if evidence:
            log.write('BEFORE\n' + shell.run(evidence) + '\n')
        pattern = name.replace('_', '[-_]')
        out = shell.run(f'/tmp/j72-load-once /tmp/{name}.ko; printf "\\nLOADER_RC=%s\\n" "$?"; '
                        f"dmesg | grep -E '{pattern}|spi-status|spi0|spi_master|power-controller@200a8|pwm0|power-controller@20070|pmgr'; "
                        'printf "TAINT="; cat /proc/sys/kernel/tainted; echo FULL_DMESG; dmesg',
                        timeout=60)
        log.write('LOAD\n' + out + '\n')
        print(out)
        if evidence:
            after = shell.run(evidence)
            log.write('AFTER\n' + after + '\n')
            print(after)
    finally:
        log.close()
        shell.close()


if __name__ == '__main__':
    main()
