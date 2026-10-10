"""Single-upgrade assessment using reviewed expectations and exact amounts."""

import re
from copy import deepcopy
from decimal import Decimal, DecimalException, localcontext

from .contracts import evidence_for_png


def _amount(value, name):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f'{name} requires an exact decimal string or integer')
    text = str(value)
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', text):
        raise ValueError(f'{name} is unknown, abbreviated or nonfinite')
    return Decimal(text)


def _known_id(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a known nonempty reference')
    return value


def _assertion(name, status='BLOCKED', expected=None, actual=None, reasons=None):
    return {'name': name, 'status': status, 'passed': status == 'PASS',
            'expected': expected, 'actual': actual, 'reasons': reasons or []}


def assess_upgrade(before: dict, after: dict, expectation: dict) -> dict:
    """Context errors block all grading; within valid context, known failures win."""
    result = {'status': 'BLOCKED', 'category': 'INSUFFICIENT_EVIDENCE',
              'assertions': [_assertion(name) for name in ('visual', 'level', 'coins')],
              'reasons': [], 'evidence': {}, 'observations': {}, 'trace': {}}
    if isinstance(expectation, dict) and isinstance(expectation.get('trace'), dict):
        result['trace'] = deepcopy(expectation['trace'])
    for name, snapshot in (('before', before), ('after', after)):
        if isinstance(snapshot, dict):
            result['observations'][name] = {field: snapshot.get(field) for field in
                ('observation_id', 'device_id', 'window_id', 'target_id', 'build_id', 'observer')}
            if isinstance(snapshot.get('screenshot'), dict):
                result['evidence'][name] = deepcopy(snapshot['screenshot'])
                result['observations'][name]['evidence_id'] = snapshot['screenshot'].get('evidence_id')
    try:
        if not all(isinstance(value, dict) for value in (before, after, expectation)):
            raise ValueError('snapshots and expectation must be objects')
        if expectation.get('approved') is not True:
            raise ValueError('expectation has not been approved')
        if type(expectation.get('level_delta')) is not int or expectation['level_delta'] != 1:
            raise ValueError('only an explicitly approved single upgrade is supported')
        for field in ('requirement_id', 'case_id', 'source_ref', 'run_id', 'attempt_id'):
            _known_id(result['trace'].get(field), 'trace.' + field)
        target = _known_id(expectation.get('target_id'), 'expectation.target_id')
        window = _known_id(expectation.get('window_id'), 'expectation.window_id')
        build = _known_id(before.get('build_id'), 'before.build_id')
        device = _known_id(before.get('device_id'), 'before.device_id')
        if after.get('build_id') != build or after.get('device_id') != device:
            raise ValueError('observations must identify the same known build and device')
        for name, snapshot in (('before', before), ('after', after)):
            if snapshot.get('target_id') != target or snapshot.get('window_id') != window:
                raise ValueError(f'{name} target/window does not match the approved context; no terrain/device mapping is inferred')
            _known_id(snapshot.get('observation_id'), name + '.observation_id')
            if snapshot.get('observer') not in ('manual', 'bridge') or snapshot.get('visual_confirmed') is not True:
                raise ValueError(f'{name} observation lacks confirmed manual/bridge provenance')
            evidence = snapshot.get('screenshot')
            if not isinstance(evidence, dict):
                raise ValueError(f'{name} screenshot evidence is missing')
            evidence_id = _known_id(evidence.get('evidence_id'), name + '.screenshot.evidence_id')
            actual = evidence_for_png(evidence['path'])
            if any(evidence.get(field) != actual[field] for field in ('sha256', 'bytes', 'width', 'height')):
                raise ValueError(f'{name} screenshot evidence has changed')
            actual['evidence_id'] = evidence_id
            result['evidence'][name] = actual
    except (ValueError, KeyError, OSError, TypeError) as error:
        reason = str(error)
        result['reasons'] = [reason]
        for assertion in result['assertions']:
            assertion['reasons'] = [reason]
        return result

    result['assertions'][0] = _assertion('visual', 'PASS', expected='confirmed manual/bridge evidence',
        actual={name: {'observer': snapshot['observer'], 'visual_confirmed': snapshot['visual_confirmed'],
                      'observation_id': snapshot['observation_id'], 'evidence_id': snapshot['screenshot']['evidence_id']}
                for name, snapshot in (('before', before), ('after', after))},
        reasons=['Provenance is recorded; manual confirmation is not machine image recognition.'])
    try:
        for name, snapshot in (('before', before), ('after', after)):
            if type(snapshot.get('level')) is not int or snapshot['level'] < 0:
                raise ValueError(f'{name} level is unknown')
        expected_level = before['level'] + expectation['level_delta']
        status = 'PASS' if after['level'] == expected_level else 'FAIL'
        result['assertions'][1] = _assertion('level', status, expected_level, after['level'],
            [] if status == 'PASS' else ['level does not match the approved single-upgrade expectation'])
    except (ValueError, KeyError, TypeError) as error:
        result['assertions'][1] = _assertion('level', reasons=[str(error)])
    try:
        before_coins = _amount(before.get('coins'), 'before.coins')
        after_coins = _amount(after.get('coins'), 'after.coins')
        cost = _amount(expectation.get('cost'), 'cost')
        other_delta = _amount(expectation.get('non_upgrade_delta'), 'non_upgrade_delta')
        if cost < 0:
            raise ValueError('approved upgrade cost cannot be negative')
        amounts = (before_coins, cost, other_delta, after_coins)
        precision = max(max(value.adjusted() + 1, 1) for value in amounts) + max(max(-value.as_tuple().exponent, 0) for value in amounts) + 4
        with localcontext() as context:
            context.prec = precision
            expected_coins = before_coins - cost + other_delta
        status = 'PASS' if after_coins == expected_coins else 'FAIL'
        result['assertions'][2] = _assertion('coins', status, str(expected_coins), str(after_coins),
            [] if status == 'PASS' else ['coins do not match the reviewed cost and non-upgrade transaction delta'])
    except (ValueError, KeyError, TypeError, DecimalException) as error:
        result['assertions'][2] = _assertion('coins', reasons=[str(error)])
    statuses = {item['status'] for item in result['assertions']}
    result['status'] = 'FAIL' if 'FAIL' in statuses else 'BLOCKED' if 'BLOCKED' in statuses else 'PASS'
    result['category'] = {'FAIL': 'BUSINESS_FAILURE', 'BLOCKED': 'INSUFFICIENT_EVIDENCE', 'PASS': 'CONFIRMED'}[result['status']]
    result['reasons'] = [reason for item in result['assertions'] if item['status'] != 'PASS' for reason in item['reasons']]
    return result
