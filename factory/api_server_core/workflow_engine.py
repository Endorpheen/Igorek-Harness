import os
import re
import shlex
import uuid
from pathlib import Path
from typing import Any

from factory.api_server_core.anthropic_messages import flatten_block_content
from factory.api_server_core.file_policy import extract_project_root
from factory.api_server_core.tool_history import ToolExchange, extract_tool_exchanges
from factory.api_server_core.tavily import (
    build_tavily_search_command,
    extract_successful_tavily_payload,
    extract_tavily_result_urls,
    extract_urls_from_text,
    is_tavily_search_command,
    is_url_fetch_bash_command,
    json_payload_looks_like_error,
    parse_json_payload,
    summarize_fetched_web_source,
)


PROJECT_ANALYSIS_MAX_SCAN_STEPS = 10

WORKFLOW_CONFIGS: dict[str, dict[str, Any]] = {
    "session_recall": {
        "max_steps": 3,
        "states": {
            "search": {
                "next": ["summarize", "stop"],
                "allowed_tool_classes": ["session_search_tool"],
            },
            "summarize": {"next": [], "allowed_tool_classes": []},
            "stop": {"next": [], "allowed_tool_classes": []},
        },
    },
    "local_implementation": {
        "max_steps": 20,
        "states": {
            "continue": {
                "next": ["continue", "blocked", "done"],
                "allowed_tool_classes": ["bash", "read_tool", "write_tool", "skills_tool"],
            },
            "blocked": {"next": ["done"], "allowed_tool_classes": []},
            "done": {"next": [], "allowed_tool_classes": []},
        },
    },
    "project_analysis": {
        "max_steps": PROJECT_ANALYSIS_MAX_SCAN_STEPS,
        "states": {
            "scan_root": {
                "next": ["scan_subdirs", "summarize"],
                "allowed_tool_classes": ["directory_scan_bash"],
            },
            "scan_subdirs": {
                "next": ["scan_subdirs", "scan_deep", "summarize"],
                "allowed_tool_classes": ["directory_scan_bash"],
            },
            "scan_deep": {
                "next": ["scan_deep", "summarize"],
                "allowed_tool_classes": ["directory_scan_bash"],
            },
            "summarize": {"next": [], "allowed_tool_classes": []},
        },
    },
    "file_investigation": {
        "max_steps": 6,
        "states": {
            "read_primary": {
                "next": ["read_primary_continuation", "read_related", "summarize"],
                "allowed_tool_classes": ["read_tool"],
            },
            "read_primary_continuation": {
                "next": ["read_primary_continuation", "read_related", "summarize"],
                "allowed_tool_classes": ["read_tool"],
            },
            "read_related": {
                "next": ["read_related", "summarize"],
                "allowed_tool_classes": ["read_tool"],
            },
            "summarize": {"next": [], "allowed_tool_classes": []},
        },
    },
    "failure_recovery": {
        "max_steps": 1,
        "states": {
            "retry_once": {
                "next": ["stop"],
                "allowed_tool_classes": ["read_tool", "bash", "directory_scan_bash", "tavily_search_bash", "http_fetch_bash"],
            },
            "stop": {"next": [], "allowed_tool_classes": []},
        },
    },
    "tavily_research": {
        "max_steps": 6,
        "states": {
            "search": {
                "next": ["fetch_sources", "stop"],
                "allowed_tool_classes": ["tavily_search_bash"],
            },
            "fetch_sources": {
                "next": ["fetch_sources", "summarize", "stop"],
                "allowed_tool_classes": ["http_fetch_bash"],
            },
            "summarize": {"next": [], "allowed_tool_classes": []},
            "stop": {"next": [], "allowed_tool_classes": []},
        },
    },
}


def _extract_bash_file_target(command: str) -> str:
    command = command.split("\n", 1)[0].strip()
    if not command:
        return ""
    redirect_match = re.search(r">\s*['\"]?(?P<path>[^'\"\s][^'\">]*)['\"]?", command)
    if redirect_match:
        candidate = redirect_match.group("path").strip()
        if any(ch in candidate for ch in ("\n", "\r", "\t", "\x00")) or len(candidate) > 240:
            return ""
        if candidate.startswith(">") or ":" in candidate:
            return ""
        return candidate
    cat_match = re.search(r"\bcat\s+['\"]?(?P<path>[^'\"\s][^'\">]*)['\"]?", command)
    if cat_match:
        candidate = cat_match.group("path").strip()
        if any(ch in candidate for ch in ("\n", "\r", "\t", "\x00")) or len(candidate) > 240:
            return ""
        if candidate.startswith(">") or ":" in candidate:
            return ""
        return candidate
    return ""


