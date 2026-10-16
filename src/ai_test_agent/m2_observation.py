"""Pure, bounded Android startup observations; no device access or business guesses."""

from datetime import datetime
import re


_THREADTIME = re.compile(
    r'^\s*(?P<stamp>\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+'
    r'(?P<pid>\d+)\s+\d+\s+[VDIWEFAS]\s+(?P<tag>[^:]+):\s?(?P<message>.*)$'
)
_PROCESS = re.compile(r'\bProcess:\s*([\w.:]+)\s*,\s*PID:\s*(\d+)')
_NATIVE_PID = re.compile(r'\bpid\s+(\d+)(?:\s+\(([^)]+)\))?', re.IGNORECASE)
_SERVER = re.compile(r'Hot InitRequestUrl Server Type\s+([\w.-]+)')
_VERSIONS = re.compile(r'localVersion:\s*(\d+)\s+remoteVersion:\s*(\d+)')
_JAVA_FATAL = re.compile(r'^\s*FATAL EXCEPTION(?::|\s*$)')
_NATIVE_FATAL = re.compile(r'^\s*Fatal signal\s+\d+\b')
_YEAR_SECONDS = 366 * 24 * 60 * 60
_WINDOW_SECONDS = 24 * 60 * 60


def _timestamp(value):
    try:
        return datetime.strptime('2000-' + value, '%Y-%m-%d %H:%M:%S.%f')
    except (TypeError, ValueError):
        return None


def _elapsed(stamp, start):
    """Resolve missing years by the nearest annual occurrence, including Dec/Jan."""
    seconds = (stamp - start).total_seconds()
    if seconds < -_YEAR_SECONDS / 2:
        seconds += _YEAR_SECONDS
    elif seconds > _YEAR_SECONDS / 2:
        seconds -= _YEAR_SECONDS
    return seconds


def _target_process(name, package):
    return name == package or name.startswith(package + ':')


def _valid_pid(value):
    return isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0


def _diagnostic(kind, *records):
    # Deliberately retain references, never payloads from an all-system capture.
    return {'kind': kind, 'line_numbers': [record['line'] for record in records]}


def _records(logs, start, diagnostics):
    records = []
    for number, raw in enumerate(logs.splitlines(), 1):
        match = _THREADTIME.match(raw)
        stamp = _timestamp(match['stamp']) if match else None
        if not stamp:
            if 'FATAL EXCEPTION' in raw or 'Fatal signal' in raw or 'ANR in ' in raw:
                diagnostics.append({'kind': 'UNTIMED_EXCEPTION', 'line_numbers': [number]})
            continue
        seconds = _elapsed(stamp, start)
        if 0 <= seconds <= _WINDOW_SECONDS:
            records.append({'line': number, 'pid': match['pid'], 'tag': match['tag'].strip(),
                            'message': match['message'], 'elapsed': seconds, 'raw': raw})
    return records


def _anr_process(record):
    message = record['message']
    match = re.search(r'\bANR\s+in\s+([\w.:]+)', message)
    if match:
        return match[1]
    if record['tag'] == 'am_anr':
        match = re.search(r'\[\s*\d+\s*,\s*\d+\s*,\s*([\w.:]+)\s*,', message)
        if match:
            return match[1]
    if record['tag'] == 'ActivityManager' and re.search(r'\bANR\b', message):
        match = re.search(r'\b(?:package|process|app)\s*[:=]\s*([\w.:]+)', message, re.IGNORECASE)
        if match:
            return match[1]
    return None


def _exceptions(records, package, pids, diagnostics):
    failures = []
    unknown = False
    for index, record in enumerate(records):
        message = record['message']
        if _JAVA_FATAL.search(message):
            identity = None
            # Java's Process line is emitted just after FATAL EXCEPTION by the
            # same process. Do not combine independently interleaved crashes.
            for following in records[index + 1:index + 5]:
                if following['line'] - record['line'] > 5:
                    break
                if (following['pid'] != record['pid'] or
                        not 0 <= following['elapsed'] - record['elapsed'] <= 2):
                    continue
                process = _PROCESS.search(following['message'])
                if process and process[2] == record['pid']:
                    identity = (process[1], following)
                    break
                if _JAVA_FATAL.search(following['message']):
                    break
            if identity:
                name, following = identity
                if _target_process(name, package):
                    failures.append('JAVA_CRASH')
                    diagnostics.append(_diagnostic('JAVA_CRASH', record, following))
                else:
                    diagnostics.append(_diagnostic('IGNORED_OTHER_PACKAGE_EXCEPTION', record, following))
            else:
                unknown = True
                diagnostics.append(_diagnostic('UNATTRIBUTED_JAVA_FATAL', record))
        elif _NATIVE_FATAL.search(message):
            process = _NATIVE_PID.search(message)
            native_pid = process[1] if process else record['pid']
            name = process[2] if process else None
            tombstone = re.search(r'>>>\s*([\w.:]+)\s*<<<', message)
            if tombstone:
                name = tombstone[1]
            # A complete package name is stronger attribution than a recycled
            # PID. Thread names and truncated names are not package identities.
            named_package = name if name and re.fullmatch(r'[\w]+(?:\.[\w]+)+(?:[:][\w.]+)?', name) else None
            if named_package and not _target_process(named_package, package):
                diagnostics.append(_diagnostic('IGNORED_OTHER_PACKAGE_EXCEPTION', record))
            elif (named_package and _target_process(named_package, package)) or native_pid in pids:
                failures.append('NATIVE_CRASH')
                diagnostics.append(_diagnostic('NATIVE_CRASH', record))
            else:
                unknown = True
                diagnostics.append(_diagnostic('UNATTRIBUTED_NATIVE_FATAL', record))
        else:
            name = _anr_process(record)
            if name:
                if _target_process(name, package):
                    failures.append('ANR')
                    diagnostics.append(_diagnostic('ANR', record))
                else:
                    diagnostics.append(_diagnostic('IGNORED_OTHER_PACKAGE_EXCEPTION', record))
            elif record['tag'] == 'am_anr' or (record['tag'] == 'ActivityManager' and
                                             re.search(r'\bANR\b', message)):
                unknown = True
                diagnostics.append(_diagnostic('UNATTRIBUTED_ANR', record))
    return failures, unknown


