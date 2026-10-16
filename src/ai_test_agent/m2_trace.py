"""Verified local M2 startup evidence handoff to the M0 foundation registry."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .contracts import evidence_for_png
from .m0 import M0Registry
from .m2 import config_digest


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    return value


def _timestamp(value, name):
    try:
        parsed = datetime.fromisoformat(_text(value, name).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone is required')
        return parsed
    except (TypeError, ValueError) as error:
        raise ValueError(f'{name} must be an ISO timestamp with timezone') from error


def _same_digest(observed, approved):
    return isinstance(observed, str) and isinstance(approved, str) and observed.lower() == approved.lower()


def _verify_inputs(config, result):
    if not isinstance(config, dict) or not isinstance(result, dict):
        raise ValueError('config and result must be objects')
    if result.get('config_sha256') != config_digest(config):
        raise ValueError('M2 configuration fingerprint does not match the result')
    if type(result.get('schema_version')) is not int or result['schema_version'] != 1:
        raise ValueError('unsupported M2 result schema')
    if result.get('status') not in {'PASS', 'FAIL', 'BLOCKED'}:
        raise ValueError('invalid M2 result status')
    expected = config.get('expected', {})
    for field, value in (
        ('scope', 'startup_readiness'), ('business_status', 'NOT_EVALUATED'),
        ('environment', 'test'), ('case_id', config.get('case_id')),
        ('package', expected.get('package')), ('device_alias', config.get('device_alias')),
    ):
        if result.get(field) != value or value is None:
            raise ValueError(f'M2 result {field} binding does not match the contract')
    if config.get('environment') != 'test':
        raise ValueError('only the test environment may be handed off')
    for field in ('run_id', 'attempt_id', 'case_id'):
        _text(result.get(field), 'result.' + field)
    started = _timestamp(result.get('started_at'), 'result.started_at')
    finished = _timestamp(result.get('finished_at'), 'result.finished_at')
    if finished < started:
        raise ValueError('M2 finish time precedes its start time')
    root = Path(_text(result.get('run_dir'), 'result.run_dir'))
    if not root.is_absolute():
        raise ValueError('M2 run_dir must be absolute')
    root = root.resolve(strict=True)
    artifact_root = Path(_text(config.get('artifact_root'), 'config.artifact_root')).resolve()
    if root == artifact_root or not root.is_relative_to(artifact_root):
        raise ValueError('M2 run_dir is outside its configured artifact root')
    source_path = Path(_text(result.get('result_path'), 'result.result_path'))
    if not source_path.is_absolute() or source_path.resolve().parent != root:
        raise ValueError('M2 result_path must belong to its run directory')
    try:
        stored = json.loads(source_path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'cannot read the original M2 result: {error}') from error
    if stored != result:
        raise ValueError('original M2 result file does not match the supplied result')
    records = result.get('evidence')
    if not isinstance(records, list):
        raise ValueError('M2 evidence must be a list')
    verified = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('M2 evidence entries must be objects')
        kind = _text(record.get('kind'), 'evidence.kind')
        path = Path(_text(record.get('path'), 'evidence.path'))
        relative = Path(_text(record.get('relative_path'), 'evidence.relative_path'))
        if not path.is_absolute() or relative.is_absolute() or relative.drive or '..' in relative.parts:
            raise ValueError('M2 evidence paths must be absolute with safe relative bindings')
        try:
            actual_path = path.resolve(strict=True)
            if (not actual_path.is_relative_to(root) or actual_path == root
                    or (root / relative).resolve() != actual_path or actual_path in seen):
                raise ValueError('M2 evidence path binding is outside the run or duplicated')
            raw = actual_path.read_bytes()
        except OSError as error:
            raise ValueError(f'cannot read M2 evidence: {error}') from error
        digest = hashlib.sha256(raw).hexdigest()
        if (type(record.get('bytes')) is not int or record['bytes'] != len(raw)
                or not isinstance(record.get('sha256'), str) or record['sha256'].lower() != digest):
            raise ValueError('M2 evidence bytes or SHA-256 do not match the actual file')
        checked = deepcopy(record)
        checked.update(path=str(actual_path), relative_path=relative.as_posix(), sha256=digest)
        if kind.lower() in {'screenshot', 'png'} or path.suffix.lower() == '.png' or raw.startswith(b'\x89PNG'):
            png = evidence_for_png(actual_path)
            if png['sha256'] != digest or png['bytes'] != len(raw):
                raise ValueError('PNG evidence changed during verification')
            for dimension in ('width', 'height'):
                if dimension in record and record[dimension] != png[dimension]:
                    raise ValueError('PNG dimensions do not match the evidence metadata')
                checked[dimension] = png[dimension]
        verified.append(checked)
        seen.add(actual_path)
    return root, source_path.resolve(), verified


def _handoff_blockers(config, result, evidence):
    blockers = []
    readiness = config.get('readiness', {})
    if readiness.get('approved') is not True:
        blockers.append('readiness.approved: explicit human approval is missing')
    for field in ('reviewer', 'source_ref'):
        if not isinstance(readiness.get(field), str) or not readiness[field].strip():
            blockers.append('readiness.' + field + ': human review binding is missing')
    try:
        _timestamp(readiness.get('reviewed_at'), 'readiness.reviewed_at')
    except ValueError:
        blockers.append('readiness.reviewed_at: a valid human review timestamp is missing')
    if result['status'] == 'BLOCKED':
        blockers.append('M2 execution is BLOCKED: ' + str(result.get('category', 'unknown')))
        blockers.extend(str(reason) for reason in result.get('reasons', []))
    if not evidence:
        blockers.append('M2 execution has no collected evidence files')
    expected = config['expected']
    proof = result.get('artifact_verification') or {}
    if (not isinstance(proof, dict) or proof.get('status') != 'PASS'
            or not _same_digest(proof.get('sha256'), expected.get('sha256'))
            or any(proof.get(key) != expected.get(key) for key in ('package', 'version_code', 'version_name'))):
        blockers.append('artifact_verification: verified APKS identity does not match the contract')
    else:
        environment = proof.get('environment_evidence') or {}
        policy = config.get('environment_policy', {})
        if (not isinstance(environment, dict) or environment.get('matched') is not True
                or not _same_digest(environment.get('asset_sha256'), policy.get('asset_sha256'))
                or type(environment.get('observed_value')) is not type(policy.get('expected_value'))
                or environment.get('observed_value') != policy.get('expected_value')):
            blockers.append('artifact_verification.environment_evidence: test environment is unverified')
    return blockers


def _registry(config, result, blockers):
    digest = result['config_sha256']
    expected = config['expected']
    if not isinstance(expected.get('sha256'), str) or not re.fullmatch(r'[a-fA-F0-9]{64}', expected['sha256']):
        raise ValueError('the expected build must have an explicit SHA-256')
    readiness = config.get('readiness', {})
    reviewer = readiness.get('reviewer')
    reviewer = reviewer if isinstance(reviewer, str) and reviewer.strip() else 'unassigned'
    reviewed_at = readiness.get('reviewed_at')
    try:
        _timestamp(reviewed_at, 'readiness.reviewed_at')
    except ValueError:
        reviewed_at = 'unreviewed'
    readiness_source = readiness.get('source_ref')
    readiness_source = (readiness_source if isinstance(readiness_source, str) and readiness_source.strip()
                        else config['source_ref'])
    registry = M0Registry.create({'project_id': config['project_id'], 'name': config['project_id']})
    registry.data.update(scope='startup_readiness', business_status='NOT_EVALUATED', imported_from='M2')
    source_id, requirement_id = 'source_' + digest[:16], 'requirement_' + config['case_id']
    risk_id, point_id = 'risk_' + config['case_id'], 'testpoint_' + config['case_id']
    build_id = 'build_' + expected['sha256'].lower()
    reviewed = readiness.get('approved') is True and not any(item.startswith('readiness.') for item in blockers)
    registry.register_source(source_id, {
        'source_ref': config['source_ref'], 'source_version': digest, 'content_sha256': digest,
        'permission': 'human-reviewed-startup-contract' if reviewed else 'unapproved-startup-input',
        'excerpt': 'Android startup readiness only; business assertions are NOT_EVALUATED.',
        'reviewer': reviewer, 'model_access': False,
    })
    registry.data['sources'][source_id]['readiness_source_ref'] = readiness.get('source_ref')
    registry.register_build(build_id, {
        'artifact_type': 'apks', 'artifact_path': config['apks_path'], 'artifact_sha256': expected['sha256'],
        'package_name': expected['package'], 'version_code': expected['version_code'],
        'version_name': expected['version_name'], 'environment': 'test',
        'environment_source': config['environment_policy']['source_ref'],
        'artifact_relation': 'M2 startup contract binding; see artifact_verification for verification status',
        'runtime': {'server': 'unknown', 'resource_version': 'unknown', 'dll_version': 'unknown', 'verified': False},
    })
    registry.data['builds'][build_id]['artifact_verification'] = deepcopy(result.get('artifact_verification'))
    registry.register_requirement(requirement_id, {
        'source_id': source_id, 'title': 'Android startup readiness', 'status': 'draft',
        'acceptance_ref': readiness_source, 'version': 1,
    })
    registry.register_risk(risk_id, {'requirement_id': requirement_id, 'severity': 'unassessed',
                                   'description': 'Startup readiness does not establish business correctness.'})
    registry.register_testpoint(point_id, {'requirement_id': requirement_id, 'risk_id': risk_id,
                                        'title': 'Reviewed Android startup readiness signals'})
    initial_state = {'data_policy': 'preserve', 'business_state': 'NOT_EVALUATED',
                     'installed_package': deepcopy(result.get('installed_package'))}
    # The startup contract does not approve an M0/M1 business case or account state.
    registry.register_case(config['case_id'], {
        'requirement_id': requirement_id, 'source_id': source_id, 'source_ref': config['source_ref'],
        'version': 1, 'approved': False, 'scenario_id': 'android-startup-readiness',
        'risk_id': risk_id, 'testpoint_id': point_id, 'build_id': build_id, 'environment': 'test',
        'device_alias': config['device_alias'], 'account_alias': 'unknown', 'initial_state': initial_state,
        'steps': ['Verify approved test artifact and installed package identity',
                  'Observe Android startup readiness within the configured budget'],
        'expectation': {'scope': 'startup_readiness', 'readiness_contract': deepcopy(readiness),
                        'business_status': 'NOT_EVALUATED'},
        'recovery': {'data_policy': 'preserve', 'approved': False, 'method': 'no automatic reset'},
        'reviewer': reviewer, 'reviewed_at': reviewed_at,
        'max_wait_seconds': config['budgets']['startup_seconds'],
    })
    registry.data['cases'][config['case_id']].update(scope='startup_readiness', business_status='NOT_EVALUATED')
    # M0's execution contract is business-oriented. Keep this case in draft until
    # M0 can review a startup scope; the actual M2 execution stays in the handoff.
    return registry, build_id


def export_m2_trace(config, result, output_path) -> dict:
    """Verify files and export a startup-only handoff; never infer business approval."""
    root, source_path, evidence = _verify_inputs(config, result)
    target = Path(output_path).resolve()
    protected = {source_path, Path(config['apks_path']).resolve(), *(Path(item['path']) for item in evidence)}
    if target in protected:
        raise ValueError('trace export cannot overwrite its source result, artifact, or evidence')
    blockers = _handoff_blockers(config, result, evidence)
    registry, build_id = _registry(config, result, blockers)
    registry.validate(evidence_root=root)
    unresolved = list(blockers) + ['M0.case_review', 'account_alias', 'initial_business_state',
                                  'runtime.resource_version', 'runtime.dll_version', 'business_assertions',
                                  'evidence.retention_owner_review']
    report = registry.report()
    report.update(scope='startup_readiness', business_status='NOT_EVALUATED')
    exported = {
        'schema_version': 1, 'status': 'BLOCKED' if blockers else 'READY_FOR_M0_REVIEW',
        'scope': 'startup_readiness', 'business_status': 'NOT_EVALUATED', 'm2_status': result['status'],
        'run_id': result['run_id'], 'attempt_id': result['attempt_id'], 'case_id': result['case_id'],
        'config_sha256': result['config_sha256'], 'output_path': str(target),
        'm0_run_imported': False,
        'chain': {'source_ref': config['source_ref'], 'readiness_source_ref': config.get('readiness', {}).get('source_ref'),
                  'case_id': result['case_id'], 'build_id': build_id, 'run_id': result['run_id'],
                  'attempt_id': result['attempt_id'], 'result_path': str(source_path), 'evidence': evidence},
        'unresolved_bindings': unresolved, 'registry': registry.data, 'm0_report': report,
    }
    payload = json.dumps(exported, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.' + target.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(payload, encoding='utf-8')
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return exported