def _looks_like_tool_error_payload(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if stripped == "(Bash completed with no output)":
        return False
    if "<tool_use_error>" in stripped:
        return True
    if re.search(r"^\[tool_result[^\n]*\berror=true\b", stripped, flags=re.MULTILINE):
        return True
    if re.search(r"(^|\n)Exit code\s+[1-9]\d*", stripped):
        return True
    payload = parse_json_payload(stripped)
    return json_payload_looks_like_error(payload)


def _extract_tool_exchanges(req) -> list[ToolExchange]:
    return extract_tool_exchanges(
        req,
        flatten_block_content=flatten_block_content,
        looks_like_tool_error_payload=_looks_like_tool_error_payload,
        extract_bash_file_target=_extract_bash_file_target,
    )


def workflow_config(workflow_name: str | None) -> dict[str, Any]:
    if not workflow_name:
        return {}
    config = WORKFLOW_CONFIGS.get(workflow_name)
    return config if isinstance(config, dict) else {}


def workflow_state_config(workflow_name: str | None, workflow_step: str | None) -> dict[str, Any]:
    config = workflow_config(workflow_name)
    states = config.get("states")
    if not isinstance(states, dict) or not workflow_step:
        return {}
    state_config = states.get(workflow_step)
    return state_config if isinstance(state_config, dict) else {}


def workflow_has_completed_assistant_text(req, workflow_name: str) -> bool:
    last_relevant_assistant_index: int | None = None
    messages = getattr(req, "messages", [])
    for index, msg in enumerate(messages):
        if getattr(msg, "role", None) != "assistant":
            continue
        content = getattr(msg, "content", None)
        has_relevant_tool_use = False
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_name = str(block.get("name", "")).strip()
                if workflow_name == "project_analysis" and tool_name == "Bash":
                    has_relevant_tool_use = True
                    break
                if workflow_name == "file_investigation" and tool_name == "Read":
                    has_relevant_tool_use = True
                    break
                if workflow_name == "failure_recovery" and tool_name in {"Read", "Bash", "Edit", "Write", "Update", "MultiEdit"}:
                    has_relevant_tool_use = True
                    break
                if workflow_name == "local_implementation" and tool_name in {"Read", "Bash", "Edit", "Write", "Update", "MultiEdit", "skills_categories", "skills_list", "skill_view", "skill_scan", "skill_manage"}:
                    has_relevant_tool_use = True
                    break
                if workflow_name == "tavily_research" and tool_name == "Bash":
                    has_relevant_tool_use = True
                    break
                if workflow_name == "session_recall" and tool_name == "SessionSearch":
                    has_relevant_tool_use = True
                    break
        if has_relevant_tool_use:
            last_relevant_assistant_index = index

    if last_relevant_assistant_index is None:
        return False

    for msg in messages[last_relevant_assistant_index + 1 :]:
        if getattr(msg, "role", None) != "assistant":
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            if content.strip():
                return True
            continue
        has_tool_use = any(isinstance(block, dict) and block.get("type") == "tool_use" for block in (content or []))
        if has_tool_use:
            continue
        visible_text = ""
        for block in (content or []):
            if isinstance(block, dict):
                visible_text += str(block.get("text", ""))
        if visible_text.strip():
            return True
    return False


def is_directory_scan_bash_command(command: str) -> bool:
    command = command.strip()
    if not command:
        return False
    return bool(re.match(r"^(ls|find|rg --files)\b", command))


def extract_ls_directories(result_text: str) -> list[str]:
    directories: list[str] = []
    seen: set[str] = set()
    for raw_line in result_text.splitlines():
        line = raw_line.rstrip()
        if not line or line.endswith(":") or line.startswith("total "):
            continue
        if not line.startswith("d"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        name = parts[-1].rstrip("/")
        if name in {".", ".."}:
            continue
        if not re.match(r"^[A-Za-z0-9_.-]+$", name):
            continue
        if name in seen:
            continue
        seen.add(name)
        directories.append(name)
    return directories


def directory_scan_targets_from_command(command: str) -> list[str]:
    command = command.strip()
    if not command.startswith("ls "):
        return []
    tokens = command.split()[1:]
    targets: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            continue
        clean = token.rstrip("/").strip()
        if not clean or clean in {".", ".."}:
            continue
        targets.append(clean)
    return targets


def project_analysis_root_scan_target(state: Any) -> str:
    explicit_path = str(getattr(state, "explicit_user_absolute_path", "") or "").strip()
    if explicit_path:
        return explicit_path.rstrip("/")
    project_root = str(getattr(state, "project_root", "") or "").strip()
    if project_root:
        return project_root.rstrip("/")
    return "."


def extract_ls_entry_name(line: str) -> str:
    stripped = line.rstrip()
    if not stripped or stripped.startswith("total "):
        return ""
    if stripped.endswith(":"):
        return ""
    if stripped[0] not in {"d", "-", "l"}:
        return ""
    parts = stripped.split(None, 8)
    if len(parts) < 9:
        return ""
    return parts[8].split(" -> ", 1)[0].strip()


def summarize_directory_scan_result(command: str, result_text: str) -> str:
    targets = directory_scan_targets_from_command(command)
    text = result_text.strip()
    if not text:
        return f"scanned {targets or ['.']}: empty output"

    current_target = targets[0] if targets else "."
    sections: list[tuple[str, list[str]]] = []
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        if line.endswith(":") and not line.startswith(("total ", "dr", "-r", "lr")):
            if current_lines:
                sections.append((current_target, current_lines))
            current_target = line[:-1].rstrip("/").strip() or current_target
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        sections.append((current_target, current_lines))

    if not sections:
        sections = [(current_target, text.splitlines())]

    parts: list[str] = []
    for target, lines in sections[:8]:
        file_names: list[str] = []
        dir_names: list[str] = []
        for line in lines:
            entry = extract_ls_entry_name(line)
            if not entry:
                continue
            if line.startswith("d"):
                dir_names.append(entry)
            else:
                file_names.append(entry)
        snippet_bits: list[str] = []
        if dir_names:
            preview = ", ".join(dir_names[:6])
            if len(dir_names) > 6:
                preview += ", ..."
            snippet_bits.append(f"dirs: {preview}")
        if file_names:
            preview = ", ".join(file_names[:6])
            if len(file_names) > 6:
                preview += ", ..."
            snippet_bits.append(f"files: {preview}")
        if not snippet_bits:
            snippet_bits.append("no visible entries")
        parts.append(f"{target}: {'; '.join(snippet_bits)}")

    return " | ".join(parts)


def project_analysis_trackable_dirs(collected_directories: list[str]) -> list[str]:
    return [directory for directory in collected_directories if directory not in {".git", "__pycache__"}]


def project_analysis_deep_branch_candidates(covered_directories: list[str]) -> list[str]:
    priority_prefixes = (
        "src",
        "app",
        "apps",
        "server",
        "backend",
        "api",
        "core",
        "lib",
        "packages",
        "services",
        "modules",
        "features",
        "cmd",
    )
    candidates: list[str] = []
    seen: set[str] = set()
    for directory in covered_directories:
        normalized = directory.rstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if normalized.count("/") >= 1:
            continue
        lower_name = normalized.lower()
        if lower_name.startswith(priority_prefixes):
            candidates.append(normalized)
    return candidates


def project_analysis_covered_dirs(collected_targets: list[str], collected_directories: list[str]) -> list[str]:
    trackable = set(project_analysis_trackable_dirs(collected_directories))
    covered: list[str] = []
    seen: set[str] = set()
    for target in collected_targets:
        root_name = target.split("/", 1)[0].rstrip("/")
        if root_name in trackable and root_name not in seen:
            seen.add(root_name)
            covered.append(root_name)
    return covered


def project_analysis_covered_deep_branches(collected_targets: list[str], candidate_deep_branches: list[str]) -> list[str]:
    covered: list[str] = []
    seen: set[str] = set()
    for branch in candidate_deep_branches:
        branch_prefix = f"{branch.rstrip('/')}/"
        if any(target.startswith(branch_prefix) or target.rstrip("/") == branch.rstrip("/") for target in collected_targets):
            normalized = branch.rstrip("/")
            if normalized and normalized not in seen:
                seen.add(normalized)
                covered.append(normalized)
    return covered


def project_analysis_has_enough_context(state: Any) -> bool:
    if state.current_workflow != "project_analysis":
        return False
    if state.workflow_step_count < 2:
        return False
    if state.remaining_directories:
        return False
    if state.candidate_deep_branches and state.remaining_deep_branches:
        return False
    return bool(state.collected_summaries)


def summarize_read_result(target: str, result_text: str) -> str:
    stripped = result_text.strip()
    lines = stripped.splitlines()
    line_count = len(lines)
    head = ""
    for line in lines:
        candidate = re.sub(r"^\s*\d+\s*", "", line).strip()
        if candidate:
            head = candidate
            break
    suffix = f" | first: {head[:160]}" if head else ""
    return f"{target}: {line_count} lines read{suffix}"


def extract_read_sections(result_text: str) -> list[str]:
    sections: list[str] = []
    seen: set[str] = set()
    for raw_line in result_text.splitlines():
        line = re.sub(r"^\s*\d+\s*", "", raw_line).rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^(class|def)\s+[A-Za-z_][A-Za-z0-9_]*", stripped):
            section = stripped.split(":", 1)[0]
        elif re.match(r"^@[A-Za-z_][A-Za-z0-9_.()=, ]*$", stripped):
            section = stripped
        elif re.match(r"^[A-Z][A-Za-z0-9 _/-]{3,}$", stripped):
            section = stripped
        else:
            continue
        if section not in seen:
            seen.add(section)
            sections.append(section)
    return sections


def strip_read_line_numbers(result_text: str) -> str:
    stripped_lines = [re.sub(r"^\s*\d+\s*", "", line) for line in result_text.splitlines()]
    return "\n".join(stripped_lines)


def extract_read_imports(result_text: str) -> list[str]:
    imports: list[str] = []
    seen: set[str] = set()
    for raw_line in strip_read_line_numbers(result_text).splitlines():
        line = raw_line.strip()
        if not line.startswith(("import ", "from ")):
            continue
        if line in seen:
            continue
        seen.add(line)
        imports.append(line)
    return imports


def extract_top_level_constants(result_text: str) -> list[str]:
    constants: list[str] = []
    seen: set[str] = set()
    for raw_line in strip_read_line_numbers(result_text).splitlines():
        if raw_line.startswith((" ", "\t")):
            continue
        line = raw_line.strip()
        if not re.match(r"^[A-Z][A-Z0-9_]*\s*=", line):
            continue
        name = line.split("=", 1)[0].strip()
        if name in seen:
            continue
        seen.add(name)
        constants.append(name)
    return constants


def extract_decorated_entrypoints(result_text: str) -> list[str]:
    entrypoints: list[str] = []
    cleaned_lines = strip_read_line_numbers(result_text).splitlines()
    seen: set[str] = set()
    for index, raw_line in enumerate(cleaned_lines):
        line = raw_line.strip()
        if not line.startswith("@"):
            continue
        if not any(keyword in line for keyword in ("app.", "router.", "bp.", "click.", "typer.", "command", "route")):
            continue
        for candidate in cleaned_lines[index + 1 : index + 4]:
            stripped = candidate.strip()
            match = re.match(r"^(async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
            if not match:
                continue
            name = match.group(2)
            if name not in seen:
                seen.add(name)
                entrypoints.append(name)
            break
    return entrypoints


def extract_main_entrypoints(result_text: str) -> list[str]:
    entrypoints: list[str] = []
    seen: set[str] = set()
    cleaned = strip_read_line_numbers(result_text)
    for match in re.finditer(r"^(async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)", cleaned, flags=re.MULTILINE):
        name = match.group(2)
        if name in {"main", "run", "serve", "start"} and name not in seen:
            seen.add(name)
            entrypoints.append(name)
    if "__main__" in cleaned and "__main__" not in seen:
        entrypoints.append("__main__")
    return entrypoints


def sample_section_names(section_names: list[str], limit: int = 10) -> list[str]:
    if len(section_names) <= limit:
        return list(section_names)
    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count)
    sampled = list(section_names[:head_count])
    sampled.append("...")
    sampled.extend(section_names[-tail_count:])
    return sampled


def sample_head_tail(items: list[str], limit: int = 8) -> list[str]:
    if len(items) <= limit:
        return list(items)
    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count)
    sampled = list(items[:head_count])
    sampled.append("...")
    sampled.extend(items[-tail_count:])
    return sampled


def summarize_read_evidence(target: str, exchanges: list[ToolExchange]) -> str:
    if not exchanges:
        return f"{target}: no read evidence"
    aggregated_text = "\n".join(exchange.result_text for exchange in exchanges if exchange.result_text)
    imports = extract_read_imports(aggregated_text)
    sections = extract_read_sections(aggregated_text)
    constants = extract_top_level_constants(aggregated_text)
    decorated = extract_decorated_entrypoints(aggregated_text)
    main_entries = extract_main_entrypoints(aggregated_text)
    line_ranges = []
    for exchange in exchanges:
        raw_offset = exchange.tool_input_obj.get("offset", 1)
        raw_limit = exchange.tool_input_obj.get("limit", 0)
        if isinstance(raw_offset, int) and raw_offset > 0 and isinstance(raw_limit, int) and raw_limit > 0:
            line_ranges.append(f"{raw_offset}-{raw_offset + raw_limit - 1}")
    summary_parts = [f"{target}"]
    if line_ranges:
        summary_parts.append(f"ranges={', '.join(line_ranges)}")
    if imports:
        summary_parts.append(f"imports={', '.join(sample_head_tail(imports, limit=6))}")
    if constants:
        summary_parts.append(f"constants={', '.join(sample_head_tail(constants, limit=6))}")
    if sections:
        summary_parts.append(f"sections={', '.join(sample_section_names(sections, limit=10))}")
    if decorated:
        summary_parts.append(f"decorated_entrypoints={', '.join(decorated[:6])}")
    if main_entries:
        summary_parts.append(f"main_like={', '.join(main_entries[:6])}")
    return " | ".join(summary_parts)


def aggregate_file_investigation_summaries(successful_reads: list[ToolExchange]) -> list[str]:
    grouped: dict[str, list[ToolExchange]] = {}
    order: list[str] = []
    for exchange in successful_reads:
        target = exchange.target or ""
        if not target:
            continue
        if target not in grouped:
            grouped[target] = []
            order.append(target)
        grouped[target].append(exchange)
    return [summarize_read_evidence(target, grouped[target]) for target in order]


def file_investigation_successful_reads(req, read_result_looks_successful_fn) -> tuple[list[ToolExchange], str | None]:
    exchanges = _extract_tool_exchanges(req)
    successful_reads: list[ToolExchange] = []
    last_error: str | None = None
    for exchange in exchanges:
        exchange_is_error = exchange.is_error and not read_result_looks_successful_fn(exchange.result_text)
        if exchange_is_error:
            last_error = exchange.result_text[:400]
            continue
        if not read_result_looks_successful_fn(exchange.result_text):
            continue
        if exchange.tool_name != "Read" or not exchange.target:
            continue
        successful_reads.append(exchange)
    return successful_reads, last_error


def resolve_python_module_path(module_name: str, project_root: Path) -> str | None:
    if not module_name:
        return None
    path_parts = module_name.split(".")
    package_candidate = project_root.joinpath(*path_parts, "__init__.py")
    if package_candidate.exists():
        return str(package_candidate)
    module_candidate = project_root.joinpath(*path_parts).with_suffix(".py")
    if module_candidate.exists():
        return str(module_candidate)
    return None


def extract_related_python_files(result_text: str, target: str, project_root: str | None) -> list[str]:
    if not project_root or not target.endswith(".py"):
        return []
    root = Path(project_root)
    related: list[str] = []
    seen: set[str] = set()
    for raw_line in strip_read_line_numbers(result_text).splitlines():
        line = raw_line.strip()
        module_name = ""
        if line.startswith("from "):
            module_name = line.split()[1].strip()
        elif line.startswith("import "):
            module_name = line.split()[1].split(",", 1)[0].strip()
        if not module_name or module_name.startswith("."):
            continue
        resolved = resolve_python_module_path(module_name, root)
        if not resolved or resolved == target or resolved in seen:
            continue
        seen.add(resolved)
        related.append(resolved)
    return related


def file_investigation_related_files(successful_reads: list[ToolExchange], req, collected_targets: list[str]) -> list[str]:
    project_root = extract_project_root(req)
    related: list[str] = []
    seen: set[str] = set(collected_targets)
    for exchange in successful_reads:
        target = exchange.target or ""
        if not target:
            continue
        for resolved in extract_related_python_files(exchange.result_text, target, project_root):
            if resolved in seen:
                continue
            seen.add(resolved)
            related.append(resolved)
    return related


def file_investigation_continuation_read(successful_reads: list[ToolExchange], collected_targets: list[str], continued_read_input_fn) -> dict[str, Any] | None:
    if not successful_reads:
        return None
    latest_target = collected_targets[-1] if collected_targets else successful_reads[-1].target
    if not latest_target:
        return None
    for exchange in reversed(successful_reads):
        if exchange.target != latest_target:
            continue
        continuation = continued_read_input_fn(exchange)
        if continuation is not None:
            return continuation
    return None


def file_investigation_has_enough_context(state: Any, file_investigation_successful_reads_fn, file_investigation_continuation_read_fn) -> bool:
    if state.current_workflow != "file_investigation":
        return False
    unique_targets = [target for target in state.collected_targets if target]
    successful_reads, _ = file_investigation_successful_reads_fn(state.req)
    continuation_input = file_investigation_continuation_read_fn(successful_reads, unique_targets)
    if continuation_input is not None:
        return False
    return len(unique_targets) >= 2


def file_investigation_state(req, read_result_looks_successful_fn, file_investigation_related_files_fn, continued_read_input_fn) -> tuple[str | None, str | None, int, list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], str | None]:
    if workflow_has_completed_assistant_text(req, "file_investigation"):
        return None, None, 0, [], [], [], [], [], [], [], [], [], None
    successful_reads, last_error = file_investigation_successful_reads(req, read_result_looks_successful_fn)
    if not successful_reads:
        return None, None, 0, [], [], [], [], [], [], [], [], [], last_error

    collected_targets: list[str] = []
    seen_targets: set[str] = set()
    for exchange in successful_reads:
        target = exchange.target or ""
        if target and target not in seen_targets:
            seen_targets.add(target)
            collected_targets.append(target)

    continuation_input = file_investigation_continuation_read(successful_reads, collected_targets, continued_read_input_fn)
    workflow_step = "read_primary"
    if len(collected_targets) >= 2 and continuation_input is None:
        workflow_step = "summarize"
    elif continuation_input is not None:
        workflow_step = "read_primary_continuation"
    elif len(collected_targets) <= 1:
        workflow_step = "read_primary"
    else:
        workflow_step = "read_related"

    collected_summaries = aggregate_file_investigation_summaries(successful_reads)
    candidate_related_files = file_investigation_related_files_fn(successful_reads, req, collected_targets)
    return (
        "file_investigation",
        workflow_step,
        len(successful_reads),
        collected_targets,
        [],
        [],
        [],
        [],
        [],
        [],
        candidate_related_files,
        collected_summaries,
        last_error,
    )


