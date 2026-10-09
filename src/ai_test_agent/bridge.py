"""Strict parsing of opt-in, read-only Unity snapshot log records."""

import json
import math
import re
from decimal import Decimal

MARKER = 'AI_TEST_STATE_JSON:'


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'duplicate JSON field: {key}')
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError(f'nonfinite JSON value: {value}')


def _string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a known nonempty string')


def _availability(value, name):
    if not isinstance(value, dict) or type(value.get('available')) is not bool:
        raise ValueError(f'{name}.available must be explicit true or false')
    return value['available']


def _exact(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', value):
        raise ValueError(f'{name} must be an exact, finite decimal string')
    if Decimal(value) < 0:
        raise ValueError(f'{name} cannot be negative')


def parse_snapshot_line(line: str) -> dict:
    """Parse one log line; use SnapshotStream for sequence/version binding."""
    if not isinstance(line, str) or line.count(MARKER) != 1:
        raise ValueError('exactly one snapshot marker is required')
    try:
        value = json.loads(line.split(MARKER, 1)[1].strip(), object_pairs_hook=_object, parse_constant=_nonfinite)
    except json.JSONDecodeError as error:
        raise ValueError('snapshot JSON is truncated or malformed') from error
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1:
        raise ValueError('unsupported snapshot schema')
    for field in ('seq', 'frame'):
        if type(value.get(field)) is not int or value[field] < 0:
            raise ValueError(f'{field} must be a nonnegative integer')
    for field in ('session_id', 'provider'):
        _string(value.get(field), field)
    source = value.get('source')
    if _availability(source, 'source'):
        for field in ('build_id', 'resource_version', 'dll_version', 'server'):
            _string(source.get(field), 'source.' + field)
    windows = value.get('windows')
    available = _availability(windows, 'windows')
    items = windows.get('items', [])
    if not isinstance(items, list) or (not available and items):
        raise ValueError('windows must contain an explicit list consistent with availability')
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('window must be an object')
        _string(item.get('path'), 'window.path')
        for field in ('x', 'y', 'width', 'height'):
            number = item.get(field)
            if type(number) not in (int, float) or not math.isfinite(number) or (field in ('width', 'height') and number < 0):
                raise ValueError('window bounds must be finite numbers with nonnegative dimensions')
    target = value.get('target')
    if _availability(target, 'target'):
        _string(target.get('target_id'), 'target.target_id')
        if type(target.get('level')) is not int or target['level'] < 0:
            raise ValueError('target.level must be a known nonnegative integer')
        _exact(target.get('coins'), 'target.coins')
        _exact(target.get('cost'), 'target.cost')
    else:
        if target.get('level', -1) != -1 or target.get('coins') not in (None, '') or target.get('cost') not in (None, ''):
            raise ValueError('unavailable target must not supply invented business values')
    return value


class SnapshotStream:
    """Maintain validation state within one host session, never globally."""

    def __init__(self, expected_build_id=None, expected_provider=None):
        self.expected_build_id = expected_build_id
        self.expected_provider = expected_provider
        self._sessions = {}

    def parse_line(self, line):
        value = parse_snapshot_line(line)
        source = value['source']
        if self.expected_build_id is not None and (not source['available'] or source.get('build_id') != self.expected_build_id):
            raise ValueError('snapshot does not match the bound build')
        if self.expected_provider is not None and value['provider'] != self.expected_provider:
            raise ValueError('snapshot does not match the bound provider')
        identity = (value['provider'], source.get('build_id'), source.get('resource_version'),
                    source.get('dll_version'), source.get('server'), source['available'])
        previous = self._sessions.get(value['session_id'])
        if previous is not None:
            seq, frame, old_identity = previous
            if value['seq'] <= seq or value['frame'] < frame:
                raise ValueError('duplicate, backward or stale snapshot sequence')
            if identity != old_identity:
                raise ValueError('runtime versions changed inside a snapshot session')
        self._sessions[value['session_id']] = (value['seq'], value['frame'], identity)
        return value