def assess_observation(snapshot, readiness, *, package, observed_pids, since) -> dict:
    """Assess one snapshot without persisting state or interpreting game behavior.

    Readiness requires approval, a source reference, at least one configured
    signal and every configured literal log/UI condition. Logs must have Android
    threadtime timestamps in the first 24 hours since the device start marker;
    readiness/runtime logs belong to the current PID, while crashes may belong
    to any PID observed in this round or an explicitly named target process.
    Diagnostic line numbers refer to the original, unfiltered ``snapshot.logs``;
    log payloads are never copied into the returned value.
    """
    runtime = {'server': None, 'resource_versions': None}
    diagnostics = []

    def outcome(status, category, reasons):
        return {'status': status, 'category': category, 'reasons': reasons,
                'runtime': runtime, 'diagnostics': diagnostics}

    if not isinstance(snapshot, dict) or not isinstance(readiness, dict):
        return outcome('BLOCKED', 'INVALID_OBSERVATION', ['snapshot and readiness must be objects'])
    start = _timestamp(since)
    if start is None:
        return outcome('BLOCKED', 'INVALID_TIME_WINDOW', ['since must be a device threadtime timestamp'])
    if not isinstance(package, str) or not package.strip():
        return outcome('BLOCKED', 'INVALID_PACKAGE', ['an explicit target package is required'])
    logs = snapshot.get('logs', '')
    if not isinstance(logs, str):
        return outcome('BLOCKED', 'INVALID_OBSERVATION', ['snapshot.logs must be text'])
    records = _records(logs, start, diagnostics)
    pid = snapshot.get('pid', '')
    pids = {str(item) for item in (observed_pids or []) if _valid_pid(str(item))}
    if _valid_pid(pid):
        pids.add(pid)
    current = [record for record in records if _valid_pid(pid) and record['pid'] == pid]
    for record in current:
        server = _SERVER.search(record['message'])
        versions = _VERSIONS.search(record['message'])
        if server:
            runtime['server'] = server[1]
        if versions:
            runtime['resource_versions'] = [versions[1], versions[2]]
    failures, unknown = _exceptions(records, package, pids, diagnostics)
    if failures:
        return outcome('FAIL', failures[0], ['current-window fatal or ANR evidence is attributed to the target package'])
    if unknown:
        return outcome('BLOCKED', 'UNATTRIBUTED_EXCEPTION', ['current-window exception cannot be attributed; inspect referenced raw log lines'])
    if readiness.get('approved') is not True:
        return outcome('BLOCKED', 'READINESS_UNAPPROVED', ['startup readiness signals require explicit approval'])
    if not isinstance(readiness.get('source_ref'), str) or not readiness['source_ref'].strip():
        return outcome('BLOCKED', 'READINESS_SOURCE_MISSING', ['approved readiness requires a source reference'])
    signals = {}
    for field in ('log_markers', 'ui_texts', 'ui_classes'):
        items = readiness.get(field, [])
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            return outcome('BLOCKED', 'INVALID_READINESS_SIGNAL', ['readiness conditions must be lists of nonempty literals'])
        signals[field] = items
    if not any(signals.values()):
        return outcome('BLOCKED', 'READINESS_SIGNAL_MISSING', ['at least one approved log or UI readiness signal is required'])
    if not pid:
        return outcome('WAIT', 'PROCESS_ABSENT', ['waiting for a target process'])
    if not _valid_pid(pid):
        return outcome('BLOCKED', 'INVALID_PID', ['current target PID must be a positive integer string'])
    if snapshot.get('foreground') != package:
        return outcome('BLOCKED', 'FOREGROUND_MISMATCH', ['the target package is not in the foreground'])
    if snapshot.get('screenshot_valid') is not True:
        return outcome('BLOCKED', 'INVALID_SCREENSHOT', ['a valid screenshot is required for readiness'])
    nodes = snapshot.get('ui_nodes', [])
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        return outcome('BLOCKED', 'INVALID_UI_OBSERVATION', ['ui_nodes must be a list of node objects'])
    target_nodes = [node for node in nodes if node.get('package', package) == package]
    missing = []
    for marker in signals['log_markers']:
        matches = [record for record in current if marker in record['raw']]
        if matches:
            diagnostics.append(_diagnostic('READINESS_LOG_MATCH', *matches))
        else:
            missing.append('required log marker not observed for the current PID')
    for text in signals['ui_texts']:
        if not any(text == node.get('text') or text == node.get('content-desc') or
                   text == node.get('content_desc') for node in target_nodes):
            missing.append('required UI text or content description not observed')
    for name in signals['ui_classes']:
        if not any(name == node.get('class') for node in target_nodes):
            missing.append('required UI class not observed')
    if missing:
        return outcome('WAIT', 'READINESS_PENDING', missing)
    return outcome('READY', 'READINESS_CONFIRMED', ['all approved startup readiness signals are present'])


__all__ = ['assess_observation']
