"""Local links keep real path identities when rendered in a transcript."""

import re
from pathlib import PurePosixPath

import pytest

from factory.core.tui import markdown_render as md
from factory.core.tui.theme import preview_theme


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/project/src/main.py#L12", "src/main.py#L12"),
        ("/project/src/main.py#L12C3-L19C8", "src/main.py#L12C3-L19C8"),
        ("/project/src/main.py:12:3", "src/main.py:12:3"),
        ("/project/src/main.py:12-19", "src/main.py:12-19"),
        ("/project/src/main.py:12–19", "src/main.py:12–19"),
        ("/project/src/my%20file.py", "src/my file.py"),
        ("/project/src/%D1%82%D0%B5%D1%81%D1%82.py", "src/тест.py"),
        ("/project/src/%FF.py", "src/%FF.py"),
        ("/project-other/main.py", "/project-other/main.py"),
        ("/projects/main.py", "/projects/main.py"),
        ("/project", "/project"),
        ("/project/", "/project/"),
        ("/outside/main.py", "/outside/main.py"),
        ("./main.py#L2", "./main.py#L2"),
        ("../main.py:2", "../main.py:2"),
        ("/project/main.py#heading", "main.py#heading"),
        ("file:///project/src/main.py#L2", "src/main.py#L2"),
        ("file://localhost/project/src/main.py", "src/main.py"),
        ("file:///project/src/my%20file.py#L2", "src/my file.py#L2"),
        ("file:///project/src/%D1%82%D0%B5%D1%81%D1%82.py", "src/тест.py"),
        ("file:///project/src/%FF.py", "src/%FF.py"),
        ("file:///project/src/my%2520file.py", "src/my%20file.py"),
        ("file://server/share/main.py", "//server/share/main.py"),
        ("file:///C:/work/main.py", "C:/work/main.py"),
    ],
)
def test_local_link_display_preserves_path_and_location(target, expected):
    assert md.render_local_link_target(target, PurePosixPath("/project")) == expected


@pytest.mark.parametrize(
    ("cwd", "target", "expected"),
    [
        ("/", "/main.py", "main.py"),
        ("/project/", "/project/src/main.py", "src/main.py"),
        ("C:/work", "C:/work/main.py", "main.py"),
        ("C:/work", "C:/workspace/main.py", "C:/workspace/main.py"),
        ("//server/share", "//server/share/main.py", "main.py"),
        ("//server/share", "//server/shared/main.py", "//server/shared/main.py"),
    ],
)
def test_path_shortening_requires_directory_boundary(cwd, target, expected):
    assert md.render_local_link_target(target, PurePosixPath(cwd)) == expected


@pytest.mark.parametrize("width", [12, 24, 80])
def test_markdown_render_retains_local_location_instead_of_label(width):
    with preview_theme("dark"):
        lines = md.render_markdown_lines_with_width_and_cwd(
            "Read [implementation](/project/src/main.py#L12) now.",
            width,
            PurePosixPath("/project"),
        )
    text = "".join(
        re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line.line).strip() for line in lines
    )
    assert "src/main.py#L12" in text
    assert "implementation" not in text
    assert "Read" in text and "now." in text


def test_markdown_render_does_not_shorten_neighbor_directory():
    with preview_theme("dark"):
        lines = md.render_markdown_lines_with_width_and_cwd(
            "[source](/project-other/src/main.py#L2)", 100, PurePosixPath("/project")
        )
    assert "/project-other/src/main.py#L2" in "".join(line.line for line in lines)
