import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

try:
    from ai_test_agent.runner import SmokeRunner
except ImportError:
    SmokeRunner = None


def png_bytes():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x20\x40\x60')) + chunk(b'IEND', b''))


class FakeDevice:
    def __init__(self, pids=None, corrupt=False, foreground='com.example.game', version=65,
                 logs=b'Unity: Hot InitRequestUrl Server Type TestServer\nUnity: localVersion:995  remoteVersion:995'):
        self.pids = iter(pids or ['123', '123'])
        self.corrupt = corrupt
        self.foreground = foreground
        self.version = version
        self.tap_calls = 0
        self.logs = logs

    def foreground_package(self):
        return self.foreground

    def device_info(self):
        return {'model': 'synthetic', 'sdk': 36, 'abi': 'arm64-v8a', 'width': 1080, 'height': 2340, 'density': 450}

    def package_info(self, package):
        return {'version_code': self.version, 'version_name': '2.1.1', 'min_sdk': 24, 'target_sdk': 36}

    def tap(self, x, y, width, height):
        self.tap_calls += 1
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'', timed_out=False, duration_ms=1)

    def launch(self, package):
        return SimpleNamespace(returncode=0, stdout=b'Status: ok', stderr=b'', timed_out=False, duration_ms=12)

    def pid(self, package):
        return next(self.pids)

    def screenshot(self, path):
        from ai_test_agent.contracts import evidence_for_png
        Path(path).write_bytes(b'broken' if self.corrupt else png_bytes())
        return evidence_for_png(path)

    def logcat(self, since=None, pid=None):
        return SimpleNamespace(returncode=0, stdout=self.logs, stderr=b'', timed_out=False, duration_ms=1)


class RunnerTests(unittest.TestCase):
    def config(self, temp):
        return {'adb_path': 'adb', 'serial': 'fake', 'device_alias': 'synthetic',
                'package': 'com.example.game', 'environment': 'test',
                'environment_source': 'synthetic approved fixture', 'artifact_root': str(temp),
                'startup_wait_seconds': 0, 'process_check_interval_seconds': 0}

    def test_process_pass_does_not_claim_game_or_upgrade_pass(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            result = SmokeRunner(self.config(temp), device=FakeDevice()).smoke()
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['scope'], 'startup_process')
            self.assertEqual(result['business_status'], 'NOT_EVALUATED')
            persisted = json.loads(Path(result['result_path']).read_text(encoding='utf-8'))
            self.assertEqual(persisted['run_id'], result['run_id'])
            self.assertTrue(Path(result['run_dir'], 'trace.jsonl').is_file())

    def test_lost_process_is_failure_with_persisted_evidence(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            result = SmokeRunner(self.config(temp), device=FakeDevice(pids=['123', ''])).smoke()
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['category'], 'PROCESS_EXIT')
            self.assertTrue(Path(result['result_path']).is_file())

    def test_corrupt_screenshot_cannot_pass(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            result = SmokeRunner(self.config(temp), device=FakeDevice(corrupt=True)).smoke()
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertNotEqual(result['category'], 'GAME_BUG')

    def test_each_run_has_unique_identity_and_keeps_previous_report(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            first = SmokeRunner(self.config(temp), device=FakeDevice()).smoke()
            second = SmokeRunner(self.config(temp), device=FakeDevice()).smoke()
            self.assertNotEqual(first['run_id'], second['run_id'])
            self.assertTrue(Path(first['result_path']).is_file())

    def test_live_process_with_other_foreground_app_is_blocked(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            result = SmokeRunner(self.config(temp), device=FakeDevice(foreground='com.example.other')).smoke()
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['category'], 'FOREGROUND_MISMATCH')

    def test_tap_refuses_other_foreground_app_without_input(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            device = FakeDevice(foreground='com.example.other')
            result = SmokeRunner(self.config(temp), device=device).tap(10, 10, 'approved upgrade')
            self.assertEqual(device.tap_calls, 0)
            self.assertEqual(result['status'], 'BLOCKED')

    def test_tap_refuses_unapproved_installed_version(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            config = self.config(temp)
            config['expected_version_code'] = 65
            device = FakeDevice(version=64)
            result = SmokeRunner(config, device=device).tap(10, 10, 'approved upgrade')
            self.assertEqual(device.tap_calls, 0)
            self.assertEqual(result['category'], 'PACKAGE_MISMATCH')

    def test_observe_does_not_capture_other_app(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            result = SmokeRunner(self.config(temp), device=FakeDevice(foreground='com.example.other')).observe()
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(result['evidence'], [])

    def test_tap_refuses_same_version_production_runtime(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            device = FakeDevice(logs=b'Unity: Hot InitRequestUrl Server Type ProduceServer')
            result = SmokeRunner(self.config(temp), device=device).tap(10, 10, 'approved upgrade')
            self.assertEqual(device.tap_calls, 0)
            self.assertEqual(result['category'], 'ENVIRONMENT_MISMATCH')

    def test_tap_requires_runtime_evidence_not_test_label(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            device = FakeDevice(logs=b'ordinary logs without environment')
            result = SmokeRunner(self.config(temp), device=device).tap(10, 10, 'approved upgrade')
            self.assertEqual(device.tap_calls, 0)
            self.assertEqual(result['status'], 'BLOCKED')

    def test_verified_test_runtime_allows_one_tap(self):
        self.assertIsNotNone(SmokeRunner, 'smoke runner is not implemented')
        with tempfile.TemporaryDirectory() as temp:
            device = FakeDevice()
            result = SmokeRunner(self.config(temp), device=device).tap(10, 10, 'approved upgrade')
            self.assertEqual(device.tap_calls, 1)
            self.assertTrue(result['runtime_environment_verified'])

    def test_failed_input_persists_command_error_evidence(self):
        from ai_test_agent.android import AndroidToolError, CommandResult
        class FailedDevice(FakeDevice):
            def tap(self, x, y, width, height):
                raise AndroidToolError('test command timeout', CommandResult(['adb'], -1, b'partial', b'timeout', True))
        with tempfile.TemporaryDirectory() as temp:
            result = SmokeRunner(self.config(temp), device=FailedDevice()).tap(10, 10, 'approved upgrade')
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertEqual(Path(result['run_dir'], 'tool_failure-stdout.txt').read_bytes(), b'partial')

    def test_verified_installed_test_asset_allows_input_without_inventing_runtime_proof(self):
        class ProvenDevice(FakeDevice):
            def verify_packaged_asset(self, package, asset, digest, directory):
                return {'verified': True, 'sha256': digest, 'asset': asset}
        with tempfile.TemporaryDirectory() as temp:
            config = self.config(temp)
            config['approved_test_asset'] = {'path': 'assets/config', 'sha256': 'a' * 64}
            device = ProvenDevice(logs=b'bootstrap logs rotated')
            result = SmokeRunner(config, device=device).tap(10, 10, 'approved upgrade')
            self.assertEqual(device.tap_calls, 1)
            self.assertTrue(result['build_environment_verified'])
            self.assertFalse(result['runtime_environment_verified'])


if __name__ == '__main__':
    unittest.main()
