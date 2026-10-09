"""Deterministic observations with persisted evidence; no business shortcuts."""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .android import AndroidDevice, AndroidToolError
from .config import validate_config
from .contracts import make_id


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


class TargetMismatch(ValueError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class SmokeRunner:
    def __init__(self, config, device=None):
        errors = validate_config(config)
        if errors:
            raise ValueError('; '.join(errors))
        self.config = config
        self.device = device or AndroidDevice(config['adb_path'], config['serial'])
        self._command_index = 0

    def _new_run(self, scope):
        run_id = make_id('run')
        run_dir = Path(self.config['artifact_root']) / run_id / self.config['device_alias']
        run_dir.mkdir(parents=True, exist_ok=False)
        result = {
            'schema_version': 1, 'run_id': run_id, 'attempt_id': make_id('attempt'),
            'case_id': self.config.get('case_id', 'CASE-ANDROID-STARTUP'), 'scope': scope,
            'status': 'BLOCKED', 'category': 'NOT_COMPLETED', 'business_status': 'NOT_EVALUATED',
            'environment': self.config['environment'], 'environment_source': self.config['environment_source'],
            'runtime_environment_verified': False, 'device_alias': self.config['device_alias'],
            'package': self.config['package'], 'source_ref': self.config.get('source_ref', 'local approved configuration'),
            'tool_version': '0.1.0', 'model_version': None, 'agent_mode': 'deterministic_tools',
            'started_at': utc_now(), 'run_dir': str(run_dir.resolve()),
            'result_path': str((run_dir / 'result.json').resolve()), 'evidence': [], 'reasons': [],
        }
        return result, run_dir

    def _trace(self, run_dir, action, result=None, **details):
        self._drain_commands(run_dir)
        record = {'time': utc_now(), 'action': action, **details}
        if result is not None:
            record.update(returncode=result.returncode, timed_out=result.timed_out,
                          duration_ms=result.duration_ms)
            (run_dir / f'{action}-stdout.txt').write_bytes(result.stdout)
            (run_dir / f'{action}-stderr.txt').write_bytes(result.stderr)
        with (run_dir / 'trace.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        if result is not None and (result.returncode != 0 or result.timed_out):
            raise RuntimeError(f'{action}: command did not complete successfully')

    def _finish(self, result):
        self._drain_commands(Path(result['run_dir']))
        result['finished_at'] = utc_now()
        write_json(result['result_path'], result)
        return result

    def _drain_commands(self, run_dir):
        commands = getattr(self.device, 'command_results', [])
        while self._command_index < len(commands):
            command = commands[self._command_index]
            self._command_index += 1
            stem = f'command-{self._command_index:04d}'
            (run_dir / f'{stem}-stdout.txt').write_bytes(command.stdout)
            (run_dir / f'{stem}-stderr.txt').write_bytes(command.stderr)
            record = {'time': utc_now(), 'action': 'underlying_command', 'argv': command.argv,
                      'returncode': command.returncode, 'timed_out': command.timed_out,
                      'duration_ms': command.duration_ms, 'stdout_path': f'{stem}-stdout.txt',
                      'stderr_path': f'{stem}-stderr.txt'}
            with (run_dir / 'trace.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    def _tool_failure(self, result, run_dir, error):
        result.update(status='BLOCKED', category='TOOL_FAILURE', reasons=[str(error)])
        if getattr(error, 'result', None) is not None:
            try:
                self._trace(run_dir, 'tool_failure', error.result)
            except RuntimeError:
                pass

    def _check_package(self, result, run_dir):
        result['installed_package'] = self.device.package_info(self.config['package'])
        self._trace(run_dir, 'package_info', metadata=result['installed_package'])
        expected = self.config.get('expected_version_code')
        if expected is not None and result['installed_package'].get('version_code') != expected:
            raise TargetMismatch('PACKAGE_MISMATCH', 'installed version does not match the approved artifact')

    def _check_foreground(self, run_dir):
        foreground = self.device.foreground_package()
        self._trace(run_dir, 'foreground', target_matches=foreground == self.config['package'])
        if foreground != self.config['package']:
            raise TargetMismatch('FOREGROUND_MISMATCH', 'target game is not the focused app; no input or screenshot taken')

    def _check_runtime_environment(self, result, run_dir):
        pid = self.device.pid(self.config['package'])
        if not pid:
            raise TargetMismatch('PROCESS_ABSENT', 'target process is not running')
        logs = self.device.logcat(pid=pid)
        self._trace(run_dir, 'runtime_verification', logs)
        text = logs.stdout.decode('utf-8', errors='replace')
        servers = re.findall(r'Hot InitRequestUrl Server Type\s+(\w+)', text)
        expected_server = self.config.get('expected_runtime_server', 'TestServer')
        if expected_server != 'TestServer' or (servers and servers[-1] != expected_server):
            raise TargetMismatch('ENVIRONMENT_MISMATCH', 'current-process logs do not prove the approved test server')
        if not servers:
            approved = self.config.get('approved_test_asset')
            if not isinstance(approved, dict):
                raise TargetMismatch('ENVIRONMENT_MISMATCH', 'no runtime or installed-build proof of the approved test server')
            options = {'apk_name': approved['apk_name']} if 'apk_name' in approved else {}
            proof = self.device.verify_packaged_asset(self.config['package'], approved['path'], approved['sha256'], run_dir / 'installed-build', **options)
            self._trace(run_dir, 'installed_build_verification', proof=proof)
            if not proof['verified']:
                raise TargetMismatch('ENVIRONMENT_MISMATCH', 'installed configuration differs from the approved test build')
            result['build_environment_verified'] = True
            result['build_environment_evidence'] = proof
            result['runtime_manifest'] = {'pid': pid, 'server': 'UNKNOWN', 'resource_versions': None,
                                          'dll_identity': 'UNKNOWN', 'note': 'installed test configuration verified; runtime logs rotated or missing'}
            return
        versions = re.findall(r'localVersion:(\d+)\s+remoteVersion:(\d+)', text)
        expected_resource = self.config.get('expected_resource_version')
        if expected_resource is not None and (not versions or any(int(v) != expected_resource for v in versions[-1])):
            raise TargetMismatch('RESOURCE_MISMATCH', 'resource version does not match the approved baseline')
        result['runtime_environment_verified'] = True
        result['runtime_manifest'] = {'pid': pid, 'server': servers[-1],
                                      'resource_versions': list(versions[-1]) if versions else None,
                                      'evidence': 'runtime_verification-stdout.txt', 'dll_identity': 'UNKNOWN'}

    def smoke(self):
        result, run_dir = self._new_run('startup_process')
        try:
            result['device'] = self.device.device_info()
            self._trace(run_dir, 'device_info', metadata=result['device'])
            self._check_package(result, run_dir)
            self._trace(run_dir, 'launch', self.device.launch(self.config['package']))
            time.sleep(self.config.get('startup_wait_seconds', 10))
            first_pid = self.device.pid(self.config['package'])
            self._trace(run_dir, 'process_first', pid=first_pid)
            time.sleep(self.config.get('process_check_interval_seconds', 1))
            second_pid = self.device.pid(self.config['package'])
            self._trace(run_dir, 'process_second', pid=second_pid)
            if not first_pid or first_pid != second_pid:
                result.update(status='FAIL', category='PROCESS_EXIT', reasons=['target process absent or restarted between observations'])
                return self._finish(result)
            self._check_foreground(run_dir)
            evidence = self.device.screenshot(run_dir / 'startup.png')
            result['evidence'].append(evidence)
            self._trace(run_dir, 'screenshot', evidence=evidence)
            logs = self.device.logcat(pid=second_pid)
            self._trace(run_dir, 'logcat', logs)
            if b'FATAL EXCEPTION' in logs.stdout or b'Fatal signal' in logs.stdout:
                result.update(status='FAIL', category='CRASH', reasons=['fatal marker in target-process log capture; review trace'])
            else:
                result.update(status='PASS', category='PROCESS_START_CONFIRMED',
                              reasons=['stable process and valid screenshot; game readiness and upgrade were not evaluated'])
        except TargetMismatch as error:
            result.update(status='BLOCKED', category=error.category, reasons=[str(error)])
        except AndroidToolError as error:
            self._tool_failure(result, run_dir, error)
        except (OSError, ValueError, RuntimeError) as error:
            result.update(status='BLOCKED', category='EVIDENCE_OR_TOOL_FAILURE', reasons=[str(error)])
            self._trace(run_dir, 'failure', error=str(error))
        return self._finish(result)

    def observe(self):
        result, run_dir = self._new_run('observation')
        try:
            result['device'] = self.device.device_info()
            self._check_package(result, run_dir)
            self._check_foreground(run_dir)
            result['pid'] = self.device.pid(self.config['package'])
            if not result['pid']:
                result.update(category='PROCESS_ABSENT', reasons=['launch the approved test package before observing'])
                return self._finish(result)
            result['evidence'].append(self.device.screenshot(run_dir / 'snapshot.png'))
            self._trace(run_dir, 'observe', evidence=result['evidence'])
            self._trace(run_dir, 'logcat', self.device.logcat(pid=result['pid']))
            result.update(status='PASS', category='OBSERVATION_CAPTURED')
        except TargetMismatch as error:
            result.update(category=error.category, reasons=[str(error)])
        except AndroidToolError as error:
            self._tool_failure(result, run_dir, error)
        except (OSError, ValueError, RuntimeError) as error:
            result.update(category='TOOL_FAILURE', reasons=[str(error)])
        return self._finish(result)

    def tap(self, x, y, reason):
        result, run_dir = self._new_run('single_tap')
        try:
            info = self.device.device_info()
            result['device'] = info
            result['reason'] = reason
            self._check_package(result, run_dir)
            self._check_foreground(run_dir)
            self._check_runtime_environment(result, run_dir)
            result['evidence'].append(self.device.screenshot(run_dir / 'before.png'))
            self._check_foreground(run_dir)
            self._trace(run_dir, 'tap', self.device.tap(x, y, info['width'], info['height']), x=x, y=y, reason=reason)
            time.sleep(self.config.get('action_wait_seconds', 1))
            result['evidence'].append(self.device.screenshot(run_dir / 'after.png'))
            self._trace(run_dir, 'after_tap', evidence=result['evidence'])
            result.update(status='PASS', category='INPUT_DELIVERED', reasons=['input delivery only; business outcome requires independent assertions'])
        except TargetMismatch as error:
            result.update(category=error.category, reasons=[str(error)])
        except AndroidToolError as error:
            self._tool_failure(result, run_dir, error)
        except (OSError, ValueError, RuntimeError) as error:
            result.update(category='TOOL_FAILURE', reasons=[str(error)])
        return self._finish(result)
