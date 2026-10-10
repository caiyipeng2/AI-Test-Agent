"""M0 data foundation: versioned manifests, traceability and coverage only.

This module deliberately does not execute devices, install packages, call models or
change game state. It stores the reviewed inputs needed by later stages.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_STATUSES = {"PLANNED", "BLOCKED", "PASS", "FAIL"}


class M0ValidationError(ValueError):
    """Raised when a foundation record cannot be traced or safely reused."""


def _nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise M0ValidationError(f"{name} must be a nonempty string")
    return value


def _sha256(value, name):
    value = _nonempty(value, name)
    if not _SHA256.fullmatch(value):
        raise M0ValidationError(f"{name} must be a SHA-256 hex digest")
    return value.lower()


def _mapping(value, name):
    if not isinstance(value, dict):
        raise M0ValidationError(f"{name} must be an object")
    return value


def _strict_object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise M0ValidationError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _json_constant(value):
    raise M0ValidationError(f"non-finite JSON value: {value}")


def _now():
    return datetime.now(timezone.utc).isoformat()


class M0Registry:
    """Append-oriented local registry for one project foundation snapshot."""

    def __init__(self, data):
        self.data = deepcopy(data)

    @classmethod
    def create(cls, project):
        project = _mapping(project, "project")
        data = {
            "schema_version": SCHEMA_VERSION,
            "created_at": _now(),
            "updated_at": _now(),
            "project": {
                "project_id": _nonempty(project.get("project_id"), "project.project_id"),
                "name": _nonempty(project.get("name"), "project.name"),
            },
            "sources": {}, "builds": {}, "requirements": {}, "risks": {}, "testpoints": {}, "cases": {},
            "runs": {}, "evidence": {}, "defects": {},
        }
        return cls(data)

    @classmethod
    def load(cls, path):
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"),
                              object_pairs_hook=_strict_object_pairs,
                              parse_constant=_json_constant)
        except (OSError, json.JSONDecodeError, M0ValidationError) as error:
            raise M0ValidationError(f"cannot load registry: {error}") from error
        if not isinstance(data, dict):
            raise M0ValidationError("registry root must be an object")
        registry = cls(data)
        registry.validate()
        return registry

    def save(self, path):
        self.validate()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = _now()
        payload = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def register_source(self, source_id, source):
        source_id = _nonempty(source_id, "source_id")
        source = _mapping(source, "source")
        record = {
            "source_id": source_id,
            "source_ref": _nonempty(source.get("source_ref"), "source.source_ref"),
            "source_version": _nonempty(source.get("source_version"), "source.source_version"),
            "permission": _nonempty(source.get("permission"), "source.permission"),
            "excerpt": _nonempty(source.get("excerpt"), "source.excerpt"),
            "content_sha256": source.get("content_sha256"),
            "location": source.get("location", "unknown"),
            "reviewer": source.get("reviewer", "unassigned"),
            "model_access": source.get("model_access", False),
            "public_summary": source.get("public_summary", True),
        }
        self._put("sources", source_id, record)
        return deepcopy(record)

    def register_build(self, build_id, build):
        build_id = _nonempty(build_id, "build_id")
        build = _mapping(build, "build")
        runtime = _mapping(build.get("runtime"), "build.runtime")
        record = {
            "build_id": build_id,
            "artifact_type": _nonempty(build.get("artifact_type"), "build.artifact_type"),
            "artifact_path": _nonempty(build.get("artifact_path"), "build.artifact_path"),
            "artifact_sha256": _sha256(build.get("artifact_sha256"), "build.artifact_sha256"),
            "signature_sha256": build.get("signature_sha256", "unknown"),
            "source_commit": build.get("source_commit", "unknown"),
            "artifact_relation": build.get("artifact_relation", "unknown"),
            "package_name": _nonempty(build.get("package_name"), "build.package_name"),
            "version_code": build.get("version_code"),
            "version_name": _nonempty(build.get("version_name"), "build.version_name"),
            "environment": _nonempty(build.get("environment"), "build.environment"),
            "environment_source": _nonempty(build.get("environment_source"), "build.environment_source"),
            "runtime": {
                "server": _nonempty(runtime.get("server"), "build.runtime.server"),
                "resource_version": _nonempty(runtime.get("resource_version"), "build.runtime.resource_version"),
                "dll_version": _nonempty(runtime.get("dll_version"), "build.runtime.dll_version"),
                "verified": runtime.get("verified") is True,
            },
        }
        if type(record["version_code"]) is not int or record["version_code"] < 0:
            raise M0ValidationError("build.version_code must be a nonnegative integer")
        if type(record["runtime"]["verified"]) is not bool:
            raise M0ValidationError("build.runtime.verified must be an explicit boolean")
        self._put("builds", build_id, record)
        return deepcopy(record)

    def register_requirement(self, requirement_id, requirement):
        requirement_id = _nonempty(requirement_id, "requirement_id")
        requirement = _mapping(requirement, "requirement")
        source_id = _nonempty(requirement.get("source_id"), "requirement.source_id")
        self._exists("sources", source_id)
        record = {
            "requirement_id": requirement_id,
            "source_id": source_id,
            "version": requirement.get("version", 1),
            "title": _nonempty(requirement.get("title"), "requirement.title"),
            "status": _nonempty(requirement.get("status"), "requirement.status"),
            "acceptance_ref": _nonempty(requirement.get("acceptance_ref"), "requirement.acceptance_ref"),
        }
        if type(record["version"]) is not int or record["version"] < 1:
            raise M0ValidationError("requirement.version must be a positive integer")
        if record["status"] not in {"draft", "approved", "blocked"}:
            raise M0ValidationError("requirement.status must be draft, approved or blocked")
        self._put("requirements", requirement_id, record)
        return deepcopy(record)

    def register_case(self, case_id, case):
        case_id = _nonempty(case_id, "case_id")
        case = _mapping(case, "case")
        requirement_id = _nonempty(case.get("requirement_id"), "case.requirement_id")
        source_id = _nonempty(case.get("source_id"), "case.source_id")
        build_id = _nonempty(case.get("build_id"), "case.build_id")
        self._exists("requirements", requirement_id)
        self._exists("sources", source_id)
        self._exists("builds", build_id)
        if type(case.get("approved")) is not bool:
            raise M0ValidationError("case.approved must be an explicit boolean")
        if type(case.get("version")) is not int or case["version"] < 1:
            raise M0ValidationError("case.version must be a positive integer")
        if not isinstance(case.get("steps"), list) or not case["steps"]:
            raise M0ValidationError("case.steps must be a nonempty list")
        initial_state = _mapping(case.get("initial_state"), "case.initial_state")
        recovery = _mapping(case.get("recovery"), "case.recovery")
        record = {
            "case_id": case_id,
            "scenario_id": _nonempty(case.get("scenario_id"), "case.scenario_id"),
            "risk_id": _nonempty(case.get("risk_id"), "case.risk_id"),
            "testpoint_id": _nonempty(case.get("testpoint_id"), "case.testpoint_id"),
            "requirement_id": requirement_id,
            "source_id": source_id,
            "source_ref": _nonempty(case.get("source_ref"), "case.source_ref"),
            "version": case["version"],
            "approved": case["approved"],
            "build_id": build_id,
            "environment": _nonempty(case.get("environment"), "case.environment"),
            "device_alias": _nonempty(case.get("device_alias"), "case.device_alias"),
            "account_alias": _nonempty(case.get("account_alias"), "case.account_alias"),
            "initial_state": deepcopy(initial_state),
            "steps": list(case["steps"]),
            "expectation": deepcopy(_mapping(case.get("expectation"), "case.expectation")),
            "recovery": deepcopy(recovery),
            "reviewer": _nonempty(case.get("reviewer"), "case.reviewer"),
            "reviewed_at": _nonempty(case.get("reviewed_at"), "case.reviewed_at"),
            "max_wait_seconds": case.get("max_wait_seconds"),
        }
        if type(record["max_wait_seconds"]) not in (int, float) or record["max_wait_seconds"] <= 0:
            raise M0ValidationError("case.max_wait_seconds must be positive")
        self._put("cases", case_id, record)
        return deepcopy(record)

    def start_run(self, run_id, run):
        run_id = _nonempty(run_id, "run_id")
        run = _mapping(run, "run")
        case_id = _nonempty(run.get("case_id"), "run.case_id")
        case = self._get("cases", case_id)
        for field in ("attempt_id", "build_id", "environment", "device_alias", "account_alias"):
            _nonempty(run.get(field), f"run.{field}")
        if run["build_id"] != case["build_id"] or run["environment"] != case["environment"]:
            raise M0ValidationError("run does not match the case build/environment binding")
        if run["device_alias"] != case["device_alias"] or run["account_alias"] != case["account_alias"]:
            raise M0ValidationError("run does not match the case device/account aliases")
        if not isinstance(run.get("initial_state"), dict) or not run["initial_state"]:
            raise M0ValidationError("run.initial_state must be a nonempty snapshot")
        status = run.get("status", "PLANNED")
        if status not in _STATUSES:
            raise M0ValidationError(f"run.status must be one of {sorted(_STATUSES)}")
        record = {"run_id": run_id, "created_at": run.get("created_at", _now()),
                  "attempt_id": run["attempt_id"], "case_id": case_id,
                  "build_id": run["build_id"], "environment": run["environment"],
                  "device_alias": run["device_alias"], "account_alias": run["account_alias"],
                  "attempt_index": run.get("attempt_index", 1),
                  "retry_reason": run.get("retry_reason"),
                  "parent_run_id": run.get("parent_run_id"),
                  "tool_version": run.get("tool_version", "m0-registry"),
                  "model_version": run.get("model_version", "none"),
                  "initial_state": deepcopy(run["initial_state"]), "status": status,
                  "blocked_reasons": list(run.get("blocked_reasons", [])),
                  "evidence_ids": [], "defect_ids": []}
        self._put("runs", run_id, record)
        return deepcopy(record)

    def register_risk(self, risk_id, risk):
        risk_id = _nonempty(risk_id, "risk_id")
        risk = _mapping(risk, "risk")
        requirement_id = _nonempty(risk.get("requirement_id"), "risk.requirement_id")
        self._exists("requirements", requirement_id)
        record = {"risk_id": risk_id, "requirement_id": requirement_id,
                  "severity": _nonempty(risk.get("severity"), "risk.severity"),
                  "description": _nonempty(risk.get("description"), "risk.description")}
        self.data.setdefault("risks", {})
        self._put("risks", risk_id, record)
        return deepcopy(record)

    def register_testpoint(self, testpoint_id, testpoint):
        testpoint_id = _nonempty(testpoint_id, "testpoint_id")
        testpoint = _mapping(testpoint, "testpoint")
        requirement_id = _nonempty(testpoint.get("requirement_id"), "testpoint.requirement_id")
        self._exists("requirements", requirement_id)
        risk_id = _nonempty(testpoint.get("risk_id"), "testpoint.risk_id")
        if risk_id in self.data.get("risks", {}):
            self._exists("risks", risk_id)
        record = {"testpoint_id": testpoint_id, "requirement_id": requirement_id,
                  "risk_id": risk_id, "title": _nonempty(testpoint.get("title"), "testpoint.title")}
        self.data.setdefault("testpoints", {})
        self._put("testpoints", testpoint_id, record)
        return deepcopy(record)

    def register_evidence(self, run_id, evidence, *, allow_absolute=False):
        self._exists("runs", run_id)
        evidence = _mapping(evidence, "evidence")
        evidence_id = _nonempty(evidence.get("evidence_id"), "evidence.evidence_id")
        status = evidence.get("status")
        if status not in {"PLANNED", "COLLECTED"}:
            raise M0ValidationError("evidence.status must be PLANNED or COLLECTED")
        record = {"evidence_id": evidence_id,
                  "run_id": run_id,
                  "attempt_id": _nonempty(evidence.get("attempt_id"), "evidence.attempt_id"),
                  "kind": _nonempty(evidence.get("kind"), "evidence.kind"),
                  "path": _nonempty(evidence.get("path"), "evidence.path"),
                  "status": status,
                  "sha256": evidence.get("sha256"),
                  "retention": _nonempty(evidence.get("retention"), "evidence.retention"),
                  "sensitivity": evidence.get("sensitivity", "internal"),
                  "owner": evidence.get("owner", "test-team"),
                  "relative_path": evidence.get("relative_path", evidence.get("path")),
                  "expires_at": evidence.get("expires_at"),
                  "collected_at": evidence.get("collected_at")}
        if status == "PLANNED" and (record["sha256"] is not None or record["collected_at"] is not None):
            raise M0ValidationError("planned evidence cannot claim a hash or collection time")
        path_text = record["path"]
        if "://" in path_text and not path_text.startswith("local-only://"):
            raise M0ValidationError("evidence.path URI must use local-only://")
        relative = record["relative_path"]
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise M0ValidationError("evidence.relative_path must be safe and relative")
        if "://" not in path_text:
            path_obj = Path(path_text)
            if (not allow_absolute and path_obj.is_absolute()) or ".." in path_obj.parts:
                raise M0ValidationError("evidence.path must be relative or an explicit local-only URI")
        if status == "COLLECTED":
            _sha256(record["sha256"], "evidence.sha256")
            _nonempty(record["collected_at"], "evidence.collected_at")
        self._put("evidence", evidence_id, record)
        run = self._get("runs", run_id)
        if evidence_id not in run["evidence_ids"]:
            run["evidence_ids"].append(evidence_id)
        return deepcopy(record)

    def attach_evidence(self, run_id, evidence):
        evidence = deepcopy(evidence)
        if evidence.get("status") != "COLLECTED":
            raise M0ValidationError("attach_evidence requires status=COLLECTED")
        path = Path(_nonempty(evidence.get("path"), "evidence.path"))
        if not path.is_file():
            raise M0ValidationError("collected evidence path does not exist")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual.lower() != str(evidence.get("sha256", "")).lower():
            raise M0ValidationError("collected evidence SHA-256 does not match the file")
        evidence["relative_path"] = evidence.get("relative_path") or path.name
        return self.register_evidence(run_id, evidence, allow_absolute=True)

    def link_defect(self, run_id, defect):
        self._exists("runs", run_id)
        defect = _mapping(defect, "defect")
        defect_id = _nonempty(defect.get("defect_id"), "defect.defect_id")
        record = {"defect_id": defect_id, "run_id": run_id,
                  "category": _nonempty(defect.get("category"), "defect.category"),
                  "status": _nonempty(defect.get("status"), "defect.status"),
                  "title": _nonempty(defect.get("title"), "defect.title"),
                  "confirmed": type(defect.get("confirmed")) is bool and defect["confirmed"]}
        if record["confirmed"] and record["category"] in {"INSUFFICIENT_EVIDENCE", "TOOL_FAILURE", "ENVIRONMENT_FAILURE"}:
            raise M0ValidationError("evidence or infrastructure blockers cannot be confirmed as game defects")
        self._put("defects", defect_id, record)
        run = self._get("runs", run_id)
        if defect_id not in run["defect_ids"]:
            run["defect_ids"].append(defect_id)
        return deepcopy(record)

    def trace_report(self, run_id):
        self.validate()
        run = self._get("runs", run_id)
        case = self._get("cases", run["case_id"])
        requirement = self._get("requirements", case["requirement_id"])
        source = self._get("sources", requirement["source_id"])
        build = self._get("builds", run["build_id"])
        point_id = case.get("testpoint_id")
        trace = [source["source_id"], requirement["requirement_id"]]
        if point_id and point_id in self.data.get("testpoints", {}):
            trace.append(point_id)
        trace.extend([case["case_id"], run["build_id"], run["run_id"], run["attempt_id"]])
        return {
            "schema_version": SCHEMA_VERSION,
            "trace": trace,
            "source": deepcopy(source), "requirement": deepcopy(requirement),
            "case": deepcopy(case), "build": deepcopy(build), "run": deepcopy(run),
            "evidence": [deepcopy(self._get("evidence", key)) for key in run["evidence_ids"]],
            "defects": [deepcopy(self._get("defects", key)) for key in run["defect_ids"]],
        }

    def coverage_report(self):
        self.validate()
        statuses = {status: 0 for status in sorted(_STATUSES)}
        cases_with_runs = {}
        evidence_complete = set()
        for run in self.data["runs"].values():
            statuses[run["status"]] += 1
            cases_with_runs.setdefault(run["case_id"], []).append(run)
            if run["status"] in {"PASS", "FAIL"} and run["evidence_ids"] and all(
                self.data["evidence"][key]["status"] == "COLLECTED" for key in run["evidence_ids"]):
                evidence_complete.add(run["case_id"])
        executed = {case_id for case_id, runs in cases_with_runs.items() if any(r["status"] in {"PASS", "FAIL"} for r in runs)}
        blocked = {case_id for case_id, runs in cases_with_runs.items() if any(r["status"] == "BLOCKED" for r in runs)}
        return {
            "schema_version": SCHEMA_VERSION,
            "planned_cases": len(self.data["cases"]),
            "executed_cases": len(executed), "blocked_cases": len(blocked),
            "evidence_complete_cases": len(evidence_complete),
            "unexecuted_cases": sorted(set(self.data["cases"]) - set(cases_with_runs)),
            "runs_by_status": statuses,
        }

    def report(self, run_id=None):
        """Create a deterministic, device-free M0 report from registered records."""
        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now(),
            "project": deepcopy(self.data["project"]),
            "coverage": self.coverage_report(),
            "traceability": [],
            "open_blockers": [],
            "foundation_status": "PASS",
            "execution_readiness": {},
        }
        run_ids = [run_id] if run_id is not None else sorted(self.data["runs"])
        for current in run_ids:
            trace = self.trace_report(current)
            report["traceability"].append(trace)
            if trace["run"]["status"] == "BLOCKED":
                report["open_blockers"].extend(trace["run"].get("blocked_reasons", []))
            blockers = []
            build = trace["build"]
            if build["runtime"].get("dll_version") in (None, "", "unknown"):
                blockers.append("runtime DLL identity is unknown")
            case = trace["case"]
            expectation = case.get("expectation", {})
            if case.get("approved") and any(expectation.get(key) in (None, "") for key in ("cost", "non_upgrade_delta")):
                blockers.append("approved case has unknown monetary expectation")
            report["execution_readiness"][case["case_id"]] = {
                "status": "PASS" if not blockers else "BLOCKED", "blockers": blockers}
            if blockers:
                report["foundation_status"] = "PASS_WITH_EXECUTION_BLOCKERS"
        return report

    def validate(self, *, evidence_root=None):
        if type(self.data.get("schema_version")) is not int or self.data.get("schema_version") != SCHEMA_VERSION:
            raise M0ValidationError("unsupported registry schema version")
        project = _mapping(self.data.get("project"), "registry.project")
        _nonempty(project.get("project_id"), "project.project_id")
        _nonempty(project.get("name"), "project.name")
        collections = ("sources", "builds", "requirements", "risks", "testpoints", "cases", "runs", "evidence", "defects")
        for collection in collections:
            if not isinstance(self.data.get(collection), dict):
                raise M0ValidationError(f"registry.{collection} must be an object")
            for key, record in self.data[collection].items():
                if not isinstance(record, dict):
                    raise M0ValidationError(f"{collection}.{key} must be an object")
                id_field = {"sources":"source_id", "builds":"build_id", "requirements":"requirement_id",
                            "risks":"risk_id", "testpoints":"testpoint_id", "cases":"case_id", "runs":"run_id",
                            "evidence":"evidence_id", "defects":"defect_id"}[collection]
                if record.get(id_field) != key:
                    raise M0ValidationError(f"{collection} key does not match record ID: {key}")
        for source in self.data["sources"].values():
            for field in ("source_ref", "source_version", "permission", "excerpt"):
                _nonempty(source.get(field), "source." + field)
            if source.get("content_sha256") not in (None, "unknown"):
                _sha256(source["content_sha256"], "source.content_sha256")
        for build in self.data["builds"].values():
            _sha256(build.get("artifact_sha256"), "build.artifact_sha256")
            if type(build.get("version_code")) is not int or build["version_code"] < 0:
                raise M0ValidationError("build.version_code must be a nonnegative integer")
            runtime = _mapping(build.get("runtime"), "build.runtime")
            for field in ("server", "resource_version", "dll_version"):
                if runtime.get(field) in (None, ""):
                    raise M0ValidationError(f"build.runtime.{field} must be explicit unknown or known")
            if type(runtime.get("verified")) is not bool:
                raise M0ValidationError("build.runtime.verified must be boolean")
        for requirement in self.data["requirements"].values():
            self._exists("sources", requirement.get("source_id"))
            if type(requirement.get("version")) is not int or requirement["version"] < 1:
                raise M0ValidationError("requirement.version must be a positive integer")
            if requirement.get("status") not in {"draft", "approved", "blocked"}:
                raise M0ValidationError("invalid requirement.status")
        for risk in self.data["risks"].values():
            self._exists("requirements", risk.get("requirement_id"))
        for point in self.data["testpoints"].values():
            self._exists("requirements", point.get("requirement_id"))
            self._exists("risks", point.get("risk_id"))
        for case in self.data["cases"].values():
            requirement = self._get("requirements", case.get("requirement_id"))
            source = self._get("sources", case.get("source_id"))
            build = self._get("builds", case.get("build_id"))
            if case.get("source_id") != requirement.get("source_id") or case.get("source_ref") != source.get("source_ref"):
                raise M0ValidationError("case source lineage does not match requirement/source")
            if case.get("environment") != build.get("environment"):
                raise M0ValidationError("case environment does not match build")
            self._exists("testpoints", case.get("testpoint_id"))
            self._exists("risks", case.get("risk_id"))
            if type(case.get("approved")) is not bool or type(case.get("version")) is not int:
                raise M0ValidationError("case approval/version fields are invalid")
            if not isinstance(case.get("steps"), list) or not case["steps"] or not isinstance(case.get("initial_state"), dict) or not case["initial_state"]:
                raise M0ValidationError("case steps and initial state are required")
            if not isinstance(case.get("expectation"), dict) or not isinstance(case.get("recovery"), dict):
                raise M0ValidationError("case expectation and recovery are required")
        for run in self.data["runs"].values():
            case = self._get("cases", run.get("case_id"))
            if run.get("build_id") != case.get("build_id") or run.get("environment") != case.get("environment"):
                raise M0ValidationError("run build/environment does not match case")
            if run.get("device_alias") != case.get("device_alias") or run.get("account_alias") != case.get("account_alias"):
                raise M0ValidationError("run device/account does not match case")
            if run.get("initial_state") != case.get("initial_state"):
                raise M0ValidationError("run initial state does not match case")
            if run.get("status") not in _STATUSES or type(run.get("attempt_index", 1)) is not int:
                raise M0ValidationError("run status/attempt index invalid")
            if not isinstance(run.get("evidence_ids"), list) or not isinstance(run.get("defect_ids"), list):
                raise M0ValidationError("run evidence_ids/defect_ids must be lists")
            if run["status"] == "BLOCKED" and not run.get("blocked_reasons"):
                raise M0ValidationError("blocked run requires blocked_reasons")
            if run["status"] in {"PASS", "FAIL"} and not run.get("evidence_ids"):
                raise M0ValidationError("executed run requires evidence")
            for evidence_id in run["evidence_ids"]:
                evidence = self._get("evidence", evidence_id)
                if evidence.get("run_id") != run["run_id"] or evidence.get("attempt_id") != run.get("attempt_id"):
                    raise M0ValidationError("evidence backlink does not match run/attempt")
            for defect_id in run["defect_ids"]:
                if self._get("defects", defect_id).get("run_id") != run["run_id"]:
                    raise M0ValidationError("defect backlink does not match run")
        for evidence in self.data["evidence"].values():
            run = self._get("runs", evidence.get("run_id"))
            if evidence.get("attempt_id") != run.get("attempt_id"):
                raise M0ValidationError("evidence attempt backlink does not match run")
            if evidence.get("status") not in {"PLANNED", "COLLECTED"}:
                raise M0ValidationError("invalid evidence status")
            path = evidence.get("path", "")
            if "://" not in path and (Path(path).is_absolute() or ".." in Path(path).parts):
                raise M0ValidationError("unsafe evidence path")
            if evidence["status"] == "PLANNED" and (evidence.get("sha256") is not None or evidence.get("collected_at") is not None):
                raise M0ValidationError("planned evidence cannot claim collection")
            if evidence["status"] == "COLLECTED":
                _sha256(evidence.get("sha256"), "evidence.sha256")
                _nonempty(evidence.get("collected_at"), "evidence.collected_at")
                if evidence_root is not None and "://" not in path:
                    actual = hashlib.sha256((Path(evidence_root) / evidence.get("relative_path", path)).read_bytes()).hexdigest()
                    if actual != evidence["sha256"]:
                        raise M0ValidationError("evidence file hash mismatch")
        for defect in self.data["defects"].values():
            if self._get("runs", defect.get("run_id")).get("run_id") != defect.get("run_id"):
                raise M0ValidationError("defect run backlink invalid")
            if type(defect.get("confirmed")) is not bool:
                raise M0ValidationError("defect.confirmed must be explicit")
            if defect["confirmed"] and defect.get("category") in {"INSUFFICIENT_EVIDENCE", "TOOL_FAILURE", "ENVIRONMENT_FAILURE"}:
                raise M0ValidationError("blocker cannot be confirmed as a game defect")
        return True

    def _put(self, collection, key, record):
        if key in self.data[collection]:
            raise M0ValidationError(f"duplicate {collection} id: {key}")
        self.data[collection][key] = deepcopy(record)

    def _get(self, collection, key):
        self._exists(collection, key)
        return self.data[collection][key]

    def _exists(self, collection, key):
        if not isinstance(key, str) or key not in self.data[collection]:
            raise M0ValidationError(f"missing {collection} reference: {key}")
