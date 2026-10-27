import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'tools/inspect-j72-kernel.py'
spec = importlib.util.spec_from_file_location('inspect_j72_kernel', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

XML = b'''<dict><key>_PrelinkInfoDictionary</key><array><dict>
<key>CFBundleIdentifier</key><string>com.apple.driver.AppleMultitouchSPIC1F15</string>
<key>shared</key><dict ID="7"><key>value</key><integer>0x10003400</integer></dict>
<key>alias</key><dict IDREF="7"/>
<key>IOKitPersonalities</key><dict><key>J72</key><dict>
<key>IONameMatch</key><array><string>multi-touch,j72</string></array>
<key>IOClass</key><string>AppleMultitouchN1SPI</string>
<key>fw-execute-addr</key><integer>0x10003400</integer>
<key>blob</key><data>YWJj</data><key>enabled</key><true/>
</dict></dict></dict></array></dict>'''


def fixture(xml=XML):
    header = struct.pack('<IiiIIIII', 0xfeedfacf, 0, 0, 0, 1, 152, 0, 0)
    offset = 32 + 152
    segment = struct.pack('<II16sQQQQiiII', 0x19, 152, b'__PRELINK_INFO',
                          0x1000, len(xml), offset, len(xml), 0, 0, 1, 0)
    section = struct.pack('<16s16sQQIIIIIIII', b'__info', b'__PRELINK_INFO',
                          0x1000, len(xml), offset, 0, 0, 0, 0, 0, 0, 0)
    return header + segment + section + xml


class InspectorTests(unittest.TestCase):
    def test_fixture_and_shared_reference(self):
        blob = fixture()
        segments, xml = module.parse_macho(blob)
        kext = module.parse_prelink(xml)['_PrelinkInfoDictionary'][0]
        self.assertIs(kext['shared'], kext['alias'])
        self.assertEqual(kext['shared']['value'], 0x10003400)
        result = module.inspect(blob)
        profile = result['j72_personalities'][0]['personality']
        self.assertEqual(profile['IOClass'], 'AppleMultitouchN1SPI')
        self.assertEqual(profile['fw-execute-addr'], 0x10003400)
        self.assertEqual(profile['blob'], {'length': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()})
        self.assertEqual(segments[0]['sections'][0]['size'], len(XML))

    def test_out_of_bounds_section(self):
        blob = bytearray(fixture())
        struct.pack_into('<I', blob, 32 + 72 + 48, len(blob) + 1)
        with self.assertRaisesRegex(module.InspectError, 'out of bounds'):
            module.parse_macho(blob)

    def test_unresolved_and_cyclic_references(self):
        for xml, message in [(b'<dict><key>a</key><dict IDREF="9"/></dict>', 'unresolved'),
                             (b'<dict ID="1"><key>a</key><dict IDREF="1"/></dict>', 'cyclic'),
                             (b'<dict><key>a</key><dict ID="1" IDREF="2"/><key>b</key><dict ID="2" IDREF="1"/></dict>', 'cyclic')]:
            with self.subTest(message=message), self.assertRaisesRegex(module.InspectError, message):
                module.parse_prelink(xml)

    def test_key_references_and_validation(self):
        xml = b'<dict><key>first</key><string ID="1">referenced</string><key IDREF="1"/><string>value</string></dict>'
        self.assertEqual(module.parse_prelink(xml), {'first': 'referenced', 'referenced': 'value'})
        for xml in (b'<dict><key IDREF="missing"/><string/></dict>',
                    b'<dict><key>a</key><integer ID="1">1</integer><key IDREF="1"/><string/></dict>',
                    b'<dict><key ID="1">a</key><string/><key IDREF="1"/><string/></dict>'):
            with self.subTest(xml=xml), self.assertRaises(module.InspectError):
                module.parse_prelink(xml)

    def test_scalar_and_reference_content(self):
        for element in (b'<string>x<dict IDREF="99"/></string>', b'<key>x<string/></key>',
                        b'<integer>1<string/></integer>', b'<data><string/></data>',
                        b'<true>yes</true>', b'<false><string/></false>',
                        b'<string IDREF="1">text</string>', b'<string IDREF="1"><string/></string>'):
            xml = b'<dict><key>a</key><string ID="1"/><key>b</key>' + element + b'</dict>'
            with self.subTest(element=element), self.assertRaises(module.InspectError):
                module.parse_prelink(xml)
        self.assertEqual(module.parse_prelink(b'<dict><key/><string/><key>data</key><data/><key>bool</key><true/></dict>'),
                         {'': '', 'data': b'', 'bool': True})

    def test_invalid_bundle_identifier(self):
        for value in (b'<data>YWJj</data>', b'<integer>1</integer>', b'<dict/>'):
            xml = b'<dict><key>_PrelinkInfoDictionary</key><array><dict><key>CFBundleIdentifier</key>' + value + b'</dict></array></dict>'
            with self.subTest(value=value), self.assertRaisesRegex(module.InspectError, 'CFBundleIdentifier'):
                module.inspect(fixture(xml))

    def test_section_owner_must_match_segment(self):
        blob = bytearray(fixture())
        struct.pack_into('<16s', blob, 32 + 8, b'__OTHER')
        with self.assertRaisesRegex(module.InspectError, 'does not belong'):
            module.parse_macho(blob)

    def test_non_macho(self):
        with self.assertRaisesRegex(module.InspectError, 'Mach-O'):
            module.parse_macho(bytes(32))

    def test_cli_output_and_input_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            kernel = Path(directory) / 'kernel'
            output = Path(directory) / 'result.json'
            blob = fixture()
            kernel.write_bytes(blob)
            subprocess.run([sys.executable, str(SCRIPT), str(kernel), '--output', str(output)], check=True)
            self.assertEqual(json.loads(output.read_text())['sha256'], hashlib.sha256(blob).hexdigest())
            result = subprocess.run([sys.executable, str(SCRIPT), str(kernel), '--output', str(kernel)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(kernel.read_bytes(), blob)


if __name__ == '__main__':
    unittest.main()
