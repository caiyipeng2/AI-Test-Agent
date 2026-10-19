from copy import deepcopy
import json
import hashlib
from pathlib import Path
import shutil
import struct
import unittest
from unittest.mock import patch
from uuid import uuid4

from m32_support import (IDENTITY_MARKER, SYNTHETIC_MVID, identity_line, provider_recording,
                         refresh_capture_log, synthetic_metadata_pe)
from m3_support import line


class ProviderHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '.local' / ('m32-handshake-' + uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.value = provider_recording(self.root)

    def inspect(self):
        try:
            from ai_test_agent.m3_handshake import inspect_provider_recording
        except ModuleNotFoundError:
            self.fail('M3.2 provider identity verifier is not implemented')
        with patch('ai_test_agent.android.AndroidDevice', side_effect=AssertionError('device-free verification')):
            return inspect_provider_recording(self.value, evidence_root=self.root)

    def blocked(self):
        result = self.inspect()
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertTrue(result['reasons'])
        self.assertEqual(result['business_status'], 'NOT_EVALUATED')
        self.assertEqual(result['execution'], 'device_free')
        self.assertIs(result['live_provider'], False)
        return result

    def identity(self):
        return json.loads(self.value['identity_line'].split(IDENTITY_MARKER)[1])

    def change_identity(self, **updates):
        value = self.identity()
        value.update(updates)
        stamp = '10-16 10:00:00.010' if self.value['mode'] == 'device' else None
        self.value['identity_line'] = identity_line(value, timestamp=stamp)

    def proof(self):
        return json.loads((self.root / self.value['transport']['installed_proof']).read_text(encoding='utf-8'))

    def save_proof(self, proof):
        (self.root / self.value['transport']['installed_proof']).write_text(json.dumps(proof), encoding='utf-8')

    def test_assembly_probe_identity_is_narrow_device_free_and_synthetic(self):
        before = deepcopy(self.value)
        result = self.inspect()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['scope'], 'compiled_assembly_identity')
        self.assertEqual(result['business_status'], 'NOT_EVALUATED')
        self.assertEqual(result['execution'], 'device_free')
        self.assertEqual(result['evidence_mode'], 'synthetic')
        self.assertIs(result['live_provider'], False)
        self.assertEqual(self.value, before)
        self.assertEqual(result['verified_files'][0]['sha256'], self.value['manifest']['dll_sha256'])

    def test_complete_synthetic_device_recording_confirms_only_supplied_identity(self):
        self.value = provider_recording(self.root, 'device')
        result = self.inspect()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['scope'], 'provider_runtime_identity')
        self.assertEqual(result['business_status'], 'NOT_EVALUATED')
        self.assertEqual(result['evidence_mode'], 'synthetic')
        self.assertIs(result['live_provider'], False)
        self.assertEqual(len(result['verified_files']), 4)

    def test_recorded_input_label_is_preserved_without_online_execution_claim(self):
        self.value['evidence_mode'] = 'recorded'
        result = self.inspect()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['evidence_mode'], 'recorded')
        self.assertIs(result['live_provider'], False)

    def test_recording_and_manifest_exact_keys_and_types(self):
        for path, field, value in [
            ([], 'unexpected', 1), ([], 'schema_version', True), ([], 'evidence_mode', 'live'),
            ([], 'mode', 'editor'), (['manifest'], 'unexpected', 1),
            (['manifest'], 'schema_version', True), (['manifest'], 'runtime_build_id', ''),
            (['manifest'], 'provider', ''), (['manifest'], 'assembly_name', ' '),
            (['manifest'], 'source_snapshot_sha256', 'not-a-hash'),
            (['manifest'], 'module_mvid', 'not-a-guid'),
            (['manifest'], 'module_mvid', '00000000-0000-0000-0000-000000000000'),
            (['manifest'], 'dll_sha256', 'A' * 64),
        ]:
            with self.subTest(path=path, field=field, value=value):
                self.value = provider_recording(self.root)
                parent = self.value
                for key in path:
                    parent = parent[key]
                parent[field] = value
                self.blocked()
        self.value = provider_recording(self.root)
        del self.value['evidence_mode']
        self.blocked()

    def test_actual_dll_tampering_and_empty_file_block(self):
        for content in (b'tampered', b''):
            with self.subTest(content=content):
                (self.root / self.value['dll_path']).write_bytes(content)
                self.blocked()

    def test_consistent_declared_mvid_must_still_match_actual_pe_bytes(self):
        path = self.root / self.value['dll_path']
        path.write_bytes(synthetic_metadata_pe())
        self.value['manifest']['dll_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.value['manifest']['module_mvid'] = '12345678-1234-4567-89ab-123456789abd'
        self.change_identity(dll_sha256=self.value['manifest']['dll_sha256'],
                             module_mvid=self.value['manifest']['module_mvid'])
        self.blocked()

    def test_legacy_manifest_and_identity_schema_are_rejected(self):
        self.value['manifest']['schema_version'] = 1
        self.value['manifest'].pop('mvid_source', None)
        old = self.identity()
        old['schema'] = 1
        old.pop('mvid_source', None)
        self.value['identity_line'] = identity_line(old)
        self.blocked()

    def test_schema2_provenance_and_actual_metadata_are_reported(self):
        for mode, source in (('assembly_probe', 'runtime_api'), ('device', 'loaded_pe_metadata')):
            with self.subTest(mode=mode):
                self.value = provider_recording(self.root, mode)
                result = self.inspect()
                self.assertEqual(result['status'], 'PASS', result)
                self.assertEqual(self.value['manifest']['schema_version'], 2)
                self.assertEqual(self.identity()['schema'], 2)
                self.assertEqual(self.value['manifest'].get('mvid_source'), source)
                self.assertEqual(self.identity().get('mvid_source'), source)
                self.assertEqual(result.get('dll_metadata'), {'module_mvid': SYNTHETIC_MVID,
                                                             'verification': 'pe_cli_metadata'})

    def test_mvid_provenance_is_exactly_bound_to_mode(self):
        for mode, sources in (('assembly_probe', ('loaded_pe_metadata', '', 'unknown')),
                              ('device', ('runtime_api', '', 'unknown'))):
            for source in sources:
                with self.subTest(mode=mode, source=source):
                    self.value = provider_recording(self.root, mode)
                    self.value['manifest']['mvid_source'] = source
                    self.change_identity(mvid_source=source)
                    if mode == 'device':
                        refresh_capture_log(self.root, self.value)
                    self.blocked()
        self.value = provider_recording(self.root)
        self.change_identity(mvid_source='loaded_pe_metadata')
        self.blocked()

    def test_non_pe_dll_redeclaration_still_blocks(self):
        path = self.root/self.value['dll_path']
        path.write_bytes(b'not PE, despite mutually matching declarations')
        new_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        self.value['manifest']['dll_sha256'] = new_hash
        self.change_identity(dll_sha256=new_hash)
        self.blocked()

    def test_dll_is_streamed_and_size_limited(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('DLL must be streamed')):
            self.assertEqual(self.inspect()['status'], 'PASS')
        with patch('ai_test_agent.m3_handshake.MAX_DLL_BYTES', 4):
            self.blocked()

    def test_paths_must_resolve_inside_evidence_root(self):
        for path in ('../outside.dll', str(self.root.parent / 'outside.dll'), '.', ''):
            with self.subTest(path=path):
                self.value['dll_path'] = path
                self.blocked()

    def test_identity_matches_every_manifest_field(self):
        for field, value in [('schema', True), ('runtime_kind', 'android_player'),
                             ('build_id', 'foreign'), ('assembly_name', 'ForeignAssembly'),
                             ('dll_sha256', 'd' * 64),
                             ('module_mvid', '12345678-1234-4567-89ab-123456789abd')]:
            with self.subTest(field=field):
                self.value = provider_recording(self.root)
                self.change_identity(**{field: value})
                self.blocked()

    def test_identity_json_duplicate_unknown_nonfinite_and_multiline_block(self):
        original = self.value['identity_line']
        for value in [original.replace('"schema": 2', '"schema": 2,"schema": 2'),
                      original[:-1] + ',"unknown":1}', original[:-1] + ',"unknown":NaN}',
                      original + '\n', original + IDENTITY_MARKER + '{}',
                      IDENTITY_MARKER + '[' * 2000 + ']' * 2000, 'not an identity line']:
            with self.subTest(value=value[:80]):
                self.value['identity_line'] = value
                self.blocked()

    def test_probe_rejects_device_context(self):
        for field, value in [('runtime_kind', 'android_player'), ('resource_version', '1'),
                             ('server', 'test'), ('artifact', {})]:
            with self.subTest(field=field):
                self.value = provider_recording(self.root)
                self.value['manifest'][field] = value
                self.blocked()
        for field, value in [('observation_line', 'unexpected'), ('transport', {})]:
            self.value = provider_recording(self.root)
            self.value[field] = value
            self.blocked()

    def test_device_requires_full_manifest_and_transport(self):
        for path, field, value in [([], 'transport', None), ([], 'observation_line', None),
            (['manifest'], 'runtime_kind', 'editor_probe'), (['manifest'], 'resource_version', ''),
            (['manifest'], 'server', ''), (['manifest'], 'artifact', None),
            (['manifest', 'artifact'], 'sha256', 'x'), (['manifest', 'artifact'], 'version_code', True),
            (['manifest', 'artifact'], 'package', 'bad-package'),
            (['transport'], 'unexpected', 1), (['transport'], 'pid', True),
            (['transport'], 'pid', 0), (['transport'], 'device_alias', ''),
            (['transport'], 'installed_proof', '../outside.json')]:
            with self.subTest(path=path, field=field):
                self.value = provider_recording(self.root, 'device')
                parent = self.value
                for key in path:
                    parent = parent[key]
                parent[field] = value
                self.blocked()

    def test_device_log_prefix_pid_and_thread_id_are_bound(self):
        for field in ('identity_line', 'observation_line'):
            for prefix in ['', '10-16 10:00:00.010 124 123 I Unity: ',
                           '10-16 10:00:00.010 123 123 I Other: ',
                           '10-16 10:00:00.010 123 2147483648 I Unity: ']:
                with self.subTest(field=field, prefix=prefix):
                    self.value = provider_recording(self.root, 'device')
                    marker = IDENTITY_MARKER if field == 'identity_line' else 'AI_TEST_OBSERVATION_JSON:'
                    self.value[field] = prefix + marker + self.value[field].split(marker)[1]
                    self.blocked()

    def test_device_timestamps_require_short_bracket_and_identity_before_observation(self):
        for field, value in [('log_start', '10-16 10:00:00.015'),
                             ('log_end', '10-16 10:00:00.015'),
                             ('log_end', '10-16 10:00:10.001'),
                             ('log_start', '02-30 10:00:00.000')]:
            with self.subTest(field=field):
                self.value = provider_recording(self.root, 'device')
                self.value['transport'][field] = value
                self.blocked()
        self.value = provider_recording(self.root, 'device')
        self.value['observation_line'] = self.value['observation_line'].replace('.020', '.005', 1)
        self.blocked()

    def test_device_year_boundary_bracket_is_valid(self):
        self.value = provider_recording(self.root, 'device')
        self.value['transport'].update(log_start='12-31 23:59:59.900', log_end='01-01 00:00:00.100')
        self.value['identity_line'] = identity_line(self.identity(), timestamp='12-31 23:59:59.950')
        state = json.loads(self.value['observation_line'].split('AI_TEST_OBSERVATION_JSON:')[1])
        self.value['observation_line'] = line(state, timestamp='01-01 00:00:00.050')
        refresh_capture_log(self.root, self.value)
        self.assertEqual(self.inspect()['status'], 'PASS')

    def test_device_requires_capture_log_exact_shape_bytes_and_hash(self):
        for field, value in [('path', '../outside.log'), ('path', 'missing.log'),
                             ('bytes', True), ('bytes', 1), ('sha256', 'd' * 64),
                             ('unexpected', 1)]:
            with self.subTest(field=field, value=value):
                self.value = provider_recording(self.root, 'device')
                self.value['transport']['log_evidence'][field] = value
                self.blocked()
        self.value = provider_recording(self.root, 'device')
        del self.value['transport']['log_evidence']
        self.blocked()
        self.value = provider_recording(self.root, 'device')
        (self.root / self.value['transport']['log_evidence']['path']).unlink()
        self.blocked()

    def test_supplied_device_lines_must_match_complete_captured_lines_in_order(self):
        for mutation in ('identity', 'observation', 'reversed', 'partial', 'duplicate'):
            with self.subTest(mutation=mutation):
                self.value = provider_recording(self.root, 'device')
                left, right = self.value['identity_line'], self.value['observation_line']
                content = {
                    'identity': left.replace('synthetic-build-stamp', 'different-build') + '\n' + right,
                    'observation': left + '\n' + right.replace('synthetic-resource', 'different-resource'),
                    'reversed': right + '\n' + left,
                    'partial': 'wrapped-prefix ' + left + '\n' + right + ' trailing-text',
                    'duplicate': left + '\n' + left + '\n' + right,
                }[mutation].encode('utf-8')
                path = self.root / self.value['transport']['log_evidence']['path']
                path.write_bytes(content)
                self.value['transport']['log_evidence'].update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
                self.blocked()

    def test_capture_log_hash_and_line_bindings_allow_crlf_and_unrelated_lines(self):
        self.value = provider_recording(self.root, 'device')
        content = ('other captured line\r\n' + self.value['identity_line'] + '\r\n'
                   + self.value['observation_line'] + '\r\nlast captured line').encode('utf-8')
        path = self.root / self.value['transport']['log_evidence']['path']
        path.write_bytes(content)
        self.value['transport']['log_evidence'].update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.assertEqual(self.inspect()['status'], 'PASS')

    def test_device_observation_provider_and_source_are_exact(self):
        for field, value in [('provider', 'foreign'), ('build_id', 'foreign'),
                             ('resource_version', 'foreign'), ('dll_version', 'foreign'), ('server', 'foreign')]:
            with self.subTest(field=field):
                self.value = provider_recording(self.root, 'device')
                state = json.loads(self.value['observation_line'].split('AI_TEST_OBSERVATION_JSON:')[1])
                (state if field == 'provider' else state['source'])[field] = value
                self.value['observation_line'] = line(state, timestamp='10-16 10:00:00.020')
                self.blocked()

    def test_missing_or_partial_m2_proof_blocks(self):
        for proof in [{}, {'status': 'PASS'}, {'schema_version': 1, 'status': 'PASS', 'environment': 'test'}]:
            self.value = provider_recording(self.root, 'device')
            self.save_proof(proof)
            self.blocked()
        self.value = provider_recording(self.root, 'device')
        (self.root / self.value['transport']['installed_proof']).unlink()
        self.blocked()

    def test_m2_context_and_installation_are_required(self):
        invalid = [([], 'status', 'FAIL'), ([], 'environment', 'production'),
            ([], 'device_alias', 'other'), ([], 'package', 'com.example.other'),
            (['installed_package'], 'version_code', 2), (['installed_package'], 'version_name', 'other'),
            (['installed_environment_evidence'], 'verified', 1),
            (['artifact_verification'], 'status', 'BLOCKED'),
            (['artifact_verification'], 'sha256', 'd' * 64),
            (['artifact_verification', 'environment_evidence'], 'matched', False),
            ([], 'installed_apk_hashes_after', {}),
            ([], 'installed_apk_hashes_after', {'base.apk': 'd' * 64}),
            ([], 'installed_apk_hashes_after', {'base.apk': 'c' * 64}),
            ([], 'observations', [{'pid': '124'}]), ([], 'evidence', [])]
        for path, field, value in invalid:
            with self.subTest(path=path, field=field):
                self.value = provider_recording(self.root, 'device')
                proof = self.proof()
                parent = proof
                for key in path:
                    parent = parent[key]
                parent[field] = value
                self.save_proof(proof)
                self.blocked()

    def test_referenced_m2_evidence_is_hashed_and_contained(self):
        for mutation in ('bytes', 'path', 'relative_path', 'sha256'):
            with self.subTest(mutation=mutation):
                self.value = provider_recording(self.root, 'device')
                proof = self.proof()
                item = proof['evidence'][0]
                item[mutation] = 1 if mutation == 'bytes' else ('../outside.log' if mutation != 'sha256' else 'd' * 64)
                self.save_proof(proof)
                self.blocked()
        self.value = provider_recording(self.root, 'device')
        (self.root / 'synthetic-m2.log').write_bytes(b'mutated evidence')
        self.blocked()

    def test_m2_empty_command_output_is_rehashed_and_allowed(self):
        self.value = provider_recording(self.root, 'device')
        proof = self.proof()
        path = self.root / 'command-empty-stderr.bin'
        path.write_bytes(b'')
        proof['evidence'].append({'kind': 'command_stderr', 'path': str(path),
                                  'relative_path': path.name, 'bytes': 0,
                                  'sha256': hashlib.sha256(b'').hexdigest()})
        self.save_proof(proof)
        result = self.inspect()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['verified_files'][-1]['bytes'], 0)
        self.assertEqual(result['verified_files'][-1]['sha256'], hashlib.sha256(b'').hexdigest())

    def test_m2_empty_output_still_requires_exact_digest_and_size(self):
        for declared_bytes, declared_hash, content in [
                (0, 'd' * 64, b''), (0, hashlib.sha256(b'').hexdigest(), b'changed'),
                (True, hashlib.sha256(b'').hexdigest(), b''),
                (-1, hashlib.sha256(b'').hexdigest(), b'')]:
            with self.subTest(bytes=declared_bytes, content=content):
                self.value = provider_recording(self.root, 'device')
                proof = self.proof()
                path = self.root / 'command-empty-stderr.bin'
                path.write_bytes(content)
                proof['evidence'].append({'kind': 'command_stderr', 'path': str(path),
                                          'relative_path': path.name, 'bytes': declared_bytes,
                                          'sha256': declared_hash})
                self.save_proof(proof)
                self.blocked()

    def test_duplicate_m2_json_fields_block(self):
        self.value = provider_recording(self.root, 'device')
        path = self.root / self.value['transport']['installed_proof']
        path.write_text(path.read_text(encoding='utf-8').replace('"status": "PASS"', '"status": "PASS","status":"PASS"', 1), encoding='utf-8')
        self.blocked()

    def test_report_retains_truthful_scope_and_uses_atomic_replacement(self):
        result = self.inspect()
        from ai_test_agent.m3_handshake import write_provider_report
        path = self.root / 'report.md'
        path.write_text('previous report', encoding='utf-8')
        write_provider_report(result, path)
        text = path.read_text(encoding='utf-8')
        for expected in ('compiled_assembly_identity', 'synthetic', 'NOT_EVALUATED', 'device_free', 'False'):
            self.assertIn(expected, text)
        self.assertEqual(list(self.root.glob('.*.tmp')), [])


