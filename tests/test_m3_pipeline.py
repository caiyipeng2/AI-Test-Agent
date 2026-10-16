import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4


class M3PipelineTests(unittest.TestCase):
    def setUp(self):
        self.repo=Path(__file__).resolve().parents[1]
        self.root=self.repo/'.local'/('m3-pipeline-'+uuid4().hex)
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_public_demo_generates_all_three_real_file_pipelines(self):
        script=self.repo/'examples'/'m3_recording_demo.py'
        self.assertTrue(script.exists(),'public M3 demo generator is not implemented')
        for scenario,expected,status in [('pass',0,'PASS'),('wrong-debit',1,'FAIL'),('missing-event',2,'BLOCKED')]:
            with self.subTest(scenario=scenario):
                output=self.root/scenario
                generated=subprocess.run([sys.executable,str(script),'--output-dir',str(output),'--scenario',scenario],capture_output=True,text=True)
                self.assertEqual(0,generated.returncode,generated.stderr)
                source=output/'recording.json'
                raw=source.read_bytes()
                reviewed=subprocess.run([sys.executable,'-m','ai_test_agent','assess-recording-m3',str(source),
                                         '--output',str(output/'review.json')],capture_output=True,text=True)
                self.assertEqual(expected,reviewed.returncode,reviewed.stdout+reviewed.stderr)
                result=json.loads((output/'review.json').read_text(encoding='utf-8'))
                self.assertEqual(status,result['status'])
                self.assertEqual('synthetic',result['evidence_mode'])
                self.assertEqual('NOT_EVALUATED',result['business_status'])
                self.assertEqual(raw,source.read_bytes())
                self.assertTrue((output/'review.md').exists())
                if scenario=='pass':
                    second=subprocess.run([sys.executable,'-m','ai_test_agent','assess-recording-m3',str(source),
                                           '--output',str(output/'review-second.json')],capture_output=True,text=True)
                    self.assertEqual(0,second.returncode,second.stdout+second.stderr)
                    again=json.loads((output/'review-second.json').read_text(encoding='utf-8'))
                    self.assertNotEqual(result['assessment_id'],again['assessment_id'])
                    self.assertEqual(result['input_sha256'],again['input_sha256'])

    def test_demo_never_overwrites_a_nonempty_directory(self):
        script=self.repo/'examples'/'m3_recording_demo.py'
        self.assertTrue(script.exists(),'public M3 demo generator is not implemented')
        retained=self.root/'keep.txt'
        retained.write_text('retained',encoding='utf-8')
        result=subprocess.run([sys.executable,str(script),'--output-dir',str(self.root)],capture_output=True,text=True)
        self.assertNotEqual(0,result.returncode)
        self.assertEqual('retained',retained.read_text(encoding='utf-8'))
