"""Reviewed test contracts and verifiable local image evidence."""

import hashlib
import re
import struct
import uuid
import zlib
from decimal import Decimal
from pathlib import Path


def make_id(prefix: str) -> str:
    if not isinstance(prefix, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', prefix):
        raise ValueError('ID prefix must contain lowercase letters, digits or underscores')
    return f'{prefix}_{uuid.uuid4().hex}'


def validate_case(case: dict, *, execution_ready: bool = True) -> list[str]:
    """Validate draft structure or, by default, an executable reviewed case."""
    if not isinstance(case, dict):
        return ['case must be an object']
    errors = []
    for field in ('case_id', 'requirement_id', 'source_ref'):
        if not isinstance(case.get(field), str) or not case[field].strip():
            errors.append(f'{field} must be a nonempty string')
    if type(case.get('approved')) is not bool:
        errors.append('approved must be an explicit boolean')
    elif execution_ready and case['approved'] is not True:
        errors.append('not executable: approved must be true after human review')
    if not isinstance(case.get('steps'), list) or not case['steps']:
        errors.append('steps must be a nonempty list')
    if not isinstance(case.get('expectation'), dict) or not case['expectation']:
        errors.append('expectation must be an independently reviewed object')
    elif execution_ready:
        expected = case['expectation']
        for field in ('target_id', 'window_id'):
            if not isinstance(expected.get(field), str) or not expected[field].strip():
                errors.append(f'not executable: expectation.{field} must bind an explicit target/context')
        if expected.get('approved') is not True:
            errors.append('not executable: expectation must be approved')
        if type(expected.get('level_delta')) is not int or expected['level_delta'] != 1:
            errors.append('not executable: expectation.level_delta must specify a single upgrade')
        for field in ('cost', 'non_upgrade_delta'):
            value = expected.get(field)
            if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)', str(value)):
                errors.append(f'not executable: expectation.{field} is unknown or not an exact amount')
            elif field == 'cost' and Decimal(str(value)) < 0:
                errors.append('not executable: expectation.cost cannot be negative')
    return errors


def _png_dimensions(raw: bytes) -> tuple[int, int]:
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('not a PNG image')
    offset, header, image_chunks, palette = 8, None, [], None
    ended, idat_finished = False, False
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise ValueError('truncated PNG chunk')
        size = struct.unpack('>I', raw[offset:offset+4])[0]
        kind = raw[offset+4:offset+8]
        end = offset + 12 + size
        if end > len(raw) or not re.fullmatch(b'[A-Za-z]{4}', kind):
            raise ValueError('invalid PNG chunk')
        data = raw[offset+8:end-4]
        checksum = struct.unpack('>I', raw[end-4:end])[0]
        if zlib.crc32(kind + data) & 0xffffffff != checksum:
            raise ValueError('PNG checksum mismatch')
        if header is None and kind != b'IHDR':
            raise ValueError('PNG must begin with IHDR')
        if kind == b'IHDR':
            if header is not None or size != 13:
                raise ValueError('invalid PNG header')
            header = struct.unpack('>IIBBBBB', data)
        elif kind == b'PLTE':
            if palette is not None or image_chunks or not size or size % 3 or size > 768:
                raise ValueError('invalid PNG palette')
            palette = data
        elif kind == b'IDAT':
            if idat_finished:
                raise ValueError('PNG IDAT chunks must be contiguous')
            image_chunks.append(data)
        elif kind == b'IEND':
            if size or not image_chunks or end != len(raw):
                raise ValueError('invalid PNG end')
            ended = True
        elif kind[0] < ord('a'):
            raise ValueError('unsupported critical PNG chunk')
        if image_chunks and kind != b'IDAT':
            idat_finished = True
        offset = end
    if not ended or header is None:
        raise ValueError('incomplete PNG')
    width, height, depth, color, compression, filtering, interlace = header
    depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if width < 1 or height < 1 or depth not in depths.get(color, ()) or compression or filtering or interlace not in (0, 1):
        raise ValueError('unsupported PNG header values')
    if color == 3 and (palette is None or len(palette) // 3 > 2**depth):
        raise ValueError('indexed PNG requires a valid palette')
    if color in (0, 4) and palette is not None:
        raise ValueError('grayscale PNG cannot contain a palette')
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = [(0, 0, 1, 1)] if interlace == 0 else [
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    scanlines = []
    for x, y, dx, dy in passes:
        columns = max(0, (width - x + dx - 1) // dx)
        rows = max(0, (height - y + dy - 1) // dy)
        if columns and rows:
            scanlines.append((rows, (columns * channels * depth + 7) // 8 + 1))
    expected_bytes = sum(rows * size for rows, size in scanlines)
    if expected_bytes > 256 * 1024 * 1024:
        raise ValueError('PNG exceeds evidence decode limit')
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(b''.join(image_chunks), expected_bytes + 1)
    except zlib.error as error:
        raise ValueError('invalid PNG compressed pixels') from error
    if len(pixels) != expected_bytes or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('invalid PNG pixel length')
    offset = 0
    for rows, size in scanlines:
        for _ in range(rows):
            if pixels[offset] > 4:
                raise ValueError('invalid PNG scanline filter')
            offset += size
    return width, height


def evidence_for_png(path: Path) -> dict:
    path = Path(path).resolve()
    raw = path.read_bytes()
    width, height = _png_dimensions(raw)
    return {'evidence_id': make_id('evidence'), 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'bytes': len(raw), 'width': width, 'height': height}
