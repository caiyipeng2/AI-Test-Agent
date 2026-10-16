"""Device-independent manual execution result recording for M1.

The M1 analyzer produces reviewed case contracts, while execution belongs to a
later stage.  This module records a human observation against that contract
without creating a run, touching a device, or changing the analyzer result in
place.  A returned copy keeps an append-only ``result_history`` and the digest
of its most recent manual result.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .m1 import M1ValidationError, validate_result

_DECISIONS = {"PASS", "FAIL", "BLOCKED"}
_ASSERTION_STATUSES = {"PASS", "FAIL", "BLOCKED"}
_RESULT_REQUIRED = {
    "case_id",
    "run_id",
    "source_result_digest",
    "decision",
    "reviewer",
    "comment",
    "observations",
    "evidence_ids",
    "created_at",
}


class ResultValidationError(ValueError):
    """Raised when a manual result cannot be safely attached to an analysis."""


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ResultValidationError(f"{field} must be a nonempty string")
    return value


def _sha256(value, field):
    _text(value, field)
    if len(value) != 64:
        raise ResultValidationError(f"{field} must be a SHA-256 digest")
    try:
        int(value, 16)
    except ValueError as error:
        raise ResultValidationError(f"{field} must be a SHA-256 digest") from error
    return value


def _digest(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique_text_list(value, field, *, allow_empty=False):
    if not isinstance(value, list):
        raise ResultValidationError(f"{field} must be a list")
    if not allow_empty and not value:
        raise ResultValidationError(f"{field} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ResultValidationError(f"{field} must contain nonempty strings")
    if len(set(value)) != len(value):
        raise ResultValidationError(f"{field} must contain unique values")
    return value


def _validate_analysis(analysis_result):
    if not isinstance(analysis_result, dict):
        raise ResultValidationError("analysis_result must be an object")
    # Manual execution metadata is deliberately outside the M1 analyzer
    # digest.  Validate the immutable analyzer payload separately so appending
    # history does not make its provenance output_digest appear tampered.
    analyzer_payload = deepcopy(analysis_result)
    analyzer_payload.pop("result_history", None)
    analyzer_payload.pop("result_digest", None)
    try:
        validate_result(analyzer_payload)
    except (M1ValidationError, ValueError, TypeError) as error:
        raise ResultValidationError(f"invalid analysis_result: {error}") from error
    provenance = analysis_result.get("provenance")
    digest = provenance.get("output_digest") if isinstance(provenance, dict) else None
    _sha256(digest, "analysis_result.provenance.output_digest")
    return digest


def _validate_history(analysis_result):
    history = analysis_result.get("result_history", [])
    if not isinstance(history, list):
        raise ResultValidationError("analysis_result.result_history must be a list")
    for index, entry in enumerate(history):
        if not isinstance(entry, dict):
            raise ResultValidationError(f"result_history[{index}] must be an object")
        digest = entry.get("result_digest")
        _sha256(digest, f"result_history[{index}].result_digest")
        content = deepcopy(entry)
        content.pop("result_digest", None)
        if _digest(content) != digest:
            raise ResultValidationError(f"result_history[{index}] digest does not match result")
    if history and analysis_result.get("result_digest") != history[-1]["result_digest"]:
        raise ResultValidationError("analysis_result.result_digest must match latest history entry")
    if not history and "result_digest" in analysis_result:
        raise ResultValidationError("analysis_result.result_digest requires result_history")
    return history


def _case_for(analysis_result, case_id):
    cases = analysis_result.get("cases", [])
    matches = [case for case in cases if case.get("case_id") == case_id]
    if len(matches) != 1:
        raise ResultValidationError(f"unknown or duplicate case_id: {case_id}")
    return matches[0]


def _validate_result(analysis_result, result, source_digest):
    if not isinstance(result, dict):
        raise ResultValidationError("result must be an object")
    unknown = sorted(set(result) - _RESULT_REQUIRED)
    if unknown:
        raise ResultValidationError(f"result contains unknown field(s): {', '.join(unknown)}")
    missing = sorted(_RESULT_REQUIRED - set(result))
    if missing:
        raise ResultValidationError(f"result is missing fields: {', '.join(missing)}")
    _text(result["case_id"], "result.case_id")
    _text(result["run_id"], "result.run_id")
    if result["source_result_digest"] != source_digest:
        raise ResultValidationError("result.source_result_digest does not match analysis output_digest")
    if result["decision"] not in _DECISIONS:
        raise ResultValidationError("result.decision must be PASS, FAIL, or BLOCKED")
    _text(result["reviewer"], "result.reviewer")
    _text(result["comment"], "result.comment")
    _text(result["created_at"], "result.created_at")
    evidence_ids = _unique_text_list(result["evidence_ids"], "result.evidence_ids", allow_empty=True)
    observations = result["observations"]
    if not isinstance(observations, list) or not observations:
        raise ResultValidationError("result.observations must be a nonempty list")

    case = _case_for(analysis_result, result["case_id"])
    expectation = case.get("expectation", {})
    assertions = expectation.get("assertions", [])
    assertion_ids = {item.get("assertion_id") for item in assertions if isinstance(item, dict)}
    seen_assertions = set()
    observation_evidence = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            raise ResultValidationError(f"observations[{index}] must be an object")
        assertion_id = _text(observation.get("assertion_id"), f"observations[{index}].assertion_id")
        if assertion_id not in assertion_ids:
            raise ResultValidationError(f"unknown assertion_id: {assertion_id}")
        if assertion_id in seen_assertions:
            raise ResultValidationError(f"duplicate assertion_id: {assertion_id}")
        seen_assertions.add(assertion_id)
        if observation.get("status") not in _ASSERTION_STATUSES:
            raise ResultValidationError(f"observations[{index}].status is invalid")
        _text(observation.get("actual"), f"observations[{index}].actual")
        ids = _unique_text_list(observation.get("evidence_ids"), f"observations[{index}].evidence_ids", allow_empty=True)
        observation_evidence.extend(ids)

    if set(observation_evidence) - set(evidence_ids):
        raise ResultValidationError("observation evidence_ids must be listed in result.evidence_ids")
    if set(evidence_ids) != set(observation_evidence):
        raise ResultValidationError("result.evidence_ids must match observation evidence_ids")

    decision = result["decision"]
    if decision in {"PASS", "FAIL"}:
        if case.get("review_status") != "APPROVED" or case.get("approved") is not True:
            raise ResultValidationError("PASS or FAIL requires an APPROVED case")
        if not evidence_ids:
            raise ResultValidationError("PASS or FAIL requires evidence_ids")
    if decision == "PASS":
        if seen_assertions != assertion_ids or any(item["status"] != "PASS" for item in observations):
            raise ResultValidationError("PASS requires every case assertion to have PASS")
    elif decision == "FAIL":
        if not any(item["status"] == "FAIL" for item in observations):
            raise ResultValidationError("FAIL requires at least one failed assertion")
    elif decision == "BLOCKED":
        if not result["comment"].strip():
            raise ResultValidationError("BLOCKED requires a blocking reason in comment")
    return case


def record_manual_result(analysis_result, result):
    """Append one validated manual result to a deep copy of an M1 analysis.

    The analyzer output and its provenance digest remain unchanged.  The
    returned copy contains ``result_history`` and ``result_digest`` only; no
    M0 run is created and no device or execution service is called.
    """

    source_digest = _validate_analysis(analysis_result)
    _validate_history(analysis_result)
    candidate = deepcopy(analysis_result)
    entry = deepcopy(result)
    _validate_result(candidate, entry, source_digest)
    entry["result_digest"] = _digest(entry)
    candidate.setdefault("result_history", []).append(entry)
    candidate["result_digest"] = entry["result_digest"]
    return candidate


__all__ = ["ResultValidationError", "record_manual_result"]
