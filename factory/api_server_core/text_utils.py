import re
from pathlib import Path
from typing import Any

from factory.api_server_core.file_policy import sanitize_local_path_candidate


def find_explicit_filename(text: str) -> str | None:
    filenames = find_explicit_filenames(text)
    return filenames[0] if filenames else None


def find_explicit_filenames(text: str) -> list[str]:
    if not text:
        return []
    filenames: list[str] = []
    seen: set[str] = set()
    patterns = (
        r"`(?P<path>(?:\.\.?/|/)?[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,10})`",
        r'["\'](?P<path>(?:\.\.?/|/)?[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,10})["\']',
        r"(?P<path>(?:\.\.?/|/)?[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,10})",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            candidate = match.group("path")
            if should_skip_filename_candidate(text, match.start("path"), match.end("path"), candidate):
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            filenames.append(candidate)
    return filenames


def should_skip_filename_candidate(text: str, start: int, end: int, candidate: str) -> bool:
    stem, _, ext = candidate.rpartition(".")
    if not stem or not ext:
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?", candidate):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?", stem) and ext.isdigit():
        return True
    if not any(ch.isalpha() for ch in ext):
        return True
    if candidate.count("/") > 12:
        return True
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before.isalnum() or before == "_":
        return True
    if after.isalnum() or after == "_":
        return True
    root = candidate.split("/", 1)[0].split(".", 1)[0]
    if "/" not in candidate and root in {
        "document",
        "window",
        "console",
        "process",
        "module",
        "exports",
        "this",
    }:
        return True
    if candidate.startswith(("http://", "https://")):
        return True
    if "/" not in candidate and candidate.lower().endswith(
        (
            ".ai",
            ".app",
            ".biz",
            ".co",
            ".com",
            ".dev",
            ".edu",
            ".io",
            ".net",
            ".org",
            ".ru",
            ".site",
            ".tech",
        )
    ):
        return True
    prefix = text[max(0, start - 24):start]
    if candidate.startswith("//") and re.search(r"[A-Za-z][A-Za-z0-9+.-]*:$", prefix):
        return True
    return False


def resolve_file_arg_name(tool: dict[str, Any]) -> str | None:
    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    for key in ("file_path", "path", "notebook_path"):
        if key in properties:
            return key
    return None


