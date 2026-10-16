"""Device-free provider identity inspection and protected report export."""

import hashlib
import json
from pathlib import Path

from .contracts import make_id
from .m3 import MAX_RECORDING_BYTES, _atomic_text, _guard_output
from .requirements import _reject_duplicate_keys, _reject_nonfinite, _parse_float


MAX_PROTECTED_PROOF_BYTES=8*1024*1024
MAX_PROTECTED_EVIDENCE_ITEMS=4096


def _load(raw):
    try:
        return json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=_reject_nonfinite,parse_float=_parse_float)
    except RecursionError as error:
        raise ValueError('Provider recording JSON nesting exceeds parser limits') from error


def _protected_sources(recording,root,source):
    protected={source}
    def add(value,base=root):
        if isinstance(value,str) and value:
            try:
                path=(base/value).resolve()
            except (OSError,ValueError,RuntimeError):
                return None  # One invalid declaration must not hide later evidence.
            protected.add(path)
            return path
        return None
    add(recording.get('dll_path'))
    transport=recording.get('transport')
    if isinstance(transport,dict):
        log=transport.get('log_evidence')
        if isinstance(log,dict): add(log.get('path'))
        proof=add(transport.get('installed_proof'))
        if proof is not None and proof.is_relative_to(root) and proof.is_file():
            try:
                with proof.open('rb') as handle: raw=handle.read(MAX_PROTECTED_PROOF_BYTES+1)
            except OSError:
                return protected  # The verifier returns BLOCKED; the proof remains protected.
            if len(raw)>MAX_PROTECTED_PROOF_BYTES:
                raise ValueError('Provider output refused: installed_proof exceeds the 8 MiB protection budget')
            try:
                data=_load(raw)
            except (ValueError,UnicodeError):
                return protected
            entries=data.get('evidence',[]) if isinstance(data,dict) else []
            if isinstance(entries,list):
                if len(entries)>MAX_PROTECTED_EVIDENCE_ITEMS:
                    raise ValueError('Provider output refused: installed_proof exceeds 4096 evidence declarations')
                for entry in entries:
                    if isinstance(entry,dict):
                        add(entry.get('path'))
                        # Protect both declarations before validation can reject their mismatch.
                        add(entry.get('relative_path'),proof.parent)
    return protected


def inspect_provider_file(input_path,output_path):
    source=Path(input_path).resolve(strict=True)
    target=Path(output_path).resolve()
    if target.suffix.lower()!='.json':
        raise ValueError('Provider output must have a .json suffix')
    report=target.with_suffix('.md')
    with source.open('rb') as handle: raw=handle.read(MAX_RECORDING_BYTES+1)
    if len(raw)>MAX_RECORDING_BYTES:
        raise ValueError('Provider recording exceeds input size budget')
    recording=_load(raw)
    if not isinstance(recording,dict):
        raise ValueError('Provider recording must be a JSON object')
    protected=_protected_sources(recording,source.parent,source)
    _guard_output((target,report),protected)
    from .m3_handshake import inspect_provider_recording,write_provider_report
    result=inspect_provider_recording(recording,evidence_root=source.parent)
    for record in result.get('verified_files',[]):
        if isinstance(record,dict) and isinstance(record.get('path'),str):
            protected.add(Path(record['path']).resolve())
    result.update(assessment_id=make_id('provider_review'),input_path=str(source),
                  input_sha256=hashlib.sha256(raw).hexdigest(),output_path=str(target),
                  report_path=str(report),model_calls=0)
    target.parent.mkdir(parents=True,exist_ok=True)
    _guard_output((target,report),protected)
    _atomic_text(target,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_provider_report(result,report)
    return result
