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
        draft.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.assertEqual(main(['analyze-m1', str(source), '--output', str(draft)]), 0)
            self.assertEqual(main(['review-m1', str(draft), '--output', str(reviewed), '--reviewer', 'qa-owner', '--decision', 'changes_requested', '--comment', 'bind exact expectation']), 0)
            result = json.loads(reviewed.read_text(encoding='utf-8'))
            self.assertEqual(result['review']['decision'], 'changes_requested')
            self.assertTrue(all(item['review_status'] == 'CHANGES_REQUESTED' for item in result['cases']))
        finally:
            for path in (draft, reviewed):
                if path.exists():
                    path.unlink()
