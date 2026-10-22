"""Resolve pinned preview libraries offline without granting network access."""
from __future__ import annotations

import base64
import hashlib
import re
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

MAX_PREVIEW_HTML_BYTES = 4 * 1024 * 1024
_THREE_SHA256 = "9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2"
_THREE_URLS = {
    ("cdnjs.cloudflare.com", "/ajax/libs/three.js/r128/three.min.js"),
    ("cdn.jsdelivr.net", "/npm/three@0.128.0/build/three.min.js"),
    ("unpkg.com", "/three@0.128.0/build/three.min.js"),
}


@lru_cache(maxsize=1)
def _three_data_url() -> str:
    data = Path(__file__).with_name("static").joinpath("vendor/three-r128.min.js").read_bytes()
    if hashlib.sha256(data).hexdigest() != _THREE_SHA256:
        raise ValueError("Invalid bundled preview library")
    return "data:text/javascript;base64," + base64.b64encode(data).decode("ascii")


class _LibraryParser(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_offsets = [0] + [match.end() for match in re.finditer("\n", source)]
        self.replacements: list[tuple[int, int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script" or len(self.replacements) >= 4:
            return
        sources = [value for key, value in attrs if key == "src"]
        if len(sources) != 1 or not sources[0]:
            return
        try:
            url = urlsplit(sources[0])
            if url.scheme not in {"https", ""} or url.username or url.password or url.port is not None:
                return
        except ValueError:
            return
        if (url.hostname, url.path) not in _THREE_URLS:
            return
        attributes = "".join(" " + key + ("=\"" + escape(_three_data_url() if key == "src" else value, quote=True) + "\"" if value is not None else "") for key, value in attrs)
        line, column = self.getpos()
        start = self.line_offsets[line - 1] + column
        self.replacements.append((start, start + len(self.get_starttag_text()), "<script" + attributes + ("/>" if self.get_starttag_text().endswith("/>") else ">")))


def prepare_html_preview(data: bytes) -> bytes:
    if len(data) > MAX_PREVIEW_HTML_BYTES:
        return data
    try:
        source = data.decode("utf-8")
        parser = _LibraryParser(source)
        parser.feed(source)
        parser.close()
        for start, end, replacement in reversed(parser.replacements):
            source = source[:start] + replacement + source[end:]
        return source.encode("utf-8")
    except (UnicodeError, ValueError, OSError, AssertionError):
        return data
