"""Bounded, read-only APKS identity and explicit environment inspection.

This module does not verify APK signatures. Android's update installation
checks the signing identity; no archive name or environment label proves it.
"""

import hashlib
import io
import json
import math
import re
import stat
import struct
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path


MAX_APKS_BYTES = 4 * 1024 ** 3
MAX_ARCHIVE_ENTRIES = 32768
MAX_CENTRAL_DIRECTORY_BYTES = 16 * 1024 ** 2
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 8 * 1024 ** 3
MAX_BASE_APK_BYTES = 64 * 1024 ** 2
MAX_SPLIT_APK_BYTES = 1024 ** 3
MAX_MANIFEST_BYTES = 4 * 1024 ** 2
MAX_CONFIG_ASSET_BYTES = 1024 ** 2
MAX_TOC_BYTES = 1024 ** 2
MAX_BASE_VARIANTS = 128
_COPY_CHUNK = 1024 ** 2
_ANDROID = 'http://schemas.android.com/apk/res/android'
_ABSENT = 0xffffffff
_EXPECTED_FIELDS = {'sha256', 'package', 'version_code', 'version_name'}
_POLICY_COMMON = {'kind', 'archive_apk', 'installed_apk', 'asset_path',
                  'asset_sha256', 'expected_value', 'source_ref'}
_PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+')
_BASE_APK = re.compile(r'(?:splits/base-master(?:_[0-9]+)?|universal)\.apk')


