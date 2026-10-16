import hashlib
import importlib
import json
import shutil
import unittest
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from ai_test_agent.m0 import M0Registry
from ai_test_agent.m2 import M2Runner, config_digest
from test_contracts import png_bytes
from test_m2 import Clock, Device, approved_artifact, contract


class M2TraceTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('ai_test_agent.m2_trace')
        except ModuleNotFoundError:
            self.fail('M2 trace export is not implemented')
        self.root = Path(__file__).resolve().parents[1] / '.local' / ('m2-trace-test-' + uuid4().hex)
        self.root.mkdir(parents=True)
        self.config = contract(self.root)
        self.run_dir = self.root / 'runs' / 'run-actual' / 'reference'
        self.run_dir.mkdir(parents=True)
        self.png = self.run_dir / 'ready.png'
        self.png.write_bytes(png_bytes(2, 3))
        self.log = self.run_dir / 'trace.jsonl'
        self.log.write_text('{"event":"startup_ready"}\n', encoding='utf-8')
        self.result = {
            'schema_version': 1, 'run_id': 'run-actual', 'attempt_id': 'attempt-actual',
            'case_id': self.config['case_id'], 'scope': 'startup_readiness', 'status': 'PASS',
            'category': 'STARTUP_READY', 'business_status': 'NOT_EVALUATED', 'environment': 'test',
            'device_alias': 'reference', 'package': 'com.example.game',
            'config_sha256': config_digest(self.config),
            'started_at': '2026-10-16T01:00:00Z', 'finished_at': '2026-10-16T01:00:04Z',
            'run_dir': str(self.run_dir), 'result_path': str(self.run_dir / 'result.json'),
            'evidence': [self.evidence(self.png, 'screenshot'), self.evidence(self.log, 'trace')],
            'artifact_verification': approved_artifact(self.config['apks_path'], self.config['expected'],
                                                       self.config['environment_policy'], self.run_dir),
            'installed_package': {'version_code': 2, 'version_name': '2.0', 'min_sdk': 24, 'target_sdk': 35},
            'observations': [], 'reasons': [],
        }
        self.output = self.root / 'handoff.json'

    def tearDown(self):
        if hasattr(self, 'root'):
            shutil.rmtree(self.root)

    def evidence(self, path, kind):
        data = path.read_bytes()
        return {'kind': kind, 'path': str(path), 'relative_path': path.relative_to(self.run_dir).as_posix(),
                'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}

    def export(self):
        Path(self.result['result_path']).write_text(json.dumps(self.result), encoding='utf-8')
        return self.api.export_m2_trace(self.config, self.result, self.output)

    def test_pass_exports_valid_draft_registry_and_exact_startup_lineage(self):
        exported = self.export()
        self.assertEqual('READY_FOR_M0_REVIEW', exported['status'])
        self.assertEqual('PASS', exported['m2_status'])
        self.assertEqual('startup_readiness', exported['scope'])
        self.assertEqual('NOT_EVALUATED', exported['business_status'])
        self.assertEqual(exported, json.loads(self.output.read_text(encoding='utf-8')))
        self.assertEqual(self.result['config_sha256'], exported['config_sha256'])
        chain = exported['chain']
        self.assertEqual(self.config['source_ref'], chain['source_ref'])
        self.assertEqual(self.config['readiness']['source_ref'], chain['readiness_source_ref'])
        self.assertEqual(self.result['case_id'], chain['case_id'])
        self.assertEqual('run-actual', chain['run_id'])
        self.assertEqual('attempt-actual', chain['attempt_id'])
        self.assertEqual(self.result['evidence'][0]['sha256'], chain['evidence'][0]['sha256'])
        registry = M0Registry(exported['registry'])
        self.assertTrue(registry.validate(evidence_root=self.run_dir))
        case = registry.data['cases'][self.result['case_id']]
        self.assertFalse(case['approved'])
        for key in ('cost', 'non_upgrade_delta', 'level_delta', 'target_id', 'window_id'):
            self.assertNotIn(key, case['expectation'])
        self.assertFalse(exported['m0_run_imported'])
        self.assertEqual({}, registry.data['runs'])
        self.assertEqual({}, registry.data['evidence'])
        self.assertEqual(0, exported['m0_report']['coverage']['executed_cases'])
        self.assertEqual({}, registry.data['defects'])
        self.assertTrue(exported['unresolved_bindings'])

    def test_startup_failure_is_preserved_without_creating_game_bug(self):
        self.result.update(status='FAIL', category='PROCESS_RESTART', reasons=['PID changed'])
        exported = self.export()
        self.assertEqual('FAIL', exported['m2_status'])
        self.assertEqual('run-actual', exported['chain']['run_id'])
        self.assertFalse(exported['m0_run_imported'])
        self.assertEqual({}, exported['registry']['runs'])
        self.assertEqual({}, exported['registry']['defects'])
        self.assertEqual('NOT_EVALUATED', exported['business_status'])

    def test_blocked_run_keeps_real_chain_but_does_not_invent_executed_m0_run(self):
        self.result.update(status='BLOCKED', category='READINESS_TIMEOUT', reasons=['No reviewed ready signal'])
        exported = self.export()
        self.assertEqual('BLOCKED', exported['status'])
        self.assertEqual('run-actual', exported['chain']['run_id'])
        self.assertEqual(2, len(exported['chain']['evidence']))
        self.assertEqual({}, exported['registry']['runs'])
        self.assertEqual({}, exported['registry']['evidence'])
        self.assertEqual({}, exported['registry']['defects'])
        self.assertTrue(M0Registry(exported['registry']).validate())

    def test_unapproved_or_incomplete_human_review_blocks_m0_run(self):
        original = deepcopy(self.config['readiness'])
        for key, value in (('approved', False), ('approved', 'true'), ('reviewer', ''),
                           ('reviewed_at', ''), ('reviewed_at', 'not-a-date'), ('source_ref', '')):
            with self.subTest(key=key, value=value):
                self.config['readiness'] = dict(original, **{key: value})
                self.result['config_sha256'] = config_digest(self.config)
                exported = self.export()
                self.assertEqual('BLOCKED', exported['status'])
                self.assertEqual({}, exported['registry']['runs'])
                self.assertTrue(exported['unresolved_bindings'])
                self.assertTrue(M0Registry(exported['registry']).validate())

    def test_configuration_fingerprint_mismatch_does_not_write_output(self):
        self.result['config_sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_evidence_hash_size_and_existence_are_verified_from_files(self):
        original = deepcopy(self.result['evidence'])
        for key, value in (('sha256', 'f' * 64), ('bytes', 0), ('bytes', True)):
            with self.subTest(key=key):
                self.result['evidence'] = deepcopy(original)
                self.result['evidence'][0][key] = value
                with self.assertRaises(ValueError):
                    self.export()
                self.assertFalse(self.output.exists())
        self.result['evidence'] = original
        self.png.unlink()
        with self.assertRaises(ValueError):
            self.export()

    def test_png_is_decoded_even_when_hash_and_size_match_corrupt_file(self):
        self.png.write_bytes(b'\x89PNG\r\n\x1a\ntruncated')
        self.result['evidence'][0] = self.evidence(self.png, 'screenshot')
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_evidence_paths_must_be_absolute_confined_and_match_relative_path(self):
        original = deepcopy(self.result['evidence'])
        outside = self.root / 'other.png'
        outside.write_bytes(png_bytes())
        for key, value in (('relative_path', '../other.png'), ('relative_path', str(self.png)),
                           ('relative_path', 'missing.png'), ('path', 'ready.png'), ('path', str(outside))):
            with self.subTest(key=key, value=value):
                self.result['evidence'] = deepcopy(original)
                self.result['evidence'][0][key] = value
                with self.assertRaises(ValueError):
                    self.export()
        self.assertFalse(self.output.exists())

    def test_duplicate_evidence_binding_is_rejected(self):
        self.result['evidence'].append(deepcopy(self.result['evidence'][0]))
        with self.assertRaises(ValueError):
            self.export()

    def test_result_bindings_cannot_claim_business_or_another_build_case_or_device(self):
        original = deepcopy(self.result)
        for key, value in (('scope', 'upgrade'), ('business_status', 'PASS'), ('environment', 'production'),
                           ('case_id', 'other-case'), ('package', 'other.game'), ('device_alias', 'other-device')):
            with self.subTest(key=key):
                self.result = dict(deepcopy(original), **{key: value})
                with self.assertRaises(ValueError):
                    self.export()

    def test_artifact_verification_mismatch_blocks_handoff_without_game_defect(self):
        self.result['artifact_verification']['sha256'] = 'f' * 64
        exported = self.export()
        self.assertEqual('BLOCKED', exported['status'])
        self.assertEqual({}, exported['registry']['runs'])
        self.assertEqual({}, exported['registry']['defects'])
        self.assertTrue(exported['unresolved_bindings'])

    def test_uppercase_approved_hashes_match_lowercase_observed_hashes(self):
        self.config['expected']['sha256'] = self.config['expected']['sha256'].upper()
        self.config['environment_policy']['asset_sha256'] = self.config['environment_policy']['asset_sha256'].upper()
        self.result['config_sha256'] = config_digest(self.config)
        exported = self.export()
        self.assertEqual('READY_FOR_M0_REVIEW', exported['status'])

    def test_invalid_review_field_types_produce_blocked_handoff(self):
        original = deepcopy(self.config['readiness'])
        for key in ('reviewer', 'reviewed_at'):
            with self.subTest(key=key):
                self.config['readiness'] = dict(original, **{key: True})
                self.result['config_sha256'] = config_digest(self.config)
                try:
                    exported = self.export()
                except ValueError as error:
                    self.fail(f'Invalid approval metadata should be a BLOCKED handoff: {error}')
                self.assertEqual('BLOCKED', exported['status'])
                self.assertEqual({}, exported['registry']['runs'])

    def test_export_refuses_to_overwrite_run_result_or_original_evidence(self):
        for path in (self.png, Path(self.result['result_path'])):
            with self.subTest(path=path):
                self.output = path
                with self.assertRaises(ValueError):
                    self.export()
        self.assertEqual(png_bytes(2, 3), self.png.read_bytes())

    def test_result_file_must_match_the_result_being_exported(self):
        self.export()
        self.output.unlink()
        self.result['status'] = 'FAIL'
        with self.assertRaises(ValueError):
            self.api.export_m2_trace(self.config, self.result, self.output)
        self.assertFalse(self.output.exists())

    def test_real_m2_runner_result_exports_using_only_simulated_devices(self):
        clock = Clock()
        result = M2Runner(self.config, device=Device(clock), artifact_verifier=approved_artifact,
                          clock=clock.monotonic, sleep=clock.sleep).run()
        exported = self.api.export_m2_trace(self.config, result, self.output)
        self.assertEqual('PASS', result['status'], result)
        self.assertEqual(result['run_id'], exported['chain']['run_id'])
        self.assertEqual(result['attempt_id'], exported['chain']['attempt_id'])
        self.assertFalse(exported['m0_run_imported'])
        self.assertEqual({}, exported['registry']['runs'])
        self.assertTrue(M0Registry(exported['registry']).validate(evidence_root=result['run_dir']))


if __name__ == '__main__':
    unittest.main()
