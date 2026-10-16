"""Strict schema2 parsing for opt-in, read-only state and ledger observations.

Amounts remain exact source strings. Structural validity does not establish that
the provider is truthful, or that an actual game operation took place.
"""

from copy import deepcopy
from decimal import Decimal
import json
import re

MARKER = 'AI_TEST_OBSERVATION_JSON:'
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 4096
MAX_TEXT = 512
MAX_AMOUNT = 1024
MAX_INT64 = 2**63 - 1
MAX_FRAME = 2**31 - 1
_ROOT_KEYS = {'schema', 'session_id', 'seq', 'frame', 'provider', 'source',
              'target', 'wallet', 'ledger'}
_RUNTIME_KEYS = {'build_id', 'resource_version', 'dll_version', 'server'}
_SOURCE_KEYS = _RUNTIME_KEYS | {'available'}
_TARGET_KEYS = {'available', 'target_id', 'window_id', 'level', 'cost', 'ready'}
_WALLET_KEYS = {'available', 'currency_id', 'balance'}
_LEDGER_KEYS = {'available', 'complete', 'first_seq', 'last_seq', 'events'}
_EVENT_KEYS = {'seq', 'transaction_id', 'operation_id', 'kind', 'target_id',
               'currency_id', 'delta', 'balance_after'}
_DECIMAL = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z')


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'duplicate JSON field: {key}')
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError(f'nonfinite JSON value: {value}')


def _keys(value, keys, name):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f'{name} must contain exactly the specified fields')


def _integer(value, name, minimum=0, maximum=MAX_INT64):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')


def _boolean(value, name):
    if type(value) is not bool:
        raise ValueError(f'{name} must be explicit true or false')


def _text(value, name, allow_empty=False):
    if (not isinstance(value, str) or len(value) > MAX_TEXT
            or (not value.strip() and not (allow_empty and value == ''))):
        raise ValueError(f'{name} must be bounded text with the required availability')


def _amount(value, name, signed=False):
    if (not isinstance(value, str) or not 1 <= len(value) <= MAX_AMOUNT
            or _DECIMAL.fullmatch(value) is None):
        raise ValueError(f'{name} must be a bounded exact decimal string')
    # Construction and comparison are exact and do not use context precision.
    if not signed and Decimal(value) < 0:
        raise ValueError(f'{name} cannot be negative')


def _source(value):
    _keys(value, _SOURCE_KEYS, 'source')
    _boolean(value['available'], 'source.available')
    for field in _RUNTIME_KEYS:
        if value['available']:
            _text(value[field], 'source.' + field)
        elif value[field] != '':
            raise ValueError('unavailable source must contain empty runtime identity')


def _target(value):
    _keys(value, _TARGET_KEYS, 'target')
    _boolean(value['available'], 'target.available')
    _boolean(value['ready'], 'target.ready')
    if value['available']:
        for field in ('target_id', 'window_id'):
            _text(value[field], 'target.' + field)
        _integer(value['level'], 'target.level')
        _amount(value['cost'], 'target.cost')
    elif (value['target_id'] != '' or value['window_id'] != '' or value['cost'] != ''
          or type(value['level']) is not int or value['level'] != -1 or value['ready']):
        raise ValueError('unavailable target must contain explicit unknown business markers')


def _wallet(value):
    _keys(value, _WALLET_KEYS, 'wallet')
    _boolean(value['available'], 'wallet.available')
    if value['available']:
        _text(value['currency_id'], 'wallet.currency_id')
        _amount(value['balance'], 'wallet.balance')
    elif value['currency_id'] != '' or value['balance'] != '':
        raise ValueError('unavailable wallet must not supply a currency or invented balance')


def _event(value):
    _keys(value, _EVENT_KEYS, 'event')
    _integer(value['seq'], 'event.seq', minimum=1)
    for field in ('transaction_id', 'currency_id'):
        _text(value[field], 'event.' + field)
    if value['kind'] not in ('upgrade', 'external'):
        raise ValueError('event.kind must be upgrade or external')
    for field in ('operation_id', 'target_id'):
        _text(value[field], 'event.' + field, allow_empty=value['kind'] == 'external')
    _amount(value['delta'], 'event.delta', signed=True)
    _amount(value['balance_after'], 'event.balance_after')


