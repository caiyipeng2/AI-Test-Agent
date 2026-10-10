import json
import unittest

from ai_test_agent.m1 import M1ValidationError, RuleBasedAnalyzer, promote_to_m0, review_result, validate_result


def source_document():
    return {
        'source_id': 'source-example-requirement',
        'source_version': '2026-10-10',
        'source_ref': 'fixture/requirements',
        'permission': 'user-approved',
        'sections': [
            {'section_id': 's1', 'title': 'Upgrade rule', 'text': 'The player must spend coins once to upgrade the device.', 'locator': 'p1'},
            {'section_id': 's2', 'title': 'Failure rule', 'text': 'The game must show an error when coins are insufficient or the request times out.', 'locator': 'p2'},
        ],
    }


class M1Tests(unittest.TestCase):
    def test_analyzer_outputs_source_anchored_risks_points_and_draft_cases(self):
        result = RuleBasedAnalyzer().analyze(source_document())
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['source']['source_id'], 'source-example-requirement')
        self.assertGreaterEqual(len(result['risks']), 1)
        self.assertGreaterEqual(len(result['testpoints']), 2)
        self.assertGreaterEqual(len(result['cases']), 2)
        for item in result['risks'] + result['testpoints'] + result['cases']:
            self.assertTrue(item['source_anchors'])
            self.assertEqual(item['review_status'], 'DRAFT')
        self.assertTrue(all(case['approved'] is False for case in result['cases']))

    def test_analyzer_does_not_invent_expected_values(self):
        result = RuleBasedAnalyzer().analyze(source_document())
        for case in result['cases']:
            self.assertIsNone(case['expectation'].get('cost'))
            self.assertIsNone(case['expectation'].get('level_delta'))
            self.assertTrue(case['needs_human_review'])

    def test_invalid_source_and_duplicate_section_ids_are_rejected(self):
        with self.assertRaises(M1ValidationError):
            RuleBasedAnalyzer().analyze({'source_id': 'x', 'sections': []})
        bad = source_document()
        bad['sections'][1]['section_id'] = 's1'
        with self.assertRaises(M1ValidationError):
            RuleBasedAnalyzer().analyze(bad)

    def test_validate_result_rejects_dangling_anchor_or_approved_unreviewed_case(self):
        result = RuleBasedAnalyzer().analyze(source_document())
        result['testpoints'][0]['source_anchors'][0]['section_id'] = 'missing'
        with self.assertRaises(M1ValidationError):
            validate_result(result)
        result = RuleBasedAnalyzer().analyze(source_document())
        result['cases'][0]['approved'] = True
        with self.assertRaises(M1ValidationError):
            validate_result(result)

    def test_review_updates_case_status_and_requires_explicit_reviewer(self):
        result = RuleBasedAnalyzer().analyze(source_document())
        with self.assertRaises(M1ValidationError):
            review_result(result, {'reviewer': '', 'decision': 'approved', 'comment': 'ok'})
        reviewed = review_result(result, {'reviewer': 'qa-owner', 'decision': 'changes_requested', 'comment': 'bind exact cost'})
        self.assertEqual(reviewed['review']['reviewer'], 'qa-owner')
        self.assertEqual(reviewed['review']['decision'], 'changes_requested')
        self.assertTrue(all(case['review_status'] == 'CHANGES_REQUESTED' for case in reviewed['cases']))
        self.assertFalse(any(case['approved'] for case in reviewed['cases']))
        self.assertEqual(len(reviewed['review']['history']), 1)

    def test_approve_requires_all_cases_have_independent_expectations(self):
        result = RuleBasedAnalyzer().analyze(source_document())
        with self.assertRaises(M1ValidationError):
            review_result(result, {'reviewer': 'qa-owner', 'decision': 'approved', 'comment': 'approved'})
        for case in result['cases']:
            case['expectation'] = {'cost': '3', 'level_delta': 1, 'target_id': 'target-1', 'window_id': 'upgrade'}
        reviewed = review_result(result, {'reviewer': 'qa-owner', 'decision': 'approved', 'comment': 'approved'})
        self.assertTrue(all(case['approved'] for case in reviewed['cases']))
        self.assertTrue(all(case['review_status'] == 'APPROVED' for case in reviewed['cases']))

    def test_anchor_digest_and_structured_confidence_are_required(self):
        result = RuleBasedAnalyzer().analyze(source_document())
        result['risks'][0]['source_anchors'][0]['section_sha256'] = '0' * 64
        with self.assertRaises(M1ValidationError):
            validate_result(result)
        result = RuleBasedAnalyzer().analyze(source_document())
        result['testpoints'][0]['confidence'] = 'heuristic'
        with self.assertRaises(M1ValidationError):
            validate_result(result)

    def test_promotion_preserves_source_and_stays_blocked_without_m0_bindings(self):
        result = RuleBasedAnalyzer().analyze(source_document())
        envelope = promote_to_m0(result)
        self.assertEqual(envelope['target_schema'], 'M0')
        self.assertEqual(envelope['status'], 'BLOCKED')
        self.assertIn('build_id', envelope['unresolved_bindings'])
        self.assertEqual(envelope['analysis_id'], result['analysis_id'])


if __name__ == '__main__':
    unittest.main()
