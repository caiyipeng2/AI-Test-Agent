import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai_test_agent.__main__ import main
from tests.test_document_analysis import SyntheticProvider


class AutoDocumentCliTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.local'/('auto-cli-test-'+uuid4().hex)
        self.root.mkdir(parents=True)
        self.document=self.root/'source.md'
        self.document.write_text('# Reward\n领取一次奖励。',encoding='utf-8')
        self.config=self.root/'provider.json'
        self.config.write_text('{"provider":"codex-cli","model":"test"}',encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_extract_only_requires_no_model_and_preserves_model_access_false(self):
        output=self.root/'extracted'
        self.assertEqual(main(['extract-document-m1',str(self.document),'--output-dir',str(output)]),0)
        self.assertFalse(json.loads((output/'source.json').read_text(encoding='utf-8'))['model_access'])

    def test_analyze_command_runs_full_pipeline_to_draft(self):
        output=self.root/'analysis'
        with patch('ai_test_agent.model_providers.load_provider',return_value=SyntheticProvider()):
            self.assertEqual(main(['analyze-document-m1',str(self.document),'--output-dir',str(output),
                '--provider-config',str(self.config),'--allow-model']),0)
        self.assertEqual(json.loads((output/'analysis.json').read_text(encoding='utf-8'))['review']['status'],'PENDING')

    def test_analyze_without_authorization_is_blocked_before_provider_call(self):
        provider=SyntheticProvider()
        with patch('ai_test_agent.model_providers.load_provider',return_value=provider):
            self.assertEqual(main(['analyze-document-m1',str(self.document),'--output-dir',str(self.root/'blocked'),
                '--provider-config',str(self.config)]),2)
        self.assertEqual(provider.calls,0)


if __name__=='__main__':
    unittest.main()