class ArtifactVerificationError(ValueError):
    """An unsafe/unreadable artifact, with a stable machine-readable category."""

    def __init__(self, message, category='artifact_archive'):
        super().__init__(message)
        self.category = category


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _sha256(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{64}', value) is not None


def _safe_archive_path(value, *, basename=False):
    if not _text(value) or value != value.strip() or '\\' in value or ':' in value:
        return False
    if value.startswith('/') or any(ord(character) < 32 for character in value):
        return False
    pieces = value.rstrip('/').split('/')
    if any(piece in ('', '.', '..') for piece in pieces):
        return False
    return not basename or len(pieces) == 1 and not value.endswith('/')


def _scalar(value):
    return (isinstance(value, (str, bool, int, float))
            and (not isinstance(value, str) or bool(value.strip()))
            and (not isinstance(value, float) or math.isfinite(value)))


def validate_artifact_contract(expected, policy):
    """Return strict schema errors without opening files or changing inputs."""
    errors = []
    if not isinstance(expected, dict):
        errors.append('expected must be an object')
    else:
        for field in sorted(_EXPECTED_FIELDS - expected.keys()):
            errors.append(f'expected.{field} is required')
        for field in sorted(expected.keys() - _EXPECTED_FIELDS, key=str):
            errors.append(f'unknown expected field: {field}')
        if not _sha256(expected.get('sha256')):
            errors.append('expected.sha256 must be a SHA-256 digest')
        if not isinstance(expected.get('package'), str) or not _PACKAGE.fullmatch(expected['package']):
            errors.append('expected.package must be an Android application ID')
        version = expected.get('version_code')
        if type(version) is not int or not 0 <= version <= 0x7fffffff:
            errors.append('expected.version_code must be an integer from 0 to 2147483647')
        if not _text(expected.get('version_name')):
            errors.append('expected.version_name must be a nonempty string')
    if not isinstance(policy, dict):
        return errors + ['environment_policy must be an object']
    kind = policy.get('kind')
    fields = _POLICY_COMMON | ({'offset'} if kind == 'uint32_le' else {'field_path'})
    for field in sorted(fields - policy.keys()):
        errors.append(f'environment_policy.{field} is required')
    for field in sorted(policy.keys() - fields, key=str):
        errors.append(f'unknown environment_policy field: {field}')
    if kind not in ('uint32_le', 'json_field'):
        errors.append('environment_policy.kind must be uint32_le or json_field')
    for field in ('archive_apk', 'asset_path', 'installed_apk'):
        value = policy.get(field)
        if not _safe_archive_path(value, basename=field == 'installed_apk') or value.endswith('/'):
            errors.append(f'environment_policy.{field} must be a safe relative archive path')
        elif field != 'asset_path' and not value.endswith('.apk'):
            errors.append(f'environment_policy.{field} must name an APK')
    if not _sha256(policy.get('asset_sha256')):
        errors.append('environment_policy.asset_sha256 must be a SHA-256 digest')
    if not _text(policy.get('source_ref')):
        errors.append('environment_policy.source_ref must identify the approved field schema')
    if not _scalar(policy.get('expected_value')):
        errors.append('environment_policy.expected_value must be a nonempty finite scalar')
    if kind == 'uint32_le':
        offset = policy.get('offset')
        if type(offset) is not int or not 0 <= offset <= MAX_CONFIG_ASSET_BYTES - 4:
            errors.append('environment_policy.offset must fit the bounded configuration asset')
        value = policy.get('expected_value')
        if type(value) is not int or not 0 <= value <= 0xffffffff:
            errors.append('uint32_le expected_value must be an unsigned 32-bit integer')
    elif kind == 'json_field':
        fields_path = policy.get('field_path')
        if not isinstance(fields_path, list) or not fields_path or not all(_text(item) for item in fields_path):
            errors.append('environment_policy.field_path must be a nonempty list of field names')
    return errors


def _archive_entries(archive):
    entries = archive.infolist()
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise ArtifactVerificationError('ZIP entry count exceeds inspection budget', 'artifact_budget')
    total, seen, result = 0, set(), {}
    for entry in entries:
        name = entry.filename
        if not _safe_archive_path(entry.orig_filename) or entry.orig_filename != name:
            raise ArtifactVerificationError(f'unsafe ZIP path: {entry.orig_filename!r}', 'artifact_path')
        canonical = name.rstrip('/').casefold()
        if canonical in seen:
            raise ArtifactVerificationError(f'duplicate ZIP entry: {name}', 'artifact_duplicate')
        seen.add(canonical)
        if stat.S_ISLNK(entry.external_attr >> 16):
            raise ArtifactVerificationError(f'ZIP symbolic link is not allowed: {name}', 'artifact_path')
        if entry.flag_bits & 1:
            raise ArtifactVerificationError(f'encrypted ZIP entry is unsupported: {name}', 'artifact_archive')
        total += entry.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ArtifactVerificationError('ZIP expanded size exceeds inspection budget', 'artifact_budget')
        result[name] = entry
    return result


def _bounded_zip(source):
    """Bound ZIP directory allocation before ZipFile builds its entry objects."""
    source.seek(0, 2)
    length = source.tell()
    source.seek(max(0, length - 65557))
    tail = source.read(65557)
    end = len(tail)
    while True:
        position = tail.rfind(b'PK\x05\x06', 0, end)
        if position < 0:
            raise zipfile.BadZipFile('no valid ZIP end record')
        if position + 22 <= len(tail):
            comment_size = struct.unpack_from('<H', tail, position + 20)[0]
            if position + 22 + comment_size == len(tail):
                break
        end = position
    disk, directory_disk, disk_count, count, directory_size, directory_offset = struct.unpack_from('<4H2I', tail, position + 4)
    if count == 0xffff or directory_size == 0xffffffff or directory_offset == 0xffffffff:
        locator_position = length - len(tail) + position - 20
        if locator_position < 0:
            raise zipfile.BadZipFile('missing ZIP64 locator')
        source.seek(locator_position)
        locator = source.read(20)
        if len(locator) != 20 or locator[:4] != b'PK\x06\x07':
            raise zipfile.BadZipFile('missing ZIP64 locator')
        locator_disk, record_offset, disks = struct.unpack_from('<IQI', locator, 4)
        if locator_disk or disks != 1 or record_offset + 56 > locator_position:
            raise zipfile.BadZipFile('unsupported split or invalid ZIP64 archive')
        source.seek(record_offset)
        record = source.read(56)
        if len(record) != 56 or record[:4] != b'PK\x06\x06':
            raise zipfile.BadZipFile('invalid ZIP64 end record')
        record_size = struct.unpack_from('<Q', record, 4)[0]
        if record_size < 44 or record_offset + 12 + record_size != locator_position:
            raise zipfile.BadZipFile('invalid ZIP64 end record bounds')
        disk, directory_disk, disk_count, count, directory_size, directory_offset = struct.unpack_from('<2I4Q', record, 16)
    if disk or directory_disk or disk_count != count:
        raise zipfile.BadZipFile('split ZIP archives are unsupported')
    if count > MAX_ARCHIVE_ENTRIES or directory_size > MAX_CENTRAL_DIRECTORY_BYTES:
        raise ArtifactVerificationError('ZIP directory exceeds inspection budget', 'artifact_budget')
    if directory_offset + directory_size > length:
        raise zipfile.BadZipFile('ZIP directory lies outside archive')
    source.seek(0)
    return zipfile.ZipFile(source)


def _read_bounded(archive, entry, limit):
    if entry.file_size > limit:
        raise ArtifactVerificationError(f'{entry.filename} exceeds inspection size budget', 'artifact_budget')
    with archive.open(entry) as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ArtifactVerificationError(f'{entry.filename} exceeds inspection size budget', 'artifact_budget')
    return data


def _spill_split(archive, entry, evidence_dir, *, prefix='configuration'):
    if entry.file_size > MAX_SPLIT_APK_BYTES:
        raise ArtifactVerificationError('APK exceeds inspection size budget', 'artifact_budget')
    destination = evidence_dir / (prefix + '-' + uuid.uuid4().hex + '.apk')
    total = 0
    with archive.open(entry) as source, destination.open('xb') as target:
        while chunk := source.read(_COPY_CHUNK):
            total += len(chunk)
            if total > MAX_SPLIT_APK_BYTES:
                raise ArtifactVerificationError('APK exceeds inspection size budget', 'artifact_budget')
            target.write(chunk)
    return destination


def _hash_all_apks(archive, entries, observed):
    total = 0
    for name, entry in entries.items():
        if not name.endswith('.apk'):
            continue
        if entry.file_size > MAX_SPLIT_APK_BYTES:
            raise ArtifactVerificationError(f'{name} exceeds APK inspection budget', 'artifact_budget')
        hasher, size = hashlib.sha256(), 0
        with archive.open(entry) as source:
            while chunk := source.read(_COPY_CHUNK):
                size += len(chunk)
                total += len(chunk)
                if size > MAX_SPLIT_APK_BYTES or total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise ArtifactVerificationError('APK stream exceeds inspection budget', 'artifact_budget')
                hasher.update(chunk)
        observed[name] = hasher.hexdigest()


@contextmanager
def _open_apk(archive, entry, evidence_dir, cached, *, force_disk=False, prefix='configuration'):
    path = cached.get(entry.filename)
    if path is None and (force_disk or entry.file_size > MAX_BASE_APK_BYTES):
        path = _spill_split(archive, entry, evidence_dir, prefix=prefix)
        cached[entry.filename] = path
    if path is not None:
        with path.open('rb') as source, _bounded_zip(source) as apk:
            yield apk
    else:
        data = _read_bounded(archive, entry, MAX_BASE_APK_BYTES)
        with _bounded_zip(io.BytesIO(data)) as apk:
            yield apk


def _protobuf_fields(data):
    """Read only the bounded protobuf wire types needed by bundletool's TOC."""
    fields, cursor = {}, 0

    def varint():
        nonlocal cursor
        value = 0
        for index in range(10):
            if cursor >= len(data):
                raise ValueError('truncated protobuf varint')
            byte = data[cursor]
            cursor += 1
            if index == 9 and byte > 1:
                raise ValueError('protobuf varint overflows uint64')
            value |= (byte & 0x7f) << (index * 7)
            if byte < 0x80:
                return value
        raise ValueError('invalid protobuf varint')

    while cursor < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not 0 < number <= 0x1fffffff:
            raise ValueError('invalid protobuf field number')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if cursor + length > len(data):
                raise ValueError('protobuf field lies outside TOC')
            value = data[cursor:cursor + length]
            cursor += length
        else:
            raise ValueError('unsupported protobuf wire type')
        fields.setdefault(number, []).append((wire, value))
    return fields


def _protobuf_values(fields, number, wire):
    values = fields.get(number, [])
    if any(kind != wire for kind, _ in values):
        raise ValueError(f'wrong wire type for TOC field {number}')
    return [value for _, value in values]


def _protobuf_one(fields, number, wire, default=None):
    values = _protobuf_values(fields, number, wire)
    if len(values) > 1:
        raise ValueError(f'ambiguous duplicate TOC field {number}')
    return values[0] if values else default


def _toc_base_paths(data):
    # Field numbers come from google/bundletool src/main/proto/commands.proto:
    # BuildApksResult.variant -> Variant.apk_set -> base module -> description.
    variants = _protobuf_values(_protobuf_fields(data), 1, 2)
    if not variants:
        raise ValueError('TOC contains no installable variants')
    if len(variants) > MAX_BASE_VARIANTS:
        raise ArtifactVerificationError('TOC base variant count exceeds budget', 'artifact_budget')
    paths = {}
    for variant in variants:
        variant = _protobuf_fields(variant)
        number = _protobuf_one(variant, 3, 0, 0)
        masters = []
        for apk_set in _protobuf_values(variant, 2, 2):
            apk_set = _protobuf_fields(apk_set)
            metadata = _protobuf_one(apk_set, 1, 2)
            if metadata is None:
                raise ValueError('TOC APK set has no module metadata')
            module_name = _protobuf_one(_protobuf_fields(metadata), 1, 2)
            if module_name != b'base':
                continue
            for description in _protobuf_values(apk_set, 2, 2):
                description = _protobuf_fields(description)
                path = _protobuf_one(description, 2, 2)
                if path is None:
                    raise ValueError('TOC APK description has no path')
                path = path.decode('utf-8')
                if not _safe_archive_path(path) or not path.endswith('.apk'):
                    raise ValueError('TOC APK description has an unsafe path')
                kinds = [kind for kind in range(3, 10) if kind in description]
                if len(kinds) != 1 or kinds[0] not in (3, 4):
                    raise ValueError('TOC uses unsupported or ambiguous base APK metadata')
                metadata = _protobuf_one(description, kinds[0], 2)
                if kinds[0] == 4:
                    masters.append(path)
                else:
                    is_master = _protobuf_one(_protobuf_fields(metadata), 2, 0, 0)
                    if is_master not in (0, 1):
                        raise ValueError('invalid master-split flag in TOC')
                    if is_master:
                        masters.append(path)
        if len(masters) != 1:
            raise ValueError('each TOC variant must identify exactly one supported base APK')
        paths.setdefault(masters[0], []).append(number)
    return paths


def _base_candidates(archive, entries):
    candidates = sorted(name for name in entries if _BASE_APK.fullmatch(name))
    for name in entries:
        if (name.startswith('splits/base-master') or name.startswith('universal')) and name.endswith('.apk') and name not in candidates:
            raise ArtifactVerificationError(f'unsupported base APK variant path: {name}', 'artifact_manifest')
    toc_paths = {}
    if 'toc.pb' in entries:
        try:
            toc_paths = _toc_base_paths(_read_bounded(archive, entries['toc.pb'], MAX_TOC_BYTES))
        except (ValueError, UnicodeError) as error:
            if isinstance(error, ArtifactVerificationError):
                raise
            raise ArtifactVerificationError(f'cannot verify TOC base variants: {error}', 'artifact_manifest') from error
        for name in toc_paths:
            if name not in entries or name not in candidates:
                raise ArtifactVerificationError(f'TOC references an absent or unsupported base APK: {name}', 'artifact_manifest')
    if len(candidates) > MAX_BASE_VARIANTS:
        raise ArtifactVerificationError('base APK variant count exceeds budget', 'artifact_budget')
    return candidates, toc_paths


def _selection_description(raw, entries, *, asset=False):
    description = _protobuf_fields(raw)
    path = _protobuf_one(description, 2, 2)
    if path is None:
        raise ValueError('TOC installable APK has no path')
    path = path.decode('utf-8')
    if not _safe_archive_path(path) or path not in entries or not path.endswith('.apk'):
        raise ValueError('TOC installable APK is absent or has an unsafe path')
    kinds = [kind for kind in range(3, 10) if kind in description]
    if len(kinds) != 1 or kinds[0] not in ((7,) if asset else (3, 4)):
        raise ValueError('unsupported installable APK metadata in TOC')
    metadata = _protobuf_fields(_protobuf_one(description, kinds[0], 2))
    if kinds[0] == 4:
        return path, 'base.apk', True
    split_id = _protobuf_one(metadata, 1, 2, b'').decode('utf-8')
    installed = 'split_' + split_id + '.apk' if split_id else 'base.apk'
    if not _safe_archive_path(installed, basename=True):
        raise ValueError('TOC split identity cannot map to a safe installed APK')
    master = _protobuf_one(metadata, 2, 0, 0)
    if master not in (0, 1):
        raise ValueError('invalid installable master-split flag')
    return path, installed, bool(master)


def _environment_selection(archive, entries, bases, policy):
    if 'toc.pb' not in entries:
        if len(bases) != 1 or policy['archive_apk'] != bases[0] or policy['installed_apk'] != 'base.apk':
            raise ValueError('without TOC, only a unique self-contained base APK can prove environment')
        return [{bases[0]}], {bases[0]}, {bases[0]: 'base.apk'}
    toc = _protobuf_fields(_read_bounded(archive, entries['toc.pb'], MAX_TOC_BYTES))
    groups, possible, names, common = [], set(), {}, set()

    def record(raw, *, asset=False):
        path, installed, master = _selection_description(raw, entries, asset=asset)
        if path in names and names[path] != installed:
            raise ValueError('TOC gives conflicting installed identities for one APK')
        names[path] = installed
        possible.add(path)
        return path, master

    for raw_variant in _protobuf_values(toc, 1, 2):
        guaranteed = set()
        for raw_set in _protobuf_values(_protobuf_fields(raw_variant), 2, 2):
            apk_set = _protobuf_fields(raw_set)
            module = _protobuf_fields(_protobuf_one(apk_set, 1, 2, b''))
            module_name = _protobuf_one(module, 1, 2)
            delivery = _protobuf_one(module, 6, 0, 0)
            if module_name != b'base' and delivery == 0:
                raise ValueError('TOC feature delivery type is unknown')
            if module_name != b'base' and delivery != 1:
                continue
            unconditional = module_name == b'base' or not _protobuf_one(module, 5, 2, b'')
            masters = []
            for raw in _protobuf_values(apk_set, 2, 2):
                path, master = record(raw)
                if master:
                    masters.append(path)
            if unconditional and len(masters) == 1:
                guaranteed.add(masters[0])
        groups.append(guaranteed)
    for raw_set in _protobuf_values(toc, 3, 2):
        asset_set = _protobuf_fields(raw_set)
        module = _protobuf_fields(_protobuf_one(asset_set, 1, 2, b''))
        delivery = _protobuf_one(module, 4, 0, 0)
        if delivery == 0:
            raise ValueError('TOC asset-pack delivery type is unknown')
        if delivery != 1:
            continue
        masters = []
        for raw in _protobuf_values(asset_set, 2, 2):
            path, master = record(raw, asset=True)
            if master:
                masters.append(path)
        if len(masters) == 1 and not _protobuf_one(module, 6, 2, b''):
            common.add(masters[0])
    if not groups:
        raise ValueError('TOC contains no selectable app variants')
    groups = [group | common for group in groups]
    eligible = set().union(*groups)
    reference = policy['archive_apk']
    if reference not in eligible or names.get(reference) != policy['installed_apk']:
        raise ValueError('configured environment APK is not proven to install with its configured installed name')
    required = [{name for name in group if names.get(name) == policy['installed_apk']} for group in groups]
    if any(not group for group in required):
        raise ValueError('environment APK is not guaranteed in every selectable variant')
    return required, possible, names


def _configuration_assets(archive, entries, bases, policy, evidence_dir, cached, environment):
    required, possible, names = _environment_selection(archive, entries, bases, policy)
    environment['installable_apks'] = sorted(possible)
    environment['validated_assets'] = []
    hashes_by_apk, reference, expanded_bytes = {}, None, 0
    for name in sorted(possible):
        with _open_apk(archive, entries[name], evidence_dir, cached,
                       force_disk=name == policy['archive_apk']) as apk:
            apk_entries = _archive_entries(apk)
            if policy['asset_path'] not in apk_entries:
                continue
            data = _read_bounded(apk, apk_entries[policy['asset_path']], MAX_CONFIG_ASSET_BYTES)
        expanded_bytes += len(data)
        if expanded_bytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ArtifactVerificationError('combined environment asset expansion exceeds budget', 'artifact_budget')
        sha256 = hashlib.sha256(data).hexdigest()
        hashes_by_apk[name] = sha256
        environment['validated_assets'].append({'archive_apk': name, 'installed_apk': names[name], 'asset_sha256': sha256})
        if name == policy['archive_apk']:
            environment['asset_sha256'] = sha256
            reference = data
    if reference is None:
        raise ValueError('the configured environment asset does not exist in its installable APK')
    if environment['asset_sha256'] != policy['asset_sha256'].lower():
        return reference
    for group in required:
        if not any(hashes_by_apk.get(name) == environment['asset_sha256'] for name in group):
            raise ValueError('a selectable variant lacks the approved environment configuration')
    if any(sha256 != environment['asset_sha256'] for sha256 in hashes_by_apk.values()):
        raise ValueError('the same environment asset has conflicting contents in another installable APK')
    return reference


def _chunk_header(data, offset, end):
    if offset + 8 > end:
        raise ValueError('truncated binary XML chunk header')
    kind, header_size, size = struct.unpack_from('<HHI', data, offset)
    if header_size < 8 or size < header_size or offset + size > end:
        raise ValueError('invalid binary XML chunk bounds')
    return kind, header_size, size


def _string_pool(data, offset, header_size, size):
    if header_size < 28:
        raise ValueError('invalid binary XML string pool header')
    count, styles, flags, strings_start, styles_start = struct.unpack_from('<5I', data, offset + 8)
    offsets_end = header_size + 4 * (count + styles)
    strings_end = styles_start or size
    if offsets_end > strings_start or not strings_start <= strings_end <= size:
        raise ValueError('invalid binary XML string pool offsets')
    end = offset + strings_end

    def length(cursor, utf8):
        unit = 1 if utf8 else 2
        if cursor + unit > end:
            raise ValueError('truncated binary XML string length')
        value = data[cursor] if utf8 else struct.unpack_from('<H', data, cursor)[0]
        cursor += unit
        flag = 0x80 if utf8 else 0x8000
        if value & flag:
            if cursor + unit > end:
                raise ValueError('truncated binary XML long string length')
            tail = data[cursor] if utf8 else struct.unpack_from('<H', data, cursor)[0]
            value = ((value & (flag - 1)) << (8 if utf8 else 16)) | tail
            cursor += unit
        return value, cursor

    strings = []
    for index in range(count):
        relative = struct.unpack_from('<I', data, offset + header_size + index * 4)[0]
        cursor = offset + strings_start + relative
        utf8 = bool(flags & 0x100)
        if utf8:
            _, cursor = length(cursor, True)
        amount, cursor = length(cursor, utf8)
        byte_count = amount if utf8 else amount * 2
        terminator = 1 if utf8 else 2
        if cursor + byte_count + terminator > end or any(data[cursor + byte_count:cursor + byte_count + terminator]):
            raise ValueError('invalid binary XML string bounds or terminator')
        strings.append(data[cursor:cursor + byte_count].decode('utf-8' if utf8 else 'utf-16le'))
    return strings


def _binary_elements(data):
    kind, header_size, size = _chunk_header(data, 0, len(data))
    if kind != 3 or size != len(data):
        raise ValueError('invalid binary XML root')
    pool, stack, elements = None, [], []

    def string(index, *, optional=False):
        if optional and index == _ABSENT:
            return None
        if pool is None or not 0 <= index < len(pool):
            raise ValueError('invalid binary XML string reference')
        return pool[index]

    offset = header_size
    while offset < size:
        kind, head, chunk_size = _chunk_header(data, offset, size)
        end = offset + chunk_size
        if kind == 1:
            if pool is not None:
                raise ValueError('multiple binary XML string pools')
            pool = _string_pool(data, offset, head, chunk_size)
        elif kind == 0x102:
            if head < 16 or offset + head + 20 > end:
                raise ValueError('truncated binary XML start element')
            extension = offset + head
            namespace, name = struct.unpack_from('<II', data, extension)
            tag = (string(namespace, optional=True), string(name))
            start, attribute_size, count = struct.unpack_from('<HHH', data, extension + 8)
            first = extension + start
            if start < 20 or attribute_size < 20 or first + attribute_size * count > end:
                raise ValueError('invalid binary XML attributes')
            attributes = {}
            for index in range(count):
                current = first + attribute_size * index
                namespace, name, _raw = struct.unpack_from('<III', data, current)
                value_size, reserved, value_type, value = struct.unpack_from('<HBBI', data, current + 12)
                if value_size != 8 or reserved != 0:
                    raise ValueError('invalid binary XML typed value')
                key = (string(namespace, optional=True), string(name))
                if key in attributes:
                    raise ValueError('duplicate binary XML attribute')
                if value_type == 3:
                    parsed = string(value)
                elif value_type in (0x10, 0x11):
                    parsed = value
                elif value_type == 0x12:
                    parsed = bool(value)
                else:
                    parsed = None  # Resource references cannot prove actual identity.
                attributes[key] = parsed
            elements.append((tag, attributes, len(stack)))
            stack.append(tag)
        elif kind == 0x103:
            if head < 16 or offset + head + 8 > end:
                raise ValueError('truncated binary XML end element')
            namespace, name = struct.unpack_from('<II', data, offset + head)
            if not stack or stack.pop() != (string(namespace, optional=True), string(name)):
                raise ValueError('unbalanced binary XML elements')
        offset = end
    if stack:
        raise ValueError('unterminated binary XML elements')
    return elements


def _manifest(data):
    try:
        if data.lstrip().startswith(b'<'):
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ValueError('XML DTD/entities are unsupported')
            root = ET.fromstring(data)
            elements = []
            for depth, element in [(0, root)] + [(1, child) for child in root]:
                attrs = {}
                for name, value in element.attrib.items():
                    namespace, local = name[1:].split('}', 1) if name.startswith('{') else (None, name)
                    attrs[(namespace, local)] = value
                elements.append(((None, element.tag), attrs, depth))
        else:
            elements = _binary_elements(data)
        manifests = [attrs for tag, attrs, depth in elements if tag == (None, 'manifest') and depth == 0]
        if len(manifests) != 1 or sum(depth == 0 for _, _, depth in elements) != 1:
            raise ValueError('manifest must have one manifest root')
        manifest = manifests[0]
        sdk = [attrs for tag, attrs, depth in elements if tag == (None, 'uses-sdk') and depth == 1]
        if len(sdk) > 1:
            raise ValueError('multiple uses-sdk elements')

        def number(value):
            if type(value) is int and 0 <= value <= 0x7fffffff:
                return value
            if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
                result = int(value)
                if result <= 0x7fffffff:
                    return result
            raise ValueError('manifest integer field is missing, unresolved or invalid')

        package = manifest.get((None, 'package'))
        version_name = manifest.get((_ANDROID, 'versionName'))
        if not isinstance(package, str) or not _PACKAGE.fullmatch(package) or not _text(version_name):
            raise ValueError('manifest package/versionName is missing or unresolved')
        return {'package': package, 'version_code': number(manifest.get((_ANDROID, 'versionCode'))),
                'version_name': version_name,
                'min_sdk': number(sdk[0][(_ANDROID, 'minSdkVersion')]) if sdk and (_ANDROID, 'minSdkVersion') in sdk[0] else None,
                'target_sdk': number(sdk[0][(_ANDROID, 'targetSdkVersion')]) if sdk and (_ANDROID, 'targetSdkVersion') in sdk[0] else None}
    except (ValueError, ET.ParseError, struct.error, UnicodeError, IndexError) as error:
        raise ArtifactVerificationError(f'cannot inspect actual APK manifest: {error}', 'artifact_manifest') from error


def _environment_value(data, policy):
    if policy['kind'] == 'uint32_le':
        if policy['offset'] + 4 > len(data):
            raise ValueError('configured uint32 offset lies outside the actual asset')
        return struct.unpack_from('<I', data, policy['offset'])[0]

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate JSON field: {key}')
            result[key] = value
        return result

    value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique_object)
    for key in policy['field_path']:
        if not isinstance(value, dict) or key not in value:
            raise ValueError('configured JSON field does not exist')
        value = value[key]
    if not _scalar(value):
        raise ValueError('configured JSON field is not a nonempty finite scalar')
    return value


