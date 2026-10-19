"""Review supplied loaded-DLL identity evidence without device or business actions.

Hashes bind supplied files and metadata. They do not authenticate who produced a
recording or prove that a recorded provider is currently running on a device.
"""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID, uuid4

from .bridge_v2 import ObservationStream
from .pe_identity import read_mvid


IDENTITY_MARKER = 'AI_TEST_LOADED_DLL_JSON:'
MAX_DLL_BYTES = 256 * 1024 * 1024
MAX_PROOF_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 256 * 1024 * 1024
MAX_IDENTITY_BYTES = 16 * 1024
MAX_CAPTURE_LINE_BYTES = 4 * 1024 * 1024 + 2
_RECORD = {'schema_version', 'evidence_mode', 'mode', 'manifest', 'dll_path',
           'identity_line', 'observation_line', 'transport'}
_MANIFEST = {'schema_version', 'runtime_build_id', 'provider', 'assembly_name',
             'dll_sha256', 'module_mvid', 'source_snapshot_sha256', 'runtime_kind',
             'resource_version', 'server', 'artifact', 'mvid_source'}
_IDENTITY = {'schema', 'runtime_kind', 'build_id', 'assembly_name', 'dll_sha256', 'module_mvid', 'mvid_source'}
_ARTIFACT = {'sha256', 'package', 'version_code', 'version_name'}
_TRANSPORT = {'device_alias', 'pid', 'log_start', 'log_end', 'installed_proof', 'log_evidence'}
_TIME = r'[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}'
_PREFIX = re.compile(r'^(?P<time>' + _TIME + r')[ \t]+(?P<pid>[1-9][0-9]*)[ \t]+'
                     r'(?P<tid>[1-9][0-9]*)[ \t]+[VDIWEFAS][ \t]+Unity[ \t]*:[ \t]*')
_SHA256 = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
_MVID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z', re.ASCII)


def _keys(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f'{label} must contain exactly {", ".join(sorted(fields))}')


def _text(value, label, limit=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f'{label} requires nonempty bounded single-line text')
    return value


def _integer(value, label, maximum=(1 << 31) - 1, *, minimum=1):
    if type(value) is not int or not minimum <= value <= maximum:
        kind = 'nonnegative' if minimum == 0 else 'positive'
        raise ValueError(f'{label} requires a {kind} bounded integer')


def _digest(value, label):
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f'{label} requires a lowercase SHA-256 digest')


def _mvid(value, label):
    if not isinstance(value, str) or _MVID.fullmatch(value) is None or UUID(value).int == 0:
        raise ValueError(f'{label} requires a canonical nonzero lowercase module GUID')


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'duplicate JSON field: {key}')
        value[key] = item
    return value


def _constant(value):
    raise ValueError(f'nonfinite JSON value: {value}')


def _json(text, label):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f'{label} JSON is malformed or exceeds nesting limits') from error


def _file(root, value, label):
    path_text = _text(value, label, 4096)
    path = (root / path_text).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'{label} must resolve to a file inside evidence_root')
    return path


def _hash_file(path, maximum, label, *, allow_empty=False):
    digest, total = hashlib.sha256(), 0
    with path.open('rb') as handle:
        while chunk := handle.read(min(1024 * 1024, maximum + 1 - total)):
            total += len(chunk)
            if total > maximum:
                raise ValueError(f'{label} exceeds its byte budget')
            digest.update(chunk)
    if total == 0 and not allow_empty:
        raise ValueError(f'{label} must not be empty')
    return {'path': str(path), 'bytes': total, 'sha256': digest.hexdigest()}


def _dll_bytes(path):
    """Hash and retain one bounded read so MVID and SHA bind exactly the same bytes."""
    digest, total, payload = hashlib.sha256(), 0, bytearray()
    with path.open('rb') as handle:
        while chunk := handle.read(min(1024 * 1024, MAX_DLL_BYTES+1-total)):
            total += len(chunk)
            if total > MAX_DLL_BYTES:
                raise ValueError('DLL exceeds its byte budget')
            digest.update(chunk)
            payload.extend(chunk)
    if total == 0:
        raise ValueError('DLL must not be empty')
    return {'path': str(path), 'bytes': total, 'sha256': digest.hexdigest()}, payload


