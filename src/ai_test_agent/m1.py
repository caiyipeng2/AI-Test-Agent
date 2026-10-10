"""Provider-neutral M1 requirement analysis, review, and M0 handoff."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone

from .requirements import M1InputError, normalize_source

SCHEMA_VERSION = 1
ANALYZER_VERSION = "m1-provider-neutral-v1"
_DECISIONS = {"approved", "changes_requested", "rejected"}
_STATUSES = {"DRAFT", "CHANGES_REQUESTED", "APPROVED", "REJECTED"}
_CATEGORIES = {"normal", "boundary", "exception"}


class M1ValidationError(ValueError):
    pass


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise M1ValidationError(f"{name} must be a nonempty string")
    return value


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable(prefix, *parts):
    joined = "\x1f".join(str(part) for part in parts)
    return f"{prefix}-{_digest(joined)[:20]}"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _source(source):
    try:
        return normalize_source(source)
    except M1InputError as error:
        raise M1ValidationError(str(error)) from error


def _anchor(source, section):
    return {"source_id": source["source_id"], "source_version": source["source_version"],
            "section_id": section["section_id"], "locator": section["locator"],
            "section_sha256": section["section_sha256"]}


def _confidence(level="low", basis="deterministic source template"):
    return {"level": level, "score": 0.35 if level == "low" else 0.7, "basis": basis}


def _case_content(case):
    return {key: deepcopy(value) for key, value in case.items()
            if key not in {"review", "review_status", "approved", "needs_human_review"}}


def _review_digest(case):
    payload = json.dumps(_case_content(case), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _digest(payload)


def _selected_digest(cases, selected):
    payload = [_case_content(case) for case in cases if case["case_id"] in selected]
    return _digest(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class RuleBasedAnalyzer:
    analyzer_id = "rule-based-m1-v1"

    def analyze(self, source):
        source = _source(source)
        requirements, risks, points, cases = [], [], [], []
        for section in source["sections"]:
            anchor = _anchor(source, section)
            key = (source["content_sha256"], section["section_id"], section["text"])
            requirement_id = _stable("req", *key)
            risk_id = _stable("risk", *key)
            point_id = _stable("point", *key)
            case_id = _stable("case", *key)
            rule = section["text"]
            requirements.append({"requirement_id": requirement_id, "title": section["title"],
                                 "rule": rule, "version": 1, "source_anchors": [anchor]})
            risks.append({"risk_id": risk_id, "requirement_id": requirement_id,
                          "description": f"Review the observable behavior required by: {rule}",
                          "severity": "medium", "source_anchors": [anchor], "anchors": [anchor]})
            lower = rule.lower()
            categories = [("normal", point_id, case_id)]
            if any(word in lower for word in ("when", "if", "error", "fail", "timeout", "异常", "失败")):
                categories.append(("exception", _stable("point", *key, "exception"), _stable("case", *key, "exception")))
            if any(word in lower for word in ("only when", "only", "unless", "limit", "boundary", "仅当", "只有", "边界")):
                categories.append(("boundary", _stable("point", *key, "boundary"), _stable("case", *key, "boundary")))
            for category, current_point_id, current_case_id in categories:
                points.append({"testpoint_id": current_point_id, "requirement_id": requirement_id,
                           "risk_id": risk_id, "title": f"Verify: {section['title']}",
                           "category": category, "source_anchors": [anchor], "anchors": [anchor]})
                case = {"case_id": current_case_id, "requirement_id": requirement_id, "risk_id": risk_id,
                    "testpoint_id": current_point_id, "title": f"Draft check: {section['title']}", "version": 1,
                    "preconditions": ["Establish the reviewed precondition"],
                    "steps": ["Perform the behavior described by the source"],
                    "expectation": {"assertions": [{"assertion_id": _stable("assert", *key),
                        "description": "Observe the source rule", "expected": rule,
                        "evidence_requirements": ["independent observation"]}], "unknowns": []},
                    "recovery": {"method": "Restore the reviewed initial state", "approved": False},
                    "source_anchors": [anchor], "review_status": "DRAFT", "approved": False,
                    "needs_human_review": True, "confidence": _confidence(), "review": None}
                cases.append(case)
        result = {"schema_version": SCHEMA_VERSION,
                  "analysis_id": _stable("analysis", source["content_sha256"], self.analyzer_id, ANALYZER_VERSION),
                  "source": source,
                  "provenance": {"analyzer_id": self.analyzer_id, "mode": "rule_based", "model_id": None,
                                 "input_digest": source["content_sha256"], "created_at": _now(), "output_digest": None},
                  "requirements": requirements, "risks": risks, "testpoints": points, "cases": cases,
                  "questions": [], "gaps": [],
                  "review": {"status": "PENDING", "reviewer": None, "decision": None, "comment": None, "history": []}}
        result["provenance"]["output_digest"] = _result_digest(result)
        validate_result(result)
        return result


def _result_digest(result):
    value = deepcopy(result)
    value.pop("provenance", None)
    return _digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _all_ids(items, field, collection):
    ids = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(field), str) or not item[field].strip() or item[field] in ids:
            raise M1ValidationError(f"invalid or duplicate {collection} id")
        ids.add(item[field])
    return ids


def _validate_confidence(confidence, where):
    if not isinstance(confidence, dict) or confidence.get("level") not in {"low", "medium", "high"} or not _text(confidence.get("basis"), where + ".basis"):
        raise M1ValidationError(f"{where} requires confidence level and basis")
    score = confidence.get("score")
    if score is not None and (type(score) not in (int, float) or isinstance(score, bool) or not math.isfinite(score) or not 0 <= score <= 1):
        raise M1ValidationError(f"{where}.score must be finite in [0,1]")


def validate_result(result):
    if not isinstance(result, dict) or result.get("schema_version") != SCHEMA_VERSION:
        raise M1ValidationError("unsupported M1 result schema")
    source = _source(result.get("source"))
    if result.get("source") != source:
        raise M1ValidationError("source is not normalized")
    provenance = result.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("mode") not in {"rule_based", "session_ai"}:
        raise M1ValidationError("invalid provenance mode")
    if provenance.get("input_digest") != source["content_sha256"] or not _text(provenance.get("analyzer_id"), "provenance.analyzer_id"):
        raise M1ValidationError("provenance input digest does not match source")
    if provenance.get("model_id") is not None and (not isinstance(provenance.get("model_id"), str) or not provenance["model_id"].strip()):
        raise M1ValidationError("provenance.model_id must be null or nonempty")
    if provenance.get("mode") == "session_ai" and provenance.get("model_id") is None:
        raise M1ValidationError("session_ai provenance requires model_id")
    if provenance.get("mode") == "rule_based" and provenance.get("model_id") is not None:
        raise M1ValidationError("rule_based provenance cannot claim a model")
    if not isinstance(provenance.get("created_at"), str) or not provenance["created_at"].strip():
        raise M1ValidationError("provenance.created_at is required")
    if not isinstance(provenance.get("output_digest"), str) or len(provenance["output_digest"]) != 64 or provenance["output_digest"] != _result_digest(result):
        raise M1ValidationError("provenance output digest does not match result")
    expected_analysis_id = _stable("analysis", source["content_sha256"], provenance["analyzer_id"], ANALYZER_VERSION)
    if result.get("analysis_id") != expected_analysis_id:
        raise M1ValidationError("analysis_id is not bound to source and analyzer version")
    reqs, risks, points, cases = (result.get(key) for key in ("requirements", "risks", "testpoints", "cases"))
    if not all(isinstance(value, list) for value in (reqs, risks, points, cases)) or not reqs or not cases:
        raise M1ValidationError("requirements, risks, testpoints and cases must be nonempty lists")
    req_ids = _all_ids(reqs, "requirement_id", "requirements")
    risk_ids = _all_ids(risks, "risk_id", "risks")
    point_ids = _all_ids(points, "testpoint_id", "testpoints")
    case_ids = _all_ids(cases, "case_id", "cases")
    if len(req_ids | risk_ids | point_ids | case_ids) != len(req_ids) + len(risk_ids) + len(point_ids) + len(case_ids):
        raise M1ValidationError("IDs must be unique across M1 collections")
    section_map = {section["section_id"]: section for section in source["sections"]}
    for collection, items in (("requirements", reqs), ("risks", risks), ("testpoints", points), ("cases", cases)):
        for item in items:
            anchors = item.get("source_anchors", item.get("anchors"))
            if not isinstance(anchors, list) or not anchors:
                raise M1ValidationError(f"{collection} requires source_anchors")
            for anchor in anchors:
                if not isinstance(anchor, dict) or anchor.get("source_id") != source["source_id"] or anchor.get("source_version") != source["source_version"]:
                    raise M1ValidationError(f"{collection} contains dangling source anchor")
                section = section_map.get(anchor.get("section_id"))
                if not section or anchor.get("locator") != section["locator"] or anchor.get("section_sha256") != section["section_sha256"]:
                    raise M1ValidationError(f"{collection} contains stale source anchor")
            if "source_anchors" in item and "anchors" in item and item["source_anchors"] != item["anchors"]:
                raise M1ValidationError(f"{collection} anchor aliases differ")
    anchor_ids = lambda item: {anchor["section_id"] for anchor in item.get("source_anchors", item.get("anchors", []))}
    for item in reqs:
        if not _text(item.get("title"), "requirement.title") or not _text(item.get("rule"), "requirement.rule") or type(item.get("version")) is not int or item["version"] < 1:
            raise M1ValidationError("requirement fields are invalid")
    for item in risks:
        if not _text(item.get("description"), "risk.description") or item.get("requirement_id") not in req_ids or item.get("severity") not in {"low", "medium", "high", "unknown"}:
            raise M1ValidationError("risk mapping is invalid")
        requirement = next(req for req in reqs if req["requirement_id"] == item["requirement_id"])
        if anchor_ids(item) != anchor_ids(requirement):
            raise M1ValidationError("risk source mapping is inconsistent")
    for item in points:
        if not _text(item.get("title"), "testpoint.title") or item.get("requirement_id") not in req_ids or item.get("risk_id") not in risk_ids or item.get("category") not in _CATEGORIES:
            raise M1ValidationError("testpoint mapping is invalid")
        requirement = next(req for req in reqs if req["requirement_id"] == item["requirement_id"])
        risk = next(risk for risk in risks if risk["risk_id"] == item["risk_id"])
        if item.get("requirement_id") != risk.get("requirement_id") or anchor_ids(item) != anchor_ids(requirement) or anchor_ids(item) != anchor_ids(risk):
            raise M1ValidationError("testpoint source mapping is inconsistent")
    for case in cases:
        if any(case.get(field) not in ids for field, ids in (("requirement_id", req_ids), ("risk_id", risk_ids), ("testpoint_id", point_ids))):
            raise M1ValidationError("case mapping is invalid")
        requirement = next(req for req in reqs if req["requirement_id"] == case["requirement_id"])
        risk = next(risk for risk in risks if risk["risk_id"] == case["risk_id"])
        point = next(point for point in points if point["testpoint_id"] == case["testpoint_id"])
        if (risk.get("requirement_id") != case["requirement_id"] or point.get("requirement_id") != case["requirement_id"]
                or point.get("risk_id") != case["risk_id"] or anchor_ids(case) != anchor_ids(requirement)
                or anchor_ids(case) != anchor_ids(risk) or anchor_ids(case) != anchor_ids(point)):
            raise M1ValidationError("case source mapping is inconsistent")
        if not _text(case.get("title"), "case.title") or type(case.get("version")) is not int or case["version"] < 1 or not isinstance(case.get("preconditions"), list) or not isinstance(case.get("steps"), list):
            raise M1ValidationError("case version, preconditions and steps are invalid")
        if any(not isinstance(value, str) or not value.strip() for value in case["preconditions"] + case["steps"]):
            raise M1ValidationError("case preconditions and steps must contain nonempty strings")
        expectation = case.get("expectation")
        if not isinstance(expectation, dict) or not isinstance(expectation.get("assertions"), list) or not isinstance(expectation.get("unknowns"), list):
            raise M1ValidationError("case expectation is invalid")
        assertion_ids = set()
        for assertion in expectation["assertions"]:
            if not isinstance(assertion, dict) or not _text(assertion.get("assertion_id"), "assertion_id") or not _text(assertion.get("description"), "assertion.description") or not _text(assertion.get("expected"), "assertion.expected") or not isinstance(assertion.get("evidence_requirements"), list) or any(not isinstance(value, str) or not value.strip() for value in assertion["evidence_requirements"]):
                raise M1ValidationError("case assertion is invalid")
            if assertion["assertion_id"] in assertion_ids:
                raise M1ValidationError("duplicate assertion_id")
            assertion_ids.add(assertion["assertion_id"])
        if any(not isinstance(value, str) or not value.strip() for value in expectation["unknowns"]):
            raise M1ValidationError("case unknowns must contain nonempty strings")
        recovery = case.get("recovery")
        if not isinstance(recovery, dict) or not _text(recovery.get("method"), "recovery.method") or type(recovery.get("approved")) is not bool:
            raise M1ValidationError("case recovery is invalid")
        if case.get("review_status") not in _STATUSES or type(case.get("approved")) is not bool or type(case.get("needs_human_review")) is not bool:
            raise M1ValidationError("case review fields are invalid")
        _validate_confidence(case.get("confidence"), "case.confidence")
        if case["approved"] != (case["review_status"] == "APPROVED"):
            raise M1ValidationError("approved flag does not match review status")
        review = case.get("review")
        if case["review_status"] == "APPROVED":
            if (not isinstance(review, dict) or review.get("decision") != "approved" or review.get("reviewed_digest") != _review_digest(case)
                    or not _text(review.get("reviewer"), "case.review.reviewer") or not _text(review.get("created_at"), "case.review.created_at")
                    or not _text(review.get("comment"), "case.review.comment") or case["case_id"] not in review.get("affected_case_ids", [])):
                raise M1ValidationError("approved case lacks valid review digest")
    for collection in ("questions", "gaps"):
        entries = result.get(collection, [])
        if not isinstance(entries, list):
            raise M1ValidationError(f"{collection} must be a list")
        _all_ids(entries, "id", collection)
        for entry in entries:
            if not _text(entry.get("description"), f"{collection}.description"):
                raise M1ValidationError(f"{collection} description is required")
            anchors = entry.get("source_anchors")
            if not isinstance(anchors, list) or not anchors:
                raise M1ValidationError(f"{collection} requires source_anchors")
            for anchor in anchors:
                section = section_map.get(anchor.get("section_id")) if isinstance(anchor, dict) else None
                if (not section or anchor.get("source_id") != source["source_id"] or anchor.get("source_version") != source["source_version"]
                        or anchor.get("locator") != section["locator"] or anchor.get("section_sha256") != section["section_sha256"]):
                    raise M1ValidationError(f"{collection} contains stale source anchor")
    review = result.get("review")
    if not isinstance(review, dict) or review.get("status") not in {"PENDING", "REVIEWED"} or not isinstance(review.get("history", []), list):
        raise M1ValidationError("review record is invalid")
    if review.get("status") == "REVIEWED" and not review.get("history"):
        raise M1ValidationError("reviewed result requires review history")
    for entry in review.get("history", []):
        if (not all(_text(entry.get(key), "review history") for key in ("reviewer", "created_at", "comment", "decision"))
                or entry["decision"] not in _DECISIONS or not isinstance(entry.get("affected_case_ids"), list)
                or not entry["affected_case_ids"] or any(case_id not in case_ids for case_id in entry["affected_case_ids"])):
            raise M1ValidationError("review history entry is invalid")
        if not isinstance(entry.get("reviewed_digest"), str) or len(entry["reviewed_digest"]) != 64:
            raise M1ValidationError("review history digest is invalid")
    if source.get("permission") != "user-approved" and any(case["approved"] for case in cases):
        raise M1ValidationError("unverified source cannot contain approved cases")
    if any(case["approved"] for case in cases) and review.get("decision") != "approved":
        raise M1ValidationError("top-level review does not approve approved cases")
    return True


def import_result(result):
    candidate = deepcopy(result)
    validate_result(candidate)
    return candidate


def analyze_with_provider(source, provider):
    if provider is None:
        raise M1ValidationError("provider output is required")
    normalized = _source(source)
    output = provider if isinstance(provider, dict) else provider.analyze(deepcopy(normalized)) if hasattr(provider, "analyze") else provider.generate(deepcopy(normalized)) if hasattr(provider, "generate") else provider(deepcopy(normalized)) if callable(provider) else None
    if output is None:
        raise M1ValidationError("provider must supply a session result")
    result = deepcopy(output)
    result.setdefault("provenance", {})["mode"] = "session_ai"
    return import_result(result)


def review_result(result, review):
    candidate = deepcopy(result)
    if candidate.get("provenance", {}).get("mode") == "rule_based" and all(case.get("review_status") != "APPROVED" for case in candidate.get("cases", [])):
        candidate["provenance"]["output_digest"] = _result_digest(candidate)
    validate_result(candidate)
    if not isinstance(review, dict):
        raise M1ValidationError("review must be an object")
    reviewer, comment, decision = review.get("reviewer"), review.get("comment"), review.get("decision")
    if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(comment, str) or not comment.strip() or decision not in _DECISIONS:
        raise M1ValidationError("reviewer, comment and decision are required")
    all_case_ids = {case["case_id"] for case in candidate["cases"]}
    selected = review.get("case_ids", [case["case_id"] for case in candidate["cases"]])
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or any(case_id not in all_case_ids for case_id in selected):
        raise M1ValidationError("review case_ids are invalid")
    reviewed_at = _now()
    for case in candidate["cases"]:
        if case["case_id"] not in selected:
            continue
        if case["review_status"] == "REJECTED" and decision == "approved":
            raise M1ValidationError("rejected case is terminal; create a new version")
        if decision == "approved":
            if candidate["source"].get("permission") != "user-approved":
                raise M1ValidationError("unverified source cannot be approved")
            if not case["preconditions"] or not case["steps"] or not case["expectation"]["assertions"] or case["expectation"]["unknowns"]:
                raise M1ValidationError("case has incomplete independent approval data")
        status = {"approved": "APPROVED", "changes_requested": "CHANGES_REQUESTED", "rejected": "REJECTED"}[decision]
        case["review_status"] = status
        case["approved"] = decision == "approved"
        case["needs_human_review"] = decision != "approved"
        case["review"] = {"reviewer": reviewer, "created_at": reviewed_at, "comment": comment,
                           "decision": decision, "affected_case_ids": [case["case_id"]],
                           "reviewed_digest": _review_digest(case)}
    history = list(candidate["review"].get("history", []))
    history.append({"reviewer": reviewer, "created_at": reviewed_at, "comment": comment,
                    "decision": decision, "affected_case_ids": selected,
                    "reviewed_digest": _selected_digest(candidate["cases"], set(selected))})
    candidate["review"] = {"status": "REVIEWED", "reviewer": reviewer, "decision": decision,
                            "comment": comment, "history": history}
    candidate["provenance"]["output_digest"] = _result_digest(candidate)
    validate_result(candidate)
    return candidate


def _blocked(result, unresolved):
    return {"schema_version": 1, "status": "BLOCKED", "target_schema": "M0",
            "analysis_id": result["analysis_id"], "source_id": result["source"]["source_id"],
            "source_version": result["source"]["source_version"], "case_drafts": [dict(deepcopy(case), approved=False) for case in result["cases"]],
            "risk_drafts": deepcopy(result["risks"]), "testpoint_drafts": deepcopy(result["testpoints"]),
            "unresolved_bindings": sorted(set(unresolved)), "provenance": deepcopy(result["provenance"])}


def promote_to_m0(result, bindings=None, registry=None):
    validate_result(result)
    bindings = {} if bindings is None else deepcopy(bindings)
    required = ("build_id", "environment", "device_alias", "account_alias", "initial_state", "max_wait_seconds")
    unresolved = [field for field in required if field not in bindings or bindings[field] in (None, "", {}, [])]
    for field in ("build_id", "environment", "device_alias", "account_alias"):
        if field in bindings and (not isinstance(bindings[field], str) or not bindings[field].strip()):
            unresolved.append(field)
    if "initial_state" in bindings and (not isinstance(bindings["initial_state"], dict) or not bindings["initial_state"]):
        unresolved.append("initial_state")
    wait = bindings.get("max_wait_seconds")
    if wait is not None and (type(wait) not in (int, float) or isinstance(wait, bool) or not math.isfinite(wait) or wait <= 0):
        unresolved.append("max_wait_seconds")
    if result["source"].get("permission") != "user-approved":
        unresolved.append("source_not_authorized")
    if any(case["review_status"] != "APPROVED" for case in result["cases"]):
        unresolved.append("human_review")
    if registry is None:
        unresolved.append("registry")
    if unresolved:
        return _blocked(result, unresolved)
    try:
        reg = deepcopy(registry)
        source_key = f"{result['source']['source_id']}--{result['source']['content_sha256'][:12]}"
        source_ref = f"{result['source']['source_ref']}@{result['source']['source_version']}/{result['analysis_id']}"
        excerpt = "\n".join(section["text"] for section in result["source"]["sections"])
        if source_key not in reg.data["sources"]:
            reg.register_source(source_key, {"source_ref": source_ref, "source_version": result["source"]["source_version"], "permission": result["source"]["permission"], "excerpt": excerpt, "content_sha256": _digest(excerpt)})
        req_map = {}
        for req in result["requirements"]:
            req_key = f"{req['requirement_id']}--{result['source']['content_sha256'][:8]}"
            req_map[req["requirement_id"]] = req_key
            if req_key not in reg.data["requirements"]:
                reg.register_requirement(req_key, {"source_id": source_key, "version": 1, "title": req["title"], "status": "draft", "acceptance_ref": req["rule"]})
        for risk in result["risks"]:
            if risk["risk_id"] not in reg.data["risks"]:
                reg.register_risk(risk["risk_id"], {"requirement_id": req_map[risk["requirement_id"]], "severity": risk["severity"], "description": risk["description"]})
        for point in result["testpoints"]:
            if point["testpoint_id"] not in reg.data["testpoints"]:
                reg.register_testpoint(point["testpoint_id"], {"requirement_id": req_map[point["requirement_id"]], "risk_id": point["risk_id"], "title": point["title"]})
        for case in result["cases"]:
            if case["case_id"] in reg.data["cases"]:
                continue
            reviewer = case["review"]["reviewer"]
            reviewed_at = case["review"]["created_at"]
            reg.register_case(case["case_id"], {"scenario_id": _stable("scenario", case["case_id"]), "risk_id": case["risk_id"], "testpoint_id": case["testpoint_id"], "requirement_id": req_map[case["requirement_id"]], "source_id": source_key, "source_ref": source_ref, "build_id": bindings["build_id"], "version": case["version"], "approved": False, "environment": bindings["environment"], "device_alias": bindings["device_alias"], "account_alias": bindings["account_alias"], "initial_state": bindings["initial_state"], "steps": case["steps"], "expectation": case["expectation"], "recovery": case["recovery"], "reviewer": reviewer, "reviewed_at": reviewed_at, "max_wait_seconds": bindings["max_wait_seconds"]})
            reg.data["cases"][case["case_id"]].setdefault("extensions", {})["m1"] = {"analysis_id": result["analysis_id"], "source_anchors": case["source_anchors"], "review": case["review"]}
        reg.data.setdefault("extensions", {})["m1"] = {"analysis_id": result["analysis_id"], "provenance": deepcopy(result["provenance"]), "source_content_sha256": result["source"]["content_sha256"]}
        reg.validate()
    except Exception as error:
        return _blocked(result, ["registry_validation:" + str(error)])
    envelope = _blocked(result, [])
    envelope.update({"status": "READY_FOR_M0_REVIEW", "registry": reg.data, "unresolved_bindings": []})
    return envelope


def summarize_result(result):
    validate_result(result)
    counts = {"requirements": len(result["requirements"]), "risks": len(result["risks"]),
              "testpoints": len(result["testpoints"]), "cases": len(result["cases"]),
              "approved_cases": sum(case["approved"] for case in result["cases"])}
    return {"analysis_id": result["analysis_id"], "source_id": result["source"]["source_id"],
            **counts, "counts": counts, "review_status": result["review"]["status"]}
