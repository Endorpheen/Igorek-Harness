import re
from pathlib import Path
import shlex
from typing import Any, Callable

from factory.api_server_core.policy_engine import POLICY_CONFIG_PATH, policy_bool, policy_list

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTECTED_POLICY_PATHS = {
    POLICY_CONFIG_PATH.resolve(),
    (REPO_ROOT / "docs" / "architecture" / "POLICY.md").resolve(),
    (REPO_ROOT / "factory" / "api_server_core" / "policy_engine.py").resolve(),
    (REPO_ROOT / "factory" / "api_server_core" / "file_policy.py").resolve(),
}
MAX_LOCAL_PATH_CANDIDATE_LENGTH = 240


def extract_project_root(req: Any) -> str | None:
    # Prefer the session working directory provided by the client/runtime.
    # AGENTS.md/CLAUDE.md contents from this server repo may appear in prompts,
    # but that repo is not the user's active project root.
    system_content = getattr(req, "system", None)
    if isinstance(system_content, str):
        system_text = system_content
    elif isinstance(system_content, list):
        system_text = "\n".join(
            block.get("text", "") if isinstance(block, dict) else ""
            for block in system_content
        )
    else:
        system_text = ""
    if system_text:
        cwd_match = re.search(
            r"Primary working directory:\s*(?P<path>/[^\s\n]+)",
            system_text,
        )
        if cwd_match:
            return cwd_match.group("path").strip()

    patterns = (
        r"Contents of (?P<path>/[^\n]*?/CLAUDE\.md)",
        r"Contents of (?P<path>/[^\n]*?/AGENTS\.md)",
    )
    for msg in req.messages:
        content = msg.content
        if isinstance(content, str):
            blocks = [content]
        else:
            blocks = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
        for text in blocks:
            if not text:
                continue
            for pattern in patterns:
                match = re.search(pattern, text)
                if not match:
                    continue
                path = match.group("path").strip()
                parent = Path(path).parent
                if parent.name.lower() in {"claude", ".claude"}:
                    parent = parent.parent
                try:
                    resolved_parent = parent.resolve()
                    resolved_repo_root = REPO_ROOT.resolve()
                    resolved_parent.relative_to(resolved_repo_root)
                    continue
                except Exception:
                    pass
                return str(parent)

    return None


