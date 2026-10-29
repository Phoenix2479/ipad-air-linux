#!/usr/bin/env python3
"""Package the J72 N1 touch firmware and calibration for request_firmware.

The firmware is the preconstructed Z2 boot packet from J72.mtprops. The
calibration is this unit's 1,024-byte MtCl copy recovered from the live ADT.
Both stay private: the output directory is under the ignored source/ tree and
the manifest records only sizes and hashes.
"""

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import sys

PROFILE = 'C1F15,1'
PREFIX = b'\x18\xe1\x30\x01'
FIRMWARE_SIZE = 57016
CALIBRATION_SIZE = 1024
FIRMWARE_NAME = 'apple/j72-n1.bin'
CALIBRATION_NAME = 'apple/j72-n1-cal.bin'


def halfword32(data):
    return data[2] << 24 | data[3] << 16 | data[0] << 8 | data[1]


def validate_firmware(packet):
    if len(packet) != FIRMWARE_SIZE or packet[:4] != PREFIX:
        raise ValueError('not a prefixed 57,016-byte Z2 boot packet')
    payload = int.from_bytes(packet[4:6], 'big') * 4
    if payload + 16 != len(packet):
        raise ValueError('packet length field mismatch')
    if halfword32(packet[6:10]):
        raise ValueError('unexpected load address')
    if int.from_bytes(packet[10:12], 'big') != sum(packet[4:10]) & 0xffff:
        raise ValueError('header checksum mismatch')
    if halfword32(packet[12 + payload:16 + payload]) != sum(packet[12:12 + payload]) & 0xffffffff:
        raise ValueError('payload checksum mismatch')


def validate_calibration(data):
    if len(data) != CALIBRATION_SIZE:
        raise ValueError(f'calibration is {len(data)} bytes, expected {CALIBRATION_SIZE}')
    if data == bytes(CALIBRATION_SIZE) or data == b'\xff' * CALIBRATION_SIZE:
        raise ValueError('calibration is blank')


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mtprops', type=Path)
    parser.add_argument('calibration', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()

    profile = plistlib.loads(args.mtprops.read_bytes()).get(PROFILE)
    if not isinstance(profile, dict) or profile.get('PreconstructedBootloadPacketType') != 'Z2':
        sys.exit(f'{args.mtprops} has no preconstructed Z2 packet for {PROFILE}')
    firmware = profile.get('Constructed Firmware')
    calibration = args.calibration.read_bytes()
    try:
        validate_firmware(firmware)
        validate_calibration(calibration)
    except (TypeError, ValueError) as error:
        sys.exit(f'refused: {error}')

    write_private(args.output / FIRMWARE_NAME, firmware)
    write_private(args.output / CALIBRATION_NAME, calibration)
    manifest = {
        'profile': PROFILE,
        'firmware_version': profile.get('Constructed Firmware Version'),
        'mtprops_sha256': hashlib.sha256(args.mtprops.read_bytes()).hexdigest(),
        'files': {
            FIRMWARE_NAME: {'bytes': len(firmware),
                            'sha256': hashlib.sha256(firmware).hexdigest()},
            CALIBRATION_NAME: {'bytes': len(calibration),
                               'sha256': hashlib.sha256(calibration).hexdigest(),
                               'private': True},
        },
    }
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
