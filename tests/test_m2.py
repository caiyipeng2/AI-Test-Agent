import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from uuid import uuid4

from ai_test_agent.android import CommandResult
from ai_test_agent.contracts import evidence_for_png
from ai_test_agent.m2 import M2Runner, DeadlineExecutor, validate_m2_contract
from tests.test_runner import png_bytes


def contract(root):
    package = root/'approved.apks'
    package.write_bytes(b'synthetic archive; archive verifier is injected')
    return {
        'schema_version':1,'project_id':'reference-game','case_id':'CASE-M2-STARTUP','source_ref':'synthetic approved startup contract',
        'adb_path':'adb','serial':'selected','device_alias':'reference','artifact_root':str(root/'runs'),
        'apks_path':str(package),'environment':'test',
        'expected':{'sha256':hashlib.sha256(package.read_bytes()).hexdigest(),'package':'com.example.game','version_code':2,'version_name':'2.0'},
        'environment_policy':{'kind':'json_field','archive_apk':'asset-slices/config.apk','installed_apk':'split_config.apk',
            'asset_path':'assets/environment.json','asset_sha256':'a'*64,'field_path':['server'],'expected_value':'test','source_ref':'fixture environment rule'},
        'tools':{'java_path':'java','bundletool_path':'bundletool.jar'},'install_policy':'if_needed','restart_before_launch':True,
        'readiness':{'approved':True,'source_ref':'fixture ready oracle','log_markers':['READY_SCREEN=home'],
                     'ui_texts':['HOME'],'ui_classes':[],'stable_samples':2,'reviewer':'fixture-qa','reviewed_at':'2026-10-16T00:00:00Z'},
        'budgets':{'preflight_seconds':30,'install_seconds':30,'startup_seconds':5,'poll_seconds':1},
    }


class Clock:
    now=0.0
    def monotonic(self):
        return self.now
    def sleep(self,seconds):
        self.now+=seconds


class Device:
    command_results=[]
    def __init__(self, clock, *, state='device', version=2, ready_at=0, pids=None, correct_asset=True):
        self.clock=clock; self.state=state; self.version=version; self.ready_at=ready_at
        self.pids=iter(pids) if pids else None; self.correct_asset=correct_asset; self.actions=[]
    def discover_devices(self):
        return [{'serial':'selected','state':self.state,'model':'fixture'}] if self.state!='missing' else []
    def device_info(self):
        return {'model':'fixture','sdk':36,'abi':'arm64-v8a','width':1,'height':1,'density':450}
    def package_info(self,package):
        return {'version_code':self.version,'version_name':f'{self.version}.0','min_sdk':24,'target_sdk':36}
    def installed_apk_hashes(self,package):
        return {'base.apk':'b'*64,'split_config.apk':'c'*64}
    def verify_packaged_asset(self,*args,**kwargs):
        return {'verified':self.correct_asset,'sha256':'a'*64,'scope':'packaged_configuration'}
    def install_apks(self,*args):
        self.actions.append('install'); self.version=2
        return CommandResult(['install'],0,b'installed',b'')
    def device_log_time(self):
        return '01-01 00:00:00.000'
    def force_stop(self,package):
        self.actions.append('force_stop'); return CommandResult(['force-stop'],0,b'',b'')
    def launch(self,package):
        self.actions.append('launch'); return CommandResult(['launch'],0,b'Status: ok',b'')
    def pid(self,package):
        return next(self.pids) if self.pids else '123'
    def foreground_package(self):
        return 'com.example.game'
    def logcat(self,since=None,pid=None):
        message='READY_SCREEN=home' if self.clock.now>=self.ready_at else 'loading'
        return CommandResult(['logcat'],0,f'01-01 00:00:01.000 123 123 I Test: {message}\n'.encode(),b'')
    def dump_ui(self,path):
        Path(path).write_text('<hierarchy/>',encoding='utf-8')
        return {'nodes':[{'package':'com.example.game','text':'HOME','class':'android.widget.TextView'}]}
    def screenshot(self,path):
        Path(path).write_bytes(png_bytes()); return evidence_for_png(path)


def approved_artifact(*args):
    return {'status':'PASS','category':'artifact_verified','reasons':[],'sha256':args[1]['sha256'],
            'package':'com.example.game','version_code':2,'version_name':'2.0','min_sdk':24,'target_sdk':36,
            'environment_evidence':{'matched':True,'observed_value':'test','expected_value':'test','asset_sha256':'a'*64},
            'base_apks':[{'archive_apk':'splits/base-master.apk','sha256':'b'*64}],
            'apk_hashes':{'splits/base-master.apk':'b'*64,'asset-slices/config.apk':'c'*64}}


