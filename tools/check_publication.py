"""Check the public file inventory, documentation links and comment language."""
from __future__ import annotations

import argparse
import ast
import io
from pathlib import Path
import re
import sys
import tokenize
from urllib.parse import unquote, urlsplit

from build_wheel import REPO, source_files

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
PRIVATE_MARKERS = re.compile(r"/home/(?!example(?:/|\b)|user(?:/|\b)|agent(?:/|\b)|runner(?:/|\b)|opencode(?:/|\b))[a-z][a-z0-9_-]+/")
LANGUAGES = {".js": "javascript", ".cjs": "javascript", ".mjs": "javascript",
             ".ts": "typescript", ".tsx": "tsx", ".kt": "kotlin", ".kts": "kotlin",
             ".sh": "bash", ".css": "css", ".html": "html", ".toml": "toml",
             ".yaml": "yaml", ".yml": "yaml", ".xml": "xml", ".svg": "xml"}
THIRD_PARTY = ("factory/web_ui/static/vendor/",
               "android/app/src/main/assets/terminal/xterm.js",
               "android/app/src/main/assets/terminal/xterm.css",
               "android/app/src/main/assets/terminal/addon-fit.js")


def comments(path: Path, text: str):
    if path.suffix == ".py":
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                yield token.start[0], token.string
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                value = ast.get_docstring(node, clean=False)
                if value:
                    yield node.body[0].lineno, value
    elif path.suffix in LANGUAGES:
        from tree_sitter_language_pack import get_parser
        raw = text.encode()
        parser = get_parser(LANGUAGES[path.suffix])
        tree = parser.parse(raw)
        root = tree.root_node
        stack = [root]
        while stack:
            node = stack.pop()
            if "comment" in node.type.lower():
                yield node.start_point[0] + 1, raw[node.start_byte:node.end_byte].decode()
            elif path.suffix == ".html" and node.type == "raw_text" and node.parent.type in {"script_element", "style_element"}:
                language = "javascript" if node.parent.type == "script_element" else "css"
                nested_raw = raw[node.start_byte:node.end_byte]
                nested_parser = get_parser(language)
                nested_tree = nested_parser.parse(nested_raw)
                nested = [nested_tree.root_node]
                while nested:
                    child = nested.pop()
                    if "comment" in child.type.lower():
                        yield node.start_point[0] + child.start_point[0] + 1, nested_raw[child.start_byte:child.end_byte].decode()
                    else:
                        nested.extend(reversed(child.children))
            else:
                stack.extend(reversed(node.children))
    elif path.name.endswith(".env") or path.suffix in {".service", ".target"} or path.name == ".gitignore":
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                yield number, line


def check(root: Path = REPO, *, language: bool = True) -> list[str]:
    problems = []
    inventory = source_files(root)
    for relative in inventory:
        path = root / relative
        name = relative.as_posix()
        if path.is_symlink():
            problems.append(f"{name}: source symlink requires explicit review")
            continue
        if name.startswith(("Archive/", "docs/local/", ".toolchain", ".venv/", "build/")) or path.suffix in {".db", ".sqlite", ".sqlite3", ".jks", ".keystore", ".p12", ".pfx"}:
            problems.append(f"{name}: local data or build output in public inventory")
        try:
            text = path.read_text()
        except UnicodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if PRIVATE_MARKERS.search(line):
                problems.append(f"{name}:{number}: private workstation or recipient marker")
        if path.suffix.lower() == ".md":
            if relative.parent == Path('.') and not (
                path.name.startswith("README") or path.name in {"AGENTS.md", "CLAUDE.md"}
            ):
                problems.append(f"{name}: ordinary root Markdown belongs in docs")
            for target in re.findall(r"\]\(([^\s)]+)\)", text):
                url = urlsplit(target)
                if url.scheme or url.netloc or not url.path:
                    continue
                if relative.is_relative_to(Path('docs/wiki/content')):
                    repository_path = Path(unquote(url.path))
                    if repository_path.parts and repository_path.parts[0] not in {'.', '..'}:
                        source = (root / repository_path).resolve()
                        if source.is_relative_to(root.resolve()) and any(
                            item == repository_path or item.is_relative_to(repository_path)
                            for item in inventory
                        ):
                            continue
                destination = (path.parent / unquote(url.path)).resolve()
                if not destination.is_relative_to(root.resolve()) or not destination.exists():
                    problems.append(f"{name}: broken or nonportable local link")
        if language and not name.startswith(THIRD_PARTY):
            for line, comment in comments(path, text):
                if CYRILLIC.search(comment):
                    problems.append(f"{name}:{line}: non-English comment or docstring")
    return problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-language", action="store_true")
    args = parser.parse_args()
    errors = check(language=not args.skip_language)
    for error in errors:
        print(error)
    print(f"Publication contracts: {len(errors)} problem(s)")
    sys.exit(bool(errors))
