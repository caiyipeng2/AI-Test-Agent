"""Evidence assertions exercise real portable PNG files and exact ledger arithmetic."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
import uuid

from m3_support import line, observation, recording


class RecordingAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '.local' / ('m3-evidence-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.value = recording(self.root)

    def assess(self, value=None):
        try:
            from ai_test_agent.m3_observation import assess_recording
        except ModuleNotFoundError:
            self.fail('M3 recording assessor is not implemented')
        return assess_recording(self.value if value is None else value, evidence_root=self.root)

    def state(self, name):
        return json.loads(self.value[name]['line'].split('AI_TEST_OBSERVATION_JSON:', 1)[1])

    def change(self, name, callback):
        value = self.state(name)
        callback(value)
        timestamp = self.value[name]['line'][:18]
        self.value[name]['line'] = line(value, timestamp=timestamp)

    def blocked(self):
        result = self.assess()
        self.assertEqual(result['status'], 'BLOCKED', result)
        self.assertTrue(result['reasons'])
        self.assertTrue(all(item['status'] == 'BLOCKED' for item in result['assertions']))
        return result

    def test_reviewed_upgrade_with_concurrent_income_passes_readonly_scope(self):
        original = deepcopy(self.value)
        result = self.assess()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['scope'], 'readonly_upgrade_evidence')
        self.assertEqual(result['business_status'], 'NOT_EVALUATED')
        self.assertEqual(result['execution'], 'device_free')
        self.assertEqual(result['evidence_mode'], 'synthetic')
        self.assertEqual(result['ledger']['non_upgrade_delta'], '1.25')
        self.assertEqual(result['ledger']['upgrade_event']['transaction_id'], 'synthetic-upgrade-1')
        self.assertEqual({item['name'] for item in result['assertions']}, {'visual', 'level', 'coins', 'cost'})
        self.assertEqual(self.value, original)
        self.assertEqual(Path(result['evidence']['before']['path']), self.root / 'before.png')

    def test_validated_wrong_debit_is_fail(self):
        def wrong(value):
            value['ledger']['events'][1].update(delta='-11.05', balance_after='90.45')
            value['wallet']['balance'] = '90.45'
        self.change('after', wrong)
        result = self.assess()
        self.assertEqual(result['status'], 'FAIL', result)
        self.assertEqual(next(a for a in result['assertions'] if a['name'] == 'coins')['status'], 'FAIL')

    def test_wrong_level_and_displayed_cost_are_known_failures(self):
        for field, value in [('level', 3), ('cost', '10.06')]:
            with self.subTest(field=field):
                self.value = recording(self.root)
                self.change('after' if field == 'level' else 'before', lambda v: v['target'].update({field: value}))
                self.assertEqual(self.assess()['status'], 'FAIL')

    def test_unknown_balance_blocks_even_with_wrong_level(self):
        self.change('before', lambda v: v['wallet'].update(available=False, currency_id='', balance=''))
        self.change('after', lambda v: v['target'].update(level=4))
        self.blocked()

    def test_contract_requires_independent_review_and_trace(self):
        invalid = [
            ('approved', False), ('approved', 1), ('reviewer', ''),
            ('reviewed_at', '2026-10-16T10:00:00'), ('reviewed_at', 'garbage'),
            ('source_ref', ''), ('cost_source_ref', ''),
            ('expected_cost', 10.05),
            ('expected_cost', 'NaN'), ('expected_cost', '-1'),
        ]
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                self.value = recording(self.root)
                self.value['contract'][field] = value
                self.blocked()
        self.value = recording(self.root)
        self.value['contract']['trace']['attempt_id'] = ''
        self.blocked()
        self.value = recording(self.root)
        self.value['contract']['non_upgrade_delta'] = '1.25'
        self.blocked()

    def test_exact_recording_shapes_reject_unknown_and_mistyped_fields(self):
        for path, field, value in [
            ([], 'schema_version', True), ([], 'evidence_mode', 'live'), ([], 'unexpected', 1),
            (['before'], 'unexpected', 1), (['before', 'capture'], 'observer', 'automatic'),
            (['before', 'capture'], 'visual_confirmed', 1), (['before', 'capture'], 'visual_review_ref', ''),
            (['before', 'capture', 'screenshot'], 'bytes', True),
        ]:
            with self.subTest(path=path, field=field):
                self.value = recording(self.root)
                parent = self.value
                for key in path:
                    parent = parent[key]
                parent[field] = value
                self.blocked()

    def test_pid_and_real_unity_prefix_are_required(self):
        for field, replacement in [('pid', 124), ('device_alias', 'different'), ('seq', 2), ('session_id', 'different')]:
            with self.subTest(field=field):
                self.value = recording(self.root)
                self.value['before']['capture'][field] = replacement
                self.blocked()
        for prefix in ['', '10-16 10:00:00.010 124 123 I Unity: ', '10-16 10:00:00.010 123 123 I Other: ', '10-16 10:00:00.010 123 I Unity: ']:
            with self.subTest(prefix=prefix):
                self.value = recording(self.root)
                self.value['before']['line'] = prefix + 'AI_TEST_OBSERVATION_JSON:' + json.dumps(self.state('before'))
                self.blocked()

    def test_capture_brackets_and_pair_time_bounds(self):
        invalid = [
            ('before', 'log_start', '10-16 10:00:00.020'),
            ('before', 'log_end', '10-16 10:00:00.005'),
            ('before', 'log_end', '10-16 10:00:10.001'),
            ('before', 'log_start', '02-30 10:00:00.000'),
            ('after', 'log_start', '10-16 10:00:00.040'),
            ('after', 'log_end', '10-16 10:02:00.001'),
        ]
        for name, field, value in invalid:
            with self.subTest(name=name, field=field, value=value):
                self.value = recording(self.root)
                self.value[name]['capture'][field] = value
                self.blocked()
        self.value = recording(self.root)
        self.value['after']['capture'].update(log_start='10-16 10:02:00.000', log_end='10-16 10:02:00.050')
        self.value['after']['line'] = line(self.state('after'), timestamp='10-16 10:02:00.010')
        self.blocked()

    def test_short_year_rollover_pair_is_valid(self):
        for name, start, stamp, end in [
            ('before', '12-31 23:59:59.000', '12-31 23:59:59.010', '12-31 23:59:59.050'),
            ('after', '01-01 00:00:00.000', '01-01 00:00:00.010', '01-01 00:00:00.050'),
        ]:
            value = self.state(name)
            self.value[name]['capture'].update(log_start=start, log_end=end)
            self.value[name]['line'] = line(value, timestamp=stamp)
        self.assertEqual(self.assess()['status'], 'PASS')

    def test_runtime_provider_target_window_currency_and_stream_bindings(self):
        for name, field, value in [
            ('source', 'build_id', 'foreign'), ('source', 'resource_version', 'foreign'),
            ('source', 'dll_version', 'foreign'), ('source', 'server', 'foreign'),
            ('target', 'target_id', 'foreign'), ('target', 'window_id', 'foreign'),
            ('wallet', 'currency_id', 'foreign'),
        ]:
            with self.subTest(name=name, field=field):
                self.value = recording(self.root)
                self.change('after', lambda v: v[name].update({field: value}))
                self.blocked()
        for field, value in [('provider', 'foreign'), ('session_id', 'foreign'), ('seq', 1), ('frame', 9)]:
            with self.subTest(field=field):
                self.value = recording(self.root)
                self.change('after', lambda v: v.update({field: value}))
                self.blocked()

    def test_png_tampering_and_wrong_metadata_block(self):
        for field, value in [('sha256', '0' * 64), ('bytes', 1), ('width', 2), ('height', 2)]:
            with self.subTest(field=field):
                self.value = recording(self.root)
                self.value['before']['capture']['screenshot'][field] = value
                self.blocked()
        self.value = recording(self.root)
        (self.root / 'before.png').write_bytes(b'not PNG')
        self.blocked()

    def test_png_paths_are_contained_and_distinct_including_hardlinks(self):
        self.value['after']['capture']['screenshot'] = deepcopy(self.value['before']['capture']['screenshot'])
        self.value['after']['capture']['screenshot']['evidence_id'] = 'other'
        self.blocked()
        self.value = recording(self.root)
        self.value['after']['capture']['screenshot']['evidence_id'] = 'synthetic-before'
        self.blocked()
        self.value = recording(self.root)
        outside = self.root.parent / ('outside-' + uuid.uuid4().hex + '.png')
        shutil.copyfile(self.root / 'before.png', outside)
        self.addCleanup(outside.unlink)
        self.value['before']['capture']['screenshot']['path'] = str(outside)
        self.blocked()
        self.value = recording(self.root)
        linked = self.root / 'linked.png'
        os.link(self.root / 'before.png', linked)
        self.value['after']['capture']['screenshot'] = dict(self.value['before']['capture']['screenshot'], evidence_id='linked', path='linked.png')
        self.blocked()

    def test_identical_bytes_in_separate_files_do_not_block(self):
        shutil.copyfile(self.root / 'before.png', self.root / 'after.png')
        self.value['after']['capture']['screenshot'] = dict(self.value['before']['capture']['screenshot'], evidence_id='different', path='after.png')
        self.assertEqual(self.assess()['status'], 'PASS')

    def test_ledger_must_cover_all_events_and_reconcile_each_balance(self):
        changes = [
            lambda v: v['ledger'].update(complete=False),
            lambda v: v['ledger'].update(first_seq=2, events=v['ledger']['events'][1:]),
            lambda v: v['ledger']['events'][0].update(balance_after='102.50'),
            lambda v: v['wallet'].update(balance='91.44'),
            lambda v: v['ledger'].update(last_seq=1, events=v['ledger']['events'][:1]),
        ]
        for change in changes:
            with self.subTest(change=change):
                self.value = recording(self.root)
                self.change('after', change)
                self.blocked()

    def test_one_upgrade_must_match_explicit_transaction_scope(self):
        for field, value in [('operation_id', 'foreign'), ('target_id', 'foreign'), ('currency_id', 'gem'), ('kind', 'external')]:
            with self.subTest(field=field):
                self.value = recording(self.root)
                self.change('after', lambda v: v['ledger']['events'][1].update({field: value}))
                self.blocked()
        self.value = recording(self.root)
        self.change('after', lambda v: v['ledger']['events'][0].update(kind='upgrade', operation_id='foreign', target_id='other'))
        self.blocked()

    def test_before_history_and_transaction_reuse_cannot_be_overwritten(self):
        historic = {'seq': 1, 'transaction_id': 'old-tx', 'operation_id': '', 'kind': 'external',
                    'target_id': '', 'currency_id': 'coin', 'delta': '0', 'balance_after': '100.25'}
        self.change('before', lambda v: v['ledger'].update(last_seq=1, events=[historic]))
        def after(value):
            events = value['ledger']['events']
            for event in events:
                event['seq'] += 1
            events[1]['transaction_id'] = 'old-tx'
            value['ledger'].update(first_seq=2, last_seq=3)
        self.change('after', after)
        self.blocked()

    def test_foreign_currency_events_preserve_sequence_without_changing_target_balance(self):
        def add(value):
            events = value['ledger']['events']
            events.append({'seq': 3, 'transaction_id': 'gem-income', 'operation_id': '', 'kind': 'external',
                           'target_id': '', 'currency_id': 'gem', 'delta': '4', 'balance_after': '20'})
            value['ledger']['last_seq'] = 3
        self.change('after', add)
        self.assertEqual(self.assess()['status'], 'PASS')

    def test_large_fractional_values_are_not_rounded_at_28_digits(self):
        base = '1234567890123456789012345678901234567890.123456789012345678901234567890'
        income_balance = '1234567890123456789012345678901234567891.373456789012345678901234567890'
        end = '1234567890123456789012345678901234567881.323456789012345678901234567890'
        self.change('before', lambda v: v['wallet'].update(balance=base))
        def after(value):
            value['wallet']['balance'] = end
            value['ledger']['events'][0]['balance_after'] = income_balance
            value['ledger']['events'][1]['balance_after'] = end
        self.change('after', after)
        self.assertEqual(self.assess()['status'], 'PASS')

    def test_recorded_mode_is_preserved_and_not_live_business(self):
        self.value['evidence_mode'] = 'recorded'
        self.value['contract']['reviewer'] = 'reviewer-7'
        result = self.assess()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['evidence_mode'], 'recorded')
        self.assertEqual(result['business_status'], 'NOT_EVALUATED')

    def test_log_prefix_cannot_contain_a_second_line(self):
        self.value['before']['line'] = self.value['before']['line'].replace(' 123 123 ', '\n123 123 ')
        self.blocked()

    def test_reviewed_source_references_are_opaque_text(self):
        for reference in ['rules/observed-before-upgrade.json#row-4', 'snapshot_rules/v1#cost',
                          'rules/after-upgrade-observation-cost.csv#level-1']:
            with self.subTest(reference=reference):
                self.value['contract']['cost_source_ref'] = reference
                self.assertEqual(self.assess()['status'], 'PASS')

    def test_observed_cost_is_never_a_fallback_for_missing_reviewed_cost(self):
        del self.value['contract']['expected_cost']
        self.blocked()

    def test_before_ledger_target_currency_history_must_match_wallet(self):
        historic = {'seq': 1, 'transaction_id': 'old-tx', 'operation_id': '', 'kind': 'external',
                    'target_id': '', 'currency_id': 'coin', 'delta': '0', 'balance_after': '99.25'}
        self.change('before', lambda v: v['ledger'].update(last_seq=1, events=[historic]))
        def after(value):
            for event in value['ledger']['events']:
                event['seq'] += 1
            value['ledger'].update(first_seq=2, last_seq=3)
        self.change('after', after)
        self.blocked()

    def test_target_currency_balances_inside_historical_window_are_consistent(self):
        historic = [
            {'seq': 1, 'transaction_id': 'old-1', 'operation_id': '', 'kind': 'external',
             'target_id': '', 'currency_id': 'coin', 'delta': '0', 'balance_after': '98.25'},
            {'seq': 2, 'transaction_id': 'old-2', 'operation_id': '', 'kind': 'external',
             'target_id': '', 'currency_id': 'coin', 'delta': '1', 'balance_after': '100.25'},
        ]
        self.change('before', lambda v: v['ledger'].update(last_seq=2, events=historic))
        def after(value):
            for event in value['ledger']['events']:
                event['seq'] += 2
            value['ledger'].update(first_seq=3, last_seq=4)
        self.change('after', after)
        self.blocked()

    def test_capture_boundary_values_are_inclusive(self):
        self.value['before']['capture'].update(log_start='10-16 10:00:00.000', log_end='10-16 10:00:10.000')
        self.value['after']['capture'].update(log_start='10-16 10:01:59.000', log_end='10-16 10:02:00.000')
        self.value['after']['line'] = line(self.state('after'), timestamp='10-16 10:02:00.000')
        self.assertEqual(self.assess()['status'], 'PASS')

    def test_independent_source_reference_may_name_before_upgrade_configuration(self):
        self.value['contract']['cost_source_ref'] = 'rules/before_upgrade_configuration.json#row-4'
        self.assertEqual(self.assess()['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
