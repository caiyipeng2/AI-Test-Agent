"""Device-independent M1 defect ingestion and coverage-gap reporting.

The module deliberately stops at analysis/reporting.  It does not create M0
defects or infer execution outcomes from historical defect records.  Inputs
are normalized into a small provider-neutral contract so that a tracker,
spreadsheet, or a model-generated result can feed the same report.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any


_CATEGORIES = {"known_bug", "environment", "tool", "requirement_gap", "unknown"}
_SEVERITIES = {"low", "medium", "high", "critical"}
_STATUSES = {"unconfirmed", "confirmed"}
_CASE_CATEGORIES = ("normal", "boundary", "exception")
_DEFECT_FIELDS = {
    "defect_id", "version", "title", "category", "severity", "source_ref",
    "source_version", "description", "repro_steps", "expected", "actual",
    "status", "public_safe", "source_anchors",
}
_ANCHOR_FIELDS = {"source_id", "source_version", "section_id", "locator", "section_sha256"}


class GapValidationError(ValueError):
    """Raised when defect input or a gap-report relation is not admissible."""


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GapValidationError(f"{field} must be a nonempty string")
    return value


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise GapValidationError("value is not canonical JSON") from error


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _stable_defect_id(value: dict[str, Any]) -> str:
    return f"defect-{_digest(value)[:20]}"


_SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret|authorization)\s*[:=]\s*\S+", re.I),
    # Compact JWT-like values are rejected even if the surrounding label was
    # omitted.  This is intentionally conservative for defect evidence.
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b(?:serial(?:\s*(?:number|no))?|imei|udid|android[_ -]?id)\s*[:=#-]?\s*[A-Za-z0-9]{6,}", re.I),
)
_SENSITIVE_FIELD_NAMES = re.compile(
    r"(?:token|secret|password|passwd|authorization|api[_-]?key|serial|imei|udid|android[_-]?id|device[_-]?id)", re.I
)


def _check_public_value(value: Any, field: str) -> None:
    """Reject credentials and device identifiers before they enter reports."""

    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_FIELD_NAMES.search(key):
                raise GapValidationError(f"{field}.{key} contains a sensitive field")
            _check_public_value(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_public_value(item, f"{field}[{index}]")
    elif isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                raise GapValidationError(f"{field} contains a credential or device identifier")


def _normalize_anchor(anchor: Any, index: int) -> dict[str, str]:
    if not isinstance(anchor, dict):
        raise GapValidationError(f"source_anchors[{index}] must be an object")
    unknown = sorted(set(anchor) - _ANCHOR_FIELDS)
    if unknown:
        raise GapValidationError(f"source_anchors[{index}] contains unknown field(s): {', '.join(unknown)}")
    result = {field: _nonempty(anchor.get(field), f"source_anchors[{index}].{field}") for field in _ANCHOR_FIELDS}
    if not re.fullmatch(r"[0-9a-f]{64}", result["section_sha256"]):
        raise GapValidationError(f"source_anchors[{index}].section_sha256 must be a SHA-256 digest")
    return result


def validate_defect(defect: dict[str, Any]) -> bool:
    """Validate a normalized or raw defect without mutating it."""

    if not isinstance(defect, dict):
        raise GapValidationError("defect must be an object")
    unknown = sorted(set(defect) - (_DEFECT_FIELDS | {"defect_digest"}))
    if unknown:
        raise GapValidationError(f"defect contains unknown field(s): {', '.join(unknown)}")
    for field in ("defect_id", "title", "source_ref", "source_version", "description", "expected", "actual"):
        _nonempty(defect.get(field), f"defect.{field}")
    version = defect.get("version")
    if isinstance(version, bool) or not isinstance(version, (int, str)) or (isinstance(version, str) and not version.strip()):
        raise GapValidationError("defect.version must be a nonempty string or integer")
    if defect.get("category") not in _CATEGORIES:
        raise GapValidationError("defect.category is invalid")
    if defect.get("severity") not in _SEVERITIES:
        raise GapValidationError("defect.severity is invalid")
    if defect.get("status") not in _STATUSES:
        raise GapValidationError("defect.status is invalid")
    if type(defect.get("public_safe")) is not bool:
        raise GapValidationError("defect.public_safe must be an explicit boolean")
    steps = defect.get("repro_steps")
    if not isinstance(steps, list) or not steps or any(not isinstance(step, str) or not step.strip() for step in steps):
        raise GapValidationError("defect.repro_steps must contain nonempty strings")
    anchors = defect.get("source_anchors")
    if not isinstance(anchors, list):
        raise GapValidationError("defect.source_anchors must be a list")
    for index, anchor in enumerate(anchors):
        _normalize_anchor(anchor, index)
    _check_public_value(defect, "defect")
    digest = defect.get("defect_digest")
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise GapValidationError("defect.defect_digest must be a SHA-256 digest")
    return True


def normalize_defect(defect: dict[str, Any]) -> dict[str, Any]:
    """Normalize an external historical defect to the M1 provider-neutral form.

    Missing status is intentionally treated as ``unconfirmed``.  A defect in
    the ``unknown`` category is never upgraded to confirmed by normalization;
    only a human can later classify it as a confirmed ``known_bug``.
    """

    if not isinstance(defect, dict):
        raise GapValidationError("defect must be an object")
    unknown = sorted(set(defect) - _DEFECT_FIELDS)
    if unknown:
        raise GapValidationError(f"defect contains unknown field(s): {', '.join(unknown)}")
    raw = deepcopy(defect)
    if "defect_id" not in raw:
        raw["defect_id"] = _stable_defect_id({key: raw.get(key) for key in sorted(_DEFECT_FIELDS - {"defect_id", "defect_digest"})})
    if "version" not in raw:
        raw["version"] = "v1"
    if "status" not in raw or raw["status"] is None:
        raw["status"] = "unconfirmed"
    if "public_safe" not in raw:
        raw["public_safe"] = False
    if "source_anchors" not in raw:
        raw["source_anchors"] = []
    normalized = {
        "defect_id": _nonempty(raw.get("defect_id"), "defect.defect_id"),
        "version": str(raw["version"]),
        "title": _nonempty(raw.get("title"), "defect.title"),
        # Unclassified tracker rows are retained as explicit unknown records;
        # they can never become an M0 defect without a human reclassification.
        "category": raw.get("category") if raw.get("category") is not None else "unknown",
        "severity": raw.get("severity"),
        "source_ref": _nonempty(raw.get("source_ref"), "defect.source_ref"),
        "source_version": _nonempty(raw.get("source_version"), "defect.source_version"),
        "description": _nonempty(raw.get("description"), "defect.description"),
        "repro_steps": deepcopy(raw.get("repro_steps")),
        "expected": _nonempty(raw.get("expected"), "defect.expected"),
        "actual": _nonempty(raw.get("actual"), "defect.actual"),
        "status": raw.get("status"),
        "public_safe": raw.get("public_safe"),
        "source_anchors": [_normalize_anchor(anchor, index) for index, anchor in enumerate(raw.get("source_anchors", []))],
    }
    if normalized["category"] == "unknown":
        normalized["status"] = "unconfirmed"
    validate_defect(normalized)
    normalized["defect_digest"] = _digest(normalized)
    return normalized


def _analysis_shape(analysis_result: dict[str, Any]) -> tuple[dict[str, Any], list[dict], list[dict], list[dict], list[dict]]:
    if not isinstance(analysis_result, dict):
        raise GapValidationError("analysis_result must be an object")
    analysis_id = _nonempty(analysis_result.get("analysis_id"), "analysis_result.analysis_id")
    source = analysis_result.get("source")
    if not isinstance(source, dict):
        raise GapValidationError("analysis_result.source must be an object")
    source_id = _nonempty(source.get("source_id"), "analysis_result.source.source_id")
    source_version = _nonempty(source.get("source_version"), "analysis_result.source.source_version")
    sections = source.get("sections")
    if not isinstance(sections, list) or any(not isinstance(section, dict) for section in sections):
        raise GapValidationError("analysis_result.source.sections must be a list of objects")
    section_ids: set[str] = set()
    for section in sections:
        section_id = _nonempty(section.get("section_id"), "source section.section_id")
        if section_id in section_ids:
            raise GapValidationError(f"duplicate source section: {section_id}")
        section_ids.add(section_id)
    collections = []
    for name in ("requirements", "risks", "testpoints", "cases"):
        value = analysis_result.get(name, [])
        if not isinstance(value, list):
            raise GapValidationError(f"analysis_result.{name} must be a list")
        collections.append(value)
    return {"source_id": source_id, "source_version": source_version, "sections": sections, "section_ids": section_ids, "analysis_id": analysis_id}, *collections


def _case_anchors(case: dict[str, Any]) -> list[dict[str, Any]]:
    anchors = case.get("source_anchors", case.get("anchors", []))
    return anchors if isinstance(anchors, list) else []


def normalize_gap_report(analysis_result: dict[str, Any], defects: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Build deterministic coverage and lineage gaps for an M1 analysis.

    ``known_defects_linked`` and ``m0_defect_ids`` intentionally only include
    confirmed ``known_bug`` records with valid anchors.  Environment, tool,
    requirement-gap, and unknown records remain report context and never
    become an M0 game defect implicitly.
    """

    source, requirements, risks, testpoints, cases = _analysis_shape(analysis_result)
    raw_defects = [] if defects is None else defects
    if not isinstance(raw_defects, list):
        raise GapValidationError("defects must be a list")
    normalized_defects: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    section_map = {section["section_id"]: section for section in source["sections"]}
    valid_anchor_keys = {
        (source["source_id"], source["source_version"], section_id,
         section.get("locator"), section.get("section_sha256"))
        for section_id, section in section_map.items()
    }
    for raw in raw_defects:
        item = normalize_defect(raw)
        if item["defect_id"] in seen_ids:
            raise GapValidationError(f"duplicate defect_id: {item['defect_id']}")
        seen_ids.add(item["defect_id"])
        for index, anchor in enumerate(item["source_anchors"]):
            key = (anchor["source_id"], anchor["source_version"], anchor["section_id"], anchor["locator"], anchor["section_sha256"])
            if key not in valid_anchor_keys:
                raise GapValidationError(f"defect {item['defect_id']} has a dangling source anchor at index {index}")
        normalized_defects.append(item)
    # Input order must not change a report digest.  This also gives callers a
    # stable display order when defects arrive from multiple trackers.
    normalized_defects.sort(key=lambda item: item["defect_id"])

    requirement_ids = []
    for item in requirements:
        rid = _nonempty(item.get("requirement_id"), "requirement.requirement_id")
        if rid in requirement_ids:
            raise GapValidationError(f"duplicate requirement_id: {rid}")
        requirement_ids.append(rid)
    risk_ids = []
    for item in risks:
        rid = _nonempty(item.get("risk_id"), "risk.risk_id")
        if rid in risk_ids:
            raise GapValidationError(f"duplicate risk_id: {rid}")
        risk_ids.append(rid)
    point_map: dict[str, dict[str, Any]] = {}
    for item in testpoints:
        point_id = _nonempty(item.get("testpoint_id"), "testpoint.testpoint_id")
        if point_id in point_map:
            raise GapValidationError(f"duplicate testpoint_id: {point_id}")
        point_map[point_id] = item

    case_ids: set[str] = set()
    point_categories: dict[str, str] = {}
    for point_id, point in point_map.items():
        category = point.get("category")
        if category in _CASE_CATEGORIES:
            point_categories[point_id] = category
    coverage: dict[str, dict[str, Any]] = {
        category: {
            "planned": 0, "reviewed": 0, "approved": 0, "unreviewed": 0,
            "executed": 0, "passed": 0, "failed": 0, "blocked": 0, "unexecuted": 0,
            "coverage_ratio": 0.0,
        }
        for category in _CASE_CATEGORIES
    }
    unreviewed_cases: list[str] = []
    unexecuted_cases: list[str] = []
    blocked_result_cases: list[str] = []
    case_source_sections: set[str] = set()
    case_requirement_ids: set[str] = set()
    case_risk_ids: set[str] = set()
    result_history = analysis_result.get("result_history", [])
    if not isinstance(result_history, list):
        raise GapValidationError("analysis_result.result_history must be a list")
    latest_results: dict[str, str] = {}
    for entry in result_history:
        if not isinstance(entry, dict):
            raise GapValidationError("analysis_result.result_history entries must be objects")
        result_case_id = _nonempty(entry.get("case_id"), "result_history.case_id")
        decision = entry.get("decision")
        if decision not in {"PASS", "FAIL", "BLOCKED"}:
            raise GapValidationError("result_history.decision must be PASS, FAIL, or BLOCKED")
        latest_results[result_case_id] = decision
    for case in cases:
        case_id = _nonempty(case.get("case_id"), "case.case_id")
        if case_id in case_ids:
            raise GapValidationError(f"duplicate case_id: {case_id}")
        case_ids.add(case_id)
        category = point_categories.get(case.get("testpoint_id"))
        if category in coverage:
            coverage[category]["planned"] += 1
            approved = case.get("approved") is True and case.get("review_status") == "APPROVED"
            if approved:
                coverage[category]["approved"] += 1
                coverage[category]["reviewed"] += 1
            else:
                coverage[category]["unreviewed"] += 1
                unreviewed_cases.append(case_id)
            coverage[category]["coverage_ratio"] = round(coverage[category]["approved"] / coverage[category]["planned"], 6)
            decision = latest_results.get(case_id)
            if decision is None:
                coverage[category]["unexecuted"] += 1
                unexecuted_cases.append(case_id)
            else:
                coverage[category]["executed"] += 1
                coverage[category][{"PASS": "passed", "FAIL": "failed", "BLOCKED": "blocked"}[decision]] += 1
                if decision == "BLOCKED":
                    blocked_result_cases.append(case_id)
        else:
            unreviewed_cases.append(case_id)
        requirement_id = case.get("requirement_id")
        risk_id = case.get("risk_id")
        if isinstance(requirement_id, str) and requirement_id:
            case_requirement_ids.add(requirement_id)
        if isinstance(risk_id, str) and risk_id:
            case_risk_ids.add(risk_id)
        for anchor in _case_anchors(case):
            if isinstance(anchor, dict) and isinstance(anchor.get("section_id"), str):
                case_source_sections.add(anchor["section_id"])
    unknown_result_cases = sorted(set(latest_results) - case_ids)
    if unknown_result_cases:
        raise GapValidationError(f"result_history references unknown case_id: {unknown_result_cases[0]}")

    requirements_with_points = {point.get("requirement_id") for point in point_map.values() if isinstance(point.get("requirement_id"), str)}
    requirements_without_testpoints = [rid for rid in requirement_ids if rid not in requirements_with_points]
    risks_without_cases = [rid for rid in risk_ids if rid not in case_risk_ids]
    uncovered_source_sections = [section["section_id"] for section in source["sections"] if section["section_id"] not in case_source_sections]
    linked_known = sorted(
        item["defect_id"] for item in normalized_defects
        if item["category"] == "known_bug" and any(anchor["section_id"] in section_map for anchor in item["source_anchors"])
    )
    m0_defect_ids = sorted(
        item["defect_id"] for item in normalized_defects
        if item["category"] == "known_bug" and item["status"] == "confirmed" and item["public_safe"] and item["defect_id"] in linked_known
    )
    blocked_reason: list[str] = []
    if not cases:
        blocked_reason.append("no_planned_cases")
    if unreviewed_cases:
        blocked_reason.append("unreviewed_cases")
    if uncovered_source_sections:
        blocked_reason.append("uncovered_source_sections")
    if requirements_without_testpoints:
        blocked_reason.append("requirements_without_testpoints")
    if risks_without_cases:
        blocked_reason.append("risks_without_cases")
    if any(item["status"] == "unconfirmed" for item in normalized_defects):
        blocked_reason.append("unconfirmed_defects")
    if unexecuted_cases:
        blocked_reason.append("unexecuted_cases")
    if blocked_result_cases:
        blocked_reason.append("blocked_results")
    result_decisions = [entry["decision"] for entry in result_history]
    manual_result_summary = {
        "recorded": len(result_history),
        "pass": sum(1 for decision in result_decisions if decision == "PASS"),
        "fail": sum(1 for decision in result_decisions if decision == "FAIL"),
        "blocked": sum(1 for decision in result_decisions if decision == "BLOCKED"),
        "cases_with_results": sorted(latest_results),
        "unexecuted_cases": sorted(set(unexecuted_cases)),
        "latest_decision": result_history[-1].get("decision") if result_history else None,
    }
    report = {
        "schema_version": 1,
        "analysis_id": source["analysis_id"],
        "planned_cases": len(cases),
        "coverage_by_category": coverage,
        "uncovered_source_sections": uncovered_source_sections,
        "requirements_without_testpoints": requirements_without_testpoints,
        "risks_without_cases": risks_without_cases,
        "known_defects_linked": linked_known,
        "m0_defect_ids": m0_defect_ids,
        "defects": normalized_defects,
        "unreviewed_cases": sorted(unreviewed_cases),
        "unexecuted_cases": sorted(set(unexecuted_cases)),
        "blocked_result_cases": sorted(set(blocked_result_cases)),
        "manual_result_summary": manual_result_summary,
        "blocked_reason": blocked_reason,
    }
    report["report_digest"] = _digest(report)
    return report
