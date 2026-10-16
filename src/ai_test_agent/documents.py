"""Bounded, local-only extraction of source documents for M1 analysis.

OOXML relationships, field codes, embedded programs and source prose are data.
This module never evaluates formulas, starts Office, or fetches linked content.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from .requirements import M1InputError, normalize_source


MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 4096
MAX_CELLS = 200000
EXTRACTOR_VERSION = 'm1-documents-v1'
NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'v': 'urn:schemas-microsoft-com:vml',
    'o': 'urn:schemas-microsoft-com:office:office',
    's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
}


class DocumentExtractionError(M1InputError):
    """A document is unsupported, malformed, or exceeds extraction limits."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _id(kind: str, *values: str) -> str:
    return kind + '-' + _sha('\0'.join(values).encode('utf-8'))[:20]


def _tag(prefix: str, name: str) -> str:
    return '{' + NS[prefix] + '}' + name


def _warning(manifest: dict, code: str, description: str, section_id: str) -> None:
    record = {'code': code, 'description': description, 'section_id': section_id}
    if record not in manifest['warnings']:
        manifest['warnings'].append(record)


def _xml(data: bytes, name: str) -> ET.Element:
    # Removing NULs also catches declarations in UTF-16/32 XML before parsing.
    if re.search(br'<!\s*(DOCTYPE|ENTITY)\b', data.replace(b'\0', b''), re.I):
        raise DocumentExtractionError(f'DTD/entity declarations are forbidden: {name}')
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise DocumentExtractionError(f'Invalid XML in {name}: {error}') from error


class _Package:
    def __init__(self, data: bytes, budget: list[int]):
        self.budget = budget
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            entries = self.zip.infolist()
        except (zipfile.BadZipFile, OSError) as error:
            raise DocumentExtractionError('Invalid OOXML ZIP package') from error
        self.entries = {}
        seen = set()
        try:
            if len(entries) > MAX_MEMBERS:
                raise DocumentExtractionError('ZIP member count exceeds limit')
            total = 0
            for entry in entries:
                name = entry.filename
                pure = PurePosixPath(name)
                if (not name or '\\' in name or ':' in name or '\0' in name
                        or pure.is_absolute() or '..' in pure.parts
                        or str(pure) != name.rstrip('/')):
                    raise DocumentExtractionError(f'Unsafe ZIP member path: {name!r}')
                if name.casefold() in seen:
                    raise DocumentExtractionError(f'Duplicate ZIP member: {name}')
                seen.add(name.casefold())
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise DocumentExtractionError(f'ZIP symbolic links are forbidden: {name}')
                if entry.flag_bits & 1:
                    raise DocumentExtractionError(f'Encrypted ZIP member is unsupported: {name}')
                if entry.file_size > MAX_MEMBER_BYTES:
                    raise DocumentExtractionError(f'ZIP member size exceeds limit: {name}')
                total += entry.file_size
                if not entry.is_dir():
                    self.entries[name] = entry
            if total + budget[0] > MAX_EXPANDED_BYTES:
                raise DocumentExtractionError('Expanded OOXML size exceeds limit')
            budget[0] += total
            # Validate all XML, including parts we cannot interpret, so DTDs
            # cannot hide in an embedded or otherwise unparsed XML part.
            for name in self.entries:
                if name.lower().endswith(('.xml', '.rels')):
                    _xml(self.read(name), name)
        except Exception:
            self.zip.close()
            raise

    def read(self, name: str) -> bytes:
        if name not in self.entries:
            raise DocumentExtractionError(f'Missing OOXML part: {name}')
        try:
            data = self.zip.read(self.entries[name])
        except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as error:
            raise DocumentExtractionError(f'Cannot read OOXML part: {name}') from error
        if len(data) > MAX_MEMBER_BYTES:
            raise DocumentExtractionError(f'ZIP member size exceeds limit: {name}')
        return data

    def xml(self, name: str) -> ET.Element:
        return _xml(self.read(name), name)

    def close(self) -> None:
        self.zip.close()


