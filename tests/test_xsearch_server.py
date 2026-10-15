import unittest

from factory.mcp_servers.xsearch_server import _format_search_response


class XsearchServerTest(unittest.TestCase):
    def test_format_search_response_preserves_full_url_contract(self):
        payload = {
            "data": [
                {
                    "id": "12345",
                    "author_id": "u1",
                    "created_at": "2026-06-04T08:40:00Z",
                    "text": "MiniMax M3 is live",
                }
            ],
            "includes": {
                "users": [
                    {
                        "id": "u1",
                        "username": "MiniMax_AI",
                        "name": "MiniMax",
                    }
                ]
            },
        }

        text = _format_search_response(payload, query="MiniMax-M3", max_results=10)

        self.assertIn("URL_COPY_CONTRACT", text)
        self.assertIn("URL: https://x.com/MiniMax_AI/status/12345", text)
        self.assertNotIn("Ссылка: https://x.com/MiniMax_AI/status/12345", text)
        self.assertIn("Не заменяй URL на `[ссылка]`", text)


if __name__ == "__main__":
    unittest.main()
