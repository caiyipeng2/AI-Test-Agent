"""Explicitly synthetic M3.2 identity fixtures, never real PE/device evidence."""

import hashlib
import json
from pathlib import Path

from m3_support import line, observation


IDENTITY_MARKER = 'AI_TEST_LOADED_DLL_JSON:'


def identity_line(value, timestamp=None, pid=123):
    prefix = '' if timestamp is None else f'{timestamp} {pid} {pid} I Unity: '
    return prefix + IDENTITY_MARKER + json.dumps(value)


def refresh_capture_log(root, recording):
    """Persist explicitly synthetic complete provider lines and their hash binding."""
    content = (recording['identity_line'] + '\n' + recording['observation_line'] + '\n').encode('utf-8')
    path = Path(root) / 'synthetic-provider.log'
    path.write_bytes(content)
    recording['transport']['log_evidence'] = {'path': path.name, 'bytes': len(content),
                                               'sha256': hashlib.sha256(content).hexdigest()}


def provider_recording(root, mode='assembly_probe'):
    """Create fake bytes and supplied observed metadata, explicitly synthetic."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dll = root / 'synthetic-hotfix.dll'
    dll.write_bytes(b'synthetic fixture; not a PE assembly or runtime execution')
    manifest = {
        'schema_version': 1, 'runtime_build_id': 'synthetic-build-stamp',
        'provider': 'SyntheticReadOnlyProvider/v1', 'assembly_name': 'SyntheticHotfix',
        'dll_sha256': hashlib.sha256(dll.read_bytes()).hexdigest(),
        'module_mvid': '12345678-1234-4567-89ab-123456789abc',
        'source_snapshot_sha256': hashlib.sha256(b'synthetic source snapshot').hexdigest(),
        'runtime_kind': 'editor_probe', 'resource_version': '', 'server': '', 'artifact': None,
    }
    identity = {'schema': 1, 'runtime_kind': manifest['runtime_kind'],
                'build_id': manifest['runtime_build_id'], 'assembly_name': manifest['assembly_name'],
                'dll_sha256': manifest['dll_sha256'], 'module_mvid': manifest['module_mvid']}
    result = {'schema_version': 1, 'evidence_mode': 'synthetic', 'mode': mode,
              'manifest': manifest, 'dll_path': dll.name, 'identity_line': identity_line(identity),
              'observation_line': None, 'transport': None}
    if mode == 'device':
        manifest.update(runtime_kind='android_player', resource_version='synthetic-resource', server='test')
        manifest['artifact'] = {'sha256': 'a' * 64, 'package': 'com.example.synthetic',
                                'version_code': 1, 'version_name': 'synthetic-1'}
        identity['runtime_kind'] = 'android_player'
        result['identity_line'] = identity_line(identity, timestamp='10-16 10:00:00.010')
        state = observation(provider=manifest['provider'])
        state['source'] = {'available': True, 'build_id': manifest['runtime_build_id'],
                           'resource_version': manifest['resource_version'],
                           'dll_version': 'sha256:' + manifest['dll_sha256'], 'server': manifest['server']}
        state['target'] = {'available': False, 'target_id': '', 'window_id': '',
                           'level': -1, 'cost': '', 'ready': False}
        state['wallet'] = {'available': False, 'currency_id': '', 'balance': ''}
        state['ledger'] = {'available': False, 'complete': False, 'first_seq': 1,
                           'last_seq': 0, 'events': []}
        result['observation_line'] = line(state, timestamp='10-16 10:00:00.020')
        result['transport'] = {'device_alias': 'synthetic-device', 'pid': 123,
                               'log_start': '10-16 10:00:00.000', 'log_end': '10-16 10:00:00.050',
                               'installed_proof': 'synthetic-m2-result.json'}
        refresh_capture_log(root, result)
        content = b'explicitly synthetic M2 captured log evidence\n'
        evidence_path = root / 'synthetic-m2.log'
        evidence_path.write_bytes(content)
        proof = {'schema_version': 1, 'scope': 'startup_readiness', 'status': 'PASS',
                 'environment': 'test', 'device_alias': 'synthetic-device',
                 'package': manifest['artifact']['package'],
                 'installed_package': {'version_code': 1, 'version_name': 'synthetic-1'},
                 'installed_environment_evidence': {'verified': True},
                 'installed_apk_hashes_after': {'base.apk': 'b' * 64, 'split_config.apk': 'c' * 64},
                 'artifact_verification': {'status': 'PASS', **manifest['artifact'],
                     'environment_evidence': {'matched': True},
                     'base_apks': [{'archive_apk': 'splits/base.apk', 'sha256': 'b' * 64}],
                     'apk_hashes': {'splits/base.apk': 'b' * 64, 'splits/config.apk': 'c' * 64}},
                 'observations': [{'pid': '123'}],
                 'evidence': [{'kind': 'log', 'path': str(evidence_path),
                               'relative_path': evidence_path.name, 'bytes': len(content),
                               'sha256': hashlib.sha256(content).hexdigest()}]}
        (root / result['transport']['installed_proof']).write_text(json.dumps(proof), encoding='utf-8')
    return result
