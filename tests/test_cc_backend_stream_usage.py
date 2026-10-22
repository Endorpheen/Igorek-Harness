import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from factory.core import cc_backend_executor as executor


class _UsageHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(request)
        usage = {'prompt_tokens': 1200, 'completion_tokens': 48,
                 'completion_tokens_details': {'reasoning_tokens': 40}}
        chunks = [
            {'model': 'fixture', 'choices': [{'delta': {'reasoning_content': 'Synthetic reasoning.'}}]},
            {'model': 'fixture', 'choices': [{'delta': {'content': 'Done.'}, 'finish_reason': 'stop'}]},
        ]
        if request['model'] == 'choice-usage':
            chunks[-1]['usage'] = usage
        elif request['model'] != 'no-usage' and request.get('stream_options', {}).get('include_usage'):
            chunks.append({'model': 'fixture', 'choices': [], 'usage': usage})
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


class StreamUsageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _UsageHandler)
        cls.server.requests = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/v1/chat/completions'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)

    def _request(self, payload):
        response, _ = executor._http_post_streaming_openai(
            self.url, payload, {'Content-Type': 'application/json'}, 5)
        return response

    def test_stream_requests_usage_and_counts_reasoning_output(self):
        payload = {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'Fixture.'}]}
        original = copy.deepcopy(payload)
        response = self._request(payload)
        self.assertEqual(payload, original)
        self.assertTrue(self.server.requests[-1]['stream_options']['include_usage'])
        self.assertEqual(response['choices'][0]['message']['content'], 'Done.')
        self.assertEqual(response['choices'][0]['message']['reasoning_content'], 'Synthetic reasoning.')
        normalized = executor._openai_response_to_anthropic(response, set())
        self.assertEqual(normalized['usage'], {'input_tokens': 1200, 'output_tokens': 48})

    def test_stream_preserves_options_and_explicit_usage_opt_out(self):
        payload = {'model': 'fixture', 'messages': [],
                   'stream_options': {'include_usage': False, 'include_obfuscation': False}}
        original = copy.deepcopy(payload)
        response = self._request(payload)
        self.assertEqual(payload, original)
        self.assertEqual(self.server.requests[-1]['stream_options'], original['stream_options'])
        self.assertEqual(executor._openai_response_to_anthropic(response, set())['usage'], {})

    def test_missing_usage_remains_unknown(self):
        response = self._request({'model': 'no-usage', 'messages': []})
        self.assertEqual(executor._openai_response_to_anthropic(response, set())['usage'], {})

    def test_usage_on_a_content_chunk_is_supported(self):
        response = self._request({'model': 'choice-usage', 'messages': []})
        self.assertEqual(response['usage']['prompt_tokens'], 1200)
        self.assertEqual(response['usage']['completion_tokens'], 48)

    def test_normalizer_distinguishes_unknown_partial_and_real_zero_usage(self):
        cases = [
            ({}, {}),
            (None, {}),
            ({'prompt_tokens': 0, 'completion_tokens': 0}, {'input_tokens': 0, 'output_tokens': 0}),
            ({'prompt_tokens': 17}, {'input_tokens': 17}),
            ({'completion_tokens': 9}, {'output_tokens': 9}),
            ({'prompt_tokens': None, 'completion_tokens': 9}, {'output_tokens': 9}),
            ({'prompt_tokens': False, 'completion_tokens': -1}, {}),
        ]
        for usage, expected in cases:
            with self.subTest(usage=usage):
                body = {'choices': [{'message': {'content': 'Fixture.'}, 'finish_reason': 'stop'}],
                        'usage': usage}
                self.assertEqual(executor._openai_response_to_anthropic(body, set())['usage'], expected)

    def test_native_anthropic_usage_is_unchanged(self):
        body = {'content': [{'type': 'text', 'text': 'Fixture.'}],
                'usage': {'input_tokens': 17, 'output_tokens': 9}}
        self.assertIs(executor._openai_response_to_anthropic(body, set()), body)
