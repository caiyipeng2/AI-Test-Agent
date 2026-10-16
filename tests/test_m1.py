import copy
import unittest

from ai_test_agent.m0 import M0Registry
from ai_test_agent.m1 import (
    M1ValidationError,
    RuleBasedAnalyzer,
    analyze_with_provider,
    import_result,
    promote_to_m0,
    review_result,
    summarize_result,
    validate_result,
)


def source_document(permission="user-approved", text=None):
    return {
        "source_id": "dialog-board-contract",
        "source_version": "v1",
        "source_ref": "fixture/dialog-board",
        "permission": permission,
        "sections": [
            {"section_id": "dialog", "title": "Dialog", "text": text or "The board shows the dialog when the player taps the button.", "locator": "p1"},
            {"section_id": "board", "title": "Board", "text": "The board displays the current status.", "locator": "p2"},
        ],
    }


def reviewed_result():
    draft = RuleBasedAnalyzer().analyze(source_document())
    return review_result(draft, {"reviewer": "qa-simulated", "decision": "approved", "comment": "simulation", "case_ids": [case["case_id"] for case in draft["cases"]]})


class M1Tests(unittest.TestCase):
    def test_rule_analyzer_is_provider_neutral_and_maps_every_case(self):
        result = RuleBasedAnalyzer().analyze(source_document())
        self.assertEqual(result["provenance"]["mode"], "rule_based")
        self.assertIsNone(result["provenance"]["model_id"])
        self.assertTrue(result["requirements"] and result["risks"] and result["testpoints"])
        self.assertGreaterEqual(len(result["testpoints"]), 3)
        risk_ids = {risk["risk_id"] for risk in result["risks"]}
        for case in result["cases"]:
            self.assertIn(case["risk_id"], risk_ids)
            self.assertFalse(case["approved"])
            self.assertEqual(case["review_status"], "DRAFT")
        self.assertEqual(result["source"]["sections"][0]["text"], source_document()["sections"][0]["text"])

    def test_unknown_source_can_be_analyzed_but_not_approved(self):
        draft = RuleBasedAnalyzer().analyze(source_document(permission="unverified"))
        with self.assertRaises(M1ValidationError):
            review_result(draft, {"reviewer": "qa", "decision": "approved", "comment": "try"})

    def test_ordinary_cases_without_amounts_can_be_approved(self):
        reviewed = reviewed_result()
        self.assertTrue(all(case["approved"] for case in reviewed["cases"]))
        self.assertTrue(all(case["review"]["reviewed_digest"] for case in reviewed["cases"]))
        self.assertEqual(len(reviewed["review"]["history"]), 1)
        validate_result(reviewed)

    def test_unknown_expectation_cannot_be_approved(self):
        draft = RuleBasedAnalyzer().analyze(source_document())
        draft["cases"][0]["expectation"]["unknowns"] = ["exact latency"]
        with self.assertRaises(M1ValidationError):
            review_result(draft, {"reviewer": "qa", "decision": "approved", "comment": "incomplete"})

    def test_anchor_tamper_and_mapping_tamper_are_rejected(self):
        draft = RuleBasedAnalyzer().analyze(source_document())
        draft["risks"][0]["anchors"][0]["locator"] = "tampered"
        with self.assertRaises(M1ValidationError):
            validate_result(draft)
        draft = RuleBasedAnalyzer().analyze(source_document())
        draft["cases"][0]["requirement_id"] = "unknown"
        with self.assertRaises(M1ValidationError):
            validate_result(draft)

    def test_content_change_changes_stable_ids(self):
        first = RuleBasedAnalyzer().analyze(source_document())
        changed = RuleBasedAnalyzer().analyze(source_document(text="The board shows another dialog when the player taps the button."))
        self.assertNotEqual(first["analysis_id"], changed["analysis_id"])
        self.assertNotEqual(first["requirements"][0]["requirement_id"], changed["requirements"][0]["requirement_id"])

    def test_rejected_case_is_terminal_and_review_does_not_mutate_input(self):
        draft = RuleBasedAnalyzer().analyze(source_document())
        original = copy.deepcopy(draft)
        rejected = review_result(draft, {"reviewer": "qa", "decision": "rejected", "comment": "not accepted", "case_ids": [draft["cases"][0]["case_id"]]})
        self.assertEqual(draft, original)
        with self.assertRaises(M1ValidationError):
            review_result(rejected, {"reviewer": "qa", "decision": "approved", "comment": "reapprove", "case_ids": [rejected["cases"][0]["case_id"]]})

    def test_approved_case_content_change_invalidates_review(self):
        approved = reviewed_result()
        approved["cases"][0]["steps"].append("tampered after approval")
        with self.assertRaises(M1ValidationError):
            validate_result(approved)

    def test_fake_approved_import_is_rejected(self):
        fake = RuleBasedAnalyzer().analyze(source_document())
        fake["cases"][0]["approved"] = True
        fake["cases"][0]["review_status"] = "APPROVED"
        fake["provenance"]["mode"] = "session_ai"
        fake["provenance"]["model_id"] = "fake"
        with self.assertRaises(M1ValidationError):
            import_result(fake)

    def test_session_provider_output_is_imported_without_remote_api(self):
        draft = RuleBasedAnalyzer().analyze(source_document())
        draft["provenance"]["mode"] = "session_ai"
        draft["provenance"]["model_id"] = "fixture-model"
        imported = analyze_with_provider(source_document(), lambda _source: draft)
        self.assertEqual(imported["provenance"]["mode"], "session_ai")
        self.assertEqual(imported["provenance"]["model_id"], "fixture-model")

    def test_missing_bindings_block_handoff(self):
        envelope = promote_to_m0(reviewed_result())
        self.assertEqual(envelope["status"], "BLOCKED")
        self.assertIn("registry", envelope["unresolved_bindings"])
        self.assertTrue(all(case["approved"] is False for case in envelope["case_drafts"]))

    def test_valid_bindings_register_traceable_draft_cases_without_runs(self):
        registry = M0Registry.create({"project_id": "p1", "name": "Dialog Board"})
        registry.register_build("build-1", {
            "artifact_type": "apk", "artifact_path": "build/app.apk", "artifact_sha256": "a" * 64,
            "package_name": "com.example.game", "version_code": 1, "version_name": "1.0",
            "environment": "qa", "environment_source": "fixture", "runtime": {
                "server": "qa", "resource_version": "r1", "dll_version": "d1", "verified": True,
            },
        })
        envelope = promote_to_m0(reviewed_result(), {
            "build_id": "build-1", "environment": "qa", "device_alias": "device-1",
            "account_alias": "account-1", "initial_state": {"screen": "board"}, "max_wait_seconds": 30,
        }, registry)
        self.assertEqual(envelope["status"], "READY_FOR_M0_REVIEW")
        self.assertEqual(envelope["unresolved_bindings"], [])
        self.assertEqual(envelope["registry"]["runs"], {})
        M0Registry(envelope["registry"]).validate()

    def test_summary_reports_counts(self):
        result = reviewed_result()
        summary = summarize_result(result)
        self.assertEqual(summary["cases"], len(result["cases"]))
        self.assertEqual(summary["approved_cases"], len(result["cases"]))


if __name__ == "__main__":
    unittest.main()