def _is_recoverable_skill_manage_error(exchange: ToolExchange) -> bool:
    if exchange.tool_name != "skill_manage":
        return False
    text = str(exchange.result_text or "")
    recoverable_markers = (
        "SKILL.md must start with YAML frontmatter",
        "SKILL.md frontmatter is not closed",
        "Frontmatter must include",
        "YAML frontmatter parse error",
        "content is required for create",
        "content is required for edit",
        "old_string is required for patch",
        "new_string is required for patch",
    )
    return any(marker in text for marker in recoverable_markers)


def should_ignore_failure_recovery_exchange(exchange: ToolExchange) -> bool:
    if exchange.tool_name == "Bash" and bool(re.match(r"^(ls|pwd|git status)\b", str(exchange.tool_input_obj.get("command", "")).strip())):
        return True
    if _is_recoverable_skill_manage_error(exchange):
        return True
    return False


def failure_recovery_exchanges(req) -> tuple[list[ToolExchange], str | None]:
    exchanges = _extract_tool_exchanges(req)
    failed_exchanges: list[ToolExchange] = []
    last_error: str | None = None
    for exchange in exchanges:
        if should_ignore_failure_recovery_exchange(exchange):
            continue
        if not exchange.is_error:
            continue
        failed_exchanges.append(exchange)
        last_error = exchange.result_text
    return failed_exchanges, last_error


