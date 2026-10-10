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
