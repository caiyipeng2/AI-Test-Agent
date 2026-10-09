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
