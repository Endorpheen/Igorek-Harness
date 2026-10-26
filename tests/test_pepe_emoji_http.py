"""Emoji delivery uses real owned loopback HTTP and disposable paired clients."""

import hashlib
import http.client
import json

import pytest

from factory.core.pepe_emoji import catalog, emoji_media
from factory.core.mobile_bridge.server import API_PREFIX


@pytest.fixture
def bridge():
    from test_mobile_bridge_server import ServerTestCase

    owned = ServerTestCase(methodName="runTest")
    owned.setUp()
    try:
        yield owned
    finally:
        owned.tearDown()


def get(bridge, suffix, token=None):
    connection = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)
    headers = {"Authorization": "Bearer " + token} if token else {}
    connection.request("GET", API_PREFIX + suffix, headers=headers)
    response = connection.getresponse()
    data = response.read()
    status = response.status
    headers = dict(response.getheaders())
    connection.close()
    return status, headers, data


def test_catalog_and_all_gifs_require_auth_and_match_bundled_digests(bridge):
    token = bridge._pair()
    status, _, data = get(bridge, "/emojis/catalog", token)
    assert status == 200 and json.loads(data) == catalog()
    for row in catalog()["emojis"]:
        status, headers, data = get(bridge, "/emojis/media/" + row["id"], token)
        assert status == 200 and headers["Content-Type"] == "image/gif"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert "private" in headers["Cache-Control"]
        assert (
            data == emoji_media(row["id"])
            and hashlib.sha256(data).hexdigest() == row["sha256"]
        )


@pytest.mark.parametrize("path", ["/emojis/catalog", "/emojis/media/pepe_think"])
def test_unpaired_client_cannot_download_emoji_assets(bridge, path):
    status, _, data = get(bridge, path)
    assert status == 401 and not data.startswith(b"GIF")


def test_unknown_path_and_revoked_client_are_refused(bridge):
    token = bridge._pair()
    for identity in ["pepe_missing", "..%2Fprivate", "pepe_THINK"]:
        status, _, data = get(bridge, "/emojis/media/" + identity, token)
        assert status == 404 and not data.startswith(b"GIF")
    device = bridge.bridge.store.authenticate(token)
    bridge.bridge.store.revoke_device(device["device_id"])
    assert get(bridge, "/emojis/media/pepe_think", token)[0] == 401
