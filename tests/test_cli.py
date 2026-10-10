import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from ai_test_agent.__main__ import main


class CliTests(unittest.TestCase):
    def test_assessment_rejects_nonobject_json_with_structured_block(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'input.json'
            source.write_text('[]', encoding='utf-8')
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = main(['assess-upgrade', str(source), '--output', str(Path(temp) / 'result.json')])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(captured.getvalue())['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
