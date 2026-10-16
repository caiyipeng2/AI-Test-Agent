from copy import deepcopy
import json
import hashlib
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from m32_support import IDENTITY_MARKER, identity_line, provider_recording, refresh_capture_log
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
        for value in [original.replace('"schema": 1', '"schema": 1,"schema": 1'),
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


if __name__ == '__main__':
    unittest.main()
