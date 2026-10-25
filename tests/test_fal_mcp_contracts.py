"""Offline image-adapter contracts; generated text and HTTP requests never leave fixtures."""

import base64
import io
import json
from pathlib import Path
from urllib import error

import pytest

from factory.mcp_servers import fal_image_server as f


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for key in list(f.os.environ):
        if key.startswith("FAL_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(f, "__file__", str(tmp_path / "factory/mcp_servers/server.py"))

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected live image API call")

    monkeypatch.setattr(f.urllib_request, "build_opener", forbidden)
    return tmp_path


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("YES", True),
        ("off", False),
        ("0", False),
        ("1", True),
        (None, False),
        ("invalid", False),
    ],
)
def test_boolean_options_accept_supported_forms(value, expected):
    assert f._parse_bool(value) is expected


@pytest.mark.parametrize(
    "size,expected",
    [
        ("square", "square"),
        ("640x480", {"width": 640, "height": 480}),
        ("640 X 480", {"width": 640, "height": 480}),
        ({"width": 640, "height": 480}, {"width": 640, "height": 480}),
    ],
)
def test_supported_image_sizes_are_normalized(size, expected):
    assert f._normalize_image_size(size) == expected


@pytest.mark.parametrize(
    "size",
    [
        "unsupported",
        {"width": 0, "height": 1},
        {"width": "bad", "height": 1},
        "0x480",
        "640x0",
    ],
)
def test_invalid_or_zero_dimensions_are_rejected(size):
    with pytest.raises(RuntimeError):
        f._normalize_image_size(size)


def test_generation_payload_keeps_controls_and_clamps_image_count():
    model, body = f._build_fal_payload(
        {
            "prompt": " Fixture image ",
            "model": "fixture/model",
            "image_size": "640x480",
            "quality": "HIGH",
            "num_images": 99,
            "output_format": "jpeg",
            "sync_mode": "yes",
        }
    )
    assert model == "fixture/model" and body == {
        "prompt": "Fixture image",
        "image_size": {"width": 640, "height": 480},
        "quality": "high",
        "num_images": 4,
        "output_format": "jpeg",
        "sync_mode": True,
    }


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"prompt": " "},
        {"prompt": "fixture", "quality": "invalid"},
        {"prompt": "fixture", "output_format": "invalid"},
        {"prompt": "fixture", "num_images": "invalid"},
    ],
)
def test_invalid_generation_fields_fail_before_transport(fields):
    with pytest.raises(RuntimeError):
        f._build_fal_payload(fields)


def test_edit_encodes_only_fixture_files_and_optional_mask(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"fixture image")
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"fixture mask")
    body = f._build_edit_payload(
        {"prompt": "Fixture edit", "image_path": str(image), "mask_path": str(mask)}
    )
    assert base64.b64decode(body["image_urls"][0].split(",", 1)[1]) == b"fixture image"
    assert base64.b64decode(body["mask_image_url"].split(",", 1)[1]) == b"fixture mask"
    for fields in [
        {},
        {"prompt": "fixture"},
        {"prompt": "fixture", "image_path": str(tmp_path / "missing.png")},
    ]:
        with pytest.raises(RuntimeError):
            f._build_edit_payload(fields)


@pytest.mark.parametrize(
    "payload,expected",
    [
        (
            {"images": [{"url": " https://fixture.invalid/a "}, None]},
            ["https://fixture.invalid/a"],
        ),
        (
            {"data": [{"url": "https://fixture.invalid/b"}]},
            ["https://fixture.invalid/b"],
        ),
        ({"url": "https://fixture.invalid/c"}, ["https://fixture.invalid/c"]),
        ({}, []),
    ],
)
def test_image_url_shapes_are_extracted_without_fetching(payload, expected):
    assert f._pick_image_urls(payload) == expected


def test_image_count_guard_rejects_excess_results():
    with pytest.raises(RuntimeError, match="image-count"):
        f._pick_image_urls({"images": [{"url": str(i)} for i in range(5)]})


def test_filenames_preserve_requested_stems_for_multiple_outputs():
    assert f._resolve_filename("Fixture", "name.webp", "png", 2) == "name-2.webp"
    assert f._resolve_filename("Fixture", "name", "jpeg", 2) == "name-2.jpg"
    assert f._resolve_filename("Fixture", "name.png", "png", 1) == "name.png"
    assert f._slugify("  A fixture image!  ") == "a-fixture-image"
    assert f._slugify("!!!") == "fal-image"


