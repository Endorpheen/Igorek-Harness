#!/usr/bin/env python3
"""Integration tests for the terminal in the web interface: route for ticket issuance, CPP/WS checks, static analysis.

Live browser is not required: deterministic contracts are verified —
presence and protection of the ticket issuance route, refusal without associated cookie, WS handshake rejections before upgrade, delivery of vendor terminal assets, and presence of translation strings ru/en. The behavior of the PTY and WebSocket themselves is covered by separate sets.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SERVER_SOURCE = (REPO_ROOT / "factory" / "web_ui" / "server.py").read_text(encoding="utf-8")
INDEX_SOURCE = (REPO_ROOT / "factory" / "web_ui" / "static" / "index.html").read_text(encoding="utf-8")
APP_SOURCE = (REPO_ROOT / "factory" / "web_ui" / "static" / "app.js").read_text(encoding="utf-8")
CSS_SOURCE = (REPO_ROOT / "factory" / "web_ui" / "static" / "app.css").read_text(encoding="utf-8")
TERMINAL_SOURCE = (REPO_ROOT / "factory" / "web_ui" / "static" / "terminal.js").read_text(encoding="utf-8")

VENDOR = REPO_ROOT / "factory" / "web_ui" / "static" / "vendor"


class ServerRoutesTest(unittest.TestCase):
    """Routes and their protection at the server level."""

    def test_ticket_route_registered(self) -> None:
        self.assertIn('parts[3] == "terminal" and method == "POST"', SERVER_SOURCE)
        self.assertIn("self._terminal_ticket(session_id)", SERVER_SOURCE)

    def test_ws_route_registered(self) -> None:
        self.assertIn('if path == "/api/terminal" and method == "GET":', SERVER_SOURCE)
        self.assertIn("self._terminal_ws()", SERVER_SOURCE)

    def test_ticket_requires_paired_cookie(self) -> None:
        block = SERVER_SOURCE.split("def _terminal_ticket", 1)[1].split("def ", 1)[0]
        self.assertIn("if not self._token():", block)
        self.assertIn("401", block)

    def test_ws_requires_exact_origin(self) -> None:
        block = SERVER_SOURCE.split("def _terminal_ws", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("if origin != self.origin:", block)
        self.assertIn("403", block)
        self.assertIn("Sec-Fetch-Site", block)

    def test_ws_validates_handshake_before_upgrade(self) -> None:
        block = SERVER_SOURCE.split("def _terminal_ws", 1)[1].split("\n    def ", 1)[0]
        upgrade_index = block.index("validate_handshake")
        switch_index = block.index("101")
        self.assertLess(upgrade_index, switch_index, "handshake проверяется до апгрейда")

    def test_terminal_manager_lives_on_the_server(self) -> None:
        self.assertIn("self.terminals = TerminalManager(", SERVER_SOURCE)

    def test_assets_registered(self) -> None:
        for name in ("terminal.js", "vendor/xterm.js", "vendor/xterm.css", "vendor/addon-fit.js"):
            with self.subTest(asset=name):
                self.assertIn(f'"{name}"', SERVER_SOURCE)


class VendoredAssetsTest(unittest.TestCase):
    """Vendor xterm files are present, with license and README."""

    def test_files_present(self) -> None:
        for name in ("xterm.js", "xterm.css", "addon-fit.js", "XTERM-LICENSE.txt"):
            with self.subTest(file=name):
                path = VENDOR / name
                self.assertTrue(path.is_file(), f"{name} отсутствует")
                self.assertGreater(path.stat().st_size, 1000)

    def test_readme_documents_versions_and_integrity(self) -> None:
        readme = (VENDOR / "README.txt").read_text(encoding="utf-8")
        self.assertIn("xterm.js", readme)
        self.assertIn("5.5.0", readme)
        self.assertIn("@xterm/addon-fit", readme)
        self.assertIn("0.10.0", readme)
        self.assertIn("sha512-", readme)
        self.assertIn("MIT", readme)

    def test_no_cdn_references(self) -> None:
        for name in ("xterm.js", "addon-fit.js", "terminal.js"):
            text = (VENDOR / name if name != "terminal.js" else REPO_ROOT / "factory" / "web_ui" / "static" / name).read_text(
                encoding="utf-8", errors="replace"
            )
            self.assertNotIn("cdn.jsdelivr.net", text)
            self.assertNotIn("unpkg.com", text)


class InterfaceMarkupTest(unittest.TestCase):
    """Terminal tab in the layout, styles, and i18n."""

    def test_tab_and_view_present(self) -> None:
        self.assertIn('id="side-tab-terminal"', INDEX_SOURCE)
        self.assertIn('id="side-view-terminal"', INDEX_SOURCE)
        self.assertIn('data-i18n="panel.tab.terminal"', INDEX_SOURCE)

    def test_statics_linked(self) -> None:
        self.assertIn('/vendor/xterm.css', INDEX_SOURCE)
        self.assertIn('/terminal.js', INDEX_SOURCE)

    def test_xterm_loaded_lazily(self) -> None:
        self.assertNotIn('<script src="/vendor/xterm.js"', INDEX_SOURCE)
        self.assertIn('var XTERM_SRC = "/vendor/xterm.js";', TERMINAL_SOURCE)

    def test_i18n_keys_ru_and_en(self) -> None:
        for key in (
            "panel.tab.terminal",
            "panel.caption.terminal",
            "terminal.connecting",
            "terminal.connected",
            "terminal.exited",
            "terminal.restart",
            "terminal.error.ticket",
            "terminal.error.connect",
            "terminal.reason.ticket_invalid",
            "terminal.reason.terminal_busy",
            "terminal.reason.terminal_limit",
        ):
            with self.subTest(key=key):
                self.assertGreaterEqual(APP_SOURCE.count(f'"{key}":'), 2, f"{key}: нужны ru и en")

    def test_terminal_tab_wired_into_panel(self) -> None:
        self.assertIn("terminal: byId(\"side-tab-terminal\")", APP_SOURCE)
        self.assertIn("terminal: byId(\"side-view-terminal\")", APP_SOURCE)
        self.assertIn('terminal: "panel.caption.terminal"', APP_SOURCE)
        self.assertIn("function renderTerminalTab()", APP_SOURCE)

    def test_css_defines_terminal_layout(self) -> None:
        for selector in (".side-view-terminal", ".terminal-box", ".terminal-screen", ".terminal-status"):
            with self.subTest(selector=selector):
                self.assertIn(selector, CSS_SOURCE)

    def test_ack_is_sent_after_render(self) -> None:
        # Core invariant for backpressure: acknowledgment (`ack`) is emitted from the callback record.
        self.assertIn("state.term.write(bytes, function ()", TERMINAL_SOURCE)
        self.assertIn('send(state, "ack", { seq: state.acked })', TERMINAL_SOURCE)

    def test_seq_persisted_for_reload(self) -> None:
        self.assertIn('var STORAGE_PREFIX = "igorek-web:term-seq:";', TERMINAL_SOURCE)
        self.assertIn("since_seq: state.acked", TERMINAL_SOURCE)
        self.assertIn("writeSeq(state.sessionId, seq)", TERMINAL_SOURCE)

    def test_control_messages_are_separate_kinds(self) -> None:
        for kind in ('"input"', '"resize"', '"ack"', '"open"'):
            with self.subTest(kind=kind):
                self.assertIn(kind, TERMINAL_SOURCE)

    def test_no_innerhtml_in_terminal_client(self) -> None:
        # Reference to `innerHTML` is allowed only in comment rules; assignment via `x.innerHTML =` is forbidden: CSP does not relax its interface.
        code = re.sub(r"/\*.*?\*/", "", TERMINAL_SOURCE, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        self.assertNotIn("innerHTML", code)
        self.assertNotIn("outerHTML", code)
        self.assertNotIn("insertAdjacentHTML", code)


if __name__ == "__main__":
    unittest.main()
