"""Read a Module MVID from bounded PE/CLI metadata without loading or executing it.

This verifies a metadata identifier, not executable validity or trusted origin.
The caller must separately bind the complete bytes with their SHA-256 digest.
"""

from uuid import UUID


MAX_PE_BYTES = 256 * 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ValueError('PE identity: ' + message)


def _span(data, offset, size, label, *, start=0, end=None):
    limit = len(data) if end is None else end
    _require(start <= offset <= limit and 0 <= size <= limit-offset,
             label + ' is truncated or outside its declared span')
    return data[offset:offset+size]


def _uint(data, offset, size, label, *, start=0, end=None):
    return int.from_bytes(_span(data, offset, size, label, start=start, end=end), 'little')


def read_mvid(value):
    """Return the canonical lowercase GUID from one raw PE image's Module table.

    Accept bytes-like contiguous byte buffers only. Missing/ambiguous mappings,
    truncated streams, duplicate streams, absent Module rows and zero GUIDs all
    raise ValueError. Neither Python import nor any CLR/native loader is called.
    """
    try:
        data = memoryview(value)
    except TypeError as error:
        raise ValueError('PE identity: input must be a byte buffer') from error
    _require(data.ndim == 1 and data.itemsize == 1 and data.contiguous,
             'input must be one contiguous byte buffer')
    data = data.cast('B')
    _require(0 < len(data) <= MAX_PE_BYTES, 'input is empty or exceeds the 256 MiB budget')
    _require(_span(data, 0, 2, 'DOS signature') == b'MZ', 'missing DOS signature')
    pe = _uint(data, 0x3c, 4, 'PE header pointer')
    _require(pe >= 0x40, 'PE header overlaps the DOS header')
    _require(_span(data, pe, 4, 'PE signature') == b'PE\0\0', 'missing PE signature')
    coff = pe+4
    _span(data, coff, 20, 'COFF header')
    section_count = _uint(data, coff+2, 2, 'COFF section count')
    _require(section_count > 0, 'no PE sections')
    optional_size = _uint(data, coff+16, 2, 'optional header size')
    optional = coff+20
    optional_end = optional+optional_size
    _span(data, optional, optional_size, 'optional header')
    magic = _uint(data, optional, 2, 'optional header magic', end=optional_end)
    _require(magic in (0x10b, 0x20b), 'unsupported PE optional header magic')
    directory_offset = 96 if magic == 0x10b else 112
    count = _uint(data, optional+directory_offset-4, 4, 'data directory count', end=optional_end)
    _require(15 <= count <= (optional_size-directory_offset)//8,
             'CLI directory is absent or the directory count exceeds the optional header')
    headers_size = _uint(data, optional+60, 4, 'PE header span', end=optional_end)
    section_table = optional_end
    _span(data, section_table, section_count*40, 'section table')
    _require(section_table+section_count*40 <= headers_size <= len(data), 'invalid PE header span')
    sections = []
    for index in range(section_count):
        entry = section_table+40*index
        virtual_size = _uint(data, entry+8, 4, 'section virtual size')
        rva = _uint(data, entry+12, 4, 'section RVA')
        raw_size = _uint(data, entry+16, 4, 'section raw size')
        raw_offset = _uint(data, entry+20, 4, 'section raw offset')
        _span(data, raw_offset, raw_size, 'section raw data')
        _require(rva+max(virtual_size, raw_size) <= 1 << 32, 'section RVA overflows 32 bits')
        if raw_size:
            _require(raw_offset >= headers_size, 'section raw data overlaps PE headers')
        sections.append((rva, max(virtual_size, raw_size), raw_offset, raw_size))

    def raw_span(rva, size, label):
        _require(rva > 0 and size > 0 and rva+size <= 1 << 32, label + ' RVA is empty or invalid')
        matches = []
        if rva < headers_size:
            matches.append((0, headers_size, 0, headers_size))
        for section in sections:
            start, extent, _, _ = section
            if extent and rva < start+extent and start < rva+size:
                matches.append(section)
        _require(len(matches) == 1, label + ' RVA mapping is missing or ambiguous')
        start, extent, raw, backed = matches[0]
        displacement = rva-start
        _require(0 <= displacement and size <= extent-displacement
                 and size <= backed-displacement, label + ' RVA span is not fully raw-backed')
        offset = raw+displacement
        _span(data, offset, size, label)
        return offset

    cli_entry = optional+directory_offset+14*8
    cli_rva = _uint(data, cli_entry, 4, 'CLI RVA', end=optional_end)
    cli_size = _uint(data, cli_entry+4, 4, 'CLI size', end=optional_end)
    _require(cli_size >= 72, 'CLI header must contain at least 72 bytes')
    cli = raw_span(cli_rva, cli_size, 'CLI header')
    cb = _uint(data, cli, 4, 'CLI cb')
    _require(72 <= cb <= cli_size, 'invalid CLI cb')
    metadata_rva = _uint(data, cli+8, 4, 'metadata RVA')
    metadata_size = _uint(data, cli+12, 4, 'metadata size')
    root = raw_span(metadata_rva, metadata_size, 'metadata root')
    end = root+metadata_size
    _require(_span(data, root, 4, 'metadata signature', end=end) == b'BSJB', 'missing CLI metadata signature')
    version_length = _uint(data, root+12, 4, 'metadata version length', end=end)
    _require(version_length > 0, 'metadata version is empty')
    _span(data, root+16, version_length, 'metadata version', end=end)
    cursor = root+16+((version_length+3) & ~3)
    _span(data, cursor, 4, 'metadata stream count', end=end)
    streams_count = _uint(data, cursor+2, 2, 'metadata stream count', end=end)
    _require(streams_count > 0, 'metadata has no streams')
    cursor += 4
    streams = {}
    for _ in range(streams_count):
        offset = _uint(data, cursor, 4, 'stream offset', end=end)
        size = _uint(data, cursor+4, 4, 'stream size', end=end)
        name_start = cursor+8
        name_end = None
        for position in range(name_start, min(name_start+32, end)):
            if data[position] == 0:
                name_end = position
                break
        _require(name_end is not None and name_end > name_start, 'stream name is missing, empty or too long')
        name_bytes = bytes(data[name_start:name_end])
        _require(all(0 < byte < 128 for byte in name_bytes), 'stream name is not ASCII')
        name = name_bytes.decode('ascii')
        _require(name not in streams, 'duplicate metadata stream: ' + name)
        cursor = name_start+((name_end-name_start+1+3) & ~3)
        _span(data, name_end, cursor-name_end, 'stream name padding', end=end)
        _span(data, root+offset, size, 'stream ' + name, start=root, end=end)
        streams[name] = (root+offset, size)
    occupied = []
    for name, (offset, size) in streams.items():
        if size:
            _require(offset >= cursor, 'metadata stream overlaps stream headers: ' + name)
            occupied.append((offset, offset+size))
    occupied.sort()
    _require(all(left[1] <= right[0] for left, right in zip(occupied, occupied[1:])),
             'metadata streams overlap')
    tables = [name for name in ('#~', '#-') if name in streams]
    _require(len(tables) == 1 and '#GUID' in streams, 'requires exactly one table stream and one GUID heap')
    table, table_size = streams[tables[0]]
    table_end = table+table_size
    _span(data, table, 24, 'table header', end=table_end)
    heap_sizes = _uint(data, table+6, 1, 'heap size flags', end=table_end)
    valid = _uint(data, table+8, 8, 'table valid mask', end=table_end)
    _require(valid & 1, 'Module table is missing')
    cursor = table+24
    module_rows = None
    for bit in range(64):
        if valid & (1 << bit):
            rows = _uint(data, cursor, 4, 'table row count', end=table_end)
            if bit == 0:
                module_rows = rows
            cursor += 4
    _require(module_rows == 1, 'Module table must contain exactly one row')
    if heap_sizes & 0x40:
        _span(data, cursor, 4, 'table ExtraData field', end=table_end)
        cursor += 4
    string_width = 4 if heap_sizes & 1 else 2
    guid_width = 4 if heap_sizes & 2 else 2
    _span(data, cursor, 2+string_width+3*guid_width, 'Module row', end=table_end)
    mvid_index = _uint(data, cursor+2+string_width, guid_width, 'Module Mvid index', end=table_end)
    guid_heap, guid_size = streams['#GUID']
    _require(guid_size > 0 and guid_size % 16 == 0, 'GUID heap size is not a positive multiple of 16')
    _require(1 <= mvid_index <= guid_size//16, 'Module Mvid index is zero or outside the GUID heap')
    raw_guid = bytes(_span(data, guid_heap+(mvid_index-1)*16, 16, 'Module Mvid GUID', end=guid_heap+guid_size))
    result = UUID(bytes_le=raw_guid)
    _require(result.int != 0, 'Module Mvid GUID is empty')
    return str(result)
