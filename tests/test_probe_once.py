"""Run the runner's pre-load checks against fake serial transcripts."""

import contextlib
import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import probe_once  # noqa: E402


GENERIC = ('KERNEL=7.0.12-j72baseline\nCOMPAT=apple,j72 apple,s5l8960x \n'
           'TAINT=4096\nLOADED=no\n')
PMU = ('PMIC=0-003c driver=simple-mfd-i2c\nRTC=10:21:07\nI2C_OPEN=0\n'
       'I2C_DMESG\n[    1.2] i2c 0-003c: registered\n')
SPI = ('SPI_DEV=yes\nSPI_DRIVER=\n'
       'SPI2_GENPD=spi2                            off-0                           0\n'
       'CTRL_DRIVER=no\nSPI_RPM=unsupported\nSPI_ATTACHED=0\nSPI_IOMEM=0\n'
       'SPI_MASTERS=\nSPI_DEVICES=\n')
TOUCH = ('PWM0_GENPD=pwm0                            off-0                           0\n'
         'PWM0_LISTED=0\n'
         'SIO_P_GENPD=sio_p                           on                              0\n'
         'GPIO_GENPD=gpio                            on                              0\n'
         'PWM_IOMEM=0\nSNAP_DEV=no\n' + PMU)
TOUCH_POWER = ('PWM0_GENPD=pwm0                            off-0                           0\n'
               'PWM0_LISTED=0\n'
               'SIO_P_GENPD=sio_p                           on                              0\n'
               'GPIO_GENPD=gpio                            on                              0\n'
               'PWM_IOMEM=0\nPOWER_DEV=no\nSPI_DEVICES=\n'
               'PINMUX_UNCLAIMED=4\nGPIO_LINES=0\n' + PMU)


class FakeShell:
    def __init__(self, transcript):
        self.transcript = transcript
        self.commands = []

    def run(self, command, timeout=None):
        self.commands.append(command)
        return self.transcript


def check(name, transcript):
    shell = FakeShell(transcript)
    with contextlib.redirect_stdout(io.StringIO()):
        _, problems = probe_once.check(shell, name)
    return problems, shell.commands


