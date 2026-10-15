from __future__ import annotations

import base64
import http.client
import io
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import urlopen
from unittest.mock import patch
from xml.sax.saxutils import escape as xml_escape

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID

from factory.web_ui.server import (
    _COOKIE,
    _OPENCODE_KEY_CACHE,
    _OPENCODE_USAGE_CACHE,
    _OPENCODE_USAGE_URL,
    _OpenCodeUsageError,
    _Server,
    _bridge_base,
    _certificate_paths,
    _fetch_opencode_usage,
    _opencode_api_key,
    run_server,
)


_TOKEN = "web-ui-test-token"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEB_STATIC = _REPO_ROOT / "factory" / "web_ui" / "static"
_NODE = shutil.which("node")
_CHROME = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
_BINARY_FILE_BYTES = b"PK\x03\x04\x00\x01\x02\xff\xfe\x00\x80binary\x00payload\xf0\x9f\x93\x84"


def _xlsx_fixture_bytes() -> bytes:
    """A small genuine Excel workbook: sheets, empty cells, formulas, and formats.

    Compiled manually via zipfile to verify both cached and uncached formulas (SheetJS-writer does not write such cells).
    """

    def inline(ref: str, value: str, style: int | None = None) -> str:
        style_attr = f' s="{style}"' if style is not None else ""
        return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t xml:space="preserve">{xml_escape(value)}</t></is></c>'

    def number(ref: str, value: float | int, style: int | None = None) -> str:
        style_attr = f' s="{style}"' if style is not None else ""
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'

    def formula(ref: str, expression: str, cached: int | None = None) -> str:
        if cached is None:
            return f'<c r="{ref}"><f>{xml_escape(expression)}</f></c>'
        return f'<c r="{ref}"><f>{xml_escape(expression)}</f><v>{cached}</v></c>'

    def sheet(rows: list[str], dimension: str) -> str:
        return (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<dimension ref="{dimension}"/><sheetData>' + "".join(rows) + "</sheetData></worksheet>"
        )

    data_rows = [
        '<row r="1">'
        + inline("A1", "Привет")
        + number("B1", 0.25, 2)
        + number("C1", 45000, 1)
        + number("D1", 1234.5, 3)
        + inline("E1", "<img src=x onerror=alert(1)>")
        + formula("F1", "A2*2", 10)
        + formula("G1", "A2+5")
        + inline("H1", "Ёж")
        + "</row>",
        '<row r="2">' + inline("B2", "второй") + "</row>",
        '<row r="5">' + inline("H5", "конец") + "</row>",
    ]
    offset_rows = ['<row r="4">' + inline("D4", "центр") + "</row>"]

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for index in range(1, 5)
        )
        + '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    sheet_names = ["Данные", "Лист2", "Большой", "Смещение"]
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        + "".join(
            f'<sheet name="{xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
            for index, name in enumerate(sheet_names, start=1)
        )
        + "</sheets></workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, 5)
        )
        + '<Relationship Id="rId5" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/></Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="3"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/>'
        '<numFmt numFmtId="165" formatCode="0%"/>'
        '<numFmt numFmtId="166" formatCode="&quot;$&quot;#,##0.00"/></numFmts>'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="4">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        "</cellXfs>"
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", sheet(data_rows, "A1:H5"))
        archive.writestr("xl/worksheets/sheet2.xml", sheet(['<row r="1">' + inline("A1", "второй лист") + "</row>"], "A1:A1"))
        archive.writestr("xl/worksheets/sheet3.xml", sheet(['<row r="1">' + inline("A1", "огромный") + "</row>"], "A1:CV2000"))
        archive.writestr("xl/worksheets/sheet4.xml", sheet(offset_rows, "C3:E5"))
    return buffer.getvalue()


_FIXTURE_ROW_EXPECTED = [
    "Привет", "25%", "2023-03-15", "$1,234.50",
    "<img src=x onerror=alert(1)>", "10", "=A2+5", "Ёж",
]


class _FakeBridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    records: list[dict] = []
    lock = threading.Lock()

    def log_message(self, format, *args):
        return

    def _record(self):
        with self.lock:
            self.records.append(
                {
                    "method": self.command,
                    "path": urlsplit(self.path).path,
                    "query": parse_qs(urlsplit(self.path).query),
                    "authorization": self.headers.get("Authorization"),
                    "body": self._read_body(),
                }
            )
        return self.records[-1]

    def _read_body(self):
        size = int(self.headers.get("Content-Length") or 0)
        if not size:
            return None
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        record = self._record()
        if record["path"] == "/mobile/v1/devices":
            self._json(
                200,
                {
                    "current_device_id": "device-123",
                    "devices": [{"device_id": "device-123", "name": "Test browser"}],
                },
            )
            return
        if record["path"] == "/mobile/v1/sessions":
            self._json(200, {"sessions": [{"session_id": "session-123"}], "total": 137})
            return
        if record["path"] == "/mobile/v1/session-options":
            self._json(200, {"profiles": ["cc-local"], "workdirs": ["/tmp/project"]})
            return
        if record["path"] == "/mobile/v1/sessions/session-123":
            self._json(200, {"session_id": "session-123", "events": []})
            return
        if record["path"] == "/mobile/v1/sessions/session-123/activity":
            self._json(200, {"processes": [{"id": "cmd-test", "kind": "command", "label": "fixture"}]})
            return
        if record["path"] == "/mobile/v1/sessions/session-123/cursor":
            self._json(200, {"seq": 42})
            return
        if record["path"] == "/mobile/v1/sessions/session-123/subagents":
            self._json(
                200,
                {
                    "subagents": [
                        {
                            "subagent_id": "sub_1",
                            "parent_task_id": "task-123",
                            "tool_use_id": "call-delegate",
                            "status": "running",
                            "goal_title": "Разобрать логи",
                            "tool_calls_count": 2,
                        }
                    ],
                    "active": 1,
                    "total": 1,
                    "truncated": False,
                },
            )
            return
        if record["path"] == "/mobile/v1/sessions/session-123/events":
            self._json(
                200,
                {
                    "events": [
                        {
                            "seq": 8,
                            "kind": "tool.request_batch",
                            "payload": {
                                "round": 2,
                                "tools": [
                                    {
                                        "id": "call-1",
                                        "name": "update_plan",
                                        "input": {
                                            "explanation": "план",
                                            "plan": [
                                                {"step": "Шаг один", "status": "completed"},
                                                {"step": "Шаг два", "status": "in_progress"},
                                            ],
                                        },
                                    }
                                ],
                            },
                        },
                        {
                            "seq": 9,
                            "kind": "model.round_response",
                            "payload": {"round": 2, "usage": {"input_tokens": 120, "output_tokens": 30}},
                        },
                    ]
                },
            )
            return
        if record["path"] == "/mobile/v1/approvals":
            self._json(
                200,
                {
                    "approvals": [
                        {
                            "kind": "tool",
                            "request_id": "req-1",
                            "task_id": "task-123",
                            "session_id": "session-123",
                            "tool": "Bash",
                            "target": "rm -rf build",
                            "reason": "опасная команда",
                            "dangerous": True,
                        }
                    ]
                },
            )
            return
        if record["path"] == "/mobile/v1/sessions/session-123/uploads/" + "a" * 32:
            data = b"\x89PNG\r\n\x1a\nfixture"
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if record["path"] == "/mobile/v1/sessions/session-123/files/file-1":
            data = b"<html><body>artifact</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''artifact.html")
            self.end_headers()
            self.wfile.write(data)
            return
        if record["path"] == "/mobile/v1/sessions/session-123/files/file-xlsx":
            # Bytes containing NUL or invalid UTF-8: the download must return them exactly as-is.
            data = _BINARY_FILE_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''book.xlsx")
            self.end_headers()
            self.wfile.write(data)
            return
        if record["path"] == "/mobile/v1/external-sessions":
            self._json(
                200,
                {
                    "sessions": [
                        {
                            "source": "claude",
                            "session_id": "sess-1",
                            "title": "Первая сессия",
                            "message_count": 3,
                        }
                    ],
                    "counts": {"claude": 1, "opencode": 0, "codex": 0},
                    "total": 1,
                    "scanned_at": 1234,
                },
            )
            return
        if record["path"] == "/mobile/v1/external-sessions/claude/sess-1":
            self._json(
                200,
                {
                    "source": "claude",
                    "session_id": "sess-1",
                    "messages": [{"role": "user", "text": "привет", "ts": 1, "kind": "text"}],
                    "total_messages": 1,
                    "next_offset": None,
                },
            )
            return
        if record["path"] == "/mobile/v1/events":
            data = b"event: session\ndata: {\"seq\":8}\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        record = self._record()
        if self._activity_stop_fixture(record):
            return
        if record["path"] == "/mobile/v1/pair/complete":
            code = (record["body"] or {}).get("code")
            if code != "ABCDEFG":
                self._json(401, {"error": "Код сопряжения не найден"})
                return
            self._json(201, {"token": _TOKEN, "name": "Test browser"})
            return
        if record["path"] == "/mobile/v1/sessions":
            self._json(201, {"session_id": "session-123"})
            return
        if record["path"] == "/mobile/v1/sessions/session-123/messages":
            self._json(202, {"task_id": "task-123", "queued": True})
            return
        if record["path"] == "/mobile/v1/sessions/session-123/messages/msg-1/send-now":
            self._json(200, {"task_id": "task-123", "delivered": True})
            return
        if record["path"] == "/mobile/v1/sessions/session-123/messages/msg-1/cancel":
            self._json(200, {"task_id": "task-123", "cancelled": True})
            return
        if record["path"] == "/mobile/v1/sessions/session-123/cancel":
            self._json(200, {"cancelled": True, "mode": (record["body"] or {}).get("mode")})
            return
        if record["path"] == "/mobile/v1/sessions/session-123/subagents/sub_1/cancel":
            self._json(200, {"subagent_id": "sub_1", "cancelled": True, "status": "cancelling"})
            return
        if record["path"] == "/mobile/v1/tasks/task-123/approvals/req-1":
            self._json(
                200,
                {
                    "task_id": "task-123",
                    "request_id": "req-1",
                    "decision": (record["body"] or {}).get("decision"),
                },
            )
            return
        if record["path"] == "/mobile/v1/external-sessions/claude/sess-1/import":
            self._json(
                201,
                {
                    "session": {"session_id": "session-123"},
                    "imported_messages": 3,
                    "skipped_messages": 0,
                    "duplicate": False,
                },
            )
            return
        self._json(404, {"error": "not found"})

    def _activity_stop_fixture(self, record):
        if record["path"] == "/mobile/v1/sessions/session-123/activity/cmd-test/stop":
            self._json(200, {"id": "cmd-test", "requested": True})
            return True
        return False

    def do_DELETE(self):
        record = self._record()
        if record["path"] == "/mobile/v1/devices/device-123":
            self._json(200, {"revoked": True})
            return
        self._json(404, {"error": "not found"})


class WebUiServerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        _FakeBridgeHandler.records = []
        self.bridge = ThreadingHTTPServer(("127.0.0.1", 0), _FakeBridgeHandler)
        self.bridge_thread = threading.Thread(target=self.bridge.serve_forever, daemon=True)
        self.bridge_thread.start()
        self.server = _Server(self._unused_port(), f"http://127.0.0.1:{self.bridge.server_port}")
        self.port = int(self.server.server_port)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.bridge.shutdown()
        self.bridge.server_close()
        self.bridge_thread.join(timeout=5)
        self._env.stop()
        self._tmp.cleanup()

    @staticmethod
    def _unused_port() -> int:
        return 0

    def _assert_access_checks_since(self, before: int, count: int) -> None:
        records = _FakeBridgeHandler.records[before:]
        self.assertEqual(len(records), count)
        for record in records:
            self.assertEqual((record['method'], record['path']), ('GET', '/mobile/v1/devices'))
            self.assertEqual(record['authorization'], f'Bearer {_TOKEN}')

    def _request(self, method: str, path: str, *, body: dict | None = None, cookie: str = "", headers: dict | None = None):
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if cookie:
            request_headers["Cookie"] = cookie
        if method == "POST":
            request_headers.setdefault("Origin", f"https://127.0.0.1:{self.port}")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        connection.request(method, path, body=payload, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.headers), data)
        connection.close()
        return result

    def _pair(self) -> str:
        status, headers, data = self._request("POST", "/api/pair", body={"code": "ABCDEFG"})
        self.assertEqual(status, 201, data)
        self.assertNotIn(_TOKEN.encode(), data)
        cookie = headers["Set-Cookie"]
        self.assertIn(f"{_COOKIE}={_TOKEN};", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        return f"{_COOKIE}={_TOKEN}"


class BridgeAddressTest(unittest.TestCase):
    def test_accepts_only_loopback_origins_without_extra_url_parts(self):
        self.assertEqual(_bridge_base("http://127.0.0.1:47821/"), "http://127.0.0.1:47821")
        self.assertEqual(_bridge_base("https://localhost:47821"), "https://localhost:47821")
        self.assertEqual(_bridge_base("http://[::1]:47821"), "http://[::1]:47821")
        for value in (
            "http://192.168.1.10:47821",
            "https://bridge.example:47821",
            "http://user@127.0.0.1:47821",
            "http://127.0.0.1:47821/mobile",
            "http://127.0.0.1:47821?debug=1",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _bridge_base(value)


class WebUiTlsTest(unittest.TestCase):
    def test_run_server_serves_page_over_tls_on_loopback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"IGOREK_FACTORY_HOME": directory}
        ):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            args = type("Args", (), {
                "data_dir": "",
                "port": port,
                "bridge_url": "http://127.0.0.1:47821",
                "tls_cert": "",
                "tls_key": "",
            })()
            observed = {}

            def serve(server, poll_interval):
                observed["host"] = server.server_address[0]
                worker = threading.Thread(
                    target=ThreadingHTTPServer.serve_forever,
                    args=(server,),
                    kwargs={"poll_interval": 0.05},
                    daemon=True,
                )
                worker.start()
                try:
                    context = ssl._create_unverified_context()
                    with urlopen(f"https://127.0.0.1:{port}/", context=context, timeout=5) as response:
                        observed["status"] = response.status
                        observed["body"] = response.read()
                finally:
                    server.shutdown()
                    worker.join(timeout=5)

            with patch("factory.web_ui.server._Server.serve_forever", new=serve):
                result = run_server(args)

            self.assertEqual(result, 0)
            self.assertEqual(observed["host"], "127.0.0.1")
            self.assertEqual(observed["status"], 200)
            self.assertIn("Рабочее место".encode(), observed["body"])

    def test_certificate_is_localhost_only_and_private_key_is_restricted(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"IGOREK_FACTORY_HOME": directory}
        ):
            cert_path, key_path, fingerprint = _certificate_paths()
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            usages = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value

            self.assertIn("localhost", names.get_values_for_type(x509.DNSName))
            self.assertIn("127.0.0.1", [str(value) for value in names.get_values_for_type(x509.IPAddress)])
            self.assertIn(ExtendedKeyUsageOID.SERVER_AUTH, usages)
            self.assertEqual(len(fingerprint), 64)
            self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(cert_path.parent.stat().st_mode & 0o777, 0o700)


