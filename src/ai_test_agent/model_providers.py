"""Model transports for document analysis, independent of any game adapter."""

from __future__ import annotations

import json
import math
import os
import platform
from pathlib import Path
import shutil
import subprocess
import time
import tomllib
from uuid import uuid4

from .requirements import read_json


class ProviderError(ValueError):
    def __init__(self, category, message):
        self.category = category
        super().__init__(message)


def _budget(config, key, default, low, high):
    value = config.get(key, default)
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ProviderError('INVALID_PROVIDER_CONFIG', f'{key} must be within [{low}, {high}]')
    return value


def _cli_settings():
    """Read identifiers only; authentication remains owned by the local CLI."""
    config_dir = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    path = config_dir / 'config.toml'
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ProviderError('INVALID_PROVIDER_CONFIG', 'Codex configuration is oversized')
        data = tomllib.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError) as error:
        raise ProviderError('INVALID_PROVIDER_CONFIG', 'Unable to read local Codex configuration') from error
    return {'model': data.get('model'), 'mcp_ids': list(data.get('mcp_servers', {})),
            'plugin_ids': list(data.get('plugins', {}))}


def _codex_command(executable):
    resolved = shutil.which(executable) or (executable if Path(executable).is_file() else None)
    if not resolved:
        raise ProviderError('MODEL_UNAVAILABLE', 'Codex CLI executable is unavailable')
    path = Path(resolved)
    if path.suffix.lower() in {'.cmd', '.bat', '.ps1'}:
        if os.name == 'nt':
            arch = 'arm64' if platform.machine().lower() in {'arm64', 'aarch64'} else 'x64'
            package = path.parent / 'node_modules' / '@openai' / 'codex'
            candidates = list(package.glob(f'node_modules/@openai/codex-win32-{arch}/vendor/*/bin/codex.exe'))
            candidates += list((path.parent / 'node_modules' / '@openai').glob(f'codex-win32-{arch}/vendor/*/bin/codex.exe'))
            candidates += list(package.glob('vendor/*-pc-windows-msvc/codex/codex.exe'))
            if candidates:
                # Kill/timeout then targets the inference process itself, not
                # a Node shim which could leave its child running on Windows.
                return [str(candidates[0])]
            raise ProviderError('MODEL_UNAVAILABLE', 'Configure the native Codex executable for Windows')
        # Run the npm entry through Node; no cmd.exe interpolation is involved.
        entry = path.parent / 'node_modules' / '@openai' / 'codex' / 'bin' / 'codex.js'
        node = shutil.which('node.exe') or shutil.which('node')
        if not entry.is_file() or not node:
            raise ProviderError('MODEL_UNAVAILABLE', 'Configure a native Codex executable or install its npm Node entry')
        return [node, str(entry)]
    return [str(path)]


