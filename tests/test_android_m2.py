import unittest

from ai_test_agent.android import AndroidDevice, AndroidToolError, CommandResult


class FakeExecutor:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run(self, argv, timeout=30):
        self.calls.append((list(argv), timeout))
        output = self.outputs.pop(0)
        if isinstance(output, dict):
            return CommandResult(list(argv), **output)
        return CommandResult(list(argv), 0, output, b'')


class AndroidM2Tests(unittest.TestCase):
    def setUp(self):
        for name in ('discover_devices', 'device_log_time', 'force_stop'):
            self.assertTrue(callable(getattr(AndroidDevice, name, None)), f'{name} is not implemented')

    def device(self, outputs):
        executor = FakeExecutor(outputs)
        return AndroidDevice('adb.exe', 'selected-device', executor), executor

    def test_discovery_preserves_device_states_and_optional_model(self):
        device, fake = self.device([
            b'List of devices attached\r\n'
            b'ready-serial device usb:1-2 product:test model:Pixel_8 device:husky transport_id:1\r\n'
            b'offline-serial offline transport_id:2\r\n'
            b'unauth-serial unauthorized usb:1-3 transport_id:3\r\n\r\n'
        ])
        self.assertEqual([
            {'serial': 'ready-serial', 'state': 'device', 'model': 'Pixel_8'},
            {'serial': 'offline-serial', 'state': 'offline'},
            {'serial': 'unauth-serial', 'state': 'unauthorized'},
        ], device.discover_devices())
        self.assertEqual([(['adb.exe', 'devices', '-l'], 30)], fake.calls)
        self.assertEqual(1, len(device.command_results))
        self.assertEqual(fake.calls[0][0], device.command_results[0].argv)

    def test_discovery_returns_empty_list_when_no_devices_are_attached(self):
        device, _ = self.device([b'List of devices attached\n\n'])
        self.assertEqual([], device.discover_devices())

    def test_discovery_never_selects_first_or_other_attached_device(self):
        device, fake = self.device([
            b'List of devices attached\nfirst-device device model:First\nselected-device device model:Second\n',
            b'10-16T13:14:15.678\n',
        ])
        self.assertEqual(2, len(device.discover_devices()))
        self.assertEqual('selected-device', device.serial)
        self.assertEqual('10-16 13:14:15.678', device.device_log_time())
        self.assertEqual(['adb.exe', '-s', 'selected-device'], fake.calls[1][0][:3])

    def test_discovery_rejects_malformed_or_ambiguous_lists(self):
        for output in (
            b'', b'error: cannot connect\n', b'serial device\n',
            b'List of devices attached\nmissing-state\n',
            b'List of devices attached\nserial nonsense\n',
            b'List of devices attached\nserial device model:\n',
            b'List of devices attached\nserial device malformed-attribute\n',
            b'List of devices attached\nserial device\nserial offline\n',
            b'List of devices attached\nserial! device\n',
            b'List of devices attached\nserial device model:One model:Two\n',
        ):
            with self.subTest(output=output):
                device, fake = self.device([output])
                with self.assertRaises(AndroidToolError) as caught:
                    device.discover_devices()
                self.assertEqual(output, caught.exception.result.stdout)
                self.assertEqual([caught.exception.result], device.command_results)
                self.assertEqual(1, len(fake.calls))

    def test_device_log_time_uses_device_date_with_literal_single_argument(self):
        device, fake = self.device([b'10-16T13:14:15.678\n'])
        self.assertEqual('10-16 13:14:15.678', device.device_log_time())
        self.assertEqual([(['adb.exe', '-s', 'selected-device', 'shell', 'date',
                           '+%m-%dT%H:%M:%S.%3N'], 30)], fake.calls)
        self.assertEqual(1, len(device.command_results))

    def test_device_log_time_accepts_leap_day_without_assuming_host_year(self):
        device, _ = self.device([b'02-29T00:00:00.000\n'])
        self.assertEqual('02-29 00:00:00.000', device.device_log_time())

    def test_device_log_time_rejects_bad_device_output_without_fallback(self):
        for output in (
            b'', b'date: bad format\n', b'10-16T13:14:15.%3N\n',
            b'10-16T13:14:15.123456789\n', b'2026-10-16T13:14:15.678\n',
            b'10-16T13:14:15.678\n10-16T13:14:16.000\n',
            b'13-16T13:14:15.678\n', b'02-30T13:14:15.678\n',
            b'10-16T24:14:15.678\n', b'10-16T13:60:15.678\n',
            b'10-16T13:14:60.678\n',
        ):
            with self.subTest(output=output):
                device, fake = self.device([output])
                with self.assertRaises(AndroidToolError) as caught:
                    device.device_log_time()
                self.assertEqual(output, caught.exception.result.stdout)
                self.assertEqual([caught.exception.result], device.command_results)
                self.assertEqual(1, len(fake.calls))

    def test_device_log_time_can_bound_logcat_without_clearing_it(self):
        device, fake = self.device([b'10-16T13:14:15.678\n', b'log evidence'])
        since = device.device_log_time()
        device.logcat(since=since, pid='1234')
        self.assertEqual(['adb.exe', '-s', 'selected-device', 'logcat', '-d', '-v',
                          'threadtime', '-T', '10-16 13:14:15.678', '--pid=1234'],
                         fake.calls[1][0])

    def test_force_stop_targets_explicit_serial_and_only_requested_package(self):
        device, fake = self.device([b''])
        result = device.force_stop('example.game')
        self.assertEqual([(['adb.exe', '-s', 'selected-device', 'shell', 'am',
                           'force-stop', 'example.game'], 30)], fake.calls)
        self.assertEqual([result], device.command_results)

    def test_force_stop_validates_package_before_running_command(self):
        device, fake = self.device([])
        for package in ('example.game; rm', 'example', '', None):
            with self.subTest(package=package):
                with self.assertRaises(ValueError):
                    device.force_stop(package)
        self.assertEqual([], fake.calls)

    def test_new_commands_preserve_failure_evidence_and_never_retry(self):
        for method, args in (
            ('discover_devices', ()), ('device_log_time', ()),
            ('force_stop', ('example.game',)),
        ):
            for failure in (
                dict(returncode=1, stdout=b'partial', stderr=b'device offline'),
                dict(returncode=-1, stdout=b'partial', stderr=b'timeout', timed_out=True),
            ):
                with self.subTest(method=method, failure=failure):
                    device, fake = self.device([failure])
                    with self.assertRaises(AndroidToolError) as caught:
                        getattr(device, method)(*args)
                    self.assertEqual(failure['stderr'], caught.exception.result.stderr)
                    self.assertEqual([caught.exception.result], device.command_results)
                    self.assertEqual(1, len(fake.calls))


class InstalledApkHashTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(AndroidDevice, 'installed_apk_hashes', None)),
                        'installed_apk_hashes is not implemented')

    def device(self, outputs):
        executor = FakeExecutor(outputs)
        return AndroidDevice('adb.exe', 'selected-device', executor), executor

    def test_hashes_actual_base_and_every_split_using_exact_read_only_paths(self):
        directory = '/data/app/~~random+_token/com.example.game-abc==/'
        names = ['base.apk', 'split_config.arm64_v8a.apk', 'split_UnityDataAssetPack.apk']
        paths = [directory + name for name in names]
        hashes = ['A' * 64, 'b' * 64, 'C' * 64]
        outputs = [(''.join('package:' + path + '\r\n' for path in paths)).encode('ascii')]
        outputs.extend((digest + '  ' + path + '\n').encode('ascii') for digest, path in zip(hashes, paths))
        device, fake = self.device(outputs)
        self.assertEqual(dict(zip(names, (digest.lower() for digest in hashes))),
                         device.installed_apk_hashes('com.example.game'))
        expected = [(['adb.exe', '-s', 'selected-device', 'shell', 'pm', 'path', 'com.example.game'], 30)]
        expected.extend((['adb.exe', '-s', 'selected-device', 'shell', 'sha256sum', path], 30) for path in paths)
        self.assertEqual(expected, fake.calls)
        self.assertEqual(4, len(device.command_results))
        self.assertEqual([call[0] for call in fake.calls], [result.argv for result in device.command_results])

    def test_hashes_base_only_install_from_classic_data_app_directory(self):
        path = '/data/app/com.example.game-1/base.apk'
        device, _ = self.device([('package:' + path + '\n').encode(), ('f' * 64 + '  ' + path + '\r\n').encode()])
        self.assertEqual({'base.apk': 'f' * 64}, device.installed_apk_hashes('com.example.game'))

    def test_validates_all_pm_paths_before_hashing_any_apk(self):
        valid = b'package:/data/app/pkg/base.apk\n'
        for listing in (
            b'', b'Unknown package: com.example.game\n',
            b'/data/app/pkg/base.apk\n', b'package:/sdcard/pkg/base.apk\n',
            b'package:/data/app/pkg/split_config.apk\n',
            valid + b'package:/data/app/pkg/base.apk\n',
            valid + b'package:/data/app/other/base.apk\n',
            valid + b'package:/data/app/pkg/../../sdcard/split.apk\n',
            valid + b'package:/data/app/./pkg/split.apk\n',
            valid + b'package:/data/app//pkg/split.apk\n',
            valid + b'package:/data/app/pkg/split.apk;id\n',
            valid + b'package:/data/app/pkg/split.apk extra\n',
            valid + b'package:/data/app/pkg/split.apk\nerror: failed\n',
            valid + b'package:/data/app/pkg/split.apk\x00\n',
            valid + b'package:/data/app/pkg/\xff.apk\n',
        ):
            with self.subTest(listing=listing):
                device, fake = self.device([listing])
                with self.assertRaises(AndroidToolError) as caught:
                    device.installed_apk_hashes('com.example.game')
                self.assertEqual(listing, caught.exception.result.stdout)
                self.assertEqual([caught.exception.result], device.command_results)
                self.assertEqual(1, len(fake.calls))

    def test_sha256_output_must_be_one_digest_for_the_same_exact_path(self):
        path = '/data/app/pkg/base.apk'
        valid = 'a' * 64 + '  ' + path
        for output in (
            b'', b'sha256sum: not found\n', ('a' * 63 + '  ' + path + '\n').encode(),
            ('a' * 65 + '  ' + path + '\n').encode(), ('g' * 64 + '  ' + path + '\n').encode(),
            ('a' * 64 + '  /data/app/other/base.apk\n').encode(),
            ('a' * 64 + '  base.apk\n').encode(), (valid + '\n' + valid + '\n').encode(),
            (valid + '\n\n').encode(), (valid + ' extra\n').encode(),
            ('a' * 64 + '\n').encode(), (valid + '\x00\n').encode(), b'\xff\n',
        ):
            with self.subTest(output=output):
                device, fake = self.device([('package:' + path + '\n').encode(), output])
                with self.assertRaises(AndroidToolError) as caught:
                    device.installed_apk_hashes('com.example.game')
                self.assertEqual(output, caught.exception.result.stdout)
                self.assertIs(caught.exception.result, device.command_results[-1])
                self.assertEqual(2, len(fake.calls))

    def test_hash_command_failure_or_unsupported_utility_is_not_retried(self):
        for failure in (
            dict(returncode=127, stdout=b'', stderr=b'/system/bin/sh: sha256sum: not found'),
            dict(returncode=1, stdout=b'', stderr=b'Permission denied'),
            dict(returncode=-1, stdout=b'partial', stderr=b'timeout', timed_out=True),
        ):
            with self.subTest(failure=failure):
                device, fake = self.device([b'package:/data/app/pkg/base.apk\n', failure])
                with self.assertRaises(AndroidToolError) as caught:
                    device.installed_apk_hashes('com.example.game')
                self.assertEqual(failure['stderr'], caught.exception.result.stderr)
                self.assertIs(caught.exception.result, device.command_results[-1])
                self.assertEqual(2, len(fake.calls))

    def test_pm_failure_preserves_evidence_and_never_attempts_hashing(self):
        failure = dict(returncode=1, stdout=b'', stderr=b'device offline')
        device, fake = self.device([failure])
        with self.assertRaises(AndroidToolError) as caught:
            device.installed_apk_hashes('com.example.game')
        self.assertEqual(b'device offline', caught.exception.result.stderr)
        self.assertEqual([caught.exception.result], device.command_results)
        self.assertEqual(1, len(fake.calls))

    def test_invalid_package_is_rejected_before_any_device_command(self):
        device, fake = self.device([])
        for package in ('com.example.game;id', 'example', '', None):
            with self.subTest(package=package):
                with self.assertRaises(ValueError):
                    device.installed_apk_hashes(package)
        self.assertEqual([], fake.calls)


if __name__ == '__main__':
    unittest.main()