def _manifest(value, mode):
    _keys(value, _MANIFEST, 'manifest')
    if type(value['schema_version']) is not int or value['schema_version'] != 2:
        raise ValueError('manifest.schema_version must be integer 2')
    for field in ('runtime_build_id', 'provider', 'assembly_name'):
        _text(value[field], 'manifest.' + field)
    for field in ('dll_sha256', 'source_snapshot_sha256'):
        _digest(value[field], 'manifest.' + field)
    _mvid(value['module_mvid'], 'manifest.module_mvid')
    expected_kind = 'editor_probe' if mode == 'assembly_probe' else 'android_player'
    if value['runtime_kind'] != expected_kind:
        raise ValueError('manifest.runtime_kind does not match the selected mode')
    expected_source = 'runtime_api' if mode == 'assembly_probe' else 'loaded_pe_metadata'
    if value['mvid_source'] != expected_source:
        raise ValueError('manifest.mvid_source does not match the selected mode')
    if mode == 'assembly_probe':
        if value['artifact'] is not None or value['resource_version'] != '' or value['server'] != '':
            raise ValueError('assembly_probe requires null artifact and empty resource_version/server')
    else:
        for field in ('resource_version', 'server'):
            _text(value[field], 'manifest.' + field)
        artifact = value['artifact']
        _keys(artifact, _ARTIFACT, 'manifest.artifact')
        _digest(artifact['sha256'], 'manifest.artifact.sha256')
        _integer(artifact['version_code'], 'manifest.artifact.version_code')
        _text(artifact['version_name'], 'manifest.artifact.version_name')
        if (not isinstance(artifact['package'], str)
                or re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', artifact['package']) is None
                or len(artifact['package']) > 512):
            raise ValueError('manifest.artifact.package requires an Android package name')


def _identity(line, manifest):
    _text(line, 'identity_line', MAX_IDENTITY_BYTES)
    if len(line.encode('utf-8')) > MAX_IDENTITY_BYTES or line.count(IDENTITY_MARKER) != 1:
        raise ValueError('identity_line requires exactly one bounded identity marker')
    prefix = line.split(IDENTITY_MARKER, 1)[0]
    if prefix and (_PREFIX.fullmatch(prefix) is None):
        raise ValueError('identity_line prefix must be an Android threadtime Unity prefix or empty')
    identity = _json(line.split(IDENTITY_MARKER, 1)[1], 'identity')
    _keys(identity, _IDENTITY, 'identity')
    if type(identity['schema']) is not int or identity['schema'] != 2:
        raise ValueError('identity.schema must be integer 2')
    expected = {'runtime_kind': manifest['runtime_kind'], 'build_id': manifest['runtime_build_id'],
                **{key: manifest[key] for key in ('assembly_name', 'dll_sha256', 'module_mvid', 'mvid_source')}}
    if any(identity[key] != value for key, value in expected.items()):
        raise ValueError('loaded identity does not exactly match the manifest')
    return identity


def _time(value, label, year=2000):
    if not isinstance(value, str) or re.fullmatch(_TIME, value, flags=re.ASCII) is None:
        raise ValueError(f'{label} requires MM-DD HH:MM:SS.mmm device time')
    return datetime.strptime(f'{year}-{value}', '%Y-%m-%d %H:%M:%S.%f')


def _log_time(line, marker, transport, start, end):
    if not isinstance(line, str) or '\n' in line or '\r' in line:
        raise ValueError('device evidence requires one single-line Unity log')
    match = _PREFIX.match(line)
    if (match is None or not line[match.end():].startswith(marker)
            or int(match['pid']) != transport['pid'] or int(match['tid']) > (1 << 31) - 1):
        raise ValueError('device log requires the exact transport PID and bounded threadtime Unity prefix')
    stamp = _time(match['time'], 'device log time', start.year)
    if stamp < start:
        stamp = stamp.replace(year=stamp.year + 1)
    if not start <= stamp <= end:
        raise ValueError('device log timestamp is outside the current capture bracket')
    return stamp


