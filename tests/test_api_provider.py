import base64
import hashlib
import json
import os
import shutil
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ai_test_agent.api_provider import APIProviderError, OpenAICompatibleProvider



class APIProviderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '.local' / ('api-test-' + uuid4().hex)
        self.root.mkdir(parents=True)
        self.requests = []
        self.status = 200
        self.headers = {'x-request-id': 'req-contract-1'}
        self.delay = 0
        self.raw_response = None
        self.response = {
            'id': 'chatcmpl-contract',
            'model': 'actual-contract-model',
            'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{"units":[]}'}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 4, 'total_tokens': 16},
        }
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                owner.requests.append({'path': self.path, 'headers': dict(self.headers), 'body': json.loads(body)})
                if owner.delay:
                    time.sleep(owner.delay)
                self.send_response(owner.status)
                for key, value in owner.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                data = owner.raw_response if owner.raw_response is not None else json.dumps(owner.response).encode()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.config = {
            'provider': 'openai-compatible',
            'base_url': f'http://127.0.0.1:{self.server.server_port}/v1/',
            'model': 'requested-contract-model',
            'api_key_env': 'AI_TEST_PROVIDER_CONTRACT_KEY',
        }
        self.key = 'contract-key-never-persisted'
        self.env = patch.dict(os.environ, {self.config['api_key_env']: self.key})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.root)

    def provider(self, **changes):
        return OpenAICompatibleProvider({**self.config, **changes})

    def generate(self, provider=None, images=None):
        return (provider or self.provider()).generate(
            'untrusted prompt', {'type': 'object', 'properties': {'units': {'type': 'array'}}, 'required': ['units'],
                                 'additionalProperties': False}, images or [], self.root)

    def image(self, content=b'png-contract-data'):
        image = self.root / 'image.png'
        image.write_bytes(content)
        return {'image_id': 'img-1', 'section_id': 'section-1', 'path': str(image),
                'sha256': hashlib.sha256(content).hexdigest(), 'mime_type': 'image/png'}

    def assert_category(self, category, operation):
        with self.assertRaises(APIProviderError) as caught:
            operation()
        self.assertEqual(caught.exception.category, category)
        self.assertNotIn(self.key, str(caught.exception))
        self.assertNotIn('private-response-text', str(caught.exception))
        return caught.exception

    def test_request_uses_configured_model_strict_schema_and_real_metadata(self):
        provider = self.provider()
        result = self.generate(provider)
        self.assertEqual(provider.provider_id, 'openai-compatible')
        self.assertEqual(provider.model_id, 'requested-contract-model')
        self.assertTrue(provider.supports_images)
        self.assertEqual(provider.max_images, 32)
        self.assertEqual(provider.max_input_chars, 120000)
        self.assertEqual(result['payload'], {'units': []})
        request = self.requests[0]
        self.assertEqual(request['path'], '/v1/chat/completions')
        self.assertEqual(request['headers']['Authorization'], 'Bearer ' + self.key)
        body = request['body']
        self.assertEqual(body['model'], 'requested-contract-model')
        self.assertEqual(body['max_completion_tokens'], 16000)
        self.assertNotIn('max_tokens', body)
        self.assertEqual(body['response_format']['type'], 'json_schema')
        self.assertTrue(body['response_format']['json_schema']['strict'])
        self.assertEqual(body['response_format']['json_schema']['schema']['required'], ['units'])
        self.assertIn('untrusted prompt', json.dumps(body['messages']))
        metadata = result['metadata']
        self.assertEqual(metadata['model'], 'actual-contract-model')
        self.assertEqual(metadata['provider_id'], 'openai-compatible')
        self.assertEqual(metadata['model_id'], 'actual-contract-model')
        self.assertEqual(metadata['attached_image_ids'], [])
        self.assertEqual(metadata['usage'], self.response['usage'])
        self.assertEqual(metadata['request_id'], 'req-contract-1')
        self.assertEqual(metadata['calls'], 1)
        self.assertGreaterEqual(metadata['elapsed_seconds'], 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_image_content_is_encoded_and_bound_to_its_identity(self):
        image = self.image()
        result = self.generate(images=[image])
        self.assertEqual(result['metadata']['attached_image_ids'], ['img-1'])
        content = self.requests[0]['body']['messages'][-1]['content']
        urls = [part['image_url']['url'] for part in content if part['type'] == 'image_url']
        self.assertEqual(urls, ['data:image/png;base64,' + base64.b64encode(b'png-contract-data').decode()])
        text = '\n'.join(part['text'] for part in content if part['type'] == 'text')
        self.assertIn('img-1', text)
        self.assertIn('section-1', text)
        self.assertIn(image['sha256'], text)

    def test_json_object_and_legacy_token_parameter_are_explicit(self):
        self.generate(self.provider(response_format='json_object', token_limit_parameter='max_tokens', max_output_tokens=321))
        body = self.requests[0]['body']
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(body['max_tokens'], 321)
        self.assertNotIn('max_completion_tokens', body)
        self.assertIn('JSON', json.dumps(body['messages']))
        self.assertIn('additionalProperties', json.dumps(body['messages']))

    def test_missing_key_fails_before_network_and_writes_no_files(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assert_category('MODEL_AUTH_MISSING', lambda: self.generate())
        self.assertEqual(self.requests, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_request_id_can_come_from_a_compatible_gateway_response(self):
        self.headers = {}
        self.response['request_id'] = 'gateway-request-123'
        self.assertEqual(self.generate()['metadata'].get('request_id'), 'gateway-request-123')

    def test_config_rejects_secret_fields_and_invalid_values(self):
        for changes in [
            {'api_key': self.key}, {'provider': 'unknown'}, {'model': ''}, {'api_key_env': 'BAD-ENV'},
            {'timeout_seconds': True}, {'timeout_seconds': 0}, {'timeout_seconds': float('inf')},
            {'max_output_tokens': True}, {'max_output_tokens': 0}, {'supports_images': 'true'},
            {'response_format': 'text'}, {'token_limit_parameter': 'both'},
            {'max_images': True}, {'max_images': -1}, {'max_input_chars': True}, {'max_input_chars': 0},
        ]:
            with self.subTest(changes=list(changes)):
                self.assert_category('MODEL_CONFIG_INVALID', lambda: self.provider(**changes))

    def test_urls_reject_plain_external_http_credentials_query_fragment_and_bad_ports(self):
        for url in ['http://example.com/v1', 'https://user:password@example.com/v1',
                    'https://example.com/v1?key=secret', 'https://example.com/v1#fragment',
                    'file:///v1', 'https:///v1', 'https://example.com:bad/v1',
                    'https://example.com/v1\nInjected: value']:
            with self.subTest(url=url):
                self.assert_category('MODEL_CONFIG_INVALID', lambda: self.provider(base_url=url))
        self.provider(base_url='https://api.example.com/v1')
        self.provider(base_url='http://localhost:12345/v1')
        self.provider(base_url='http://[::1]:12345/v1')

    def test_refusal_truncation_and_tool_calls_are_explicit_failures(self):
        for message, finish, category in [
            ({'content': None, 'refusal': 'private-response-text'}, 'stop', 'MODEL_REFUSAL'),
            ({'content': '{}'}, 'length', 'MODEL_OUTPUT_TRUNCATED'),
            ({'content': '{}', 'tool_calls': [{'id': 'private-response-text'}]}, 'tool_calls', 'MODEL_RESPONSE_INVALID'),
            ({'content': '{}'}, 'content_filter', 'MODEL_REFUSAL'),
        ]:
            self.response['choices'] = [{'message': message, 'finish_reason': finish}]
            self.assert_category(category, lambda: self.generate())
        self.assertEqual(len(self.requests), 4)

    def test_invalid_outer_json_and_model_json_are_rejected_without_excerpts(self):
        cases = [
            b'{"model":"first","model":"second"}', b'{"private-response-text":NaN}',
            b'{"private-response-text":1e999}', b'private-response-text', b'\xff',
        ]
        for raw in cases:
            self.raw_response = raw
            self.assert_category('MODEL_RESPONSE_INVALID', lambda: self.generate())
        self.raw_response = None
        for raw in ['{"units":[],"units":[]}', '{"score":NaN}', '{"score":1e999}', '[]',
                    '```json\n{}\n```', 'private-response-text']:
            self.response['choices'][0]['message']['content'] = raw
            self.assert_category('MODEL_RESPONSE_INVALID', lambda: self.generate())

    def test_malformed_response_shapes_fail_without_guessing(self):
        for response in [None, [], {}, {'choices': []},
                         {**self.response, 'choices': [{'message': {'content': {}}, 'finish_reason': 'stop'}]},
                         {**self.response, 'choices': [{'message': {'content': '{}'}}]},
                         {**self.response, 'model': None},
                         {**self.response, 'usage': {'total_tokens': 'private-response-text'}}]:
            self.response = response
            self.assert_category('MODEL_RESPONSE_INVALID', lambda: self.generate())

    def test_http_errors_do_not_retry_or_expose_response_body(self):
        for status in [400, 401, 429, 500]:
            self.status = status
            self.raw_response = ('private-response-text ' + self.key).encode()
            self.assert_category('MODEL_HTTP_ERROR', lambda: self.generate())
        self.assertEqual(len(self.requests), 4)

    def test_success_response_cannot_return_the_environment_credential(self):
        for response in [
            {**self.response, 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'value': self.key})}}]},
            {**self.response, 'usage': {self.key: 1}},
        ]:
            self.response = response
            self.assert_category('MODEL_RESPONSE_INVALID', lambda: self.generate())

    def test_truncated_http_body_is_rejected_even_if_its_json_looks_complete(self):
        self.headers['Content-Length'] = str(len(json.dumps(self.response).encode()) + 10)
        self.assert_category('MODEL_RESPONSE_INVALID', lambda: self.generate())

    def test_redirect_is_rejected_without_forwarding_authorization(self):
        self.status = 307
        self.headers = {'Location': f'http://127.0.0.1:{self.server.server_port}/redirected'}
        self.assert_category('MODEL_HTTP_ERROR', lambda: self.generate())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]['path'], '/v1/chat/completions')

    def test_timeout_is_explicit_and_does_not_retry(self):
        self.delay = 0.15
        self.assert_category('MODEL_TIMEOUT', lambda: self.generate(self.provider(timeout_seconds=0.03)))
        self.assertEqual(len(self.requests), 1)

    def test_unsupported_or_changed_images_fail_before_network(self):
        image = self.image()
        self.assert_category('MODEL_IMAGES_UNSUPPORTED', lambda: self.generate(self.provider(supports_images=False), [image]))
        Path(image['path']).write_bytes(b'changed')
        self.assert_category('MODEL_IMAGE_INVALID', lambda: self.generate(images=[image]))
        self.assertEqual(self.requests, [])

    def test_input_and_image_budgets_fail_without_truncation_or_network(self):
        self.assert_category('MODEL_BUDGET_EXCEEDED', lambda: self.generate(self.provider(max_input_chars=3)))
        image = self.image()
        self.assert_category('MODEL_BUDGET_EXCEEDED', lambda: self.generate(self.provider(max_images=0), [image]))
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