def failure_recovery_state(req) -> tuple[str | None, str | None, int, list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], str | None]:
    if workflow_has_completed_assistant_text(req, "failure_recovery"):
        return None, None, 0, [], [], [], [], [], [], [], [], [], None
    relevant_exchanges = [
        exchange
        for exchange in _extract_tool_exchanges(req)
        if not should_ignore_failure_recovery_exchange(exchange)
    ]
    if not relevant_exchanges:
        return None, None, 0, [], [], [], [], [], [], [], [], [], None
    latest_relevant = relevant_exchanges[-1]
    if not latest_relevant.is_error:
        return None, None, 0, [], [], [], [], [], [], [], [], [], None

    failed_exchanges = [exchange for exchange in relevant_exchanges if exchange.is_error]
    if not failed_exchanges:
        return None, None, 0, [], [], [], [], [], [], [], [], [], None
    last_error = failed_exchanges[-1].result_text
    latest_failed = failed_exchanges[-1]
    target = latest_failed.target or ""
    summary = f"{target} failed: {latest_failed.result_text[:200]}" if target else f"failed: {latest_failed.result_text[:200]}"
    workflow_step = "retry_once" if len(failed_exchanges) == 1 else "stop"
    collected_targets = [target] if target else []
    return (
        "failure_recovery",
        workflow_step,
        len(failed_exchanges),
        collected_targets,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [summary],
        last_error,
    )


