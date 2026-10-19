"""Explicitly synthetic metadata-only PE fixtures, never executable/device proof."""

import hashlib
import json
from pathlib import Path
import struct
from uuid import UUID

from m3_support import line, observation


IDENTITY_MARKER = 'AI_TEST_LOADED_DLL_JSON:'
SYNTHETIC_MVID = '12345678-1234-4567-89ab-123456789abc'


def synthetic_metadata_pe(mvid=SYNTHETIC_MVID, *, pe32_plus=False, tables='#~',
                          string_width=2, guid_width=2, guid_index=1, guid_count=1,
                          extra_data=None):
    """A deliberately non-executable layout with one Module row, no IL/Assembly table.

    Layout locations are fixed for malformed-input tests: PE=0x80, raw section
    /CLI=0x200, metadata=0x280, table stream=metadata+0x60, GUID=metadata+0xa0.
    This is test data, never a generated game DLL or successful CLR load.
    """
    value = bytearray(0x600)
    value[:2] = b'MZ'
    struct.pack_into('<I', value, 0x3c, 0x80)
    value[0x80:0x84] = b'PE\0\0'
    optional_size = 240 if pe32_plus else 224
    struct.pack_into('<HHIIIHH', value, 0x84, 0x8664 if pe32_plus else 0x14c,
                     1, 0, 0, 0, optional_size, 0)
    optional = 0x98
    struct.pack_into('<H', value, optional, 0x20b if pe32_plus else 0x10b)
    struct.pack_into('<I', value, optional+60, 0x200)
    directories = 112 if pe32_plus else 96
    struct.pack_into('<I', value, optional+directories-4, 16)
    struct.pack_into('<II', value, optional+directories+14*8, 0x2000, 72)
    section = optional+optional_size
    value[section:section+8] = b'.text\0\0\0'
    struct.pack_into('<IIII', value, section+8, 0x400, 0x2000, 0x400, 0x200)
    struct.pack_into('<IHHII', value, 0x200, 72, 2, 5, 0x2080, 0xa0+16*guid_count)
    metadata = 0x280
    struct.pack_into('<IHHII', value, metadata, 0x424a5342, 1, 1, 0, 12)
    value[metadata+16:metadata+28] = b'v4.0.30319\0\0'
    struct.pack_into('<HH', value, metadata+28, 0, 2)
    heap_flags = int(string_width == 4) | (2 if guid_width == 4 else 0)
    if extra_data is not None:
        heap_flags |= 0x40
    module = struct.pack('<H', 0) + bytes(string_width)
    module += guid_index.to_bytes(guid_width, 'little') + bytes(guid_width*2)
    table_data = struct.pack('<IBBBBQQI', 0, 2, 0, heap_flags, 1, 1, 0, 1)
    if extra_data is not None:
        table_data += struct.pack('<I', extra_data)
    table_data += module
    struct.pack_into('<II', value, metadata+32, 0x60, len(table_data))
    value[metadata+40:metadata+44] = tables.encode('ascii')+b'\0\0'
    struct.pack_into('<II', value, metadata+44, 0xa0, 16*guid_count)
    value[metadata+52:metadata+60] = b'#GUID\0\0\0'
    value[metadata+0x60:metadata+0x60+len(table_data)] = table_data
    for index in range(guid_count):
        selected = UUID(mvid).bytes_le if index == guid_count-1 else UUID(int=index+1).bytes_le
        value[metadata+0xa0+16*index:metadata+0xb0+16*index] = selected
    return bytes(value)


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
    """Create a metadata-only test layout and declarations, explicitly synthetic."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dll = root / 'synthetic-hotfix.dll'
    dll.write_bytes(synthetic_metadata_pe())
    manifest = {
        'schema_version': 2, 'runtime_build_id': 'synthetic-build-stamp',
        'provider': 'SyntheticReadOnlyProvider/v1', 'assembly_name': 'SyntheticHotfix',
        'dll_sha256': hashlib.sha256(dll.read_bytes()).hexdigest(),
        'module_mvid': SYNTHETIC_MVID, 'mvid_source': 'runtime_api',
        'source_snapshot_sha256': hashlib.sha256(b'synthetic source snapshot').hexdigest(),
        'runtime_kind': 'editor_probe', 'resource_version': '', 'server': '', 'artifact': None,
    }
    identity = {'schema': 2, 'runtime_kind': manifest['runtime_kind'],
                'build_id': manifest['runtime_build_id'], 'assembly_name': manifest['assembly_name'],
                'dll_sha256': manifest['dll_sha256'], 'module_mvid': manifest['module_mvid'],
                'mvid_source': manifest['mvid_source']}
    result = {'schema_version': 1, 'evidence_mode': 'synthetic', 'mode': mode,
              'manifest': manifest, 'dll_path': dll.name, 'identity_line': identity_line(identity),
              'observation_line': None, 'transport': None}
    if mode == 'device':
        manifest.update(runtime_kind='android_player', resource_version='synthetic-resource', server='test',
                        mvid_source='loaded_pe_metadata')
        manifest['artifact'] = {'sha256': 'a' * 64, 'package': 'com.example.synthetic',
                                'version_code': 1, 'version_name': 'synthetic-1'}
        identity['runtime_kind'] = 'android_player'
        identity['mvid_source'] = 'loaded_pe_metadata'
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
