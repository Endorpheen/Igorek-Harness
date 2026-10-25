"""Pasted path and image adapter contracts with a synthetic clipboard boundary."""

import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from factory.core.tui import clipboard_paste as p


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(p, "_is_probably_wsl", lambda: False)
    monkeypatch.setattr(p, "_IS_ANDROID", False)
    monkeypatch.setattr(p.tempfile, "tempdir", str(tmp_path))


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/tmp/image.png", "/tmp/image.png"),
        ("relative.png", "relative.png"),
        ("'/tmp/image file.png'", "/tmp/image file.png"),
        ('"/tmp/image file.png"', "/tmp/image file.png"),
        ("file:///tmp/image%20file.png", "/tmp/image file.png"),
        ("file:/tmp/image.png", "/tmp/image.png"),
        ("two paths", None),
        ("", None),
        ('"unfinished', None),
    ],
)
def test_pasted_paths_preserve_spaces_and_uri_encoding(text, expected):
    result = p.normalize_pasted_path(text)
    assert (None if result is None else str(result)) == expected


@pytest.mark.parametrize(
    "source,expected",
    [
        (r"C:\Users\fixture\image.png", "/mnt/c/Users/fixture/image.png"),
        ("D:/images/file.jpg", "/mnt/d/images/file.jpg"),
        (r"\\server\share\image.png", None),
        ("relative.png", None),
        ("C:", None),
    ],
)
def test_windows_to_wsl_path_mapping_is_pure(source, expected):
    result = p._convert_windows_path_to_wsl(source)
    assert (None if result is None else str(result)) == expected


def test_windows_normalization_uses_wsl_mapping_only_when_detected(monkeypatch):
    monkeypatch.setattr(p.sys, "platform", "linux")
    monkeypatch.setattr(p, "_is_probably_wsl", lambda: True)
    assert p.normalize_pasted_path(r"C:\Users\fixture\image.png") == Path(
        "/mnt/c/Users/fixture/image.png"
    )


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("image.PNG", p.EncodedImageFormat.Png),
        ("image.jpeg", p.EncodedImageFormat.Jpeg),
        ("image.JPG", p.EncodedImageFormat.Jpeg),
        ("file.bin", p.EncodedImageFormat.Other),
    ],
)
def test_image_suffixes_are_case_insensitive(filename, expected):
    assert p.pasted_image_format(Path(filename)) is expected


@pytest.mark.parametrize(
    "method,kind",
    [
        ("clipboard_unavailable", "clipboard unavailable"),
        ("no_image", "no image on clipboard"),
        ("encode_failed", "could not encode image"),
        ("io_error", "io error"),
    ],
)
def test_clipboard_errors_keep_machine_kind_and_detail(method, kind):
    error = getattr(p.PasteImageError, method)("fixture cause")
    assert (
        error.kind == kind
        and error.message == "fixture cause"
        and str(error) == kind + ": fixture cause"
    )


def test_png_persistence_only_writes_captured_bytes_to_own_temp_file(
    monkeypatch, tmp_path
):
    info = p.PastedImageInfo(2, 3, p.EncodedImageFormat.Png)
    monkeypatch.setattr(p, "paste_image_as_png", lambda: (b"synthetic-png-bytes", info))
    path, result = p.paste_image_to_temp_png()
    assert path.is_relative_to(tmp_path) and path.suffix == ".png"
    assert path.read_bytes() == b"synthetic-png-bytes" and result is info


@pytest.mark.parametrize(
    "entrypoint", [p.paste_image_as_png, p.paste_image_to_temp_png]
)
def test_android_unsupported_is_explicit_before_clipboard_access(
    monkeypatch, entrypoint
):
    monkeypatch.setattr(p, "_IS_ANDROID", True)
    with pytest.raises(p.PasteImageError, match="unsupported on Android"):
        entrypoint()


def test_image_grab_adapter_converts_and_serializes_without_real_clipboard(monkeypatch):
    pillow = types.ModuleType("PIL")
    grabber = types.ModuleType("PIL.ImageGrab")
    image_module = types.ModuleType("PIL.Image")
    converted = Mock(mode="RGBA", width=2, height=3)
    converted.save.side_effect = lambda output, format: output.write(b"synthetic-png")
    image = Mock(mode="RGB")
    image.convert.return_value = converted
    grabber.grabclipboard = Mock(return_value=image)
    pillow.ImageGrab = grabber
    pillow.Image = image_module
    monkeypatch.setitem(sys.modules, "PIL", pillow)
    monkeypatch.setitem(sys.modules, "PIL.ImageGrab", grabber)
    monkeypatch.setitem(sys.modules, "PIL.Image", image_module)
    encoded, info = p.paste_image_as_png()
    assert encoded == b"synthetic-png" and (info.width, info.height) == (2, 3)
    image.convert.assert_called_once_with("RGBA")
    grabber.grabclipboard.assert_called_once_with()
    grabber.grabclipboard.return_value = None
    with pytest.raises(p.PasteImageError, match="no image"):
        p.paste_image_as_png()
    grabber.grabclipboard.side_effect = OSError("fixture clipboard failure")
    with pytest.raises(p.PasteImageError, match="no image"):
        p.paste_image_as_png()


def test_non_wsl_fallback_preserves_original_error():
    error = p.PasteImageError.no_image("fixture")
    with pytest.raises(p.PasteImageError) as raised:
        p._try_wsl_clipboard_fallback(error)
    assert raised.value is error


def test_wsl_fallback_does_not_claim_success_without_dump(monkeypatch):
    monkeypatch.setattr(p, "_is_probably_wsl", lambda: True)
    monkeypatch.setattr(p, "_try_dump_windows_clipboard_image", lambda: None)
    with pytest.raises(p.PasteImageError, match="fixture"):
        p._try_wsl_clipboard_fallback(p.PasteImageError.no_image("fixture"))


def test_windows_dump_fallback_retries_only_mocked_executables(monkeypatch):
    import subprocess

    run = Mock(
        side_effect=[
            FileNotFoundError(),
            Mock(returncode=1, stdout=""),
            Mock(returncode=0, stdout=" C:\\fixture\\image.png\n"),
        ]
    )
    monkeypatch.setattr(subprocess, "run", run)
    assert p._try_dump_windows_clipboard_image() == r"C:\fixture\image.png"
    assert [call.args[0][0] for call in run.call_args_list] == [
        "powershell.exe",
        "pwsh",
        "powershell",
    ]
