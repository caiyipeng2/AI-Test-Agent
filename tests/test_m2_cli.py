import contextlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai_test_agent.__main__ import main
from ai_test_agent.m2 import M2Runner
from test_m2 import Clock, Device, approved_artifact, contract


class M2CliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '.local' / ('m2-cli-' + uuid4().hex)
        self.root.mkdir(parents=True)
        self.config = contract(self.root)
        self.input = self.root / 'contract.json'
        self.input.write_text(json.dumps(self.config), encoding='utf-8')
        self.clock = Clock()
        self.device = Device(self.clock)

    def tearDown(self):
        shutil.rmtree(self.root)

    def invoke(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = main(argv)
            except SystemExit as error:
                code = error.code
        return code, json.loads(output.getvalue()) if output.getvalue() else {}

    def smoke(self):
        def factory(config):
            return M2Runner(config, device=self.device, artifact_verifier=approved_artifact,
                            clock=self.clock.monotonic, sleep=self.clock.sleep)
        with patch('ai_test_agent.m2.M2Runner', side_effect=factory):
            return self.invoke(['m2-smoke', '--contract', str(self.input)])

    def test_smoke_runs_contract_and_exports_evidence_verified_handoff(self):
        code, summary = self.smoke()
        self.assertEqual(0, code, summary)
        self.assertEqual('PASS', summary['status'])
        self.assertEqual('NOT_EVALUATED', summary['business_status'])
        self.assertEqual('READY_FOR_M0_REVIEW', summary['m0_handoff_status'])
        handoff = json.loads(Path(summary['handoff_path']).read_text(encoding='utf-8'))
        self.assertFalse(handoff['m0_run_imported'])
        self.assertEqual(summary['run_id'], handoff['chain']['run_id'])

    def test_report_is_device_free_and_exports_scoped_markdown(self):
        _, run = self.smoke()
        self.assertIn('result_path', run)
        output = self.root / 'review.json'
        with patch('ai_test_agent.android.AndroidDevice', side_effect=AssertionError('no device access')):
            code, summary = self.invoke(['m2-report', run['result_path'], '--contract', str(self.input), '--output', str(output)])
        self.assertEqual(0, code, summary)
        self.assertEqual('PASS', summary['m2_status'])
        self.assertIn('NOT_EVALUATED', output.with_suffix('.md').read_text(encoding='utf-8'))

    def test_report_rejects_changed_evidence(self):
        _, run = self.smoke()
        self.assertIn('result_path', run)
        result = json.loads(Path(run['result_path']).read_text(encoding='utf-8'))
        Path(result['evidence'][0]['path']).write_bytes(b'changed')
        output = self.root / 'review.json'
        code, summary = self.invoke(['m2-report', run['result_path'], '--contract', str(self.input), '--output', str(output)])
        self.assertEqual(2, code)
        self.assertEqual('BLOCKED', summary['status'])
        self.assertFalse(output.exists())

    def test_smoke_returns_blocked_exit_and_handoff_when_readiness_is_unapproved(self):
        self.config['readiness']['approved'] = False
        self.input.write_text(json.dumps(self.config), encoding='utf-8')
        code, summary = self.smoke()
        self.assertEqual(2, code)
        self.assertEqual('BLOCKED', summary.get('m0_handoff_status'), summary)
        self.assertEqual([], self.device.actions)

    def test_invalid_contract_does_not_access_device(self):
        self.input.write_text('{"schema_version":1}', encoding='utf-8')
        with patch('ai_test_agent.android.AndroidDevice', side_effect=AssertionError('no device access')):
            code, summary = self.invoke(['m2-smoke', '--contract', str(self.input)])
        self.assertEqual(2, code)
        self.assertEqual('INVALID_INPUT', summary.get('category'), summary)

    def test_duplicate_keys_are_rejected(self):
        self.input.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
        code, summary = self.invoke(['m2-smoke', '--contract', str(self.input)])
        self.assertEqual(2, code)
        self.assertIn('duplicate', str(summary).lower())

    def test_report_cannot_overwrite_original_result(self):
        _, run = self.smoke()
        self.assertIn('result_path', run)
        original = Path(run['result_path']).read_bytes()
        code, _ = self.invoke(['m2-report', run['result_path'], '--contract', str(self.input), '--output', run['result_path']])
        self.assertEqual(2, code)
        self.assertEqual(original, Path(run['result_path']).read_bytes())

    def test_report_markdown_hardlink_cannot_change_original_evidence(self):
        _, run = self.smoke()
        self.assertIn('result_path', run)
        result = json.loads(Path(run['result_path']).read_text(encoding='utf-8'))
        evidence = Path(next(item['path'] for item in result['evidence'] if item['kind']=='screenshot'))
        original = evidence.read_bytes()
        output = self.root / 'review.json'
        output.with_suffix('.md').hardlink_to(evidence)
        self.invoke(['m2-report', run['result_path'], '--contract', str(self.input), '--output', str(output)])
        self.assertEqual(original, evidence.read_bytes())
