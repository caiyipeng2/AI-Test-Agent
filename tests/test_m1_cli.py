import json
import unittest
from pathlib import Path

from ai_test_agent.__main__ import main


class M1CliTests(unittest.TestCase):
    def test_analyze_and_review_cli_are_device_free(self):
        root = Path(__file__).resolve().parents[1]
        source = root / 'fixtures' / 'm1-source.example.json'
        draft = root / '.local' / 'm1-draft-test.json'
        reviewed = root / '.local' / 'm1-reviewed-test.json'
        promoted = root / '.local' / 'm1-promoted-test.json'
        imported = root / '.local' / 'm1-imported-test.json'
        draft.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.assertEqual(main(['analyze-m1', str(source), '--output', str(draft)]), 0)
            self.assertEqual(main(['import-m1', str(draft), '--output', str(imported)]), 0)
            self.assertEqual(main(['review-m1', str(draft), '--output', str(reviewed), '--reviewer', 'qa-owner', '--decision', 'changes_requested', '--comment', 'bind exact expectation']), 0)
            self.assertEqual(main(['promote-m1', str(reviewed), '--output', str(promoted)]), 0)
            result = json.loads(reviewed.read_text(encoding='utf-8'))
            self.assertEqual(result['review']['decision'], 'changes_requested')
            self.assertTrue(all(item['review_status'] == 'CHANGES_REQUESTED' for item in result['cases']))
            handoff = json.loads(promoted.read_text(encoding='utf-8'))
            self.assertEqual(handoff['status'], 'BLOCKED')
            self.assertIn('build_id', handoff['unresolved_bindings'])
        finally:
            for path in (draft, reviewed, promoted, imported):
                if path.exists():
                    path.unlink()

    def test_manual_result_and_gap_report_cli_are_device_free(self):
        root = Path(__file__).resolve().parents[1]
        source = root / 'fixtures' / 'm1-source.example.json'
        draft = root / '.local' / 'm1-draft-result-test.json'
        manual = root / '.local' / 'm1-manual-result-test.json'
        recorded = root / '.local' / 'm1-recorded-result-test.json'
        defects = root / '.local' / 'm1-defects-test.json'
        report = root / '.local' / 'm1-gap-report-test.json'
        draft.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.assertEqual(main(['analyze-m1', str(source), '--output', str(draft)]), 0)
            analysis = json.loads(draft.read_text(encoding='utf-8'))
            case = analysis['cases'][0]
            assertion = case['expectation']['assertions'][0]
            manual.write_text(json.dumps({
                'case_id': case['case_id'],
                'run_id': 'manual-cli-run',
                'source_result_digest': analysis['provenance']['output_digest'],
                'decision': 'BLOCKED',
                'reviewer': 'qa-owner',
                'comment': 'device execution is deferred to M3',
                'observations': [{
                    'assertion_id': assertion['assertion_id'],
                    'status': 'BLOCKED',
                    'actual': 'not executed',
                    'evidence_ids': ['manual-note-1'],
                }],
                'evidence_ids': ['manual-note-1'],
                'created_at': '2026-10-10T00:00:00+00:00',
            }, ensure_ascii=False), encoding='utf-8')
            defects.write_text('[]', encoding='utf-8')
            self.assertEqual(main(['record-m1-result', str(draft), str(manual), '--output', str(recorded)]), 0)
            self.assertEqual(main(['report-m1-gaps', str(recorded), '--defects', str(defects), '--output', str(report)]), 2)
            recorded_data = json.loads(recorded.read_text(encoding='utf-8'))
            report_data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(recorded_data['result_history'][0]['decision'], 'BLOCKED')
            self.assertEqual(report_data['planned_cases'], len(analysis['cases']))
            self.assertIn('unreviewed_cases', report_data)
            self.assertEqual(report_data['manual_result_summary']['blocked'], 1)
        finally:
            for path in (draft, manual, recorded, defects, report):
                if path.exists():
                    path.unlink()
