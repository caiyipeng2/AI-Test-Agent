"""One-shot OpenAI-compatible Chat Completions with environment-only credentials."""

from __future__ import annotations

import base64
import hashlib
from http.client import HTTPException
import ipaddress
import json
import math
import os
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class APIProviderError(ValueError):
    """A safe failure category; messages never include remote or credential text."""

    def __init__(self, category: str, message: str):
        self.category = category
        super().__init__(message)


_CONFIG_KEYS = {
    'provider', 'base_url', 'model', 'api_key_env', 'timeout_seconds',
    'max_output_tokens', 'supports_images', 'response_format', 'token_limit_parameter',
    'max_images', 'max_input_chars',
}
_IMAGE_KEYS = {'image_id', 'section_id', 'path', 'sha256', 'mime_type'}
_MIME_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def _config_error() -> APIProviderError:
    return APIProviderError('MODEL_CONFIG_INVALID', 'Invalid OpenAI-compatible provider configuration.')


def _response_error() -> APIProviderError:
    return APIProviderError('MODEL_RESPONSE_INVALID', 'The model returned an invalid response.')


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key.')
        value[key] = item
    return value


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError('Non-finite JSON number.')
    return value


def _invalid_constant(raw: str) -> Any:
    raise ValueError('Non-finite JSON constant.')


def _strict_json(raw: str | bytes) -> Any:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_float=_finite_float, parse_constant=_invalid_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise _response_error() from None


class _NoRedirect(HTTPRedirectHandler):
    # Credentials must never be forwarded to a redirect destination.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _usage_counts(value: Any) -> bool:
    if isinstance(value, dict):
        return all(isinstance(key, str) and _usage_counts(item) for key, item in value.items())
    return value is None or (type(value) is int and value >= 0)


def _contains_credential(value: Any, credential: str) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str) and credential in current:
            return True
        if isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


