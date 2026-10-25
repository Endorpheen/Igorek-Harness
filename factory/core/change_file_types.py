"""Classify formats whose serialized bytes are not meaningful source lines."""

from pathlib import PurePosixPath

_NON_TEXT_EXTENSIONS = {
    "spreadsheet": frozenset(
        {"xlsx", "xls", "xlsm", "xlsb", "xlt", "xltx", "xltm", "ods", "ots", "numbers"}
    ),
    "video": frozenset(
        {"mp4", "m4v", "mov", "webm", "mkv", "avi", "mpeg", "mpg", "3gp", "ogv"}
    ),
    "audio": frozenset(
        {"mp3", "wav", "flac", "ogg", "m4a", "aac", "opus", "aiff", "wma"}
    ),
    "image": frozenset(
        {
            "jpg",
            "jpeg",
            "png",
            "gif",
            "webp",
            "bmp",
            "tif",
            "tiff",
            "heic",
            "heif",
            "avif",
            "ico",
        }
    ),
}


def non_text_change_kind(path: str) -> str | None:
    """Return a known non-text kind without opening or resolving a file."""
    extension = PurePosixPath(path.replace("\\", "/")).suffix.lower().lstrip(".")
    return next(
        (
            kind
            for kind, extensions in _NON_TEXT_EXTENSIONS.items()
            if extension in extensions
        ),
        None,
    )
