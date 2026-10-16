import copy
import hashlib
import io
import json
import shutil
import struct
import unittest
import uuid
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from ai_test_agent import m2_artifacts
from ai_test_agent.m2_artifacts import (
    ArtifactVerificationError,
    validate_artifact_contract,
    verify_apks,
)


ANDROID = 'http://schemas.android.com/apk/res/android'
ABSENT = 0xffffffff


def digest(data):
    return hashlib.sha256(data).hexdigest()


def zip_bytes(entries):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries:
                entry = zipfile.ZipInfo(name)
                # Windows ZipInfo normalizes backslashes at construction.
                entry.filename = name
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, content)
    return output.getvalue()


def plain_manifest():
    return (f'<manifest xmlns:android="{ANDROID}" package="example.test.game" '
            'android:versionCode="77" android:versionName="4.2">'
            '<uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36"/>'
            '</manifest>').encode()


def binary_manifest(*, utf8=True):
    """Small actual ResXMLTree fixture, including typed and raw attributes."""
    strings = ['manifest', 'uses-sdk', 'package', 'example.test.game',
               'versionCode', 'versionName', '4.2', 'minSdkVersion',
               'targetSdkVersion', ANDROID]
    offsets, encoded = [], bytearray()
    for value in strings:
        offsets.append(len(encoded))
        if utf8:
            data = value.encode('utf-8')
            encoded.extend(bytes([len(value), len(data)]) + data + b'\0')
        else:
            encoded.extend(struct.pack('<H', len(value)) + value.encode('utf-16le') + b'\0\0')
    while len(encoded) % 4:
        encoded.append(0)
    start = 28 + 4 * len(strings)
    pool = (struct.pack('<HHI5I', 1, 28, start + len(encoded), len(strings), 0,
                        0x100 if utf8 else 0, start, 0)
            + struct.pack(f'<{len(strings)}I', *offsets) + encoded)

    def attribute(name, value, *, namespace=9, raw=ABSENT, kind=0x10):
        return struct.pack('<IIIHBBI', namespace, strings.index(name), raw, 8, 0, kind, value)

    def element(name, attributes):
        size = 36 + 20 * len(attributes)
        return (struct.pack('<HHIII', 0x102, 16, size, 1, ABSENT)
                + struct.pack('<II6H', ABSENT, strings.index(name), 20, 20,
                              len(attributes), 0, 0, 0) + b''.join(attributes))

    def end(name):
        return struct.pack('<HHIIIII', 0x103, 16, 24, 1, ABSENT, ABSENT, strings.index(name))

    body = (pool + element('manifest', [
        attribute('package', 3, namespace=ABSENT, kind=3),
        attribute('versionCode', 77),
        attribute('versionName', 6, raw=6, kind=3),
    ]) + element('uses-sdk', [attribute('minSdkVersion', 24), attribute('targetSdkVersion', 36)])
        + end('uses-sdk') + end('manifest'))
    return struct.pack('<HHI', 3, 8, 8 + len(body)) + body


def protobuf_varint(value):
    result = bytearray()
    while value > 0x7f:
        result.append((value & 0x7f) | 0x80)
        value >>= 7
    return bytes(result + bytes([value]))


def protobuf_message(field, content):
    return protobuf_varint(field * 8 + 2) + protobuf_varint(len(content)) + content


def toc_bytes(variants, *, standalone=False):
    result = bytearray()
    for number, paths in enumerate(variants):
        module = protobuf_message(1, protobuf_message(1, b'base'))
        for path in paths:
            is_standalone = standalone or path == 'universal.apk'
            description = (protobuf_message(2, path.encode())
                           + protobuf_message(4 if is_standalone else 3, b'' if is_standalone else b'\x10\x01'))
            module += protobuf_message(2, description)
        variant = protobuf_message(2, module) + b'\x18' + protobuf_varint(number)
        result.extend(protobuf_message(1, variant))
    return bytes(result)