def extract_code_block(text: str) -> str | None:
    match = re.search(r"```(?:[A-Za-z0-9_+-]+)?\n(.*?)```", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def is_protected_instruction_file(path: str) -> bool:
    name = Path(path).name.lower()
    return name in {"claude.md", "agents.md", "agents.md.md"}


def clean_request_token(text: str) -> str:
    return text.strip().strip("\"'`").strip()


def extract_creation_content_from_user_text(text: str, filename: str) -> str | None:
    escaped = re.escape(filename)
    block_patterns = (
        rf"{escaped}\s+с\s+тремя\s+строками:\s*(.*?)(?=\n\s*(?:Потом|Затем|После этого)\b|\Z)",
        rf"{escaped}\s+с\s+содержимым\s*(.*?)(?=\n\s*(?:Потом|Затем|После этого)\b|\Z)",
    )
    for pattern in block_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        body = match.group(1).strip()
        if not body:
            continue
        lines = [line.rstrip() for line in body.splitlines()]
        non_empty = [line.strip() for line in lines if line.strip()]
        if "тремя строками" in pattern:
            if non_empty:
                return "\n".join(non_empty)
            continue
        if non_empty:
            if len(non_empty) == 1:
                return non_empty[0]
            return "\n".join(non_empty)
    return None


def extract_bash_file_target(command: str) -> str:
    command = command.splitlines()[0].strip()
    if not command:
        return ""

    redirect_match = re.search(r">\s*['\"]?(?P<path>[^'\"\s][^'\">]*)['\"]?", command)
    if redirect_match:
        return sanitize_local_path_candidate(redirect_match.group("path").strip()) or ""

    cat_match = re.search(r"\bcat\s+['\"]?(?P<path>[^'\"\s][^'\">]*)['\"]?", command)
    if cat_match:
        return sanitize_local_path_candidate(cat_match.group("path").strip()) or ""

    return ""


def extract_requested_replacements(text: str) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    patterns = (
        r"замени\s+(.+?)\s+на\s+(.+?)(?=,|\n|$)",
        r"значение\s+(.+?)\s+на\s+(.+?)(?=,|\n|$)",
    )
    for pattern in patterns:
        for old, new in re.findall(pattern, text, flags=re.IGNORECASE):
            old_clean = clean_request_token(old)
            new_clean = clean_request_token(new)
            if old_clean and new_clean and old_clean != new_clean:
                replacements.append((old_clean, new_clean))
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in replacements:
        if pair in seen:
            continue
        seen.add(pair)
        deduped.append(pair)
    return deduped


def extract_read_sections(result_text: str) -> list[str]:
    sections: list[str] = []
    seen: set[str] = set()
    patterns = [
        re.compile(r"^\s*(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("),
        re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(|:)"),
    ]
    for raw_line in result_text.splitlines():
        cleaned = re.sub(r"^\s*\d+→", "", raw_line).rstrip()
        for pattern in patterns:
            match = pattern.match(cleaned)
            if not match:
                continue
            section_name = match.group(1)
            if section_name in seen:
                break
            seen.add(section_name)
            sections.append(section_name)
            break
    return sections[:12]


def strip_read_line_numbers(result_text: str) -> str:
    cleaned_lines: list[str] = []
    for raw_line in result_text.splitlines():
        cleaned_lines.append(re.sub(r"^\s*\d+→\s?", "", raw_line))
    return "\n".join(cleaned_lines)


def extract_read_imports(result_text: str) -> list[str]:
    imports: list[str] = []
    seen: set[str] = set()
    for raw_line in strip_read_line_numbers(result_text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("import "):
            module = line[len("import ") :].split(",", 1)[0].strip()
        elif line.startswith("from "):
            module = line[len("from ") :].split(" import ", 1)[0].strip()
        else:
            continue
        if not module or module in seen:
            continue
        seen.add(module)
        imports.append(module)
    return imports[:12]


def extract_top_level_constants(result_text: str) -> list[str]:
    constants: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"^[A-Z][A-Z0-9_]{2,}\s*=")
    for raw_line in strip_read_line_numbers(result_text).splitlines():
        if raw_line.startswith((" ", "\t")):
            continue
        match = pattern.match(raw_line)
        if not match:
            continue
        name = raw_line.split("=", 1)[0].strip()
        if not name or name in seen:
            continue
        seen.add(name)
        constants.append(name)
    return constants[:12]


def extract_decorated_entrypoints(result_text: str) -> list[str]:
    entrypoints: list[str] = []
    seen: set[str] = set()
    lines = strip_read_line_numbers(result_text).splitlines()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line.startswith("@"):
            continue
        if not any(token in line for token in ("app.", "router.", "api_router.", "FastAPI", "asynccontextmanager")):
            continue
        for next_line in lines[index + 1 : index + 4]:
            stripped = next_line.strip()
            match = re.match(r"^(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
            if not match:
                continue
            name = match.group(1)
            if name in seen:
                break
            seen.add(name)
            entrypoints.append(name)
            break
    return entrypoints[:10]


def extract_main_entrypoints(result_text: str) -> list[str]:
    entrypoints: list[str] = []
    seen: set[str] = set()
    for raw_line in strip_read_line_numbers(result_text).splitlines():
        if raw_line.startswith((" ", "\t")):
            continue
        stripped = raw_line.strip()
        match = re.match(r"^(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)
        if not match:
            continue
        name = match.group(1)
        if name != "main":
            continue
        if name in seen:
            continue
        seen.add(name)
        entrypoints.append(name)
    return entrypoints[:10]


def sample_section_names(section_names: list[str], limit: int = 10) -> list[str]:
    if len(section_names) <= limit:
        return section_names
    head_count = min(4, limit)
    tail_count = min(3, max(0, limit - head_count - 1))
    remaining = limit - head_count - tail_count

    middle: list[str] = []
    if remaining > 0:
        middle_slice = section_names[head_count : len(section_names) - tail_count]
        if middle_slice:
            if len(middle_slice) <= remaining:
                middle = middle_slice
            else:
                if remaining == 1:
                    middle = [middle_slice[len(middle_slice) // 2]]
                else:
                    step = (len(middle_slice) - 1) / float(remaining - 1)
                    indexes = {round(i * step) for i in range(remaining)}
                    middle = [middle_slice[index] for index in sorted(indexes)[:remaining]]

    sampled: list[str] = []
    seen: set[str] = set()
    for item in [*section_names[:head_count], *middle, *section_names[-tail_count:]]:
        if item in seen:
            continue
        seen.add(item)
        sampled.append(item)
    return sampled[:limit]