@pytest.mark.parametrize("edit", [False, True])
def test_generate_and_edit_save_only_into_fixture_output_directory(
    tmp_path, monkeypatch, edit
):
    image = tmp_path / "source.png"
    image.write_bytes(b"fixture")
    captured = []
    monkeypatch.setattr(
        f,
        "_request_json",
        lambda url, payload: (
            captured.append((url, payload))
            or {
                "images": [
                    {"url": "https://fixture.invalid/a"},
                    {"url": "https://fixture.invalid/b"},
                ]
            }
        ),
    )

    def download(url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture result")

    monkeypatch.setattr(f, "_download_file", download)
    fields = {
        "prompt": "Fixture image",
        "output_dir": str(tmp_path / "output"),
        "filename": "fixture.png",
        "image_path": str(image),
    }
    result = f._edit_image(fields) if edit else f._generate_image(fields)
    assert [Path(p).name for p in result["saved_files"]] == [
        "fixture.png",
        "fixture-2.png",
    ]
    assert all(Path(p).read_bytes() == b"fixture result" for p in result["saved_files"])
    assert "STATUS: COMPLETE" in result["content"][0]["text"]
    assert len(captured) == 1


def test_missing_urls_are_not_reported_as_completed_generation(monkeypatch):
    monkeypatch.setattr(f, "_request_json", lambda *a: {})
    with pytest.raises(RuntimeError, match="URL"):
        f._generate_image({"prompt": "Fixture"})


@pytest.mark.parametrize("operation", ["request", "download"])
@pytest.mark.parametrize("failure", ["http", "network", "oversize"])
def test_transport_failures_and_limits_are_explicit(
    tmp_path, monkeypatch, operation, failure
):
    monkeypatch.setenv("FAL_KEY", "fixture-token")
    monkeypatch.setattr(f, "MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(f, "MAX_MEDIA_BYTES", 4)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=-1):
            return b"xxxxx"[:size]

    def open_request(*args, **kwargs):
        if failure == "http":
            raise error.HTTPError(
                "https://fixture.invalid", 429, "fixture", {}, io.BytesIO(b"rate")
            )
        if failure == "network":
            raise error.URLError("fixture offline")
        return Response()

    monkeypatch.setattr(
        f.urllib_request,
        "build_opener",
        lambda *a: type("Opener", (), {"open": staticmethod(open_request)})(),
    )
    target = tmp_path / "never-created.png"
    with pytest.raises(RuntimeError):
        f._request_json(
            "https://fixture.invalid", {"fixture": 1}
        ) if operation == "request" else f._download_file(
            "https://fixture.invalid", target
        )
    assert not target.exists()


def test_request_requires_key_and_carries_unicode_only_to_fake_transport(monkeypatch):
    with pytest.raises(RuntimeError, match="FAL_KEY"):
        f._request_json("https://fixture.invalid", {})
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=-1):
            return b'{"images":[]}'

    def send(request, **kwargs):
        calls.append(request)
        return Response()

    monkeypatch.setenv("FAL_KEY", "fixture-key")
    monkeypatch.setattr(
        f.urllib_request,
        "build_opener",
        lambda *a: type("Opener", (), {"open": staticmethod(send)})(),
    )
    assert f._request_json("https://fixture.invalid", {"prompt": "Привет"}) == {
        "images": []
    }
    assert (
        json.loads(calls[0].data) == {"prompt": "Привет"}
        and calls[0].get_header("Authorization") == "Key fixture-key"
    )


def test_stdio_mcp_handshake_and_dispatch_are_offline(monkeypatch, capsys):
    monkeypatch.setattr(
        f,
        "_generate_image",
        lambda a: {"content": [{"type": "text", "text": a["prompt"]}]},
    )
    monkeypatch.setattr(
        f,
        "_edit_image",
        lambda a: {"content": [{"type": "text", "text": "fixture edit"}]},
    )
    requests = [
        {"id": 1, "method": "initialize"},
        {"id": 2, "method": "tools/list"},
        {"id": 3, "method": "ping"},
        {"method": "notifications/initialized"},
        {
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "fal_generate_image",
                "arguments": {"prompt": "Fixture"},
            },
        },
        {"id": 5, "method": "tools/call", "params": {"name": "fal_edit_image"}},
        {"id": 6, "method": "tools/call", "params": {"name": "unknown"}},
        {"id": 7, "method": "unknown"},
    ]
    monkeypatch.setattr(
        f.sys, "stdin", io.StringIO("\n" + "\n".join(json.dumps(r) for r in requests))
    )
    assert f.main() == 0
    replies = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6, 7]
    assert replies[0]["result"]["serverInfo"]["name"] == "FalImage"
    assert (
        replies[-2]["error"]["code"] == -32602
        and replies[-1]["error"]["code"] == -32601
    )


def test_malformed_stdio_line_does_not_reuse_previous_request_identity(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        f.sys, "stdin", io.StringIO('{"id":42,"method":"ping"}\nmalformed JSON\n')
    )
    f.main()
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["id"] == 42 and rows[1]["id"] is None and "error" in rows[1]
