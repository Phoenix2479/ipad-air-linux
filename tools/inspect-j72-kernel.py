#!/usr/bin/env python3
"""Read metadata from a 64-bit little-endian Mach-O kernelcache."""
import argparse
import base64
import hashlib
import json
import os
import struct
import sys
import xml.etree.ElementTree as ET


class InspectError(ValueError):
    pass


def bounded(offset, size, limit, what):
    if offset < 0 or size < 0 or offset > limit or size > limit - offset:
        raise InspectError(f"{what} is out of bounds")


def name(raw):
    return raw.split(b'\0', 1)[0].decode('ascii', errors='replace')


def parse_macho(blob):
    bounded(0, 32, len(blob), 'Mach-O header')
    magic, _, _, _, ncmds, sizeofcmds, _, _ = struct.unpack_from('<IiiIIIII', blob)
    if magic != 0xfeedfacf:
        raise InspectError('not a 64-bit little-endian Mach-O')
    bounded(32, sizeofcmds, len(blob), 'load commands')
    end, pos = 32 + sizeofcmds, 32
    segments, info = [], None
    for _ in range(ncmds):
        bounded(pos, 8, end, 'load command header')
        cmd, size = struct.unpack_from('<II', blob, pos)
        if size < 8 or size % 8:
            raise InspectError('invalid load command size')
        bounded(pos, size, end, 'load command')
        if cmd == 0x19:
            if size < 72:
                raise InspectError('short segment command')
            _, _, segname, vmaddr, vmsize, fileoff, filesize, _, _, nsects, _ = struct.unpack_from('<II16sQQQQiiII', blob, pos)
            bounded(fileoff, filesize, len(blob), 'segment file range')
            bounded(72, nsects * 80, size, 'section table')
            segment = dict(name=name(segname), address=vmaddr, size=vmsize,
                           file_offset=fileoff, file_size=filesize, sections=[])
            for i in range(nsects):
                fields = struct.unpack_from('<16s16sQQIIIIIIII', blob, pos + 72 + i * 80)
                sectname, owner, addr, length, offset, _, _, _, flags, _, _, _ = fields
                sectname, owner = name(sectname), name(owner)
                if owner != segment['name']:
                    raise InspectError(f'section {owner}.{sectname} does not belong to segment {segment["name"]}')
                if addr < vmaddr or length > vmsize or addr - vmaddr > vmsize - length:
                    raise InspectError(f'section {owner}.{sectname} exceeds segment VA range')
                # S_ZEROFILL, S_GB_ZEROFILL and S_THREAD_LOCAL_ZEROFILL have no file bytes.
                if flags & 0xff not in (1, 0xc, 0x12):
                    bounded(offset, length, len(blob), f'section {owner}.{sectname}')
                    if offset < fileoff or length > filesize or offset - fileoff > filesize - length:
                        raise InspectError(f'section {owner}.{sectname} exceeds segment file range')
                section = dict(name=sectname, segment=owner, address=addr, size=length, file_offset=offset)
                segment['sections'].append(section)
                if segment['name'] == '__PRELINK_INFO' and sectname == '__info':
                    if info is not None or flags & 0xff in (1, 0xc, 0x12):
                        raise InspectError('invalid or duplicate prelink info section')
                    info = blob[offset:offset + length]
            segments.append(segment)
        pos += size
    if pos != end:
        raise InspectError('load commands do not fill declared size')
    if info is None:
        raise InspectError('missing __PRELINK_INFO.__info')
    return segments, info