def toc_asset_slice(path, *, name='config', delivery=1):
    metadata = protobuf_message(1, name.encode()) + b'\x20' + protobuf_varint(delivery)
    split = protobuf_message(1, name.encode()) + b'\x10\x01'
    description = protobuf_message(2, path.encode()) + protobuf_message(7, split)
    return protobuf_message(3, protobuf_message(1, metadata) + protobuf_message(2, description))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '.local' / ('m2-artifact-tests-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.asset = struct.pack('<III', 123, 2, 456)
        self.policy = {
            'kind': 'uint32_le', 'archive_apk': 'asset-slices/config-master.apk',
            'installed_apk': 'split_config.apk', 'asset_path': 'assets/config.bytes',
            'asset_sha256': digest(self.asset), 'offset': 4, 'expected_value': 2,
            'source_ref': 'fixture/config-schema#server',
        }
        self.expected = {'sha256': '0' * 64, 'package': 'example.test.game',
                         'version_code': 77, 'version_name': '4.2'}

    def artifact(self, *, manifest=None, asset=None, entries=None, base='splits/base-master.apk',
                 split_entries=None):
        base_apk = zip_bytes([('AndroidManifest.xml', plain_manifest() if manifest is None else manifest)])
        split = zip_bytes(split_entries if split_entries is not None else [
            (self.policy['asset_path'], self.asset if asset is None else asset)])
        content = zip_bytes(entries if entries is not None else [
            (base, base_apk), (self.policy['archive_apk'], split),
            ('toc.pb', toc_bytes([[base]]) + toc_asset_slice(self.policy['archive_apk']))])
        source = self.root / ('misleading-formal-v1-' + uuid.uuid4().hex + '.apks')
        source.write_bytes(content)
        self.expected['sha256'] = digest(content)
        return source

    def verify(self, source):
        return verify_apks(source, self.expected, self.policy, self.root / 'evidence')

    def variant_artifact(self, variants, *, toc=None, deliver_config=True):
        entries = [(name, zip_bytes([('AndroidManifest.xml', manifest)])) for name, manifest in variants]
        entries.append((self.policy['archive_apk'], zip_bytes([(self.policy['asset_path'], self.asset)])))
        if toc is None:
            toc = toc_bytes([[name] for name, _ in variants])
        if deliver_config:
            toc += toc_asset_slice(self.policy['archive_apk'])
        entries.append(('toc.pb', toc))
        return self.artifact(entries=entries)

    def test_reads_actual_plain_manifest_and_configuration_and_saves_evidence(self):
        result = self.verify(self.artifact())
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual({key: result[key] for key in ('package', 'version_code', 'version_name', 'min_sdk', 'target_sdk')},
                         {'package': 'example.test.game', 'version_code': 77, 'version_name': '4.2',
                          'min_sdk': 24, 'target_sdk': 36})
        self.assertEqual(result['sha256'], self.expected['sha256'])
        environment = result['environment_evidence']
        self.assertEqual(environment['observed_value'], 2)
        self.assertTrue(environment['matched'])
        for key in ('archive_apk', 'installed_apk', 'asset_path', 'asset_sha256', 'expected_value'):
            self.assertEqual(environment[key], self.policy[key])
        saved = json.loads((self.root / 'evidence' / 'artifact-inspection.json').read_text())
        self.assertEqual(saved, result)

    def test_reads_real_binary_axml_utf8_and_utf16_string_pools(self):
        for utf8 in (True, False):
            with self.subTest(utf8=utf8):
                result = self.verify(self.artifact(manifest=binary_manifest(utf8=utf8)))
                self.assertEqual(result['status'], 'PASS')
                self.assertEqual(result['version_code'], 77)
                self.assertEqual(result['target_sdk'], 36)

    def test_formal_value_is_blocked_and_preserves_actual_observation(self):
        formal = struct.pack('<III', 123, 3, 456)
        self.policy['asset_sha256'] = digest(formal)
        result = self.verify(self.artifact(asset=formal))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['category'], 'environment_mismatch')
        self.assertEqual(result['environment_evidence']['observed_value'], 3)
        self.assertFalse(result['environment_evidence']['matched'])
        self.assertTrue(result['reasons'])
        self.assertTrue((self.root / 'evidence' / 'artifact-inspection.json').is_file())

    def test_apks_hash_must_match_before_any_zip_content_is_read(self):
        source = self.root / 'not-a-zip.apks'
        source.write_bytes(b'not even a ZIP')
        result = self.verify(source)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['category'], 'artifact_hash_mismatch')
        self.assertEqual(result['sha256'], digest(source.read_bytes()))
        self.assertIsNone(result['package'])

    def test_actual_manifest_identity_must_match_every_approved_field(self):
        for key, value in [('package', 'example.other.game'), ('version_code', 78), ('version_name', '4.3')]:
            with self.subTest(key=key):
                source = self.artifact()
                expected = dict(self.expected, **{key: value})
                result = verify_apks(source, expected, self.policy, self.root / 'evidence')
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['category'], 'artifact_identity_mismatch')
                self.assertIn(key, ' '.join(result['reasons']))

    def test_asset_hash_mismatch_blocks_before_decoding_untrusted_content(self):
        result = self.verify(self.artifact(asset=b'{invalid JSON'))
        self.assertEqual(result['category'], 'environment_asset_hash_mismatch')
        self.assertIsNone(result['environment_evidence']['observed_value'])
        self.assertEqual(result['environment_evidence']['asset_sha256'], digest(b'{invalid JSON'))

    def test_json_field_is_explicit_scalar_and_compares_types(self):
        self.asset = b'{"environment":{"server":"test"}}'
        self.policy = dict(self.policy, kind='json_field', field_path=['environment', 'server'],
                           expected_value='test', asset_sha256=digest(self.asset))
        self.policy.pop('offset')
        self.assertEqual(self.verify(self.artifact())['status'], 'PASS')
        self.asset = b'{"environment":{"server":true}}'
        self.policy.update(expected_value=1, asset_sha256=digest(self.asset))
        self.assertEqual(self.verify(self.artifact())['status'], 'BLOCKED')

    def test_missing_or_invalid_json_field_is_blocked_with_evidence(self):
        for asset in (b'{}', b'{"environment":{"server":[]}}', b'not json',
                      b'{"environment":{"server":"formal","server":"test"}}'):
            with self.subTest(asset=asset):
                self.policy = dict(self.policy, kind='json_field', field_path=['environment', 'server'],
                                   expected_value='test', asset_sha256=digest(asset))
                self.policy.pop('offset', None)
                result = self.verify(self.artifact(asset=asset))
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['category'], 'environment_unverified')
                self.assertTrue((self.root / 'evidence' / 'artifact-inspection.json').is_file())

    def test_uint32_offset_must_fit_actual_asset(self):
        self.asset = b'12'
        self.policy['asset_sha256'] = digest(self.asset)
        result = self.verify(self.artifact())
        self.assertEqual(result['category'], 'environment_unverified')

    def test_universal_is_supported_but_guessed_base_filename_is_not(self):
        self.assertEqual(self.verify(self.artifact(base='universal.apk'))['status'], 'PASS')
        result = self.verify(self.artifact(entries=[('splits/pretend-base.apk', zip_bytes([('AndroidManifest.xml', plain_manifest())]))]))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIsNone(result['package'])

    def test_missing_config_archive_or_asset_is_blocked(self):
        source = self.artifact()
        self.policy['archive_apk'] = 'asset-slices/missing.apk'
        self.assertEqual(self.verify(source)['category'], 'environment_unverified')
        self.policy['archive_apk'] = 'asset-slices/config-master.apk'
        self.assertEqual(self.verify(self.artifact(split_entries=[]))['category'], 'environment_unverified')

    def test_hashes_accept_uppercase_approved_digest(self):
        source = self.artifact()
        self.expected['sha256'] = self.expected['sha256'].upper()
        self.policy['asset_sha256'] = self.policy['asset_sha256'].upper()
        self.assertEqual(self.verify(source)['status'], 'PASS')

    def test_large_split_is_streamed_to_unique_evidence_file(self):
        source = self.artifact(split_entries=[(self.policy['asset_path'], self.asset), ('assets/padding', b'x' * 2_000_000)])
        original = zipfile.ZipFile.read

        def no_whole_split(archive, name, *args, **kwargs):
            if name == self.policy['archive_apk']:
                self.fail('configuration split was loaded wholesale into memory')
            return original(archive, name, *args, **kwargs)

        with patch.object(zipfile.ZipFile, 'read', no_whole_split):
            self.assertEqual(self.verify(source)['status'], 'PASS')
            self.assertEqual(self.verify(source)['status'], 'PASS')
        self.assertGreaterEqual(len(list((self.root / 'evidence').glob('*.apk'))), 2)
        self.assertEqual(digest(source.read_bytes()), self.expected['sha256'])

    def test_base_above_memory_threshold_is_streamed_and_all_apk_hashes_are_observed(self):
        source = self.artifact()
        original = m2_artifacts._read_bounded

        def no_whole_base(archive, entry, limit):
            if entry.filename == 'splits/base-master.apk':
                self.fail('large base APK was read into memory')
            return original(archive, entry, limit)

        with patch.object(m2_artifacts, 'MAX_BASE_APK_BYTES', 8), patch.object(m2_artifacts, '_read_bounded', no_whole_base):
            result = self.verify(source)
        self.assertEqual(result['status'], 'PASS')
        with zipfile.ZipFile(source) as archive:
            expected = {name: digest(archive.read(name)) for name in archive.namelist() if name.endswith('.apk')}
        self.assertEqual(result['apk_hashes'], expected)
        self.assertTrue(any((self.root / 'evidence').glob('base-*.apk')))

    def test_orphan_test_config_cannot_hide_formal_base_configuration(self):
        formal = struct.pack('<III', 123, 3, 456)
        base = zip_bytes([('AndroidManifest.xml', plain_manifest()), (self.policy['asset_path'], formal)])
        orphan = zip_bytes([(self.policy['asset_path'], self.asset)])
        source = self.artifact(entries=[('splits/base-master.apk', base),
            (self.policy['archive_apk'], orphan), ('toc.pb', toc_bytes([['splits/base-master.apk']]))])
        self.assertEqual(self.verify(source)['category'], 'environment_unverified')

    def test_no_toc_only_accepts_unique_self_contained_base_configuration(self):
        source = self.artifact(entries=[('splits/base-master.apk', zip_bytes([('AndroidManifest.xml', plain_manifest())])),
            (self.policy['archive_apk'], zip_bytes([(self.policy['asset_path'], self.asset)]))])
        self.assertEqual(self.verify(source)['category'], 'environment_unverified')
        self.policy.update(archive_apk='universal.apk', installed_apk='base.apk')
        self_contained = zip_bytes([('AndroidManifest.xml', plain_manifest()), (self.policy['asset_path'], self.asset)])
        self.assertEqual(self.verify(self.artifact(entries=[('universal.apk', self_contained)]))['status'], 'PASS')

    def test_non_install_time_or_wrong_installed_name_does_not_prove_environment(self):
        variants = [('splits/base-master.apk', plain_manifest())]
        for delivery in (0, 2, 3):
            with self.subTest(delivery=delivery):
                toc = toc_bytes([[variants[0][0]]]) + toc_asset_slice(self.policy['archive_apk'], delivery=delivery)
                self.assertEqual(self.verify(self.variant_artifact(variants, toc=toc, deliver_config=False))['category'],
                                 'environment_unverified')
        self.policy['installed_apk'] = 'split_wrong.apk'
        self.assertEqual(self.verify(self.artifact())['category'], 'environment_unverified')

    def test_installable_duplicate_asset_with_formal_content_blocks(self):
        formal = struct.pack('<III', 123, 3, 456)
        base = zip_bytes([('AndroidManifest.xml', plain_manifest()), (self.policy['asset_path'], formal)])
        config = zip_bytes([(self.policy['asset_path'], self.asset)])
        toc = toc_bytes([['splits/base-master.apk']]) + toc_asset_slice(self.policy['archive_apk'])
        source = self.artifact(entries=[('splits/base-master.apk', base), (self.policy['archive_apk'], config), ('toc.pb', toc)])
        self.assertEqual(self.verify(source)['category'], 'environment_unverified')

    def test_other_install_time_asset_pack_cannot_hide_conflicting_configuration(self):
        variants = [('splits/base-master.apk', plain_manifest())]
        config = zip_bytes([(self.policy['asset_path'], self.asset)])
        other_path = 'asset-slices/other-master.apk'
        other = zip_bytes([(self.policy['asset_path'], struct.pack('<III', 123, 3, 456))])
        toc = (toc_bytes([[variants[0][0]]]) + toc_asset_slice(self.policy['archive_apk'])
               + toc_asset_slice(other_path, name='other'))
        source = self.artifact(entries=[(variants[0][0], zip_bytes([('AndroidManifest.xml', plain_manifest())])),
            (self.policy['archive_apk'], config), (other_path, other), ('toc.pb', toc)])
        result = self.verify(source)
        self.assertEqual(result['category'], 'environment_unverified')
        self.assertEqual(len(result['environment_evidence']['validated_assets']), 2)

    def test_total_configuration_asset_expansion_is_bounded_across_apks(self):
        self.asset = b'x' * 1000
        self.policy['asset_sha256'] = digest(self.asset)
        base = zip_bytes([('AndroidManifest.xml', plain_manifest()), (self.policy['asset_path'], self.asset)])
        config = zip_bytes([(self.policy['asset_path'], self.asset)])
        toc = toc_bytes([['splits/base-master.apk']]) + toc_asset_slice(self.policy['archive_apk'])
        source = self.artifact(entries=[('splits/base-master.apk', base), (self.policy['archive_apk'], config), ('toc.pb', toc)])
        with patch.object(m2_artifacts, 'MAX_ARCHIVE_UNCOMPRESSED_BYTES', 1500):
            self.assert_structure_error(source, 'artifact_budget')

    def test_each_base_variant_must_contain_matching_config_when_policy_uses_base(self):
        self.policy.update(archive_apk='splits/base-master.apk', installed_apk='base.apk')
        names = ['splits/base-master.apk', 'splits/base-master_2.apk']
        first = zip_bytes([('AndroidManifest.xml', plain_manifest()), (self.policy['asset_path'], self.asset)])
        for second_asset in (None, struct.pack('<III', 123, 3, 456), self.asset):
            with self.subTest(second_asset=second_asset):
                second_entries = [('AndroidManifest.xml', plain_manifest())]
                if second_asset is not None:
                    second_entries.append((self.policy['asset_path'], second_asset))
                source = self.artifact(entries=[(names[0], first), (names[1], zip_bytes(second_entries)),
                                               ('toc.pb', toc_bytes([[name] for name in names]))])
                result = self.verify(source)
                self.assertEqual(result['status'], 'PASS' if second_asset == self.asset else 'BLOCKED')

    def test_evidence_report_cannot_overwrite_input_through_a_hard_link(self):
        source = self.artifact()
        evidence = self.root / 'evidence'
        evidence.mkdir()
        (evidence / 'artifact-inspection.json').hardlink_to(source)
        self.assertEqual(self.verify(source)['status'], 'PASS')
        self.assertEqual(digest(source.read_bytes()), self.expected['sha256'])

    def test_zip_directory_is_bounded_before_entries_are_allocated(self):
        content = bytearray(zip_bytes([]))
        struct.pack_into('<HH', content, 8, 32769, 32769)
        source = self.root / 'excessive-directory.apks'
        source.write_bytes(content)
        self.expected['sha256'] = digest(content)
        self.assert_structure_error(source, 'artifact_budget')

    def test_zip64_end_records_are_supported_with_the_same_budgets(self):
        source = self.artifact()
        content = source.read_bytes()
        position = content.rfind(b'PK\x05\x06')
        footer = bytearray(content[position:])
        count = struct.unpack_from('<H', footer, 10)[0]
        size, offset = struct.unpack_from('<II', footer, 12)
        record = struct.pack('<4sQ2H2I4Q', b'PK\x06\x06', 44, 45, 45, 0, 0,
                             count, count, size, offset)
        locator = struct.pack('<4sIQI', b'PK\x06\x07', 0, position, 1)
        struct.pack_into('<HHII', footer, 8, 0xffff, 0xffff, 0xffffffff, 0xffffffff)
        updated = content[:position] + record + locator + footer
        source.write_bytes(updated)
        self.expected['sha256'] = digest(updated)
        self.assertEqual(self.verify(source)['status'], 'PASS')

    def test_contract_errors_are_categorized_and_have_safe_json_evidence(self):
        self.policy['expected_value'] = float('nan')
        self.assert_structure_error(self.artifact(), 'artifact_contract')
        saved = json.loads((self.root / 'evidence' / 'artifact-inspection.json').read_text())
        self.assertEqual(saved['status'], 'BLOCKED')

    def assert_structure_error(self, source, category):
        with self.assertRaises(ArtifactVerificationError) as caught:
            self.verify(source)
        self.assertEqual(caught.exception.category, category)
        self.assertTrue((self.root / 'evidence' / 'artifact-inspection.json').is_file())

    def test_duplicate_and_case_colliding_entries_are_rejected(self):
        for names in [('x', 'x'), ('X', 'x')]:
            with self.subTest(names=names):
                self.assert_structure_error(self.artifact(entries=[(name, b'') for name in names]), 'artifact_duplicate')

    def test_unsafe_outer_and_nested_zip_paths_are_rejected(self):
        for name in ('../outside', '/absolute', 'C:/drive', 'back\\slash', 'a/../b', 'nul\0suffix'):
            with self.subTest(name=name):
                self.assert_structure_error(self.artifact(entries=[(name, b'x')]), 'artifact_path')
                self.assert_structure_error(self.artifact(split_entries=[(name, b'x')]), 'artifact_path')

    def test_bad_outer_and_inner_zip_are_categorized(self):
        source = self.root / 'bad.apks'
        source.write_bytes(b'bad ZIP')
        self.expected['sha256'] = digest(source.read_bytes())
        self.assert_structure_error(source, 'artifact_archive')
        self.assert_structure_error(self.artifact(entries=[('splits/base-master.apk', b'bad APK')]), 'artifact_archive')

    def test_corrupt_deflate_stream_is_a_categorized_archive_error(self):
        content = bytearray(zip_bytes([('splits/base-master.apk', b'APK content' * 20)]))
        name_length, extra_length = struct.unpack_from('<HH', content, 26)
        content[30 + name_length + extra_length] = 0xff
        source = self.root / 'broken-compressed.apks'
        source.write_bytes(content)
        self.expected['sha256'] = digest(content)
        self.assert_structure_error(source, 'artifact_archive')

    def test_all_matching_base_variants_are_verified_without_archive_order_preference(self):
        variants = [('splits/base-master.apk', plain_manifest()), ('splits/base-master_2.apk', binary_manifest())]
        toc = toc_bytes([[variants[0][0]], [variants[1][0]]])
        for ordered in (variants, list(reversed(variants))):
            with self.subTest(order=ordered[0][0]):
                result = self.verify(self.variant_artifact(ordered, toc=toc))
                self.assertEqual(result['status'], 'PASS')
                self.assertEqual({item['archive_apk'] for item in result['base_apks']}, {item[0] for item in variants})
                self.assertTrue(all(item['package'] == self.expected['package'] for item in result['base_apks']))

    def test_second_base_with_different_identity_is_blocked_and_observed(self):
        changes = [(b'example.test.game', b'example.evil.game'), (b'versionCode="77"', b'versionCode="78"'),
                   (b'versionName="4.2"', b'versionName="4.3"')]
        for before, after in changes:
            with self.subTest(change=before):
                variants = [('splits/base-master.apk', plain_manifest()),
                            ('splits/base-master_2.apk', plain_manifest().replace(before, after))]
                result = self.verify(self.variant_artifact(variants, toc=toc_bytes([[item[0]] for item in variants])))
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertEqual(result['category'], 'artifact_identity_mismatch')
                self.assertEqual(len(result['base_apks']), 2)
                self.assertIn('base-master_2.apk', ' '.join(result['reasons']))

    def test_universal_and_split_variants_require_matching_identity_and_sdk(self):
        variants = [('splits/base-master.apk', plain_manifest()), ('universal.apk', binary_manifest())]
        self.assertEqual(self.verify(self.variant_artifact(variants))['status'], 'PASS')
        variants[1] = ('universal.apk', plain_manifest().replace(b'targetSdkVersion="36"', b'targetSdkVersion="35"'))
        result = self.verify(self.variant_artifact(variants))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['category'], 'artifact_variant_mismatch')

    def test_toc_master_reference_cannot_hide_a_base_under_an_unchecked_name(self):
        for master in ('splits/unchecked.apk', 'splits/base-master_missing.apk', 'splits/base-master_3.apk'):
            with self.subTest(master=master):
                variants = [('splits/base-master.apk', plain_manifest())]
                if master != 'splits/base-master_3.apk':
                    variants.append((master, plain_manifest().replace(b'77', b'78')))
                self.assert_structure_error(self.variant_artifact(variants, toc=toc_bytes([[master]])), 'artifact_manifest')

    def test_malformed_or_ambiguous_toc_cannot_pass_variant_verification(self):
        variants = [('splits/base-master.apk', plain_manifest()), ('splits/base-master_2.apk', plain_manifest())]
        for toc in (b'\x0a\x80', b'\x0b', toc_bytes([[item[0] for item in variants]])):
            with self.subTest(toc=toc):
                self.assert_structure_error(self.variant_artifact(variants, toc=toc), 'artifact_manifest')

    def test_universal_standalone_toc_is_supported(self):
        variants = [('universal.apk', plain_manifest())]
        result = self.verify(self.variant_artifact(variants, toc=toc_bytes([['universal.apk']], standalone=True)))
        self.assertEqual(result['status'], 'PASS')

    def test_all_base_variant_manifests_are_subject_to_budgets_and_parsing(self):
        variants = [('splits/base-master.apk', plain_manifest()), ('splits/base-master_2.apk', b'broken')]
        self.assert_structure_error(self.variant_artifact(variants), 'artifact_manifest')

    def test_archive_and_asset_budgets_are_enforced(self):
        source = self.artifact()
        for name, limit in [('MAX_ARCHIVE_ENTRIES', 1), ('MAX_ARCHIVE_UNCOMPRESSED_BYTES', 8),
                            ('MAX_SPLIT_APK_BYTES', 8), ('MAX_CONFIG_ASSET_BYTES', 8), ('MAX_APKS_BYTES', 8)]:
            with self.subTest(name=name), patch.object(m2_artifacts, name, limit):
                self.assert_structure_error(source, 'artifact_budget')

    def test_missing_namespace_or_corrupt_binary_manifest_cannot_supply_identity(self):
        corrupt = binary_manifest()[:-4]
        for manifest in (b'<manifest package="example.test.game" versionCode="77" versionName="4.2"/>', corrupt):
            with self.subTest(manifest=manifest[:20]):
                self.assert_structure_error(self.artifact(manifest=manifest), 'artifact_manifest')


