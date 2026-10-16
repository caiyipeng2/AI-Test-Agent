import copy
import unittest

from ai_test_agent.m1 import RuleBasedAnalyzer
from ai_test_agent.m1_gaps import GapValidationError, normalize_defect, normalize_gap_report


def source_document():
    return {
        "source_id": "board-contract",
        "source_version": "v1",
        "source_ref": "fixture/board",
        "permission": "user-approved",
        "sections": [
            {"section_id": "normal", "title": "Normal", "text": "The board shows the current status.", "locator": "p1"},
            {"section_id": "boundary", "title": "Boundary", "text": "Only when the limit is reached, show a warning.", "locator": "p2"},
            {"section_id": "exception", "title": "Exception", "text": "When the request fails, show an error.", "locator": "p3"},
            {"section_id": "uncovered", "title": "Uncovered", "text": "The help panel explains recovery.", "locator": "p4"},
        ],
    }


def analysis_result():
    return RuleBasedAnalyzer().analyze(source_document())


def defect(**overrides):
    value = {
        "defect_id": "def-1",
        "version": "v1",
        "title": "Warning is missing",
        "category": "known_bug",
        "severity": "high",
        "source_ref": "tracker/def-1",
        "source_version": "v1",
        "description": "The warning is not rendered at the limit.",
        "repro_steps": ["Open board", "Reach the limit"],
        "expected": "Warning is visible",
        "actual": "No warning is visible",
        "status": "confirmed",
        "public_safe": True,
        "source_anchors": [],
    }
    value.update(overrides)
    return value


class M1GapTests(unittest.TestCase):
    def test_normalize_defect_is_deterministic_and_preserves_safe_fields(self):
        first = normalize_defect(defect())
        second = normalize_defect(defect())
        self.assertEqual(first, second)
        self.assertEqual(first["defect_id"], "def-1")
        self.assertEqual(len(first["defect_digest"]), 64)

    def test_unknown_defect_defaults_to_unconfirmed(self):
        normalized = normalize_defect(defect(defect_id="unknown-1", category="unknown", status=None))
        self.assertEqual(normalized["status"], "unconfirmed")
        self.assertEqual(normalized["category"], "unknown")

    def test_empty_or_secret_values_are_rejected(self):
        with self.assertRaises(GapValidationError):
            normalize_defect(defect(title=" "))
        with self.assertRaises(GapValidationError):
            normalize_defect(defect(actual="Authorization: Bearer abcdefghijklmnop"))
        with self.assertRaises(GapValidationError):
            normalize_defect(defect(description="password=top-secret"))

    def test_gap_report_counts_categories_and_review_gaps(self):
        result = analysis_result()
        result["cases"] = [case for case in result["cases"] if all(anchor.get("section_id") != "uncovered" for anchor in case.get("source_anchors", []))]
        report = normalize_gap_report(result, [defect()])
        self.assertEqual(report["analysis_id"], result["analysis_id"])
        self.assertEqual(report["planned_cases"], len(result["cases"]))
        self.assertEqual(set(report["coverage_by_category"]), {"normal", "boundary", "exception"})
        self.assertGreaterEqual(report["coverage_by_category"]["normal"]["planned"], 1)
        self.assertTrue(report["unreviewed_cases"])
        self.assertIn("uncovered", report["uncovered_source_sections"])
        self.assertFalse(report["known_defects_linked"])
        self.assertTrue(report["blocked_reason"])

    def test_confirmed_known_defect_only_links_when_anchor_is_valid(self):
        result = analysis_result()
        anchor = copy.deepcopy(result["source"]["sections"][0])
        anchor = {"source_id": result["source"]["source_id"], "source_version": result["source"]["source_version"],
                  "section_id": anchor["section_id"], "locator": anchor["locator"], "section_sha256": anchor["section_sha256"]}
        report = normalize_gap_report(result, [defect(source_anchors=[anchor])])
        self.assertEqual(report["known_defects_linked"], ["def-1"])
        self.assertEqual(report["m0_defect_ids"], ["def-1"])

    def test_dangling_anchor_and_duplicate_defects_are_rejected(self):
        result = analysis_result()
        bad_anchor = {"source_id": "wrong", "source_version": "v1", "section_id": "normal", "locator": "p1", "section_sha256": "0" * 64}
        with self.assertRaises(GapValidationError):
            normalize_gap_report(result, [defect(source_anchors=[bad_anchor])])
        with self.assertRaises(GapValidationError):
            normalize_gap_report(result, [defect(), defect()])

    def test_missing_requirement_or_risk_links_are_reported(self):
        result = analysis_result()
        result["testpoints"] = []
        result["cases"] = []
        report = normalize_gap_report(result, [])
        self.assertTrue(report["requirements_without_testpoints"])
        self.assertTrue(report["risks_without_cases"])
        self.assertEqual(report["planned_cases"], 0)
        self.assertIn("no_planned_cases", report["blocked_reason"])

    def test_manual_result_history_is_summarized_without_creating_runs(self):
        result = analysis_result()
        result["result_history"] = [
            {"case_id": result["cases"][0]["case_id"], "decision": "PASS"},
            {"case_id": result["cases"][1]["case_id"], "decision": "BLOCKED"},
        ]
        report = normalize_gap_report(result, [])
        self.assertEqual(report["manual_result_summary"], {
            "recorded": 2,
            "pass": 1,
            "fail": 0,
            "blocked": 1,
            "cases_with_results": sorted([result["cases"][0]["case_id"], result["cases"][1]["case_id"]]),
            "unexecuted_cases": sorted(case["case_id"] for case in result["cases"][2:]),
            "latest_decision": "BLOCKED",
        })

    def test_manual_result_summary_counts_each_record_and_tracks_latest_case_state(self):
        result = analysis_result()
        result["result_history"] = [
            {"case_id": result["cases"][0]["case_id"], "decision": "PASS"},
            {"case_id": result["cases"][0]["case_id"], "decision": "FAIL"},
            {"case_id": result["cases"][1]["case_id"], "decision": "BLOCKED"},
        ]
        report = normalize_gap_report(result, [])
        summary = report["manual_result_summary"]
        self.assertEqual((summary["recorded"], summary["pass"], summary["fail"], summary["blocked"]), (3, 1, 1, 1))
        self.assertEqual(summary["cases_with_results"], sorted([result["cases"][0]["case_id"], result["cases"][1]["case_id"]]))
        self.assertEqual(summary["latest_decision"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
