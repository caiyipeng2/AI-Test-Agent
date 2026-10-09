"""Explicit device and environment selection for the first test loop."""

import json
import math
import re
from pathlib import Path


def validate_config(data):
    if not isinstance(data, dict):
        return ['configuration must be an object']
    errors = []
    for key in ('adb_path', 'serial', 'device_alias', 'package', 'environment_source', 'artifact_root'):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(f'{key} must be an explicit nonempty string')
    if data.get('environment') != 'test':
        errors.append('only the explicitly selected test environment is enabled')
    if not isinstance(data.get('device_alias'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', data['device_alias']):
        errors.append('device_alias must be a safe directory component')
    if not isinstance(data.get('serial'), str) or not re.fullmatch(r'[A-Za-z0-9._:-]+', data['serial']):
        errors.append('serial has invalid characters')
    if not isinstance(data.get('package'), str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', data['package']):
        errors.append('package must be an Android application ID')
    for key, default in [('startup_wait_seconds', 10), ('process_check_interval_seconds', 1), ('action_wait_seconds', 1)]:
        value = data.get(key, default)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 120):
            errors.append(f'{key} must be a finite number from 0 to 120')
    return errors


def load_config(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    errors = validate_config(data)
    if errors:
        raise ValueError('; '.join(errors))
    return data