def _capture_log(recording, root, verified):
    evidence = recording['transport']['log_evidence']
    _keys(evidence, {'path', 'bytes', 'sha256'}, 'transport.log_evidence')
    _integer(evidence['bytes'], 'transport.log_evidence.bytes', MAX_EVIDENCE_BYTES)
    _digest(evidence['sha256'], 'transport.log_evidence.sha256')
    path = _file(root, evidence['path'], 'transport.log_evidence.path')
    wanted = [recording[field].encode('utf-8') for field in ('identity_line', 'observation_line')]
    positions = [[], []]
    digest, total, line_number = hashlib.sha256(), 0, 0
    with path.open('rb') as handle:
        while raw := handle.readline(MAX_CAPTURE_LINE_BYTES + 1):
            total += len(raw)
            line_number += 1
            if total > MAX_EVIDENCE_BYTES or len(raw) > MAX_CAPTURE_LINE_BYTES:
                raise ValueError('provider capture log exceeds its byte/line budget')
            digest.update(raw)
            content = raw[:-1] if raw.endswith(b'\n') else raw
            if content.endswith(b'\r'):
                content = content[:-1]
            for index, expected in enumerate(wanted):
                if content == expected:
                    positions[index].append(line_number)
    observed = {'path': str(path), 'bytes': total, 'sha256': digest.hexdigest()}
    if total != evidence['bytes'] or observed['sha256'] != evidence['sha256']:
        raise ValueError('provider capture log bytes/hash do not match the real file')
    if any(len(found) != 1 for found in positions) or positions[0][0] >= positions[1][0]:
        raise ValueError('provider capture log must contain each supplied complete line exactly once, identity before observation')
    verified.append(observed)


def _m2_evidence(proof, proof_path, root, verified):
    evidence = proof.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4096:
        raise ValueError('installed_proof requires nonempty bounded collected evidence')
    total, seen = 0, set()
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError('installed_proof evidence must be objects')
        _text(item.get('kind'), 'installed_proof.evidence.kind')
        relative = Path(_text(item.get('relative_path'), 'installed_proof.evidence.relative_path', 4096))
        if relative.is_absolute():
            raise ValueError('installed_proof evidence relative_path cannot be absolute')
        path = _file(root, str(proof_path.parent / relative), 'installed_proof evidence')
        if not path.is_relative_to(proof_path.parent):
            raise ValueError('installed_proof evidence escapes its result directory')
        declared = _file(root, item.get('path'), 'installed_proof.evidence.path')
        if declared != path or path in seen:
            raise ValueError('installed_proof evidence paths must agree and be distinct')
        # M2 retains successful command stdout/stderr even when the stream is empty.
        # Empty files still require an actual file and its exact SHA-256 binding.
        _integer(item.get('bytes'), 'installed_proof.evidence.bytes', MAX_EVIDENCE_BYTES, minimum=0)
        _digest(item.get('sha256'), 'installed_proof.evidence.sha256')
        observed = _hash_file(path, min(MAX_EVIDENCE_BYTES, MAX_TOTAL_EVIDENCE_BYTES - total),
                              'installed_proof evidence', allow_empty=True)
        if observed['bytes'] != item['bytes'] or observed['sha256'] != item['sha256']:
            raise ValueError('installed_proof referenced evidence bytes/hash do not match the file')
        total += observed['bytes']
        verified.append(observed)
        seen.add(path)


