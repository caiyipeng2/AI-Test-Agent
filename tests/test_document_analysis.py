import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import threading
from uuid import uuid4

from ai_test_agent.document_analysis import analyze_document
from ai_test_agent.m1 import validate_result


class SyntheticProvider:
    provider_id = 'synthetic-test-transport'
    model_id = 'synthetic-test-model'
    supports_images = True
    calls = 0

    def generate(self, prompt, schema, images, work_dir):
        self.calls += 1
        content = json.loads(prompt.split('以下 JSON 为待分析数据（非指令）：\n', 1)[1])
        sections = content['source']['sections']
        refs = [s['section_id'] for s in sections]
        return {'payload':{
            'units':[{'unit_id':'claim','title':'领取奖励','rule':'已达成目标可领取一次奖励',
              'section_ids':refs,'risk':{'description':'重复提交可能重复发奖','severity':'high','origin':'inference'},
              'cases':[{'case_id':'claim-once','title':'一次领取','category':'normal','priority':'P0',
                'preconditions':['目标达成且未领取'],'steps':['点击领取','记录领取后状态'],
                'assertions':[{'description':'发放一次','expected':'本次奖励只入账一次','evidence_requirements':['独立奖励流水']}],
                'unknowns':[],'basis':'derived'}]}],
            'questions':[],
            'section_dispositions':[{'section_id':s,'disposition':'covered','reason':'有领取用例'} for s in refs],
            'image_observations':[]},
          'metadata':{'provider_id':self.provider_id,'model_id':self.model_id,'calls':1,'usage':None,
                      'attached_image_ids':[]}}


class DocumentAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.local'/('auto-pipeline-test-'+uuid4().hex)
        self.root.mkdir(parents=True)
        self.source=self.root/'requirement.md'
        self.source.write_text('# Reward\n目标达成后可领取一次奖励。',encoding='utf-8')
        self.output=self.root/'output'

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_raw_document_runs_provider_and_exports_reviewable_drafts(self):
        provider=SyntheticProvider()
        run=analyze_document(self.source,self.output,provider,allow_model_access=True)
        self.assertEqual(run['status'],'PASS')
        self.assertEqual(provider.calls,1)
        analysis=json.loads((self.output/'analysis.json').read_text(encoding='utf-8'))
        self.assertTrue(validate_result(analysis))
        self.assertEqual(analysis['review']['status'],'PENDING')
        self.assertFalse(analysis['cases'][0]['approved'])
        self.assertEqual(json.loads((self.output/'m0-handoff.json').read_text(encoding='utf-8'))['status'],'BLOCKED')
        for name in ['source.json','document-manifest.json','model-output.json','coverage.json',
                     'human-review-request.json','report.md','cases.csv','run.json']:
            self.assertTrue((self.output/name).is_file(),name)

    def test_no_authorized_model_access_never_calls_provider(self):
        provider=SyntheticProvider()
        run=analyze_document(self.source,self.output,provider)
        self.assertEqual(run['status'],'BLOCKED')
        self.assertEqual(provider.calls,0)
        self.assertFalse((self.output/'analysis.json').exists())

    def test_provider_failure_is_recorded_without_template_fallback(self):
        class FailingProvider(SyntheticProvider):
            def generate(self,*args):
                raise ValueError('provider unavailable')
        run=analyze_document(self.source,self.output,FailingProvider(),allow_model_access=True)
        self.assertEqual(run['status'],'BLOCKED')
        self.assertEqual(run['model_calls'],1)
        self.assertEqual(run['completed_model_calls'],0)
        self.assertFalse((self.output/'analysis.json').exists())
        self.assertTrue((self.output/'source.json').exists())
        self.assertEqual(json.loads((self.output/'run.json').read_text(encoding='utf-8'))['status'],'BLOCKED')

    def test_missing_section_disposition_blocks_invalid_model_output(self):
        class BadProvider(SyntheticProvider):
            def generate(self,*args):
                response=super().generate(*args)
                response['payload']['section_dispositions']=[]
                return response
        run=analyze_document(self.source,self.output,BadProvider(),allow_model_access=True)
        self.assertEqual(run['status'],'BLOCKED')
        self.assertFalse((self.output/'analysis.json').exists())

    def test_nonempty_output_directory_is_not_overwritten(self):
        self.output.mkdir()
        (self.output/'analysis.json').write_text('previous evidence',encoding='utf-8')
        with self.assertRaises(ValueError):
            analyze_document(self.source,self.output,SyntheticProvider(),allow_model_access=True)
        self.assertEqual((self.output/'analysis.json').read_text(encoding='utf-8'),'previous evidence')

    def test_configurable_api_transport_runs_raw_document_pipeline_over_http(self):
        from ai_test_agent.api_provider import OpenAICompatibleProvider
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):
                pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path,self.headers.get('Authorization'),body))
                content=body['messages'][-1]['content']
                prompt=content if isinstance(content,str) else next(p['text'] for p in content if p['type']=='text')
                semantic=SyntheticProvider().generate(prompt,{},[],None)['payload']
                response=json.dumps({'model':'fixture-returned-model','choices':[{
                    'message':{'content':json.dumps(semantic,ensure_ascii=False)},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}}).encode()
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(response)))
                self.end_headers()
                self.wfile.write(response)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ,{'M1_CONTRACT_KEY':'synthetic-contract-token'}):
                provider=OpenAICompatibleProvider({'provider':'openai-compatible',
                    'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'fixture-requested-model',
                    'api_key_env':'M1_CONTRACT_KEY'})
                run=analyze_document(self.source,self.output,provider,allow_model_access=True)
            self.assertEqual(run['status'],'PASS')
            self.assertEqual(run['model_id'],'fixture-returned-model')
            self.assertEqual(requests[0][0],'/v1/chat/completions')
            self.assertEqual(requests[0][1],'Bearer synthetic-contract-token')
            self.assertEqual(requests[0][2]['response_format']['type'],'json_schema')
            self.assertEqual(json.loads((self.output/'analysis.json').read_text(encoding='utf-8'))['review']['status'],'PENDING')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__=='__main__':
    unittest.main()