def tavily_research_state(req, strip_system_reminder_fn) -> tuple[str | None, str | None, int, list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], str | None]:
    if workflow_has_completed_assistant_text(req, "tavily_research"):
        return None, None, 0, [], [], [], [], [], [], [], [], [], None

    exchanges = _extract_tool_exchanges(req)
    tavily_attempts: list[ToolExchange] = []
    successful_search: ToolExchange | None = None
    last_error: str | None = None
    for exchange in exchanges:
        if exchange.tool_name != "Bash":
            continue
        command = str(exchange.tool_input_obj.get("command", "")).strip()
        if not is_tavily_search_command(command):
            continue
        tavily_attempts.append(exchange)
        if exchange.is_error:
            cleaned_error = strip_system_reminder_fn(exchange.result_text or "").strip()
            if cleaned_error:
                last_error = cleaned_error[:500]
            continue
        if extract_successful_tavily_payload(command, exchange.result_text, strip_system_reminder_fn) is not None:
            successful_search = exchange

    if not tavily_attempts:
        return None, None, 0, [], [], [], [], [], [], [], [], [], None

    if successful_search is None:
        return (
            "tavily_research",
            "search",
            len(tavily_attempts),
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            last_error,
        )

    payload = extract_successful_tavily_payload(
        str(successful_search.tool_input_obj.get("command", "")).strip(),
        successful_search.result_text,
        strip_system_reminder_fn,
    )
    if payload is None:
        return None, None, 0, [], [], [], [], [], [], [], [], [], last_error

    allowed_urls = extract_tavily_result_urls(payload, limit=5)
    attempted_targets: list[str] = []
    fetched_summaries: list[str] = []
    seen_targets: set[str] = set()
    for exchange in exchanges:
        if exchange.index <= successful_search.index or exchange.tool_name != "Bash":
            continue
        command = str(exchange.tool_input_obj.get("command", "")).strip()
        if not is_url_fetch_bash_command(command, allowed_urls):
            continue
        fetch_urls = extract_urls_from_text(command)
        if not fetch_urls:
            continue
        target_url = fetch_urls[0]
        if target_url in seen_targets:
            continue
        seen_targets.add(target_url)
        attempted_targets.append(target_url)
        if exchange.is_error:
            cleaned_error = strip_system_reminder_fn(exchange.result_text or "").strip()
            if cleaned_error:
                last_error = cleaned_error[:500]
            continue
        fetched_summaries.append(summarize_fetched_web_source(target_url, exchange.result_text, strip_system_reminder_fn))

    remaining_urls = [url for url in allowed_urls if url not in set(attempted_targets)]
    workflow_step = "fetch_sources"
    if not remaining_urls and fetched_summaries:
        workflow_step = "summarize"

    return (
        "tavily_research",
        workflow_step,
        len(tavily_attempts) + len(attempted_targets),
        attempted_targets,
        [],
        [],
        [],
        [],
        [],
        [],
        allowed_urls,
        fetched_summaries,
        last_error,
    )