class WebUiHttpTest(WebUiServerTestCase):
    def test_server_binds_only_to_ipv4_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_static_page_has_browser_security_headers(self):
        status, headers, data = self._request("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn("Рабочее место".encode(), data)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_host_and_origin_checks_reject_untrusted_requests(self):
        status, _, _ = self._request("GET", "/api/status", headers={"Host": "evil.example"})
        self.assertEqual(status, 421)

        status, _, _ = self._request(
            "POST", "/api/pair", body={"code": "ABCDEFG"}, headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(status, 403)

        status, _, _ = self._request(
            "POST",
            "/api/pair",
            body={"code": "ABCDEFG"},
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(_FakeBridgeHandler.records, [])

    def test_pairing_keeps_bridge_token_out_of_response_and_sets_protected_cookie(self):
        cookie = self._pair()

        self.assertEqual(cookie, f"{_COOKIE}={_TOKEN}")
        record = _FakeBridgeHandler.records[0]
        self.assertEqual(record["path"], "/mobile/v1/pair/complete")
        self.assertEqual(record["body"], {"code": "ABCDEFG", "name": "Igorek local web", "platform": "web"})
        self.assertIsNone(record["authorization"])

    def test_proxy_uses_cookie_token_only_for_loopback_bridge(self):
        cookie = self._pair()

        status, _, data = self._request("GET", "/api/sessions", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(data),
            {"sessions": [{"session_id": "session-123"}], "total": 137},
        )
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")

    def test_sessions_total_is_forwarded_without_changing_limit(self):
        cookie = self._pair()

        status, _, data = self._request("GET", "/api/sessions", cookie=cookie)

        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["total"], 137)
        self.assertEqual(payload["sessions"], [{"session_id": "session-123"}])
        self.assertEqual(_FakeBridgeHandler.records[-1]["query"], {"limit": ["100"]})

    def test_options_session_creation_and_message_are_proxied(self):
        cookie = self._pair()

        status, _, data = self._request("GET", "/api/session-options", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"profiles": ["cc-local"], "workdirs": ["/tmp/project"]})

        session_body = {"profile": "cc-local", "cwd": "/tmp/project", "idempotency_key": "create-1"}
        status, _, data = self._request("POST", "/api/sessions", body=session_body, cookie=cookie)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data), {"session_id": "session-123"})
        self.assertEqual(_FakeBridgeHandler.records[-1]["body"], session_body)

        message_body = {"text": "Привет", "idempotency_key": "message-1"}
        status, _, data = self._request(
            "POST", "/api/sessions/session-123/messages", body=message_body, cookie=cookie
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(data), {"task_id": "task-123", "queued": True})
        self.assertEqual(_FakeBridgeHandler.records[-1]["body"], message_body)
        self.assertEqual(_FakeBridgeHandler.records[-1]["authorization"], f"Bearer {_TOKEN}")

    def test_message_send_now_is_proxied_with_body(self):
        cookie = self._pair()

        status, _, data = self._request(
            "POST",
            "/api/sessions/session-123/messages/msg-1/send-now",
            body={"task_id": "task-123"},
            cookie=cookie,
        )

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["delivered"])
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(
            (record["method"], record["path"]),
            ("POST", "/mobile/v1/sessions/session-123/messages/msg-1/send-now"),
        )
        self.assertEqual(record["body"], {"task_id": "task-123"})

    def test_message_cancel_is_proxied_with_body(self):
        cookie = self._pair()

        status, _, data = self._request(
            "POST",
            "/api/sessions/session-123/messages/msg-1/cancel",
            body={"task_id": "task-123"},
            cookie=cookie,
        )

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["cancelled"])
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(
            (record["method"], record["path"]),
            ("POST", "/mobile/v1/sessions/session-123/messages/msg-1/cancel"),
        )
        self.assertEqual(record["body"], {"task_id": "task-123"})

    def test_invalid_message_id_is_rejected_before_bridge_request(self):
        cookie = self._pair()
        bridge_call_count = len(_FakeBridgeHandler.records)

        status, _, data = self._request(
            "POST",
            "/api/sessions/session-123/messages/bad%20id/send-now",
            body={"task_id": "task-123"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertIn("сообщения", json.loads(data)["error"])

        status, _, data = self._request(
            "POST",
            "/api/sessions/session-123/messages/bad%20id/cancel",
            body={"task_id": "task-123"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertIn("сообщения", json.loads(data)["error"])

        self.assertEqual(len(_FakeBridgeHandler.records), bridge_call_count)

    def test_missing_pairing_cookie_does_not_call_bridge(self):
        status, headers, _ = self._request("GET", "/api/sessions")

        self.assertEqual(status, 401)
        self.assertEqual(_FakeBridgeHandler.records, [])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])

    def test_logout_revokes_current_bridge_device_and_clears_cookie(self):
        cookie = self._pair()

        status, headers, data = self._request("POST", "/api/logout", body={}, cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"logged_out": True})
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(
            [(item["method"], item["path"]) for item in _FakeBridgeHandler.records[-2:]],
            [("GET", "/mobile/v1/devices"), ("DELETE", "/mobile/v1/devices/device-123")],
        )
        self.assertEqual(_FakeBridgeHandler.records[-1]["authorization"], f"Bearer {_TOKEN}")

    def test_events_stream_forwards_bridge_event_and_authenticates(self):
        cookie = self._pair()

        status, headers, data = self._request(
            "GET", "/api/sessions/session-123/events?after=7", cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertIn(b"event: session\ndata: {\"seq\":8}\n\n", data)
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(record["query"], {"after": ["7"], "session_id": ["session-123"]})
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")

    def test_invalid_event_cursor_is_rejected_before_bridge_request(self):
        cookie = self._pair()
        bridge_call_count = len(_FakeBridgeHandler.records)

        status, _, data = self._request(
            "GET", "/api/sessions/session-123/events?after=wrong", cookie=cookie
        )

        self.assertEqual(status, 400)
        self.assertIn("числом", json.loads(data)["error"])
        self.assertEqual(len(_FakeBridgeHandler.records), bridge_call_count)

    def test_session_cursor_comes_from_bridge_journal(self):
        cookie = self._pair()

        status, _, data = self._request("GET", "/api/sessions/session-123/cursor", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"seq": 42})
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual((record["method"], record["path"]), ("GET", "/mobile/v1/sessions/session-123/cursor"))
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")

    def test_session_cursor_requires_pairing(self):
        status, _, _ = self._request("GET", "/api/sessions/session-123/cursor")

        self.assertEqual(status, 401)
        self.assertEqual(_FakeBridgeHandler.records, [])

    def test_pending_approvals_are_listed_for_session(self):
        cookie = self._pair()

        status, _, data = self._request("GET", "/api/approvals?session_id=session-123", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["approvals"][0]["request_id"], "req-1")
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(record["path"], "/mobile/v1/approvals")
        self.assertEqual(record["query"], {"session_id": ["session-123"]})

    def test_approval_decision_is_sent_with_request_id(self):
        cookie = self._pair()

        status, _, data = self._request(
            "POST",
            "/api/tasks/task-123/approvals/req-1",
            body={"decision": "allow_once"},
            cookie=cookie,
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["decision"], "allow_once")
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(
            (record["method"], record["path"]),
            ("POST", "/mobile/v1/tasks/task-123/approvals/req-1"),
        )
        self.assertEqual(record["body"], {"decision": "allow_once"})
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")

    def test_unknown_approval_decision_is_rejected_before_bridge_request(self):
        cookie = self._pair()
        bridge_call_count = len(_FakeBridgeHandler.records)

        status, _, data = self._request(
            "POST",
            "/api/tasks/task-123/approvals/req-1",
            body={"decision": "maybe"},
            cookie=cookie,
        )

        self.assertEqual(status, 400)
        self.assertIn("allow_once", json.loads(data)["error"])
        self.assertEqual(len(_FakeBridgeHandler.records), bridge_call_count)

    def test_session_cancel_is_proxied_with_mode(self):
        cookie = self._pair()

        status, _, data = self._request(
            "POST", "/api/sessions/session-123/cancel", body={"mode": "stop"}, cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["mode"], "stop")
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual((record["method"], record["path"]), ("POST", "/mobile/v1/sessions/session-123/cancel"))
        self.assertEqual(record["body"], {"mode": "stop"})

    def test_subagents_list_is_proxied_with_limit(self):
        cookie = self._pair()

        status, _, data = self._request(
            "GET", "/api/sessions/session-123/subagents?limit=50", cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["subagents"][0]["subagent_id"], "sub_1")
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(
            (record["method"], record["path"]),
            ("GET", "/mobile/v1/sessions/session-123/subagents"),
        )
        self.assertEqual(record["query"], {"limit": ["50"]})

    def test_subagents_list_requires_pairing(self):
        bridge_call_count = len(_FakeBridgeHandler.records)

        status, _, _ = self._request("GET", "/api/sessions/session-123/subagents")

        self.assertEqual(status, 401)
        self.assertEqual(len(_FakeBridgeHandler.records), bridge_call_count)

    def test_subagent_cancel_is_proxied(self):
        cookie = self._pair()

        status, _, data = self._request(
            "POST", "/api/sessions/session-123/subagents/sub_1/cancel", body={}, cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["cancelled"])
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(
            (record["method"], record["path"]),
            ("POST", "/mobile/v1/sessions/session-123/subagents/sub_1/cancel"),
        )

    def test_invalid_subagent_id_is_rejected_before_bridge_request(self):
        cookie = self._pair()
        bridge_call_count = len(_FakeBridgeHandler.records)

        status, _, data = self._request(
            "POST", "/api/sessions/session-123/subagents/bad%20id/cancel", body={}, cookie=cookie
        )

        self.assertEqual(status, 400)
        self.assertIn("субагента", json.loads(data)["error"])
        self.assertEqual(len(_FakeBridgeHandler.records), bridge_call_count)

    def test_unknown_cancel_mode_is_rejected_before_bridge_request(self):
        cookie = self._pair()
        bridge_call_count = len(_FakeBridgeHandler.records)

        status, _, data = self._request(
            "POST", "/api/sessions/session-123/cancel", body={"mode": "nuke"}, cookie=cookie
        )

        self.assertEqual(status, 400)
        self.assertIn("cancel", json.loads(data)["error"])
        self.assertEqual(len(_FakeBridgeHandler.records), bridge_call_count)

    def test_pair_attempts_are_rate_limited_after_repeated_failures(self):
        for attempt in range(10):
            status, _, _ = self._request("POST", "/api/pair", body={"code": "WRONGCODE"})
            self.assertEqual(status, 401, f"попытка {attempt + 1} должна дойти до моста")
        bridge_call_count = len(_FakeBridgeHandler.records)

        status, headers, data = self._request("POST", "/api/pair", body={"code": "WRONGCODE"})

        self.assertEqual(status, 429)
        self.assertIn("Повторите", json.loads(data)["error"])
        self.assertGreater(int(headers["Retry-After"]), 0)
        self.assertEqual(len(_FakeBridgeHandler.records), bridge_call_count)

    def test_completion_audio_is_served_with_correct_type_and_script(self):
        status, headers, data = self._request("GET", "/alarm1.mp3")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "audio/mpeg")
        self.assertEqual(data, (_WEB_STATIC / "audio/task-completed.mp3").read_bytes())
        canonical_status, canonical_headers, canonical_data = self._request("GET", "/audio/task-completed.mp3")
        self.assertEqual(canonical_status, 200)
        self.assertEqual(canonical_headers["Content-Type"], "audio/mpeg")
        self.assertEqual(canonical_data, data)
        status, _, html = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertLess(html.index(b"/completion-sound.js"), html.index(b"/app.js"))
        status, headers, _ = self._request("GET", "/completion-sound.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")

    def test_static_render_helper_is_served_locally(self):
        status, headers, data = self._request("GET", "/render.js")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertIn(b"renderMarkdown", data)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_static_entry_script_loads_render_helper(self):
        status, _, data = self._request("GET", "/")

        self.assertEqual(status, 200)
        self.assertIn(b"/render.js", data)

    def test_render_helper_covers_core_markdown_blocks(self):
        status, _, data = self._request("GET", "/render.js")

        self.assertEqual(status, 200)
        for token in (b"blockquote", b"table", b"pre", b"code"):
            self.assertIn(token, data)
        # Rendering is performed via DOM API, not raw markup insertion; otherwise,
        # an agent response containing angle brackets would become markup. We verify this by
        # checking property/method usage, not just mentioning the terminal in the comment.
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, data)

    def test_assistant_answers_use_local_markdown_renderer(self):
        status, _, data = self._request("GET", "/app.js")

        self.assertEqual(status, 200)
        self.assertIn(b"IgorekMarkdown", data)
        self.assertIn(b"renderMarkdown", data)

    def test_entry_script_loads_markdown_helper_before_app(self):
        status, _, data = self._request("GET", "/")

        self.assertEqual(status, 200)
        html = data.decode("utf-8")
        self.assertLess(html.index("/render.js"), html.index("/app.js"))

    def test_entry_markup_has_no_duplicate_element_ids(self):
        # Regression: an incomplete edit to index.html once left two buttons with the same id="stop-button";
        # the duplicate is visually invisible but breaks `byId()` and the cancel handler picks the first encountered node.
        status, _, data = self._request("GET", "/")

        self.assertEqual(status, 200)
        html = data.decode("utf-8")
        identifiers = re.findall(r'id="([^"]+)"', html)
        duplicates = sorted({value for value in identifiers if identifiers.count(value) > 1})
        self.assertEqual([], duplicates, f"дублирующиеся id в index.html: {duplicates}")

    def test_approval_card_offers_local_decisions(self):
        status, _, data = self._request("GET", "/app.js")

        self.assertEqual(status, 200)
        for token in (b"allow_once", b"allow_session", b"reject"):
            self.assertIn(token, data)
        self.assertIn(b"/api/tasks/", data)
        self.assertIn(b"/approvals/", data)

    def test_approval_cards_are_restored_on_session_open(self):
        cookie = self._pair()

        status, _, data = self._request("GET", "/api/approvals?session_id=session-123", cookie=cookie)

        self.assertEqual(status, 200)
        approvals = json.loads(data)["approvals"]
        self.assertEqual(approvals[0]["request_id"], "req-1")
        self.assertEqual(approvals[0]["task_id"], "task-123")

    def test_session_can_be_stopped_from_web_ui(self):
        status, _, data = self._request("GET", "/app.js")

        self.assertEqual(status, 200)
        self.assertIn(b"/cancel", data)
        self.assertIn(b'"cancel"', data)

    def test_approval_pause_is_rendered_as_a_continuation_not_a_restart(self):
        status, _, data = self._request("GET", "/app.js")

        self.assertEqual(status, 200)
        self.assertIn(b"pauseTask", data)
        self.assertIn(b"waiting_approval", data)
        self.assertIn(b"startedTasks", data)
        self.assertIn("Продолжаю после подтверждения".encode("utf-8"), data)

    def test_file_viewer_is_inline_capped_and_csp_safe(self):
        status, _, data = self._request("GET", "/app.js")

        self.assertEqual(status, 200)
        for token in (b"openFileViewer", b"readCappedBody", b"FILE_VIEW_TEXT_LIMIT", b"file-viewer-text"):
            self.assertIn(token, data)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, data)

    def test_artifact_preview_gets_own_sandboxed_csp_and_inline_disposition(self):
        cookie = self._pair()
        status, headers, data = self._request(
            "GET", "/api/sessions/session-123/files/file-1?preview=1", cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(data, b"<html><body>artifact</body></html>")
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn("sandbox allow-scripts", csp)
        self.assertIn("script-src 'unsafe-inline'", csp)
        self.assertIn("style-src 'unsafe-inline'", csp)
        self.assertNotIn("allow-same-origin", csp)
        self.assertEqual(headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertNotIn("attachment", headers.get("Content-Disposition", ""))

    def test_plain_file_download_keeps_attachment_and_interface_csp(self):
        cookie = self._pair()
        status, headers, data = self._request("GET", "/api/sessions/session-123/files/file-1", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(data, b"<html><body>artifact</body></html>")
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))

    def test_artifact_preview_requires_pairing(self):
        status, _, _ = self._request("GET", "/api/sessions/session-123/files/file-1?preview=1")

        self.assertEqual(status, 401)

    def test_logs_dialog_is_resizable_and_remembers_size_locally(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (b"LOGS_SIZE_STORAGE_KEY", b"persistLogsSize", b"toggleLogsExpanded", b"ResizeObserver"):
            self.assertIn(token, script)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b"resize:both", css.replace(b" ", b""))

    def test_profile_is_local_and_avatar_replaces_letter_on_user_messages(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (b"PROFILE_STORAGE_KEY", b"PROFILE_AVATAR_LIMIT", b"FileReader", b"profile.avatar", b"localStorage"):
            self.assertIn(token, script)

    def test_viewer_offers_code_preview_and_fullscreen_toggle(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (b"viewerModeButton", b"?preview=1", b"allow-scripts", b"file-viewer-frame", b"togglePanelFullscreen"):
            self.assertIn(token, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="panel-fullscreen"', markup)

    def test_side_panel_is_drag_resizable_and_remembers_width_local(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"PANEL_WIDTH_STORAGE_KEY",
            b"startPanelResize",
            b"setPointerCapture",
            b"resetPanelWidth",
            b"panelClampWidth",
            b"handleViewportResize",
        ):
            self.assertIn(token, script)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        self.assertIn(b"cursor:col-resize", compact)
        self.assertIn(b"var(--panel-width", compact)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="panel-resize"', markup)
        self.assertIn(b'role="separator"', markup)

    def test_file_rows_offer_two_styled_actions(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (b"file-action-button", b"openFileViewer(file", b"downloadFileUrl"):
            self.assertIn(token, script)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        self.assertIn(b".file-action-button{", compact)
        self.assertIn(b".file-actions{", compact)

    def test_message_rail_tracks_user_messages(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"buildMessageRail",
            b"updateRailActive",
            b"message-rail-tick",
            b"scrollToRailMessage",
            b"scheduleRailLayout",
        ):
            self.assertIn(token, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="message-rail"', markup)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b".message-rail-tick.active", css.replace(b" ", b""))

    def test_composer_mentions_offer_skills_and_followed_by_relative_paths(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"openSkillMention",
            b"openFileMention",
            b"skillsTracked",
            b"/tree?path=",
            b"skill_ids",
            b"trackedSkillIds",
            b"mentionKeydown",
        ):
            self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="mention-menu"', markup)
        self.assertIn(b'id="mention-rows"', markup)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        self.assertIn(b".mention-menu{", compact)
        self.assertIn(b".mention-row.active", compact)
        self.assertIn(b".mention-row-name{", compact)
        self.assertIn(b"var(--line2)", compact)

    def test_composer_shows_session_model_and_profile_chip(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"renderComposerModel",
            b"composer.model_title",
            b"composer.profile_title",
            b"meta.model",
            b"composer-model-name",
        ):
            self.assertIn(token, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="composer-meta"', markup)
        self.assertIn(b'id="composer-model"', markup)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        self.assertIn(b".composer-model{", compact)
        self.assertIn(b".composer-model-name{", compact)
        self.assertIn(b".composer-model-dot{", compact)

    def test_mic_button_uses_web_speech_api_and_hides_when_unsupported(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"SpeechRecognition",
            b"webkitSpeechRecognition",
            b"toggleMic",
            b"renderMicButton",
            b"interimResults",
            b"composer.mic_start",
            b"composer.mic_stop",
        ):
            self.assertIn(token, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="mic-button"', markup)
        self.assertIn(b"mic-icon", markup)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        self.assertIn(b".mic-button{", compact)
        self.assertIn(b".mic-button.recording", compact)

    def test_interface_allows_microphone_for_self_only(self):
        status, headers, _ = self._request("GET", "/")

        self.assertEqual(status, 200)
        policy = headers.get("Permissions-Policy", "")
        self.assertIn("microphone=(self)", policy)
        self.assertIn("camera=()", policy)
        self.assertIn("geolocation=()", policy)

    def test_reveal_button_and_route_are_wired_csp_safe(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (b"revealFile", b"/reveal", b"viewer.reveal", b"reveal.no_display", b"reveal.failed"):
            self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b".file-viewer-reveal", css)

    def test_every_message_offers_a_copy_button_with_copied_state(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"copyMessage",
            b"message-copy",
            b"message.copy",
            b"message.copied",
            b"clipboard.writeText",
        ):
            self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        self.assertIn(b".message-copy{", compact)
        self.assertIn(b".message-copy.copied", compact)

    def test_composer_plan_mode_toggle_and_payload(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"plan_mode: planModeActive()",
            b"igorek-web:mode",
            b"MODE_PLAN = \"plan\"",
            b"setComposerMode",
            b"toggleComposerMode",
            b"composer.mode_plan_note",
            b'mentionModel().menu === "closed"',
        ):
            self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="mode-plan"', markup)
        self.assertIn(b'id="mode-execute"', markup)
        self.assertIn(b'id="composer-mode-note"', markup)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        self.assertIn(b".mode-toggle{", compact)
        self.assertIn(b".mode-button.active", compact)
        self.assertIn(b".composer.plan-mode{", compact)

    def test_new_composer_strings_exist_in_both_languages(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)

        for text in (
            '"composer.model_title": "Модель сессии: {model}"',
            '"composer.model_title": "Session model: {model}"',
            '"composer.mic_start": "Голосовой ввод"',
            '"composer.mic_start": "Voice input"',
            '"viewer.reveal": "Открыть в папке"',
            '"viewer.reveal": "Open in folder"',
            '"reveal.no_display": "Нет графической сессии — файловый менеджер недоступен"',
            '"reveal.no_display": "No graphical session — the file manager is unavailable"',
            '"composer.mode_plan": "План"',
            '"composer.mode_plan": "Plan"',
            '"composer.mode_execute": "Выполнить"',
            '"composer.mode_execute": "Execute"',
            '"composer.mode_plan_note": "Режим планировки — агент только планирует, ничего не выполняет"',
            '"composer.mode_plan_note": "Planning mode — the agent only plans and performs no actions"',
            '"message.copy": "копировать"',
            '"message.copy": "copy"',
            '"message.copied": "скопировано"',
            '"message.copied": "copied"',
        ):
            with self.subTest(text=text):
                self.assertIn(text.encode("utf-8"), script)


class WebUiChangesReviewTest(WebUiServerTestCase):
    def test_review_query_is_encoded_and_requires_pairing(self):
        status, _, _ = self._request("GET", "/api/sessions/chat-test/changes/review?mode=branch&base=backend%2Fmain&path=src%2Fa.py")
        self.assertEqual(status, 401)
        cookie = self._pair()
        self._request("GET", "/api/sessions/chat-test/changes/review?mode=branch&base=backend%2Fmain&path=src%2Fa.py&ignored=1", cookie=cookie)
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(record["path"], "/mobile/v1/sessions/chat-test/changes/review")
        self.assertEqual(record["query"], {"mode":["branch"],"base":["backend/main"],"path":["src/a.py"],"include_untracked":["0"]})


class WebUiSessionTreeTest(WebUiServerTestCase):
    """Session file tree for @-mentions: only within the working directory."""

    def setUp(self):
        super().setUp()
        self.workdir = Path(self._tmp.name) / "project"
        (self.workdir / "src").mkdir(parents=True)
        (self.workdir / "src" / "app.js").write_text("console.log(1)\n", encoding="utf-8")
        (self.workdir / "README.md").write_text("readme", encoding="utf-8")
        for hidden in (".git", "node_modules", ".igorek-host-data", ".igorek-artifacts"):
            (self.workdir / hidden).mkdir()
            (self.workdir / hidden / "inside.txt").write_text("hidden", encoding="utf-8")
        self.outside = Path(self._tmp.name) / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.workdir / "escape").symlink_to(self.outside)
        sessions = Path(self._tmp.name) / "chat_sessions"
        sessions.mkdir()
        (sessions / "session-123.jsonl").write_text(
            json.dumps({
                "type": "meta", "session_id": "session-123", "profile": "cc-local",
                "cwd": str(self.workdir),
            }) + "\n",
            encoding="utf-8",
        )

    def test_tree_requires_pairing_cookie(self):
        status, _, _ = self._request("GET", "/api/sessions/session-123/tree?path=")

        self.assertEqual(status, 401)
        self.assertEqual(_FakeBridgeHandler.records, [])

    def test_tree_lists_workdir_with_hidden_dirs_and_symlink_escapes_filtered(self):
        cookie = self._pair()

        status, _, data = self._request("GET", "/api/sessions/session-123/tree?path=&depth=1", cookie=cookie)

        self.assertEqual(status, 200, data)
        payload = json.loads(data)
        entries = payload["entries"]
        names = [entry["name"] for entry in entries]
        first_file = next(index for index, entry in enumerate(entries) if entry["type"] != "dir")
        self.assertTrue(all(entry["type"] == "dir" for entry in entries[:first_file]))
        self.assertIn("src", names)
        self.assertIn("README.md", names)
        self.assertNotIn(".git", names)
        self.assertNotIn("node_modules", names)
        self.assertNotIn(".igorek-host-data", names)
        self.assertNotIn(".igorek-artifacts", names)
        self.assertNotIn("escape", names)
        src = next(entry for entry in entries if entry["name"] == "src")
        self.assertEqual(src["type"], "dir")
        self.assertEqual(src["path"], "src")
        readme = next(entry for entry in entries if entry["name"] == "README.md")
        self.assertEqual(readme["type"], "file")
        self.assertGreater(readme["size"], 0)
        self.assertEqual(entries, sorted(entries, key=lambda item: (item["type"] != "dir", item["name"].casefold(), item["path"])))
        self.assertEqual(payload["truncated"], False)
        self.assertNotIn(str(self.workdir), data.decode("utf-8"))

    def test_tree_drills_into_subdirectory(self):
        cookie = self._pair()

        status, _, data = self._request("GET", "/api/sessions/session-123/tree?path=src/", cookie=cookie)

        self.assertEqual(status, 200, data)
        entries = json.loads(data)["entries"]
        self.assertEqual([entry["path"] for entry in entries], ["src/app.js"])
        self.assertEqual(entries[0]["type"], "file")
        self.assertGreater(entries[0]["size"], 0)

    def test_tree_rejects_traversal_absolute_paths_and_hidden_dirs(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        for target, expected in (
            ("/api/sessions/session-123/tree?path=../../etc", 400),
            ("/api/sessions/session-123/tree?path=" + quote("/etc", safe=""), 400),
            ("/api/sessions/session-123/tree?path=" + quote("src/../../outside", safe=""), 400),
            ("/api/sessions/session-123/tree?path=escape", 403),
            ("/api/sessions/session-123/tree?path=" + quote(".git", safe=""), 403),
        ):
            with self.subTest(target=target):
                status, _, data = self._request("GET", target, cookie=cookie)
                self.assertEqual(status, expected, data)

        self._assert_access_checks_since(before, 5)

    def test_tree_reports_missing_session_directory_or_cwd(self):
        cookie = self._pair()

        status, _, _ = self._request("GET", "/api/sessions/session-999/tree", cookie=cookie)
        self.assertEqual(status, 404)

        status, _, _ = self._request("GET", "/api/sessions/session-123/tree?path=nope", cookie=cookie)
        self.assertEqual(status, 404)

    def test_tree_depth_is_clamped_and_entries_are_capped(self):
        cookie = self._pair()

        status, _, data = self._request(
            "GET", "/api/sessions/session-123/tree?path=&depth=2", cookie=cookie
        )
        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["depth"], 2)
        paths = [entry["path"] for entry in payload["entries"]]
        self.assertIn("src", paths)
        self.assertIn("src/app.js", paths)

        bulk = self.workdir / "bulk"
        bulk.mkdir()
        for index in range(510):
            (bulk / f"file-{index:03d}.txt").write_text("x", encoding="utf-8")

        status, _, data = self._request(
            "GET", "/api/sessions/session-123/tree?path=bulk&depth=99", cookie=cookie
        )

        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["depth"], 3)
        self.assertEqual(len(payload["entries"]), 500)
        self.assertTrue(payload["truncated"])


class WebUiRevealTest(WebUiServerTestCase):
    """‘Open in folder’: Path strictly within the working directory; uses a non-shell opener."""

    def setUp(self):
        super().setUp()
        self.workdir = Path(self._tmp.name) / "project"
        (self.workdir / "src").mkdir(parents=True)
        (self.workdir / "src" / "app.js").write_text("console.log(1)\n", encoding="utf-8")
        (self.workdir / "README.md").write_text("readme", encoding="utf-8")
        (self.workdir / ".git").mkdir()
        (self.workdir / ".git" / "config").write_text("x", encoding="utf-8")
        self.outside = Path(self._tmp.name) / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.workdir / "escape").symlink_to(self.outside)
        sessions = Path(self._tmp.name) / "chat_sessions"
        sessions.mkdir()
        (sessions / "session-123.jsonl").write_text(
            json.dumps({
                "type": "meta", "session_id": "session-123", "profile": "cc-local",
                "cwd": str(self.workdir),
            }) + "\n",
            encoding="utf-8",
        )

    def _reveal(self, cookie: str, path):
        return self._request(
            "POST", "/api/sessions/session-123/reveal", body={"path": path}, cookie=cookie
        )

    def test_reveal_requires_pairing_cookie(self):
        status, _, _ = self._request(
            "POST", "/api/sessions/session-123/reveal", body={"path": "README.md"}
        )

        self.assertEqual(status, 401)
        self.assertEqual(_FakeBridgeHandler.records, [])

    def test_reveal_rejects_traversal_absolute_hidden_and_missing_paths(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), patch(
            "factory.web_ui.server.subprocess.run"
        ) as runner:
            for target, expected in (
                ("../../etc/passwd", 400),
                ("/etc/passwd", 400),
                ("src/../../outside/secret.txt", 400),
                ("escape/secret.txt", 403),
                (".git/config", 403),
                ("nope.txt", 404),
            ):
                with self.subTest(target=target):
                    status, _, data = self._reveal(cookie, target)
                    self.assertEqual(status, expected, data)
            runner.assert_not_called()
        self._assert_access_checks_since(before, 6)

    def test_reveal_opens_file_directory_with_xdg_open(self):
        cookie = self._pair()
        completed = subprocess.CompletedProcess(["xdg-open"], 0, "", "")
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), patch(
            "factory.web_ui.server.subprocess.run", return_value=completed
        ) as runner:
            status, _, data = self._reveal(cookie, "src/app.js")

        self.assertEqual(status, 200, data)
        payload = json.loads(data)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["opener"], "xdg-open")
        self.assertEqual(payload["path"], "src/app.js")
        args, kwargs = runner.call_args
        self.assertEqual(args[0], ["xdg-open", str(self.workdir / "src")])
        self.assertFalse(kwargs["shell"])
        self.assertLessEqual(kwargs["timeout"], 10)
        self.assertEqual(kwargs["env"]["DISPLAY"], ":0")

    def test_reveal_falls_back_to_nautilus_select_and_reports_failure(self):
        cookie = self._pair()
        missing = FileNotFoundError("xdg-open")
        selected = subprocess.CompletedProcess(["nautilus"], 0, "", "")
        with patch("factory.web_ui.server._session_display_env", return_value={"DISPLAY": ":0"}), patch(
            "factory.web_ui.server.subprocess.run", side_effect=[missing, selected]
        ) as runner:
            status, _, data = self._reveal(cookie, "README.md")

        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["opener"], "nautilus")
        self.assertEqual(
            runner.call_args_list[1][0][0],
            ["nautilus", "--select", str(self.workdir / "README.md")],
        )

        failed = subprocess.CompletedProcess(["xdg-open"], 3, "", "no handler")
        with patch("factory.web_ui.server._session_display_env", return_value={"DISPLAY": ":0"}), patch(
            "factory.web_ui.server.subprocess.run", return_value=failed
        ):
            status, _, data = self._reveal(cookie, "README.md")

        self.assertEqual(status, 502)
        payload = json.loads(data)
        self.assertEqual(payload["code"], "opener_failed")
        self.assertIn("no handler", payload["error"])

    def test_reveal_without_display_reports_no_display(self):
        cookie = self._pair()
        blank = {key: "" for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
        with patch.dict(os.environ, blank, clear=False), patch(
            "factory.web_ui.server._run_command", return_value=None
        ), patch("factory.web_ui.server.subprocess.run") as runner:
            status, _, data = self._reveal(cookie, "README.md")

        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["code"], "no_display")
        runner.assert_not_called()

    def test_reveal_resolves_unique_attachment_name_and_rejects_ambiguous(self):
        cookie = self._pair()
        completed = subprocess.CompletedProcess(["xdg-open"], 0, "", "")
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), patch(
            "factory.web_ui.server.subprocess.run", return_value=completed
        ) as runner:
            status, _, data = self._reveal(cookie, "app.js")

        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["path"], "src/app.js")
        self.assertEqual(runner.call_args[0][0], ["xdg-open", str(self.workdir / "src")])

        (self.workdir / "copy").mkdir()
        (self.workdir / "copy" / "app.js").write_text("x", encoding="utf-8")
        with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), patch(
            "factory.web_ui.server.subprocess.run"
        ) as runner:
            status, _, data = self._reveal(cookie, "app.js")

        self.assertEqual(status, 409)
        self.assertEqual(json.loads(data)["code"], "ambiguous")
        runner.assert_not_called()

    def test_reveal_requires_json_body_and_string_path(self):
        cookie = self._pair()

        status, _, _ = self._request(
            "POST", "/api/sessions/session-123/reveal", body={"path": 5}, cookie=cookie
        )
        self.assertEqual(status, 400)

        status, _, _ = self._request(
            "POST", "/api/sessions/session-123/reveal", body={}, cookie=cookie
        )
        self.assertEqual(status, 400)


