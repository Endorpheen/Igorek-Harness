"""Update transport and idle mouse frames preserve user-visible state."""
import io
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import cli_chat_idle as idle


def test_mouse_frame_tracks_current_reasoning_and_composer_rows():
    controller = Mock()
    app = SimpleNamespace(renderer=SimpleNamespace(rows_above_layout=7))
    state = {"visible": False, "lines": ["", "summary"], "expanded_lines": ["full", "details"]}
    idle._idle_mouse_frame_builder(app, controller, state)
    build = controller.set_frame_builder.call_args.args[0]
    collapsed = build()
    assert [row.screen_row for row in collapsed.rows] == [7]
    assert collapsed.region_for_row(7).kind == "reasoning"
    assert collapsed.composer_top == 9
    state["visible"] = True
    expanded = build()
    assert [row.screen_row for row in expanded.rows] == [7, 8]
    assert expanded.composer_top == 10


def test_mouse_frame_preserves_placeholder_for_hidden_reasoning():
    controller = Mock()
    app = SimpleNamespace(renderer=SimpleNamespace(rows_above_layout=4))
    idle._idle_mouse_frame_builder(app, controller, {"visible": False, "lines": [], "expanded_lines": ["detail"]})
    built = controller.set_frame_builder.call_args.args[0]()
    assert built.rows[0].screen_row == 4
    assert "Ctrl+O" in built.rows[0].plain
    assert built.composer_top == 6


def test_mouse_frame_gracefully_handles_unavailable_renderer():
    controller = Mock()
    app = SimpleNamespace(renderer=SimpleNamespace())
    idle._idle_mouse_frame_builder(app, controller, {})
    assert controller.set_frame_builder.call_args.args[0]() is None


def test_update_http_request_uses_timeout_and_accepts_only_json_objects(monkeypatch):
    opener = Mock(return_value=io.BytesIO(b'{"tag_name":"v1.2.3"}'))
    monkeypatch.setattr(idle.urllib.request, "urlopen", opener)
    assert idle._http_get_json("https://example.invalid/releases") == {"tag_name": "v1.2.3"}
    request = opener.call_args.args[0]
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("User-agent").endswith("/update-check")
    assert opener.call_args.kwargs["timeout"] == idle._UPDATE_HTTP_TIMEOUT


@pytest.mark.parametrize("payload", [b'not-json', b'[]', b'null', b'\xff'])
def test_update_http_rejects_malformed_or_non_object_json(monkeypatch, payload):
    monkeypatch.setattr(idle.urllib.request, "urlopen", lambda *_a, **_kw: io.BytesIO(payload))
    assert idle._http_get_json("https://example.invalid/releases") is None


@pytest.mark.parametrize("failure", [TimeoutError(), OSError(), urllib.error.URLError("offline"), ValueError()])
def test_update_http_handles_transport_failure(monkeypatch, failure):
    monkeypatch.setattr(idle.urllib.request, "urlopen", Mock(side_effect=failure))
    assert idle._http_get_json("https://example.invalid/releases") is None


@pytest.mark.parametrize("payload,expected", [
    ({"tag_name": " v2.0.0 ", "name": "ignored"}, "v2.0.0"),
    ({"tag_name": "", "name": " v1.9.0 "}, "v1.9.0"),
    ({"tag_name": None}, ""), ({}, ""), (None, ""),
])
def test_github_version_prefers_tag_then_release_name(monkeypatch, payload, expected):
    monkeypatch.setattr(idle, "_GITHUB_RELEASES_URL", "https://example.invalid/releases")
    monkeypatch.setattr(idle, "_http_get_json", lambda _: payload)
    assert idle._fetch_latest_from_github() == expected


def test_missing_release_url_does_not_make_a_request(monkeypatch):
    monkeypatch.setattr(idle, "_GITHUB_RELEASES_URL", "")
    lookup = Mock()
    monkeypatch.setattr(idle, "_http_get_json", lookup)
    assert idle._fetch_latest_from_github() == ""
    lookup.assert_not_called()