def session_recall_state(req, strip_system_reminder_fn) -> tuple[str | None, str | None, int, list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], str | None]:
    if workflow_has_completed_assistant_text(req, "session_recall"):
        return None, None, 0, [], [], [], [], [], [], [], [], [], None

    exchanges = _extract_tool_exchanges(req)
    recall_attempts: list[ToolExchange] = []
    successful_search: ToolExchange | None = None
    last_error: str | None = None
    for exchange in exchanges:
        if exchange.tool_name != "SessionSearch":
            continue
        recall_attempts.append(exchange)
        if exchange.is_error:
            cleaned_error = strip_system_reminder_fn(exchange.result_text or "").strip()
            if cleaned_error:
                last_error = cleaned_error[:500]
            continue
        payload = parse_json_payload(strip_system_reminder_fn(exchange.result_text or "").strip())
        if isinstance(payload, dict) and payload.get("success") is True:
            successful_search = exchange

    if not recall_attempts:
        return None, None, 0, [], [], [], [], [], [], [], [], [], None

    if successful_search is None:
        return (
            "session_recall",
            "search",
            len(recall_attempts),
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            last_error,
        )

    payload = parse_json_payload(strip_system_reminder_fn(successful_search.result_text or "").strip())
    if not isinstance(payload, dict):
        return None, None, 0, [], [], [], [], [], [], [], [], [], last_error

    results = payload.get("results")
    collected_targets: list[str] = []
    collected_summaries: list[str] = []
    if isinstance(results, list):
        for item in results[:5]:
            if not isinstance(item, dict):
                continue
            session_id = str(item.get("session", "") or "").strip()
            if session_id:
                collected_targets.append(session_id)
            excerpt = str(item.get("excerpt", "") or item.get("preview", "") or "").strip()
            last_active = str(item.get("last_active", "") or "").strip()
            entry_kind = str(item.get("entry_kind", "") or item.get("source", "") or "").strip()
            header_parts = []
            if session_id:
                header_parts.append(f"session={session_id}")
            if last_active:
                header_parts.append(f"last_active={last_active}")
            if entry_kind:
                header_parts.append(f"kind={entry_kind}")
            header = " | ".join(header_parts)
            if excerpt:
                collected_summaries.append(f"{header}\n{excerpt}" if header else excerpt)
            elif header:
                collected_summaries.append(header)

    return (
        "session_recall",
        "summarize",
        len(recall_attempts),
        collected_targets,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        collected_summaries,
        last_error,
    )


def workflow_completion_policy(state: Any, file_investigation_has_enough_context_fn) -> tuple[str | None, str | None]:
    workflow_name = state.current_workflow
    if not workflow_name:
        return None, None

    workflow_max_steps = int(workflow_config(workflow_name).get("max_steps", PROJECT_ANALYSIS_MAX_SCAN_STEPS))

    if workflow_name == "project_analysis":
        if project_analysis_has_enough_context(state):
            return "summarize", "sufficient_context"
        if state.workflow_step in {"scan_root", "scan_subdirs", "scan_deep"} and state.workflow_step_count >= workflow_max_steps:
            return "summarize", "max_steps"
        return None, None

    if workflow_name == "file_investigation":
        if file_investigation_has_enough_context_fn(state):
            return "summarize", "sufficient_context"
        if state.workflow_step in {"read_primary", "read_primary_continuation", "read_related"} and state.workflow_step_count >= workflow_max_steps:
            return "summarize", "max_steps"
        return None, None

    if workflow_name == "failure_recovery":
        if state.workflow_step == "stop":
            return "stop", "recovery_exhausted"
        if state.workflow_step == "retry_once" and state.workflow_step_count >= workflow_max_steps:
            return "stop", "max_steps"
        return None, None

    if workflow_name == "local_implementation":
        if state.workflow_step == "blocked":
            return "stop", "blocked"
        if state.workflow_step == "done":
            return "stop", "done"
        if state.workflow_step == "continue" and state.workflow_step_count >= workflow_max_steps:
            return "stop", "max_steps"
        return None, None

    if workflow_name == "tavily_research":
        if state.workflow_step == "summarize":
            return "summarize", "workflow_summary_ready"
        if state.workflow_step == "search" and state.workflow_step_count >= workflow_max_steps:
            return "stop", "max_steps"
        if state.workflow_step == "fetch_sources":
            remaining_urls = [url for url in state.candidate_related_files if url not in set(state.collected_targets)]
            if not remaining_urls and state.collected_summaries:
                return "summarize", "exhausted_urls"
            if state.workflow_step_count >= workflow_max_steps:
                return ("summarize", "max_steps") if state.collected_summaries else ("stop", "max_steps")
        if state.workflow_step == "stop":
            return "stop", "workflow_stop"
        return None, None

    if workflow_name == "session_recall":
        if state.workflow_step == "summarize":
            return "summarize", "workflow_summary_ready"
        if state.workflow_step == "search" and state.workflow_step_count >= workflow_max_steps:
            return "stop", "max_steps"
        if state.workflow_step == "stop":
            return "stop", "workflow_stop"
        return None, None

    return None, None


def workflow_followup_requirements(
    state: Any,
    *,
    file_investigation_has_enough_context_fn,
) -> dict[str, Any]:
    completion_action, completion_reason = workflow_completion_policy(
        state,
        file_investigation_has_enough_context_fn,
    )
    in_project_scan_workflow = (
        state.current_workflow == "project_analysis"
        and state.workflow_step in {"scan_root", "scan_subdirs", "scan_deep"}
    )
    in_file_investigation_workflow = (
        state.current_workflow == "file_investigation"
        and state.workflow_step in {"read_primary", "read_primary_continuation", "read_related"}
    )
    in_failure_recovery_workflow = (
        state.current_workflow == "failure_recovery"
        and state.workflow_step == "retry_once"
    )
    in_local_implementation_workflow = (
        state.current_workflow == "local_implementation"
        and state.workflow_step == "continue"
    )
    in_tavily_research_workflow = (
        state.current_workflow == "tavily_research"
        and state.workflow_step in {"search", "fetch_sources"}
    )
    in_session_recall_workflow = (
        state.current_workflow == "session_recall"
        and state.workflow_step == "search"
    )
    file_investigation_has_enough_context = file_investigation_has_enough_context_fn(state)
    force_project_scan_followup = (
        in_project_scan_workflow
        and (
            (state.workflow_step == "scan_root" and bool(state.collected_directories))
            or (state.workflow_step == "scan_subdirs" and bool(state.remaining_directories))
            or (state.workflow_step == "scan_deep" and bool(state.remaining_deep_branches))
        )
    )
    force_file_investigation_followup = (
        in_file_investigation_workflow
        and completion_action is None
        and not file_investigation_has_enough_context
    )
    force_failure_recovery_followup = (
        in_failure_recovery_workflow and completion_action is None
    )
    force_local_implementation_followup = (
        in_local_implementation_workflow and completion_action is None
    )
    force_tavily_research_followup = (
        in_tavily_research_workflow and completion_action is None
    )
    force_session_recall_followup = (
        in_session_recall_workflow and completion_action is None
    )
    workflow_max_steps = int(
        workflow_config(state.current_workflow).get(
            "max_steps",
            PROJECT_ANALYSIS_MAX_SCAN_STEPS,
        )
    )
    return {
        "completion_action": completion_action,
        "completion_reason": completion_reason,
        "workflow_max_steps": workflow_max_steps,
        "file_investigation_has_enough_context": file_investigation_has_enough_context,
        "in_project_scan_workflow": in_project_scan_workflow,
        "in_file_investigation_workflow": in_file_investigation_workflow,
        "in_failure_recovery_workflow": in_failure_recovery_workflow,
        "in_local_implementation_workflow": in_local_implementation_workflow,
        "in_tavily_research_workflow": in_tavily_research_workflow,
        "in_session_recall_workflow": in_session_recall_workflow,
        "force_project_scan_followup": force_project_scan_followup,
        "force_file_investigation_followup": force_file_investigation_followup,
        "force_failure_recovery_followup": force_failure_recovery_followup,
        "force_local_implementation_followup": force_local_implementation_followup,
        "force_tavily_research_followup": force_tavily_research_followup,
        "force_session_recall_followup": force_session_recall_followup,
    }