def extract_explicit_absolute_path(text: str) -> str | None:
    if not text:
        return None
    patterns = (
        r"`(?P<path>/[^\n`]+)`",
        r"(?<![A-Za-z0-9._-])(?P<path>/[^\s'\"<>`]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            start = match.start("path")
            value = match.group("path").strip()
            if not value or not value.startswith("/"):
                continue
            prefix = text[max(0, start - 24):start]
            if value.startswith("//") and re.search(r"[A-Za-z][A-Za-z0-9+.-]*:$", prefix):
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                continue
            # Reject paths that look like slashed natural-language text
            # (for example, direction names separated by slashes).
            parts = [p for p in value.split("/") if p]
            if parts and not all(re.fullmatch(r"[A-Za-z0-9._-]+", p) for p in parts):
                continue
            return str(candidate)
    return None


def resolution_base_dir(
    last_successful_read_file: str | None,
    last_tool_file: str | None,
    project_root: str | None,
    current_shell_cwd: str | None = None,
) -> Path | None:
    candidates = (
        last_successful_read_file,
        last_tool_file,
        current_shell_cwd,
        project_root,
    )
    for candidate in candidates:
        if not candidate:
            continue
        p = Path(candidate).expanduser()
        if not p.is_absolute():
            continue
        return p.parent if p.is_file() or p.suffix else p
    return None


def sanitize_local_path_candidate(path: str | None) -> str | None:
    if not isinstance(path, str):
        return None
    candidate = path.strip().strip("'\"")
    if candidate.startswith(">"):
        candidate = candidate[1:].strip()
    if not candidate:
        return None
    if any(ch in candidate for ch in ("\n", "\r", "\t", "\x00")):
        return None
    if len(candidate) > MAX_LOCAL_PATH_CANDIDATE_LENGTH:
        return None
    if any(ch in candidate for ch in ("|", "&", ";", "$", "`", "{", "}", "(", ")")):
        return None
    if ":" in candidate and not re.match(r"^[A-Za-z]:[\\/]", candidate):
        return None
    return candidate


def resolve_local_path_if_valid(path: str | None, *, base_dir: Path | None = None) -> Path | None:
    candidate = sanitize_local_path_candidate(path)
    if not candidate:
        return None
    try:
        return resolve_local_path(candidate, base_dir=base_dir)
    except ValueError:
        return None


def resolve_local_path(path: str, *, base_dir: Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    if base_dir is None:
        raise ValueError("relative path requires a known base_dir")
    root = base_dir
    return (root / candidate).resolve()


def is_within_project_root(path: Path, project_root: str | None) -> bool:
    if not project_root:
        return True
    root = Path(project_root).expanduser().resolve()
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def temporary_allowed_root_for_path(path: str | None, project_root: str | None) -> str | None:
    if not policy_bool("file_tools", "allow_absolute_path_override", default=True):
        return None
    if not path:
        return None
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return None
    resolved = candidate.resolve()
    if is_within_project_root(resolved, project_root):
        return None
    return str(resolved.parent if resolved.suffix else resolved)


def is_within_allowed_roots(path: Path, project_root: str | None, temporary_allowed_root: str | None) -> bool:
    if is_within_project_root(path, project_root):
        return True
    if temporary_allowed_root:
        root = Path(temporary_allowed_root).expanduser().resolve()
        try:
            path.resolve().relative_to(root)
            return True
        except ValueError:
            pass
    return False


def _is_within_any_root(
    path: Path,
    project_root: str | None,
    temporary_allowed_root: str | None,
) -> bool:
    if is_within_allowed_roots(path, project_root, temporary_allowed_root):
        return True
    for root_path in policy_list("file_tools", "additional_allowed_roots", default=[]):
        if not isinstance(root_path, str) or not root_path.strip():
            continue
        try:
            path.resolve().relative_to(Path(root_path.strip()).expanduser().resolve())
            return True
        except ValueError:
            pass
    return False




def is_protected_policy_path(path: Path) -> bool:
    try:
        return path.resolve() in PROTECTED_POLICY_PATHS
    except FileNotFoundError:
        return path.expanduser().resolve() in PROTECTED_POLICY_PATHS


def _bash_first_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.strip().split()


def _bash_has_compound_operators(command: str) -> bool:
    return any(token in command for token in ("|", "&&", "||", ";", "$(", "`", ">", "<"))


def _normalize_bash_command(command: str) -> str:
    return " ".join(command.strip().split())


def _bash_allowed_by_allowlist(command: str) -> bool:
    allowed = policy_list("bash", "allowed_commands", default=[])
    if not allowed:
        return True

    normalized = _normalize_bash_command(command)
    for rule in allowed:
        candidate = _normalize_bash_command(rule)
        if not candidate:
            continue
        if normalized == candidate or normalized.startswith(candidate + " "):
            return True
    return False


def _bash_mutation_reason(command: str) -> str | None:
    tokens = _bash_first_tokens(command)
    if not tokens:
        return "bash_empty_command"

    first = tokens[0]
    if first in {"open", "xdg-open", "gio", "gnome-open", "kde-open", "sensible-browser"}:
        return f"bash_gui_open_not_allowed:{first}"
    mutating = set(policy_list("bash", "mutation_commands", default=[]))
    if first in mutating:
        return f"bash_file_mutation_not_allowed:{first}"

    disallow_inline_edit_flags = policy_bool("bash", "disallow_inline_edit_flags", default=True)
    if disallow_inline_edit_flags and first == "sed" and any(part.startswith("-i") for part in tokens[1:]):
        return "bash_file_mutation_not_allowed:sed -i"
    if disallow_inline_edit_flags and first == "perl" and any(part.startswith("-pi") or part.startswith("-i") for part in tokens[1:]):
        return "bash_file_mutation_not_allowed:perl -pi"
    if first in set(policy_list("bash", "nested_shell_commands", default=[])):
        return f"bash_compound_command_not_allowed:{first}"
    return None


def _bash_protected_policy_target(command: str, state: Any) -> str | None:
    tokens = _bash_first_tokens(command)
    if len(tokens) < 2:
        return None
    base_dir = resolution_base_dir(
        state.last_successful_read_file,
        state.last_tool_file,
        state.project_root,
        getattr(state, "current_shell_cwd", None),
    )
    candidates = list(tokens[1:])
    for index, token in enumerate(tokens[:-1]):
        if token in {'-c', '-S', '--split-string'}:
            candidates.extend(_bash_first_tokens(tokens[index + 1]))
        if token in {'-C', '--chdir'}:
            directory = resolve_local_path_if_valid(tokens[index + 1], base_dir=base_dir)
            if directory is not None:
                base_dir = directory
        elif token.startswith('--chdir='):
            directory = resolve_local_path_if_valid(token.split('=', 1)[1], base_dir=base_dir)
            if directory is not None:
                base_dir = directory
    for token in candidates:
        if '=' in token:
            token = token.split('=', 1)[1]
        if not token or token.startswith("-"):
            continue
        if "/" not in token and not token.endswith((".yaml", ".yml", ".md", ".py")):
            continue
        resolved = resolve_local_path_if_valid(token, base_dir=base_dir)
        if resolved is None:
            continue
        if is_protected_policy_path(resolved):
            return str(resolved)
    return None


def _bash_reads_policy_only(command: str) -> bool:
    """Positively recognize simple reads; unknown wrappers cannot authorize writes."""
    tokens = _bash_first_tokens(command)
    while tokens:
        executable = tokens[0]
        name = Path(executable).name
        if '/' in executable and executable not in {'/bin/' + name, '/usr/bin/' + name}:
            return False
        if name == 'env':
            tokens = tokens[1:]
            while tokens:
                if tokens[0] in {'-i', '--ignore-environment', '--'}:
                    tokens = tokens[1:]
                elif tokens[0] in {'-u', '--unset', '-C', '--chdir'} and len(tokens) > 1:
                    tokens = tokens[2:]
                elif tokens[0].startswith(('--unset=', '--chdir=')):
                    tokens = tokens[1:]
                elif '=' in tokens[0]:
                    key = tokens[0].split('=', 1)[0]
                    if key != 'LANG' and not key.startswith('LC_'):
                        return False
                    tokens = tokens[1:]
                else:
                    break
            continue
        if name in {'command', 'nohup'}:
            tokens = tokens[1:]
            if tokens and tokens[0] == '--':
                tokens = tokens[1:]
            continue
        return name in {'cat', 'head', 'tail', 'stat', 'wc', 'sha256sum', 'sha512sum', 'readlink', 'realpath'}
    return False


def validate_existing_file_policy(
    state: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    tool_target_from_input: Callable[[str, dict[str, Any]], str],
) -> tuple[bool, str]:
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            if policy_bool("bash", "allow_empty_command", default=False):
                return True, ""
            state.policy_denial = "bash_empty_command"
            return False, state.policy_denial
        tokens = _bash_first_tokens(command)
        first = tokens[0] if tokens else ""
        if (
            getattr(state, "current_workflow", None) == "local_implementation"
            and first in {"echo", "printf"}
        ):
            state.policy_denial = f"bash_display_command_not_allowed:{first}"
            return False, state.policy_denial
        if (
            getattr(state, "current_workflow", None) == "local_implementation"
            and first == "cat"
            and any(marker in command for marker in ("<!DOCTYPE", "<html", "<script", "<style"))
        ):
            state.policy_denial = "bash_source_dump_not_allowed"
            return False, state.policy_denial
        mutation_reason = _bash_mutation_reason(command)
        protected_target = _bash_protected_policy_target(command, state)
        if protected_target and (
            _bash_has_compound_operators(command) or not _bash_reads_policy_only(command)
        ):
            state.policy_denial = f"protected_policy_file:{protected_target}"
            return False, state.policy_denial
        if not policy_bool("bash", "allow_compound_commands", default=False) and _bash_has_compound_operators(command):
            state.policy_denial = "bash_compound_command_not_allowed"
            return False, state.policy_denial
        if not policy_bool("bash", "allow_file_mutation", default=False) and mutation_reason:
            state.policy_denial = mutation_reason
            return False, state.policy_denial
        if not _bash_allowed_by_allowlist(command):
            state.policy_denial = "bash_command_not_allowlisted"
            return False, state.policy_denial

        target = tool_target_from_input(tool_name, tool_input)
        if target:
            resolved = resolve_local_path_if_valid(
                target,
                base_dir=resolution_base_dir(
                    state.last_successful_read_file,
                    state.last_tool_file,
                    state.project_root,
                    getattr(state, "current_shell_cwd", None),
                ),
            )
            if resolved is None:
                state.policy_denial = f"invalid_file_target:{target}"
                return False, state.policy_denial
            if policy_bool("file_tools", "enforce_project_root", default=True) and not _is_within_any_root(
                resolved,
                state.project_root,
                getattr(state, "temporary_allowed_root", None),
            ):
                state.policy_denial = f"path_outside_project_root:{target}"
                return False, state.policy_denial
        return True, ""

    managed_tools = set(policy_list("file_tools", "managed_tools", default=["Read", "Edit", "Update", "MultiEdit", "Write"]))
    if tool_name not in managed_tools:
        return True, ""

    target = tool_target_from_input(tool_name, tool_input)
    if not target:
        target = state.active_filename or ""
        if target:
            properties = state.tool_map.get(tool_name, {}).get("input_schema", {}).get("properties", {})
            for key in ("file_path", "path", "notebook_path"):
                if key in tool_input or key not in properties:
                    continue
                tool_input[key] = target
                break

    # Resolve short/wrong-root filenames to explicit_user_file when it already
    # has the correct absolute path (e.g. self-inspection files resolved via
    # policy allowed roots).  The pre-resolution step in _validate_tool_policy
    # may already have resolved "handoff.md" against the temp workspace, so we
    # cannot rely on is_absolute() — instead compare the resolved targets.
    if (
        target
        and state.explicit_user_file
        and Path(state.explicit_user_file).is_absolute()
        and Path(state.explicit_user_file).exists()
        and Path(target).name == Path(state.explicit_user_file).name
        and str(Path(target).resolve()) != str(Path(state.explicit_user_file).resolve())
    ):
        target = state.explicit_user_file
        for key in ("file_path", "path", "notebook_path"):
            if key in tool_input:
                tool_input[key] = target
                break

    if not target:
        state.missing_file_target = True
        state.policy_denial = "missing_file_target"
        return False, "missing_file_target"

    resolved = resolve_local_path_if_valid(
        target,
        base_dir=resolution_base_dir(
            state.last_successful_read_file,
            state.last_tool_file,
            state.project_root,
            getattr(state, "current_shell_cwd", None),
        ),
    )
    if resolved is None:
        state.missing_file_target = True
        state.policy_denial = f"invalid_file_target:{target}"
        return False, state.policy_denial
    if tool_name != "Read" and is_protected_policy_path(resolved):
        state.policy_denial = f"protected_policy_file:{resolved}"
        return False, state.policy_denial
    if policy_bool("file_tools", "enforce_project_root", default=True) and not _is_within_any_root(
        resolved,
        state.project_root,
        getattr(state, "temporary_allowed_root", None),
    ):
        state.policy_denial = f"path_outside_project_root:{target}"
        return False, state.policy_denial

    if tool_name == "Write" and policy_bool("file_tools", "allow_write_new_files", default=True):
        return True, ""

    if resolved.exists() and resolved.is_file():
        return True, ""

    if state.explicit_user_file and Path(state.explicit_user_file).name == Path(target).name:
        state.missing_file_target = True
        state.policy_denial = f"explicit_file_not_found:{target}"
        return False, state.policy_denial

    state.missing_file_target = True
    state.policy_denial = f"target_file_not_found:{target}"
    return False, state.policy_denial


def build_policy_denial_response(reason: str | None) -> str:
    value = reason or ""
    if value == "user_forbids_subagents":
        return "Пользователь явно запретил `subagents` в текущем ходе. Продолжай без `Agent`."
    if value.startswith("user_forbids_background:"):
        tool_name = value.split(":", 1)[1]
        return f"Пользователь явно запретил фоновые задачи в текущем ходе. Не используй `{tool_name}` и продолжай без background-path."
    if value.startswith("local_implementation_forbids_tool:"):
        tool_name = value.split(":", 1)[1]
        return f"Внутри local_implementation workflow инструмент `{tool_name}` запрещён. Продолжай только через локальные file/bash tools."
    if value.startswith("unsupported_tool_in_local_setup:"):
        tool_name = value.split(":", 1)[1]
        return f"Инструмент `{tool_name}` отключён в этом локальном сетапе. Используй `Bash`, Tavily через `curl` или прямые HTTP-запросы."
    if value.startswith("protected_policy_file:"):
        target = value.split(":", 1)[1]
        return f"Файл policy/control-plane `{target}` защищён. Менять его может только Администратор вручную вне агентного workflow."
    if value == "bash_empty_command":
        return "Не удалось безопасно определить bash-команду. Укажи одну явную команду без shell-цепочек."
    if value == "bash_compound_command_not_allowed":
        return "Составные bash-команды и редиректы сейчас запрещены policy-слоем. Используй одну простую команду или отдельные file tools."
    if value.startswith("bash_compound_command_not_allowed:"):
        command = value.split(":", 1)[1]
        return f"Запуск вложенной shell-команды `{command}` сейчас запрещён policy-слоем. Используй одну простую команду без оболочки поверх оболочки."
    if value.startswith("bash_file_mutation_not_allowed:"):
        command = value.split(":", 1)[1]
        return f"Bash-команда `{command}` может изменять файлы или директории. Для таких действий используй `Write`/`Edit` или более явный workflow."
    if value.startswith("bash_gui_open_not_allowed:"):
        command = value.split(":", 1)[1]
        return f"Bash-команда `{command}` пытается открыть GUI/браузер на headless-сервере. Не используй open-команды; проверяй файлы через `Read`, `Bash ls`, `Grep` или локальные file tools."
    if value.startswith("bash_display_command_not_allowed:"):
        command = value.split(":", 1)[1]
        return f"Bash-команда `{command}` не должна использоваться для вывода текста или исходников внутри local_implementation. Для сообщений отвечай текстом, для содержимого файлов используй `Write`/`Edit`."
    if value == "bash_source_dump_not_allowed":
        return "Нельзя вываливать HTML/CSS/JS исходник через Bash. Для создания файла используй `Write`, для правок — `Edit`/`Update`/`MultiEdit`."
    if value == "bash_command_not_allowlisted":
        return "Эта Bash-команда не входит в allowlist из `factory/config/inference-policy.yaml`. Либо используй разрешённую команду, либо обнови policy вручную через Администратор."
    if value.startswith("path_outside_project_root:"):
        target = value.split(":", 1)[1]
        return f"Путь `{target}` находится вне рабочей директории проекта. Укажи файл внутри текущего проекта."
    if value.startswith("explicit_file_not_found:"):
        target = value.split(":", 1)[1]
        return f"Я не нашёл файл `{target}`. Уточни корректный существующий путь к файлу."
    if value.startswith("target_file_not_found:"):
        target = value.split(":", 1)[1]
        return f"Файл `{target}` не найден. Укажи существующий файл, с которым нужно работать."
    if value.startswith("invalid_file_target:"):
        target = value.split(":", 1)[1]
        return f"Строка `{target}` не похожа на корректный путь к файлу. Укажи нормальный однострочный путь внутри проекта."
    if value == "missing_file_target":
        return "Не удалось однозначно определить файл. Укажи точный существующий путь или имя файла."
    return "Не удалось безопасно определить файл для действия. Укажи точный существующий файл."


def file_resolution_source(
    explicit_user_file: str | None,
    last_successful_read_file: str | None,
    last_tool_file: str | None,
) -> str:
    if explicit_user_file:
        return "explicit_user_file"
    if last_successful_read_file:
        return "last_successful_read_file"
    if last_tool_file:
        return "last_tool_file"
    return "none"
