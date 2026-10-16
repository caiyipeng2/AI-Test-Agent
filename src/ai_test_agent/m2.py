"""Deterministic M2 install/startup orchestration; no autonomous game input."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from uuid import uuid4

from .android import AndroidDevice, AndroidToolError, CommandExecutor, CommandResult
from .contracts import evidence_for_png, make_id
from .m2_artifacts import validate_artifact_contract, verify_apks
from .m2_observation import assess_observation


def _now():
    return datetime.now(timezone.utc).isoformat()


def config_digest(config):
    return hashlib.sha256(json.dumps(config,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _json(path,data):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)


def validate_m2_contract(config):
    if not isinstance(config,dict):
        return ['M2 contract must be an object']
    required={'schema_version','project_id','case_id','source_ref','adb_path','serial','device_alias','artifact_root',
              'apks_path','environment','expected','environment_policy','tools','install_policy','restart_before_launch','readiness','budgets'}
    allowed=required|{'expected_runtime_server','expected_resource_version'}
    errors=[]
    if set(config)-allowed or required-set(config):
        errors.append('M2 contract has missing or unknown fields')
    if type(config.get('schema_version')) is not int or config['schema_version']!=1:
        errors.append('unsupported M2 schema_version')
    for key in ('project_id','case_id','source_ref','adb_path','serial','device_alias','artifact_root','apks_path'):
        if not isinstance(config.get(key),str) or not config[key].strip() or '\0' in config[key]:
            errors.append(f'{key} must be an explicit nonempty string')
    if not isinstance(config.get('serial'),str) or not re.fullmatch(r'[A-Za-z0-9._:-]+',config['serial']):
        errors.append('serial must explicitly identify the selected device')
    if not isinstance(config.get('device_alias'),str) or not re.fullmatch(r'[A-Za-z0-9_-]+',config['device_alias']):
        errors.append('device_alias must be a safe directory component')
    if config.get('environment')!='test':
        errors.append('only test environment is enabled')
    try:
        errors.extend(validate_artifact_contract(config.get('expected'),config.get('environment_policy')))
    except (TypeError,ValueError):
        errors.append('invalid artifact/environment contract')
    tools=config.get('tools')
    if not isinstance(tools,dict) or set(tools)!={'java_path','bundletool_path'} or any(not isinstance(v,str) or not v.strip() for v in tools.values()):
        errors.append('tools must explicitly identify Java and bundletool')
    if not isinstance(config.get('install_policy'),str) or config['install_policy'] not in {'always','if_needed','never'}:
        errors.append('install_policy must be always, if_needed or never')
    if type(config.get('restart_before_launch')) is not bool:
        errors.append('restart_before_launch must be boolean')
    ready=config.get('readiness')
    ready_keys={'approved','source_ref','log_markers','ui_texts','ui_classes','stable_samples','reviewer','reviewed_at'}
    if not isinstance(ready,dict) or set(ready)!=ready_keys:
        errors.append('readiness has missing or unknown fields')
    else:
        if type(ready['approved']) is not bool:
            errors.append('readiness.approved must be boolean')
        for key in ('log_markers','ui_texts','ui_classes'):
            if not isinstance(ready[key],list) or any(not isinstance(s,str) or not s.strip() or len(s)>512 for s in ready[key]):
                errors.append(f'readiness.{key} must contain bounded literal signals')
        if type(ready['stable_samples']) is not int or not 2<=ready['stable_samples']<=20:
            errors.append('stable_samples must be an integer from 2 to 20')
        if not isinstance(ready['source_ref'],str) or not ready['source_ref'].strip():
            errors.append('readiness source_ref is required')
        if ready['approved'] is True:
            if not any(ready.get(k) for k in ('log_markers','ui_texts','ui_classes')):
                errors.append('approved readiness needs independent signals')
            if any(not isinstance(ready[k],str) or not ready[k].strip() for k in ('reviewer','reviewed_at')):
                errors.append('approved readiness needs a reviewer and review time')
            try:
                timestamp=datetime.fromisoformat(ready['reviewed_at'].replace('Z','+00:00'))
                if timestamp.tzinfo is None:
                    raise ValueError('timezone is required')
            except (TypeError,ValueError,AttributeError):
                errors.append('readiness.reviewed_at must be an ISO timestamp with timezone')
    budgets=config.get('budgets')
    limits={'preflight_seconds':(1,600),'install_seconds':(1,600),'startup_seconds':(1,600),'poll_seconds':(0.1,30)}
    if not isinstance(budgets,dict) or set(budgets)!=set(limits):
        errors.append('budgets has missing or unknown fields')
    else:
        for key,(low,high) in limits.items():
            value=budgets[key]
            if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:
                errors.append(f'{key} must be finite within [{low}, {high}]')
        if not errors and budgets['poll_seconds']>=budgets['startup_seconds']:
            errors.append('poll interval must be smaller than startup timeout')
    for key in ('expected_runtime_server',):
        if config.get(key) is not None and (not isinstance(config[key],str) or not config[key].strip()):
            errors.append(f'{key} must be null or a nonempty string')
    if config.get('expected_resource_version') is not None and (type(config['expected_resource_version']) is not int or config['expected_resource_version']<0):
        errors.append('expected_resource_version must be null or a nonnegative integer')
    return errors


class DeadlineExecutor:
    def __init__(self,delegate,clock=time.monotonic):
        self.delegate=delegate
        self.clock=clock
        self.deadline=None
    def run(self,argv,timeout=30):
        remaining=self.deadline-self.clock() if self.deadline is not None else timeout
        if remaining<=0:
            return CommandResult(list(argv),-1,b'',b'phase deadline exhausted',timed_out=True)
        return self.delegate.run(argv,timeout=min(timeout,remaining))


class M2Runner:
    def __init__(self,config,*,device=None,executor=None,artifact_verifier=verify_apks,clock=time.monotonic,sleep=time.sleep):
        errors=validate_m2_contract(config)
        if errors:
            raise ValueError('; '.join(errors))
        self.config=deepcopy(config)
        self.clock,self.sleep,self.verify=clock,sleep,artifact_verifier
        self.executor=DeadlineExecutor(executor or CommandExecutor(),clock)
        self.device=device or AndroidDevice(config['adb_path'],config['serial'],executor=self.executor)
        self._command_index=0

    def _event(self,action,**fields):
        record={'time':_now(),'elapsed_seconds':round(self.clock()-self.started,3),'action':action,**fields}
        with (self.directory/'trace.jsonl').open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+'\n')

    def _drain(self):
        commands=getattr(self.device,'command_results',[])
        while self._command_index<len(commands):
            command=commands[self._command_index]
            self._command_index+=1
            stem=f'command-{self._command_index:04d}'
            (self.directory/(stem+'-stdout.bin')).write_bytes(command.stdout)
            (self.directory/(stem+'-stderr.bin')).write_bytes(command.stderr)
            self._evidence(self.directory/(stem+'-stdout.bin'),'command_stdout')
            self._evidence(self.directory/(stem+'-stderr.bin'),'command_stderr')
            self._event('command',argv=command.argv,returncode=command.returncode,timed_out=command.timed_out,
                        duration_ms=command.duration_ms,stdout=stem+'-stdout.bin',stderr=stem+'-stderr.bin')

    def _call(self,name,function,*args,**kwargs):
        try:
            if self.executor.deadline is not None and self.clock()>=self.executor.deadline:
                raise AndroidToolError('M2 phase deadline exhausted',CommandResult(['M2',name],-1,b'',b'',timed_out=True))
            value=function(*args,**kwargs)
            fields={'returncode':value.returncode,'timed_out':value.timed_out,'duration_ms':value.duration_ms} if isinstance(value,CommandResult) else {}
            self._event(name,**fields)
            if isinstance(value,CommandResult) and (value.returncode!=0 or value.timed_out):
                raise AndroidToolError('Android operation failed or timed out',value)
            return value
        finally:
            self._drain()
            if self.executor.deadline is not None and self.clock()>=self.executor.deadline:
                raise AndroidToolError('Android operation or evidence persistence exceeded phase deadline',
                                       CommandResult(['M2',name],-1,b'',b'',timed_out=True))

    def _evidence(self,path,kind):
        path=Path(path).resolve()
        data=path.read_bytes()
        record={'kind':kind,'path':str(path),'relative_path':path.relative_to(self.directory).as_posix(),
                'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        self.result['evidence'].append(record)
        return record

    def _blocked(self,category,reason):
        self.result.update(status='BLOCKED',category=category,reasons=[reason])
        return self._finish()

    def _installed_identity(self,proof,label):
        observed=self._call(label,self.device.installed_apk_hashes,self.config['expected']['package'])
        self.result[label]=observed
        allowed={value.lower() for value in proof.get('apk_hashes',{}).values()}
        bases={item['sha256'].lower() for item in proof.get('base_apks',[])}
        return bool(observed and bases and observed.get('base.apk','').lower() in bases
                    and all(value.lower() in allowed for value in observed.values()))

    def _finish(self):
        self._drain()
        for evidence in self.result['evidence']:
            data=Path(evidence['path']).read_bytes()
            if len(data)!=evidence['bytes'] or hashlib.sha256(data).hexdigest()!=evidence['sha256']:
                self.result.update(status='BLOCKED',category='EVIDENCE_CHANGED',reasons=['Evidence changed before result persistence'])
        self.result['finished_at']=_now()
        self.result['elapsed_seconds']=round(self.clock()-self.started,3)
        self._event('finish',status=self.result['status'],category=self.result['category'])
        self._evidence(self.directory/'trace.jsonl','trace')
        _json(self.directory/'result.json',self.result)
        write_m2_report(self.result,self.directory/'report.md')
        return deepcopy(self.result)

    def run(self):
        self.started=self.clock()
        run_id=make_id('run')
        self.directory=(Path(self.config['artifact_root'])/run_id/self.config['device_alias']).resolve()
        self.directory.mkdir(parents=True,exist_ok=False)
        self._command_index=len(getattr(self.device,'command_results',[]))
        (self.directory/'trace.jsonl').write_text('',encoding='utf-8')
        _json(self.directory/'contract.json',self.config)
        self.result={'schema_version':1,'run_id':run_id,'attempt_id':make_id('attempt'),'case_id':self.config['case_id'],
            'scope':'startup_readiness','status':'BLOCKED','category':'NOT_COMPLETED','business_status':'NOT_EVALUATED',
            'environment':'test','device_alias':self.config['device_alias'],'package':self.config['expected']['package'],
            'config_sha256':config_digest(self.config),'started_at':_now(),'run_dir':str(self.directory),
            'result_path':str(self.directory/'result.json'),'evidence':[],'observations':[],'reasons':[],
            'data_policy':'preserve','model_calls':0}
        self._event('begin',config_sha256=self.result['config_sha256'])
        _json(self.directory/'result.json',self.result)
        expected=self.config['expected']; policy=self.config['environment_policy']; budgets=self.config['budgets']
        self.executor.deadline=self.clock()+budgets['preflight_seconds']
        try:
            self.result['phase']='artifact_preflight'
            proof=self.verify(self.config['apks_path'],expected,policy,self.directory/'artifact')
            self.result['artifact_verification']=proof
            self._event('artifact_verified',status=proof['status'],category=proof['category'])
            if proof.get('inspection_path') and Path(proof['inspection_path']).is_file():
                self._evidence(proof['inspection_path'],'artifact_verification')
            if proof['status']!='PASS':
                return self._blocked(proof['category'],'; '.join(proof.get('reasons',[])))
            if self.clock()>=self.executor.deadline:
                return self._blocked('PREFLIGHT_TIMEOUT','Artifact inspection exceeded the preflight budget; no device command was dispatched')
            self.result['phase']='device_preflight'
            devices=self._call('discover_devices',self.device.discover_devices)
            selected=[d for d in devices if d['serial']==self.config['serial']]
            if len(selected)!=1 or selected[0]['state']!='device':
                return self._blocked('DEVICE_NOT_READY','The explicitly selected device is absent, offline or unauthorized')
            self.result['device']=self._call('device_info',self.device.device_info)
            if self.result['device']['sdk']<proof['min_sdk']:
                return self._blocked('DEVICE_SDK_UNSUPPORTED','Selected device does not satisfy artifact minSdk')
            installed=None
            try:
                installed=self._call('package_info_before',self.device.package_info,expected['package'])
            except AndroidToolError as error:
                if error.result.returncode==0 and b'Unable to find package' in error.result.stdout:
                    self._event('package_not_installed')
                else:
                    raise
            self.result['installed_before']=installed
            if installed and installed['version_code']>expected['version_code']:
                return self._blocked('VERSION_DOWNGRADE_BLOCKED','Artifact is older than installed version; data-preserving update cannot proceed')
            if not self.config['readiness']['approved']:
                return self._blocked('READINESS_UNAPPROVED','A reviewer must approve the independent readiness contract before device mutations')
            identity_ok=bool(installed and installed['version_code']==expected['version_code'] and installed['version_name']==expected['version_name'])
            asset_before=None
            if identity_ok:
                asset_before=self._call('installed_asset_before',self.device.verify_packaged_asset,expected['package'],policy['asset_path'],policy['asset_sha256'],
                                        self.directory/'installed-before',apk_name=policy['installed_apk'])
            installed_ok=identity_ok and asset_before and asset_before['verified']
            if installed_ok:
                installed_ok=self._installed_identity(proof,'installed_apk_hashes_before')
            needs_install=self.config['install_policy']=='always' or (self.config['install_policy']=='if_needed' and not installed_ok)
            self.result['installation']={'requested':needs_install,'performed':False,'data_policy':'preserve'}
            if needs_install:
                self.result['phase']='install'
                self.executor.deadline=self.clock()+budgets['install_seconds']
                self._call('install_apks',self.device.install_apks,self.config['tools']['java_path'],self.config['tools']['bundletool_path'],self.config['apks_path'])
                self.result['installation']['performed']=True
            elif not installed_ok:
                return self._blocked('INSTALLED_ARTIFACT_MISMATCH','Installation is disabled and the approved test artifact is not installed')
            self.result['phase']='post_install_verification'
            self.executor.deadline=self.clock()+budgets['preflight_seconds']
            current=self._call('package_info_after',self.device.package_info,expected['package'])
            self.result['installed_package']=current
            if current['version_code']!=expected['version_code'] or current['version_name']!=expected['version_name']:
                return self._blocked('POST_INSTALL_VERSION_MISMATCH','Installed package identity does not match the verified artifact')
            asset=self._call('installed_asset_after',self.device.verify_packaged_asset,expected['package'],policy['asset_path'],policy['asset_sha256'],
                             self.directory/'installed-after',apk_name=policy['installed_apk'])
            self.result['installed_environment_evidence']=asset
            if not asset['verified']:
                return self._blocked('POST_INSTALL_ENVIRONMENT_MISMATCH','Actual installed configuration is not the approved test configuration')
            if not self._installed_identity(proof,'installed_apk_hashes_after'):
                return self._blocked('POST_INSTALL_APK_MISMATCH','Installed APK bytes do not belong to the verified APKS')
            self.result['phase']='startup'
            self.executor.deadline=self.clock()+budgets['startup_seconds']
            since=self._call('device_log_time',self.device.device_log_time)
            self.result['log_since']=since
            if self.config['restart_before_launch']:
                self._call('force_stop',self.device.force_stop,expected['package'])
            self._call('launch',self.device.launch,expected['package'])
            seen_pids=set(); first_pid=None; stable=0; sample=0
            while self.clock()<self.executor.deadline:
                sample+=1
                stem=f'observation-{sample:04d}'
                pid=self._call(stem+'-pid',self.device.pid,expected['package'])
                if pid:
                    seen_pids.add(pid)
                    if first_pid and pid!=first_pid:
                        self.result.update(status='FAIL',category='PROCESS_RESTART',reasons=['Primary process restarted during this attempt'])
                        return self._finish()
                    first_pid=pid
                foreground=self._call(stem+'-foreground',self.device.foreground_package) if pid else ''
                nodes=[]; screenshot_valid=False
                if pid and foreground==expected['package']:
                    ui_path=self.directory/(stem+'.xml')
                    ui=self._call(stem+'-ui',self.device.dump_ui,ui_path)
                    nodes=ui['nodes']
                    self._evidence(ui_path,'ui_hierarchy')
                    png=self.directory/(stem+'.png')
                    self._call(stem+'-screenshot',self.device.screenshot,png)
                    evidence_for_png(png)
                    self._evidence(png,'screenshot'); screenshot_valid=True
                initial_pid=pid
                if pid:
                    pid=self._call(stem+'-pid-after-capture',self.device.pid,expected['package'])
                    if pid:
                        seen_pids.add(pid)
                    foreground=self._call(stem+'-foreground-after-capture',self.device.foreground_package) if pid else ''
                logs=self._call(stem+'-logs',self.device.logcat,since=since)
                log_path=self.directory/(stem+'.log')
                log_path.write_bytes(logs.stdout)
                log_evidence=self._evidence(log_path,'log')
                snapshot={'pid':pid,'foreground':foreground,'logs':logs.stdout.decode('utf-8',errors='replace'),
                          'ui_nodes':nodes,'screenshot_valid':screenshot_valid}
                assessment=assess_observation(snapshot,self.config['readiness'],package=expected['package'],observed_pids=seen_pids,since=since)
                row={'sample':sample,'elapsed_seconds':round(self.clock()-self.started,3),'pid':pid,'foreground':foreground,
                     'assessment':assessment,'logs':log_evidence['relative_path']}
                self.result['observations'].append(row)
                self._event('observation',**row)
                if assessment['status'] in {'FAIL','BLOCKED'}:
                    self.result.update(status=assessment['status'],category=assessment['category'],reasons=assessment['reasons'])
                    return self._finish()
                if self.clock()>=self.executor.deadline:
                    return self._blocked('READINESS_TIMEOUT','Observation finished after the startup deadline')
                if first_pid and not pid:
                    self.result.update(status='FAIL',category='PROCESS_EXIT',reasons=['Primary process exited after being observed'])
                    return self._finish()
                if initial_pid and pid!=initial_pid:
                    self.result.update(status='FAIL',category='PROCESS_RESTART',reasons=['Primary process changed during evidence capture'])
                    return self._finish()
                runtime=assessment['runtime']
                wanted=self.config.get('expected_runtime_server')
                if wanted and runtime['server'] and runtime['server']!=wanted:
                    return self._blocked('RUNTIME_ENVIRONMENT_MISMATCH','Current-run server does not match the approved test target')
                wanted_version=self.config.get('expected_resource_version')
                if wanted_version is not None and runtime['resource_versions'] and any(int(v)!=wanted_version for v in runtime['resource_versions']):
                    return self._blocked('RUNTIME_RESOURCE_MISMATCH','Current-run resource identity differs from the approved baseline')
                runtime_ok=(not wanted or runtime['server']==wanted) and (wanted_version is None or runtime['resource_versions']==[str(wanted_version)]*2)
                stable=stable+1 if assessment['status']=='READY' and runtime_ok else 0
                if stable>=self.config['readiness']['stable_samples']:
                    self.result.update(status='PASS',category='READINESS_CONFIRMED',reasons=['Approved independent readiness signals remained satisfied across stable samples'])
                    self.result['runtime_manifest']=runtime
                    return self._finish()
                remaining=self.executor.deadline-self.clock()
                if remaining>0:
                    self.sleep(min(budgets['poll_seconds'],remaining))
            return self._blocked('READINESS_TIMEOUT','Readiness was not independently confirmed before the startup deadline')
        except AndroidToolError as error:
            category='READINESS_TIMEOUT' if self.result['phase']=='startup' and self.clock()>=self.executor.deadline else 'ANDROID_TOOL_FAILURE'
            return self._blocked(category,str(error))
        except (OSError,ValueError,RuntimeError,KeyError,TypeError) as error:
            return self._blocked(getattr(error,'category','M2_TOOL_OR_EVIDENCE_FAILURE'),str(error))


def write_m2_report(result,path):
    lines=['# M2 Android 冒烟报告','',f"结果：{result['status']} / {result['category']}",
           f"范围：{result['scope']}；业务：{result['business_status']}；数据策略：preserve。",'',
           f"run_id：{result['run_id']}；attempt_id：{result['attempt_id']}。",'',
           f"设备别名：{result['device_alias']}；配置指纹：{result['config_sha256']}。",'',
           '## 判定依据','']+['- '+x for x in result['reasons']]
    lines+=['','## 当次观测','']
    for sample in result['observations']:
        lines.append(f"- 样本{sample['sample']}，{sample['elapsed_seconds']}秒：{sample['assessment']['status']} / {sample['assessment']['category']}。")
    lines+=['','## 证据','']
    for evidence in result['evidence']:
        lines.append(f"- {evidence['kind']}：{evidence['relative_path']}；SHA-256 {evidence['sha256']}。")
    lines+=['','本报告仅判定批准的安装/启动与readiness合同，不代表设备升级、金币、登录账号进度等业务验证通过。']
    target=Path(path)
    temporary=target.with_name('.'+target.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as handle:
            handle.write('\n'.join(lines)+'\n')
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