class ArtifactContractTests(unittest.TestCase):
    def setUp(self):
        self.expected = {'sha256': 'a' * 64, 'package': 'example.test.game', 'version_code': 77, 'version_name': '4.2'}
        self.policy = {'kind': 'uint32_le', 'archive_apk': 'asset-slices/config-master.apk',
                       'installed_apk': 'split_config.apk', 'asset_path': 'assets/config.bytes',
                       'asset_sha256': 'b' * 64, 'offset': 4, 'expected_value': 2, 'source_ref': 'fixture/source'}

    def test_accepts_only_complete_explicit_contract(self):
        self.assertEqual(validate_artifact_contract(self.expected, self.policy), [])
        for key in self.expected:
            expected = dict(self.expected)
            del expected[key]
            self.assertTrue(validate_artifact_contract(expected, self.policy), key)
        for key in self.policy:
            policy = dict(self.policy)
            del policy[key]
            self.assertTrue(validate_artifact_contract(self.expected, policy), key)

    def test_rejects_unknown_fields_bad_types_and_paths(self):
        for field, value in [('sha256', 'x' * 64), ('version_code', True), ('version_name', ''),
                             ('package', 'not-package'), ('unexpected', 1)]:
            self.assertTrue(validate_artifact_contract(dict(self.expected, **{field: value}), self.policy))
        for field, value in [('offset', True), ('offset', -1), ('expected_value', ''),
                             ('expected_value', []), ('source_ref', ''), ('asset_path', '../escape'),
                             ('archive_apk', 'C:/x.apk'), ('installed_apk', '../device.apk'),
                             ('asset_sha256', 'z' * 64), ('kind', 'execute'), ('unexpected', 1)]:
            self.assertTrue(validate_artifact_contract(self.expected, dict(self.policy, **{field: value})), field)

    def test_json_policy_rejects_empty_path_nonfinite_scalar_and_uint32_field(self):
        policy = copy.deepcopy(self.policy)
        policy.pop('offset')
        policy.update(kind='json_field', field_path=['server'], expected_value='test')
        self.assertEqual(validate_artifact_contract(self.expected, policy), [])
        for field, value in [('field_path', []), ('field_path', ['']), ('expected_value', float('nan')), ('offset', 0)]:
            self.assertTrue(validate_artifact_contract(self.expected, dict(policy, **{field: value})))


if __name__ == '__main__':
    unittest.main()
