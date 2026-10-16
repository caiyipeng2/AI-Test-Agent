"""Associate reviewed, read-only observations, ledger entries and real PNG evidence.

PASS describes the supplied recording contract, never a live business execution.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException, localcontext
from pathlib import Path
import re

from .bridge_v2 import ObservationStream
from .contracts import evidence_for_png
from .upgrade import assess_upgrade


_SOURCE = {'build_id', 'resource_version', 'dll_version', 'server'}
_TRACE = {'requirement_id', 'case_id', 'source_ref', 'run_id', 'attempt_id'}
_CONTRACT = {'approved', 'reviewer', 'reviewed_at', 'source_ref', 'cost_source_ref',
             'provider', 'runtime_source', 'device_alias', 'pid', 'target_id',
             'window_id', 'currency_id', 'operation_id', 'expected_cost', 'trace'}
_CAPTURE = {'device_alias', 'pid', 'log_start', 'log_end', 'session_id', 'seq',
            'observer', 'visual_confirmed', 'visual_review_ref', 'screenshot'}
_SCREENSHOT = {'evidence_id', 'path', 'bytes', 'sha256', 'width', 'height'}
_TIME = r'[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}'
_PREFIX = re.compile(r'^(?P<time>' + _TIME + r')[ \t]+(?P<pid>[1-9][0-9]*)[ \t]+'
                     r'(?P<tid>[1-9][0-9]*)[ \t]+[VDIWEFAS][ \t]+Unity[ \t]*:[ \t]*'
                     r'AI_TEST_OBSERVATION_JSON:')
_MONEY = re.compile(r'[+]?(?:\d+(?:\.\d*)?|\.\d+)\Z', re.ASCII)


def _keys(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f'{label} must contain exactly {", ".join(sorted(fields))}')


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f'{label} requires a known nonempty string of at most 512 characters')
    return value


def _integer(value, label, maximum=(1 << 63) - 1):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f'{label} must be a positive bounded integer')
    return value


def _expected_cost(value):
    if not isinstance(value, str) or len(value) > 1024 or not _MONEY.fullmatch(value):
        raise ValueError('contract.expected_cost requires a nonnegative exact decimal string')
    return Decimal(value)


def _contract(value, mode):
    _keys(value, _CONTRACT, 'contract')
    if value['approved'] is not True:
        raise ValueError('contract must be explicitly approved by its reviewer')
    for field in _CONTRACT - {'approved', 'pid', 'runtime_source', 'trace', 'expected_cost'}:
        _text(value[field], 'contract.' + field)
    _integer(value['pid'], 'contract.pid', (1 << 31) - 1)
    reviewed = datetime.fromisoformat(value['reviewed_at'])
    if reviewed.tzinfo is None or reviewed.utcoffset() is None or 'T' not in value['reviewed_at']:
        raise ValueError('contract.reviewed_at requires an ISO timestamp with timezone')
    if mode == 'synthetic' and 'synthetic' not in value['reviewer'].lower():
        raise ValueError('synthetic evidence reviewer must be explicitly identified as synthetic')
    _keys(value['runtime_source'], _SOURCE, 'contract.runtime_source')
    for field in _SOURCE:
        _text(value['runtime_source'][field], 'contract.runtime_source.' + field)
    _keys(value['trace'], _TRACE, 'contract.trace')
    for field in _TRACE:
        _text(value['trace'][field], 'contract.trace.' + field)
    if value['trace']['source_ref'] != value['source_ref']:
        raise ValueError('contract source_ref must match its trace source_ref')
    # Reference text records the reviewer's claim. Its contents and independence
    # require review at real-provider integration; never infer cost from target.cost.
    return _expected_cost(value['expected_cost'])


def _time(value, label, year=2000):
    if not isinstance(value, str) or not re.fullmatch(_TIME, value, flags=re.ASCII):
        raise ValueError(f'{label} requires MM-DD HH:MM:SS.mmm device time')
    return datetime.strptime(f'{year}-{value}', '%Y-%m-%d %H:%M:%S.%f')


def _capture_time(capture, log_time, label, floor=None):
    start = _time(capture['log_start'], label + '.log_start')
    if floor is not None:
        start = start.replace(year=floor.year)
        if start <= floor:
            start = start.replace(year=start.year + 1)
    end = _time(capture['log_end'], label + '.log_end', start.year)
    if end < start:
        end = end.replace(year=end.year + 1)
    if end - start > timedelta(seconds=10):
        raise ValueError(f'{label} capture bracket exceeds 10 seconds or is reversed')
    stamp = _time(log_time, label + '.line time', start.year)
    if stamp < start:
        stamp = stamp.replace(year=stamp.year + 1)
    if not start <= stamp <= end:
        raise ValueError(f'{label} log time is outside its capture bracket')
    return start, end


def _screenshot(evidence, root, label):
    _keys(evidence, _SCREENSHOT, label)
    evidence_id = _text(evidence['evidence_id'], label + '.evidence_id')
    path_text = _text(evidence['path'], label + '.path')
    for field in ('bytes', 'width', 'height'):
        _integer(evidence[field], label + '.' + field)
    if not isinstance(evidence['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', evidence['sha256']):
        raise ValueError(f'{label}.sha256 must be a lowercase SHA-256 digest')
    path = (root / path_text).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'{label} PNG must be a file inside evidence_root')
    actual = evidence_for_png(path)
    if any(evidence[field] != actual[field] for field in ('bytes', 'width', 'height', 'sha256')):
        raise ValueError(f'{label} PNG bytes/hash/dimensions do not match the real file')
    actual['evidence_id'] = evidence_id
    return actual


def _snapshot(value, label, contract, stream, root, floor=None):
    _keys(value, {'line', 'capture'}, label)
    text = value['line']
    if not isinstance(text, str):
        raise ValueError(f'{label}.line must be a prefixed observation log line')
    prefix = _PREFIX.match(text)
    if prefix is None or int(prefix['pid']) != contract['pid']:
        raise ValueError(f'{label}.line requires exact contract PID and device-time Unity log prefix')
    if int(prefix['tid']) > (1 << 31) - 1:
        raise ValueError(f'{label}.line thread ID is out of bounds')
    capture = value['capture']
    _keys(capture, _CAPTURE, label + '.capture')
    for field in ('device_alias', 'session_id', 'observer', 'visual_review_ref'):
        _text(capture[field], label + '.capture.' + field)
    _integer(capture['pid'], label + '.capture.pid', (1 << 31) - 1)
    _integer(capture['seq'], label + '.capture.seq')
    if capture['pid'] != contract['pid'] or capture['device_alias'] != contract['device_alias']:
        raise ValueError(f'{label} capture PID/device do not match the contract')
    if capture['observer'] not in ('manual', 'bridge') or capture['visual_confirmed'] is not True:
        raise ValueError(f'{label} requires confirmed manual/bridge visual provenance')
    observation = stream.parse_line(text)
    if capture['session_id'] != observation['session_id'] or capture['seq'] != observation['seq']:
        raise ValueError(f'{label} capture session/sequence do not match its observation')
    for field in ('source', 'target', 'wallet', 'ledger'):
        if observation[field]['available'] is not True:
            raise ValueError(f'{label}.{field} is unavailable')
    for field in ('target_id', 'window_id'):
        if observation['target'][field] != contract[field]:
            raise ValueError(f'{label} target/window does not match the reviewed contract')
    if observation['wallet']['currency_id'] != contract['currency_id']:
        raise ValueError(f'{label} wallet currency does not match the reviewed contract')
    bracket = _capture_time(capture, prefix['time'], label, floor)
    screenshot = _screenshot(capture['screenshot'], root, label + '.capture.screenshot')
    snapshot = {
        'observation_id': observation['session_id'] + ':' + str(observation['seq']),
        'device_id': contract['device_alias'], 'build_id': observation['source']['build_id'],
        'target_id': observation['target']['target_id'], 'window_id': observation['target']['window_id'],
        'observer': capture['observer'], 'visual_confirmed': capture['visual_confirmed'],
        'screenshot': screenshot, 'level': observation['target']['level'],
        'coins': observation['wallet']['balance'],
    }
    return observation, snapshot, bracket


def _precision(values, event_count):
    return (max(max(v.adjusted() + 1, 1) for v in values)
            + max(max(-v.as_tuple().exponent, 0) for v in values)
            + len(str(event_count + 1)) + 4)


def _historical_balances(events, wallet_balance, currency):
    previous = None
    for event in events:
        if event['currency_id'] != currency:
            continue
        balance = Decimal(event['balance_after'])
        if previous is not None and previous + Decimal(event['delta']) != balance:
            raise ValueError('historical target-currency ledger balances are inconsistent')
        previous = balance
    if previous is not None and previous != wallet_balance:
        raise ValueError('historical ledger final balance does not match its wallet snapshot')


def _ledger(before, after, contract, cost):
    left, right = before['ledger'], after['ledger']
    if not right['complete']:
        raise ValueError('after ledger is not complete')
    first, last = left['last_seq'] + 1, right['last_seq']
    if last < left['last_seq'] or right['first_seq'] > first:
        raise ValueError('after ledger does not cover (before.last_seq, after.last_seq]')
    history = {event['seq']: event for event in left['events']}
    transactions = {event['transaction_id']: event['seq'] for event in left['events']}
    for event in right['events']:
        old = history.get(event['seq'])
        if old is not None and old != event:
            raise ValueError('overlapping ledger history changed')
        previous_seq = transactions.get(event['transaction_id'])
        if previous_seq is not None and previous_seq != event['seq']:
            raise ValueError('transaction_id reused across ledger sequence numbers')
    events = [event for event in right['events'] if event['seq'] >= first]
    if len(events) != last - first + 1:
        raise ValueError('after ledger has missing events in the required interval')
    upgrades = [event for event in events if event['kind'] == 'upgrade']
    if len(upgrades) != 1:
        raise ValueError('exactly one explicitly attributed upgrade event is required')
    upgrade = upgrades[0]
    if any(upgrade[field] != contract[field] for field in ('operation_id', 'target_id', 'currency_id')):
        raise ValueError('upgrade operation/target/currency does not match the reviewed contract')
    amounts = [Decimal(before['wallet']['balance']), Decimal(after['wallet']['balance']), cost]
    amounts.extend(Decimal(event[field]) for event in left['events'] + right['events']
                   for field in ('delta', 'balance_after'))
    with localcontext() as context:
        context.prec = _precision(amounts, len(left['events']) + len(right['events']))
        _historical_balances(left['events'], amounts[0], contract['currency_id'])
        _historical_balances(right['events'], amounts[1], contract['currency_id'])
        balance, non_upgrade = amounts[0], Decimal(0)
        for event in events:
            if event['currency_id'] != contract['currency_id']:
                continue
            delta = Decimal(event['delta'])
            balance += delta
            if balance != Decimal(event['balance_after']):
                raise ValueError('ledger balance_after is inconsistent with the preceding target-currency balance')
            if event['kind'] == 'external':
                non_upgrade += delta
        if balance != Decimal(after['wallet']['balance']):
            raise ValueError('ledger final balance does not match after wallet balance')
        actual_cost = -Decimal(upgrade['delta'])
    return {'first_seq': first, 'last_seq': last, 'events': deepcopy(events),
            'upgrade_event': deepcopy(upgrade), 'non_upgrade_delta': str(non_upgrade),
            'actual_cost': str(actual_cost)}


def _blocked(result, reason):
    result['status'], result['category'] = 'BLOCKED', 'INSUFFICIENT_EVIDENCE'
    result['reasons'] = [reason]
    result['assertions'] = [
        {'name': name, 'status': 'BLOCKED', 'passed': False, 'expected': None,
         'actual': None, 'reasons': [reason]}
        for name in ('visual', 'level', 'coins', 'cost')
    ]
    return result


def assess_recording(recording, *, evidence_root):
    """Return PASS/FAIL/BLOCKED without device access or mutating the recording."""
    result = {'status': 'BLOCKED', 'category': 'INSUFFICIENT_EVIDENCE',
              'scope': 'readonly_upgrade_evidence', 'business_status': 'NOT_EVALUATED',
              'execution': 'device_free', 'evidence_mode': None,
              'assertions': [], 'reasons': [], 'evidence': {}, 'observations': {},
              'provenance': {}, 'trace': {}, 'ledger': {}}
    try:
        _keys(recording, {'schema_version', 'evidence_mode', 'contract', 'before', 'after'}, 'recording')
        result['evidence_mode'] = recording['evidence_mode']
        if type(recording['schema_version']) is not int or recording['schema_version'] != 1:
            raise ValueError('recording.schema_version must be integer 1')
        if recording['evidence_mode'] not in ('synthetic', 'recorded'):
            raise ValueError('recording.evidence_mode must be synthetic or recorded')
        contract = recording['contract']
        cost = _contract(contract, recording['evidence_mode'])
        root = Path(evidence_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('evidence_root must be an existing directory')
        stream = ObservationStream(expected_source=contract['runtime_source'], expected_provider=contract['provider'])
        before, before_snapshot, before_bracket = _snapshot(recording['before'], 'before', contract, stream, root)
        after, after_snapshot, after_bracket = _snapshot(recording['after'], 'after', contract, stream, root, before_bracket[1])
        if after_bracket[1] - before_bracket[0] > timedelta(seconds=120):
            raise ValueError('recording capture pair exceeds 120 seconds or is reversed/overlapping')
        before_png, after_png = before_snapshot['screenshot'], after_snapshot['screenshot']
        if (before_png['evidence_id'] == after_png['evidence_id']
                or Path(before_png['path']).samefile(after_png['path'])):
            raise ValueError('before/after PNG evidence must use distinct evidence IDs and distinct files')
        ledger = _ledger(before, after, contract, cost)
        expectation = {'approved': True, 'level_delta': 1, 'target_id': contract['target_id'],
                       'window_id': contract['window_id'], 'cost': contract['expected_cost'],
                       'non_upgrade_delta': ledger['non_upgrade_delta'], 'trace': contract['trace']}
        assessed = assess_upgrade(before_snapshot, after_snapshot, expectation)
        result.update(assessed)
        if assessed['status'] == 'BLOCKED':
            return _blocked(result, '; '.join(assessed['reasons']))
        display_cost = Decimal(before['target']['cost'])
        cost_ok = display_cost == cost and Decimal(ledger['actual_cost']) == cost
        result['assertions'].append({
            'name': 'cost', 'status': 'PASS' if cost_ok else 'FAIL', 'passed': cost_ok,
            'expected': contract['expected_cost'],
            'actual': {'displayed_cost': before['target']['cost'], 'debit': ledger['actual_cost']},
            'reasons': [] if cost_ok else ['displayed cost or actual debit differs from the independently reviewed cost'],
        })
        if not cost_ok:
            result['status'], result['category'] = 'FAIL', 'BUSINESS_FAILURE'
        result['reasons'] = [reason for item in result['assertions'] if item['status'] != 'PASS' for reason in item['reasons']]
        result['ledger'] = ledger
        result['provenance'] = deepcopy(contract)
        result['observations'] = {'before': deepcopy(before), 'after': deepcopy(after)}
        return result
    except (ValueError, TypeError, KeyError, OSError, OverflowError, DecimalException) as error:
        return _blocked(result, str(error))