class M2Tests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).resolve().parents[1]/'.local'/('m2-test-'+uuid4().hex)
        self.root.mkdir(parents=True); self.config=contract(self.root); self.clock=Clock()
    def tearDown(self):
        shutil.rmtree(self.root)
    def run_case(self,device=None,verifier=approved_artifact):
        device=device or Device(self.clock)
        result=M2Runner(self.config,device=device,artifact_verifier=verifier,clock=self.clock.monotonic,sleep=self.clock.sleep).run()
        return result,device
    def test_late_readiness_passes_with_stable_samples_and_never_claims_business_pass(self):
        result,device=self.run_case(Device(self.clock,ready_at=2))
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(result['business_status'],'NOT_EVALUATED')
        self.assertGreaterEqual(len(result['observations']),4)
        self.assertTrue(Path(result['run_dir'],'trace.jsonl').exists())
        self.assertEqual(device.actions,['force_stop','launch'])
    def test_formal_artifact_blocks_before_any_device_mutation(self):
        def formal(*args):
            return dict(approved_artifact(*args),status='BLOCKED',category='environment_mismatch',reasons=['formal'])
        result,device=self.run_case(verifier=formal)
        self.assertEqual(result['status'],'BLOCKED'); self.assertEqual(device.actions,[])
    def test_missing_offline_unauthorized_selected_device_never_launches(self):
        for state in ['missing','offline','unauthorized']:
            result,device=self.run_case(Device(self.clock,state=state))
            self.assertEqual(result['status'],'BLOCKED',state); self.assertEqual(device.actions,[])
    def test_downgrade_is_blocked_without_uninstall_or_data_clear(self):
        result,device=self.run_case(Device(self.clock,version=3))
        self.assertEqual(result['category'],'VERSION_DOWNGRADE_BLOCKED'); self.assertEqual(device.actions,[])
    def test_update_install_is_checked_then_launched(self):
        result,device=self.run_case(Device(self.clock,version=1))
        self.assertEqual(result['status'],'PASS',result); self.assertEqual(device.actions,['install','force_stop','launch'])
    def test_post_install_asset_mismatch_prevents_launch(self):
        result,device=self.run_case(Device(self.clock,version=1,correct_asset=False))
        self.assertEqual(result['status'],'BLOCKED'); self.assertEqual(device.actions,['install'])
    def test_failed_install_exit_cannot_pass_even_if_package_appears_updated(self):
        class FailedInstall(Device):
            def install_apks(self,*args):
                self.actions.append('install'); self.version=2
                return CommandResult(['install'],1,b'',b'failed')
        result,device=self.run_case(FailedInstall(self.clock,version=1))
        self.assertEqual(result['status'],'BLOCKED'); self.assertEqual(device.actions,['install'])
    def test_readiness_observed_after_deadline_cannot_pass(self):
        class SlowSecondFrame(Device):
            shots=0
            def screenshot(self,path):
                self.shots+=1
                evidence=super().screenshot(path)
                if self.shots==2:
                    self.clock.now+=10
                return evidence
        result,_=self.run_case(SlowSecondFrame(self.clock))
        self.assertEqual(result['status'],'BLOCKED'); self.assertEqual(result['category'],'READINESS_TIMEOUT')
    def test_readiness_timeout_is_blocked(self):
        result,_=self.run_case(Device(self.clock,ready_at=100))
        self.assertEqual(result['status'],'BLOCKED'); self.assertEqual(result['category'],'READINESS_TIMEOUT')
        self.assertLessEqual(self.clock.now,5)
    def test_process_restart_is_fail(self):
        result,_=self.run_case(Device(self.clock,pids=['123','124']))
        self.assertEqual(result['status'],'FAIL'); self.assertEqual(result['category'],'PROCESS_RESTART')
    def test_unapproved_readiness_contract_blocks_before_restart(self):
        self.config['readiness']['approved']=False
        result,device=self.run_case()
        self.assertEqual(result['status'],'BLOCKED'); self.assertEqual(device.actions,[])
    def test_initially_missing_pid_waits_until_process_is_ready(self):
        class DelayedProcess(Device):
            def pid(self,package):
                return '' if self.clock.now<1 else '123'
        result,_=self.run_case(DelayedProcess(self.clock))
        self.assertEqual('PASS',result['status'],result)
    def test_process_exits_during_final_screenshot_cannot_pass(self):
        class ExitDuringCapture(Device):
            alive=True
            frames=0
            def pid(self,package):
                return '123' if self.alive else ''
            def screenshot(self,path):
                answer=super().screenshot(path)
                self.frames+=1
                if self.frames==2:
                    self.alive=False
                return answer
        result,_=self.run_case(ExitDuringCapture(self.clock))
        self.assertEqual('FAIL',result['status'],result)
        self.assertEqual('PROCESS_EXIT',result['category'])
    def test_foreground_changes_during_capture_cannot_pass(self):
        class LoseForeground(Device):
            frames=0
            def screenshot(self,path):
                self.frames+=1
                return super().screenshot(path)
            def foreground_package(self):
                return 'com.other.app' if self.frames==2 else 'com.example.game'
        result,_=self.run_case(LoseForeground(self.clock))
        self.assertEqual('BLOCKED',result['status'],result)
        self.assertEqual('FOREGROUND_MISMATCH',result['category'])
    def test_same_version_other_code_hash_requires_update(self):
        class OtherBuild(Device):
            def installed_apk_hashes(self,package):
                return super().installed_apk_hashes(package) if 'install' in self.actions else {'base.apk':'d'*64}
        result,device=self.run_case(OtherBuild(self.clock))
        self.assertEqual('PASS',result['status'],result)
        self.assertEqual(['install','force_stop','launch'],device.actions)
    def test_other_installed_code_after_update_blocks_launch(self):
        class WrongUpdate(Device):
            def installed_apk_hashes(self,package):
                return {'base.apk':'d'*64}
        result,device=self.run_case(WrongUpdate(self.clock,version=1))
        self.assertEqual('BLOCKED',result['status'],result)
        self.assertEqual('POST_INSTALL_APK_MISMATCH',result['category'])
        self.assertEqual(['install'],device.actions)
    def test_expired_artifact_preflight_never_dispatches_device_calls(self):
        def slow_verifier(*args):
            self.clock.now+=100
            return approved_artifact(*args)
        class UnexpectedDevice(Device):
            def discover_devices(self):
                raise AssertionError('expired phase must not touch device')
        result,device=self.run_case(UnexpectedDevice(self.clock),verifier=slow_verifier)
        self.assertEqual('BLOCKED',result['status'],result)
        self.assertEqual('PREFLIGHT_TIMEOUT',result['category'])
        self.assertEqual([],device.actions)
    def test_invalid_approval_timestamp_and_policy_type_are_validation_errors(self):
        self.config['readiness']['reviewed_at']='yesterday'
        self.assertTrue(validate_m2_contract(self.config))
        self.config['install_policy']=[]
        self.assertTrue(validate_m2_contract(self.config))
    def test_device_preflight_expiration_cannot_reset_budget_and_install(self):
        class SlowPreflight(Device):
            def package_info(self,package):
                self.clock.now+=100
                return super().package_info(package)
        result,device=self.run_case(SlowPreflight(self.clock,version=1))
        self.assertEqual('BLOCKED',result['status'],result)
        self.assertEqual([],device.actions)
    def test_raw_command_outputs_are_checksum_bound_evidence(self):
        class LoggedDevice(Device):
            def __init__(self,*args):
                super().__init__(*args)
                self.command_results=[]
            def launch(self,package):
                command=super().launch(package)
                self.command_results.append(command)
                return command
        result,_=self.run_case(LoggedDevice(self.clock))
        outputs=[e for e in result['evidence'] if e['kind']=='command_stdout']
        self.assertTrue(outputs,'raw command outputs must be in the verified evidence manifest')
        self.assertEqual(b'Status: ok',Path(outputs[0]['path']).read_bytes())
    def test_slow_command_evidence_cannot_escape_preflight_budget(self):
        class MarkedDevice(Device):
            pending=False
            def package_info(self,package):
                self.pending=True
                return super().package_info(package)
        clock=self.clock
        class SlowEvidenceRunner(M2Runner):
            def _drain(self):
                super()._drain()
                if self.device.pending:
                    self.device.pending=False
                    clock.now+=100
        device=MarkedDevice(self.clock,version=1)
        result=SlowEvidenceRunner(self.config,device=device,artifact_verifier=approved_artifact,
                                  clock=clock.monotonic,sleep=clock.sleep).run()
        self.assertEqual('BLOCKED',result['status'],result['category'])
        self.assertEqual([],device.actions)
    def test_same_contract_rerun_preserves_old_evidence_and_fingerprint(self):
        first,_=self.run_case(); second,_=self.run_case()
        self.assertNotEqual(first['run_id'],second['run_id'])
        self.assertEqual(first['config_sha256'],second['config_sha256'])
        self.assertTrue(Path(first['result_path']).exists())
    def test_invalid_budget_and_unknown_contract_fields_are_rejected(self):
        for key,value in [('poll_seconds',0),('startup_seconds',float('nan')),('install_seconds',True)]:
            c=json.loads(json.dumps(self.config)); c['budgets'][key]=value
            self.assertTrue(validate_m2_contract(c))
        self.config['unrecognized']=True
        self.assertTrue(validate_m2_contract(self.config))
    def test_deadline_executor_clamps_each_command_and_never_dispatches_after_deadline(self):
        calls=[]
        base=SimpleNamespace(run=lambda argv,timeout:(calls.append(timeout) or CommandResult(argv,0,b'',b'')))
        budget=DeadlineExecutor(base,self.clock.monotonic)
        budget.deadline=3
        budget.run(['adb'],timeout=30)
        self.assertEqual(calls,[3])
        self.clock.now=3
        self.assertTrue(budget.run(['adb'],timeout=30).timed_out)
        self.assertEqual(len(calls),1)


if __name__=='__main__':
    unittest.main()
