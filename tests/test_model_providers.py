import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ai_test_agent.model_providers import CodexCLIProvider, ProviderError, load_provider, _codex_command


class CodexProviderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '.local' / ('provider-test-' + uuid4().hex)
        self.root.mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root)

    def test_cli_uses_stdin_schema_read_only_and_records_real_usage(self):
        def runner(argv, **kw):
            self.assertFalse(kw.get('shell', False))
            self.assertIn('--ephemeral', argv)
            self.assertEqual(argv[argv.index('--sandbox') + 1], 'read-only')
            self.assertIn('features.shell_tool=false', argv)
            self.assertEqual(kw['input'], 'untrusted source input')
            output = Path(argv[argv.index('--output-last-message') + 1])
            output.write_text('{"units":[]}', encoding='utf-8')
            events = '\n'.join(json.dumps(e) for e in [
                {'type':'item.completed','item':{'type':'agent_message','text':'{}'}},
                {'type':'turn.completed','usage':{'input_tokens':12,'output_tokens':4}},
            ])
            return subprocess.CompletedProcess(argv, 0, events, '')
        with patch('ai_test_agent.model_providers.subprocess.run', side_effect=runner):
            result = CodexCLIProvider({'provider':'codex-cli','executable':sys.executable,'model':'test-model'}).generate(
                'untrusted source input', {'type':'object'}, [], self.root)
        self.assertEqual(result['payload'], {'units':[]})
        self.assertEqual(result['metadata']['usage']['output_tokens'], 4)
        self.assertEqual(result['metadata']['calls'], 1)

    def test_timeout_is_explicit_and_never_exposes_prompt(self):
        with patch('ai_test_agent.model_providers.subprocess.run', side_effect=subprocess.TimeoutExpired(['codex'], 1)):
            with self.assertRaises(ProviderError) as caught:
                CodexCLIProvider({'provider':'codex-cli','executable':sys.executable,'model':'test'}).generate('secret source', {}, [], self.root)
        self.assertEqual(caught.exception.category, 'MODEL_TIMEOUT')
        self.assertNotIn('secret', str(caught.exception))

    def test_document_reasoning_effort_is_configurable_independently(self):
        def runner(argv,**kw):
            self.assertIn('model_reasoning_effort="medium"',argv)
            Path(argv[argv.index('--output-last-message')+1]).write_text('{}',encoding='utf-8')
            return subprocess.CompletedProcess(argv,0,'','')
        with patch('ai_test_agent.model_providers.subprocess.run',side_effect=runner):
            result=CodexCLIProvider({'provider':'codex-cli','executable':sys.executable,'model':'test','reasoning_effort':'medium'}).generate('prompt',{},[],self.root)
        self.assertEqual(result['metadata']['reasoning_effort'],'medium')

    def test_cli_config_overrides_use_literal_identifiers_without_quote_characters(self):
        def runner(argv, **kw):
            self.assertIn('mcp_servers.codegraph.enabled=false', argv)
            self.assertIn('plugins.demo@personal.enabled=false', argv)
            Path(argv[argv.index('--output-last-message')+1]).write_text('{}',encoding='utf-8')
            return subprocess.CompletedProcess(argv,0,json.dumps({'type':'turn.completed','usage':None}),'')
        with patch('ai_test_agent.model_providers._cli_settings',return_value={'model':'test','mcp_ids':['codegraph'],'plugin_ids':['demo@personal']}), patch('ai_test_agent.model_providers.subprocess.run',side_effect=runner):
            CodexCLIProvider({'provider':'codex-cli','executable':sys.executable}).generate('prompt',{},[],self.root)

    def test_recovered_stream_error_does_not_override_successful_final_turn(self):
        def runner(argv, **kw):
            Path(argv[argv.index('--output-last-message')+1]).write_text('{"ok":true}',encoding='utf-8')
            events='\n'.join(json.dumps(x) for x in [
                {'type':'error','message':'Reconnecting...'},
                {'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}])
            return subprocess.CompletedProcess(argv,0,events,'')
        with patch('ai_test_agent.model_providers.subprocess.run',side_effect=runner):
            result=CodexCLIProvider({'provider':'codex-cli','executable':sys.executable,'model':'test'}).generate('prompt',{},[],self.root)
        self.assertTrue(result['payload']['ok'])
        self.assertEqual(result['metadata']['recovered_transport_errors'],1)

    def test_stale_output_file_cannot_be_reused_after_failed_call(self):
        (self.root/'response.json').write_text('{"stale":true}',encoding='utf-8')
        with patch('ai_test_agent.model_providers.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'secret stderr')):
            with self.assertRaises(ProviderError):
                CodexCLIProvider({'provider':'codex-cli','executable':sys.executable,'model':'test'}).generate('prompt', {}, [], self.root)

    def test_tool_execution_event_invalidates_analysis(self):
        def runner(argv, **kw):
            Path(argv[argv.index('--output-last-message')+1]).write_text('{}',encoding='utf-8')
            return subprocess.CompletedProcess(argv,0,json.dumps({'type':'item.completed','item':{'type':'command_execution'}}),'')
        with patch('ai_test_agent.model_providers.subprocess.run',side_effect=runner):
            with self.assertRaises(ProviderError) as caught:
                CodexCLIProvider({'provider':'codex-cli','executable':sys.executable,'model':'test'}).generate('prompt',{},[],self.root)
        self.assertEqual(caught.exception.category,'UNEXPECTED_MODEL_TOOL')

    def test_duplicate_json_and_nonfinite_responses_are_rejected(self):
        for raw in ['{"units":[],"units":[]}','{"score":NaN}']:
            def runner(argv, **kw):
                Path(argv[argv.index('--output-last-message')+1]).write_text(raw,encoding='utf-8')
                return subprocess.CompletedProcess(argv,0,'','')
            with patch('ai_test_agent.model_providers.subprocess.run',side_effect=runner):
                with self.assertRaises(ProviderError):
                    CodexCLIProvider({'provider':'codex-cli','executable':sys.executable,'model':'test'}).generate('prompt',{},[],self.root)

    def test_config_rejects_unknown_provider_secret_keys_and_invalid_budget(self):
        for config in [{'provider':'unknown'},{'provider':'codex-cli','api_key':'secret'},
                       {'provider':'codex-cli','timeout_seconds':True},
                       {'provider':'codex-cli','model':'test','max_images':-1},
                       {'provider':'codex-cli','model':'test','max_images':1.5}]:
            with self.assertRaises(ValueError):
                load_provider(config)

    @unittest.skipUnless(os.name=='nt','Windows npm shim resolution')
    def test_windows_npm_shim_resolves_native_executable_for_timeout_cleanup(self):
        shim=self.root/'codex.cmd'
        shim.write_text('npm shim',encoding='utf-8')
        package=self.root/'node_modules/@openai/codex'
        (package/'bin').mkdir(parents=True)
        (package/'bin/codex.js').write_text('npm entry',encoding='utf-8')
        native=package/'node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'fixture')
        with patch('ai_test_agent.model_providers.shutil.which',side_effect=lambda name:str(shim) if name=='codex' else sys.executable):
            self.assertEqual(_codex_command('codex'),[str(native)])


if __name__ == '__main__':
    unittest.main()