def _target(part: str, value: str) -> str:
    value = unquote(value)
    if '\\' in value or '\0' in value or ':' in value:
        raise DocumentExtractionError(f'Unsafe OOXML relationship target: {value!r}')
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(part), value))
    if value.startswith('/'):
        resolved = posixpath.normpath(value.lstrip('/'))
    if resolved == '..' or resolved.startswith('../') or resolved in ('', '.'):
        raise DocumentExtractionError(f'OOXML relationship escapes package: {value!r}')
    return resolved


def _relationships(package: _Package, part: str, manifest: dict, section_id: str) -> dict:
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    if name not in package.entries:
        return {}
    result = {}
    for node in package.xml(name):
        rid, target = node.get('Id'), node.get('Target', '')
        if not rid or rid in result:
            raise DocumentExtractionError(f'Missing/duplicate relationship ID in {name}')
        external = node.get('TargetMode', '').lower() == 'external' or bool(urlsplit(target).scheme or urlsplit(target).netloc)
        entry = {'relationship_id': rid, 'type': node.get('Type', ''), 'target': target,
                 'external': external, 'part': part}
        if external:
            manifest['external_targets'].append(entry.copy())
            _warning(manifest, 'external_relationship', f'External target was not fetched: {target}', section_id)
        else:
            entry['target'] = _target(part, target)
        result[rid] = entry
    return result


def _paragraph(node: ET.Element, deleted: bool = False) -> dict:
    runs = []
    run_keys = []

    def visit(item: ET.Element, tracked: bool, strike: bool = False, run=None) -> None:
        tracked = tracked or item.tag in {_tag('w', 'del'), _tag('w', 'moveFrom')}
        if item.tag in {_tag('m', 'oMath'), _tag('m', 'oMathPara')}:
            # OMML structure changes meaning (fractions, scripts, radicals).
            # Text runs inside it are retained as unparsed data, never flattened
            # into active prose as though the formula had been understood.
            return
        if item.tag == _tag('w', 'r'):
            run = item
            for kind in ('strike', 'dstrike'):
                prop = item.find('w:rPr/w:' + kind, NS)
                if prop is not None and prop.get(_tag('w', 'val'), 'true').lower() not in ('0', 'false', 'off', 'no'):
                    strike = True
        elif item.tag == _tag('w', 'rPr'):
            return
        text = None
        if item.tag in {_tag('w', 't'), _tag('w', 'delText')}:
            text = item.text or ''
            tracked = tracked or item.tag == _tag('w', 'delText')
        elif item.tag == _tag('w', 'tab'):
            text = '\t'
        elif item.tag in {_tag('w', 'br'), _tag('w', 'cr')}:
            text = '\n'
        if text:
            key = (run, tracked, strike)
            if run_keys and run_keys[-1] == key:
                runs[-1]['text'] += text
            else:
                run_keys.append(key)
                runs.append({'text': text, 'strike': strike, 'tracked_deleted': tracked})
        for child in item:
            visit(child, tracked, strike, run)

    visit(node, deleted)
    return {'text': ''.join(r['text'] for r in runs),
            'active_text': ''.join(r['text'] for r in runs if not r['strike'] and not r['tracked_deleted']),
            'strike': any(r['strike'] or r['tracked_deleted'] for r in runs), 'runs': runs}


def _table(node: ET.Element, deleted: bool = False) -> dict:
    rows = []
    all_runs = []

    def cell_paragraphs(parent: ET.Element, tracked: bool):
        tracked = tracked or parent.tag in {_tag('w', 'del'), _tag('w', 'moveFrom')}
        if parent.tag == _tag('w', 'p'):
            # The paragraph parser already visits textbox paragraphs. Do not
            # collect them a second time, losing their inherited revision state.
            yield _paragraph(parent, tracked)
        else:
            for child in parent:
                yield from cell_paragraphs(child, tracked)

    for row in node.findall('w:tr', NS):
        cells = []
        for cell in row.findall('w:tc', NS):
            paragraphs = list(cell_paragraphs(cell, deleted))
            runs = [run for paragraph in paragraphs for run in paragraph['runs']]
            all_runs.extend(runs)
            cells.append({'text': '\n'.join(p['text'] for p in paragraphs),
                          'active_text': '\n'.join(p['active_text'] for p in paragraphs),
                          'strike': any(p['strike'] for p in paragraphs), 'runs': runs})
        rows.append(cells)
    return {'rows': rows, 'text': '\n'.join(' | '.join(c['text'] for c in row) for row in rows),
            'active_text': '\n'.join(' | '.join(c['active_text'] for c in row) for row in rows),
            'strike': any(r['strike'] or r['tracked_deleted'] for r in all_runs), 'runs': all_runs}