def classify_workflow_tool(tool_use: dict[str, Any] | None) -> str | None:
    if not isinstance(tool_use, dict):
        return None
    tool_name = str(tool_use.get("name", "")).strip()
    tool_input = tool_use.get("input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool_name == "SessionSearch":
        return "session_search_tool"
    if tool_name == "Bash":
        command = str(tool_input.get("command", "")).strip()
        if is_tavily_search_command(command):
            return "tavily_search_bash"
        if is_url_fetch_bash_command(command):
            return "http_fetch_bash"
        if is_directory_scan_bash_command(command):
            return "directory_scan_bash"
        return "bash"
    if tool_name == "Read":
        return "read_tool"
    if tool_name in {"Edit", "Update", "MultiEdit", "Write"}:
        return "write_tool"
    if tool_name in {
        "skills_categories",
        "skills_list",
        "skill_view",
        "skill_scan",
        "skill_manage",
    }:
        return "skills_tool"
    return tool_name or None


def workflow_allows_tool(workflow_name: str | None, workflow_step: str | None, tool_use: dict[str, Any] | None, state: Any | None = None) -> bool:
    state_config = workflow_state_config(workflow_name, workflow_step)
    allowed_tool_classes = state_config.get("allowed_tool_classes")
    if not isinstance(allowed_tool_classes, list):
        return True
    if workflow_name == "local_implementation" and state is not None and isinstance(tool_use, dict):
        explicit_constraints = getattr(state, "explicit_runtime_constraints", None)
        tool_name = str(tool_use.get("name", "")).strip()
        if (
            tool_name == "Agent"
            and getattr(explicit_constraints, "allow_subagents", False)
        ):
            return True
    tool_class = classify_workflow_tool(tool_use)
    if workflow_name == "project_analysis" and tool_class == "directory_scan_bash" and state is not None:
        tool_input = tool_use.get("input") if isinstance(tool_use, dict) else {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        command = str(tool_input.get("command", "")).strip()
        if workflow_step == "scan_root":
            if not state.collected_directories:
                root_target = project_analysis_root_scan_target(state)
                root_targets = directory_scan_targets_from_command(command)
                if command == "ls -la":
                    return True
                if root_target != "." and root_targets == [root_target]:
                    return True
                return False
            return is_allowed_subdir_scan_command(command, state.collected_directories)
        if workflow_step == "scan_subdirs":
            return is_allowed_subdir_scan_command(command, state.remaining_directories)
        if workflow_step == "scan_deep":
            return is_allowed_subdir_scan_command(command, state.remaining_deep_branches)
    if workflow_name == "file_investigation" and tool_class == "read_tool" and state is not None:
        tool_input = tool_use.get("input") if isinstance(tool_use, dict) else {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        file_path = str(tool_input.get("file_path", "")).strip()
        if not file_path:
            return False
        primary_targets = {target for target in state.collected_targets if target}
        active_target = str(getattr(state, "resolved_active_path", "") or getattr(state, "active_filename", "") or "").strip()
        if active_target:
            primary_targets.add(active_target)
        if file_path in primary_targets:
            return True
        return file_path in set(state.candidate_related_files)
    if workflow_name == "tavily_research" and state is not None and tool_class in {"tavily_search_bash", "http_fetch_bash"}:
        tool_input = tool_use.get("input") if isinstance(tool_use, dict) else {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        command = str(tool_input.get("command", "")).strip()
        if tool_class == "tavily_search_bash":
            return workflow_step == "search" and is_tavily_search_command(command)
        if tool_class == "http_fetch_bash":
            if workflow_step != "fetch_sources":
                return False
            return is_url_fetch_bash_command(command, state.candidate_related_files)
    return tool_class in allowed_tool_classes


def is_allowed_subdir_scan_command(command: str, allowed_dirs: list[str]) -> bool:
    if not command.startswith("ls -la "):
        return False
    targets = directory_scan_targets_from_command(command)
    if not targets:
        return False
    return all(target in set(allowed_dirs) for target in targets)


def project_analysis_step(scan_count: int, remaining_directories: list[str], remaining_deep_branches: list[str]) -> str | None:
    if scan_count <= 0:
        return None
    if scan_count == 1:
        return "scan_root"
    if remaining_directories:
        return "scan_subdirs"
    if remaining_deep_branches:
        return "scan_deep"
    return "summarize"


def project_analysis_forced_scan_tool_use(state: Any) -> dict[str, Any] | None:
    if state.current_workflow != "project_analysis":
        return None
    if "Bash" not in state.tool_map:
        return None

    targets: list[str] = []
    if state.workflow_step == "scan_root":
        root_target = project_analysis_root_scan_target(state)
        if root_target == ".":
            return {
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": "Bash",
                "input": {"command": "ls -la"},
            }
        return {
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": "Bash",
            "input": {"command": f"ls -la {root_target.rstrip('/')}/"},
        }
    elif state.workflow_step == "scan_subdirs":
        targets = [directory for directory in state.remaining_directories if directory]
    elif state.workflow_step == "scan_deep":
        targets = [directory for directory in state.remaining_deep_branches if directory]

    cleaned_targets: list[str] = []
    seen: set[str] = set()
    for target in targets:
        normalized = target.rstrip("/").strip()
        if not normalized or normalized in {".", ".."} or normalized in seen:
            continue
        seen.add(normalized)
        cleaned_targets.append(f"{normalized}/")

    if not cleaned_targets:
        return None

    return {
        "id": f"toolu_{uuid.uuid4().hex[:24]}",
        "name": "Bash",
        "input": {"command": f"ls -la {' '.join(cleaned_targets)}"},
    }


def file_investigation_forced_read_tool_use(state: Any, initial_read_input_fn) -> dict[str, Any] | None:
    if state.current_workflow != "file_investigation":
        return None
    if state.workflow_step != "read_primary":
        return None
    if "Read" not in state.tool_map:
        return None
    if not state.resolved_active_path or not state.active_file_exists:
        return None
    if any(target for target in state.collected_targets if target):
        return None

    return {
        "id": f"toolu_{uuid.uuid4().hex[:24]}",
        "name": "Read",
        "input": initial_read_input_fn(state.resolved_active_path),
    }


def tavily_research_forced_tool_use(state: Any) -> dict[str, Any] | None:
    if state.current_workflow != "tavily_research" or "Bash" not in state.tool_map:
        return None
    if state.workflow_step == "search":
        command = build_tavily_search_command(state.latest_user_text)
        if not command:
            return None
        return {
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": "Bash",
            "input": {"command": command},
        }
    if state.workflow_step == "fetch_sources":
        fetched = set(state.collected_targets)
        for url in state.candidate_related_files:
            if not url or url in fetched:
                continue
            return {
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": "Bash",
                "input": {"command": "curl -sL -- " + shlex.quote(url)},
            }
    return None


def project_analysis_state(req) -> tuple[str | None, str | None, int, list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], str | None]:
    if workflow_has_completed_assistant_text(req, "project_analysis"):
        return None, None, 0, [], [], [], [], [], [], [], [], [], None
    exchanges = _extract_tool_exchanges(req)
    directory_scan_exchanges: list[ToolExchange] = []
    last_error: str | None = None

    for exchange in exchanges:
        if exchange.is_error:
            last_error = exchange.result_text[:400]
            continue
        if exchange.tool_name != "Bash":
            continue
        command = str(exchange.tool_input_obj.get("command", "")).strip()
        if not is_directory_scan_bash_command(command):
            continue
        directory_scan_exchanges.append(exchange)

    if not directory_scan_exchanges:
        return None, None, 0, [], [], [], [], [], [], [], [], [], last_error

    collected_targets = [target for exchange in directory_scan_exchanges for target in directory_scan_targets_from_command(str(exchange.tool_input_obj.get("command", "")).strip())]
    collected_directories = extract_ls_directories(directory_scan_exchanges[0].result_text)
    covered_directories = project_analysis_covered_dirs(collected_targets, collected_directories)
    remaining_directories = [directory for directory in project_analysis_trackable_dirs(collected_directories) if directory not in covered_directories]
    candidate_deep_branches = project_analysis_deep_branch_candidates(covered_directories)
    covered_deep_branches = project_analysis_covered_deep_branches(collected_targets, candidate_deep_branches)
    remaining_deep_branches = [branch for branch in candidate_deep_branches if branch not in covered_deep_branches]
    collected_summaries = [summarize_directory_scan_result(str(exchange.tool_input_obj.get("command", "")).strip(), exchange.result_text) for exchange in directory_scan_exchanges]
    return (
        "project_analysis",
        project_analysis_step(len(directory_scan_exchanges), remaining_directories, remaining_deep_branches),
        len(directory_scan_exchanges),
        collected_targets,
        collected_directories,
        covered_directories,
        remaining_directories,
        candidate_deep_branches,
        covered_deep_branches,
        remaining_deep_branches,
        [],
        collected_summaries,
        last_error,
    )


def local_implementation_state(req) -> tuple[str | None, str | None, int, list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], list[str], str | None]:
    if workflow_has_completed_assistant_text(req, "local_implementation"):
        return None, None, 0, [], [], [], [], [], [], [], [], [], None

    exchanges = _extract_tool_exchanges(req)
    implementation_exchanges: list[ToolExchange] = []
    last_error: str | None = None

    for exchange in exchanges:
        if exchange.tool_name in {"Read", "Write", "Edit", "Update", "MultiEdit"}:
            implementation_exchanges.append(exchange)
            if exchange.is_error:
                cleaned_error = exchange.result_text.strip()
                if cleaned_error:
                    last_error = cleaned_error[:800]
            continue
        if exchange.tool_name != "Bash":
            continue
        command = str(exchange.tool_input_obj.get("command", "")).strip()
        if not command:
            continue
        if is_directory_scan_bash_command(command) or is_tavily_search_command(command) or is_url_fetch_bash_command(command):
            continue
        implementation_exchanges.append(exchange)
        if exchange.is_error:
            cleaned_error = exchange.result_text.strip()
            if cleaned_error:
                last_error = cleaned_error[:800]

    if not implementation_exchanges:
        return None, None, 0, [], [], [], [], [], [], [], [], [], None

    def _is_recoverable_local_implementation_error(tool_name: str, result_text: str) -> bool:
        text = str(result_text or "")
        if not text:
            return False
        if "File has not been read yet" in text:
            return True
        if "String to replace not found" in text:
            return True
        if "<tool_use_error>" not in text:
            # Bash "command not found" (exit code 127) is recoverable
            if tool_name == "Bash" and ("command not found" in text.lower() or "команда не найдена" in text.lower()):
                return True
            return False
        if "InputValidationError" in text:
            return True
        if "missing required field:" in text:
            return True
        if "unexpected fields:" in text:
            return True
        if tool_name == "Write" and "required parameter `content` is missing" in text:
            return True
        return False

    workflow_step = "continue"
    latest_exchange = implementation_exchanges[-1]
    if latest_exchange.is_error and not _is_recoverable_local_implementation_error(
        latest_exchange.tool_name,
        latest_exchange.result_text,
    ):
        workflow_step = "blocked"

    collected_targets = [exchange.target for exchange in implementation_exchanges if exchange.target]
    return (
        "local_implementation",
        workflow_step,
        len(implementation_exchanges),
        collected_targets,
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        last_error,
    )