class ProbeOnceCheckTests(unittest.TestCase):
    def test_clean_pmu_transcript_passes(self):
        problems, commands = check('j72_pmu_read', GENERIC + PMU)
        self.assertEqual(problems, [])
        self.assertIn('mount -t debugfs none /sys/kernel/debug', commands[0])
        self.assertIn('test -e /sys/module/j72_pmu_read', commands[0])
        self.assertIn('busybox readlink $d/driver', commands[0])

    def test_echoed_pmic_text_does_not_count_as_a_pmic_line(self):
        transcript = (GENERIC + PMU +
                      "PMIC=%s driver=%s\\n' \"${d##*/}\" \"${l##*/}\"\n")
        problems, _ = check('j72_pmu_read', transcript)
        self.assertEqual(problems, [])

    def test_pmu_failures_are_reported_in_order(self):
        transcript = (GENERIC.replace('LOADED=no', 'LOADED=yes') +
                      'PMIC=0-003c driver=\nRTC=failed\nI2C_OPEN=1\n'
                      'I2C_DMESG\ni2c i2c-0: transfer timed out\n')
        problems, _ = check('j72_pmu_read', transcript)
        self.assertEqual(problems, [
            'j72_pmu_read is already loaded',
            'expected exactly one PMIC bound to simple-mfd-i2c',
            'PMIC RTC read failed', 'a process has /dev/i2c-* open',
            'kernel log reports I2C errors'])

    def test_clean_spi_ctrl_transcript_passes(self):
        problems, commands = check('j72_spi_ctrl', GENERIC + SPI)
        self.assertEqual(problems, [])
        command = commands[0]
        self.assertIn('test -e /sys/module/j72_spi_ctrl', command)
        self.assertIn('busybox readlink $d/driver', command)
        self.assertIn('${l##*/}', command)
        self.assertNotIn('basename', command)
        self.assertNotIn('i2c', command)

    def test_spi_ctrl_refuses_each_unsafe_state(self):
        for old, new, problem in (
                ('SPI_DEV=yes', 'SPI_DEV=no',
                 'platform device 20a088000.spi-status is missing'),
                ('SPI_DRIVER=\n', 'SPI_DRIVER=j72-spi-ctrl\n',
                 '20a088000.spi-status already has a driver'),
                ('SPI_DRIVER=\n', '', '20a088000.spi-status already has a driver'),
                ('off-0', 'on', 'genpd summary does not show spi2 off'),
                (SPI.splitlines()[2], 'SPI2_GENPD=missing',
                 'genpd summary does not show spi2 off'),
                ('CTRL_DRIVER=no', 'CTRL_DRIVER=yes',
                 'j72-spi-ctrl driver is already registered'),
                ('SPI_RPM=unsupported', 'SPI_RPM=active',
                 '20a088000.spi-status runtime PM is not "unsupported"'),
                ('SPI_ATTACHED=0', 'SPI_ATTACHED=1',
                 '20a088000.spi-status is still attached to a genpd'),
                ('SPI_IOMEM=0', 'SPI_IOMEM=1',
                 'a region at 0x20a08xxxx is already claimed'),
                ('SPI_MASTERS=\n', 'SPI_MASTERS=spi0\n',
                 'an SPI controller or device already exists'),
                ('SPI_DEVICES=\n', 'SPI_DEVICES=spi0.0\n',
                 'an SPI controller or device already exists')):
            with self.subTest(new=new):
                problems, _ = check('j72_spi_ctrl', GENERIC + SPI.replace(old, new))
                self.assertEqual(problems, [problem])

    def test_generic_checks_apply_to_both_modules(self):
        transcript = GENERIC.replace('7.0.12-j72baseline', '7.0.12').replace(
            'apple,j72 ', 'apple,j71 ')
        for name, extra in (('j72_pmu_read', PMU), ('j72_spi_ctrl', SPI),
                            ('j72_touch_snap', TOUCH),
                            ('j72_touch_power', TOUCH_POWER)):
            with self.subTest(name=name):
                problems, _ = check(name, transcript + extra)
                self.assertEqual(problems, ['kernel release is not 7.0.12-j72baseline',
                                            'board is not apple,j72'])

    def test_clean_touch_snap_transcript_passes(self):
        problems, commands = check('j72_touch_snap', GENERIC + TOUCH)
        self.assertEqual(problems, [])
        command = commands[0]
        self.assertIn('test -e /sys/module/j72_touch_snap', command)
        self.assertIn('test -e /sys/bus/platform/devices/j72_touch_snap', command)
        self.assertIn('busybox readlink $d/driver', command)
        self.assertIn('${l##*/}', command)
        self.assertNotIn('basename', command)

    def test_touch_snap_refuses_each_unsafe_state(self):
        for old, new, problem in (
                ('off-0', 'on', 'genpd summary does not show pwm0 off'),
                (TOUCH.splitlines()[0], 'PWM0_GENPD=missing',
                 'genpd summary does not show pwm0 off'),
                ('PWM0_LISTED=0', 'PWM0_LISTED=1', 'genpd lists a device or child under pwm0'),
                ('sio_p                           on', 'sio_p                           off-0',
                 'genpd summary does not show sio_p on'),
                ('gpio                            on', 'gpio                            off-0',
                 'genpd summary does not show gpio on'),
                ('PWM_IOMEM=0', 'PWM_IOMEM=1', 'a region at 0x20a118xxx is already claimed'),
                ('SNAP_DEV=no', 'SNAP_DEV=yes', 'platform device j72_touch_snap already exists'),
                ('SNAP_DEV=no\n', '', 'platform device j72_touch_snap already exists'),
                ('driver=simple-mfd-i2c', 'driver=',
                 'expected exactly one PMIC bound to simple-mfd-i2c')):
            with self.subTest(new=new):
                problems, _ = check('j72_touch_snap', GENERIC + TOUCH.replace(old, new))
                self.assertEqual(problems, [problem])

    def test_clean_touch_power_transcript_passes(self):
        problems, commands = check('j72_touch_power', GENERIC + TOUCH_POWER)
        self.assertEqual(problems, [])
        command = commands[0]
        self.assertIn('test -e /sys/module/j72_touch_power', command)
        self.assertIn('test -e /sys/bus/platform/devices/j72_touch_power', command)
        self.assertIn('SPI_DEVICES=', command)
        self.assertIn('PINMUX_UNCLAIMED=', command)
        self.assertIn('GPIO_LINES=', command)
        self.assertIn('busybox readlink $d/driver', command)
        self.assertIn('${l##*/}', command)
        self.assertNotIn('basename', command)

    def test_touch_power_refuses_each_unsafe_state(self):
        for old, new, problem in (
                ('off-0', 'on', 'genpd summary does not show pwm0 off'),
                (TOUCH_POWER.splitlines()[0], 'PWM0_GENPD=missing',
                 'genpd summary does not show pwm0 off'),
                ('PWM0_LISTED=0', 'PWM0_LISTED=1',
                 'genpd lists a device or child under pwm0'),
                ('sio_p                           on', 'sio_p                           off-0',
                 'genpd summary does not show sio_p on'),
                ('gpio                            on', 'gpio                            off-0',
                 'genpd summary does not show gpio on'),
                ('PWM_IOMEM=0', 'PWM_IOMEM=1',
                 'a region at 0x20a118xxx is already claimed'),
                ('POWER_DEV=no', 'POWER_DEV=yes',
                 'platform device j72_touch_power already exists'),
                ('POWER_DEV=no', 'POWER_DEV=',
                 'platform device j72_touch_power already exists'),
                ('SPI_DEVICES=', 'SPI_DEVICES=spi2.0',
                 'an SPI device already exists'),
                ('PINMUX_UNCLAIMED=4', 'PINMUX_UNCLAIMED=3',
                 'pins 15/79/111/119 are not all unclaimed'),
                ('PINMUX_UNCLAIMED=4', 'PINMUX_UNCLAIMED=',
                 'pins 15/79/111/119 are not all unclaimed'),
                ('GPIO_LINES=0', 'GPIO_LINES=1',
                 'a GPIO line for pin 15/79/111/119 is already requested'),
                ('driver=simple-mfd-i2c', 'driver=',
                 'expected exactly one PMIC bound to simple-mfd-i2c')):
            with self.subTest(new=new):
                problems, _ = check('j72_touch_power',
                                    GENERIC + TOUCH_POWER.replace(old, new))
                self.assertEqual(problems, [problem])

    def test_touch_power_evidence_covers_power_regions_pins_rtc_taint_and_errors(self):
        evidence = probe_once.EVIDENCE['j72_touch_power']
        for needle in ('cat /sys/kernel/debug/pm_genpd/pm_genpd_summary',
                       "grep -i '20a1' /proc/iomem", '/sys/kernel/debug/gpio',
                       "'^pin (15|79|111|119) '",
                       '/sys/kernel/debug/pinctrl/20e300000.pinctrl/pinmux-pins',
                       '/sys/class/rtc/rtc0/time', '/proc/sys/kernel/tainted',
                       'j72_touch_power', 'pmgr', 'oops', 'SError'):
            self.assertIn(needle, evidence)
        self.assertNotIn('basename', evidence)

    def test_touch_snap_evidence_covers_power_regions_pins_rtc_and_taint(self):
        evidence = probe_once.EVIDENCE['j72_touch_snap']
        for needle in ('cat /sys/kernel/debug/pm_genpd/pm_genpd_summary',
                       "grep -i '20a1' /proc/iomem", '/sys/kernel/debug/gpio',
                       "'^pin (15|79|111|119) '",
                       '/sys/kernel/debug/pinctrl/20e300000.pinctrl/pinmux-pins',
                       '/sys/class/rtc/rtc0/time', '/proc/sys/kernel/tainted'):
            self.assertIn(needle, evidence)
        self.assertNotIn('basename', evidence)

    def test_unknown_module_is_refused_without_a_device_command(self):
        problems, commands = check('j72_spi_enable', GENERIC)
        self.assertEqual(problems, ['no device checks are defined for j72_spi_enable'])
        self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