def _unparsed_inline(node: ET.Element, locator: str, deleted: bool = False) -> list[dict]:
    """Locate unsupported inline content without interpreting or evaluating it."""
    records = []

    def visit(item: ET.Element, location: str, inactive: bool) -> None:
        inactive = inactive or item.tag in {_tag('w', 'del'), _tag('w', 'moveFrom')}
        if item.tag == _tag('w', 'r'):
            for name in ('strike', 'dstrike'):
                prop = item.find('w:rPr/w:' + name, NS)
                if prop is not None and prop.get(_tag('w', 'val'), 'true').lower() not in ('0', 'false', 'off', 'no'):
                    inactive = True
        kind = None
        if item.tag in {_tag('m', 'oMath'), _tag('m', 'oMathPara')}:
            kind = 'omml_formula'
        elif item.tag == _tag('w', 'sym'):
            kind = 'word_symbol'
        if kind:
            fragments = [child.text or '' for child in item.iter()
                         if child.tag in {_tag('m', 't'), _tag('w', 't'), _tag('w', 'delText')}]
            records.append({'part': 'word/document.xml', 'locator': location, 'kind': kind,
                            'text_fragments': fragments, 'text': ''.join(fragments),
                            'raw_xml': ET.tostring(item, encoding='unicode'), 'inactive': inactive})
            # An oMathPara can contain several oMath nodes; preserve the outer
            # object once rather than duplicating its text in nested records.
            return
        for index, child in enumerate(item, 1):
            namespace, _, local = child.tag.rpartition('}')
            prefix = next((key for key, value in NS.items() if namespace == '{' + value), '')
            name = prefix + ':' + local if prefix else child.tag.split('}')[-1]
            visit(child, f'{location}/{name}[{index}]', inactive)

    visit(node, locator, deleted)
    return records


def _heading_styles(package: _Package) -> dict:
    if 'word/styles.xml' not in package.entries:
        return {}
    styles = {s.get(_tag('w', 'styleId')): s for s in package.xml('word/styles.xml').findall('w:style', NS)}
    result = {}

    def level(sid: str, visiting: set) -> int | None:
        if sid in visiting or sid not in styles:
            return None
        item = styles[sid]
        outline = item.find('w:pPr/w:outlineLvl', NS)
        if outline is not None:
            value = outline.get(_tag('w', 'val'), '')
            return int(value) + 1 if value.isdigit() and int(value) < 9 else None
        name = item.find('w:name', NS)
        match = re.search(r'(?:heading|标题)\s*([1-9])', name.get(_tag('w', 'val'), '') if name is not None else sid, re.I)
        if match:
            return int(match.group(1))
        parent = item.find('w:basedOn', NS)
        return level(parent.get(_tag('w', 'val')), visiting | {sid}) if parent is not None else None

    for sid in styles:
        result[sid] = level(sid, set())
    return result


def _heading(node: ET.Element, styles: dict) -> int | None:
    outline = node.find('w:pPr/w:outlineLvl', NS)
    if outline is not None:
        val = outline.get(_tag('w', 'val'), '')
        return int(val) + 1 if val.isdigit() and int(val) < 9 else None
    style = node.find('w:pPr/w:pStyle', NS)
    if style is not None:
        sid = style.get(_tag('w', 'val'), '')
        if sid in styles:
            return styles[sid]
        match = re.fullmatch(r'(?:heading|标题)\s*([1-9])', sid, re.I)
        return int(match.group(1)) if match else None
    return None


def _new_section(source_id: str, title: str, locator: str) -> dict:
    return {'section_id': _id('section', source_id, locator), 'title': title,
            'text': '', 'locator': locator}