class CodexCLIProvider:
    provider_id = 'codex-cli'
    supports_images = True

    def __init__(self, config):
        allowed = {'provider', 'executable', 'model', 'timeout_seconds', 'max_images', 'max_input_chars', 'reasoning_effort'}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ProviderError('INVALID_PROVIDER_CONFIG', 'Unknown Codex provider configuration field')
        self.timeout = _budget(config, 'timeout_seconds', 900, 1, 3600)
        if any(type(config.get(key, default)) is not int for key, default in (('max_images', 32), ('max_input_chars', 120000))):
            raise ProviderError('INVALID_PROVIDER_CONFIG', 'Image and input character budgets must be integers')
        self.max_images = int(_budget(config, 'max_images', 32, 0, 128))
        self.max_input_chars = int(_budget(config, 'max_input_chars', 120000, 100, 1000000))
        self.reasoning_effort = config.get('reasoning_effort', 'medium')
        if self.reasoning_effort not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}:
            raise ProviderError('INVALID_PROVIDER_CONFIG', 'Unknown reasoning_effort')
        self.settings = _cli_settings()
        self.model_id = config.get('model', self.settings['model'])
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ProviderError('INVALID_PROVIDER_CONFIG', 'A Codex model must be configured locally or in the provider file')
        executable = config.get('executable', 'codex')
        if not isinstance(executable, str) or not executable.strip():
            raise ProviderError('INVALID_PROVIDER_CONFIG', 'executable must be a nonempty string')
        self.command = _codex_command(executable)

    def generate(self, prompt, schema, images, work_dir):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > self.max_input_chars:
            raise ProviderError('INPUT_BUDGET_EXCEEDED', 'Model prompt is empty or exceeds the configured input budget')
        if len(images) > self.max_images:
            raise ProviderError('INPUT_BUDGET_EXCEEDED', 'Image count exceeds the configured budget; no images were silently dropped')
        directory = Path(work_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        schema_path, response_path = directory / f'schema-{token}.json', directory / f'response-{token}.json'
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding='utf-8')
        argv = self.command + ['exec', '--model', self.model_id, '--sandbox', 'read-only',
                '--ephemeral', '--skip-git-repo-check', '--color', 'never', '--json',
                '-c', 'features.shell_tool=false', '-c', 'project_doc_max_bytes=0',
                '-c', f'model_reasoning_effort={json.dumps(self.reasoning_effort)}',
                '-c', 'web_search="disabled"', '--output-schema', str(schema_path),
                '--output-last-message', str(response_path), '--cd', str(directory)]
        for server in self.settings['mcp_ids']:
            if any(char in server for char in '.="\r\n'):
                raise ProviderError('INVALID_PROVIDER_CONFIG', 'A Codex MCP identifier cannot be safely overridden')
            argv += ['-c', f'mcp_servers.{server}.enabled=false']
        for plugin in self.settings['plugin_ids']:
            if any(char in plugin for char in '.="\r\n'):
                raise ProviderError('INVALID_PROVIDER_CONFIG', 'A Codex plugin identifier cannot be safely overridden')
            argv += ['-c', f'plugins.{plugin}.enabled=false']
        for image in images:
            path = Path(image['path']).resolve()
            if not path.is_file():
                raise ProviderError('MISSING_IMAGE', 'An image attachment is unavailable')
            argv += ['--image', str(path)]
        argv += ['-']
        env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', PYTHONIOENCODING='utf-8')
        started = time.monotonic()
        try:
            completed = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=self.timeout, cwd=directory,
                env=env, shell=False)
        except subprocess.TimeoutExpired as error:
            raise ProviderError('MODEL_TIMEOUT', 'Codex analysis exceeded its timeout; no draft was accepted') from error
        except OSError as error:
            raise ProviderError('MODEL_UNAVAILABLE', 'Codex analysis could not be started') from error
        if completed.returncode != 0:
            raise ProviderError('MODEL_CALL_FAILED', f'Codex analysis failed with exit code {completed.returncode}')
        if len(completed.stdout.encode('utf-8')) > 8 * 1024 * 1024:
            raise ProviderError('MODEL_OUTPUT_INVALID', 'Codex event output exceeded its size budget')
        usage = None
        completed_turn = False
        transport_errors = 0
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'turn.failed':
                raise ProviderError('MODEL_CALL_FAILED', 'Codex reported a failed turn')
            if event.get('type') == 'error':
                transport_errors += 1
            item = event.get('item', {})
            if item.get('type') in {'command_execution', 'mcp_tool_call', 'file_change', 'web_search'}:
                raise ProviderError('UNEXPECTED_MODEL_TOOL', 'Document analysis attempted a tool action instead of returning analysis only')
            if event.get('type') == 'turn.completed':
                completed_turn = True
                usage = event.get('usage')
        if transport_errors and not completed_turn:
            raise ProviderError('MODEL_CALL_FAILED', 'Codex did not recover from a reported transport failure')
        try:
            if response_path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError('oversized response')
            payload = read_json(response_path)
            if not isinstance(payload, dict):
                raise ValueError('response must be an object')
        except (OSError, ValueError) as error:
            raise ProviderError('MODEL_OUTPUT_INVALID', 'Codex did not produce a valid strict JSON object') from error
        (directory / f'events-{token}.jsonl').write_text(completed.stdout, encoding='utf-8')
        return {'payload': payload, 'metadata': {'provider_id': self.provider_id, 'model_id': self.model_id,
                'calls': 1, 'elapsed_seconds': round(time.monotonic() - started, 3),
                'usage': usage, 'attached_image_ids': [x['image_id'] for x in images],
                'recovered_transport_errors': transport_errors,
                'reasoning_effort': self.reasoning_effort,
                'response_file': str(response_path), 'schema_file': str(schema_path)}}


def load_provider(config):
    if isinstance(config, (str, Path)):
        config = read_json(config)
    if not isinstance(config, dict):
        raise ProviderError('INVALID_PROVIDER_CONFIG', 'Provider configuration must be an object')
    if config.get('provider') == 'codex-cli':
        return CodexCLIProvider(config)
    if config.get('provider') == 'openai-compatible':
        from .api_provider import OpenAICompatibleProvider
        return OpenAICompatibleProvider(config)
    raise ProviderError('INVALID_PROVIDER_CONFIG', 'Unknown model provider')
