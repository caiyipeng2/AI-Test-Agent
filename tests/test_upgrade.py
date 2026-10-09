import copy
import importlib
import tempfile
import unittest
from pathlib import Path

from test_contracts import png_bytes


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.api = importlib.import_module('ai_test_agent.upgrade')
            contracts = importlib.import_module('ai_test_agent.contracts')
        except ModuleNotFoundError:
            self.fail('upgrade assessment has not been implemented')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.before = dict(target_id='explicit-target', build_id='test-build', level=2, coins='100.50',
                           observer='manual', visual_confirmed=True, device_id='device-alias',
                           window_id='UIUpgradeWindow', observation_id='observation_before')
        self.after = dict(target_id='explicit-target', build_id='test-build', level=3, coins='94.75',
                          observer='bridge', visual_confirmed=True, device_id='device-alias',
                          window_id='UIUpgradeWindow', observation_id='observation_after')
        for name, snapshot in (('before', self.before), ('after', self.after)):
            path = Path(self.directory.name) / (name + '.png')
            path.write_bytes(png_bytes())
            snapshot['screenshot'] = contracts.evidence_for_png(path)
            snapshot['screenshot']['evidence_id'] = 'evidence_' + name
        self.expected = dict(target_id='explicit-target', window_id='UIUpgradeWindow', level_delta=1,
                             cost='10.00', non_upgrade_delta='4.25', approved=True,
                             trace={'requirement_id': 'req_1', 'case_id': 'case_1', 'source_ref': 'manual-contract-v1',
                                    'run_id': 'run_1', 'attempt_id': 'attempt_1'})

    def assess(self):
        return self.api.assess_upgrade(self.before, self.after, self.expected)

    def test_confirmed_upgrade_with_concurrent_income_passes(self):
        result = self.assess()
        self.assertEqual('PASS', result['status'])
        self.assertEqual(3, len(result['assertions']))
        self.assertTrue(all(item['passed'] for item in result['assertions']))

    def test_known_level_failure_is_business_failure(self):
        self.after['level'] = 4
        result = self.assess()
        self.assertEqual('FAIL', result['status'])
        self.assertEqual('BUSINESS_FAILURE', result['category'])

    def test_known_cost_failure_is_business_failure(self):
        self.after['coins'] = '90.50'
        self.assertEqual('FAIL', self.assess()['status'])

    def test_unknown_income_blocks_instead_of_inventing_zero(self):
        self.expected.pop('non_upgrade_delta')
        self.assertEqual('BLOCKED', self.assess()['status'])

    def test_money_must_be_exact_not_float_abbreviation_or_special_value(self):
        for value in (100.5, True, '1.2K', 'NaN', 'Infinity', '1e3', None):
            with self.subTest(value=value):
                self.before['coins'] = value
                self.assertEqual('BLOCKED', self.assess()['status'])

    def test_target_or_build_mismatch_blocks(self):
        for field in ('target_id', 'build_id'):
            previous = self.after[field]
            self.after[field] = 'different'
            self.assertEqual('BLOCKED', self.assess()['status'])
            self.after[field] = previous

    def test_unapproved_or_non_single_upgrade_blocks(self):
        self.expected['approved'] = False
        self.assertEqual('BLOCKED', self.assess()['status'])
        self.expected['approved'] = True
        self.expected['level_delta'] = 2
        self.assertEqual('BLOCKED', self.assess()['status'])

    def test_missing_or_tampered_png_blocks(self):
        self.before['screenshot']['sha256'] = '0' * 64
        self.assertEqual('BLOCKED', self.assess()['status'])
        self.before.pop('screenshot')
        self.assertEqual('BLOCKED', self.assess()['status'])

    def test_unknown_observer_or_unconfirmed_visual_blocks(self):
        self.before['observer'] = 'model-guess'
        self.assertEqual('BLOCKED', self.assess()['status'])
        self.before['observer'] = 'manual'
        self.before['visual_confirmed'] = False
        self.assertEqual('BLOCKED', self.assess()['status'])

    def test_does_not_mutate_supplied_snapshots(self):
        before = copy.deepcopy(self.before)
        self.assess()
        self.assertEqual(before, self.before)

    def test_decimal_arithmetic_does_not_round_large_precise_balances(self):
        self.before['coins'] = '10000000000000000000000000000.50'
        self.after['coins'] = '9999999999999999999999999994.75'
        self.assertEqual('PASS', self.assess()['status'])

    def test_cross_device_or_missing_device_cannot_be_combined(self):
        self.after['device_id'] = 'other-device'
        self.assertEqual('BLOCKED', self.assess()['status'])
        self.after['device_id'] = 'device-alias'
        self.before.pop('device_id')
        self.assertEqual('BLOCKED', self.assess()['status'])

    def test_wrong_window_context_blocks_even_with_level_failure(self):
        self.after['level'] = 4
        self.after['window_id'] = 'UnrelatedWindow'
        self.assertEqual('BLOCKED', self.assess()['status'])
        self.after['window_id'] = 'UIUpgradeWindow'
        self.expected.pop('window_id')
        self.assertEqual('BLOCKED', self.assess()['status'])

    def test_missing_trace_or_blank_reference_blocks(self):
        trace = self.expected.pop('trace')
        self.assertEqual('BLOCKED', self.assess()['status'])
        self.expected['trace'] = trace
        for field in trace:
            previous = trace[field]
            trace[field] = ' '
            self.assertEqual('BLOCKED', self.assess()['status'], field)
            trace[field] = previous

    def test_observation_and_evidence_ids_are_required(self):
        self.before.pop('observation_id')
        self.assertEqual('BLOCKED', self.assess()['status'])
        self.before['observation_id'] = 'observation_before'
        self.after['screenshot'].pop('evidence_id')
        self.assertEqual('BLOCKED', self.assess()['status'])

    def test_known_level_failure_is_not_hidden_by_unknown_income(self):
        self.after['level'] = 4
        self.expected['non_upgrade_delta'] = None
        result = self.assess()
        self.assertEqual('FAIL', result['status'])
        assertions = {item['name']: item for item in result['assertions']}
        self.assertEqual('FAIL', assertions['level']['status'])
        self.assertEqual('BLOCKED', assertions['coins']['status'])
        self.assertTrue(assertions['coins']['reasons'])

    def test_result_has_three_distinct_assertions_and_preserves_trace_ids(self):
        result = self.assess()
        self.assertEqual({'visual', 'level', 'coins'}, {item['name'] for item in result['assertions']})
        self.assertTrue(all(item['status'] == 'PASS' for item in result['assertions']))
        self.assertEqual(self.expected['trace'], result['trace'])
        self.assertEqual('observation_before', result['observations']['before']['observation_id'])
        self.assertEqual('evidence_before', result['evidence']['before']['evidence_id'])
        visual = next(item for item in result['assertions'] if item['name'] == 'visual')
        self.assertIn('manual', str(visual))

    def test_unknown_level_does_not_hide_known_money_failure(self):
        self.after['level'] = None
        self.after['coins'] = '90.50'
        result = self.assess()
        self.assertEqual('FAIL', result['status'])
        assertions = {item['name']: item for item in result['assertions']}
        self.assertEqual('BLOCKED', assertions['level']['status'])
        self.assertEqual('FAIL', assertions['coins']['status'])


if __name__ == '__main__':
    unittest.main()