def _save(output_dir: Path, category: str, name: str, data: bytes) -> Path:
    # Package paths are never used as output paths.  A digest prevents same-name
    # assets from different parts from overwriting one another.
    leaf = re.sub(r'[^\w.()-]', '_', PurePosixPath(name).name)
    if not leaf or leaf in ('.', '..'):
        leaf = 'asset.bin'
    destination = output_dir / category / (_sha(name.encode())[:12] + '-' + leaf)
    root = output_dir.resolve()
    if not destination.resolve().is_relative_to(root):
        raise DocumentExtractionError('Asset output path escapes output directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return destination.resolve()


def _xlsx(data: bytes, name: str, section_id: str, manifest: dict, budget: list[int]) -> dict:
    package = _Package(data, budget)
    try:
        book = package.xml('xl/workbook.xml')
        rels = _relationships(package, 'xl/workbook.xml', manifest, section_id)
        shared = []
        if 'xl/sharedStrings.xml' in package.entries:
            shared = [''.join(t.text or '' for t in si.iter(_tag('s', 't')))
                      for si in package.xml('xl/sharedStrings.xml').findall('s:si', NS)]
        sheets = []
        count = 0
        for sheet in book.findall('s:sheets/s:sheet', NS):
            relation = rels.get(sheet.get(_tag('r', 'id')))
            if not relation or relation['external']:
                _warning(manifest, 'unreadable_worksheet', f'Worksheet relationship unavailable: {sheet.get("name")}', section_id)
                continue
            cells = []
            # Never use dimension/iter_rows: exported files often lie about the
            # used range, and materializing a sparse bounding box is unbounded.
            tree = package.xml(relation['target'])
            for index, cell in enumerate(tree.findall('.//s:sheetData/s:row/s:c', NS), 1):
                count += 1
                if count > MAX_CELLS:
                    raise DocumentExtractionError('Embedded XLSX cell count exceeds limit')
                kind = cell.get('t', 'n')
                value_node = cell.find('s:v', NS)
                value = value_node.text if value_node is not None else None
                formula_node = cell.find('s:f', NS)
                formula = formula_node.text or '' if formula_node is not None else None
                if kind == 'inlineStr':
                    value = ''.join(t.text or '' for t in cell.findall('s:is//s:t', NS))
                elif kind == 's' and value is not None:
                    try:
                        position = int(value)
                        if position < 0:
                            raise ValueError()
                        value = shared[position]
                    except (IndexError, ValueError) as error:
                        raise DocumentExtractionError(f'Invalid XLSX shared string index: {value}') from error
                elif kind == 'b' and value is not None:
                    if value not in ('0', '1'):
                        raise DocumentExtractionError(f'Invalid XLSX boolean: {value}')
                    value = value == '1'
                reference = cell.get('r') or f'cell-{index}'
                if formula_node is not None and value is None:
                    _warning(manifest, 'formula_without_cached_value',
                             f'{name}/{sheet.get("name")}/{reference}: formula has no cached result; it was not evaluated', section_id)
                cells.append({'reference': reference, 'type': kind, 'value': value, 'formula': formula})
            sheets.append({'name': sheet.get('name', 'Unnamed'), 'part': relation['target'], 'cells': cells})
        for part in package.entries:
            if 'externallink' in part.lower() or 'vbaproject' in part.lower():
                _warning(manifest, 'inactive_embedded_content', f'Embedded active/external content was not executed: {name}/{part}', section_id)
        return {'name': name, 'section_id': section_id, 'sheets': sheets}
    finally:
        package.close()


def _table_text(table: dict) -> str:
    lines = []
    for sheet in table['sheets']:
        lines.append(f'[Embedded XLSX {table["name"]} / {sheet["name"]}]')
        for cell in sheet['cells']:
            if cell['value'] is not None:
                value = json.dumps(cell['value'], ensure_ascii=False) if isinstance(cell['value'], bool) else str(cell['value'])
                lines.append(f'{cell["reference"]}: {value}')
            elif cell['formula'] is not None:
                lines.append(f'{cell["reference"]}: [formula result unavailable; not evaluated]')
    return '\n'.join(lines)


def _references(node: ET.Element, rels: dict, deleted: bool = False) -> list[tuple[str, str, bool]]:
    refs = []

    def visit(item: ET.Element, inactive: bool) -> None:
        inactive = inactive or item.tag in {_tag('w', 'del'), _tag('w', 'moveFrom')}
        if item.tag == _tag('w', 'r'):
            for kind in ('strike', 'dstrike'):
                prop = item.find('w:rPr/w:' + kind, NS)
                if prop is not None and prop.get(_tag('w', 'val'), 'true').lower() not in ('0', 'false', 'off', 'no'):
                    inactive = True
        rid = None
        if item.tag == _tag('a', 'blip'):
            rid = item.get(_tag('r', 'embed')) or item.get(_tag('r', 'link'))
        elif item.tag in {_tag('v', 'imagedata'), _tag('o', 'OLEObject')}:
            rid = item.get(_tag('r', 'id'))
        if rid:
            relation = rels.get(rid)
            refs.append((rid, relation['target'] if relation and not relation['external'] else '', inactive))
        for child in item:
            visit(child, inactive)

    visit(node, deleted)
    return refs


def _docx(data: bytes, output_dir: Path, source_id: str, manifest: dict) -> tuple[list[dict], list[dict]]:
    budget = [0]
    package = _Package(data, budget)
    try:
        document = package.xml('word/document.xml')
        body = document.find('w:body', NS)
        if body is None:
            raise DocumentExtractionError('DOCX has no document body')
        styles = _heading_styles(package)
        current = _new_section(source_id, 'Document context', 'word/document.xml/body')
        sections = [current]
        rels = _relationships(package, 'word/document.xml', manifest, current['section_id'])
        asset_sections: dict[str, list[str]] = {}
        active_asset_sections: dict[str, list[str]] = {}
        attachment_origins: dict[tuple[str, str], list[dict]] = {}
        attachment_gaps = []
        block_lookup = {}
        related_media = set()
        related_embeddings = set()
        references = set()

        def attachment_gap(kind: str, section_id: str, target: str, origin: dict) -> None:
            record = {'gap_id': _id('attachment-gap', source_id, kind, section_id,
                                     origin['part'], origin['locator'], target),
                      'kind': kind, 'part': origin['part'], 'locator': origin['locator'],
                      'section_id': section_id, 'target': target,
                      'relationship_id': origin.get('relationship_id')}
            if origin.get('block_id'):
                record['block_id'] = origin['block_id']
                block_lookup[origin['block_id']]['parse_status'] = 'partial'
            if record not in attachment_gaps:
                attachment_gaps.append(record)
                manifest['unparsed_inputs'].append(record)

        def associate(node: ET.Element, section_id: str, block: dict | None = None,
                      deleted: bool = False, relations: dict | None = None,
                      part: str = 'word/document.xml') -> None:
            relations = rels if relations is None else relations
            for rid, target, inactive in _references(node, relations, deleted):
                origin = {'part': part, 'locator': (block['locator'] if block else part) + '#relationship=' + rid,
                          'relationship_id': rid, 'block_id': block['block_id'] if block else None}
                relation = relations.get(rid)
                if relation is None:
                    _warning(manifest, 'missing_relationship', f'Unresolved relationship: {rid}', section_id)
                    if not inactive:
                        attachment_gap('missing_relationship', section_id, rid, origin)
                elif relation['external']:
                    if not inactive:
                        _warning(manifest, 'external_attachment', f'Active external attachment was not fetched: {relation["target"]}', section_id)
                        attachment_gap('external_attachment', section_id, relation['target'], origin)
                elif not relation['external']:
                    references.add(target)
                    relation_kind = relation['type'].rsplit('/', 1)[-1]
                    is_image = relation_kind == 'image' or target.startswith('word/media/')
                    (related_media if is_image else related_embeddings).add(target)
                    if target not in package.entries:
                        _warning(manifest, 'missing_related_part', f'Related part is missing: {target}', section_id)
                        if not inactive:
                            attachment_gap('missing_related_part', section_id, target, origin)
                    if section_id not in asset_sections.setdefault(target, []):
                        asset_sections[target].append(section_id)
                    if not inactive and section_id not in active_asset_sections.setdefault(target, []):
                        active_asset_sections[target].append(section_id)
                    if not inactive:
                        origins = attachment_origins.setdefault((target, section_id), [])
                        if origin not in origins:
                            origins.append(origin)
                    if inactive:
                        code = 'deleted_embedding' if 'embeddings/' in target else 'deleted_media'
                        _warning(manifest, code, f'Deleted/struck attachment retained only in manifest: {target}', section_id)
                    if block is not None:
                        key = 'images' if is_image else 'embeddings'
                        if target not in block[key]:
                            block[key].append(target)

        def blocks(parent: ET.Element, locator: str, deleted: bool = False):
            for index, node in enumerate(parent, 1):
                tag = node.tag.split('}')[-1]
                loc = f'{locator}/{tag}[{index}]'
                if node.tag in {_tag('w', 'p'), _tag('w', 'tbl')}:
                    yield node, loc, deleted
                elif node.tag in {_tag('w', 'sdt'), _tag('w', 'sdtContent'), _tag('w', 'ins'),
                                   _tag('w', 'del'), _tag('w', 'moveFrom'), _tag('w', 'moveTo'), _tag('w', 'customXml')}:
                    yield from blocks(node, loc, deleted or node.tag in {_tag('w', 'del'), _tag('w', 'moveFrom')})
                elif node.tag != _tag('w', 'sectPr'):
                    manifest['unparsed_inputs'].append({'part': 'word/document.xml', 'locator': loc, 'kind': tag})
                    _warning(manifest, 'unparsed_document_block', f'Unsupported document block at {loc}', current['section_id'])

        for node, locator, deleted in blocks(body, 'word/document.xml/body'):
            kind = 'paragraph' if node.tag == _tag('w', 'p') else 'table'
            content = _paragraph(node, deleted) if kind == 'paragraph' else _table(node, deleted)
            heading = _heading(node, styles) if kind == 'paragraph' else None
            if heading and content['active_text'].strip():
                current = _new_section(source_id, content['active_text'], locator)
                sections.append(current)
            block = {'block_id': _id('block', source_id, locator), 'section_id': current['section_id'],
                     'kind': kind, 'locator': locator, 'heading_level': heading,
                     **content, 'images': [], 'embeddings': []}
            block_lookup[block['block_id']] = block
            associate(node, current['section_id'], block, deleted)
            unparsed = _unparsed_inline(node, locator, deleted)
            if unparsed:
                block['parse_status'] = 'partial'
                for record in unparsed:
                    record.update({'block_id': block['block_id'], 'section_id': current['section_id']})
                    manifest['unparsed_inputs'].append(record)
                    _warning(manifest, 'unsupported_inline_object',
                             f'Unsupported {record["kind"]} at {record["locator"]}; XML/text retained without interpretation or evaluation',
                             current['section_id'])
            manifest['blocks'].append(block)
            if content['active_text'].strip():
                current['text'] += ('\n' if current['text'] else '') + content['active_text']
            for record in unparsed:
                if not record['inactive']:
                    current['text'] += f'\n[Unparsed inline object: {record["kind"]} at {record["locator"]}; inspect extraction manifest]'

        # Do not create an empty synthetic preamble when a heading starts the
        # document. Backgrounds and header/footer art belong to document context.
        if not sections[0]['text'].strip() and len(sections) > 1:
            removed = sections.pop(0)['section_id']
            replacement = sections[0]['section_id']
            for block in manifest['blocks']:
                if block['section_id'] == removed:
                    block['section_id'] = replacement
            for mapping in (asset_sections, active_asset_sections):
                for targets in mapping.values():
                    targets[:] = list(dict.fromkeys(replacement if sid == removed else sid for sid in targets))
            for (target, sid), origins in list(attachment_origins.items()):
                if sid == removed:
                    attachment_origins.setdefault((target, replacement), []).extend(origins)
                    del attachment_origins[(target, sid)]
            for warning in manifest['warnings']:
                if warning['section_id'] == removed:
                    warning['section_id'] = replacement
            for record in manifest['unparsed_inputs']:
                if record.get('section_id') == removed:
                    record['section_id'] = replacement
        fallback = sections[0]['section_id']
        for child in document:
            if child is not body:
                associate(child, fallback)
        # Read relationships for other XML parts too; retain secondary-part
        # images and explicitly mark prose we do not currently interpret.
        for part in package.entries:
            if part.startswith('word/') and part.endswith('.xml') and part not in ('word/document.xml', 'word/styles.xml'):
                tree = package.xml(part)
                secondary_rels = _relationships(package, part, manifest, fallback)
                associate(tree, fallback, relations=secondary_rels, part=part)
                if re.match(r'word/(?:header|footer|footnotes|endnotes|comments)', part):
                    if any((t.text or '').strip() for t in tree.iter(_tag('w', 't'))):
                        manifest['unparsed_inputs'].append({'part': part, 'kind': 'secondary_document_text'})
                        _warning(manifest, 'unparsed_secondary_text', f'Secondary document prose is retained as an extraction gap: {part}', fallback)

        images = []
        media_parts = {p for p in package.entries if p.startswith('word/media/')}
        media_parts.update(p for p in related_media if p in package.entries)
        media_parts.update(p for p in references if p in package.entries and p.lower().endswith(('.png', '.jpg', '.jpeg')))
        embedding_parts = {p for p in package.entries if p.startswith('word/embeddings/')}
        embedding_parts.update(p for p in related_embeddings if p in package.entries)
        embedding_parts.update(p for p in references if p in package.entries and p.lower().endswith('.xlsx'))
        section_map = {s['section_id']: s for s in sections}
        for part in sorted(media_parts):
            content = package.read(part)
            destination = _save(output_dir, 'media', part, content)
            ids = asset_sections.get(part, [fallback])
            active_ids = active_asset_sections.get(part, [] if part in references else [fallback])
            mime = 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if content.startswith(b'\xff\xd8\xff') else 'application/octet-stream'
            record = {'name': part, 'path': str(destination), 'sha256': _sha(content),
                      'bytes': len(content), 'mime_type': mime, 'section_ids': ids,
                      'active_section_ids': active_ids}
            manifest['media'].append(record)
            if part not in references:
                _warning(manifest, 'unreferenced_media', f'Media has no resolved document reference; assigned to document context: {part}', fallback)
            if mime == 'application/octet-stream':
                _warning(manifest, 'unsupported_media', f'Media retained without decoding: {part}', ids[0])
                for sid in active_ids:
                    origins = attachment_origins.get((part, sid), [{'part': part, 'locator': part}])
                    for origin in origins:
                        attachment_gap('unsupported_media', sid, part, origin)
                continue
            for sid in active_ids:
                image_id = _id('image', source_id, part, sid)
                images.append({'image_id': image_id, 'section_id': sid, 'path': str(destination),
                               'sha256': record['sha256'], 'mime_type': mime})
                section_map[sid]['text'] += f'\n[Image attachment: {image_id}; visual content requires model inspection]'
        for part in sorted(embedding_parts):
            content = package.read(part)
            destination = _save(output_dir, 'embeddings', part, content)
            ids = asset_sections.get(part, [fallback])
            active_ids = active_asset_sections.get(part, [] if part in references else [fallback])
            record = {'name': part, 'path': str(destination), 'sha256': _sha(content),
                      'bytes': len(content), 'section_ids': ids, 'active_section_ids': active_ids}
            manifest['embeddings'].append(record)
            if part.lower().endswith('.xlsx'):
                for sid in ids:
                    table = _xlsx(content, part, sid, manifest, budget)
                    table.update({'path': str(destination), 'sha256': record['sha256']})
                    manifest['embedded_tables'].append(table)
                    if sid in active_ids:
                        section_map[sid]['text'] += '\n' + _table_text(table)
            else:
                _warning(manifest, 'unsupported_embedding', f'Embedded object retained without execution or decoding: {part}', ids[0])
                for sid in active_ids:
                    origins = attachment_origins.get((part, sid), [{'part': part, 'locator': part}])
                    for origin in origins:
                        attachment_gap('unsupported_embedding', sid, part, origin)
        for record in attachment_gaps:
            section_map[record['section_id']]['text'] += (
                f'\n[Unparsed attachment: {record["kind"]}; {record["gap_id"]} at {record["locator"]}; inspect extraction manifest]')
        for part in package.entries:
            if 'vbaproject' in part.lower():
                manifest['unparsed_inputs'].append({'part': part, 'kind': 'macro'})
                _warning(manifest, 'inactive_macro', f'Macro was not executed: {part}', fallback)
        return sections, images
    finally:
        package.close()


def _text(data: bytes, extension: str, source_id: str, manifest: dict) -> list[dict]:
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise DocumentExtractionError('Text input must be UTF-8') from error
    sections = [_new_section(source_id, 'Document text', 'line:1')]
    current = sections[0]
    fence = None
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        locator = f'line:{number}'
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line) if extension == '.md' else None
        heading = re.match(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line) if extension == '.md' and fence is None else None
        if marker:
            sequence = marker.group(1)
            if fence is None:
                fence = sequence
            elif sequence[0] == fence[0] and len(sequence) >= len(fence):
                fence = None
        if heading:
            current = _new_section(source_id, heading.group(2), locator)
            sections.append(current)
        block = {'block_id': _id('block', source_id, locator), 'section_id': current['section_id'],
                 'kind': 'line', 'locator': locator, 'text': line, 'active_text': line,
                 'strike': False, 'runs': []}
        manifest['blocks'].append(block)
        current['text'] += line
    kept = [section for section in sections if section['text'].strip()] or sections[:1]
    kept_ids = {section['section_id'] for section in kept}
    for block in manifest['blocks']:
        if block['section_id'] not in kept_ids:
            block['section_id'] = kept[0]['section_id']
    return kept


