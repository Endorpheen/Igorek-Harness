import unittest

from factory.api_server_core.tavily import (
    json_payload_has_body_validation_error,
    json_payload_looks_like_error,
    parse_json_payload,
)


class TestParseJsonPayload(unittest.TestCase):
    def test_clean_object_and_array(self):
        self.assertEqual(parse_json_payload('{"results": []}'), {"results": []})
        self.assertEqual(parse_json_payload("[1, 2]"), [1, 2])

    def test_empty_or_invalid_returns_none(self):
        self.assertIsNone(parse_json_payload(""))
        self.assertIsNone(parse_json_payload("not json"))


class TestJsonPayloadLooksLikeError(unittest.TestCase):
    def test_error_variants_detected(self):
        self.assertTrue(json_payload_looks_like_error({"status": 400}))
        self.assertTrue(json_payload_looks_like_error({"code": 500}))
        self.assertTrue(json_payload_looks_like_error({"success": False}))
        self.assertTrue(json_payload_looks_like_error({"errors": ["x"]}))
        self.assertTrue(json_payload_looks_like_error({"error": "x"}))

    def test_normal_payload_not_error(self):
        self.assertFalse(json_payload_looks_like_error({"results": [], "error": None}))
        self.assertFalse(json_payload_looks_like_error({"results": [], "error": False}))
        self.assertFalse(json_payload_looks_like_error([1, 2]))


class TestJsonPayloadHasBodyValidationError(unittest.TestCase):
    def test_body_validation_error_detected(self):
        self.assertTrue(
            json_payload_has_body_validation_error({"detail": [{"loc": ["body", "query"]}]})
        )

    def test_body_validation_error_not_detected_for_other_cases(self):
        self.assertFalse(json_payload_has_body_validation_error(None))
        self.assertFalse(json_payload_has_body_validation_error({"detail": "bad"}))
        self.assertFalse(json_payload_has_body_validation_error({"detail": []}))
        self.assertFalse(
            json_payload_has_body_validation_error({"detail": [None, {"loc": ["query"]}]})
        )


if __name__ == "__main__":
    unittest.main()
