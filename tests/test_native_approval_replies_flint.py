"""Reviewed Flint approval reply formats on the canonical adapter."""

import unittest

from factory.core.codex_app_server_adapter import CodexAppServerSession as NativeSession


class CodexAppServerSession(NativeSession):
    """Collect formatted replies without launching or contacting an executor."""

    def __init__(self):
        super().__init__(
            cwd=".",
            model=None,
            model_provider=None,
            env=None,
            developer_instructions=None,
            notification_handler=lambda *args: None,
            server_request_handler=lambda *args: None,
            error_handler=lambda *args: None,
        )
        self.sent = []

    def _send(self, payload):
        self.sent.append(payload)


class TestNativeApprovalRepliesFlint(unittest.TestCase):
    def _resolve(
        self,
        method,
        params=None,
        remember_for_session=False,
        approved=True,
        request_id=1,
    ):
        session = CodexAppServerSession()
        session.resolve_server_request(
            request_id=request_id,
            method=method,
            params=params if params is not None else {},
            remember_for_session=remember_for_session,
            approved=approved,
        )
        return session

    def test_file_change_decision_matrix(self):
        with self.subTest(decision="accept"):
            session = self._resolve(
                method="item/fileChange/requestApproval",
                params={"changes": []},
                remember_for_session=False,
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"decision": "accept"}}],
            )

        with self.subTest(decision="acceptForSession"):
            session = self._resolve(
                method="item/fileChange/requestApproval",
                params={"changes": []},
                remember_for_session=True,
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"decision": "acceptForSession"}}],
            )

        with self.subTest(decision="decline"):
            session = self._resolve(
                method="item/fileChange/requestApproval",
                params={"changes": []},
                remember_for_session=False,
                approved=False,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"decision": "decline"}}],
            )

    def test_command_execution_decision_matrix(self):
        with self.subTest(decision="accept"):
            session = self._resolve(
                method="item/commandExecution/requestApproval",
                params={"command": "echo hi"},
                remember_for_session=False,
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"decision": "accept"}}],
            )

        with self.subTest(decision="acceptForSession"):
            session = self._resolve(
                method="item/commandExecution/requestApproval",
                params={"command": "echo hi"},
                remember_for_session=True,
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"decision": "acceptForSession"}}],
            )

        with self.subTest(decision="decline"):
            session = self._resolve(
                method="item/commandExecution/requestApproval",
                params={"command": "echo hi"},
                remember_for_session=False,
                approved=False,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"decision": "decline"}}],
            )

    def test_permissions_result_format(self):
        params = {"permissions": {"fileRead": ["**/*.py"]}}

        with self.subTest(case="approved_remember_true"):
            session = self._resolve(
                method="item/permissions/requestApproval",
                params=params,
                remember_for_session=True,
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [
                    {
                        "id": 1,
                        "result": {
                            "permissions": {"fileRead": ["**/*.py"]},
                            "scope": "session",
                        },
                    },
                ],
            )

        with self.subTest(case="approved_remember_false"):
            session = self._resolve(
                method="item/permissions/requestApproval",
                params=params,
                remember_for_session=False,
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [
                    {
                        "id": 1,
                        "result": {
                            "permissions": {"fileRead": ["**/*.py"]},
                            "scope": "turn",
                        },
                    },
                ],
            )

        with self.subTest(case="denied"):
            session = self._resolve(
                method="item/permissions/requestApproval",
                params=params,
                remember_for_session=True,
                approved=False,
            )
            self.assertEqual(
                session.sent,
                [
                    {
                        "id": 1,
                        "result": {
                            "permissions": {},
                            "scope": "turn",
                        },
                    },
                ],
            )

    def test_mcp_elicitation_approved_uses_only_default_fields(self):
        with self.subTest(case="defaults_only"):
            session = self._resolve(
                method="mcpServer/elicitation/request",
                params={
                    "requestedSchema": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "default": "Fallback title"},
                            "note": {"type": "string"},
                            "invalid": "not-a-dict",
                        },
                    },
                },
                approved=True,
                request_id="req-42",
            )
            self.assertEqual(
                session.sent,
                [
                    {
                        "id": "req-42",
                        "result": {
                            "action": "accept",
                            "content": {"title": "Fallback title"},
                        },
                    },
                ],
            )

        with self.subTest(case="no_defaults_means_no_content"):
            session = self._resolve(
                method="mcpServer/elicitation/request",
                params={
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"note": {"type": "string"}},
                    },
                },
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"action": "accept"}}],
            )

    def test_mcp_elicitation_denied_or_invalid_schema(self):
        with self.subTest(case="denied"):
            session = self._resolve(
                method="mcpServer/elicitation/request",
                params={
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"a": {"default": 1}},
                    }
                },
                approved=False,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"action": "decline"}}],
            )

        with self.subTest(case="approved_missing_schema"):
            session = self._resolve(
                method="mcpServer/elicitation/request",
                params={},
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"action": "accept"}}],
            )

        with self.subTest(case="approved_invalid_schema"):
            session = self._resolve(
                method="mcpServer/elicitation/request",
                params={"requestedSchema": "not-a-dict"},
                approved=True,
            )
            self.assertEqual(
                session.sent,
                [{"id": 1, "result": {"action": "accept"}}],
            )

    def test_unknown_method_raises_and_sends_nothing(self):
        session = CodexAppServerSession()
        with self.assertRaises(RuntimeError) as ctx:
            session.resolve_server_request(
                request_id=7,
                method="item/unknown/requestApproval",
                params={},
                remember_for_session=False,
                approved=True,
            )
        self.assertEqual(
            str(ctx.exception),
            "unsupported app-server approval method: item/unknown/requestApproval",
        )
        self.assertEqual(session.sent, [])


if __name__ == "__main__":
    unittest.main()
