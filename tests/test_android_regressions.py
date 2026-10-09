import unittest
import hashlib
import zipfile
import tempfile
from pathlib import Path
from ai_test_agent.android import AndroidDevice, AndroidToolError, CommandResult


class Executor:
    def __init__(self, result):
        self.result = result
    def run(self, argv, timeout=30):
        self.result.argv = argv
        return self.result


class AndroidRegressionTests(unittest.TestCase):
    def test_pidof_no_process_is_an_observation_not_tool_failure(self):
        device = AndroidDevice('adb', 'fake', Executor(CommandResult([], 1, b'', b'')))
        self.assertEqual(device.pid('com.example.game'), '')

    def test_every_underlying_command_is_retained_for_trace(self):
        device = AndroidDevice('adb', 'fake', Executor(CommandResult([], 0, b'123', b'', duration_ms=7)))
        self.assertEqual(device.pid('com.example.game'), '123')
        self.assertEqual(len(getattr(device, 'command_results', [])), 1)
        self.assertEqual(device.command_results[0].duration_ms, 7)

    def test_transport_failure_is_not_reclassified_as_missing_process(self):
        device = AndroidDevice('adb', 'fake', Executor(CommandResult([], 1, b'', b'error: device offline')))
        with self.assertRaises(AndroidToolError):
            device.pid('com.example.game')

    def test_installed_asset_is_verified_from_actual_package_not_filename(self):
        payload = b'approved-test-config'
        class PullExecutor:
            def run(self, argv, timeout=30):
                if argv[-3:] == ['path', 'com.example.game', 'unused']:
                    raise AssertionError('invalid command')
                if 'pm' in argv:
                    return CommandResult(argv, 0, b'package:/data/app/example/base.apk\n', b'')
                if 'pull' in argv:
                    with zipfile.ZipFile(argv[-1], 'w') as archive:
                        archive.writestr('assets/config', payload)
                    return CommandResult(argv, 0, b'pulled', b'')
                raise AssertionError(argv)
        device = AndroidDevice('adb', 'fake', PullExecutor())
        self.assertTrue(callable(getattr(device, 'verify_packaged_asset', None)), 'installed build verification is missing')
        with tempfile.TemporaryDirectory() as temp:
            proof = device.verify_packaged_asset('com.example.game', 'assets/config', hashlib.sha256(payload).hexdigest(), Path(temp))
            self.assertTrue(proof['verified'])
            mismatch = device.verify_packaged_asset('com.example.game', 'assets/config', '0' * 64, Path(temp))
            self.assertFalse(mismatch['verified'])

    def test_asset_pack_configuration_uses_explicit_installed_split(self):
        payload = b'asset-pack-config'
        class SplitExecutor:
            def run(self, argv, timeout=30):
                if 'pm' in argv:
                    return CommandResult(argv, 0, b'package:/data/app/example/base.apk\npackage:/data/app/example/split_UnityDataAssetPack.apk\n', b'')
                if 'pull' in argv:
                    self.assert_selected = argv[-2].endswith('/split_UnityDataAssetPack.apk')
                    with zipfile.ZipFile(argv[-1], 'w') as archive:
                        archive.writestr('assets/config', payload)
                    return CommandResult(argv, 0, b'pulled', b'')
                raise AssertionError(argv)
        executor = SplitExecutor()
        device = AndroidDevice('adb', 'fake', executor)
        with tempfile.TemporaryDirectory() as temp:
            proof = device.verify_packaged_asset('com.example.game', 'assets/config', hashlib.sha256(payload).hexdigest(), Path(temp), apk_name='split_UnityDataAssetPack.apk')
            self.assertTrue(executor.assert_selected)
            self.assertTrue(proof['verified'])


if __name__ == '__main__':
    unittest.main()
