import json
import unittest
from pathlib import Path

from ai_test_agent.__main__ import main


class M0CliTests(unittest.TestCase):
    def test_validate_and_report_m0_are_device_free(self):
        fixture = Path(__file__).resolve().parent.parent / 'fixtures' / 'm0-registry.example.json'
        self.assertEqual(main(['validate-m0', str(fixture)]), 0)
        output = Path(__file__).resolve().parent.parent / 'm0-cli-report-test.json'
        try:
            self.assertEqual(main(['report-m0', str(fixture), '--output', str(output)]), 0)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['coverage']['planned_cases'], 1)
            self.assertEqual(report['coverage']['blocked_cases'], 1)
            self.assertEqual(report['traceability'][0]['trace'], [
                'source-example-scenario', 'req-example-scenario', 'point-example',
                'case-example-v1', 'build-example-test', 'run-m0-planned', 'attempt-m0-1'])
        finally:
            if output.exists():
                output.unlink()


if __name__ == '__main__':
    unittest.main()