def _ledger(value):
    _keys(value, _LEDGER_KEYS, 'ledger')
    _boolean(value['available'], 'ledger.available')
    _boolean(value['complete'], 'ledger.complete')
    _integer(value['first_seq'], 'ledger.first_seq', minimum=1)
    _integer(value['last_seq'], 'ledger.last_seq')
    events = value['events']
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError('ledger.events must be a bounded list')
    if not value['available']:
        if (value['complete'] or value['first_seq'] != 1
                or value['last_seq'] != 0 or events):
            raise ValueError('unavailable ledger must contain explicit unknown markers')
    if value['last_seq'] - value['first_seq'] + 1 != len(events):
        raise ValueError('ledger events must cover the contiguous declared window')
    transactions = set()
    for offset, event in enumerate(events):
        _event(event)
        if event['seq'] != value['first_seq'] + offset:
            raise ValueError('ledger event sequence is not contiguous')
        transaction = event['transaction_id']
        if transaction in transactions:
            raise ValueError('ledger transaction_id must be unique')
        transactions.add(transaction)


def parse_observation_line(line: str) -> dict:
    """Parse one bounded log line, preserving exact decimal text; raise ValueError."""
    if not isinstance(line, str):
        raise ValueError('observation log must be text')
    try:
        if len(line) > MAX_LINE_BYTES or len(line.encode('utf-8')) > MAX_LINE_BYTES:
            raise ValueError('observation log exceeds 4 MiB')
    except UnicodeError as error:
        raise ValueError('observation log is not valid UTF-8 text') from error
    if line.count(MARKER) != 1:
        raise ValueError('exactly one observation marker is required')
    try:
        value = json.loads(line.split(MARKER, 1)[1].strip(),
                           object_pairs_hook=_object, parse_constant=_nonfinite)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError('observation JSON is truncated or malformed') from error
    _keys(value, _ROOT_KEYS, 'observation')
    if type(value['schema']) is not int or value['schema'] != 2:
        raise ValueError('unsupported observation schema')
    _integer(value['seq'], 'seq', minimum=1)
    _integer(value['frame'], 'frame', maximum=MAX_FRAME)
    for field in ('session_id', 'provider'):
        _text(value[field], field)
    _source(value['source'])
    _target(value['target'])
    _wallet(value['wallet'])
    _ledger(value['ledger'])
    return value


class ObservationStream:
    """Bind one session and runtime; failed lines never change accepted history.

    ``expected_source`` accepts the four runtime identity fields, or the complete
    source object including availability. Overlap events are compared exactly;
    transaction IDs cannot be reused at another sequence in this stream.
    """

    def __init__(self, expected_source=None, expected_provider=None):
        if expected_source is not None:
            expected_source = deepcopy(expected_source)
            if isinstance(expected_source, dict) and set(expected_source) == _RUNTIME_KEYS:
                expected_source['available'] = True
            _source(expected_source)
        if expected_provider is not None:
            _text(expected_provider, 'expected_provider')
        self._expected_source = expected_source
        self._expected_provider = expected_provider
        self._previous = None
        self._ledger_window = None
        self._events = {}
        self._transactions = {}

    def parse_line(self, line):
        value = parse_observation_line(line)
        if self._expected_source is not None and value['source'] != self._expected_source:
            raise ValueError('observation does not match the bound runtime source')
        if self._expected_provider is not None and value['provider'] != self._expected_provider:
            raise ValueError('observation does not match the bound provider')
        if self._previous is not None:
            old = self._previous
            if value['session_id'] != old['session_id']:
                raise ValueError('observation stream accepts only one session')
            if value['provider'] != old['provider'] or value['source'] != old['source']:
                raise ValueError('provider or runtime identity changed inside the stream')
            if value['seq'] <= old['seq'] or value['frame'] < old['frame']:
                raise ValueError('duplicate, backward or stale observation sequence/frame')
        ledger = value['ledger']
        if ledger['available']:
            if self._ledger_window is not None:
                first, last = self._ledger_window
                if ledger['first_seq'] < first or ledger['last_seq'] < last:
                    raise ValueError('ledger event window moved backward')
            for event in ledger['events']:
                sequence, transaction = event['seq'], event['transaction_id']
                if sequence in self._events and event != self._events[sequence]:
                    raise ValueError('overlapping ledger event content changed')
                if transaction in self._transactions and self._transactions[transaction] != sequence:
                    raise ValueError('ledger transaction reused at another event sequence')
        # Commit only after every check passes; keep state separate from caller data.
        self._previous = deepcopy(value)
        if ledger['available']:
            self._ledger_window = (ledger['first_seq'], ledger['last_seq'])
            for event in ledger['events']:
                self._events[event['seq']] = deepcopy(event)
                self._transactions[event['transaction_id']] = event['seq']
        return value
