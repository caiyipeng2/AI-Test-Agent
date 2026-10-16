"""Explicitly synthetic, portable M3 fixtures shared by protocol/evidence tests."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import zlib


def observation(seq=1, **overrides):
    """Return a fresh schema2 observation; overrides replace top-level fields."""
    value = {
        'schema': 2, 'session_id': 'synthetic-session', 'seq': seq, 'frame': 10,
        'provider': 'SyntheticProvider',
        'source': {'available': True, 'build_id': 'synthetic-build',
                   'resource_version': 'synthetic-resource', 'dll_version': 'synthetic-dll',
                   'server': 'test'},
        'target': {'available': True, 'target_id': 'device-1', 'window_id': 'upgrade-window',
                   'level': 1, 'cost': '10.05', 'ready': True},
        'wallet': {'available': True, 'currency_id': 'coin', 'balance': '100.25'},
        'ledger': {'available': True, 'complete': True, 'first_seq': 1,
                   'last_seq': 0, 'events': []},
    }
    value.update(deepcopy(overrides))
    return value


def line(value, timestamp='10-16 10:00:00.010', pid=123):
    """Wrap a synthetic observation in the Android threadtime Unity log format."""
    return f'{timestamp} {pid} {pid} I Unity: AI_TEST_OBSERVATION_JSON:' + json.dumps(value)


def _png(rgb):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00' + bytes(rgb))) + chunk(b'IEND', b''))


def recording(root):
    """Create two distinct real PNGs and return a valid synthetic evidence package."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    before = observation()
    after = observation(2, frame=20)
    after['target']['level'] = 2
    after['wallet']['balance'] = '91.45'
    after['ledger'].update(last_seq=2, events=[
        {'seq': 1, 'transaction_id': 'synthetic-income-1', 'operation_id': '',
         'kind': 'external', 'target_id': '', 'currency_id': 'coin',
         'delta': '+1.25', 'balance_after': '101.50'},
        {'seq': 2, 'transaction_id': 'synthetic-upgrade-1', 'operation_id': 'upgrade-1',
         'kind': 'upgrade', 'target_id': 'device-1', 'currency_id': 'coin',
         'delta': '-10.05', 'balance_after': '91.45'},
    ])
    result = {
        'schema_version': 1, 'evidence_mode': 'synthetic',
        'contract': {
            'approved': True, 'reviewer': 'synthetic fixture',
            'reviewed_at': '2026-10-16T10:00:00+08:00',
            'source_ref': 'synthetic-fixture:upgrade-requirement',
            'cost_source_ref': 'synthetic-fixture:independent-cost-rule',
            'provider': 'SyntheticProvider',
            'runtime_source': {key: value for key, value in before['source'].items()
                               if key != 'available'},
            'device_alias': 'synthetic-device', 'pid': 123, 'target_id': 'device-1',
            'window_id': 'upgrade-window', 'currency_id': 'coin',
            'operation_id': 'upgrade-1', 'expected_cost': '10.05',
            'trace': {'requirement_id': 'synthetic-requirement', 'case_id': 'synthetic-case',
                      'source_ref': 'synthetic-fixture:upgrade-requirement',
                      'run_id': 'synthetic-run', 'attempt_id': 'synthetic-attempt'},
        },
    }
    for name, value, second, rgb in (
            ('before', before, 0, (20, 40, 80)), ('after', after, 1, (40, 80, 160))):
        content = _png(rgb)
        path = root / (name + '.png')
        path.write_bytes(content)
        prefix = f'10-16 10:00:0{second}'
        result[name] = {
            'line': line(value, timestamp=prefix + '.010'),
            'capture': {
                'device_alias': 'synthetic-device', 'pid': 123,
                'log_start': prefix + '.000', 'log_end': prefix + '.050',
                'session_id': value['session_id'], 'seq': value['seq'],
                'observer': 'bridge', 'visual_confirmed': True,
                'visual_review_ref': 'synthetic-fixture:visual-' + name,
                'screenshot': {'evidence_id': 'synthetic-' + name, 'path': path.name,
                               'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
                               'width': 1, 'height': 1},
            },
        }
    return result
