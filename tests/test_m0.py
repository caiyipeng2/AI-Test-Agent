import hashlib
import json
import shutil
import uuid
import unittest
from pathlib import Path

from ai_test_agent.m0 import M0Registry, M0ValidationError


def base_registry():
    registry = M0Registry.create({'project_id': 'idle-outpost', 'name': 'Idle Outpost'})
    registry.register_source('source-1', {
        'source_ref': 'approved/user-contract', 'source_version': '2026-10-10',
        'permission': 'user-approved', 'excerpt': 'first upgrade joint acceptance',
    })
    registry.register_build('build-test', {
        'artifact_type': 'apks', 'artifact_path': 'local-only://test-package.apks',
        'artifact_sha256': 'a' * 64, 'package_name': 'com.example.game',
        'version_code': 65, 'version_name': '2.1.1', 'environment': 'test',
        'environment_source': 'approved packaged manifest',
        'runtime': {'server': 'TestServer', 'resource_version': '995', 'dll_version': 'unknown', 'verified': True},
    })
    registry.register_requirement('req-upgrade', {
        'source_id': 'source-1', 'title': 'first upgrade', 'status': 'approved',
        'acceptance_ref': 'source-1', 'version': 1,
    })
    registry.register_risk('risk-1', {'requirement_id': 'req-upgrade', 'severity': 'high', 'description': 'concurrent income'})
    registry.register_testpoint('point-1', {'requirement_id': 'req-upgrade', 'risk_id': 'risk-1', 'title': 'joint evidence'})
    registry.register_case('case-upgrade', {
        'requirement_id': 'req-upgrade', 'source_id': 'source-1',
        'source_ref': 'approved/user-contract', 'version': 1, 'approved': True,
        'scenario_id': 'scenario-upgrade', 'risk_id': 'risk-1', 'testpoint_id': 'point-1',
        'reviewer': 'test-owner', 'reviewed_at': '2026-10-10T01:00:00Z', 'max_wait_seconds': 30,
        'build_id': 'build-test', 'environment': 'test', 'device_alias': 'reference',
        'account_alias': 'test-account-1', 'initial_state': {'progress': 'new'},
        'steps': ['observe', 'single upgrade'],
        'expectation': {'target_id': 'device-1', 'window_id': 'upgrade', 'level_delta': 1,
                        'cost': '3', 'non_upgrade_delta': '0', 'approved': True},
        'recovery': {'method': 'in-game-delete-account', 'approved': True},
    })
    return registry