def verify_apks(path, expected, environment_policy, evidence_dir):
    """Inspect an approved APKS and save observations before any device action.

    Hash/identity/environment mismatches return BLOCKED. Unsafe ZIP structure,
    parsing failures and exceeded size limits raise ArtifactVerificationError;
    their observations are saved as well. Large configuration APKs are copied
    in chunks to uniquely named files under evidence_dir, never OS temp.
    """
    evidence_dir = Path(evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    inspection_path = evidence_dir / 'artifact-inspection.json'
    source_path = Path(path).resolve()
    if inspection_path == source_path or (inspection_path.exists() and source_path.exists()
                                          and inspection_path.samefile(source_path)):
        inspection_path = evidence_dir / ('artifact-inspection-' + uuid.uuid4().hex + '.json')
    policy = environment_policy
    report = {
        'status': 'BLOCKED', 'category': 'artifact_unverified', 'reasons': [],
        'sha256': None, 'package': None, 'version_code': None, 'version_name': None,
        'min_sdk': None, 'target_sdk': None, 'base_apk': None, 'base_apks': [], 'apk_hashes': {},
        'inspection_path': str(inspection_path),
        'environment_evidence': {
            'asset_sha256': None, 'observed_value': None, 'expected_value': policy.get('expected_value') if isinstance(policy, dict) else None,
            'matched': False, **{key: policy.get(key) if isinstance(policy, dict) else None
                                 for key in ('archive_apk', 'installed_apk', 'asset_path', 'source_ref')},
        },
    }

    def finish(category, reasons, status='BLOCKED'):
        report.update(status=status, category=category, reasons=reasons)
        temporary = evidence_dir / ('inspection-' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf-8') as output:
            json.dump(report, output, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.replace(inspection_path)
        return report

    try:
        errors = validate_artifact_contract(expected, policy)
        if errors:
            raise ArtifactVerificationError('; '.join(errors), 'artifact_contract')
        with Path(path).open('rb') as source:
            hasher, total = hashlib.sha256(), 0
            while chunk := source.read(_COPY_CHUNK):
                total += len(chunk)
                if total > MAX_APKS_BYTES:
                    raise ArtifactVerificationError('APKS exceeds inspection size budget', 'artifact_budget')
                hasher.update(chunk)
            report['sha256'] = hasher.hexdigest()
            if report['sha256'] != expected['sha256'].lower():
                return finish('artifact_hash_mismatch', ['actual APKS SHA-256 does not match the approved artifact'])
            source.seek(0)
            with _bounded_zip(source) as archive:
                entries = _archive_entries(archive)
                _hash_all_apks(archive, entries, report['apk_hashes'])
                bases, toc_paths = _base_candidates(archive, entries)
                if not bases:
                    return finish('artifact_manifest_missing', ['no supported base APK exists in APKS'])
                report['base_apk'] = bases[0]
                cached = {}
                for name in bases:
                    observation = {'archive_apk': name, 'sha256': report['apk_hashes'][name],
                                   'toc_variants': toc_paths.get(name, [])}
                    report['base_apks'].append(observation)
                    with _open_apk(archive, entries[name], evidence_dir, cached, prefix='base') as base:
                        base_entries = _archive_entries(base)
                        if 'AndroidManifest.xml' not in base_entries:
                            raise ArtifactVerificationError(f'{name} has no AndroidManifest.xml', 'artifact_manifest')
                        try:
                            observation.update(_manifest(_read_bounded(base, base_entries['AndroidManifest.xml'], MAX_MANIFEST_BYTES)))
                        except ArtifactVerificationError as error:
                            raise ArtifactVerificationError(f'{name}: {error}', error.category) from error
                # All candidates participate in the gate. The first observation
                # supplies legacy summary fields only, never selection priority.
                identity_fields = ('package', 'version_code', 'version_name', 'min_sdk', 'target_sdk')
                report.update({key: report['base_apks'][0][key] for key in identity_fields})
                mismatches = [f'{item["archive_apk"]}: actual {key} does not match approved {key}'
                              for item in report['base_apks'] for key in ('package', 'version_code', 'version_name')
                              if item[key] != expected[key]]
                if mismatches:
                    return finish('artifact_identity_mismatch', mismatches)
                differences = [f'{item["archive_apk"]}: {key} differs across base APK variants'
                               for item in report['base_apks'] for key in ('min_sdk', 'target_sdk')
                               if item[key] != report[key]]
                if differences:
                    return finish('artifact_variant_mismatch', differences)
                if policy['archive_apk'] not in entries:
                    return finish('environment_unverified', ['the configured environment APK does not exist in APKS'])
                environment = report['environment_evidence']
                try:
                    data = _configuration_assets(archive, entries, bases, policy, evidence_dir, cached, environment)
                except (ValueError, UnicodeError) as error:
                    if isinstance(error, ArtifactVerificationError):
                        raise
                    return finish('environment_unverified', [f'cannot prove installable environment configuration: {error}'])
                if environment['asset_sha256'] != policy['asset_sha256'].lower():
                    return finish('environment_asset_hash_mismatch', ['actual environment asset SHA-256 does not match approved field schema'])
                try:
                    observed = _environment_value(data, policy)
                except (ValueError, UnicodeError, RecursionError) as error:
                    return finish('environment_unverified', [f'cannot inspect configured environment value: {error}'])
                environment['observed_value'] = observed
                environment['matched'] = type(observed) is type(policy['expected_value']) and observed == policy['expected_value']
                if not environment['matched']:
                    return finish('environment_mismatch', ['actual environment value does not match the approved test environment value'])
        return finish('verified', [], status='PASS')
    except ArtifactVerificationError as error:
        # Invalid contracts may contain non-JSON values; preserve a safe report.
        if error.category == 'artifact_contract':
            report['environment_evidence'] = {'matched': False}
        finish(error.category, [str(error)])
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as error:
        finish('artifact_archive', [str(error)])
        raise ArtifactVerificationError(f'cannot inspect ZIP archive: {error}', 'artifact_archive') from error
    except OSError as error:
        finish('artifact_io', [str(error)])
        raise ArtifactVerificationError(f'cannot read/write artifact evidence: {error}', 'artifact_io') from error


__all__ = ['ArtifactVerificationError', 'validate_artifact_contract', 'verify_apks']
