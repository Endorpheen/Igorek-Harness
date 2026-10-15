"""Clipboard paste helpers (port of codex-rs/tui/src/clipboard_paste.rs).

This module reproduces the *structure* of the Rust source - error
types, the public ``paste_image_as_png`` / ``paste_image_to_temp_png``
entry points, and the path-normalization helpers - while delegating
to the Python equivalents (Pillow for image I/O, ``urllib`` for file
URLs, ``shlex`` for shell tokenization).
"""
from __future__ import annotations

import os
import shlex
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse


class PasteImageError(Exception):
    """Port of ``codex::clipboard_paste::PasteImageError``."""
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message

    @classmethod
    def clipboard_unavailable(cls, msg: str) -> "PasteImageError":
        return cls("clipboard unavailable", msg)

    @classmethod
    def no_image(cls, msg: str) -> "PasteImageError":
        return cls("no image on clipboard", msg)

    @classmethod
    def encode_failed(cls, msg: str) -> "PasteImageError":
        return cls("could not encode image", msg)

    @classmethod
    def io_error(cls, msg: str) -> "PasteImageError":
        return cls("io error", msg)


class EncodedImageFormat(Enum):
    Png = "PNG"
    Jpeg = "JPEG"
    Other = "IMG"

    @property
    def label(self) -> str:
        return self.value


@dataclass
class PastedImageInfo:
    width: int
    height: int
    encoded_format: EncodedImageFormat


# ---------------------------------------------------------------------------
# Clipboard image capture
# ---------------------------------------------------------------------------

_IS_ANDROID = sys.platform == "android"


def paste_image_as_png() -> Tuple[bytes, PastedImageInfo]:
    """Capture an image from the clipboard and return ``(png_bytes, info)``.

    Mirrors the Rust ``paste_image_as_png`` API. The Python port tries
    Pillow's image grabber if available, otherwise returns
    :class:`PasteImageError.clipboard_unavailable`.
    """
    if _IS_ANDROID:
        raise PasteImageError.clipboard_unavailable(
            "clipboard image paste is unsupported on Android"
        )
    try:
        from PIL import Image, ImageGrab  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dep
        raise PasteImageError.clipboard_unavailable(str(exc)) from exc

    img = None
    try:
        grabbed = ImageGrab.grab(clipboard=True)
        if grabbed is not None:
            img = grabbed
    except Exception:
        img = None
    if img is None:
        raise PasteImageError.no_image("ImageGrab returned no image")
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()
    return png, PastedImageInfo(
        width=img.width, height=img.height, encoded_format=EncodedImageFormat.Png
    )


def _is_probably_wsl() -> bool:
    """WSL detection (port of ``is_probably_wsl``)."""
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="ignore") as f:
            version = f.read().lower()
        if "microsoft" in version or "wsl" in version:
            return True
    except OSError:
        pass
    return "WSL_DISTRO_NAME" in os.environ or "WSL_INTEROP" in os.environ


def _convert_windows_path_to_wsl(input_str: str) -> Optional[Path]:
    """Convert a Windows path (``C:\\foo\\bar``) to its WSL mapping.

    Port of ``convert_windows_path_to_wsl``.
    """
    if input_str.startswith("\\\\"):
        return None
    if not input_str or not input_str[0].isalpha():
        return None
    if len(input_str) < 3 or input_str[1] != ":":
        return None
    drive = input_str[0].lower()
    rest = input_str[2:].lstrip("\\/ ")
    result = Path(f"/mnt/{drive}")
    for component in rest.replace("\\", "/").split("/"):
        if component:
            result = result / component
    return result