class OpenAICompatibleProvider:
    """Call exactly the configured endpoint/model without retries or fallback.

    ``token_limit_parameter`` selects ``max_completion_tokens`` (the default)
    or the legacy ``max_tokens``. ``max_output_tokens`` supplies its value.
    ``work_dir`` is accepted for the provider protocol; this provider writes no
    request, response, image, or credential artifacts.
    """

    provider_id = 'openai-compatible'

    def __init__(self, config: dict):
        if not isinstance(config, dict) or set(config) - _CONFIG_KEYS:
            raise _config_error()
        if config.get('provider') != self.provider_id:
            raise _config_error()
        if not _nonempty(config.get('model')):
            raise _config_error()
        env_name = config.get('api_key_env')
        if not isinstance(env_name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env_name):
            raise _config_error()
        timeout = config.get('timeout_seconds', 180)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise _config_error()
        output_tokens = config.get('max_output_tokens', 16000)
        if type(output_tokens) is not int or output_tokens <= 0:
            raise _config_error()
        max_images = config.get('max_images', 32)
        max_input_chars = config.get('max_input_chars', 120000)
        if (type(max_images) is not int or max_images < 0
                or type(max_input_chars) is not int or max_input_chars <= 0):
            raise _config_error()
        supports_images = config.get('supports_images', True)
        response_format = config.get('response_format', 'json_schema')
        token_parameter = config.get('token_limit_parameter', 'max_completion_tokens')
        if (type(supports_images) is not bool or not isinstance(response_format, str)
                or response_format not in {'json_schema', 'json_object'}
                or not isinstance(token_parameter, str)
                or token_parameter not in {'max_completion_tokens', 'max_tokens'}):
            raise _config_error()
        base_url = config.get('base_url')
        if not _nonempty(base_url) or any(character.isspace() or ord(character) < 32 for character in base_url):
            raise _config_error()
        try:
            parsed = urlsplit(base_url)
            host = parsed.hostname
            port = parsed.port
            if (parsed.scheme not in {'https', 'http'} or not host or parsed.username is not None
                    or parsed.password is not None or parsed.query or parsed.fragment
                    or '?' in base_url or '#' in base_url or port == 0):
                raise _config_error()
            if parsed.scheme == 'http':
                try:
                    loopback = ipaddress.ip_address(host).is_loopback
                except ValueError:
                    loopback = host.lower() == 'localhost'
                if not loopback:
                    raise _config_error()
        except (ValueError, UnicodeError):
            raise _config_error() from None
        self.model_id = config['model']
        self._endpoint = base_url.rstrip('/') + '/chat/completions'
        self._api_key_env = env_name
        self._timeout = timeout
        self._output_tokens = output_tokens
        self.supports_images = supports_images
        self.max_images = max_images
        self.max_input_chars = max_input_chars
        self._response_format = response_format
        self._token_parameter = token_parameter

    def _content(self, prompt: str, images: list[dict]) -> list[dict]:
        if not isinstance(prompt, str) or not isinstance(images, list):
            raise APIProviderError('MODEL_REQUEST_INVALID', 'The model request is invalid.')
        if len(prompt) > self.max_input_chars or len(images) > self.max_images:
            raise APIProviderError('MODEL_BUDGET_EXCEEDED', 'The model request exceeds its configured input budget.')
        if images and not self.supports_images:
            raise APIProviderError('MODEL_IMAGES_UNSUPPORTED', 'The configured model does not support images.')
        content = [{'type': 'text', 'text': prompt}]
        for image in images:
            try:
                if (not isinstance(image, dict) or set(image) != _IMAGE_KEYS
                        or not _nonempty(image['image_id']) or not _nonempty(image['section_id'])
                        or not isinstance(image['mime_type'], str) or image['mime_type'] not in _MIME_TYPES
                        or not isinstance(image['sha256'], str)
                        or not re.fullmatch('[a-f0-9]{64}', image['sha256'])
                        or not isinstance(image['path'], (str, Path))):
                    raise ValueError()
                raw = Path(image['path']).read_bytes()
                if not raw or hashlib.sha256(raw).hexdigest() != image['sha256']:
                    raise ValueError()
                identity = json.dumps({key: image[key] for key in ('image_id', 'section_id', 'sha256')}, ensure_ascii=False)
                content.append({'type': 'text', 'text': 'Image identity: ' + identity})
                encoded = base64.b64encode(raw).decode('ascii')
                content.append({'type': 'image_url', 'image_url': {'url': f'data:{image["mime_type"]};base64,{encoded}'}})
            except (ValueError, TypeError, OSError, UnicodeError):
                raise APIProviderError('MODEL_IMAGE_INVALID', 'A model image could not be verified or read.') from None
        return content

    def generate(self, prompt: str, schema: dict, images: list[dict], work_dir: str | Path) -> dict:
        key = os.environ.get(self._api_key_env)
        if not key or not key.strip():
            raise APIProviderError('MODEL_AUTH_MISSING', 'The configured API key environment variable is missing.')
        if any(ord(character) < 32 or ord(character) > 126 for character in key):
            raise APIProviderError('MODEL_AUTH_INVALID', 'The API key environment variable is invalid.')
        content = self._content(prompt, images)
        if not isinstance(schema, dict):
            raise APIProviderError('MODEL_REQUEST_INVALID', 'The model schema must be an object.')
        system = 'Return exactly one JSON object matching the supplied response schema.'
        response_format: dict[str, Any] = {'type': self._response_format}
        if self._response_format == 'json_schema':
            response_format['json_schema'] = {'name': 'analysis_response', 'strict': True, 'schema': schema}
        else:
            try:
                system += '\nResponse schema: ' + json.dumps(schema, ensure_ascii=False, allow_nan=False)
            except (ValueError, TypeError, RecursionError):
                raise APIProviderError('MODEL_REQUEST_INVALID', 'The model schema is invalid.') from None
        body = {'model': self.model_id, 'messages': [{'role': 'system', 'content': system},
                {'role': 'user', 'content': content}], 'response_format': response_format,
                self._token_parameter: self._output_tokens}
        try:
            encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise APIProviderError('MODEL_REQUEST_INVALID', 'The model request cannot be encoded.') from None
        request = Request(self._endpoint, data=encoded, method='POST',
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                   'Accept': 'application/json'})
        started = time.monotonic()
        try:
            with build_opener(_NoRedirect()).open(request, timeout=self._timeout) as response:
                expected_length = response.length
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                request_id = response.headers.get('x-request-id')
        except HTTPError as error:
            error.close()
            raise APIProviderError('MODEL_HTTP_ERROR', 'The model endpoint returned an HTTP error.') from None
        except (TimeoutError, socket.timeout):
            raise APIProviderError('MODEL_TIMEOUT', 'The model request timed out.') from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise APIProviderError('MODEL_TIMEOUT', 'The model request timed out.') from None
            raise APIProviderError('MODEL_TRANSPORT_ERROR', 'The model endpoint could not be reached.') from None
        except HTTPException:
            raise _response_error() from None
        except (OSError, ValueError):
            raise APIProviderError('MODEL_TRANSPORT_ERROR', 'The model request could not be completed.') from None
        elapsed = time.monotonic() - started
        if len(raw) > _MAX_RESPONSE_BYTES or (expected_length is not None and len(raw) != expected_length):
            raise _response_error()
        result = _strict_json(raw)
        if (not isinstance(result, dict) or not _nonempty(result.get('model'))
                or _contains_credential(result, key)):
            raise _response_error()
        choices = result.get('choices')
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise _response_error()
        choice = choices[0]
        message = choice.get('message')
        if not isinstance(message, dict):
            raise _response_error()
        if message.get('refusal') or choice.get('finish_reason') == 'content_filter':
            raise APIProviderError('MODEL_REFUSAL', 'The model refused the request.')
        if choice.get('finish_reason') == 'length':
            raise APIProviderError('MODEL_OUTPUT_TRUNCATED', 'The model response was truncated.')
        if (choice.get('finish_reason') != 'stop' or message.get('tool_calls') or message.get('function_call')
                or message.get('role', 'assistant') != 'assistant' or not isinstance(message.get('content'), str)):
            raise _response_error()
        payload = _strict_json(message['content'])
        usage = result.get('usage', {})
        if (not isinstance(payload, dict) or not isinstance(usage, dict) or not _usage_counts(usage)
                or _contains_credential(payload, key)):
            raise _response_error()
        request_id = request_id or result.get('request_id')
        if request_id is not None and (not _nonempty(request_id) or key in request_id):
            raise _response_error()
        metadata = {'provider_id': self.provider_id, 'model_id': result['model'],
                    'provider': self.provider_id, 'model': result['model'], 'usage': usage,
                    'elapsed_seconds': elapsed, 'calls': 1,
                    'attached_image_ids': [image['image_id'] for image in images]}
        if request_id is not None:
            metadata['request_id'] = request_id
        return {'payload': payload, 'metadata': metadata}