class PeModuleIdentityTests(unittest.TestCase):
    def read(self, value):
        try:
            from ai_test_agent.pe_identity import read_mvid
        except ModuleNotFoundError:
            self.fail('Bounded metadata-only PE MVID reader is not implemented')
        return read_mvid(value)

    def rejected(self, value):
        with self.assertRaises(ValueError):
            self.read(value)

    def test_pe32_pe32_plus_and_unoptimized_tables_use_guid_bytes_le(self):
        for plus in (False, True):
            for tables in ('#~', '#-'):
                with self.subTest(plus=plus, tables=tables):
                    self.assertEqual(SYNTHETIC_MVID, self.read(synthetic_metadata_pe(pe32_plus=plus, tables=tables)))

    def test_wide_heaps_and_nonfirst_guid_are_supported(self):
        for strings, guids in ((2, 4), (4, 2), (4, 4)):
            with self.subTest(strings=strings, guids=guids):
                self.assertEqual(SYNTHETIC_MVID, self.read(synthetic_metadata_pe(
                    string_width=strings, guid_width=guids, guid_index=2, guid_count=2)))

    def test_table_extra_data_precedes_module_rows(self):
        for plus in (False, True):
            for tables in ('#~', '#-'):
                for strings, guids in ((2, 2), (2, 4), (4, 2), (4, 4)):
                    for extra in (0, 0x12345678):
                        with self.subTest(plus=plus, tables=tables, strings=strings, guids=guids, extra=extra):
                            self.assertEqual(SYNTHETIC_MVID, self.read(synthetic_metadata_pe(
                                pe32_plus=plus, tables=tables, string_width=strings, guid_width=guids,
                                guid_index=2, guid_count=2, extra_data=extra)))

    def test_truncated_table_extra_data_and_following_module_are_rejected(self):
        original = synthetic_metadata_pe(extra_data=0x12345678)
        # The row count ends at 28; ExtraData occupies 28..31, then the Module row.
        for size in (28, 29, 30, 31, 32, 33, 41):
            with self.subTest(table_size=size):
                content = bytearray(original)
                struct.pack_into('<I', content, 0x280+36, size)
                self.rejected(content)

    def test_invalid_headers_and_every_truncation_boundary_are_rejected(self):
        source = synthetic_metadata_pe()
        for length in (0, 2, 0x3f, 0x83, 0x97, 0x177, 0x19f, 0x247, 0x28f, 0x2bb, 0x32f):
            with self.subTest(length=length):
                self.rejected(source[:length])
        for offset in (0, 0x80, 0x280):
            content = bytearray(source)
            content[offset] ^= 0xff
            self.rejected(content)
        for offset, width, replacement in ((0x3c, 'I', 0xfffffff0), (0x98, 'H', 0x999),
                (0x98+92, 'I', 14), (0x98+96+14*8, 'I', 0), (0x200, 'I', 71),
                (0x20c, 'I', 0xfffffff0), (0x280+12, 'I', 0xfffffff0)):
            content = bytearray(source)
            struct.pack_into('<'+width, content, offset, replacement)
            self.rejected(content)

    def test_ambiguous_section_mapping_and_raw_unbacked_cli_are_rejected(self):
        source = bytearray(synthetic_metadata_pe())
        section = 0x98+224
        struct.pack_into('<H', source, 0x86, 2)
        source[section+40:section+80] = source[section:section+40]
        self.rejected(source)
        source = bytearray(synthetic_metadata_pe())
        struct.pack_into('<H', source, 0x86, 2)
        source[section+40:section+80] = source[section:section+40]
        # Metadata starts uniquely in the first section, but its middle aliases
        # a second section. Checking only the starting RVA would miss this.
        struct.pack_into('<IIII', source, section+40+8, 16, 0x2090, 16, 0x300)
        self.rejected(source)
        source = bytearray(synthetic_metadata_pe())
        struct.pack_into('<I', source, section+16, 32)
        self.rejected(source)

    def test_duplicate_ambiguous_overlapping_and_header_backed_streams_are_rejected(self):
        original = synthetic_metadata_pe()
        for case in ('duplicate_guid', 'both_tables', 'overlap', 'header', 'unbounded', 'unaligned_guid'):
            with self.subTest(case=case):
                content = bytearray(original)
                if case in ('duplicate_guid', 'both_tables'):
                    struct.pack_into('<H', content, 0x280+30, 3)
                    struct.pack_into('<II', content, 0x280+60, 0x100, 16)
                    name = b'#GUID\0\0\0' if case == 'duplicate_guid' else b'#-\0\0'
                    content[0x280+68:0x280+68+len(name)] = name
                    struct.pack_into('<I', content, 0x20c, 0x180)
                else:
                    offset, size = {'overlap': (0x60, 16), 'header': (0x20, 16),
                                    'unbounded': (0xa0, 0xfffffff0), 'unaligned_guid': (0xa0, 15)}[case]
                    struct.pack_into('<II', content, 0x280+44, offset, size)
                self.rejected(content)

    def test_module_row_count_guid_index_and_empty_mvid_are_rejected(self):
        missing = bytearray(synthetic_metadata_pe())
        struct.pack_into('<Q', missing, 0x280+0x60+8, 0)
        self.rejected(missing)
        for count in (0, 2):
            content = bytearray(synthetic_metadata_pe())
            struct.pack_into('<I', content, 0x280+0x60+24, count)
            self.rejected(content)
        for index in (0, 2):
            self.rejected(synthetic_metadata_pe(guid_index=index))
        self.rejected(synthetic_metadata_pe(mvid='00000000-0000-0000-0000-000000000000'))
        for size in (23, 27, 35):
            content = bytearray(synthetic_metadata_pe())
            struct.pack_into('<I', content, 0x280+36, size)
            self.rejected(content)

    def test_non_pe_data_types_and_bounded_input_are_rejected(self):
        for data in (None, 42, 'MZ', b'not an assembly'):
            self.rejected(data)
        try:
            import ai_test_agent.pe_identity as module
        except ModuleNotFoundError:
            self.fail('Bounded metadata-only PE MVID reader is not implemented')
        with patch.object(module, 'MAX_PE_BYTES', 1024):
            self.rejected(synthetic_metadata_pe())


if __name__ == '__main__':
    unittest.main()
