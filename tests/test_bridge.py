import importlib
import json
import unittest


def snapshot(seq=1):
    return {
        'schema': 1, 'session_id': 'synthetic-session', 'seq': seq, 'frame': 10,
        'provider': 'SyntheticProvider',
        'source': {'available': True, 'build_id': 'synthetic-build', 'resource_version': 'synthetic-resource',
                   'dll_version': 'synthetic-dll', 'server': 'synthetic-environment'},
        'windows': {'available': True, 'items': [{'path': '/SyntheticCanvas/Button', 'x': 1, 'y': 2, 'width': 100, 'height': 50}]},
        'target': {'available': True, 'target_id': 'synthetic-target', 'level': 2, 'coins': '100.25', 'cost': '10.05'},
    }


def line(value):
    return '10-09 12:00:00.000 100 100 I Unity: AI_TEST_STATE_JSON:' + json.dumps(value)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('ai_test_agent.bridge')
        except ModuleNotFoundError:
            self.fail('bridge parser has not been implemented')

    def test_accepts_logcat_prefix_and_preserves_exact_coin_string(self):
        actual = self.api.parse_snapshot_line(line(snapshot()))
        self.assertEqual('100.25', actual['target']['coins'])
        self.assertEqual(2, actual['target']['level'])
        self.assertEqual('SyntheticProvider', actual['provider'])

    def test_unknown_business_is_unavailable_without_invented_zero(self):
        value = snapshot()
        value['target'] = {'available': False}
        value['source'] = {'available': False}
        value['windows'] = {'available': False, 'items': []}
        actual = self.api.parse_snapshot_line(line(value))
        self.assertFalse(actual['target']['available'])
        self.assertNotIn('coins', actual['target'])

    def test_unknown_target_cannot_supply_zero_as_if_measured(self):
        value = snapshot()
        value['target'] = {'available': False, 'level': 0, 'coins': '0', 'cost': '0'}
        with self.assertRaises(ValueError):
            self.api.parse_snapshot_line(line(value))

    def test_missing_schema_versions_provider_and_false_field_types_fail_closed(self):
        for field, invalid in (('schema', 2), ('schema', True), ('seq', -1), ('frame', -1),
                               ('provider', ''), ('session_id', ''), ('seq', 1.0)):
            with self.subTest(field=field, invalid=invalid):
                value = snapshot()
                value[field] = invalid
                with self.assertRaises(ValueError):
                    self.api.parse_snapshot_line(line(value))
        value = snapshot()
        value['source'].pop('dll_version')
        with self.assertRaises(ValueError):
            self.api.parse_snapshot_line(line(value))

    def test_float_nonfinite_and_abbreviated_money_are_rejected(self):
        for amount in (100.25, True, 'NaN', 'Infinity', '1e3', '1K'):
            value = snapshot()
            value['target']['coins'] = amount
            with self.assertRaises(ValueError):
                self.api.parse_snapshot_line(line(value))

    def test_truncated_json_missing_marker_and_duplicate_keys_fail_closed(self):
        for invalid in ('ordinary log message', line(snapshot())[:-2],
                        'AI_TEST_STATE_JSON:{"schema":1,"schema":2}',
                        'AI_TEST_STATE_JSON:' + json.dumps(snapshot()) + ' trailing text'):
            with self.assertRaises(ValueError):
                self.api.parse_snapshot_line(invalid)

    def test_nonfinite_and_invalid_window_bounds_are_rejected(self):
        for width in (-1, float('nan'), True):
            value = snapshot()
            value['windows']['items'][0]['width'] = width
            with self.assertRaises(ValueError):
                self.api.parse_snapshot_line(line(value))

    def test_stream_rejects_duplicate_backward_sequence_and_build_mismatch(self):
        parser = self.api.SnapshotStream(expected_build_id='synthetic-build', expected_provider='SyntheticProvider')
        parser.parse_line(line(snapshot(2)))
        for sequence in (2, 1):
            with self.assertRaises(ValueError):
                parser.parse_line(line(snapshot(sequence)))
        changed = snapshot(3)
        changed['source']['build_id'] = 'different-build'
        with self.assertRaises(ValueError):
            parser.parse_line(line(changed))
        self.assertEqual(3, parser.parse_line(line(snapshot(3)))['seq'])

    def test_stream_rejects_provider_mismatch_or_frame_regression(self):
        parser = self.api.SnapshotStream(expected_provider='SyntheticProvider')
        parser.parse_line(line(snapshot(1)))
        changed = snapshot(2)
        changed['frame'] = 9
        with self.assertRaises(ValueError):
            parser.parse_line(line(changed))
        changed = snapshot(2)
        changed['provider'] = 'OtherProvider'
        with self.assertRaises(ValueError):
            parser.parse_line(line(changed))


if __name__ == '__main__':
    unittest.main()