def parse_prelink(xml):
    try:
        root = ET.fromstring(xml.rstrip(b'\0'))
    except ET.ParseError as exc:
        raise InspectError(f'invalid prelink XML: {exc}') from exc
    if root.tag == 'plist' and len(root) == 1:
        root = root[0]
    if root.tag != 'dict':
        raise InspectError('prelink XML root must be a dict')
    ids = {}
    for node in root.iter():
        ident = node.get('ID')
        if ident is not None:
            if ident in ids:
                raise InspectError(f'duplicate XML ID {ident}')
            ids[ident] = node
    memo, active = {}, set()

    def convert(node):
        if node in active:
            raise InspectError(f'cyclic XML reference at {node.get("ID", node.tag)}')
        if node in memo:
            return memo[node]
        active.add(node)
        try:
            ref = node.get('IDREF')
            if ref is not None:
                if len(node) or (node.text and node.text.strip()):
                    raise InspectError('XML reference cannot contain content')
                if ref not in ids:
                    raise InspectError(f'unresolved XML IDREF {ref}')
                return convert(ids[ref])
            tag, value = node.tag, node.text or ''
            if tag == 'dict':
                children = list(node)
                if len(children) % 2:
                    raise InspectError('dictionary has an unmatched key')
                result = {}
                for key, child in zip(children[::2], children[1::2]):
                    if key.tag != 'key':
                        raise InspectError('invalid or duplicate dictionary key')
                    resolved_key = convert(key)
                    if not isinstance(resolved_key, str) or resolved_key in result:
                        raise InspectError('invalid or duplicate dictionary key')
                    result[resolved_key] = convert(child)
            elif tag == 'array':
                result = [convert(child) for child in node]
            elif tag in ('string', 'key', 'integer', 'true', 'false', 'data') and len(node):
                raise InspectError(f'{tag} cannot contain child elements')
            elif tag in ('string', 'key'):
                result = value
            elif tag == 'integer':
                try:
                    stripped = value.strip()
                    result = int(stripped, 16 if stripped.lower().lstrip('+-').startswith('0x') else 10)
                except ValueError as exc:
                    raise InspectError(f'invalid integer {value!r}') from exc
            elif tag in ('true', 'false'):
                if value.strip():
                    raise InspectError(f'{tag} cannot contain text')
                result = tag == 'true'
            elif tag == 'data':
                try:
                    result = base64.b64decode(''.join(value.split()), validate=True)
                except ValueError as exc:
                    raise InspectError('invalid base64 data') from exc
            else:
                raise InspectError(f'unsupported XML element {tag}')
            memo[node] = result
            return result
        finally:
            active.remove(node)
    return convert(root)


def json_value(value):
    if isinstance(value, bytes):
        return {'length': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_value(item) for item in value]
    return value


def inspect(blob):
    segments, xml = parse_macho(blob)
    prelink = parse_prelink(xml)
    kexts = prelink.get('_PrelinkInfoDictionary', [])
    if not isinstance(kexts, list):
        raise InspectError('_PrelinkInfoDictionary must be an array')
    identifiers, profiles = [], []
    for kext in kexts:
        if not isinstance(kext, dict):
            raise InspectError('prelink kext must be a dictionary')
        identifier = kext.get('CFBundleIdentifier')
        if 'CFBundleIdentifier' in kext:
            if not isinstance(identifier, str):
                raise InspectError('CFBundleIdentifier must be a string')
            identifiers.append(identifier)
        if identifier == 'com.apple.driver.AppleMultitouchSPIC1F15':
            personalities = kext.get('IOKitPersonalities', {})
            if not isinstance(personalities, dict):
                raise InspectError('IOKitPersonalities must be a dictionary')
            for profile_name, profile in personalities.items():
                if isinstance(profile, dict) and (profile.get('IONameMatch') == 'multi-touch,j72' or
                                                  isinstance(profile.get('IONameMatch'), list) and
                                                  'multi-touch,j72' in profile['IONameMatch']):
                    profiles.append({'name': profile_name, 'personality': json_value(profile)})
    return {'sha256': hashlib.sha256(blob).hexdigest(), 'segments': segments,
            'prelink_kext_identifiers': identifiers, 'j72_personalities': profiles}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel')
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    try:
        if args.output and (os.path.realpath(args.output) == os.path.realpath(args.kernel) or
                            (os.path.exists(args.output) and os.path.samefile(args.kernel, args.output))):
            raise InspectError('output must differ from input')
        with open(args.kernel, 'rb') as source:
            result = inspect(source.read())
        output = json.dumps(result, indent=2) + '\n'
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as dest:
                dest.write(output)
        else:
            sys.stdout.write(output)
    except (InspectError, OSError) as exc:
        parser.exit(1, f'error: {exc}\n')


if __name__ == '__main__':
    main()