def workspace_temp():
    path = Path(__file__).resolve().parent.parent / f".m0-test-{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class M0RegistryTests(unittest.TestCase):
    def test_create_and_save_round_trip_preserves_schema(self):
        registry = base_registry()
        for temp in workspace_temp():
            path = temp / 'registry.json'
            registry.save(path)
            loaded = M0Registry.load(path)
            self.assertEqual(loaded.data['schema_version'], 1)
            self.assertEqual(loaded.data['project']['project_id'], 'idle-outpost')
            self.assertEqual(loaded.data['builds']['build-test']['runtime']['server'], 'TestServer')

    def test_build_requires_environment_and_runtime_verification(self):
        registry = M0Registry.create({'project_id': 'p', 'name': 'P'})
        with self.assertRaises(M0ValidationError):
            registry.register_build('b', {'artifact_type': 'apks', 'artifact_path': 'x',
                                          'artifact_sha256': 'a' * 64, 'package_name': 'com.example.game',
                                          'version_code': 1, 'version_name': '1', 'environment': 'test',
                                          'environment_source': 'source',
                                          'runtime': {'server': 'unknown', 'resource_version': '', 'dll_version': '', 'verified': False}})

    def test_run_binds_case_build_environment_account_device_and_initial_state(self):
        registry = base_registry()
        registry.start_run('run-1', {
            'attempt_id': 'attempt-1', 'case_id': 'case-upgrade', 'build_id': 'build-test',
            'environment': 'test', 'device_alias': 'reference', 'account_alias': 'test-account-1',
            'initial_state': {'progress': 'new'}, 'status': 'PLANNED',
        })
        with self.assertRaises(M0ValidationError):
            registry.start_run('run-bad', {
                'attempt_id': 'attempt-bad', 'case_id': 'case-upgrade', 'build_id': 'other',
                'environment': 'test', 'device_alias': 'reference', 'account_alias': 'test-account-1',
                'initial_state': {'progress': 'new'}, 'status': 'PLANNED',
            })
        self.assertEqual(registry.data['runs']['run-1']['status'], 'PLANNED')

    def test_collected_evidence_is_hashed_and_retention_is_explicit(self):
        registry = base_registry()
        registry.start_run('run-1', {
            'attempt_id': 'attempt-1', 'case_id': 'case-upgrade', 'build_id': 'build-test',
            'environment': 'test', 'device_alias': 'reference', 'account_alias': 'test-account-1',
            'initial_state': {'progress': 'new'}, 'status': 'PLANNED',
        })
        for temp in workspace_temp():
            evidence_path = temp / 'state.json'
            evidence_path.write_text('{"state":"new"}', encoding='utf-8')
            digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            evidence = registry.attach_evidence('run-1', {
                'evidence_id': 'evidence-1', 'attempt_id': 'attempt-1', 'kind': 'state', 'path': str(evidence_path),
                'sha256': digest, 'status': 'COLLECTED', 'retention': 'run+30d',
                'collected_at': '2026-10-10T01:00:00Z',
            })
            self.assertEqual(evidence['sha256'], digest)
            with self.assertRaises(M0ValidationError):
                registry.attach_evidence('run-1', {
                    'evidence_id': 'evidence-bad', 'attempt_id': 'attempt-1', 'kind': 'state', 'path': str(evidence_path),
                    'sha256': 'b' * 64, 'status': 'COLLECTED', 'retention': '',
                    'collected_at': '2026-10-10T01:00:00Z',
                })

    def test_trace_report_links_source_requirement_case_run_evidence_and_defect(self):
        registry = base_registry()
        registry.start_run('run-1', {
            'attempt_id': 'attempt-1', 'case_id': 'case-upgrade', 'build_id': 'build-test',
            'environment': 'test', 'device_alias': 'reference', 'account_alias': 'test-account-1',
            'initial_state': {'progress': 'new'}, 'status': 'PLANNED',
        })
        registry.register_evidence('run-1', {'evidence_id': 'evidence-planned', 'kind': 'screenshot',
                                             'attempt_id': 'attempt-1', 'path': 'local-only://before.png', 'status': 'PLANNED',
                                             'retention': 'run+30d', 'collected_at': None})
        registry.link_defect('run-1', {'defect_id': 'defect-1', 'category': 'BUSINESS_FAILURE',
                                       'status': 'DRAFT', 'title': 'example', 'confirmed': False})
        report = registry.trace_report('run-1')
        self.assertEqual(report['trace'], ['source-1', 'req-upgrade', 'point-1', 'case-upgrade', 'build-test', 'run-1', 'attempt-1'])
        self.assertEqual(report['evidence'][0]['evidence_id'], 'evidence-planned')
        self.assertEqual(report['defects'][0]['defect_id'], 'defect-1')

    def test_coverage_separates_planned_started_blocked_and_evidence_complete(self):
        registry = base_registry()
        registry.start_run('run-1', {
            'attempt_id': 'attempt-1', 'case_id': 'case-upgrade', 'build_id': 'build-test',
            'environment': 'test', 'device_alias': 'reference', 'account_alias': 'test-account-1',
            'initial_state': {'progress': 'new'}, 'status': 'BLOCKED', 'blocked_reasons': ['missing bridge'],
        })
        coverage = registry.coverage_report()
        self.assertEqual(coverage['planned_cases'], 1)
        self.assertEqual(coverage['runs_by_status']['BLOCKED'], 1)
        self.assertEqual(coverage['executed_cases'], 0)
        self.assertEqual(coverage['blocked_cases'], 1)
        self.assertEqual(coverage['evidence_complete_cases'], 0)

    def test_validation_rejects_orphan_reference_and_missing_initial_state(self):
        registry = M0Registry.create({'project_id': 'p', 'name': 'P'})
        registry.data['runs']['orphan'] = {'case_id': 'missing'}
        with self.assertRaises(M0ValidationError):
            registry.validate()


if __name__ == '__main__':
    unittest.main()
