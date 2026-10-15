"""Tests for the OpenCode Go `x-opencode-session` request header.

OpenCode Zen/Go rejects requests without `x-opencode-session` with
`HTTP 400 {"type":"MissingSessionID"}`. Both the main cc-backend request
path (`worker._resolve_cc_backend_auth_headers`) and the memory-review
request path (`memory_review._openai_request_headers`) must send it for
opencode providers only.
"""

import os
import unittest
from unittest.mock import patch

from factory.core.worker import _resolve_cc_backend_auth_headers
from factory.core.memory_review import _openai_request_headers


class WorkerHeaderTest(unittest.TestCase):
    def test_session_header_for_opencode(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-test"}, clear=False):
            headers = _resolve_cc_backend_auth_headers(
                {"provider": "opencode", "api_format": "openai"}, api_format="openai"
            )
        self.assertIsNotNone(headers)
        self.assertIn("x-opencode-session", headers)
        self.assertTrue(headers["x-opencode-session"].strip())
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(headers["Origin"], "https://opencode.ai")

    def test_session_header_for_opencode_go(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-test"}, clear=False):
            headers = _resolve_cc_backend_auth_headers(
                {"provider": "opencode-go", "api_format": "openai"}, api_format="openai"
            )
        self.assertIn("x-opencode-session", headers)

    def test_no_session_header_for_other_providers(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or"}, clear=False):
            headers = _resolve_cc_backend_auth_headers(
                {"provider": "openrouter", "api_format": "openai"}, api_format="openai"
            )
        self.assertNotIn("x-opencode-session", headers or {})


class MemoryReviewHeaderTest(unittest.TestCase):
    def test_session_header_for_opencode(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-test"}, clear=False):
            headers = _openai_request_headers(
                {"provider": "opencode", "api_format": "openai"}, api_format="openai"
            )
        self.assertIn("x-opencode-session", headers)
        self.assertEqual(headers["Authorization"], "Bearer sk-test")

    def test_no_session_header_for_glm(self):
        with patch.dict(os.environ, {"GLM_API_KEY": "sk-glm"}, clear=False):
            headers = _openai_request_headers(
                {"provider": "glm", "api_format": "openai"}, api_format="openai"
            )
        self.assertNotIn("x-opencode-session", headers)


if __name__ == "__main__":
    unittest.main()