class WebUiSessionJournalTest(WebUiServerTestCase):
    """Durable session log: agent schedule and usage for the ‘Plan’ tab."""

    def test_journal_requires_pairing(self):
        status, _, _ = self._request("GET", "/api/sessions/session-123/events?after=0&limit=500")

        self.assertEqual(status, 401)
        self.assertEqual(_FakeBridgeHandler.records, [])

    def test_journal_proxies_after_and_limit_to_bridge_journal(self):
        cookie = self._pair()

        status, headers, data = self._request(
            "GET", "/api/sessions/session-123/events?after=7&limit=25", cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(data)["events"][1]["kind"], "model.round_response")
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual((record["method"], record["path"]), ("GET", "/mobile/v1/sessions/session-123/events"))
        self.assertEqual(record["query"], {"after": ["7"], "limit": ["25"]})
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")

    def test_journal_limit_is_clamped_and_bad_cursor_is_rejected(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        status, _, _ = self._request(
            "GET", "/api/sessions/session-123/events?after=0&limit=9999", cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(_FakeBridgeHandler.records[-1]["query"]["limit"], ["500"])

        status, _, data = self._request(
            "GET", "/api/sessions/session-123/events?after=wrong&limit=500", cookie=cookie
        )

        self.assertEqual(status, 400)
        self.assertIn("числом", json.loads(data)["error"])
        self.assertEqual(len(_FakeBridgeHandler.records), before + 1)

    def test_journal_rejects_unsafe_session_id(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        status, _, _ = self._request(
            "GET", "/api/sessions/bad%20id/events?after=0&limit=500", cookie=cookie
        )

        self.assertEqual(status, 400)
        self.assertEqual(len(_FakeBridgeHandler.records), before)

    def test_events_without_limit_still_stream(self):
        cookie = self._pair()

        status, headers, _ = self._request(
            "GET", "/api/sessions/session-123/events?after=7", cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream; charset=utf-8")

    def test_plan_tab_ui_is_wired_csp_safe(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"loadSessionJournal",
            b"buildPlanView",
            b"buildPlanContext",
            b"notePlanFromTools",
            b"noteUsage",
            b"JOURNAL_PAGE_LIMIT",
        ):
            self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        for token in (b'id="side-tab-plan"', b'id="side-view-plan"'):
            self.assertIn(token, markup)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        for token in (b".plan-marker.in_progress", b".plan-context-grid", b".plan-metric-value", b"plan-pulse"):
            self.assertIn(token, compact)

    def test_plan_strings_exist_in_both_languages(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)

        for text in (
            '"panel.tab.plan": "План"',
            '"panel.tab.plan": "Plan"',
            '"plan.context_title": "Контекст"',
            '"plan.context_title": "Context"',
            "Размер окна контекста недоступен",
            "The context window is not exposed",
            "Просмотрены не все события",
            "Not all events were scanned",
        ):
            with self.subTest(text=text):
                self.assertIn(text.encode("utf-8"), script)


class WebUiModelInfoTest(WebUiServerTestCase):
    """Actual profile model and context window for the Igorek composer chip."""

    def _write_profile(self, name: str, payload: dict) -> None:
        directory = Path(self._tmp.name) / "profiles"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_model_info_requires_pairing(self):
        status, _, _ = self._request("GET", "/api/model-info?profile=cc-test")

        self.assertEqual(status, 401)
        self.assertEqual(_FakeBridgeHandler.records, [])

    def test_model_info_reads_profile_model_and_context_window(self):
        self._write_profile("cc-test", {"model": "deepseek-v4.1-flash", "backend": "cc-backend"})
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        with patch("factory.core.opencode_models.get_context_window", return_value=1_000_000) as window:
            status, _, data = self._request("GET", "/api/model-info?profile=cc-test", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(data),
            {"profile": "cc-test", "model": "deepseek-v4.1-flash", "context_window": 1000000},
        )
        self.assertEqual(window.call_args.args[0], "deepseek-v4.1-flash")
        self._assert_access_checks_since(before, 1)

    def test_model_info_falls_back_to_passed_model_and_null_window(self):
        cookie = self._pair()

        with (
            patch("factory.core.opencode_models.get_context_window", return_value=None),
            patch("factory.core.openrouter_pricing.get_openrouter_context_window", return_value=None) as fallback,
        ):
            status, _, data = self._request(
                "GET",
                "/api/model-info?profile=cc-missing&model=" + quote("unknown-model", safe=""),
                cookie=cookie,
            )

        fallback.assert_called_once_with("unknown-model")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(data),
            {"profile": "cc-missing", "model": "unknown-model", "context_window": None},
        )

    def test_local_model_info_tracks_running_server_instead_of_stale_profile(self):
        self._write_profile("cc-test", {
            "backend": "cc-backend", "provider": "local", "model": "old-model",
            "base_url": "http://127.0.0.1:8081/v1",
        })
        cookie = self._pair()
        with patch("factory.core.runtime.resolve_local_model_display_name", side_effect=["Nemotron-Bolt", "next-model"]) as resolve, patch("factory.web_ui.server._model_context_window", return_value=None):
            for expected in ("Nemotron-Bolt", "next-model"):
                status, _, data = self._request("GET", "/api/model-info?profile=cc-test", cookie=cookie)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(data)["model"], expected)
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(resolve.call_args.args, ("http://127.0.0.1:8081/v1", "old-model"))

    def test_cloud_model_info_does_not_probe_provider(self):
        self._write_profile("cc-test", {
            "backend": "cc-backend", "provider": "glm", "model": "cloud-model",
            "base_url": "https://provider.example/v1",
        })
        cookie = self._pair()
        with patch("factory.core.runtime.resolve_local_model_display_name") as resolve, patch("factory.web_ui.server._model_context_window", return_value=None):
            status, _, data = self._request("GET", "/api/model-info?profile=cc-test", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["model"], "cloud-model")
        resolve.assert_not_called()

    def test_model_info_rejects_unsafe_profile_before_filesystem(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        status, _, data = self._request(
            "GET", "/api/model-info?profile=" + quote("../etc/passwd", safe=""), cookie=cookie
        )

        self.assertEqual(status, 400)
        self.assertIn("профил", json.loads(data)["error"].lower())
        self._assert_access_checks_since(before, 1)


class WebUiOpenCodeUsageTest(WebUiServerTestCase):
    """OpenCode Go quota: proxy endpoint `/api/opencode-usage`; the API key does not leak to the browser."""

    _PAYLOAD = {
        "usage": {
            "rolling": {"percent": 5, "status": "ok", "resetsAt": "2026-10-01T18:27:14.544Z"},
            "weekly": {"percent": 26, "status": "ok", "resetsAt": "2026-10-05T00:00:00.000Z"},
            "monthly": {"percent": 59, "status": "ok", "resetsAt": "2026-10-17T19:35:03.000Z"},
        }
    }

    def setUp(self):
        super().setUp()
        _OPENCODE_USAGE_CACHE.clear()
        _OPENCODE_USAGE_CACHE.update({"at": 0.0, "payload": None})
        _OPENCODE_KEY_CACHE.clear()
        _OPENCODE_KEY_CACHE.update({"at": 0.0, "key": ""})

    def test_usage_requires_pairing(self):
        status, _, _ = self._request("GET", "/api/opencode-usage")

        self.assertEqual(status, 401)
        self.assertEqual(_FakeBridgeHandler.records, [])

    def test_desktop_provider_bypasses_host_key_and_shared_cache(self):
        cookie = self._pair()
        self.server.opencode_usage_provider = lambda **kwargs: {"usage": self._PAYLOAD["usage"], "fetched_at": "desktop"}
        with patch("factory.web_ui.server._opencode_api_key", side_effect=AssertionError("Host vault accessed")):
            status, _, data = self._request("GET", "/api/opencode-usage", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["fetched_at"], "desktop")
        self.server.opencode_usage_provider = lambda **kwargs: (_ for _ in ()).throw(_OpenCodeUsageError("no_key", 503, "Locked"))
        status, _, data = self._request("GET", "/api/opencode-usage", cookie=cookie)
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["code"], "no_key")

    def test_usage_prefers_process_environment_key(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "env-key"}, clear=False):
            self.assertEqual(_opencode_api_key(), "env-key")

    def test_usage_returns_normalized_upstream_and_caches(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        with patch("factory.web_ui.server._opencode_api_key", return_value="host-key"), patch(
            "factory.web_ui.server._fetch_opencode_usage", return_value=self._PAYLOAD
        ) as fetch:
            status, _, data = self._request("GET", "/api/opencode-usage", cookie=cookie)
            self.assertEqual(status, 200, data)
            payload = json.loads(data)
            self.assertEqual(payload["usage"], self._PAYLOAD["usage"])
            self.assertNotIn(b"host-key", data)
            self.assertEqual(fetch.call_count, 1)
            status, _, _ = self._request("GET", "/api/opencode-usage", cookie=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(fetch.call_count, 1)
            status, _, _ = self._request("GET", "/api/opencode-usage?refresh=1", cookie=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(fetch.call_args.args[0], "host-key")

        self._assert_access_checks_since(before, 3)

    def test_usage_without_key_reports_unavailable_without_upstream(self):
        cookie = self._pair()

        with patch("factory.web_ui.server._opencode_api_key", return_value=""), patch(
            "factory.web_ui.server._fetch_opencode_usage"
        ) as fetch:
            status, _, data = self._request("GET", "/api/opencode-usage", cookie=cookie)

        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["code"], "no_key")
        fetch.assert_not_called()

    def test_usage_maps_upstream_rejection_to_typed_error(self):
        cookie = self._pair()
        error = _OpenCodeUsageError("subscription_required", 502, "Нужна подписка OpenCode Go")

        with patch("factory.web_ui.server._opencode_api_key", return_value="host-key"), patch(
            "factory.web_ui.server._fetch_opencode_usage", side_effect=error
        ):
            status, _, data = self._request("GET", "/api/opencode-usage", cookie=cookie)

        self.assertEqual(status, 502)
        self.assertEqual(json.loads(data)["code"], "subscription_required")
        self.assertNotIn(b"host-key", data)

    def test_fetch_maps_forbidden_to_subscription_required(self):
        error = urllib.error.HTTPError(_OPENCODE_USAGE_URL, 403, "Forbidden", {}, io.BytesIO(b""))

        with patch("factory.web_ui.server.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(_OpenCodeUsageError) as raised:
                _fetch_opencode_usage("host-key")

        self.assertEqual(raised.exception.code, "subscription_required")
        self.assertEqual(raised.exception.status, 502)

    def test_fetch_sends_user_agent_header(self):
        class _Response:
            status = 200

            def read(self):
                return b'{"usage": {"rolling": {"percent": 1}, "weekly": {"percent": 2}, "monthly": {"percent": 3}}}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("factory.web_ui.server.urllib.request.urlopen", return_value=_Response()) as opened:
            payload = _fetch_opencode_usage("host-key")

        request = opened.call_args.args[0]
        self.assertEqual(request.headers.get("User-agent"), "IgorekFactory local-web")
        self.assertEqual(payload["usage"]["rolling"]["percent"], 1)

    def test_fetch_rejects_unexpected_upstream_shape(self):
        class _Response:
            status = 200

            def read(self):
                return b'{"usage": {"rolling": {"percent": "many"}}}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("factory.web_ui.server.urllib.request.urlopen", return_value=_Response()):
            with self.assertRaises(_OpenCodeUsageError) as raised:
                _fetch_opencode_usage("host-key")

        self.assertEqual(raised.exception.code, "invalid_response")

    def test_quota_card_is_wired_csp_safe_and_styled(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"buildPlanOpenCodeUsage",
            b"loadOpenCodeUsage",
            b"refreshOpenCodeUsageIfVisible",
            b"/api/opencode-usage",
            b"buildQuotaRow",
            b"state.quota",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        for token in (
            b".plan-quota-row",
            b".plan-quota-percent",
            b".plan-quota-fill.warn",
            b".plan-quota-fill.danger",
            b".plan-quota-error",
        ):
            with self.subTest(token=token):
                self.assertIn(token, compact)

    def test_quota_strings_exist_in_both_languages(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)

        for text in (
            '"plan.quota_title": "Квота OpenCode"',
            '"plan.quota_title": "OpenCode quota"',
            '"plan.quota_window_5h": "5 часов"',
            '"plan.quota_window_5h": "5 hours"',
            '"plan.quota_window_week": "Неделя"',
            '"plan.quota_window_week": "Week"',
            '"plan.quota_window_month": "Месяц"',
            '"plan.quota_window_month": "Month"',
            '"plan.quota_reset_in": "сброс через {when}"',
            '"plan.quota_reset_in": "resets in {when}"',
            '"plan.quota_rate_limited": "лимит исчерпан"',
            '"plan.quota_rate_limited": "rate limited"',
            '"plan.quota_bar_aria"',
            '"plan.quota_error_no_key"',
            '"plan.quota_error_subscription"',
        ):
            with self.subTest(text=text):
                self.assertIn(text.encode("utf-8"), script)


class WebUiSessionHistoryTest(WebUiServerTestCase):
    """Session history: complete timeline retrieved from the durable journal and context window."""

    def test_history_reconstruction_is_wired_csp_safe(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"newJournalHistory",
            b"collectJournalHistory",
            b"buildHistoryEntries",
            b"applyHistoryEntries",
            b"historyAssistantMatch",
            b"HISTORY_SUB",
            b'"reasoning-" + taskId',
            b'"status-" + taskId + "-finish"',
            b"modelInfoKey",
            b"loadModelInfo",
            b"modelContextWindow",
            b"buildPlanContextUsage",
            b"state.historyBaseIds",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

    def test_context_window_ui_is_rendered_in_existing_panel(self):
        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        for token in (b".plan-context-usage", b".plan-context-bar", b".plan-context-bar-fill"):
            with self.subTest(token=token):
                self.assertIn(token, compact)

    def test_history_and_context_strings_exist_in_both_languages(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)

        for text in (
            '"plan.context_window": "Окно контекста"',
            '"plan.context_window": "Context window"',
            '"plan.context_used_pct": "{pct}% использовано"',
            '"plan.context_used_pct": "{pct}% used"',
            '"plan.context_free_pct": "свободно {pct}%"',
            '"plan.context_free_pct": "{pct}% free"',
            '"plan.context_bar_aria"',
            '"plan.context_window_note"',
            '"composer.model_title"',
        ):
            with self.subTest(text=text):
                self.assertIn(text.encode("utf-8"), script)

    def test_reasoning_card_renders_markdown_and_real_duration(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"reasoningDurationMs",
            b"reasoning-duration",
            b"startedTs",
            b"endedTs",
            b"firstTs",
            b"lastTs",
            b"details.open = Boolean(next.open)",
            b"window.IgorekMarkdown.renderMarkdown(raw, body)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b".reasoning-duration", css)

    def test_sidebar_session_count_uses_bridge_total(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (b"state.sessionTotal", b"Number(result.total)", b"result.total"):
            with self.subTest(token=token):
                self.assertIn(token, script)


class WebUiExternalSessionsTest(WebUiServerTestCase):
    """Agent history: agent list, transcript, and proxy import are validated."""

    def test_external_sessions_list_requires_pairing(self):
        status, _, _ = self._request("GET", "/api/external-sessions")

        self.assertEqual(status, 401)
        self.assertEqual(_FakeBridgeHandler.records, [])

    def test_external_sessions_list_passes_filters_to_bridge(self):
        cookie = self._pair()

        status, _, data = self._request(
            "GET",
            "/api/external-sessions?limit=25&refresh=1&source=claude&query=" + quote("при", safe=""),
            cookie=cookie,
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["total"], 1)
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual((record["method"], record["path"]), ("GET", "/mobile/v1/external-sessions"))
        self.assertEqual(
            record["query"],
            {"limit": ["25"], "refresh": ["1"], "source": ["claude"], "query": ["при"]},
        )
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")

    def test_external_sessions_limit_is_clamped(self):
        cookie = self._pair()

        status, _, _ = self._request("GET", "/api/external-sessions?limit=99999", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(_FakeBridgeHandler.records[-1]["query"]["limit"], ["1000"])

    def test_external_session_detail_passes_offset_and_limit(self):
        cookie = self._pair()

        status, _, data = self._request(
            "GET", "/api/external-sessions/claude/sess-1?offset=100&limit=50", cookie=cookie
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["session_id"], "sess-1")
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(
            (record["method"], record["path"]), ("GET", "/mobile/v1/external-sessions/claude/sess-1")
        )
        self.assertEqual(record["query"], {"offset": ["100"], "limit": ["50"]})
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")

    def test_external_session_import_forwards_body(self):
        cookie = self._pair()
        body = {"profile": "cc-local", "cwd": "/tmp/project", "idempotency_key": "import-1"}

        status, _, data = self._request(
            "POST", "/api/external-sessions/claude/sess-1/import", body=body, cookie=cookie
        )

        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data)["session"]["session_id"], "session-123")
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(
            (record["method"], record["path"]),
            ("POST", "/mobile/v1/external-sessions/claude/sess-1/import"),
        )
        self.assertEqual(record["body"], body)
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")

    def test_unknown_source_or_missing_route_never_reaches_bridge(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        for target, expected_status, fragment in (
            ("/api/external-sessions?source=unknown", 400, "Неизвестный источник"),
            ("/api/external-sessions/unknown/sess-1", 400, "Некорректный источник"),
            ("/api/external-sessions/claude/sess-1/extra", 404, "не найдена"),
        ):
            with self.subTest(target=target):
                status, _, data = self._request("GET", target, cookie=cookie)
                self.assertEqual(status, expected_status, data)
                self.assertIn(fragment, json.loads(data)["error"])

        self.assertEqual(len(_FakeBridgeHandler.records), before)

    def test_unsafe_session_id_is_rejected_before_bridge(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        for bad_id in ("..%2Fetc", "bad%20id", "x" * 201):
            with self.subTest(bad_id=bad_id):
                status, _, data = self._request(
                    "GET", f"/api/external-sessions/claude/{bad_id}", cookie=cookie
                )
                self.assertEqual(status, 400, data)
                self.assertIn("Некорректный", json.loads(data)["error"])

        status, _, _ = self._request(
            "POST",
            "/api/external-sessions/claude/bad%20id/import",
            body={"profile": "cc-local", "cwd": "/tmp/project", "idempotency_key": "k"},
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertEqual(len(_FakeBridgeHandler.records), before)

    def test_import_body_fields_must_be_strings(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)

        status, _, data = self._request(
            "POST",
            "/api/external-sessions/claude/sess-1/import",
            body={"profile": 7, "cwd": "/tmp/project"},
            cookie=cookie,
        )

        self.assertEqual(status, 400)
        self.assertIn("profile", json.loads(data)["error"])
        self.assertEqual(len(_FakeBridgeHandler.records), before)

    def test_import_rejects_home_and_root_cwd_before_bridge(self):
        cookie = self._pair()
        before = len(_FakeBridgeHandler.records)
        home = str(Path.home())

        for cwd in ("~", home, home + "/", "/", "//"):
            with self.subTest(cwd=cwd):
                status, _, data = self._request(
                    "POST",
                    "/api/external-sessions/claude/sess-1/import",
                    body={"profile": "cc-local", "cwd": cwd, "idempotency_key": "k"},
                    cookie=cookie,
                )
                self.assertEqual(status, 400, data)
                payload = json.loads(data)
                self.assertEqual(payload["code"], "cwd_denied")
                self.assertIn("Рабочая папка", payload["error"])

        self.assertEqual(len(_FakeBridgeHandler.records), before)

    def test_import_allows_normal_project_cwd(self):
        cookie = self._pair()

        status, _, data = self._request(
            "POST",
            "/api/external-sessions/claude/sess-1/import",
            body={"profile": "cc-local", "cwd": "/tmp/project", "idempotency_key": "k"},
            cookie=cookie,
        )

        self.assertEqual(status, 201, data)
        self.assertEqual(_FakeBridgeHandler.records[-1]["body"]["cwd"], "/tmp/project")

    def test_agents_import_cwd_guard_ui_is_wired_csp_safe(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"createWorkdirCombo",
            b"agentsCwdCombo",
            b"cwdDeniedReason",
            b"cwdDeniedMessage",
            b"renderAgentsCwdHint",
            b"originalCwd",
            b"options.default_cwd",
            b"agents.cwd_original",
            b"cwd.denied_home",
            b"cwd.denied_root",
            b"cwd.denied_system",
            b'noteKey === "task.finished_error"',
        ):
            self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        for token in (
            b'id="agents-cwd-combo"',
            b'id="agents-cwd-toggle"',
            b'id="agents-cwd-options"',
            b'id="agents-cwd-value"',
            b'id="agents-cwd-hint"',
            b'id="agents-cwd-note"',
        ):
            self.assertIn(token, markup)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        for token in (b".agents-import-field{", b".agents-cwd-hint{", b".agents-cwd-note{"):
            self.assertIn(token, compact)

    def test_import_cwd_strings_exist_in_both_languages(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)

        for text in (
            '"agents.cwd_note": "Сервер проверит исходную папку по правилам изоляции',
            '"agents.cwd_note": "The server checks the source folder against isolation rules',
            '"agents.cwd_original": "исходная папка: {path}"',
            '"agents.cwd_original": "original folder: {path}"',
            '"cwd.denied_home": "Домашнюю папку нельзя использовать',
            '"cwd.denied_home": "The home folder cannot be used',
            '"cwd.denied_root": "Корень файловой системы нельзя',
            '"cwd.denied_root": "The filesystem root cannot be used',
        ):
            with self.subTest(text=text):
                self.assertIn(text.encode("utf-8"), script)

    def test_agents_history_ui_is_wired_csp_safe(self):
        status, _, script = self._request("GET", "/app.js")
        self.assertEqual(status, 200)
        for token in (
            b"openAgents",
            b"externalTimelineItems",
            b"renderAgentsTimeline",
            b"importExternalSession",
            b"agents-dialog",
        ):
            self.assertIn(token, script)
        for forbidden in (b".innerHTML", b"outerHTML", b"insertAdjacentHTML", b"document.write"):
            self.assertNotIn(forbidden, script)

        status, _, markup = self._request("GET", "/")
        self.assertEqual(status, 200)
        for token in (
            b'id="open-agents"',
            b'id="agents-dialog"',
            b'id="agents-list"',
            b'id="agents-import"',
        ):
            self.assertIn(token, markup)

        status, _, css = self._request("GET", "/app.css")
        self.assertEqual(status, 200)
        compact = css.replace(b" ", b"")
        for token in (b".agents-dialog", b".agents-session", b".agents-timeline", b".agents-import"):
            self.assertIn(token, compact)


class WebUiLogsTest(WebUiServerTestCase):
    """Service logs: strict unit allowlist enforced; no arbitrary commands allowed."""

    @staticmethod
    def _completed(args, stdout="", returncode=0, stderr=""):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    def _journal_fake(self, stdout="", returncode=0, stderr="", calls=None):
        def run(args, **kwargs):
            if calls is not None:
                calls.append(list(args))
            return self._completed(args, stdout, returncode, stderr)
        return run

    def test_logs_require_pairing_cookie(self):
        with patch("factory.web_ui.server.subprocess.run") as run:
            status, _, _ = self._request("GET", "/api/logs?unit=gateway")

        self.assertEqual(status, 401)
        run.assert_not_called()

    def test_desktop_logs_use_owned_sources_after_auth_without_host_commands(self):
        import importlib.util
        from types import SimpleNamespace
        spec = importlib.util.spec_from_file_location('desktop_runtime_logs', _REPO_ROOT / 'desktop/runtime_logs.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        logs = module.RuntimeLogs(io.StringIO())
        logs.write('own desktop journal\n')
        self.server.logs_provider = logs.tail
        self.server.logs_status_provider = lambda: logs.status(
            SimpleNamespace(is_set=lambda: False), [], SimpleNamespace(status=lambda: {'running': False}))
        with patch('factory.web_ui.server.subprocess.run') as run:
            status, _, _ = self._request('GET', '/api/logs/status')
            self.assertEqual(status, 401)
            status, _, _ = self._request('GET', '/api/logs?unit=runtime')
            self.assertEqual(status, 401)
            cookie = self._pair()
            status, _, data = self._request('GET', '/api/logs/status', cookie=cookie)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)['units'][0]['key'], 'runtime')
            self.assertEqual(json.loads(data)['units'][0]['active'], 'active')
            status, _, data = self._request('GET', '/api/logs?unit=runtime&lines=1', cookie=cookie)
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(data)['lines'][0].endswith('own desktop journal'))
            status, _, _ = self._request('GET', '/api/logs?unit=gateway', cookie=cookie)
            self.assertEqual(status, 400)
            run.assert_not_called()

    def test_logs_status_requires_pairing_cookie(self):
        with patch("factory.web_ui.server.subprocess.run") as run:
            status, _, _ = self._request("GET", "/api/logs/status")

        self.assertEqual(status, 401)
        run.assert_not_called()

    def test_logs_reject_units_outside_allowlist(self):
        cookie = self._pair()
        for unit in ("shell", "worker:", "worker:a b", "worker:" + "x" * 65, "../gateway", "GATEWAY"):
            with self.subTest(unit=unit):
                with patch("factory.web_ui.server.subprocess.run") as run:
                    status, _, data = self._request(
                        "GET", "/api/logs?unit=" + quote(unit, safe=""), cookie=cookie
                    )
                self.assertEqual(status, 400, data)
                self.assertIn("gateway", json.loads(data)["error"])
                run.assert_not_called()

    def test_logs_return_tail_with_clamped_line_limit(self):
        cookie = self._pair()
        calls = []
        rows = ["строка " + str(index) for index in range(501)]
        with patch(
            "factory.web_ui.server.subprocess.run",
            new=self._journal_fake("\n".join(rows) + "\n", calls=calls),
        ):
            status, _, data = self._request("GET", "/api/logs?unit=gateway&lines=9999", cookie=cookie)

        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["unit"], "igorek-gateway.service")
        self.assertEqual(len(payload["lines"]), 500)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["lines"][-1], "строка 500")
        self.assertEqual(calls[0][:3], ["journalctl", "--user", "-u"])
        self.assertIn("igorek-gateway.service", calls[0])
        self.assertEqual(calls[0][calls[0].index("-n") + 1], "501")

    def test_logs_default_limit_and_worker_unit_name(self):
        cookie = self._pair()
        calls = []
        with patch(
            "factory.web_ui.server.subprocess.run",
            new=self._journal_fake("a\nb\n", calls=calls),
        ):
            status, _, data = self._request("GET", "/api/logs?unit=worker:cc-wally", cookie=cookie)

        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["unit"], "igorek-worker@cc-wally.service")
        self.assertEqual(payload["lines"], ["a", "b"])
        self.assertFalse(payload["truncated"])
        self.assertEqual(calls[0][calls[0].index("-n") + 1], "201")

    def test_logs_journal_failure_returns_json_error_not_500(self):
        cookie = self._pair()
        with patch(
            "factory.web_ui.server.subprocess.run",
            new=self._journal_fake(returncode=1, stderr="No journal files were found."),
        ):
            status, _, data = self._request("GET", "/api/logs?unit=gateway", cookie=cookie)

        self.assertEqual(status, 502)
        self.assertIn("No journal files", json.loads(data)["error"])

    def test_logs_skip_journalctl_no_entries_placeholder(self):
        cookie = self._pair()
        with patch(
            "factory.web_ui.server.subprocess.run",
            new=self._journal_fake("-- No entries --\n"),
        ):
            status, _, data = self._request("GET", "/api/logs?unit=mobile", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["lines"], [])

    def test_logs_status_maps_systemctl_states(self):
        cookie = self._pair()
        show_output = (
            "Id=igorek-gateway.service\nLoadState=loaded\nActiveState=active\n"
            "ActiveEnterTimestamp=Wed 2026-09-30 17:54:26 MSK\n\n"
            "Id=igorek-mobile.service\nLoadState=loaded\nActiveState=inactive\n"
            "ActiveEnterTimestamp=\n\n"
            "Id=igorek-web-ui.service\nLoadState=loaded\nActiveState=failed\n"
            "ActiveEnterTimestamp=Fri 2026-09-25 10:00:00 MSK\n\n"
            "Id=igorek-worker@cc-wally.service\nLoadState=loaded\nActiveState=active\n"
            "ActiveEnterTimestamp=Wed 2026-09-30 12:00:00 MSK\n"
        )

        def run(args, **kwargs):
            if "show" in args:
                return self._completed(args, show_output)
            if "list-units" in args:
                return self._completed(args, "igorek-worker@cc-wally.service loaded active running w\n")
            if "list-unit-files" in args:
                return self._completed(args, "igorek-worker@.service indirect enabled\n")
            return self._completed(args, "")

        with patch("factory.web_ui.server.subprocess.run", side_effect=run):
            status, _, data = self._request("GET", "/api/logs/status", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data).get("error", ""), "")
        units = {item["key"]: item for item in json.loads(data)["units"]}
        self.assertEqual(units["gateway"]["unit"], "igorek-gateway.service")
        self.assertEqual(units["gateway"]["active"], "active")
        self.assertEqual(units["gateway"]["since"], "Wed 2026-09-30 17:54:26 MSK")
        self.assertEqual(units["mobile"]["active"], "inactive")
        self.assertEqual(units["web"]["active"], "failed")
        self.assertEqual(units["worker:cc-wally"]["active"], "active")
        self.assertEqual(units["worker:cc-wally"]["unit"], "igorek-worker@cc-wally.service")

    def test_logs_status_missing_unit_is_not_found_and_unknown_on_failure(self):
        cookie = self._pair()
        show_output = (
            "Id=igorek-gateway.service\nLoadState=loaded\nActiveState=active\n"
            "ActiveEnterTimestamp=\n\n"
            "Id=igorek-mobile.service\nLoadState=not-found\nActiveState=inactive\n"
            "ActiveEnterTimestamp=\n\n"
            "Id=igorek-web-ui.service\nLoadState=loaded\nActiveState=active\n"
            "ActiveEnterTimestamp=\n"
        )

        def run(args, **kwargs):
            if "show" in args:
                return self._completed(args, show_output)
            return self._completed(args, "")

        with patch("factory.web_ui.server.subprocess.run", side_effect=run):
            status, _, data = self._request("GET", "/api/logs/status", cookie=cookie)
        units = {item["key"]: item for item in json.loads(data)["units"]}
        self.assertEqual(units["mobile"]["active"], "not-found")

        with patch("factory.web_ui.server.subprocess.run", return_value=None):
            status, _, data = self._request("GET", "/api/logs/status", cookie=cookie)
        payload = json.loads(data)
        self.assertEqual(status, 200)
        self.assertIn("systemctl", payload["error"])
        self.assertTrue(all(item["active"] == "unknown" for item in payload["units"]))


_NODE_EXCEL_HARNESS = r'''
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const STATIC = process.env.IGOREK_WEB_STATIC;
const FIXTURE = process.env.IGOREK_XLSX_FIXTURE;
const checks = [];
function check(name, ok, detail) {
  checks.push({ name: name, ok: Boolean(ok), detail: detail === undefined ? "" : String(detail) });
}
function equal(name, actual, expected) {
  const actualText = JSON.stringify(actual);
  const expectedText = JSON.stringify(expected);
  check(name, actualText === expectedText, "expected=" + expectedText + " actual=" + actualText);
}
function makeContext(extra) {
  const sandbox = Object.assign({
    console: console, setTimeout: setTimeout, clearTimeout: clearTimeout,
    setInterval: setInterval, clearInterval: clearInterval,
    TextEncoder: TextEncoder, TextDecoder: TextDecoder,
    Uint8Array: Uint8Array, ArrayBuffer: ArrayBuffer, DataView: DataView,
    Promise: Promise, Date: Date, Math: Math, JSON: JSON, String: String,
    Number: Number, Boolean: Boolean, Object: Object, Array: Array, Error: Error,
    TypeError: TypeError, RegExp: RegExp, Map: Map, Set: Set,
    isFinite: isFinite, parseInt: parseInt, parseFloat: parseFloat,
    encodeURIComponent: encodeURIComponent
  }, extra || {});
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}
function loadVendoredXlsx() {
  const context = makeContext({});
  vm.runInContext(fs.readFileSync(path.join(STATIC, "vendor", "xlsx.full.min.js"), "utf8"), context);
  return context.XLSX;
}
function workerFactory(mainSandbox) {
  return function WorkerStub() {
    const stub = this;
    this.onmessage = null;
    this.terminated = false;
    this.addEventListener = function () {};
    this.terminate = function () { stub.terminated = true; };
    const context = makeContext({});
    context.importScripts = function (request) {
      const target = path.join(STATIC, "vendor", path.basename(request));
      vm.runInContext(fs.readFileSync(target, "utf8"), context, { filename: target });
    };
    context.postMessage = function (message) {
      if (stub.terminated) return;
      setImmediate(function () { if (stub.onmessage) stub.onmessage({ data: message }); });
    };
    vm.runInContext(
      fs.readFileSync(path.join(STATIC, "workers", "xlsx-worker.js"), "utf8"),
      context,
      { filename: "xlsx-worker.js" }
    );
    stub.context = context;
    this.postMessage = function (message) {
      if (stub.terminated) return;
      setImmediate(function () { if (context.onmessage) context.onmessage({ data: message }); });
    };
  };
}
function neverRespondingWorker() {
  const stub = { onmessage: null, terminated: false };
  stub.addEventListener = function () {};
  stub.terminate = function () { stub.terminated = true; };
  stub.postMessage = function () {};
  return stub;
}
(async function main() {
  try {
    const main = makeContext({});
    main.Worker = workerFactory(main);
    vm.runInContext(
      fs.readFileSync(path.join(STATIC, "file-viewer.js"), "utf8"),
      main,
      { filename: "file-viewer.js" }
    );
    const FV = main.IgorekFileViewer;
    const XLSX = loadVendoredXlsx();
    const fixture = new main.Uint8Array(fs.readFileSync(FIXTURE));

    equal("kind_xlsx_extension", FV.kind({ name: "book.xlsx", mime_type: "application/octet-stream" }), "spreadsheet");
    equal("kind_xls_extension", FV.kind({ name: "book.xls", mime_type: "" }), "spreadsheet");
    equal("kind_xlsx_mime_only", FV.kind({ name: "book", mime_type: "application/vnd.ms-excel" }), "spreadsheet");
    equal("kind_csv_stays_text", FV.kind({ name: "table.csv", mime_type: "application/vnd.ms-excel" }), "text");
    equal("kind_binary_stays_binary", FV.kind({ name: "blob.bin", mime_type: "application/octet-stream" }), "binary");
    equal("kind_html_stays_html", FV.kind({ name: "page.html", mime_type: "" }), "html");
    equal("limits_bytes", FV.limits.spreadsheetBytes, 10 * 1024 * 1024);
    equal("limits_rows_cols_page", [FV.limits.spreadsheetRows, FV.limits.spreadsheetCols, FV.limits.rowsPerPage], [1000, 100, 100]);
    check("limits_too_big_boundary",
      FV.spreadsheetTooBig(FV.limits.spreadsheetBytes) === false && FV.spreadsheetTooBig(FV.limits.spreadsheetBytes + 1) === true);
    equal("labels_column_letters", [FV.columnLabel(0), FV.columnLabel(25), FV.columnLabel(26), FV.columnLabel(99)], ["A", "Z", "AA", "CV"]);

    const session = FV.createWorkbookSession({});
    const sheets = await session.parse(fixture);
    equal("data_sheet_names", sheets.map(function (item) { return item.name; }), ["Данные", "Лист2", "Большой", "Смещение"]);
    check("data_first_sheet_dims", sheets[0].rows === 5 && sheets[0].cols === 8 && sheets[0].truncated === false, JSON.stringify(sheets[0]));
    const page = await session.sheet(0, 0, 99);
    equal("data_formatted_first_row", page.rows[0], [
      "Привет", "25%", "2023-03-15", "$1,234.50",
      "<img src=x onerror=alert(1)>", "10", "=A2+5", "Ёж"
    ]);
    equal("data_empty_cells_preserved", page.rows[1], ["", "второй", "", "", "", "", "", ""]);
    check("data_literal_html_cell", page.rows[0][4] === "<img src=x onerror=alert(1)>");
    check("data_cached_formula_value", page.rows[0][5] === "10");
    check("data_uncached_formula_text", page.rows[0][6] === "=A2+5");
    check("data_cyrillic", page.rows[0][0] === "Привет" && page.rows[0][7] === "Ёж");
    check("data_last_row", page.rows[4][7] === "конец");
    const offset = sheets[3];
    check("data_offset_sheet_coords", offset.rowStart === 2 && offset.colStart === 2 && offset.rows === 3 && offset.cols === 3, JSON.stringify(offset));
    const offsetPage = await session.sheet(3, 0, 99);
    check("data_offset_cell", offsetPage.rows[1][1] === "центр", JSON.stringify(offsetPage.rows));
    const big = sheets[2];
    check("data_big_sheet_clamped",
      big.rows === 1000 && big.cols === 100 && big.totalRows === 2000 && big.totalCols === 100 && big.truncated === true,
      JSON.stringify(big));
    const bigPage = await session.sheet(2, 0, 99);
    check("data_big_page_rows", bigPage.rows.length === 100 && bigPage.rows[0].length === 100, JSON.stringify({ rows: bigPage.rows.length }));
    const tailPage = await session.sheet(2, 999, 1099);
    check("data_big_tail_clamped", tailPage.rows.length === 1 && tailPage.start === 999 && tailPage.end === 999,
      JSON.stringify({ start: tailPage.start, end: tailPage.end, rows: tailPage.rows.length }));
    const requestsBefore = session.requestCount();
    await session.sheet(0, 0, 99);
    check("data_page_cache_no_extra_request", session.requestCount() === requestsBefore,
      "before=" + requestsBefore + " after=" + session.requestCount());
    const otherSheet = await session.sheet(1, 0, 99);
    check("data_sheet_switch_no_reparse", otherSheet.rows[0][0] === "второй лист" && session.sheetMeta().length === 4);
    const secondPage = await session.sheet(0, 100, 199);
    check("data_out_of_range_page_clamped",
      secondPage.start === 4 && secondPage.end === 4 && secondPage.rows.length === 1,
      JSON.stringify({ start: secondPage.start, end: secondPage.end }));

    const workbook = XLSX.utils.book_new();
    const worksheet = {};
    worksheet["A1"] = { t: "s", v: "из xls" };
    worksheet["A2"] = { t: "n", v: 7 };
    worksheet["!ref"] = "A1:A2";
    XLSX.utils.book_append_sheet(workbook, worksheet, "Старый");
    const xlsBytes = new main.Uint8Array(XLSX.write(workbook, { type: "array", bookType: "xls" }));
    const xlsSession = FV.createWorkbookSession({});
    const xlsSheets = await xlsSession.parse(xlsBytes);
    check("data_xls_sheet_name", xlsSheets.length === 1 && xlsSheets[0].name === "Старый", JSON.stringify(xlsSheets));
    const xlsPage = await xlsSession.sheet(0, 0, 99);
    equal("data_xls_values", xlsPage.rows.map(function (row) { return row[0]; }), ["из xls", "7"]);
    xlsSession.dispose();

    const corruptSession = FV.createWorkbookSession({});
    let corruptCode = "";
    try {
      await corruptSession.parse(new main.Uint8Array([0x50, 0x4b, 0x03, 0x04, 0, 0, 0, 0, 0, 0, 0, 0]));
    } catch (error) { corruptCode = error.code; }
    check("error_corrupt", corruptCode === "corrupt", corruptCode);
    let htmlCode = "";
    try {
      await corruptSession.parse(new main.Uint8Array(TextEncoder.prototype.encode
        ? new TextEncoder().encode("<html><body>не таблица</body></html>")
        : [60, 104, 116, 109, 108, 62]));
    } catch (error) { htmlCode = error.code; }
    check("error_not_spreadsheet", htmlCode === "corrupt", htmlCode);
    corruptSession.dispose();

    let encryptedWorker = null;
    const encryptedSession = FV.createWorkbookSession({
      workerFactory: function () { encryptedWorker = new main.Worker(); return encryptedWorker; }
    });
    const encryptedAttempt = encryptedSession.parse(new main.Uint8Array([1, 2, 3]));
    encryptedWorker.context.XLSX.read = function () { throw new Error("File is password-protected: ECMA-376 Extensible"); };
    let encryptedCode = "";
    try { await encryptedAttempt; } catch (error) { encryptedCode = error.code; }
    check("error_encrypted", encryptedCode === "encrypted", encryptedCode);
    encryptedSession.dispose();

    let timeoutWorker = null;
    const timeoutSession = FV.createWorkbookSession({
      timeoutMs: 40,
      workerFactory: function () { timeoutWorker = neverRespondingWorker(); return timeoutWorker; }
    });
    let timeoutCode = "";
    try { await timeoutSession.parse(new main.Uint8Array([1, 2, 3])); } catch (error) { timeoutCode = error.code; }
    check("error_timeout", timeoutCode === "timeout", timeoutCode);
    check("error_timeout_terminates_worker", timeoutWorker.terminated === true);

    let slowWorker = null;
    const slowSession = FV.createWorkbookSession({
      workerFactory: function () { slowWorker = neverRespondingWorker(); return slowWorker; }
    });
    const slowPending = slowSession.parse(new main.Uint8Array([1, 2, 3]));
    slowSession.dispose();
    let cancelledCode = "";
    try { await slowPending; } catch (error) { cancelledCode = error.code; }
    check("session_dispose_rejects_pending", cancelledCode === "cancelled", cancelledCode);
    check("session_dispose_clears_sheets", slowSession.sheetMeta().length === 0);
    if (slowWorker && slowWorker.onmessage) {
      slowWorker.onmessage({ data: { type: "parsed", id: 1, sheets: [{ name: "СТАРОЕ" }] } });
    }
    check("session_dispose_ignores_late_result", slowSession.sheetMeta().length === 0);

    let staleWorker = null;
    const staleSession = FV.createWorkbookSession({
      workerFactory: function () { staleWorker = neverRespondingWorker(); return staleWorker; }
    });
    const stalePending = staleSession.parse(new main.Uint8Array([1, 2, 3]));
    const freshSession = FV.createWorkbookSession({});
    const freshSheets = await freshSession.parse(fixture);
    if (staleWorker && staleWorker.onmessage) {
      staleWorker.onmessage({ data: { type: "parsed", id: 1, sheets: [{ name: "СТАРОЕ" }] } });
    }
    await stalePending.catch(function () {});
    check("session_late_result_cannot_replace",
      freshSheets[0].name === "Данные" && freshSession.sheetMeta()[0].name === "Данные");
    staleSession.dispose();
    freshSession.dispose();
    const disposedSheet = await freshSession.sheet(0, 0, 99).then(
      function () { return "resolved"; },
      function (error) { return error.code; }
    );
    check("session_sheet_after_dispose_rejected", disposedSheet === "cancelled", String(disposedSheet));

    session.dispose();
    process.stdout.write("__RESULT__" + JSON.stringify(checks) + "\n");
  } catch (error) {
    process.stdout.write("__RESULT__" + JSON.stringify([
      { name: "harness", ok: false, detail: String((error && error.stack) || error) }
    ]) + "\n");
    process.exitCode = 1;
  }
})();
'''


_NODE_HIGHLIGHT_HARNESS = r'''
"use strict";
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const STATIC = process.env.IGOREK_WEB_STATIC;
const checks = [];
function check(name, ok, detail) {
  checks.push({ name: name, ok: Boolean(ok), detail: detail === undefined ? "" : String(detail) });
}
function makeContext(extra) {
  const sandbox = Object.assign({
    console: console, setTimeout: setTimeout, clearTimeout: clearTimeout,
    TextEncoder: TextEncoder, TextDecoder: TextDecoder,
    Uint8Array: Uint8Array, ArrayBuffer: ArrayBuffer,
    Promise: Promise, Date: Date, Math: Math, JSON: JSON, String: String,
    Number: Number, Boolean: Boolean, Object: Object, Array: Array, Error: Error,
    TypeError: TypeError, RegExp: RegExp, Map: Map, Set: Set,
    isFinite: isFinite, parseInt: parseInt, parseFloat: parseFloat
  }, extra || {});
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  return sandbox;
}
function makeCodeElement() {
  return {
    className: "",
    dataset: {},
    children: [],
    textContent: "",
    innerHTML: "",
    classList: { add: function () {}, remove: function () {}, contains: function () { return false; } }
  };
}
function decode(html) {
  return String(html)
    .replace(/<[^>]*>/g, "")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}
(async function main() {
  try {
    const sandbox = makeContext({});
    vm.runInContext(fs.readFileSync(path.join(STATIC, "vendor", "highlight.min.js"), "utf8"), sandbox, { filename: "highlight.min.js" });
    vm.runInContext(fs.readFileSync(path.join(STATIC, "file-viewer.js"), "utf8"), sandbox, { filename: "file-viewer.js" });
    const FV = sandbox.IgorekFileViewer;
    check("hl_lib_loaded", Boolean(sandbox.hljs));
    check("hl_grammar_mapping",
      FV.grammarForName("tsx") === "typescript" &&
      FV.grammarForName("jsx") === "javascript" &&
      FV.grammarForName("hpp") === "cpp" &&
      FV.grammarForName("yml") === "yaml" &&
      FV.grammarForName("sh") === "bash");
    check("hl_plain_languages",
      FV.grammarForName("txt") === "" && FV.grammarForName("log") === "" && FV.grammarForName("") === "");

    async function highlight(source, grammar) {
      const element = makeCodeElement();
      const status = await FV.applyHighlight(element, source, grammar);
      return {
        element: element,
        status: status,
        rendered: element.innerHTML,
        decoded: decode(element.innerHTML),
        text: element.textContent
      };
    }

    const pythonSource = "# комментарий\ndef total(rate=42):\n    return \"сумма\" + str(rate)\n";
    const python = await highlight(pythonSource, "python");
    check("hl_python_applied", python.status.highlighted === true, JSON.stringify(python.status));
    check("hl_python_distinct_tokens",
      python.rendered.indexOf("hljs-comment") >= 0 &&
      python.rendered.indexOf("hljs-string") >= 0 &&
      python.rendered.indexOf("hljs-keyword") >= 0 &&
      python.rendered.indexOf("hljs-number") >= 0 &&
      python.rendered.indexOf("hljs-title") >= 0,
      python.rendered.slice(0, 260));
    check("hl_python_roundtrip", python.decoded === pythonSource && python.text === pythonSource);

    const jsSource = "const value = \"строка\"; // комментарий\nfunction run(count) { return count + 1; }\n";
    const js = await highlight(jsSource, "javascript");
    check("hl_js_tokens",
      js.status.highlighted === true &&
      js.rendered.indexOf("hljs-keyword") >= 0 &&
      js.rendered.indexOf("hljs-string") >= 0 &&
      js.rendered.indexOf("hljs-comment") >= 0 &&
      js.decoded === jsSource,
      js.rendered.slice(0, 220));

    const jsonSource = "{\n  \"ключ\": \"значение\",\n  \"число\": 42\n}\n";
    const json = await highlight(jsonSource, "json");
    check("hl_json_tokens",
      json.status.highlighted === true &&
      json.rendered.indexOf("hljs-attr") >= 0 &&
      json.rendered.indexOf("hljs-string") >= 0 &&
      json.rendered.indexOf("hljs-number") >= 0 &&
      json.decoded === jsonSource,
      json.rendered.slice(0, 260));

    const htmlSource = "<div class=\"card\">текст</div>\n";
    const html = await highlight(htmlSource, "xml");
    check("hl_html_tokens",
      html.status.highlighted === true &&
      html.rendered.indexOf("hljs-tag") >= 0 &&
      html.rendered.indexOf("hljs-name") >= 0 &&
      html.rendered.indexOf("hljs-attr") >= 0 &&
      html.decoded === htmlSource,
      html.rendered.slice(0, 260));

    const attackSource = "<script>window.__pwned = 1;</script><img src=x onerror=\"window.__pwned=2\">";
    const attack = await highlight(attackSource, "xml");
    check("hl_source_not_markup",
      attack.status.highlighted === true &&
      attack.rendered.indexOf("<script") < 0 &&
      attack.rendered.indexOf("<img") < 0,
      attack.rendered.slice(0, 260));
    check("hl_source_roundtrip_after_escape", attack.decoded === attackSource);

    const unknownSource = "const x = 1;";
    const unknown = await highlight(unknownSource, "zzz-unknown");
    check("hl_unknown_plain",
      unknown.status.highlighted === false && unknown.status.reason === "unknown" &&
      unknown.element.innerHTML === "" && unknown.element.textContent === unknownSource);

    const plain = await highlight("просто текст", "txt");
    check("hl_plain_no_autodetect",
      plain.status.highlighted === false && plain.status.reason === "plain" &&
      plain.element.textContent === "просто текст");

    const oversized = "x".repeat(FV.limits.highlightBytes + 1);
    const over = await highlight(oversized, "python");
    check("hl_over_limit_readable",
      over.status.highlighted === false && over.status.reason === "limit" &&
      over.element.textContent === oversized && over.element.innerHTML === "");

    const missing = await FV.applyHighlight(null, "text", "python");
    check("hl_missing_element", missing.highlighted === false && missing.reason === "missing");
    check("hl_limits", FV.limits.highlightBytes === 100 * 1024);

    process.stdout.write("__RESULT__" + JSON.stringify(checks) + "\n");
  } catch (error) {
    process.stdout.write("__RESULT__" + JSON.stringify([
      { name: "harness", ok: false, detail: String((error && error.stack) || error) }
    ]) + "\n");
    process.exitCode = 1;
  }
})();
'''


_CHROME_PAGE = r'''<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>pending</title>
<script src="/file-viewer.js"></script>
<script src="/vendor/highlight.min.js"></script>
<script>
// Загружаем gate-скрипт: пока он не ответит, событие load не наступит, поэтому
// dump-dom дождётся всех асинхронных проверок (воркер и подсветка).
(function () {
  const gate = document.createElement("script");
  gate.src = "/hold.js";
  document.head.append(gate);
})();
</script>
</head>
<body>
<pre id="results">pending</pre>
<script>
(async function () {
  const checks = [];
  const add = function (name, ok, detail) {
    checks.push({ name: name, ok: Boolean(ok), detail: detail === undefined ? "" : String(detail) });
  };
  const finish = function () {
    document.title = "done";
    document.getElementById("results").textContent = "RESULTS:" + btoa(unescape(encodeURIComponent(JSON.stringify(checks))));
  };
  try {
    const viewer = window.IgorekFileViewer;
    const binary = atob("@FIXTURE_BASE64@");
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    const session = viewer.createWorkbookSession({ timeoutMs: 600000 });
    const sheets = await session.parse(bytes);
    add("browser_sheet_names", sheets.map(function (item) { return item.name; }).join("|") === "Данные|Лист2|Большой|Смещение");
    const grid = await session.sheet(0, 0, 99);
    add("browser_formatted_row",
      JSON.stringify(grid.rows[0]) === JSON.stringify(["Привет","25%","2023-03-15","$1,234.50","<img src=x onerror=alert(1)>","10","=A2+5","Ёж"]),
      JSON.stringify(grid.rows[0]));
    const table = document.createElement("table");
    const tbody = document.createElement("tbody");
    const row = document.createElement("tr");
    for (const value of grid.rows[0]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    tbody.append(row);
    table.append(tbody);
    document.body.append(table);
    add("browser_cell_html_stays_literal",
      document.querySelectorAll("img").length === 0 &&
      table.textContent.indexOf("<img src=x onerror=alert(1)>") >= 0);
    const code = document.createElement("code");
    const source = "<img src=x onerror=\"window.__pwned=1\">\n<b>текст</b>";
    const status = await viewer.applyHighlight(code, source, "xml");
    add("browser_highlight_applied", status.highlighted === true, JSON.stringify(status));
    add("browser_code_textContent_equals_source", code.textContent === source, JSON.stringify(code.textContent));
    add("browser_code_not_executed",
      window.__pwned !== 1 && code.querySelectorAll("img,b").length === 0);
    add("browser_token_classes",
      code.querySelectorAll(".hljs-tag,.hljs-name").length > 0 && code.querySelectorAll(".hljs-attr").length > 0);
    const origins = performance.getEntriesByType("resource").map(function (entry) {
      return new URL(entry.name).origin;
    });
    const foreign = origins.filter(function (origin) { return origin !== location.origin; });
    add("browser_no_external_resources", foreign.length === 0, foreign.join(","));
    session.dispose();
  } catch (error) {
    add("browser_harness", false, String((error && error.stack) || error));
  }
  try { await fetch("/done"); } catch (error) { /* сервер уже закрыт */ }
  finish();
})();
</script>
</body>
</html>
'''


def _run_node(script: str, env: dict[str, str]) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        script_path = handle.name
    try:
        completed = subprocess.run(
            [_NODE, script_path],
            capture_output=True,
            text=True,
            timeout=180,
            env={**os.environ, **env},
        )
    finally:
        os.unlink(script_path)
    marker = "__RESULT__"
    for line in completed.stdout.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker):])
    raise AssertionError(
        f"node harness вернул rc={completed.returncode} без результата\n"
        f"stdout={completed.stdout}\nstderr={completed.stderr}"
    )


class _ChromeFixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # Disables warning for BaseHTTPRequestHandler class signature (A002)
        return

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/hold.js":
            self.server.hold.wait(60)
            body = b"window.__holdReady = 1;\n"
            content_type = "text/javascript; charset=utf-8"
        elif path == "/done":
            self.server.hold.set()
            body = b"ok"
            content_type = "text/plain; charset=utf-8"
        elif path == "/":
            body = self.server.page.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        else:
            target = _WEB_STATIC / path.lstrip("/")
            try:
                body = target.read_bytes()
            except OSError:
                self.send_error(404)
                return
            content_type = "text/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WebUiFileViewerAssetsTest(WebUiServerTestCase):
    def test_compact_tool_group_assets_are_served_before_the_app(self):
        for name, content_type, marker in (
            ('tool-groups.js', 'text/javascript; charset=utf-8', b'IgorekToolGroups'),
            ('activity.css', 'text/css; charset=utf-8', b'.activity-group'),
        ):
            status, headers, data = self._request('GET', '/' + name)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], content_type)
            self.assertIn(marker, data)
        _, _, markup = self._request('GET', '/')
        self.assertLess(markup.index(b'/tool-groups.js'), markup.index(b'/app.js'))
        self.assertLess(markup.index(b'/app.css'), markup.index(b'/activity.css'))

    def test_new_viewer_assets_are_served_locally(self):
        for name, content_type, marker in (
            ("file-viewer.js", "text/javascript; charset=utf-8", b"createWorkbookSession"),
            ("workers/xlsx-worker.js", "text/javascript; charset=utf-8", b"importScripts"),
            ("vendor/xlsx.full.min.js", "text/javascript; charset=utf-8", b"SheetJS"),
            ("vendor/highlight.min.js", "text/javascript; charset=utf-8", b"highlight.js"),
            ("vendor/XLSX-LICENSE.txt", "text/plain; charset=utf-8", b"Apache License"),
            ("vendor/HIGHLIGHT-LICENSE.txt", "text/plain; charset=utf-8", b"BSD 3-Clause"),
        ):
            status, headers, data = self._request("GET", "/" + name)
            self.assertEqual(status, 200, name)
            self.assertEqual(headers["Content-Type"], content_type, name)
            self.assertIn(marker, data, name)

    def test_github_reviews_script_is_served_locally(self):
        status, headers, data = self._request("GET", "/github-reviews.js")
        self.assertEqual(status, 200)
        self.assertIn(b"IgorekGitHubReviews", data)
        status, headers, markup = self._request("GET", "/")
        self.assertIn(b'id="github-reviews"', markup)
        self.assertLess(markup.index(b'/github-reviews.js'), markup.index(b'/app.js'))

    def test_downloads_script_is_served_locally(self):
        status, headers, data = self._request("GET", "/downloads.js")
        self.assertEqual(status, 200)
        self.assertIn(b"IgorekDownloads", data)
        status, headers, markup = self._request("GET", "/")
        self.assertLess(markup.index(b'/downloads.js'), markup.index(b'/app.js'))

    def test_workspace_tabs_script_is_served_locally(self):
        status, headers, data = self._request("GET", "/workspace-tabs.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertIn(b"class Model", data)
        self.assertIn(b"normalizeSite", data)
        _, _, markup = self._request("GET", "/")
        self.assertLess(markup.index(b'/workspace-tabs.js'), markup.index(b'/app.js'))

    def test_vendored_versions_are_pinned_and_licensed(self):
        _, _, xlsx = self._request("GET", "/vendor/xlsx.full.min.js")
        self.assertIn(b'version="0.20.3"', xlsx)
        _, _, highlight = self._request("GET", "/vendor/highlight.min.js")
        self.assertIn(b"Highlight.js v11.11.1", highlight)
        status, _, _ = self._request("GET", "/vendor/README.txt")
        self.assertEqual(status, 200)
        readme = (_WEB_STATIC / "vendor" / "README.txt").read_text(encoding="utf-8")
        self.assertIn("0.20.3", readme)
        self.assertIn("11.11.1", readme)
        self.assertIn("Apache", readme)
        self.assertIn("BSD", readme)

    def test_interface_csp_allows_local_worker_without_unsafe(self):
        status, headers, _ = self._request("GET", "/")
        self.assertEqual(status, 200)
        csp = headers["Content-Security-Policy"]
        self.assertIn("worker-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("unsafe-inline", csp)
        directives = dict(part.strip().split(" ", 1) for part in csp.split(";") if part.strip())
        self.assertEqual(directives["worker-src"], "'self'")
        self.assertEqual(directives["script-src"], "'self'")
        self.assertEqual(directives["img-src"], "'self' data: blob:")
        for directive, value in directives.items():
            if directive != "img-src":
                self.assertNotIn("blob:", value)

    def test_entry_loads_file_viewer_before_app(self):
        status, _, data = self._request("GET", "/")
        self.assertEqual(status, 200)
        html = data.decode("utf-8")
        self.assertLess(html.index("/render.js"), html.index("/file-viewer.js"))
        self.assertLess(html.index("/file-viewer.js"), html.index("/app.js"))

    def test_package_data_includes_nested_viewer_assets(self):
        pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"web_ui/static/vendor/*"', pyproject)
        self.assertIn('"web_ui/static/workers/*"', pyproject)
        for name in (
            "file-viewer.js",
            "workers/xlsx-worker.js",
            "vendor/xlsx.full.min.js",
            "vendor/highlight.min.js",
            "vendor/XLSX-LICENSE.txt",
            "vendor/HIGHLIGHT-LICENSE.txt",
            "vendor/README.txt",
        ):
            self.assertTrue((_WEB_STATIC / name).is_file(), name)

    def test_viewer_assets_have_no_external_urls_or_dynamic_code(self):
        for name in ("index.html", "app.js", "file-viewer.js", "workers/xlsx-worker.js"):
            text = (_WEB_STATIC / name).read_text(encoding="utf-8")
            if name == "app.js":
                text = "\n".join(
                    line.replace("https://api.openai.com/v1", "OPENAI_API_ADDRESS")
                    if line.lstrip().startswith('"voice.error.key_setup":') else line
                    for line in text.splitlines()
                )
            for namespace in (
                'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
                'http://www.w3.org/2000/svg',
            ):
                text = text.replace('"' + namespace + '"', '"XML_NAMESPACE"')
            self.assertNotIn("http://", text, name)
            self.assertNotIn("https://", text, name)
        worker = (_WEB_STATIC / "workers" / "xlsx-worker.js").read_text(encoding="utf-8")
        for forbidden in ("eval(", "new Function", "blob:", "fetch(", "XMLHttpRequest", "import("):
            self.assertNotIn(forbidden, worker, forbidden)
        viewer = (_WEB_STATIC / "file-viewer.js").read_text(encoding="utf-8")
        for forbidden in ("blob:", "eval(", "new Function"):
            self.assertNotIn(forbidden, viewer, forbidden)

    def test_spreadsheet_viewer_wiring_and_guards(self):
        script = (_WEB_STATIC / "app.js").read_text(encoding="utf-8")
        for token in (
            "loadSpreadsheetContent",
            "loadSpreadsheetPage",
            "buildSpreadsheetView",
            "buildSpreadsheetGrid",
            "closeFileViewerSession",
            "FILE_VIEW_SPREADSHEET_LIMIT",
            "readCappedBody(response, FILE_VIEW_SPREADSHEET_LIMIT)",
            "capped.truncated",
            "FILE_VIEWER.applyHighlight(code, text, grammar)",
            "clipboard.writeText(text)",
            "viewer.sheet_timeout",
            "viewer.sheet_encrypted",
            "viewer.sheet_corrupt",
        ):
            self.assertIn(token, script, token)
        start = script.index("async function loadSpreadsheetContent")
        end = script.index("async function loadSpreadsheetPage")
        body = script[start:end]
        self.assertNotIn("looksBinary", body)
        self.assertNotIn("TextDecoder", body)

    def test_spreadsheet_and_highlight_strings_exist_in_both_languages(self):
        script = (_WEB_STATIC / "app.js").read_text(encoding="utf-8")
        for key in (
            "viewer.sheet_tabs_aria",
            "viewer.sheet_empty",
            "viewer.sheet_note",
            "viewer.sheet_truncated",
            "viewer.sheet_too_big",
            "viewer.sheet_timeout",
            "viewer.sheet_encrypted",
            "viewer.sheet_corrupt",
            "viewer.sheet_error",
            "viewer.sheet_page",
            "viewer.highlight_limit",
        ):
            self.assertGreaterEqual(script.count('"' + key + '":'), 2, key)

    def test_download_returns_exact_binary_bytes(self):
        cookie = self._pair()
        status, headers, data = self._request(
            "GET", "/api/sessions/session-123/files/file-xlsx", cookie=cookie
        )
        self.assertEqual(status, 200)
        self.assertEqual(data, _BINARY_FILE_BYTES)
        self.assertEqual(headers["Content-Length"], str(len(_BINARY_FILE_BYTES)))


@unittest.skipUnless(_NODE, "node недоступен")
class WebUiSpreadsheetBehaviorTest(unittest.TestCase):
    results: list[dict] = []

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "fixture.xlsx"
            fixture.write_bytes(_xlsx_fixture_bytes())
            cls.results = _run_node(_NODE_EXCEL_HARNESS, {
                "IGOREK_WEB_STATIC": str(_WEB_STATIC),
                "IGOREK_XLSX_FIXTURE": str(fixture),
            })

    def _failures(self, prefix: str = "") -> list[dict]:
        selected = [item for item in self.results if item["name"].startswith(prefix)]
        self.assertTrue(selected, f"нет поведенческих проверок с префиксом {prefix!r}")
        return [item for item in selected if not item["ok"]]

    def test_excel_kind_mapping_and_limits(self):
        self.assertEqual([], self._failures("kind_") + self._failures("limits_") + self._failures("labels_"))

    def test_excel_parses_formats_sheets_and_pages(self):
        failures = self._failures("data_")
        self.assertEqual([], failures, failures)

    def test_excel_error_states_and_session_switching(self):
        failures = self._failures("error_") + self._failures("session_")
        self.assertEqual([], failures, failures)

    def test_all_excel_checks_pass(self):
        failures = [item for item in self.results if not item["ok"]]
        self.assertEqual([], failures, failures)


@unittest.skipUnless(_NODE, "node недоступен")
class WebUiHighlightBehaviorTest(unittest.TestCase):
    results: list[dict] = []

    @classmethod
    def setUpClass(cls):
        cls.results = _run_node(_NODE_HIGHLIGHT_HARNESS, {"IGOREK_WEB_STATIC": str(_WEB_STATIC)})

    def test_highlighting_tokens_and_source_safety(self):
        failures = [item for item in self.results if not item["ok"]]
        self.assertEqual([], failures, failures)
        names = {item["name"] for item in self.results}
        for required in (
            "hl_python_distinct_tokens",
            "hl_js_tokens",
            "hl_json_tokens",
            "hl_html_tokens",
            "hl_python_roundtrip",
            "hl_source_not_markup",
            "hl_unknown_plain",
            "hl_plain_no_autodetect",
            "hl_over_limit_readable",
        ):
            self.assertIn(required, names)


@unittest.skipUnless(_CHROME, "headless Chrome недоступен")
class WebUiFileViewerBrowserTest(unittest.TestCase):
    def test_browser_worker_highlight_literals_and_local_resources(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ChromeFixtureHandler)
        server.page = _CHROME_PAGE.replace(
            "@FIXTURE_BASE64@",
            base64.b64encode(_xlsx_fixture_bytes()).decode("ascii"),
        )
        server.hold = threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"
            with tempfile.TemporaryDirectory() as profile:
                completed = subprocess.run(
                    [
                        _CHROME,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                        f"--user-data-dir={profile}",
                        "--dump-dom",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
        finally:
            server.hold.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        match = re.search(r"RESULTS:([A-Za-z0-9+/=]+)", completed.stdout)
        self.assertIsNotNone(match, completed.stdout[-2000:] + completed.stderr[-2000:])
        checks = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))
        failures = [item for item in checks if not item["ok"]]
        self.assertEqual([], failures, failures)
        names = {item["name"] for item in checks}
        for required in (
            "browser_sheet_names",
            "browser_formatted_row",
            "browser_cell_html_stays_literal",
            "browser_code_textContent_equals_source",
            "browser_code_not_executed",
            "browser_no_external_resources",
        ):
            self.assertIn(required, names)




class WebUiWorkspaceNavigationTest(WebUiServerTestCase):
    def test_schedule_proxy_requires_pairing_and_preserves_csrf_check(self):
        status,_,_=self._request('GET','/api/schedules')
        self.assertEqual(status,401)
        cookie=self._pair()
        status,_,_=self._request('POST','/api/schedules',body={},cookie=cookie,headers={'Origin':'https://foreign.test'})
        self.assertEqual(status,403)
        response=json.dumps({'available':True,'running':True,'schedules':[]}).encode()
        with patch('factory.web_ui.server._Handler._upstream',return_value=(200,response,'application/json')) as upstream:
            status,_,_=self._request('GET','/api/schedules',cookie=cookie)
            self.assertEqual(status,200);self.assertEqual(upstream.call_args.args[1],'/schedules')
            fields={'name':'Example','payload':'Inspect','profile':'demo','cwd':'/tmp','schedule_type':'daily','time_value':'09:00','timezone':'Europe/Moscow'}
            status,_,_=self._request('POST','/api/schedules',body=fields,cookie=cookie)
            self.assertEqual(status,200);self.assertEqual(upstream.call_args.args[3],fields)
            origin={'Origin':f'https://127.0.0.1:{self.port}'}
            status,_,_=self._request('DELETE','/api/schedules/schedule-123',cookie=cookie,headers=origin)
            self.assertEqual(status,200);self.assertEqual(upstream.call_args.args[1],'/schedules/schedule-123')

    def test_pin_proxy_and_larger_session_page_are_validated(self):
        cookie=self._pair();origin={'Origin':f'https://127.0.0.1:{self.port}'}
        with patch('factory.web_ui.server._Handler._upstream',return_value=(200,b'{"pinned":true}','application/json')) as upstream:
            status,_,_=self._request('PATCH','/api/sessions/session-123',body={'pinned':'yes'},cookie=cookie,headers=origin)
            self.assertEqual(status,400);upstream.assert_not_called()
            status,_,_=self._request('PATCH','/api/sessions/session-123',body={'pinned':True},cookie=cookie,headers=origin)
            self.assertEqual(status,200);self.assertEqual(upstream.call_args.args[3],{'pinned':True})
            status,_,_=self._request('GET','/api/sessions?limit=350',cookie=cookie)
            self.assertEqual(status,200);self.assertEqual(upstream.call_args.args[1],'/sessions?limit=350')
            upstream.reset_mock()
            status,_,_=self._request('GET','/api/sessions?limit=invalid',cookie=cookie)
            self.assertEqual(status,400);upstream.assert_not_called()

if __name__ == "__main__":
    unittest.main()


class WebUiMcpTest(WebUiServerTestCase):
    def test_mcp_needs_cookie_and_same_origin(self):
        status,_,_=self._request('POST','/api/mcp',body={'action':'list','fields':{}})
        self.assertEqual(status,401)
        cookie=self._pair()
        status,_,_=self._request('POST','/api/mcp',body={'action':'list','fields':{}},cookie=cookie,headers={'Origin':'https://other.test'})
        self.assertEqual(status,403)
        self.assertFalse(any(record.get('path')=='/mobile/v1/mcp' for record in _FakeBridgeHandler.records))

    def test_plugins_assets_and_separate_rail_page(self):
        status, headers, body = self._request('GET', '/plugins.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        self.assertIn(b'IgorekDesktop', body)
        status, _, html = self._request('GET', '/')
        self.assertEqual(status, 200)
        self.assertLess(html.index(b'id="nav-scheduled"'), html.index(b'id="nav-plugins"'))
        self.assertIn(b'id="plugins-view"', html)
        self.assertIn(b'id="scheduled-view"', html)

    def test_mcp_static_script_is_served(self):
        status,_,body=self._request('GET','/mcp-task.js')
        self.assertEqual(status,200)
        self.assertIn(b'visibilitychange',body)


class WebUiProfileUsageTest(WebUiServerTestCase):
    def test_profile_quota_requires_pairing_and_binds_the_selected_profile(self):
        calls = []
        self.server.profile_usage_provider = lambda profile, **kwargs: calls.append((profile, kwargs)) or {
            'provider': 'codex', 'profile': profile, 'usage': {'weekly': {'percent': 28}}}
        status, _, _ = self._request('GET', '/api/provider-usage?profile=codexpro')
        self.assertEqual(status, 401)
        self.assertEqual(calls, [])
        cookie = self._pair()
        status, _, raw = self._request('GET', '/api/provider-usage?profile=codexpro&refresh=1', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['provider'], 'codex')
        self.assertEqual(calls, [('codexpro', {'force': True})])
        status, _, _ = self._request('GET', '/api/provider-usage?profile=../other', cookie=cookie)
        self.assertEqual(status, 400)
        self.assertEqual(len(calls), 1)
    def test_profile_quota_preserves_locked_state_and_unknown_failures(self):
        from factory.core.opencode_quota import OpenCodeUsageError
        cookie = self._pair()
        def locked(profile, *, force=False):
            raise OpenCodeUsageError('vault_locked', 423, 'Разблокируйте хранилище')
        self.server.profile_usage_provider = locked
        status, _, raw = self._request('GET', '/api/provider-usage?profile=codexpro', cookie=cookie)
        self.assertEqual((status, json.loads(raw)['code']), (423, 'vault_locked'))
        def broken(profile, *, force=False):
            raise RuntimeError('private fixture failure')
        self.server.profile_usage_provider = broken
        status, _, raw = self._request('GET', '/api/provider-usage?profile=codexpro', cookie=cookie)
        self.assertEqual((status, json.loads(raw)['code']), (503, 'unavailable'))
        self.assertNotIn('private fixture failure', raw.decode())


class WebUiMessageAttachmentTest(WebUiServerTestCase):
    def test_message_image_uses_cookie_auth_and_binary_proxy(self):
        path = "/api/sessions/session-123/uploads/" + "a" * 32
        self.assertEqual(self._request("GET", path)[0], 401)
        cookie = self._pair()
        status, headers, data = self._request("GET", path, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(data, b"\x89PNG\r\n\x1a\nfixture")
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Content-Disposition"], "inline")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        record = _FakeBridgeHandler.records[-1]
        self.assertEqual(record["authorization"], f"Bearer {_TOKEN}")
        self.assertEqual(record["path"], "/mobile/v1/sessions/session-123/uploads/" + "a" * 32)
        self.assertEqual(self._request("GET", "/api/sessions/session-123/uploads/index.json", cookie=cookie)[0], 400)


class WebUiActivityTest(WebUiServerTestCase):
    def test_activity_proxy_auth_stop_origin_and_terminal_scope(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        path = "/api/sessions/session-123/activity"
        self.assertEqual(self._request("GET", path)[0], 401)
        cookie = self._pair()
        manager = Mock()
        manager.terminal_for_session.side_effect = lambda session: SimpleNamespace(terminal_id="term-test",opened_at=1) if session == "session-123" else None
        manager.poll_exit.return_value = None
        manager.close_terminal.return_value = True
        self.server.terminals = manager
        status, _, body = self._request("GET", path, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in json.loads(body)["processes"]], ["cmd-test", "webterm-term-test"])
        stop = path + "/cmd-test/stop"
        self.assertEqual(self._request("POST", stop, body={}, cookie=cookie, headers={"Origin":"https://foreign.test"})[0], 403)
        status, _, body = self._request("POST", stop, body={}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["requested"])
        self.assertEqual(self._request("POST", path+"/webterm-other/stop",body={},cookie=cookie)[0],404)
        manager.close_terminal.assert_not_called()
        self.assertEqual(self._request("POST", path+"/webterm-term-test/stop",body={},cookie=cookie)[0],200)
        manager.close_terminal.assert_called_once_with("term-test")
