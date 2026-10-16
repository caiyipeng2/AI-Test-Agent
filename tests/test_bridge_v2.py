from copy import deepcopy
import importlib
import json
import unittest

from m3_support import line, observation


def event(seq=1, **overrides):
    value = {'seq': seq, 'transaction_id': f'transaction-{seq}', 'operation_id': 'upgrade-1',
             'kind': 'upgrade', 'target_id': 'device-1', 'currency_id': 'coin',
             'delta': '-10.05', 'balance_after': '90.20'}
    value.update(overrides)
    return value


def with_events(seq=1, events=None, first_seq=1):
    events = [event()] if events is None else events
    value = observation(seq)
    value['ledger'].update(first_seq=first_seq, last_seq=first_seq + len(events) - 1,
                           events=events)
    return value


class _ObservationCase(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('ai_test_agent.bridge_v2')
        except ModuleNotFoundError:
            self.fail('schema2 observation parser has not been implemented')

    def parse(self, value):
        return self.api.parse_observation_line(line(value))

    def reject(self, value):
        with self.assertRaises(ValueError):
            self.parse(value)


class ObservationTests(_ObservationCase):
    def test_preserves_arbitrary_precision_decimal_text_and_signed_delta(self):
        value = with_events()
        amount = '123456789012345678901234567890.00000000000000000001'
        value['wallet']['balance'] = amount
        value['target']['cost'] = '0.00000000000000000001'
        value['ledger']['events'][0]['delta'] = '-0.00000000000000000001'
        self.assertEqual(value, self.parse(value))

    def test_unavailable_sections_require_explicit_empty_markers(self):
        value = observation()
        value['source'] = dict.fromkeys(value['source'], '')
        value['source']['available'] = False
        value['target'] = {'available': False, 'target_id': '', 'window_id': '',
                           'level': -1, 'cost': '', 'ready': False}
        value['wallet'] = {'available': False, 'currency_id': '', 'balance': ''}
        value['ledger'] = {'available': False, 'complete': False, 'first_seq': 1,
                           'last_seq': 0, 'events': []}
        self.assertEqual(value, self.parse(value))
        for section, key, invalid in [('source', 'build_id', 'invented'),
                                      ('target', 'level', 0), ('target', 'cost', '0'),
                                      ('target', 'ready', True), ('wallet', 'balance', '0'),
                                      ('wallet', 'currency_id', 'coin'),
                                      ('ledger', 'complete', True), ('ledger', 'last_seq', 1)]:
            with self.subTest(section=section, key=key):
                changed = deepcopy(value)
                changed[section][key] = invalid
                self.reject(changed)

    def test_available_ledger_may_explicitly_be_incomplete(self):
        value = with_events()
        value['ledger']['complete'] = False
        self.assertEqual(value, self.parse(value))

    def test_objects_reject_unknown_missing_and_duplicate_keys_at_every_depth(self):
        value = with_events()
        paths = [(), ('source',), ('target',), ('wallet',), ('ledger',),
                 ('ledger', 'events', 0)]
        for path in paths:
            for mode in ('unknown', 'missing'):
                with self.subTest(path=path, mode=mode):
                    changed = deepcopy(value)
                    part = changed
                    for key in path:
                        part = part[key]
                    if mode == 'unknown':
                        part['unexpected'] = False
                    else:
                        part.pop(next(iter(part)))
                    self.reject(changed)
        for key in ('schema', 'balance', 'transaction_id'):
            serialized = line(value)
            original = f'"{key}": ' + json.dumps(
                2 if key == 'schema' else '100.25' if key == 'balance' else 'transaction-1')
            duplicate = original + ', ' + original
            with self.subTest(duplicate=key), self.assertRaises(ValueError):
                self.api.parse_observation_line(serialized.replace(original, duplicate))

    def test_decimal_rejects_floats_nonfinite_abbreviations_unicode_and_overflow_length(self):
        for amount in (True, 1, 1.25, None, '', 'NaN', 'Infinity', '1e2', '1K',
                       ' 1', '1 ', '１２', '١٢', '0' * 1025):
            for path in (('wallet', 'balance'), ('target', 'cost')):
                with self.subTest(amount=str(amount)[:50], path=path):
                    value = observation()
                    value[path[0]][path[1]] = amount
                    self.reject(value)
        for field in ('balance', 'cost'):
            value = observation()
            value['wallet' if field == 'balance' else 'target'][field] = '-0.01'
            self.reject(value)
        value = observation()
        value['wallet']['balance'] = '0' * 1024
        self.assertEqual('0' * 1024, self.parse(value)['wallet']['balance'])

    def test_integer_boolean_and_text_bounds_are_strict(self):
        for key, invalid in [('schema', True), ('schema', 1), ('seq', True), ('seq', 0),
                             ('seq', 2**63), ('seq', 1.0), ('frame', -1), ('frame', 2**31),
                             ('frame', False), ('provider', ''), ('provider', ' '),
                             ('provider', 'a' * 513), ('session_id', 123)]:
            with self.subTest(key=key, invalid=str(invalid)[:50]):
                value = observation()
                value[key] = invalid
                self.reject(value)
        value = observation(seq=2**63 - 1, frame=2**31 - 1, provider='a' * 512)
        value['target']['level'] = 2**63 - 1
        self.assertEqual(value, self.parse(value))
        for section, field in [('source', 'available'), ('target', 'available'),
                               ('target', 'ready'), ('wallet', 'available'),
                               ('ledger', 'available'), ('ledger', 'complete')]:
            value = observation()
            value[section][field] = 1
            self.reject(value)
        for invalid in (-1, True, 2**63):
            value = observation()
            value['target']['level'] = invalid
            self.reject(value)

    def test_event_window_must_be_contiguous_bounded_and_transaction_unique(self):
        for events, first, last in [([event(2)], 1, 1), ([event(1), event(3)], 1, 3),
                                     ([event(1), event(1)], 1, 2), ([], 1, 1),
                                     ([], 0, -1), ([event()], 2, 1),
                                     ([event(1), event(2, transaction_id='transaction-1')], 1, 2)]:
            with self.subTest(first=first, last=last, count=len(events)):
                value = observation()
                value['ledger'].update(first_seq=first, last_seq=last, events=events)
                self.reject(value)
        value = with_events(events=[event(i) for i in range(1, 4098)])
        self.reject(value)
        value = with_events(events=[], first_seq=7)
        self.assertEqual(value, self.parse(value))

    def test_events_require_typed_scope_and_exact_finite_amounts(self):
        for key, invalid in [('seq', True), ('seq', 0), ('seq', 2**63),
                             ('transaction_id', ''), ('kind', 'reward'),
                             ('operation_id', ''), ('target_id', ''), ('currency_id', ''),
                             ('delta', 1.2), ('delta', 'NaN'), ('delta', '0' * 1025),
                             ('balance_after', '-0.01')]:
            value = with_events()
            value['ledger']['events'][0][key] = invalid
            self.reject(value)
        value = with_events(events=[event(kind='external', operation_id='', target_id='', delta='+1.25')])
        self.assertEqual(value, self.parse(value))

    def test_rejects_malformed_oversized_nonfinite_and_multiple_marker_lines(self):
        for invalid in (None, b'bytes', 'ordinary line', line(observation())[:-2],
                        line(observation()) + ' trailing', line(observation()) * 2,
                        line(observation()) + ' ' * (4 * 1024 * 1024),
                        line(observation()).replace('"schema": 2', '"schema": NaN')):
            with self.subTest(invalid=str(invalid)[:60]), self.assertRaises(ValueError):
                self.api.parse_observation_line(invalid)
        nested = 'AI_TEST_OBSERVATION_JSON:' + '[' * 2000 + '0' + ']' * 2000
        with self.assertRaises(ValueError):
            self.api.parse_observation_line(nested)


class ObservationStreamTests(_ObservationCase):
    def test_stream_binds_one_session_provider_and_full_runtime_identity(self):
        parser = self.api.ObservationStream()
        parser.parse_line(line(observation()))
        for section, key in [(None, 'session_id'), (None, 'provider'),
                             ('source', 'build_id'), ('source', 'resource_version'),
                             ('source', 'dll_version'), ('source', 'server')]:
            with self.subTest(section=section, key=key):
                value = observation(2)
                (value if section is None else value[section])[key] = 'different'
                with self.assertRaises(ValueError):
                    parser.parse_line(line(value))
        self.assertEqual(2, parser.parse_line(line(observation(2)))['seq'])

    def test_expected_source_and_provider_are_checked_before_first_commit(self):
        source = {key: value for key, value in observation()['source'].items() if key != 'available'}
        parser = self.api.ObservationStream(expected_source=source, expected_provider='SyntheticProvider')
        invalid = observation()
        invalid['source']['dll_version'] = 'wrong'
        with self.assertRaises(ValueError):
            parser.parse_line(line(invalid))
        invalid = observation()
        invalid['provider'] = 'wrong'
        with self.assertRaises(ValueError):
            parser.parse_line(line(invalid))
        self.assertEqual(1, parser.parse_line(line(observation()))['seq'])

    def test_failed_sequence_frame_or_parse_does_not_advance_state(self):
        parser = self.api.ObservationStream()
        parser.parse_line(line(observation(2)))
        for value in (observation(2), observation(1), observation(3, frame=9),
                      observation(3, schema=1)):
            with self.assertRaises(ValueError):
                parser.parse_line(line(value))
        self.assertEqual(3, parser.parse_line(line(observation(3)))['seq'])

    def test_stream_rejects_ledger_regression_changed_overlap_and_transaction_reuse(self):
        parser = self.api.ObservationStream()
        parser.parse_line(line(with_events(events=[event(1), event(2)])))
        invalid_values = [with_events(2, [event(1)]),
                          with_events(2, [event(2, delta='-20'), event(3)], 2),
                          with_events(2, [event(3, transaction_id='transaction-1')], 3)]
        for value in invalid_values:
            with self.assertRaises(ValueError):
                parser.parse_line(line(value))
        value = with_events(2, [event(2), event(3)], 2)
        self.assertEqual(value, parser.parse_line(line(value)))

    def test_returned_values_and_expected_source_cannot_mutate_stream_history(self):
        source = deepcopy(observation()['source'])
        parser = self.api.ObservationStream(expected_source=source)
        source['build_id'] = 'modified-after-construction'
        first = parser.parse_line(line(with_events()))
        first['source']['build_id'] = 'modified-return'
        first['ledger']['events'][0]['delta'] = '0'
        self.assertEqual(2, parser.parse_line(line(with_events(2)))['seq'])


if __name__ == '__main__':
    unittest.main()