def _try_dump_windows_clipboard_image() -> Optional[str]:
    """Use PowerShell to write the Windows clipboard image to a temp PNG."""
    import subprocess
    script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$img = Get-Clipboard -Format Image; "
        "if ($img -ne $null) { "
        "$p=[System.IO.Path]::GetTempFileName(); "
        "$p = [System.IO.Path]::ChangeExtension($p,'png'); "
        "$img.Save($p,[System.Drawing.Imaging.ImageFormat]::Png); "
        "Write-Output $p } else { exit 1 }"
    )
    for cmd in ("powershell.exe", "pwsh", "powershell"):
        try:
            out = subprocess.run(
                [cmd, "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            continue
        if out.returncode == 0:
            win_path = (out.stdout or "").strip()
            if win_path:
                return win_path
    return None


def _try_wsl_clipboard_fallback(
    error: PasteImageError,
) -> Tuple[Path, PastedImageInfo]:
    """Try the WSL PowerShell fallback (port of ``try_wsl_clipboard_fallback``)."""
    if not _is_probably_wsl() or error.kind not in (
        "clipboard unavailable",
        "no image on clipboard",
    ):
        raise error
    win_path = _try_dump_windows_clipboard_image()
    if not win_path:
        raise error
    mapped = _convert_windows_path_to_wsl(win_path)
    if mapped is None:
        raise error
    try:
        from PIL import Image  # type: ignore
        with Image.open(mapped) as im:
            w, h = im.size
    except Exception as exc:
        raise PasteImageError.io_error(str(exc)) from exc
    return mapped, PastedImageInfo(
        width=w, height=h, encoded_format=EncodedImageFormat.Png
    )


def paste_image_to_temp_png() -> Tuple[Path, PastedImageInfo]:
    """Capture an image, persist it to a temp file, and return the path.

    Port of ``paste_image_to_temp_png``.
    """
    if _IS_ANDROID:
        raise PasteImageError.clipboard_unavailable(
            "clipboard image paste is unsupported on Android"
        )
    try:
        png, info = paste_image_as_png()
    except PasteImageError as err:
        if sys.platform == "linux":
            return _try_wsl_clipboard_fallback(err)
        raise
    fd, path = tempfile.mkstemp(prefix="codex-clipboard-", suffix=".png")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png)
    except Exception as exc:
        raise PasteImageError.io_error(str(exc)) from exc
    return Path(path), info


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------

def _normalize_windows_path(input_str: str) -> Optional[Path]:
    drive = (
        bool(input_str)
        and input_str[0].isascii()
        and input_str[0].isalpha()
        and len(input_str) >= 3
        and input_str[1] == ":"
        and input_str[2] in ("\\", "/")
    )
    unc = input_str.startswith("\\\\")
    if not drive and not unc:
        return None
    if sys.platform == "linux" and _is_probably_wsl():
        converted = _convert_windows_path_to_wsl(input_str)
        if converted is not None:
            return converted
    return Path(input_str)


def normalize_pasted_path(pasted: str) -> Optional[Path]:
    """Normalize a pasted string that may be a filesystem path.

    Port of ``codex::clipboard_paste::normalize_pasted_path``.
    """
    pasted = pasted.strip()
    unquoted = pasted
    if unquoted.startswith('"') and unquoted.endswith('"'):
        unquoted = unquoted[1:-1]
    elif unquoted.startswith("'") and unquoted.endswith("'"):
        unquoted = unquoted[1:-1]

    # file:// URL → filesystem path
    if unquoted.startswith("file://"):
        parsed = urlparse(unquoted)
        if parsed.scheme == "file":
            return Path(parsed.path)

    if unquoted.startswith("file:"):
        parsed = urlparse(unquoted)
        if parsed.scheme == "file":
            return Path(parsed.path)

    if (win_path := _normalize_windows_path(unquoted)) is not None:
        return win_path

    parts = list(shlex.shlex(pasted, posix=True))
    if len(parts) == 1:
        part = parts[0]
        if (win_path := _normalize_windows_path(part)) is not None:
            return win_path
        return Path(part)
    return None


def pasted_image_format(path: Path) -> EncodedImageFormat:
    """Infer the encoded image format from a path's extension (port one-to-one)."""
    ext = path.suffix.lower().lstrip(".")
    if ext == "png":
        return EncodedImageFormat.Png
    if ext in ("jpg", "jpeg"):
        return EncodedImageFormat.Jpeg
    return EncodedImageFormat.Other


__all__ = [
    "EncodedImageFormat",
    "PastedImageInfo",
    "PasteImageError",
    "normalize_pasted_path",
    "paste_image_as_png",
    "paste_image_to_temp_png",
    "pasted_image_format",
]