def extract_document(path, output_dir, *, allow_model_access=False) -> dict:
    """Extract DOCX/UTF-8 Markdown/text and write no model/network side effects.

    ``manifest`` keeps original text, deletions, cell metadata and every media
    asset. ``source`` contains only active text plus labelled attachment/gap
    markers. IDs depend on original bytes and XML/line locators, not output paths.
    The caller persists the returned bundle alongside its analysis artifacts.
    """
    if type(allow_model_access) is not bool:
        raise DocumentExtractionError('allow_model_access must be a boolean')
    path = Path(path).resolve()
    extension = path.suffix.lower()
    if extension not in {'.docx', '.md', '.txt'}:
        raise DocumentExtractionError('Supported inputs are .docx, .md and .txt')
    if not path.is_file():
        raise DocumentExtractionError(f'Document not found: {path}')
    if path.stat().st_size > MAX_FILE_BYTES:
        raise DocumentExtractionError('Document size exceeds limit')
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise DocumentExtractionError('Document size exceeds limit')
    digest = _sha(data)
    source_id = _id('source', digest)
    manifest = {'extractor_version': EXTRACTOR_VERSION, 'file_sha256': digest,
                'fingerprint': {'name': path.name, 'path': str(path), 'sha256': digest, 'bytes': len(data)},
                'blocks': [], 'media': [], 'embeddings': [], 'embedded_tables': [],
                'external_targets': [], 'unparsed_inputs': [], 'warnings': []}
    output_dir = Path(output_dir).resolve()
    if extension == '.docx':
        sections, images = _docx(data, output_dir, source_id, manifest)
    else:
        sections, images = _text(data, extension, source_id, manifest), []
    for section in sections:
        if not section['text'].strip():
            section['text'] = '[No active text in this source section; inspect extraction manifest]'
            _warning(manifest, 'no_active_text', 'Section has no active text; deleted text is retained only in the manifest', section['section_id'])
    source = normalize_source({'source_id': source_id, 'source_version': 'sha256-' + digest,
                               'source_ref': path.name + '#sha256=' + digest,
                               'permission': 'user-approved', 'model_access': allow_model_access,
                               'data_class': 'project-document', 'sections': sections})
    return {'source': source, 'manifest': manifest, 'images': images, 'warnings': manifest['warnings']}
