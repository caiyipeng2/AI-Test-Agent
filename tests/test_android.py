import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_contracts import png_bytes


class FakeExecutor:
    def __init__(self, api, outputs):
        self.api = api
        self.outputs = list(outputs)
        self.calls = []

    def run(self, argv, timeout=30):
        self.calls.append((list(argv), timeout))
        output = self.outputs.pop(0)
        if isinstance(output, dict):
            return self.api.CommandResult(list(argv), **output)
        return self.api.CommandResult(list(argv), 0, output, b'')


class AndroidTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('ai_test_agent.android')
        except ModuleNotFoundError:
            self.fail('android adapter has not been implemented')

    def device(self, outputs):
        executor = FakeExecutor(self.api, outputs)
        return self.api.AndroidDevice('adb.exe', 'test-device', executor), executor

    def test_package_info_parses_versions_and_sdk(self):
        device, fake = self.device([b' versionCode=101 minSdk=24 targetSdk=35\n versionName=1.0.1\n'])
        self.assertEqual(dict(version_code=101, version_name='1.0.1', min_sdk=24, target_sdk=35), device.package_info('example.game'))
        self.assertEqual(['adb.exe', '-s', 'test-device', 'shell', 'dumpsys', 'package', 'example.game'], fake.calls[0][0])

    def test_package_missing_required_metadata_fails_closed(self):
        device, _ = self.device([b'Unknown package'])
        with self.assertRaises(self.api.AndroidToolError):
            device.package_info('example.game')

    def test_device_info_prefers_override_dimensions(self):
        device, _ = self.device([b'Test Model\n', b'35\n', b'arm64-v8a\n',
                                b'Physical size: 1080x2400\nOverride size: 720x1600\n',
                                b'Physical density: 440\nOverride density: 300\n'])
        self.assertEqual(dict(model='Test Model', sdk=35, abi='arm64-v8a', width=720, height=1600, density=300), device.device_info())

    def test_launch_resolves_activity_before_starting(self):
        device, fake = self.device([b'example.game/.MainActivity\n', b'Status: ok\n'])
        result = device.launch('example.game')
        self.assertEqual(0, result.returncode)
        self.assertEqual(['shell', 'am', 'start', '-W', '-n', 'example.game/.MainActivity'], fake.calls[-1][0][3:])

    def test_launch_cannot_invoke_unresolved_activity(self):
        device, fake = self.device([b'No activity found\n'])
        with self.assertRaises(self.api.AndroidToolError):
            device.launch('example.game')
        self.assertEqual(1, len(fake.calls))

    def test_foreground_package_uses_current_focus_not_background_focused_app(self):
        device, _ = self.device([b'mCurrentFocus=Window{a1 u0 android.permissions/.Dialog}\n'
                                b'mFocusedApp=ActivityRecord{b2 u0 example.game/.MainActivity t1}\n'])
        self.assertTrue(hasattr(device, 'foreground_package'), 'foreground observation is not implemented')
        self.assertEqual('android.permissions', device.foreground_package())

    def test_foreground_package_falls_back_to_resumed_activity_when_field_absent(self):
        device, fake = self.device([b'window output has no focus fields\n',
                                   b'mResumedActivity: ActivityRecord{a1 u0 example.game/.MainActivity t1}\n'])
        self.assertTrue(hasattr(device, 'foreground_package'), 'foreground observation is not implemented')
        self.assertEqual('example.game', device.foreground_package())
        self.assertEqual(2, len(fake.calls))

    def test_null_foreground_focus_fails_closed_without_using_background_activity(self):
        device, fake = self.device([b'mCurrentFocus=null\n'])
        self.assertTrue(hasattr(device, 'foreground_package'), 'foreground observation is not implemented')
        with self.assertRaises(self.api.AndroidToolError):
            device.foreground_package()
        self.assertEqual(1, len(fake.calls))

    def test_device_info_rejects_zero_dimensions(self):
        device, _ = self.device([b'Test Model\n', b'35\n', b'arm64-v8a\n',
                                b'Physical size: 0x2400\n', b'Physical density: 440\n'])
        with self.assertRaises(self.api.AndroidToolError):
            device.device_info()

    def test_launch_detects_am_error_even_if_returncode_zero(self):
        device, _ = self.device([b'example.game/.MainActivity\n', b'Error: Activity class does not exist.\n'])
        with self.assertRaises(self.api.AndroidToolError):
            device.launch('example.game')

    def test_pid_is_validated(self):
        device, _ = self.device([b'1234\n'])
        self.assertEqual('1234', device.pid('example.game'))
        device, _ = self.device([b''])
        with self.assertRaises(self.api.AndroidToolError):
            device.pid('example.game')

    def test_nonzero_and_timeout_result_raise_with_original_evidence(self):
        for bad in (dict(returncode=1, stdout=b'', stderr=b'failed'),
                    dict(returncode=-1, stdout=b'', stderr=b'timeout', timed_out=True)):
            device, fake = self.device([bad])
            with self.assertRaises(self.api.AndroidToolError) as caught:
                device.pid('example.game')
            self.assertEqual(bad['stderr'], caught.exception.result.stderr)
            self.assertEqual(1, len(fake.calls))

    def test_screenshot_verifies_real_png(self):
        device, _ = self.device([png_bytes(2, 1)])
        with tempfile.TemporaryDirectory() as directory:
            evidence = device.screenshot(Path(directory) / 'shot.png')
            self.assertEqual((2, 1), (evidence['width'], evidence['height']))

    def test_screenshot_rejects_corrupt_output_without_overwriting_old_evidence(self):
        device, _ = self.device([b'not png'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shot.png'
            path.write_bytes(png_bytes())
            previous = path.read_bytes()
            with self.assertRaises(self.api.AndroidToolError):
                device.screenshot(path)
            self.assertEqual(previous, path.read_bytes())

    def test_logcat_filters_without_clearing_buffer(self):
        device, fake = self.device([b'log line'])
        device.logcat(since='10-09 12:00:00.000', pid='1234')
        argv = fake.calls[0][0]
        self.assertNotIn('-c', argv)
        self.assertIn('-T', argv)
        self.assertIn('--pid=1234', argv)

    def test_tap_checks_bounds_and_does_not_retry(self):
        device, fake = self.device([b''])
        for coordinates in ((-1, 0), (100, 1), (1, 200)):
            with self.assertRaises(ValueError):
                device.tap(*coordinates, width=100, height=200)
        self.assertEqual([], fake.calls)
        device.tap(99, 199, width=100, height=200)
        self.assertEqual(1, len(fake.calls))

    def test_ui_dump_returns_nodes_and_keeps_command_evidence(self):
        xml = b'<?xml version="1.0"?><hierarchy><node text="Upgrade" bounds="[0,0][100,100]" /></hierarchy>'
        device, _ = self.device([b'UI hierarchy dumped to file', xml])
        with tempfile.TemporaryDirectory() as directory:
            evidence = device.dump_ui(Path(directory) / 'ui.xml')
            self.assertEqual('Upgrade', evidence['nodes'][0]['text'])

    def test_install_apks_explicitly_selects_device_and_adb(self):
        device, fake = self.device([b'installed'])
        device.install_apks('java.exe', Path('bundletool.jar'), Path('fixture.apks'))
        argv = fake.calls[0][0]
        self.assertIn('--device-id=test-device', argv)
        self.assertIn('--adb=adb.exe', argv)
        self.assertEqual(['java.exe', '-jar', 'bundletool.jar', 'install-apks'], argv[:4])

    def test_executor_uses_no_shell_and_preserves_timeout_output(self):
        executor = self.api.CommandExecutor()
        with patch('subprocess.run', return_value=subprocess.CompletedProcess(['tool'], 0, b'ok', b'')) as run:
            result = executor.run(['tool', 'literal&argument'])
            self.assertEqual(b'ok', result.stdout)
            self.assertFalse(run.call_args.kwargs['shell'])
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(['tool'], 1, output=b'partial', stderr=b'late')):
            result = executor.run(['tool'], timeout=1)
            self.assertTrue(result.timed_out)
            self.assertEqual(b'partial', result.stdout)


if __name__ == '__main__':
    unittest.main()