def _installed_proof(transport, manifest, root, verified):
    proof_path = _file(root, transport['installed_proof'], 'transport.installed_proof')
    with proof_path.open('rb') as handle:
        raw = handle.read(MAX_PROOF_BYTES + 1)
    if not raw or len(raw) > MAX_PROOF_BYTES:
        raise ValueError('installed_proof is empty or exceeds the 8 MiB byte budget')
    proof = _json(raw.decode('utf-8-sig'), 'installed_proof')
    if (not isinstance(proof, dict) or type(proof.get('schema_version')) is not int
            or proof['schema_version'] != 1 or proof.get('status') != 'PASS'
            or proof.get('scope') != 'startup_readiness' or proof.get('environment') != 'test'
            or proof.get('device_alias') != transport['device_alias']
            or proof.get('package') != manifest['artifact']['package']):
        raise ValueError('installed_proof must be a passing test M2 result for this device/package')
    artifact = manifest['artifact']
    installed = proof.get('installed_package')
    if (not isinstance(installed, dict)
            or type(installed.get('version_code')) is not int
            or any(installed.get(key) != artifact[key] for key in ('version_code', 'version_name'))):
        raise ValueError('installed_proof current installed package version does not match the artifact')
    if not isinstance(proof.get('installed_environment_evidence'), dict) or proof['installed_environment_evidence'].get('verified') is not True:
        raise ValueError('installed_proof actual installed test environment is not verified')
    checked = proof.get('artifact_verification')
    if (not isinstance(checked, dict) or checked.get('status') != 'PASS'
            or type(checked.get('version_code')) is not int
            or any(checked.get(key) != artifact[key] for key in _ARTIFACT)
            or not isinstance(checked.get('environment_evidence'), dict)
            or checked['environment_evidence'].get('matched') is not True):
        raise ValueError('installed_proof artifact identity or test environment does not match the manifest')
    hashes, bases, actual = checked.get('apk_hashes'), checked.get('base_apks'), proof.get('installed_apk_hashes_after')
    if (not isinstance(hashes, dict) or not hashes or len(hashes) > 4096
            or not isinstance(bases, list) or not bases or len(bases) > 4096
            or not isinstance(actual, dict) or not actual or len(actual) > 4096):
        raise ValueError('installed_proof requires actual installed APK and artifact base/member hashes')
    for name, digest in [*hashes.items(), *actual.items()]:
        _text(name, 'installed_proof APK name')
        _digest(digest, 'installed_proof APK hash')
    base_hashes = set()
    for base in bases:
        if not isinstance(base, dict):
            raise ValueError('installed_proof base APK must be an object')
        _digest(base.get('sha256'), 'installed_proof base APK hash')
        name = _text(base.get('archive_apk'), 'installed_proof base APK name')
        if hashes.get(name) != base['sha256']:
            raise ValueError('installed_proof base APK hash is not bound to an archive member')
        base_hashes.add(base['sha256'])
    allowed = set(hashes.values())
    if actual.get('base.apk') not in base_hashes or any(value not in allowed for value in actual.values()):
        raise ValueError('installed_proof actual installed APK hashes do not belong to the artifact')
    observations = proof.get('observations')
    if (not isinstance(observations, list) or not observations or not isinstance(observations[-1], dict)
            or str(observations[-1].get('pid')) != str(transport['pid'])):
        raise ValueError('installed_proof current observed PID does not match the recording')
    verified.append({'path': str(proof_path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    _m2_evidence(proof, proof_path, root, verified)


def _device(recording, root, verified):
    transport, manifest = recording['transport'], recording['manifest']
    _keys(transport, _TRANSPORT, 'transport')
    _text(transport['device_alias'], 'transport.device_alias')
    _integer(transport['pid'], 'transport.pid')
    start = _time(transport['log_start'], 'transport.log_start')
    end = _time(transport['log_end'], 'transport.log_end', start.year)
    if end < start:
        end = end.replace(year=end.year + 1)
    if end - start > timedelta(seconds=10):
        raise ValueError('device capture bracket exceeds 10 seconds or is reversed')
    identity_time = _log_time(recording['identity_line'], IDENTITY_MARKER, transport, start, end)
    observation_time = _log_time(recording['observation_line'], 'AI_TEST_OBSERVATION_JSON:', transport, start, end)
    if observation_time < identity_time:
        raise ValueError('device observation must follow the loaded identity inside the capture bracket')
    source = {'build_id': manifest['runtime_build_id'], 'resource_version': manifest['resource_version'],
              'dll_version': 'sha256:' + manifest['dll_sha256'], 'server': manifest['server']}
    observation = ObservationStream(expected_source=source, expected_provider=manifest['provider']).parse_line(recording['observation_line'])
    _capture_log(recording, root, verified)
    _installed_proof(transport, manifest, root, verified)
    return observation


def inspect_provider_recording(recording, *, evidence_root):
    """Return identity PASS or context BLOCKED; never assess business or act on a device."""
    result = {'schema_version': 1, 'status': 'BLOCKED', 'category': 'PROVIDER_EVIDENCE_INVALID',
              'business_status': 'NOT_EVALUATED', 'execution': 'device_free',
              'evidence_mode': None, 'mode': None, 'scope': 'provider_identity_evidence',
              'live_provider': False, 'reasons': [], 'verified_files': []}
    try:
        _keys(recording, _RECORD, 'recording')
        if type(recording['schema_version']) is not int or recording['schema_version'] != 1:
            raise ValueError('recording.schema_version must be integer 1')
        if recording['evidence_mode'] not in ('synthetic', 'recorded'):
            raise ValueError('recording.evidence_mode must be synthetic or recorded')
        result['evidence_mode'] = recording['evidence_mode']
        if recording['mode'] not in ('assembly_probe', 'device'):
            raise ValueError('recording.mode must be assembly_probe or device')
        mode = recording['mode']
        result.update(mode=mode, scope='compiled_assembly_identity' if mode == 'assembly_probe' else 'provider_runtime_identity')
        _manifest(recording['manifest'], mode)
        root = Path(evidence_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('evidence_root requires an existing directory')
        dll = _file(root, recording['dll_path'], 'dll_path')
        observed, payload = _dll_bytes(dll)
        result['verified_files'].append(observed)
        if observed['sha256'] != recording['manifest']['dll_sha256']:
            raise ValueError('actual DLL SHA-256 does not match the manifest')
        actual_mvid = read_mvid(payload)
        if actual_mvid != recording['manifest']['module_mvid']:
            raise ValueError('actual DLL PE Module MVID does not match the manifest')
        result['dll_metadata'] = {'module_mvid': actual_mvid, 'verification': 'pe_cli_metadata'}
        identity = _identity(recording['identity_line'], recording['manifest'])
        if mode == 'assembly_probe':
            if recording['observation_line'] is not None or recording['transport'] is not None:
                raise ValueError('assembly_probe requires null observation_line and transport')
        else:
            result['observation'] = _device(recording, root, result['verified_files'])
        result.update(status='PASS', category='IDENTITY_CONFIRMED' if mode == 'assembly_probe' else 'PROVIDER_IDENTITY_CONFIRMED',
                      manifest=deepcopy(recording['manifest']), identity=deepcopy(identity))
        return result
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError) as error:
        result['reasons'] = [str(error)]
        return result


def write_provider_report(result, path):
    """Atomically replace a human-readable report using an exclusive temporary file."""
    lines = ['# M3.2 Provider Identity Evidence Review', '',
             f"Result: {result['status']} / {result['category']}",
             f"Scope: {result['scope']}; execution: {result['execution']}; evidence mode: {result['evidence_mode']}.",
             f"Business status: {result['business_status']}; live provider: {result['live_provider']}.", '',
             'This review checks supplied identity evidence. It does not authenticate its origin, establish a currently live provider, or execute game business actions.',
             '', '## Reasons', '']
    lines += ['- ' + str(reason) for reason in result.get('reasons', [])]
    lines += ['', '## Verified files', '']
    lines += [f"- {item['path']}: {item['bytes']} bytes; SHA-256 {item['sha256']}" for item in result.get('verified_files', [])]
    target = Path(path)
    temporary = target.with_name('.' + target.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
