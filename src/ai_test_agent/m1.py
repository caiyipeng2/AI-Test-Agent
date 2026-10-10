"""M1 device-free requirement analysis and human review contract.

The default analyzer is intentionally deterministic. It creates reviewable drafts
from normalized source sections; it does not call a model or invent product values.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone

from .requirements import M1InputError, normalize_source


SCHEMA_VERSION = 1
_DECISIONS = {"approved", "changes_requested", "rejected"}
_ANCHOR_FIELDS = ("source_id", "source_version", "section_id", "locator")


class M1ValidationError(ValueError):
    pass


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise M1ValidationError(f"{name} must be a nonempty string")
    return value.strip()


def _stable_id(prefix, value):
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _anchor(source, section):
    return {"source_id": source["source_id"], "source_version": source["source_version"],
            "section_id": section["section_id"], "locator": section["locator"],
            "section_sha256": hashlib.sha256(section["text"].encode("utf-8")).hexdigest()}


def _confidence(basis="heuristic"):
    return {"level": "low", "score": 0.35, "basis": basis}


def _execution_expectation_valid(expectation):
    if not isinstance(expectation, dict):
        return False
    if not isinstance(expectation.get("target_id"), str) or not expectation["target_id"].strip():
        return False
    if not isinstance(expectation.get("window_id"), str) or not expectation["window_id"].strip():
        return False
    if type(expectation.get("level_delta")) is not int or expectation["level_delta"] != 1:
        return False
    for field in ("cost", "non_upgrade_delta"):
        value = expectation.get(field)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return False
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", str(value)):
            return False
    return True


def _normalize_source(source):
    try:
        return normalize_source(source)
    except M1InputError as error:
        raise M1ValidationError(str(error)) from error


class RuleBasedAnalyzer:
    """Portable deterministic baseline used before a model provider is approved."""

    analyzer_id = "rule-based-m1-v1"

    def analyze(self, source):
        source = _normalize_source(source)
        risks = []
        points = []
        cases = []
        for section in source["sections"]:
            anchor = _anchor(source, section)
            key = f"{source['source_id']}:{source['source_version']}:{section['section_id']}"
            text = section["text"]
            lower = text.lower()
            risk_terms = [term for term in ("must", "不得", "失败", "timeout", "超时", "insufficient", "不足", "重复", "网络") if term in lower or term in text]
            if risk_terms:
                risks.append({
                    "risk_id": _stable_id("risk", key), "title": f"Review risk: {section['title']}",
                    "description": f"The source contains behavior-sensitive terms: {', '.join(risk_terms)}.",
                    "severity": "medium", "source_anchors": [anchor], "review_status": "DRAFT",
                    "confidence": _confidence(), "needs_human_review": True,
                })
            point_id = _stable_id("testpoint", key)
            points.append({
                "testpoint_id": point_id, "title": f"Verify: {section['title']}",
                "question": f"What observable behavior proves this rule? Source says: {text}",
                "source_anchors": [anchor], "review_status": "DRAFT", "confidence": _confidence(),
                "needs_human_review": True,
            })
            cases.append({
                "case_id": _stable_id("case", key), "version": 1,
                "title": f"Draft check: {section['title']}",
                "source_anchors": [anchor], "risk_ids": [risk["risk_id"] for risk in risks if risk["source_anchors"] == [anchor]],
                "testpoint_ids": [point_id],
                "confidence": _confidence(),
                "steps": ["Establish the reviewed precondition", "Perform the single behavior described by the source", "Capture independent evidence"],
                "expectation": {"target_id": None, "window_id": None, "level_delta": None, "cost": None, "assertions": []},
                "approved": False, "review_status": "DRAFT", "needs_human_review": True,
            })
        if not risks:
            fallback_risk = {"risk_id": _stable_id("risk", source["source_id"]), "title": "Unclassified source rule risk",
                          "description": "The source has not been risk-reviewed by a human.", "severity": "unknown",
                          "source_anchors": [_anchor(source, source["sections"][0])], "review_status": "DRAFT",
                          "confidence": _confidence(), "needs_human_review": True}
            risks.append(fallback_risk)
            for case in cases:
                if not case["risk_ids"]:
                    case["risk_ids"] = [fallback_risk["risk_id"]]
        analysis_id = _stable_id("analysis", f"{source['source_id']}:{source['source_version']}:{source['content_sha256']}:{self.analyzer_id}")
        result = {"schema_version": SCHEMA_VERSION, "analysis_id": analysis_id, "analyzer_id": self.analyzer_id,
                  "source": source, "provenance": {"analyzer_id": self.analyzer_id, "mode": "deterministic",
                   "model_id": None, "prompt_hash": None, "input_digest": source["content_sha256"], "created_at": None},
                  "questions": [], "coverage_gaps": ["formal acceptance values require human review"],
                  "risks": risks, "testpoints": points, "cases": cases,
                  "review": {"status": "PENDING", "reviewer": None, "decision": None, "comment": None, "history": []},
                  "promotion": None}
        validate_result(result)
        return result


def validate_result(result):
    if not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION:
        raise M1ValidationError("unsupported M1 result schema")
    source = _normalize_source(result.get("source"))
    source_sections = {section["section_id"] for section in source["sections"]}
    source_id, source_version = source["source_id"], source["source_version"]
    for collection in ("risks", "testpoints", "cases"):
        if not isinstance(result.get(collection), list):
            raise M1ValidationError(f"{collection} must be a list")
        ids = set()
        id_field = {"risks": "risk_id", "testpoints": "testpoint_id", "cases": "case_id"}[collection]
        for item in result[collection]:
            if not isinstance(item, dict) or not isinstance(item.get(id_field), str) or item[id_field] in ids:
                raise M1ValidationError(f"invalid or duplicate {collection} id")
            ids.add(item[id_field])
            anchors = item.get("source_anchors")
            if not isinstance(anchors, list) or not anchors:
                raise M1ValidationError(f"{collection} requires source_anchors")
            for anchor in anchors:
                if not isinstance(anchor, dict) or any(anchor.get(key) != expected for key, expected in
                    (("source_id", source_id), ("source_version", source_version))) or anchor.get("section_id") not in source_sections:
                    raise M1ValidationError(f"{collection} contains a dangling source anchor")
                section = next(section for section in source["sections"] if section["section_id"] == anchor["section_id"])
                if anchor.get("locator") != section["locator"] or anchor.get("section_sha256") != hashlib.sha256(section["text"].encode("utf-8")).hexdigest():
                    raise M1ValidationError(f"{collection} contains a stale source anchor")
            confidence = item.get("confidence")
            if not isinstance(confidence, dict) or confidence.get("level") not in {"low", "medium", "high"} or not isinstance(confidence.get("basis"), str):
                raise M1ValidationError(f"{collection} requires structured confidence")
            if item.get("review_status") not in {"DRAFT", "CHANGES_REQUESTED", "APPROVED", "REJECTED"}:
                raise M1ValidationError(f"invalid {collection} review_status")
    for case in result["cases"]:
        if case.get("approved") is True and case.get("review_status") != "APPROVED":
            raise M1ValidationError("approved case must have APPROVED review_status")
    review = result.get("review")
    if not isinstance(review, dict) or review.get("status") not in {"PENDING", "REVIEWED"}:
        raise M1ValidationError("review status is invalid")
    if not isinstance(review.get("history", []), list):
        raise M1ValidationError("review history must be a list")
    return True


def import_result(result):
    """Import a session/model-produced result only after strict local validation."""
    result = deepcopy(result)
    validate_result(result)
    return result


def review_result(result, review):
    result = deepcopy(result)
    validate_result(result)
    if not isinstance(review, dict) or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise M1ValidationError("reviewer is required")
    decision = review.get("decision")
    if decision not in _DECISIONS:
        raise M1ValidationError("decision must be approved, changes_requested or rejected")
    comment = _text(review.get("comment"), "review.comment")
    if result.get("review", {}).get("decision") == "rejected":
        raise M1ValidationError("rejected analysis is terminal; create a new revision")
    if decision == "approved":
        for case in result["cases"]:
            expectation = case.get("expectation")
            if not _execution_expectation_valid(expectation):
                raise M1ValidationError("all case expectations must be independently filled before approval")
    status = {"approved": "APPROVED", "changes_requested": "CHANGES_REQUESTED", "rejected": "REJECTED"}[decision]
    for case in result["cases"]:
        case["review_status"] = status
        case["approved"] = decision == "approved"
        case["needs_human_review"] = decision != "approved"
    history = list(result.get("review", {}).get("history", []))
    history.append({"reviewer": review["reviewer"], "decision": decision, "comment": comment,
                    "created_at": datetime.now(timezone.utc).isoformat(), "analysis_id": result.get("analysis_id")})
    result["review"] = {"status": "REVIEWED", "reviewer": review["reviewer"], "decision": decision, "comment": comment, "history": history}
    validate_result(result)
    return result


def promote_to_m0(result, bindings=None):
    """Create a blocked M0 handoff envelope without inventing project bindings."""
    validate_result(result)
    bindings = bindings or {}
    unresolved = []
    if result.get("review", {}).get("decision") != "approved":
        unresolved.append("human_review")
    if any(case.get("expectation", {}).get("target_id") in (None, "") for case in result["cases"]):
        unresolved.append("independent_expectation")
    unresolved.extend([field for field in ("requirement_id", "build_id", "environment", "device_alias", "account_alias", "initial_state") if not bindings.get(field)])
    if result["source"].get("permission") != "user-approved":
        unresolved.append("source_not_authorized")
    case_drafts = [{**deepcopy(case), "requirement_id": bindings.get("requirement_id"),
                    "source_id": result["source"]["source_id"], "source_ref": result["source"]["source_ref"],
                    "build_id": bindings.get("build_id"), "environment": bindings.get("environment"),
                    "device_alias": bindings.get("device_alias"), "account_alias": bindings.get("account_alias"),
                    "initial_state": bindings.get("initial_state"), "approved": False}
                   for case in result["cases"]]
    return {"schema_version": 1, "status": "BLOCKED" if unresolved else "READY_FOR_M0_REVIEW",
            "target_schema": "M0", "analysis_id": result.get("analysis_id"),
            "source_id": result["source"]["source_id"], "source_version": result["source"]["source_version"],
            "case_drafts": case_drafts, "risk_drafts": deepcopy(result["risks"]),
            "testpoint_drafts": deepcopy(result["testpoints"]), "unresolved_bindings": sorted(set(unresolved)),
            "provenance": deepcopy(result.get("provenance"))}
