"""Startup update checks tolerate unavailable or malformed optional metadata."""

import json
import os
from unittest.mock import Mock

import pytest
from rich.console import Console

from factory.core import cli_chat_idle as idle


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(idle, "_UPDATE_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(idle, "_factory_version", lambda: "1.2.3")
    monkeypatch.setattr(idle, "_fetch_latest_from_pypi", Mock(return_value="1.3.0"))
    monkeypatch.setattr(idle, "_fetch_latest_from_github", Mock(return_value=""))
    return tmp_path / "cache" / "update-status.json"


@pytest.mark.parametrize(
    "payload", [None, [], [1], 3, True, "text", {}, {"latest": ""}]
)
def test_invalid_cache_is_ignored_and_startup_checks_provider(cache, payload):
    cache.parent.mkdir()
    cache.write_text(json.dumps(payload))
    result = idle._get_update_status(Console())
    assert result[:2] == (True, "1.3.0")
    idle._fetch_latest_from_pypi.assert_called_once()
    assert json.loads(cache.read_text())["latest"] == "1.3.0"


@pytest.mark.parametrize("raw", ["", "{", "not json", "[1,"])
def test_broken_json_cache_is_replaced(cache, raw):
    cache.parent.mkdir()
    cache.write_text(raw)
    assert idle._get_update_status(Console())[1] == "1.3.0"
    assert json.loads(cache.read_text())["available"] is True


@pytest.mark.parametrize("available", [True, False])
def test_fresh_cache_avoids_network(cache, available):
    cache.parent.mkdir()
    cache.write_text(
        json.dumps(dict(available=available, latest="1.4.0", command="upgrade"))
    )
    result = idle._get_update_status(Console())
    assert result == ((True, "1.4.0", "upgrade") if available else None)
    idle._fetch_latest_from_pypi.assert_not_called()
    idle._fetch_latest_from_github.assert_not_called()


def test_expired_cache_is_refreshed(cache):
    cache.parent.mkdir()
    cache.write_text('{"available": true, "latest": "9.0.0"}')
    os.utime(cache, (1, 1))
    assert idle._get_update_status(Console())[1] == "1.3.0"


@pytest.mark.parametrize(
    "failure", [PermissionError("denied"), OSError("disk unavailable")]
)
def test_unwritable_cache_directory_does_not_break_startup(cache, monkeypatch, failure):
    monkeypatch.setattr(idle, "_update_cache_path", Mock(side_effect=failure))
    assert idle._get_update_status(Console())[1] == "1.3.0"


@pytest.mark.parametrize("version", ["", "0.0.0"])
def test_unknown_installed_version_does_not_query_network(cache, monkeypatch, version):
    monkeypatch.setattr(idle, "_factory_version", lambda: version)
    assert idle._get_update_status(Console()) is None
    idle._fetch_latest_from_pypi.assert_not_called()


@pytest.mark.parametrize("latest", ["1.2.3", "1.2.3.0", "v1.2.2", "1.1.9"])
def test_equal_or_older_release_is_cached_without_upgrade(cache, latest):
    idle._fetch_latest_from_pypi.return_value = latest
    assert idle._get_update_status(Console()) is None
    payload = json.loads(cache.read_text())
    assert payload["available"] is False
    assert payload["latest"] == latest
    assert payload["command"] == ""


def test_github_fallback_returns_source_specific_install_hint(cache):
    idle._fetch_latest_from_pypi.return_value = ""
    idle._fetch_latest_from_github.return_value = "v1.5.0"
    result = idle._get_update_status(Console())
    assert result[:2] == (True, "v1.5.0")
    assert "uv tool install --force" in result[2]


def test_no_provider_metadata_does_not_write_negative_cache(cache):
    idle._fetch_latest_from_pypi.return_value = ""
    assert idle._get_update_status(Console()) is None
    assert not cache.exists()


@pytest.mark.parametrize(
    "info", [None, [], [1], "text", 7, True, {}, {"version": None}]
)
def test_malformed_pypi_metadata_allows_github_fallback(cache, monkeypatch, info):
    monkeypatch.setattr(idle, "_fetch_latest_from_pypi", _REAL_FETCH_PYPI)
    monkeypatch.setattr(idle, "_http_get_json", lambda url: {"info": info})
    idle._fetch_latest_from_github.return_value = "v1.6.0"
    assert idle._get_update_status(Console())[1] == "v1.6.0"


_REAL_FETCH_PYPI = idle._fetch_latest_from_pypi


def test_cache_directory_replaced_by_file_does_not_break_startup(cache):
    cache.parent.write_text("unrelated file")
    assert idle._get_update_status(Console())[1] == "1.3.0"
    assert cache.parent.read_text() == "unrelated file"
