"""One-command raw document to model-generated, source-linked M1 drafts."""

from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from .analysis_protocol import MODEL_OUTPUT_SCHEMA, build_analysis_prompt, compile_analysis
from .documents import extract_document
from .m1 import _result_digest, import_result, promote_to_m0
from .m1_gaps import normalize_gap_report
from .model_providers import ProviderError


def _write_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def _csv_text(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def _report(output, analysis, run):
    source_map = {s['section_id']: s for s in analysis['source']['sections']}
    lines = ['# M1 自动文档分析报告', '',
             f"模型：{run['provider_id']} / {run['model_id']}。",
             f"分析状态：{run['status']}；人工审核：PENDING；游戏执行：NOT_EXECUTED。", '',
             '此文件由文档提取、真实模型调用和确定性校验自动生成。用例仍需人工确认，未执行游戏。', '',
             '## 统计', '',
             f"需求 {len(analysis['requirements'])}；风险 {len(analysis['risks'])}；测试点 {len(analysis['testpoints'])}；用例 {len(analysis['cases'])}；问题 {len(analysis['questions'])}。", '',
             '## 待确认问题', '']
    for question in analysis['questions']:
        lines += [f"- {question.get('priority', '')} {question['description']}（{', '.join(a['section_id'] for a in question['source_anchors'])}）"]
    lines += ['', '## 读取警告与缺口', '']
    for warning in run.get('extraction_warnings', []):
        lines.append(f"- {warning['code']}：{warning['description']}")
    for gap in analysis['gaps']:
        lines.append(f"- {gap['description']}")
    lines += ['', '## 需求与风险', '']
    for requirement in analysis['requirements']:
        risk = next(x for x in analysis['risks'] if x['requirement_id'] == requirement['requirement_id'])
        lines += [f"### {requirement['requirement_id']} {requirement['title']}", '', requirement['rule'], '',
                  f"风险（{risk['severity']}）：{risk['description']}", '',
                  '来源：' + '; '.join(f"{a['section_id']} {a['locator']}" for a in requirement['source_anchors']), '']
    lines += ['## 测试点与用例草稿', '']
    for case in analysis['cases']:
        lines += [f"### {case['case_id']} {case['title']}", '',
                  f"{case.get('priority', '')} / {case.get('category', '')} / {case.get('basis', '')} / DRAFT", '',
                  f"需求：{case['requirement_id']}；风险：{case['risk_id']}；测试点：{case['testpoint_id']}。", '',
                  '来源：' + '; '.join(f"{source_map[a['section_id']]['title']} [{a['locator']}]" for a in case['source_anchors']), '',
                  '前置条件：', ''] + ['- ' + x for x in case['preconditions']]
        lines += ['', '步骤：', ''] + [f'{i}. {x}' for i, x in enumerate(case['steps'], 1)]
        lines += ['', '独立预期：', ''] + ['- ' + x['expected'] for x in case['expectation']['assertions']]
        lines += ['', '证据要求：', ''] + ['- ' + x for a in case['expectation']['assertions'] for x in a['evidence_requirements']]
        lines += ['', '待确认：', ''] + ['- ' + x for x in (case['expectation']['unknowns'] or ['无额外业务未知项；仍需真实负责人审核并绑定环境。'])] + ['']
    lines += ['## 来源索引', '']
    for section in analysis['source']['sections']:
        lines += [f"- {section['section_id']}：{section['title']} / {section['locator']} / SHA-256 {section['section_sha256']}"]
    (output / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    with (output / 'cases.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['用例ID', '需求ID', '风险ID', '测试点ID', '优先级', '类别', '标题', '前置', '步骤', '独立预期', '待确认', '来源', '状态'])
        for case in analysis['cases']:
            writer.writerow([_csv_text(v) for v in [case['case_id'], case['requirement_id'], case['risk_id'], case['testpoint_id'],
                case.get('priority', ''), case.get('category', ''), case['title'], '\n'.join(case['preconditions']),
                '\n'.join(case['steps']), '\n'.join(a['expected'] for a in case['expectation']['assertions']),
                '\n'.join(case['expectation']['unknowns']), '; '.join(a['locator'] for a in case['source_anchors']), 'DRAFT']])


def analyze_document(path, output_dir, provider, *, allow_model_access=False):
    """Run extraction/model/validation without creating approvals or game runs.

    Each invocation requires an empty output directory, preserving previous
    evidence even when the new call fails. Expected failures are returned and
    saved as BLOCKED rather than being replaced with rule-based output.
    """
    output = Path(output_dir).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Output directory must be empty; choose a new run directory')
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    run = {'schema_version': 1, 'status': 'RUNNING', 'stage': 'extract',
           'started_at': datetime.now(timezone.utc).isoformat(),
           'provider_id': getattr(provider, 'provider_id', 'unknown'),
           'model_id': getattr(provider, 'model_id', 'unknown'),
           'human_review_status': 'PENDING', 'game_execution_status': 'NOT_EXECUTED',
           'model_calls': 0, 'completed_model_calls': 0, 'output_directory': str(output)}
    _write_json(output / 'run.json', run)
    try:
        if type(allow_model_access) is not bool:
            raise ValueError('allow_model_access must be an explicit boolean')
        bundle = extract_document(path, output / 'extraction', allow_model_access=allow_model_access)
        _write_json(output / 'source.json', bundle['source'])
        _write_json(output / 'document-manifest.json', bundle['manifest'])
        run['extraction_warnings'] = bundle['warnings']
        run['source_content_sha256'] = bundle['source']['content_sha256']
        run['input_file_sha256'] = bundle['manifest']['file_sha256']
        run['source_sections'] = len(bundle['source']['sections'])
        run['image_count'] = len(bundle['images'])
        if not allow_model_access or not bundle['source']['model_access']:
            raise ProviderError('MODEL_ACCESS_NOT_AUTHORIZED', 'Use --allow-model to authorize this analysis call')
        images = bundle['images']
        if images and not getattr(provider, 'supports_images', False):
            raise ProviderError('MODEL_IMAGES_UNSUPPORTED', 'The configured provider cannot analyze document image attachments')
        for image in images:
            raw = Path(image['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != image['sha256']:
                raise ProviderError('IMAGE_HASH_MISMATCH', 'An extracted attachment changed before model submission')
        run['stage'] = 'model'
        prompt = build_analysis_prompt(bundle)
        run['prompt_sha256'] = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        run['prompt_characters'] = len(prompt)
        run['model_calls'] = 1
        _write_json(output / 'run.json', run)
        response = provider.generate(prompt, MODEL_OUTPUT_SCHEMA, images, output / 'model-call')
        run['completed_model_calls'] = 1
        metadata = response['metadata']
        expected_ids = [image['image_id'] for image in images]
        if metadata.get('attached_image_ids') != expected_ids:
            raise ProviderError('MODEL_ATTACHMENT_EVIDENCE_MISMATCH', 'Provider attachment evidence does not match extracted images')
        run['model_call'] = metadata
        run['model_id'] = metadata.get('model_id', provider.model_id)
        _write_json(output / 'model-output.json', response['payload'])
        run['stage'] = 'validate'
        analysis = compile_analysis(bundle['source'], response['payload'], provider_id=provider.provider_id,
                                    model_id=run['model_id'], images=images)
        analysis['extensions']['auto_document_analysis']['model_call'] = metadata
        analysis['provenance']['output_digest'] = _result_digest(analysis)
        analysis = import_result(analysis)
        coverage = normalize_gap_report(analysis, [])
        handoff = promote_to_m0(analysis)
        _write_json(output / 'analysis.json', analysis)
        _write_json(output / 'coverage.json', coverage)
        _write_json(output / 'm0-handoff.json', handoff)
        _write_json(output / 'human-review-request.json', {
            'schema_version': 1, 'analysis_id': analysis['analysis_id'],
            'source_result_digest': analysis['provenance']['output_digest'],
            'status': 'PENDING', 'reviewer': None, 'decision': None, 'comment': None,
            'case_ids': [case['case_id'] for case in analysis['cases']]})
        run['stage'] = 'complete'
        run['status'] = 'BLOCKED' if analysis['gaps'] or bundle['manifest'].get('unparsed_inputs') else 'PASS'
        run['analysis_status'] = 'PARTIAL_DRAFTS' if run['status'] == 'BLOCKED' else 'DRAFTS_READY'
        run['analysis_id'] = analysis['analysis_id']
        run['counts'] = {name: len(analysis[name]) for name in ('requirements', 'risks', 'testpoints', 'cases', 'questions', 'gaps')}
        run['categories'] = dict(Counter(p['category'] for p in analysis['testpoints']))
        run['m0_handoff_status'] = handoff['status']
        run['elapsed_seconds'] = round(time.monotonic() - started, 3)
        _report(output, analysis, run)
    except (ValueError, OSError, KeyError, TypeError) as error:
        run['status'] = 'BLOCKED'
        run['category'] = getattr(error, 'category', 'DOCUMENT_ANALYSIS_FAILED')
        run['reason'] = str(error)
        run['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (output / 'report.md').write_text(
            '# M1 自动文档分析未完成\n\n状态：BLOCKED。\n\n'
            f"阶段：{run['stage']}。类别：{run['category']}。\n\n{run['reason']}\n\n"
            '未将本次失败替换为规则模板，未批准用例，未执行游戏。\n', encoding='utf-8')
    _write_json(output / 'run.json', run)
    return run
