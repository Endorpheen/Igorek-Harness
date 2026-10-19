import unittest
from unittest import mock

import factory.core.cc_backend_executor as ce


class _FakeResp:
    status = 200
    _fp = None

    def __init__(self, lines):
        self._lines = lines
        self._i = 0

    def readline(self):
        if self._i >= len(self._lines):
            return b""
        line = self._lines[self._i]
        self._i += 1
        return line

    def read(self):
        return b""


class _FakeConn:
    def __init__(self, lines):
        self._lines = lines

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        return _FakeResp(self._lines)

    def close(self):
        pass


class StreamCancelTest(unittest.TestCase):
    def _run(self, lines, cancel_check):
        with mock.patch.object(
            ce.http.client,
            "HTTPConnection",
            lambda *a, **k: _FakeConn(lines),
        ):
            return ce._http_post_streaming_openai(
                "http://127.0.0.1:9/v1/chat/completions",
                {"model": "m", "messages": []},
                {"Content-Type": "application/json"},
                5,
                cancel_check=cancel_check,
            )

    def test_cancel_aborts_stream_and_returns_interrupted(self):
        body, _lat = self._run(
            [b'data: {"choices":[{"delta":{"content":"hi"}}]}\n'],
            lambda: True,
        )
        self.assertTrue(body.get("__interrupted__"))
        self.assertEqual(body.get("error"), "interrupted")

    def test_no_cancel_returns_assembled_response(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ]
        body, _lat = self._run(lines, lambda: False)
        self.assertFalse(body.get("__interrupted__"))
        self.assertEqual(body["choices"][0]["message"]["content"], "Hi")

    def test_missing_terminal_event_is_not_success(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
            b"\n",
        ]
        with mock.patch.object(
            ce.http.client,
            "HTTPConnection",
            lambda *a, **k: _FakeConn(lines),
        ):
            body, _lat = ce._http_post_streaming_openai(
                "http://127.0.0.1:9/v1/chat/completions",
                {"model": "m", "messages": []},
                {},
                5,
            )
        self.assertFalse(body.get("__interrupted__"))
        self.assertEqual(body['error_code'], 'STREAM.INTERRUPTED')
        self.assertNotIn('choices', body)

    def test_partial_tool_call_is_never_exposed(self):
        body, _ = self._run([b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"fixture","type":"function","function":{"name":"Write","arguments":"{\\\"path\\\":\\\"unfinished"}}]}}]}\n', b'\n'], lambda: False)
        self.assertEqual(body['error_code'], 'STREAM.INTERRUPTED')
        self.assertNotIn('choices', body)

    def test_finish_reason_is_a_terminal_marker_without_done(self):
        body, _ = self._run([b'data: {"choices":[{"delta":{"content":"complete"},"finish_reason":"stop"}]}\n', b'\n'], lambda: False)
        self.assertEqual(body['choices'][0]['message']['content'], 'complete')


if __name__ == "__main__":
    unittest.main()
