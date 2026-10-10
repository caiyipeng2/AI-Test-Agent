import copy
import unittest

from ai_test_agent.m1 import RuleBasedAnalyzer, review_result
from ai_test_agent.m1_results import ResultValidationError, record_manual_result


def source_document():
    return {
        "source_id": "manual-result-fixture",
        "source_version": "v1",
        "source_ref": "fixture/manual-result",
        "permission": "user-approved",
        "sections": [
            {
                "section_id": "flow",
                "title": "Core flow",
                "text": "The player can open the board.",
                "locator": "p1",
            }
        ],
    }


def approved_analysis():
    draft = RuleBasedAnalyzer().analyze(source_document())
    return review_result(
        draft,
        {
            "reviewer": "qa-owner",
            "decision": "approved",
            "comment": "approved for manual execution",
            "case_ids": [case["case_id"] for case in draft["cases"]],
        },
    )


def manual_input(analysis, *, decision="PASS", case=None, **overrides):
    case = case or analysis["cases"][0]
    assertion_ids = [item["assertion_id"] for item in case["expectation"]["assertions"]]
    observations = [
        {
            "assertion_id": assertion_id,
            "status": "PASS" if decision == "PASS" else decision,
            "actual": "observed",
            "evidence_ids": [f"evidence-{index}"],
        }
        for index, assertion_id in enumerate(assertion_ids, 1)
    ]
    result = {
        "case_id": case["case_id"],
        "run_id": "manual-run-001",
        "source_result_digest": analysis["provenance"]["output_digest"],
        "decision": decision,
        "reviewer": "manual-qa",
        "comment": "manual observation" if decision != "BLOCKED" else "environment unavailable",
        "observations": observations,
        "evidence_ids": [item for observation in observations for item in observation["evidence_ids"]],
        "created_at": "2026-10-10T00:00:00+00:00",
    }
    result.update(overrides)
    return result


class ManualResultTests(unittest.TestCase):
    def test_pass_appends_history_and_does_not_mutate_analysis(self):
        analysis = approved_analysis()
        original = copy.deepcopy(analysis)
        recorded = record_manual_result(analysis, manual_input(analysis))

        self.assertEqual(analysis, original)
        self.assertEqual(len(recorded["result_history"]), 1)
        self.assertEqual(recorded["result_history"][0]["decision"], "PASS")
        self.assertEqual(recorded["result_history"][0]["result_digest"], recorded["result_digest"])
        self.assertEqual(recorded["provenance"]["output_digest"], analysis["provenance"]["output_digest"])

    def test_fail_requires_failed_assertion_and_evidence(self):
        analysis = approved_analysis()
        result = manual_input(analysis, decision="FAIL")
        recorded = record_manual_result(analysis, result)
        self.assertEqual(recorded["result_history"][0]["observations"][0]["status"], "FAIL")

        no_failure = manual_input(analysis, decision="FAIL")
        no_failure["observations"][0]["status"] = "PASS"
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, no_failure)

        no_evidence = manual_input(analysis, decision="FAIL", evidence_ids=[])
        no_evidence["observations"][0]["evidence_ids"] = []
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, no_evidence)

    def test_blocked_can_be_recorded_before_case_approval_but_needs_reason(self):
        draft = RuleBasedAnalyzer().analyze(source_document())
        blocked = manual_input(draft, decision="BLOCKED")
        recorded = record_manual_result(draft, blocked)
        self.assertEqual(recorded["result_history"][0]["decision"], "BLOCKED")

        blocked["comment"] = ""
        with self.assertRaises(ResultValidationError):
            record_manual_result(draft, blocked)

    def test_digest_case_and_assertion_references_are_strict(self):
        analysis = approved_analysis()
        wrong_digest = manual_input(analysis, source_result_digest="0" * 64)
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, wrong_digest)

        wrong_case = manual_input(analysis, case_id="missing-case")
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, wrong_case)

        unknown_assertion = manual_input(analysis)
        unknown_assertion["observations"][0]["assertion_id"] = "missing-assertion"
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, unknown_assertion)

    def test_evidence_ids_are_nonempty_and_unique(self):
        analysis = approved_analysis()
        duplicate = manual_input(analysis)
        duplicate["evidence_ids"] = ["same", "same"]
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, duplicate)

        empty = manual_input(analysis)
        empty["evidence_ids"] = [""]
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, empty)

    def test_input_analysis_review_fields_are_strict(self):
        analysis = approved_analysis()
        analysis["cases"][0]["approved"] = False
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, manual_input(analysis))

    def test_result_rejects_unknown_fields(self):
        analysis = approved_analysis()
        extra = manual_input(analysis)
        extra["unexpected"] = "must not be silently persisted"
        with self.assertRaises(ResultValidationError):
            record_manual_result(analysis, extra)

    def test_history_is_append_only_and_previous_entries_are_retained(self):
        analysis = approved_analysis()
        first = record_manual_result(analysis, manual_input(analysis))
        second = record_manual_result(first, manual_input(first, decision="FAIL"))
        self.assertEqual(len(second["result_history"]), 2)
        self.assertEqual(second["result_history"][0]["decision"], "PASS")
        self.assertEqual(second["result_history"][1]["decision"], "FAIL")


if __name__ == "__main__":
    unittest.main()
