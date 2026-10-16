import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai_test_agent.__main__ import main


class M3CliTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.local'/('m3-cli-'+uuid4().hex)
        self.root.mkdir(parents=True)
        self.input=self.root/'recording.json'
        self.output=self.root/'result.json'

    def tearDown(self):
        shutil.rmtree(self.root)

    def invoke(self,output=None):
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(io.StringIO()), \
             patch('ai_test_agent.android.AndroidDevice',side_effect=AssertionError('device-free command')):
            try:
                code=main(['assess-recording-m3',str(self.input),'--output',str(output or self.output)])
            except SystemExit as error:
                code=error.code
        return code,json.loads(buffer.getvalue()) if buffer.getvalue() else {}

    def prepare(self,scenario='pass'):
        from m3_support import recording,line
        data=recording(self.root)
        if scenario!='pass':
            marker='AI_TEST_OBSERVATION_JSON:'
            after=json.loads(data['after']['line'].split(marker)[1])
            if scenario=='wrong-debit':
                after['wallet']['balance']='92.45'
                after['ledger']['events'][-1].update(delta='-9.05',balance_after='92.45')
            else:
                after['ledger']['first_seq']=2
                after['ledger']['events']=after['ledger']['events'][1:]
            data['after']['line']=line(after,timestamp='10-16 10:00:01.010')
        self.input.write_text(json.dumps(data),encoding='utf-8')
        return data

    def test_malformed_input_is_structured_blocked_without_device(self):
        self.input.write_text('{',encoding='utf-8')
        code,result=self.invoke()
        self.assertEqual(2,code)
        self.assertEqual('INVALID_INPUT',result.get('category'),result)
        self.assertFalse(self.output.exists())

    def test_duplicate_input_keys_are_rejected(self):
        self.input.write_text('{"schema_version":1,"schema_version":1}',encoding='utf-8')
        code,result=self.invoke()
        self.assertEqual(2,code)
        self.assertIn('duplicate',str(result).lower())

    def test_deep_nonobject_json_is_structured_blocked(self):
        self.input.write_text('['*2000+']'*2000,encoding='utf-8')
        code,result=self.invoke()
        self.assertEqual(2,code)
        self.assertEqual('INVALID_INPUT',result.get('category'),result)
        self.assertFalse(self.output.exists())

    def test_output_requires_json_suffix(self):
        self.input.write_text('{}',encoding='utf-8')
        code,result=self.invoke(self.root/'report.md')
        self.assertEqual(2,code)
        self.assertIn('.json',str(result))

    def test_report_cannot_overwrite_source_recording(self):
        self.input.write_text('{"schema_version":1}',encoding='utf-8')
        original=self.input.read_bytes()
        code,result=self.invoke(self.input)
        self.assertEqual(2,code)
        self.assertEqual(original,self.input.read_bytes())
        self.assertIn('overwrite',str(result).lower())

    def test_pass_exports_report_and_records_synthetic_scope(self):
        self.prepare()
        code,summary=self.invoke()
        self.assertEqual(0,code,summary)
        result=json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual('PASS',result['status'])
        self.assertEqual('synthetic',result['evidence_mode'])
        self.assertEqual('readonly_upgrade_evidence',result['scope'])
        self.assertEqual('NOT_EVALUATED',result['business_status'])
        self.assertEqual('device_free',result['execution'])
        self.assertEqual(hashlib.sha256(self.input.read_bytes()).hexdigest(),result['input_sha256'])
        self.assertIn('NOT_EVALUATED',self.output.with_suffix('.md').read_text(encoding='utf-8'))

    def test_wrong_debit_exits_fail_and_missing_event_exits_blocked(self):
        for scenario,expected,status in [('wrong-debit',1,'FAIL'),('missing-event',2,'BLOCKED')]:
            with self.subTest(scenario=scenario):
                self.prepare(scenario)
                code,result=self.invoke()
                self.assertEqual(expected,code,result)
                self.assertEqual(status,result.get('status'),result)

    def test_json_or_markdown_output_hardlink_cannot_change_png(self):
        self.prepare()
        png=self.root/'before.png'
        original=png.read_bytes()
        for target in (self.output,self.output.with_suffix('.md')):
            with self.subTest(target=target.name):
                if target.exists(): target.unlink()
                target.hardlink_to(png)
                code,result=self.invoke()
                self.assertEqual(2,code,result)
                self.assertIn('overwrite',str(result).lower())
                self.assertEqual(original,png.read_bytes())
                target.unlink()

    def test_repeated_review_has_same_input_fingerprint_and_unique_assessment(self):
        self.prepare()
        first_code,first=self.invoke()
        second_code,second=self.invoke(self.root/'second.json')
        self.assertEqual(0,first_code,first)
        self.assertEqual(0,second_code,second)
        self.assertEqual(first['input_sha256'],second['input_sha256'])
        self.assertNotEqual(first['assessment_id'],second['assessment_id'])
