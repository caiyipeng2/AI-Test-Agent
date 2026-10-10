"""CLI for explicitly selected test devices and evidence-based assertions."""

import argparse
import json
from pathlib import Path

from .config import load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description='Android test tools; no autonomous model is enabled by default.')
    parser.add_argument('--config', help='local JSON device/environment configuration')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('smoke', help='validate package/process startup and collect evidence')
    commands.add_parser('observe', help='capture the current game screenshot and process logs')
    tap = commands.add_parser('tap', help='one input only, without automatic retries')
    tap.add_argument('x', type=int)
    tap.add_argument('y', type=int)
    tap.add_argument('--reason', required=True)
    assess = commands.add_parser('assess-upgrade', help='joint screenshot, level and precise-currency assertion')
    assess.add_argument('input', help='JSON with before/after/expectation')
    assess.add_argument('--output', required=True)
    validate = commands.add_parser('validate-case', help='validate an approved requirement/case contract')
    validate.add_argument('input')
    m0_validate = commands.add_parser('validate-m0', help='validate the device-free M0 registry')
    m0_validate.add_argument('input')
    m0_report = commands.add_parser('report-m0', help='export M0 coverage and traceability JSON')
    m0_report.add_argument('input')
    m0_report.add_argument('--output', required=True)
    m0_report.add_argument('--run-id')
    m1_analyze = commands.add_parser('analyze-m1', help='create device-free source-anchored M1 drafts')
    m1_analyze.add_argument('input')
    m1_analyze.add_argument('--output', required=True)
    m1_review = commands.add_parser('review-m1', help='apply an explicit human review decision to M1 drafts')
    m1_review.add_argument('input')
    m1_review.add_argument('--output', required=True)
    m1_review.add_argument('--reviewer', required=True)
    m1_review.add_argument('--decision', choices=['approved', 'changes_requested', 'rejected'], required=True)
    m1_review.add_argument('--comment', required=True)
    m1_promote = commands.add_parser('promote-m1', help='create a blocked or bound M0 handoff envelope')
    m1_promote.add_argument('input')
    m1_promote.add_argument('--output', required=True)
    m1_import = commands.add_parser('import-m1', help='strictly import a session-generated M1 result')
    m1_import.add_argument('input')
    m1_import.add_argument('--output', required=True)
    m1_record = commands.add_parser('record-m1-result', help='append one device-free manual result to an M1 analysis')
    m1_record.add_argument('analysis')
    m1_record.add_argument('result')
    m1_record.add_argument('--output', required=True)
    m1_gaps = commands.add_parser('report-m1-gaps', help='report M1 coverage gaps and normalize historical defects')
    m1_gaps.add_argument('input', help='M1 analysis JSON')
    m1_gaps.add_argument('--defects', help='historical defects JSON array', default=None)
    m1_gaps.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'assess-upgrade':
            from .upgrade import assess_upgrade
            data = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict):
                raise ValueError('assessment input must be an object')
            result = assess_upgrade(data['before'], data['after'], data['expectation'])
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        elif args.command == 'validate-case':
            from .contracts import validate_case
            errors = validate_case(json.loads(Path(args.input).read_text(encoding='utf-8-sig')))
            result = {'status': 'BLOCKED' if errors else 'PASS', 'errors': errors}
        elif args.command == 'validate-m0':
            from .m0 import M0Registry
            registry = M0Registry.load(args.input)
            result = {'status': 'PASS', 'coverage': registry.coverage_report(), 'errors': []}
        elif args.command == 'report-m0':
            from .m0 import M0Registry
            registry = M0Registry.load(args.input)
            report = registry.report(args.run_id)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            result = {'status': 'PASS', 'output': str(Path(args.output).resolve()), 'coverage': report['coverage']}
        elif args.command == 'analyze-m1':
            from .m1 import RuleBasedAnalyzer
            from .requirements import read_json
            source = read_json(args.input)
            result = RuleBasedAnalyzer().analyze(source)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            result = {'status': 'PASS', 'output': str(Path(args.output).resolve()), 'draft_cases': len(json.loads(Path(args.output).read_text(encoding='utf-8'))['cases'])}
        elif args.command == 'review-m1':
            from .m1 import review_result
            from .requirements import read_json
            draft = read_json(args.input)
            result_data = review_result(draft, {'reviewer': args.reviewer, 'decision': args.decision, 'comment': args.comment})
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding='utf-8')
            result = {'status': 'PASS', 'output': str(Path(args.output).resolve()), 'decision': args.decision}
        elif args.command == 'promote-m1':
            from .m1 import promote_to_m0
            from .requirements import read_json
            draft = read_json(args.input)
            handoff = promote_to_m0(draft)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding='utf-8')
            result = {'status': 'PASS', 'handoff_status': handoff['status'], 'output': str(Path(args.output).resolve()), 'unresolved_bindings': handoff['unresolved_bindings']}
        elif args.command == 'import-m1':
            from .m1 import import_result
            from .requirements import read_json
            imported = import_result(read_json(args.input))
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(imported, ensure_ascii=False, indent=2), encoding='utf-8')
            result = {'status': 'PASS', 'output': str(Path(args.output).resolve()), 'analysis_id': imported.get('analysis_id')}
        elif args.command == 'record-m1-result':
            from .m1_results import record_manual_result
            from .requirements import read_json
            recorded = record_manual_result(read_json(args.analysis), read_json(args.result))
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(recorded, ensure_ascii=False, indent=2), encoding='utf-8')
            result = {
                'status': 'PASS',
                'output': str(Path(args.output).resolve()),
                'analysis_id': recorded.get('analysis_id'),
                'result_count': len(recorded.get('result_history', [])),
            }
        elif args.command == 'report-m1-gaps':
            from .m1_gaps import normalize_gap_report
            from .requirements import read_json
            analysis = read_json(args.input)
            defects = read_json(args.defects) if args.defects else []
            gap_report = normalize_gap_report(analysis, defects)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(gap_report, ensure_ascii=False, indent=2), encoding='utf-8')
            result = {
                'status': 'PASS' if not gap_report['blocked_reason'] else 'BLOCKED',
                'output': str(Path(args.output).resolve()),
                'analysis_id': gap_report['analysis_id'],
                'blocked_reason': gap_report['blocked_reason'],
            }
        else:
            if not args.config:
                parser.error('--config is required for device commands')
            from .runner import SmokeRunner
            runner = SmokeRunner(load_config(args.config))
            result = runner.tap(args.x, args.y, args.reason) if args.command == 'tap' else getattr(runner, args.command)()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}.get(result['status'], 2)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'BLOCKED', 'category': 'INVALID_INPUT', 'reasons': [str(error)]}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
