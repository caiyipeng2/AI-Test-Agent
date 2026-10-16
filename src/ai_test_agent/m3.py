"""Device-free review of recorded M3 evidence; never executes an upgrade."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .contracts import make_id
from .requirements import _reject_duplicate_keys, _reject_nonfinite, _parse_float

MAX_RECORDING_BYTES=24*1024*1024


def _atomic_text(path,content):
    temporary=path.with_name('.'+path.name+'.'+uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as handle:
            handle.write(content)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _protected(recording,root,input_path):
    paths={input_path}
    if isinstance(recording,dict):
        for phase in ('before','after'):
            state=recording.get(phase)
            if not isinstance(state,dict): continue
            capture=state.get('capture')
            if not isinstance(capture,dict): continue
            screenshot=capture.get('screenshot')
            if isinstance(screenshot,dict) and isinstance(screenshot.get('path'),str):
                paths.add((root/screenshot['path']).resolve())
    return paths


def _guard_output(targets,protected):
    for target in targets:
        for source in protected:
            if target==source or (target.exists() and source.exists() and target.samefile(source)):
                raise ValueError('M3 output cannot overwrite source recording or evidence')


def write_m3_report(result,path):
    lines=['# M3 Read-only Evidence Review','',
           f"Result: {result['status']} / {result.get('category','unknown')}",
           f"Scope: {result['scope']}; execution: {result['execution']}; evidence mode: {result['evidence_mode']}.",
           f"Business status: {result['business_status']}.",'',
           'This command reviews supplied evidence. It does not execute a device action or establish a real game upgrade PASS.',
           '',f"Assessment: {result['assessment_id']}",f"Input SHA-256: {result['input_sha256']}",'',
           '## Assertions','']
    for item in result.get('assertions',[]):
        lines.append(f"- {item.get('name','unknown')}: {item.get('status','BLOCKED')}")
    lines+=['','## Reasons','']+['- '+str(reason) for reason in result.get('reasons',[])]
    _atomic_text(Path(path),'\n'.join(lines)+'\n')


def assess_recording_file(input_path,output_path):
    source=Path(input_path).resolve(strict=True)
    target=Path(output_path).resolve()
    if target.suffix.lower()!='.json':
        raise ValueError('M3 output must have a .json suffix')
    markdown=target.with_suffix('.md')
    with source.open('rb') as handle:
        raw=handle.read(MAX_RECORDING_BYTES+1)
    if len(raw)>MAX_RECORDING_BYTES:
        raise ValueError('M3 recording exceeds the 24 MiB input budget')
    try:
        recording=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_reject_duplicate_keys,
                             parse_constant=_reject_nonfinite,parse_float=_parse_float)
    except RecursionError as error:
        raise ValueError('M3 recording JSON nesting exceeds parser limits') from error
    if not isinstance(recording,dict):
        raise ValueError('M3 recording must be a JSON object')
    protected=_protected(recording,source.parent,source)
    _guard_output((target,markdown),protected)
    from .m3_observation import assess_recording
    result=assess_recording(recording,evidence_root=source.parent)
    result.update(assessment_id=make_id('assessment'),input_path=str(source),
                  input_sha256=hashlib.sha256(raw).hexdigest(),model_calls=0,
                  output_path=str(target),report_path=str(markdown))
    target.parent.mkdir(parents=True,exist_ok=True)
    _guard_output((target,markdown),protected)
    _atomic_text(target,json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_m3_report(result,markdown)
    return result
