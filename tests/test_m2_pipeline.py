"""Full synthetic APKS -> runner -> verified handoff; only Android is simulated."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai_test_agent.__main__ import main
from ai_test_agent.m2 import M2Runner
from test_m2 import Clock, Device, contract
from test_m2_artifacts import toc_asset_slice, toc_bytes, zip_bytes


class M2PipelineTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.local'/('m2-pipeline-'+uuid4().hex)
        self.root.mkdir(parents=True)
        self.config=contract(self.root)
        self.clock=Clock()

    def tearDown(self):
        shutil.rmtree(self.root)

    def prepare(self,server):
        self.asset=struct.pack('<III',123,server,456)
        manifest=(b'<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                  b'package="com.example.game" android:versionCode="2" android:versionName="2.0">'
                  b'<uses-sdk android:minSdkVersion="24" android:targetSdkVersion="36"/></manifest>')
        base=zip_bytes([('AndroidManifest.xml',manifest)])
        split=zip_bytes([('assets/environment.bytes',self.asset)])
        archive=zip_bytes([('splits/base-master.apk',base),('asset-slices/config-master.apk',split),
                           ('toc.pb',toc_bytes([['splits/base-master.apk']])+toc_asset_slice('asset-slices/config-master.apk'))])
        Path(self.config['apks_path']).write_bytes(archive)
        self.config['expected']['sha256']=hashlib.sha256(archive).hexdigest()
        self.config['environment_policy']={
            'kind':'uint32_le','archive_apk':'asset-slices/config-master.apk','installed_apk':'split_config.apk',
            'asset_path':'assets/environment.bytes','asset_sha256':hashlib.sha256(self.asset).hexdigest(),
            'offset':4,'expected_value':2,'source_ref':'synthetic schema server uint32 at offset4',
        }
        hashes={'base.apk':hashlib.sha256(base).hexdigest(),'split_config.apk':hashlib.sha256(split).hexdigest()}
        asset_sha=self.config['environment_policy']['asset_sha256']
        class SyntheticAndroid(Device):
            def installed_apk_hashes(self,package):
                return dict(hashes)
            def verify_packaged_asset(self,*args,**kwargs):
                return {'verified':True,'sha256':asset_sha,'scope':'packaged_configuration'}
        self.device=SyntheticAndroid(self.clock)
        self.input=self.root/'contract.json'
        self.input.write_text(json.dumps(self.config),encoding='utf-8')

    def invoke(self):
        def factory(config):
            return M2Runner(config,device=self.device,clock=self.clock.monotonic,sleep=self.clock.sleep)
        output=io.StringIO()
        with patch('ai_test_agent.m2.M2Runner',side_effect=factory), contextlib.redirect_stdout(output):
            code=main(['m2-smoke','--contract',str(self.input)])
        return code,json.loads(output.getvalue())

    def test_actual_archive_parser_runner_and_cli_handoff_complete_twice(self):
        self.prepare(2)
        first_code,first=self.invoke()
        second_code,second=self.invoke()
        self.assertEqual(0,first_code,first)
        self.assertEqual(0,second_code,second)
        self.assertNotEqual(first['run_id'],second['run_id'])
        self.assertEqual(first['config_sha256'],second['config_sha256'])
        for summary in (first,second):
            result=json.loads(Path(summary['result_path']).read_text(encoding='utf-8'))
            self.assertEqual('PASS',result['artifact_verification']['status'])
            self.assertEqual(2,len(result['artifact_verification']['apk_hashes']))
            self.assertEqual(0,result['model_calls'])
            handoff=json.loads(Path(summary['handoff_path']).read_text(encoding='utf-8'))
            self.assertEqual('READY_FOR_M0_REVIEW',handoff['status'])
            self.assertFalse(handoff['m0_run_imported'])
            self.assertTrue(handoff['chain']['evidence'])

    def test_actual_production_field_blocks_without_any_android_mutation(self):
        self.prepare(3)
        code,result=self.invoke()
        self.assertEqual(2,code,result)
        self.assertEqual('environment_mismatch',result['category'])
        self.assertEqual([],self.device.actions)
