"""Mobile bridge operations over the existing Igorek runtime.

The bridge does not launch its own executor: it places messages into the same queue
`tasks`, which is processed by the local worker, continues sessions in the same files
`chat_sessions/*.jsonl` and uses the same storage for `approvals` and `user-input`
as TUI and Telegram.
"""

from __future__ import annotations

import hashlib
import json
import re
import logging
import os
import sqlite3
import time
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import security_levels, security_settings
from ..agent_foundation import extract_memory_write_blocks, strip_memory_status_lines
from ..approval_resume import _forced_tool_call_for_resume
from ..cli_chat import (
    _append_entry,
    _build_prompt,
    _history_tail,
    _load_entries,
    _read_session_meta,
    _session_path,
    _session_write_lock,
    _write_session_meta,
    generate_session_name,
    list_sessions,
    update_session_meta,
)
from ..cli_chat_approval import _blocked_tool_session_rule
from ..checkpoint_manager import CheckpointManager
from ..artifact_files import open_regular_file
from ..cli_tasks import cancel_task as cancel_task_row
from ..cli_tasks import create_task, get_task, resume_waiting_approval_task
from ..durable_root_scope import durable_root_within_workdir, is_within_relative
from ..isolation.artifact_map import ARTIFACT_DIRNAME, read_artifact_route
from ..isolation.host_op_approval import (
    HOST_OP_APPROVAL_KIND,
    REASON_ALREADY_RESOLVED,
    HostOpApprovalClass,
    HostOpApprovalError,
    is_host_op_blocked_record,
    parse_host_op_blocked_record,
)
from ..isolation.errors import IsolationError
from ..isolation.host_op_gate import HostOpApprovalGate
from ..isolation.persistence import durable_data_root
from ..isolation.status import (
    ISOLATION_KVM,
    isolation_status_for_backend,
    resolve_isolation_status,
)
from ..isolation.workspace import resolve_workspace_cwd
from ..memory_manager import MemoryManager
from ..thread_goals import get_goal
from .. import subagents as subagent_journal
from ..bash_sandbox_settings import sandbox_status, set_sandbox_enabled
from ..mobile_approval_settings import (
    mobile_approvals_enabled,
    set_mobile_approvals_enabled,
)
from ..mcp_config import list_mcp_capabilities_for_profile
from ..opencode_quota import OPENCODE_MOBILE_USER_AGENT, usage_payload
from ..external_sessions import ExternalSessionCatalog
from ..session_history import read_history, build_working_history
from ..runtime import (
    get_data_root,
    get_db_path,
    get_profile_config,
    get_profile_path,
    get_workspace_root,
    list_profile_names,
    profile_backend_name,
)
from ..schema import open_db
from ..security_store import SecurityStore
from ..session_changes import (
    aggregate_changes,
    count_line_changes,
    load_task_changes,
    normalize_workdir_path,
)
from ..session_diff import MAX_DIFF_FILE_BYTES, build_diff_hunks
from ..sudo_broker import pending_requests
from ..skills_manager import skills_manager as runtime_skills_manager
from ..steer_queue import (
    cancel_steer,
    drain_steer,
    enqueue_steer,
    pending_steer_count,
    pending_steer_sessions,
    prioritize_steer,
)
from ..user_input_store import (
    get_user_input_request,
    list_pending_user_input_requests,
    resolve_user_input_request,
)
from .store import MobileStore
from .files import collect_files, describe_file, open_file_safely
from .uploads import (
    format_attachments_block,
    public_attachments,
    resolve_session_uploads,
    save_session_upload,
    session_upload_dir,
    MAX_UPLOAD_BYTES,
)

ACTIVE_STATUSES = frozenset({"queued", "processing", "waiting_approval", "waiting_user_input", "cancelling", "retry"})
FINAL_STATUSES = frozenset({"ok", "fail", "dead_letter", "cancelled", "timeout"})

logger = logging.getLogger(__name__)

# Maintained in sync with `cc_backend_tools.DANGEROUS_TOOLS` (risk: `test_mobile_bridge_service`).
HIGH_RISK_TOOLS = frozenset({"write", "edit", "bash"})

# External channels (Telegram) deliver their own approvals in chat. Their tasks and
# their sessions must not surface in the mobile Approval Center, otherwise the same
# decision is duplicated in Telegram and in the app.
_FOREIGN_APPROVAL_FILTER_SQL = (
    "  AND NOT (json_valid(metadata) "
    "    AND COALESCE(json_extract(metadata, '$.platform'), '') = 'telegram') "
    "  AND NOT (json_valid(metadata) "
    "    AND COALESCE(json_extract(metadata, '$.session'), '') LIKE '%-chat-%') "
)

DEFAULT_HISTORY_MESSAGES = 20
DEFAULT_HISTORY_CHARS = None
MAX_HISTORY_IMAGES = 5
SUPPORTED_HISTORY_IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp"}
)
TASK_SCAN_LIMIT = 400


class MobileNotFound(Exception):
    pass


class MobileConflict(Exception):
    pass


class MobileInvalid(Exception):
    pass


def _loads(raw: Any, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _drop_artifact_paths(
    aggregated: dict[str, Any], artifact_root: str | None
) -> dict[str, Any]:
    """Remove kvm guest-artifact paths from an aggregated changes payload."""
    if not artifact_root:
        return aggregated
    files = [
        item
        for item in aggregated.get("files", [])
        if not is_within_relative(str(item.get("path") or ""), artifact_root)
    ]
    if len(files) == len(aggregated.get("files", [])):
        return aggregated
    return {
        "files": files,
        "totals": {
            "files": len(files),
            "additions": sum(int(item.get("additions") or 0) for item in files),
            "deletions": sum(int(item.get("deletions") or 0) for item in files),
        },
    }


def _historical_session_images(
    history: list[dict[str, Any]], *, session_id: str, workdir: str
) -> list[str]:
    """Return recent image attachments so resumed turns retain visual context.

    Mobile history stores attachment names in the journal and the actual paths
    on their originating task rows. Replaying only the journal text otherwise
    leaves the model with a filename it can OCR through Bash, but no image part
    in its multimodal request.
    """
    task_ids = list(
        dict.fromkeys(
            str(entry.get("task_id") or "").strip()
            for entry in history
            if str(entry.get("task_id") or "").strip()
        )
    )
    if not task_ids:
        return []
    try:
        root = Path(workdir).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    placeholders = ",".join("?" for _ in task_ids)
    with open_db(get_db_path()) as conn:
        rows = conn.execute(
            "SELECT task_id, input_files FROM tasks "
            f"WHERE task_id IN ({placeholders}) AND json_valid(metadata) "
            "AND COALESCE(json_extract(metadata, '$.session'), '') = ?",
            (*task_ids, session_id),
        ).fetchall()
    files_by_task = {str(task_id): raw_files for task_id, raw_files in rows}
    images: list[str] = []
    seen: set[str] = set()
    for task_id in task_ids:
        raw_files = files_by_task.get(task_id)
        files = _loads(raw_files, [])
        if not isinstance(files, list):
            continue
        for raw_path in files:
            try:
                path = Path(str(raw_path)).expanduser().resolve(strict=True)
                path.relative_to(root)
            except (OSError, RuntimeError, ValueError):
                continue
            if (
                not path.is_file()
                or path.suffix.lower() not in SUPPORTED_HISTORY_IMAGE_EXTENSIONS
            ):
                continue
            normalized = str(path)
            if normalized not in seen:
                seen.add(normalized)
                images.append(normalized)
    return images[-MAX_HISTORY_IMAGES:]


def _entry_skills(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("skills")
    return [str(item) for item in raw] if isinstance(raw, list) else []


def _reassign_journal_task_id(
    path: Path, message_ids: set[str], task_id: str
) -> None:
    """Reassign session log entries to a different task.

    Orphan scenario: the task string was deleted, but its messages remained in the log with
    the `queued` status. Without reassignment, the client forever displays them as "in queue",
    since `finalize_turns`/`resume_pending_steers` cannot find the original task.

    The file is rewritten in full (JSONL), so lines that `_load_entries` does not return
    (e.g., the `meta` header) are saved as-is.
    """
    with _session_write_lock(path):
        if not message_ids or not path.exists():
            return
        rewritten: list[str] = []
        changed = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                rewritten.append(line)
                continue
            if isinstance(entry, dict) and str(entry.get("message_id") or "") in message_ids:
                entry["task_id"] = task_id
                changed = True
            rewritten.append(json.dumps(entry, ensure_ascii=False))
        if not changed:
            return
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        os.replace(tmp, path)


class MobileService:
    def __init__(
        self,
        *,
        profile: str = "",
        workdir: str = "",
        store: MobileStore | None = None,
        data_dir: str | None = None,
        external_sessions: ExternalSessionCatalog | None = None,
        isolation_status: Callable[[], str] | None = None,
        host_op_gate: HostOpApprovalGate | None = None,
        available_profiles: Callable[[], list[str]] | None = None,
    ) -> None:
        self.store = store or MobileStore()
        from .sources import SourceIndex
        self._source_index = SourceIndex(self.store)
        self.profile = str(profile or os.environ.get("FACTORY_MOBILE_PROFILE", "")).strip()
        self.workdir = str(Path(workdir).expanduser()) if workdir else str(Path.cwd())
        self.data_dir = data_dir or str(get_data_root())
        self._memory_manager = MemoryManager()
        self.external_sessions_catalog = external_sessions or ExternalSessionCatalog()
        self._isolation_status_provider = isolation_status or resolve_isolation_status
        self._isolation_status_cache: str | None = None
        self._cache_isolation_status = True
        self.available_profiles = available_profiles
        self.profile_control = None
        self._host_op_gate = host_op_gate
        self._foundation: str | None = None
        self._approval_lock = threading.RLock()
        self._session_lock = threading.RLock()
        self._extensions_lock = threading.RLock()
        self._event_hooks: list[Callable[[dict[str, Any]], None]] = []

    def add_event_hook(self, hook: Callable[[dict[str, Any]], None]) -> None:
        """Register a live-event sink (mobile bridge broadcaster).

        Journal writes alone reach the client only after the next poll tick
        (up to ``poll_interval_s`` later), so the mobile bridge additionally
        mirrors them on the live channel. This matters most for session-message
        events, where the UI must react immediately.
        """
        self._event_hooks.append(hook)

    def _publish_event(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        session_id: str,
        task_id: str,
        seq: int | None = None,
    ) -> None:
        """Mirror a journaled event onto the live channel (no-op without hooks).

        The durable journal record stays the single source of truth: the live
        frame carries ``seq`` when known, and the SSE reader skips frames whose
        ``seq`` is already past its cursor, so a client can never see a message
        event twice.
        """
        if not self._event_hooks:
            return
        event: dict[str, Any] = {
            "kind": str(kind),
            "ts": int(time.time()),
            "session_id": session_id or None,
            "task_id": task_id or None,
            "payload": payload,
        }
        if seq:
            event["seq"] = int(seq)
        for hook in list(self._event_hooks):
            try:
                hook(event)
            except Exception:
                continue

    def _foundation_context(self) -> str:
        if self._foundation is None:
            try:
                self._foundation = self._memory_manager.foundation_context(data_dir=self.data_dir).strip()
            except Exception:
                self._foundation = ""
        return self._foundation

    def _load_runtime_skills(self):
        with self._extensions_lock:
            runtime_skills_manager.load_settings()
            runtime_skills_manager.load_skills()
            return runtime_skills_manager

    def _profile_backend(self, profile: str) -> str:
        """Backend name of a profile; ``""`` when the profile cannot be resolved.

        The service's own ``data_dir`` profiles win; the package fallback keeps
        bundled profiles (which have no runtime copy) resolvable, so a truthful
        isolation status does not depend on where the profile file happens to
        live.
        """
        if not profile:
            return ""
        for profiles_dir in (Path(self.data_dir).expanduser() / "profiles", None):
            try:
                config = get_profile_config(profile, profiles_dir=profiles_dir)
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                continue
            return profile_backend_name(config)
        return ""

    def configure_live_isolation_status(self, provider: Callable[[], str]) -> None:
        """Use an owner-managed in-memory status without caching desktop mode changes."""
        self._isolation_status_provider = provider
        self._isolation_status_cache = None
        self._cache_isolation_status = False

    def _isolation_status_value(self, profile: str = "") -> str:
        """Additive ``isolation`` value for one profile's payloads (todo 12/35).

        By default the config-level value is resolved once per service instance:
        refreshes must not re-run the host preflight. Owner-managed desktop
        mode uses an uncached in-memory callback instead. Session creation stays
        host-side and instant. It is then narrowed to the profile's actual
        backend: only ``cc-backend`` runs in a VM in this plan, so ``claude``/
        ``codex`` (and unresolved profiles) report ``"none"``.
        """
        if not self._cache_isolation_status:
            value = str(self._isolation_status_provider())
        else:
            if self._isolation_status_cache is None:
                self._isolation_status_cache = str(self._isolation_status_provider())
            value = self._isolation_status_cache
        return isolation_status_for_backend(
            value,
            self._profile_backend(profile),
        )

    @staticmethod
    def _skill_payload(skill: Any, *, backend: str = "") -> dict[str, Any]:
        return {
            "skill_id": str(skill.skill_id),
            "name": str(skill.name),
            "description": str(skill.description or ""),
            "category": str(skill.category or ""),
            "enabled": bool(skill.enabled),
            "supported": not backend or bool(skill.supports_backend(backend)),
            "backend_filter": [str(item) for item in skill.backend_filter],
            "resource_dirs": [str(item) for item in skill.resource_dirs()],
        }

    def opencode_usage(self, *, force: bool = False) -> dict[str, Any]:
        """OpenCode Go subscription quota for the mobile client.

        Implementation shared with the web panel (``factory/core/opencode_quota.py``):
        One key resolution via Igorek Vault and one 60-second response cache, so
        the phone and browser display the same numbers. Errors arrive as typed
        (``OpenCodeUsageError``) — the route passes its code to the client,
        while the key remains on the host.
        """
        return usage_payload(force=force, user_agent=OPENCODE_MOBILE_USER_AGENT)

    def extensions(self, *, profile: str | None = None) -> dict[str, Any]:
        effective_profile = str(profile or self.profile).strip()
        backend = self._profile_backend(effective_profile)
        manager = self._load_runtime_skills()
        skills = [
            self._skill_payload(skill, backend=backend)
            for skill in manager.all_skills.values()
        ]
        skills.sort(key=lambda item: ((item["category"] or ""), item["name"].lower()))
        try:
            mcp_servers = list_mcp_capabilities_for_profile(
                effective_profile,
                data_dir=self.data_dir,
            ) if effective_profile else []
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            mcp_servers = []
        return {
            "profile": effective_profile,
            "backend": backend,
            "skills": skills,
            "mcp_servers": mcp_servers,
            "hooks": [],
            "hooks_available": False,
        }

    def set_skill_enabled(self, skill_id: str, *, enabled: bool) -> dict[str, Any]:
        identifier = str(skill_id or "").strip()
        if not identifier:
            raise MobileInvalid("skill_id is required")
        manager = self._load_runtime_skills()
        success, message = manager.set_skill_enabled(identifier, bool(enabled))
        if not success:
            if "not found" in message.lower():
                raise MobileNotFound(message)
            raise MobileInvalid(message)
        skill = manager.all_skills.get(identifier)
        if skill is None:
            raise MobileNotFound(f"Skill not found: {identifier}")
        return {"skill": self._skill_payload(skill), "message": message}

    def _validate_selected_skills(self, skill_ids: list[str], profile: str) -> list[str]:
        unique: list[str] = []
        for raw in skill_ids:
            identifier = str(raw or "").strip()
            if identifier and identifier not in unique:
                unique.append(identifier)
        if len(unique) > 5:
            raise MobileInvalid("no more than 5 skills may be selected")
        if not unique:
            return []
        manager = self._load_runtime_skills()
        backend = self._profile_backend(profile)
        for identifier in unique:
            skill = manager.all_skills.get(identifier)
            if skill is None:
                raise MobileInvalid(f"unknown skill: {identifier}")
            if not skill.enabled:
                raise MobileInvalid(f"skill is disabled: {identifier}")
            if backend and not skill.supports_backend(backend):
                raise MobileInvalid(f"skill is not available for this profile: {identifier}")
        return unique

    def _scan_tasks(self, *, limit: int = TASK_SCAN_LIMIT) -> list[dict[str, Any]]:
        with open_db(get_db_path()) as conn:
            rows = conn.execute(
                "SELECT task_id, parent_id, status, profile, input_text, output_text, metadata, "
                "approval_blocked_tools, created_at, updated_at, started_at, completed_at, error_message "
                "FROM tasks ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            tasks.append(
                {
                    "task_id": row[0],
                    "parent_id": row[1],
                    "status": row[2],
                    "profile": row[3],
                    "input_text": row[4] or "",
                    "output_text": row[5] or "",
                    "metadata": _loads(row[6], {}),
                    "approval_blocked_tools": _loads(row[7], []),
                    "created_at": int(row[8] or 0),
                    "updated_at": int(row[9] or 0),
                    "started_at": row[10],
                    "completed_at": row[11],
                    "error_message": row[12] or "",
                }
            )
        return tasks

    @staticmethod
    def _session_of(task: dict[str, Any]) -> str:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        return str(metadata.get("session") or "")

    def _tasks_by_session(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for task in self._scan_tasks():
            session_id = self._session_of(task)
            if not session_id:
                continue
            grouped.setdefault(session_id, []).append(task)
        return grouped

    def _active_tasks_by_session(self) -> dict[str, list[dict[str, Any]]]:
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with open_db(get_db_path()) as conn:
            rows = conn.execute(
                f"SELECT task_id, status, metadata, created_at FROM tasks "
                f"WHERE status IN ({placeholders}) ORDER BY created_at DESC",
                tuple(sorted(ACTIVE_STATUSES)),
            ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            metadata = _loads(row[2], {})
            session_id = str(metadata.get("session") or "") if isinstance(metadata, dict) else ""
            if not session_id:
                continue
            grouped.setdefault(session_id, []).append(
                {"task_id": row[0], "status": row[1], "created_at": int(row[3] or 0)}
            )
        return grouped

    def _active_task_id_for_session(self, session_id: str) -> str:
        """Return the active session task using desktop-chat busy semantics.

        This prevents a new message from allocating a second task ID while the
        existing session task is still active.
        """
        identifier = str(session_id or "").strip()
        if not identifier:
            return ""
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with open_db(get_db_path()) as conn:
            row = conn.execute(
                f"SELECT task_id FROM tasks WHERE status IN ({placeholders}) "
                "AND json_valid(metadata) "
                "AND COALESCE(json_extract(metadata, '$.session'), '') = ? "
                "AND COALESCE(json_extract(metadata, '$.source'), '') = 'mobile_bridge' "
                "ORDER BY created_at DESC LIMIT 1",
                (*tuple(sorted(ACTIVE_STATUSES)), identifier),
            ).fetchone()
        return str(row[0]) if row else ""

    def _approved_session_rules_for_session(self, session_id: str) -> list[dict[str, Any]]:
        identifier = str(session_id or "").strip()
        if not identifier:
            return []
        generation = str((_read_session_meta(_session_path(identifier)) or {}).get('profile_generation') or '')
        with open_db(get_db_path()) as conn:
            rows = conn.execute(
                "SELECT metadata FROM tasks WHERE json_valid(metadata) "
                "AND COALESCE(json_extract(metadata, '$.session'), '') = ? "
                "AND COALESCE(json_extract(metadata, '$.source'), '') = 'mobile_bridge' "
                "ORDER BY created_at DESC, rowid DESC",
                (identifier,),
            ).fetchall()
        rules: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            metadata = _loads(row[0], {})
            if not isinstance(metadata, dict) or str(metadata.get('profile_generation') or '') != generation:
                continue
            raw_rules = metadata.get("approved_session_rules") if isinstance(metadata, dict) else None
            if not isinstance(raw_rules, list):
                continue
            for rule in raw_rules:
                if not isinstance(rule, dict):
                    continue
                key = json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if key in seen:
                    continue
                seen.add(key)
                rules.append(rule)
        return rules

    def sessions_overview(
        self,
        *,
        limit: int = 50,
        active_only: bool = False,
        archived_only: bool = False,
    ) -> dict[str, Any]:
        grouped = self._tasks_by_session()
        active_grouped = self._active_tasks_by_session()
        result: list[dict[str, Any]] = []
        archive_candidate_count = 0
        for item in list_sessions():
            session_id = str(item.get("session_id") or "")
            is_archived = bool(item.get("archived"))
            running = active_grouped.get(session_id, [])
            if not is_archived and not running:
                archive_candidate_count += 1
            if archived_only != is_archived:
                continue
            related = grouped.get(session_id, [])
            pending = [task for task in running if str(task.get("status")) == "waiting_approval"]
            entry = {
                "session_id": session_id,
                "created_at": item.get("created_at", 0),
                "updated_at": item.get("updated_at", 0),
                "profile": item.get("profile", ""),
                "model": item.get("model", ""),
                "provider": item.get("provider", ""),
                "cwd": item.get("cwd", ""),
                "title": str(item.get("title") or item.get("first_user_message") or "")[:160],
                "archived": is_archived,
                "pinned": item.get("pinned") is True,
                "message_count": item.get("message_count", 0),
                "active": bool(running),
                "active_status": str(running[0]["status"]) if running else "",
                "running_task_id": running[0]["task_id"] if running else None,
                "last_task_status": str(related[0]["status"]) if related else "",
                "pending_approvals": len(pending),
                "isolation": self._isolation_status_value(
                    str(item.get("profile") or "")
                ),
            }
            if active_only and not entry["active"]:
                continue
            result.append(entry)
        result.sort(key=lambda item: (item.get('pinned') is True, float(item.get('updated_at') or 0)), reverse=True)
        return {
            # total — number of sessions passing filters **before** the limit cutoff; clients (web) display this counter, while the list remains capped.
            "sessions": result[: max(1, int(limit))],
            "total": len(result),
            "archive_candidate_count": archive_candidate_count,
            "active_count": len(active_grouped),
        }

    def has_pending_profile_tasks(self, profile):
        from factory.core.runtime import get_db_path
        path = Path(get_db_path())
        if not path.exists():
            return False
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as connection:
            return connection.execute("SELECT 1 FROM tasks WHERE profile=? AND status IN "
                "('queued','processing','waiting_approval','waiting_user_input','cancelling') LIMIT 1", (profile,)).fetchone() is not None

    def activate_profile(self, profile):
        if self.profile_control is None:
            return
        reply = self.profile_control('activate', profile)
        error = reply.get('error')
        if error == 'profile_busy':
            raise MobileConflict('Текущий профиль занят. Дождитесь завершения его задач и повторите отправку.')
        if error:
            messages = {'vault_locked': 'Разблокируйте хранилище на компьютере один раз после запуска Игорька.',
                        'local_requires_host_mode': 'Локальная модель требует обычного режима выполнения на компьютере.',
                        'profile_not_verified': 'Профиль не проверен или больше недоступен.',
                        'image_busy': 'Дождитесь завершения подготовки окружения.'}
            raise MobileInvalid(messages.get(error, 'Не удалось запустить выбранный профиль.'))

    def session_options(self, *, workdir_limit: int = 20, model_profile: str = '') -> dict[str, Any]:
        """Return safe, server-derived choices for the Android new-chat form."""
        profiles: list[str] = []
        # Runtime profiles (~/.igorek-agent-factory/profiles) plus profiles bundled with the package (factory/config/profiles); otherwise bundled profiles are not visible in the form.
        for profile_name in list_profile_names():
            try:
                payload = json.loads(get_profile_path(profile_name).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                profiles.append(profile_name)
        if self.profile and self.profile not in profiles:
            profiles.insert(0, self.profile)
        elif self.profile in profiles:
            profiles.remove(self.profile)
            profiles.insert(0, self.profile)

        if self.available_profiles is not None:
            profiles = self.available_profiles()

        workdirs: list[str] = []
        external_workdirs: list[str] = []
        try:
            external_workdirs = [
                str(item.get("cwd") or "")
                for item in self.external_sessions_catalog.list_sessions(limit=100)["sessions"]
            ]
        except (OSError, ValueError, sqlite3.Error):
            external_workdirs = []
        for raw in [
            self.workdir,
            *(item.get("cwd", "") for item in list_sessions()),
            *external_workdirs,
        ]:
            value = str(raw or "").strip()
            if not value:
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute() or not candidate.is_dir():
                continue
            normalized = str(candidate.resolve())
            if normalized not in workdirs:
                workdirs.append(normalized)
            if len(workdirs) >= max(1, int(workdir_limit)):
                break

        default_cwd = ""
        configured_workdir = Path(self.workdir).expanduser()
        if configured_workdir.is_absolute() and configured_workdir.is_dir():
            default_cwd = str(configured_workdir.resolve())
        elif workdirs:
            default_cwd = workdirs[0]
        control = self.profile_control('catalog', '') if self.profile_control else {}
        profile_catalog = [dict(item) for item in control.get('profiles', [])]
        if model_profile and self.profile_control:
            selected = next((item for item in profile_catalog if item.get('name') == model_profile), None)
            if selected is not None:
                models = self.profile_control('models', model_profile)
                if models.get('models_status') in {'ready', 'unavailable', 'unsupported'}:
                    selected.update({key: models[key] for key in ('models', 'model_labels', 'models_status') if key in models})
                    if models.get('models_status') == 'unavailable':
                        default = selected.get('model', '')
                        selected['models'] = [default] if default and default != 'select-model' and models.get('allow_default_fallback', True) else []
        return {
            "profiles": profiles,
            "profile_catalog": profile_catalog,
            "profile_switch_supported": control.get('can_switch', False),
            "workdirs": workdirs,
            "default_profile": self.profile if self.profile in profiles else (profiles[0] if profiles else ""),
            "default_cwd": default_cwd,
        }

    def external_sessions(
        self,
        *,
        source: str = "",
        query: str = "",
        limit: int = 250,
        refresh: bool = False,
    ) -> dict[str, Any]:
        try:
            return self.external_sessions_catalog.list_sessions(
                source=source,
                query=query,
                limit=limit,
                refresh=refresh,
            )
        except ValueError as exc:
            raise MobileInvalid(str(exc)) from exc

    def get_external_session(
        self,
        source: str,
        session_id: str,
        *,
        offset: int = 0,
        limit: int = 400,
    ) -> dict[str, Any]:
        try:
            return self.external_sessions_catalog.get_session(
                source,
                session_id,
                offset=offset,
                limit=limit,
            )
        except KeyError as exc:
            raise MobileNotFound(str(exc)) from exc

    @staticmethod
    def _resolve_import_cwd(origin_cwd: str) -> tuple[str, str, str]:
        """Select an external session workspace using the VM admission policy.

        The source cwd is untrusted input. Rejected paths fall back to the
        service workspace and return a machine-readable reason.
        """
        raw = str(origin_cwd or "").strip()
        if not raw:
            return "", "default", "origin_cwd_empty"
        try:
            return resolve_workspace_cwd(raw), "origin", ""
        except IsolationError as exc:
            return "", "default", f"origin_cwd_rejected:{exc.reason}"

    def import_external_session(
        self,
        source: str,
        external_session_id: str,
        *,
        profile: str = "",
        cwd: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        effective_profile = str(profile or self.profile).strip()
        if not effective_profile:
            raise MobileInvalid("Выберите профиль для продолжения")
        # Empty cwd delegates workspace selection to the server.
        requested_cwd = str(cwd or "").strip()
        workdir = Path(requested_cwd or self.workdir).expanduser()
        if not workdir.is_absolute() or not workdir.is_dir():
            raise MobileInvalid("Рабочая папка должна существовать на компьютере и иметь абсолютный путь")
        cwd_source = "requested" if requested_cwd else "default"
        cwd_fallback_reason = ""
        origin_cwd = ""
        key = (
            f"external-import:{source}:{external_session_id}:{idempotency_key}"
            if idempotency_key
            else ""
        )
        with self._session_lock:
            existing = self.store.lookup_request(key) if key else None
            if existing and existing.get("session_id"):
                session_id = str(existing["session_id"])
                detail = self.get_session(session_id)
                meta = detail.get("meta") if isinstance(detail.get("meta"), dict) else {}
                return {
                    "session": detail,
                    "imported_messages": 0,
                    "skipped_messages": 0,
                    "duplicate": True,
                    "cwd": str(meta.get("cwd") or ""),
                    "cwd_source": str(meta.get("cwd_source") or ""),
                    "cwd_fallback_reason": str(meta.get("cwd_fallback_reason") or ""),
                    "origin_cwd": str(meta.get("origin_cwd") or ""),
                }
            try:
                record, messages, skipped = self.external_sessions_catalog.import_messages(
                    source,
                    external_session_id,
                )
            except KeyError as exc:
                raise MobileNotFound(str(exc)) from exc
            origin_cwd = str(record.cwd or "").strip()
            if not requested_cwd:
                inherited, cwd_source, cwd_fallback_reason = self._resolve_import_cwd(origin_cwd)
                if inherited:
                    workdir = Path(inherited)
            session_id = generate_session_name(prefix=f"import-{record.source}")
            path = _session_path(session_id)
            _write_session_meta(
                path,
                session_id=session_id,
                profile=effective_profile,
                cwd=str(workdir.resolve()),
            )
            meta_update: dict[str, Any] = {
                "title": f"Продолжение: {record.title}"[:160],
                "origin_source": record.source,
                "origin_session_id": record.session_id,
                "origin_updated_at": record.updated_at,
                "import_mode": "history_copy",
                "cwd_source": cwd_source,
                "origin_cwd": origin_cwd,
            }
            if cwd_fallback_reason:
                meta_update["cwd_fallback_reason"] = cwd_fallback_reason
            update_session_meta(path, meta_update)
            for item in messages:
                _append_entry(
                    path,
                    role=str(item.get("role") or "user"),
                    text=str(item.get("text") or ""),
                    extra={
                        **{key: value for key, value in item.items() if key not in {"role", "text"}},
                        "origin_source": record.source,
                        "origin_session_id": record.session_id,
                    },
                )
            if key:
                self.store.remember_request(key, kind="session", session_id=session_id)
        detail = self.get_session(session_id)
        return {
            "session": detail,
            "imported_messages": len(messages),
            "skipped_messages": skipped,
            "duplicate": False,
            "cwd": str(workdir.resolve()),
            "cwd_source": cwd_source,
            "cwd_fallback_reason": cwd_fallback_reason,
            "origin_cwd": origin_cwd,
        }

    def list_sessions(
        self,
        *,
        limit: int = 50,
        active_only: bool = False,
        archived_only: bool = False,
    ) -> list[dict[str, Any]]:
        return self.sessions_overview(
            limit=limit,
            active_only=active_only,
            archived_only=archived_only,
        )["sessions"]

    def switch_session_profile(self, session_id: str, profile: str, *, expected_profile: str | None = None) -> dict[str, Any]:
        """Activate a verified profile and retain the idle chat and its history."""
        if not isinstance(profile, str) or not profile or self.profile_control is None:
            raise MobileInvalid('Переключение профиля недоступно.')
        with self._approval_lock, self._session_lock:
            path = _session_path(session_id)
            if not path.is_file():
                raise MobileNotFound(f'session not found: {session_id}')
            meta = _read_session_meta(path) or {}
            if expected_profile is not None and meta.get('profile') != expected_profile:
                raise MobileConflict('Профиль чата изменился. Обновите чат перед переключением.')
            if meta.get('agent_session') or meta.get('demo'):
                raise MobileInvalid('Для этой сессии переключение профиля недоступно.')
            if self._active_task_id_for_session(session_id):
                raise MobileConflict('Дождитесь завершения текущей задачи перед переключением профиля.')
            catalog = self.profile_control('catalog', '')
            selected = next((p for p in catalog.get('profiles', []) if p.get('name') == profile), None)
            if selected is None or not catalog.get('can_switch'):
                raise MobileInvalid('Профиль не проверен, хранилище заблокировано или подключение недоступно.')
            self.activate_profile(profile)
            self._require_available_profile(profile)
            if profile != meta.get('profile'):
                update_session_meta(path, {'profile': profile, 'model': selected.get('model', ''),
                    'model_override': '', 'provider': '', 'profile_generation': uuid.uuid4().hex})
            return self.get_session(session_id)

    def switch_session_model(self, session_id: str, profile: str, model: str) -> dict[str, Any]:
        if not isinstance(profile, str) or not isinstance(model, str) or self.profile_control is None:
            raise MobileInvalid('Переключение модели недоступно.')
        with self._approval_lock, self._session_lock:
            path = _session_path(session_id)
            if not path.is_file():
                raise MobileNotFound(f'session not found: {session_id}')
            meta = _read_session_meta(path) or {}
            if meta.get('agent_session') or meta.get('demo'):
                raise MobileInvalid('Для этой сессии переключение модели недоступно.')
            if self._active_task_id_for_session(session_id):
                raise MobileConflict('Дождитесь завершения текущей задачи перед переключением модели.')
            if meta.get('profile') != profile:
                raise MobileConflict('Профиль чата изменился. Обновите список моделей.')
        models = self.profile_control('models', profile)
        fallback = models.get('models_status') == 'unavailable'
        if fallback and not models.get('allow_default_fallback', True):
            raise MobileInvalid('Не удалось проверить доступные модели. Обновите список и повторите выбор.')
        if not fallback and models.get('models_status') != 'ready':
            raise MobileInvalid('Не удалось загрузить доступные модели. Повторите позже.')
        if not fallback and model not in models.get('models', []):
            raise MobileInvalid('Модель недоступна для этого профиля.')
        with self._approval_lock, self._session_lock:
            path = _session_path(session_id)
            if not path.is_file():
                raise MobileNotFound(f'session not found: {session_id}')
            meta = _read_session_meta(path) or {}
            if meta.get('agent_session') or meta.get('demo'):
                raise MobileInvalid('Для этой сессии переключение модели недоступно.')
            if self._active_task_id_for_session(session_id):
                raise MobileConflict('Дождитесь завершения текущей задачи перед переключением модели.')
            if meta.get('profile') != profile:
                raise MobileConflict('Профиль чата изменился. Обновите список моделей.')
            catalog = self.profile_control('catalog', '')
            selected = next((p for p in catalog.get('profiles', []) if p.get('name') == profile), None)
            if not catalog.get('can_switch') or not selected:
                raise MobileInvalid('Модель недоступна для этого профиля.')
            current_models = self.profile_control('models', profile)
            if current_models.get('models_status') == 'unavailable':
                if not current_models.get('allow_default_fallback', True) or model != selected.get('model') or model == 'select-model':
                    raise MobileInvalid('Не удалось загрузить доступные модели. Повторите позже.')
            elif current_models.get('models_status') != 'ready' or model not in current_models.get('models', []):
                raise MobileInvalid('Модель недоступна для этого профиля.')
            update_session_meta(path, {'model': model, 'model_override': model})
            return self.get_session(session_id)

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
        pinned: bool | None = None,
    ) -> dict[str, Any]:
        """Rename the chat and/or archive it."""
        with self._session_lock:
            path = _session_path(session_id)
            if not path.exists():
                raise MobileNotFound(f"session not found: {session_id}")
            updates: dict[str, Any] = {}
            if title is not None:
                updates["title"] = str(title).strip()[:160]
            if archived is not None:
                updates["archived"] = bool(archived)
            if pinned is not None:
                if not isinstance(pinned, bool):
                    raise MobileInvalid('pinned must be boolean')
                updates['pinned'] = pinned
            meta = update_session_meta(path, updates) if updates else (_read_session_meta(path) or {})
        return {
            "session_id": session_id,
            "meta": meta,
            "title": str(meta.get("title") or ""),
            "archived": bool(meta.get("archived")),
            "pinned": meta.get('pinned') is True,
        }

    def archive_sessions(self, *, archived: bool = True) -> dict[str, Any]:
        with self._session_lock:
            active_sessions = set(self._active_tasks_by_session())
            updated: list[str] = []
            skipped_active: list[str] = []
            unchanged = 0
            for item in list_sessions():
                session_id = str(item.get("session_id") or "")
                if not session_id or bool(item.get("archived")) == bool(archived):
                    unchanged += 1
                    continue
                if archived and session_id in active_sessions:
                    skipped_active.append(session_id)
                    continue
                update_session_meta(_session_path(session_id), {"archived": bool(archived)})
                updated.append(session_id)
        return {
            "archived": bool(archived),
            "updated": len(updated),
            "skipped_active": len(skipped_active),
            "unchanged": unchanged,
            "session_ids": updated,
            "skipped_session_ids": skipped_active,
        }

    def session_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return self.store.list_events(
            after_seq=after_seq,
            session_id=session_id,
            limit=limit,
        )

    # --------------------------------- (delegate_task subagents) --

    @staticmethod
    def _task_artifact_workdir(task_id: str) -> Path:
        """The working directory for task artifacts is the same as the trace directory."""
        return Path(get_workspace_root()) / f"temp_execution_{str(task_id or '')[:8]}"

    def _agent_record(self, session_id: str) -> dict[str, Any] | None:
        if not session_id.startswith("agent-sub_"):
            return None
        start = self.store.agent_record_event(session_id)
        if start is None:
            raise MobileNotFound(f"agent session not found: {session_id}")
        root = str(start.get("root_session_id") or "")
        records = subagent_journal.fold_subagent_events(self.store.list_subagent_events(root))
        return next((record for record in records if record["subagent_id"] == start["subagent_id"]), None)

    def agent_sessions(self, session_id: str) -> dict[str, Any]:
        child = self._agent_record(session_id)
        root = str(child.get("root_session_id") or child.get("session_id")) if child else session_id
        main = self.get_session(root)
        rows = subagent_journal.fold_subagent_events(self.store.list_subagent_events(root))
        latest = main.get("tasks", [])
        latest_status = latest[0].get("status") if latest else None
        main_status = {"fail": "failed", "waiting_approval": "waiting_approval", "queued": "running", "processing": "running", "running": "running", "cancelled": "cancelled"}.get(latest_status, "running" if main.get("running_task_id") else "completed")
        nodes = [{
            "session_id": root, "parent_session_id": None, "root_session_id": root,
            "title": main["meta"].get("title") or "Main", "role": "Main",
            "status": main_status,
            "activity": "", "depth": 0,
        }]
        from ..cc_backend_trace import agent_session_id
        for row in rows:
            status = {"ok": "completed", "error": "failed", "cancelled": "cancelled"}.get(row["status"], row["status"])
            nodes.append({**row, "session_id": row.get("agent_session_id") or agent_session_id(row["subagent_id"]),
                          "parent_session_id": row.get("parent_session_id") or root,
                          "root_session_id": root, "title": row["goal_title"], "role": "Subagent",
                          "status": status, "activity": row.get("last_tool") or row.get("last_stage") or ""})
        return {"root_session_id": root, "sessions": nodes}

    def _agent_session_detail(self, session_id: str, record: dict[str, Any]) -> dict[str, Any]:
        root = str(record.get("root_session_id") or record.get("session_id") or "")
        main = self.get_session(root)
        active = record["status"] in {"running", "waiting_approval", "waiting_input"}
        messages = [{"role": "user", "text": record.get("goal_text") or record["goal_title"], "history_id": "goal-" + session_id}]
        has_trace = bool(self.store.list_events(session_id=session_id, limit=1))
        if not has_trace and record.get("summary"):
            messages.append({"role": "assistant", "text": record["summary"], "history_id": "summary-" + session_id})
        meta = {**main["meta"], "title": record["goal_title"], "parent_session_id": record.get("parent_session_id") or root,
                "root_session_id": root, "agent_session": True, "subagent_id": record["subagent_id"], "agent_status": record["status"]}
        return {"session_id": session_id, "meta": meta, "messages": messages,
                "running_task_id": record["parent_task_id"] if active else None,
                "tasks": [], "isolation": main.get("isolation"), "context_window": main.get("context_window")}

    def list_subagents(self, session_id: str, *, limit: int = 200) -> dict[str, Any]:
        """Session subagent summary: active and recently completed.

        Source: durable log ``mobile_events`` (kind ``subagent.*``),
        so the list survives web process restart and browser reconnection.
        No hidden child context is present here: only task title, status, progress, and result.
        """
        path = _session_path(session_id)
        if not path.exists():
            raise MobileNotFound(f"session not found: {session_id}")
        events = self.store.list_subagent_events(session_id)
        records = subagent_journal.fold_subagent_events(events)
        active = [r for r in records if r["status"] in subagent_journal.ACTIVE_STATUSES]
        finished = [r for r in records if r["status"] not in subagent_journal.ACTIVE_STATUSES]
        active.sort(key=lambda r: int(r.get("started_at") or 0))
        finished.sort(key=lambda r: int(r.get("finished_at") or r.get("updated_at") or 0), reverse=True)
        bounded = max(1, min(int(limit), 500))
        ordered = active + finished
        visible = ordered[:bounded]
        return {
            "subagents": visible,
            "active": len(active),
            "total": len(ordered),
            "truncated": len(ordered) > len(visible),
        }

    def cancel_subagent(self, session_id: str, subagent_id: str) -> dict[str, Any]:
        """Addressed cooperative cancellation of a single child launch.

        The child execution runs outside of ``tasks``, so the signal is passed via a file flag in the task working directory: both the host process and the guest session virtual machine detect it through virtiofs. Cancellation affects only the selected child; adjacent launches and the parent task remain unaffected.
        """
        path = _session_path(session_id)
        if not path.exists():
            raise MobileNotFound(f"session not found: {session_id}")
        if not subagent_id:
            raise MobileInvalid("subagent_id обязателен")
        record = next(
            (
                item
                for item in subagent_journal.fold_subagent_events(
                    self.store.list_subagent_events(session_id)
                )
                if item["subagent_id"] == subagent_id
            ),
            None,
        )
        if record is None:
            raise MobileNotFound(f"subagent not found: {subagent_id}")
        if record["status"] not in subagent_journal.ACTIVE_STATUSES:
            return {
                "subagent_id": subagent_id,
                "cancelled": False,
                "status": record["status"],
                "terminal": True,
                "reason": "already_finished",
            }
        parent_task_id = str(record.get("parent_task_id") or "")
        artifact_workdir = self._task_artifact_workdir(parent_task_id)
        directories = [artifact_workdir]
        mapped = read_artifact_route(artifact_workdir)
        if mapped is not None and mapped != artifact_workdir:
            directories.append(mapped)
        accepted = False
        for directory in directories:
            reporter = subagent_journal.SubagentReporter(
                artifact_workdir=directory,
                session_id=session_id,
                parent_task_id=parent_task_id,
            )
            accepted = reporter.request_cancel(subagent_id) or accepted
        if accepted:
            logger.warning(
                "mobile_bridge.subagent_cancel_requested session_id=%s subagent_id=%s parent_task_id=%s",
                session_id,
                subagent_id,
                parent_task_id or "(none)",
            )
        self.store.append_event(
            kind="subagent.cancel_requested",
            payload={
                "subagent_id": subagent_id,
                "parent_task_id": parent_task_id,
                "task_id": parent_task_id,
                "accepted": bool(accepted),
            },
            session_id=session_id,
            task_id=parent_task_id or None,
        )
        return {
            "subagent_id": subagent_id,
            "cancelled": bool(accepted),
            "status": "cancelling" if accepted else record["status"],
            "terminal": False,
            "reason": "" if accepted else "cancel_flag_unavailable",
        }

    def _active_subagent_ids(self, session_id: str) -> list[dict[str, Any]]:
        events = self.store.list_subagent_events(session_id)
        return [
            item
            for item in subagent_journal.fold_subagent_events(events)
            if item["status"] in subagent_journal.ACTIVE_STATUSES
        ]

    def _session_durable_root(self, session_workdir: str, profile: str) -> str | None:
        """Workdir-relative durable root a kvm session writes its artifacts to.

        Only an operational kvm ``cc-backend`` session gets ``data_root`` inside
        the workspace (``.igorek-host-data``, todo 26); the helper checks that
        same data-root/workdir relation the tracker uses, and every other case
        (off mode, non-cc-backend, unavailable kvm) stays ``None`` so nothing is
        excluded at aggregation.
        """
        if self._isolation_status_value(profile) != ISOLATION_KVM:
            return None
        return durable_root_within_workdir(
            workdir=session_workdir,
            data_root=durable_data_root(session_workdir),
        )

    def _session_artifact_root(self, profile: str) -> str | None:
        """Workdir-relative guest artifact tree of a kvm session, or ``None``.

        The kvm dispatcher routes the guest artifact dir into the exported
        workspace (``<workdir>/.igorek-artifacts``, see
        :mod:`factory.core.isolation.artifact_map`); those paths are runtime
        internals and never user files. Off mode and non-cc-backend profiles
        pass ``None`` so the directory name stays ordinary user data there.
        """
        if self._isolation_status_value(profile) != ISOLATION_KVM:
            return None
        return ARTIFACT_DIRNAME

    def session_changes(self, session_id: str) -> dict[str, Any]:
        """Aggregate changed files across all tasks of a session.

        Paths are relative to the session working directory; internal artifact
        directories (``.checkpoints``, ``.tool_results``, ``.codex``) and the
        kvm durable root are excluded.

        A kvm task persists its record inside the workspace durable root (the
        guest tracker writes through virtiofs), so those records are read as a
        fallback when no host-side record exists for the task.
        """
        record = self._agent_record(session_id)
        if record:
            session_id = str(record.get("root_session_id") or record.get("session_id"))
        path = _session_path(session_id)
        if not path.exists():
            raise MobileNotFound(f"session not found: {session_id}")
        meta = _read_session_meta(path) or {}
        session_workdir = str(Path(meta.get("cwd") or self.workdir).expanduser())
        related = self._tasks_by_session().get(session_id, [])
        ordered = sorted(related, key=lambda task: (task["created_at"], task["task_id"]))
        durable_root = self._session_durable_root(
            session_workdir, str(meta.get("profile") or "")
        )
        durable_dir = Path(session_workdir) / durable_root if durable_root else None
        records: list[dict[str, Any]] = []
        for task in ordered:
            task_id = str(task["task_id"])
            record = load_task_changes(task_id, data_root=self.data_dir)
            if record is None and durable_dir is not None:
                record = self._load_durable_task_changes(task_id, durable_dir, session_workdir)
            if record is not None:
                records.append(record)
        return _drop_artifact_paths(
            aggregate_changes(
                records,
                session_workdir=session_workdir,
                durable_root=durable_root,
            ),
            self._session_artifact_root(str(meta.get("profile") or "")),
        )

    @staticmethod
    def _load_durable_task_changes(
        task_id: str, durable_dir: Path, session_workdir: str
    ) -> dict[str, Any] | None:
        """Read a kvm task record from the workspace durable root.

        The in-guest tracker records the guest-visible workdir (``/workspace``),
        which is the exported session workdir; the host rewrites the field so
        relative paths resolve against the real tree. Paths escaping the workdir
        are dropped later by :func:`aggregate_changes` as before.
        """
        record = load_task_changes(task_id, data_root=str(durable_dir))
        if record is None:
            return None
        return {**record, "workdir": session_workdir}


    def session_changes_review(self, session_id: str, *, mode: str = "session", base: str = "", path: str = "", revision: str = "", include_untracked: bool = False) -> dict[str, Any]:
        """Review a bounded comparison without mutating the working tree."""
        from ..review_changes import ChangesReview, ReviewError, checkpoint_repositories
        record = self._agent_record(session_id)
        if record:
            session_id = str(record.get("root_session_id") or record.get("session_id"))
        session_file = _session_path(session_id)
        if not session_file.exists():
            raise MobileNotFound("session not found")
        meta = _read_session_meta(session_file) or {}
        root = Path(meta.get("cwd") or self.workdir).expanduser().resolve()
        durable_relative = self._session_durable_root(str(root), str(meta.get("profile") or ""))
        durable = root / durable_relative if durable_relative else None
        baseline = ""
        tasks = sorted(self._tasks_by_session().get(session_id, []), key=lambda t: (t["created_at"], t["task_id"]))
        for task in tasks:
            record = load_task_changes(str(task["task_id"]), data_root=self.data_dir)
            if record is None and durable is not None:
                record = self._load_durable_task_changes(str(task["task_id"]), durable, str(root))
            if record and record.get("files"):
                baseline = str(record.get("baseline_revision") or "")
                break
        review = ChangesReview(root, session_files=self.session_changes(session_id)["files"], repositories=checkpoint_repositories(root, Path(self.data_dir), durable), baseline=baseline, include_untracked=include_untracked, excluded=[p for p in [durable_relative, self._session_artifact_root(str(meta.get("profile") or ""))] if p])
        try:
            review.prepare(mode, base)
            review.pin_revision(revision)
            return review.result(path)
        except ReviewError as exc:
            raise MobileInvalid(str(exc)) from exc

    def session_changes_diff(self, session_id: str, path: str) -> dict[str, Any]:
        """Return the structured unified diff for one changed file of a session.

        The baseline is the pre-change snapshot in the checkpoint shadow repo;
        the current side is read from disk. Binary or oversized files, and
        files without a checkpoint baseline, degrade to ``source: unavailable``.
        """
        session_file = _session_path(session_id)
        if not session_file.exists():
            raise MobileNotFound(f"session not found: {session_id}")
        target = str(path or "").strip()
        if not target:
            raise MobileNotFound("path is required")
        if Path(target).is_absolute():
            raise MobileNotFound(f"path is outside the session workdir: {target}")
        meta = _read_session_meta(session_file) or {}
        session_workdir = str(Path(meta.get("cwd") or self.workdir).expanduser())
        relative = normalize_workdir_path(target, workdir=session_workdir)
        if relative is None:
            raise MobileNotFound(f"path is outside the session workdir: {target}")
        artifact_root = self._session_artifact_root(str(meta.get("profile") or ""))
        if artifact_root and is_within_relative(relative, artifact_root):
            raise MobileNotFound(f"path is a runtime artifact: {relative}")
        match = next(
            (
                item
                for item in self.session_changes(session_id)["files"]
                if item["path"] == relative
            ),
            None,
        )
        if match is None:
            raise MobileNotFound(f"file has no recorded changes: {relative}")
        absolute = Path(session_workdir).resolve() / relative
        if not absolute.is_file() and str(match.get("status")) != "deleted":
            raise MobileNotFound(f"file not found: {relative}")

        from ..change_file_types import non_text_change_kind
        if non_text_change_kind(relative):
            return self._unavailable_diff(relative, 0, 0)

        additions = int(match["additions"])
        deletions = int(match["deletions"])
        try:
            try:
                with open_regular_file(absolute) as stream:
                    raw = stream.read(MAX_DIFF_FILE_BYTES + 1)
            except FileNotFoundError:
                raw = b""
        except OSError as exc:
            raise MobileNotFound(f"file not found: {relative}") from exc
        if b"\x00" in raw:
            return self._unavailable_diff(relative, additions, deletions)
        if len(raw) > MAX_DIFF_FILE_BYTES:
            return self._unavailable_diff(relative, additions, deletions, truncated=True)

        source = "checkpoint"
        available, baseline = CheckpointManager().baseline_content(
            target_path=str(absolute),
            workdir=session_workdir,
        )
        if not available:
            from ..review_changes import checkpoint_repositories, checkpoint_text
            durable_relative = self._session_durable_root(session_workdir, str(meta.get("profile") or ""))
            durable = Path(session_workdir) / durable_relative if durable_relative else None
            available, baseline = checkpoint_text(Path(session_workdir), relative, checkpoint_repositories(Path(session_workdir), Path(self.data_dir), durable))
        if not available:
            # A kvm task keeps its shadow repo in the workspace durable root,
            # invisible here; for a new file the honest baseline is empty.
            if str(match.get("status") or "") != "added":
                return self._unavailable_diff(relative, additions, deletions)
            baseline = None
            source = "added"
        if baseline is not None and "\x00" in baseline:
            return self._unavailable_diff(relative, additions, deletions)

        old_text = baseline or ""
        new_text = raw.decode("utf-8", errors="replace")
        additions, deletions = count_line_changes(old_text, new_text)
        hunks, truncated = build_diff_hunks(old_text, new_text)
        return {
            "path": relative,
            "additions": additions,
            "deletions": deletions,
            "source": source,
            "truncated": truncated,
            "hunks": hunks,
        }

    @staticmethod
    def _unavailable_diff(
        path: str,
        additions: int,
        deletions: int,
        *,
        truncated: bool = False,
    ) -> dict[str, Any]:
        return {
            "path": path,
            "additions": int(additions),
            "deletions": int(deletions),
            "source": "unavailable",
            "truncated": bool(truncated),
            "hunks": [],
        }

    def _session_workdir(self, session_id: str) -> str:
        """Resolve the working directory of a session, mirroring ``session_changes``."""
        record = self._agent_record(session_id)
        if record:
            session_id = str(record.get("root_session_id") or record.get("session_id"))
        path = _session_path(session_id)
        if not path.exists():
            raise MobileNotFound(f"session not found: {session_id}")
        meta = _read_session_meta(path) or {}
        return str(Path(meta.get("cwd") or self.workdir).expanduser())

    def session_checkpoints(
        self,
        session_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read-only page of the session's checkpoint history (newest first)."""
        workdir = self._session_workdir(session_id)
        bounded_limit = max(0, int(limit))
        bounded_offset = max(0, int(offset))
        manager = CheckpointManager()
        if not manager.has_snapshot_repo(workdir):
            return {
                "workdir": workdir,
                "available": False,
                "checkpoints": [],
                "limit": bounded_limit,
                "offset": bounded_offset,
                "has_more": False,
            }
        checkpoints = manager.list_checkpoints(workdir)
        page = checkpoints[bounded_offset : bounded_offset + bounded_limit]
        return {
            "workdir": workdir,
            "available": True,
            "checkpoints": page,
            "limit": bounded_limit,
            "offset": bounded_offset,
            "has_more": bounded_offset + len(page) < len(checkpoints),
        }

    def session_checkpoint_changes(self, session_id: str, revision: str) -> dict[str, Any]:
        """Changed-file stats between a checkpoint revision and the current work tree."""
        workdir = self._session_workdir(session_id)
        manager = CheckpointManager()
        rev = str(revision or "").strip()
        if not manager.has_revision(working_dir=workdir, revision=rev):
            raise MobileNotFound(f"unknown checkpoint: {revision}")
        files = manager.changed_paths_since(working_dir=workdir, revision=rev) or []
        return {
            "files": files,
            "totals": {
                "files": len(files),
                "additions": sum(int(item["additions"]) for item in files),
                "deletions": sum(int(item["deletions"]) for item in files),
            },
        }

    def session_checkpoint_diff(self, session_id: str, revision: str, path: str) -> dict[str, Any]:
        """Unified diff of one file between a checkpoint revision and the current disk.

        Read-only: the old side comes from the shadow repo, the new side from the
        work tree. Binary, oversized or unavailable content degrades to
        ``source: unavailable``.
        """
        workdir = self._session_workdir(session_id)
        manager = CheckpointManager()
        rev = str(revision or "").strip()
        if not manager.has_revision(working_dir=workdir, revision=rev):
            raise MobileNotFound(f"unknown checkpoint: {revision}")
        target = str(path or "").strip()
        if not target:
            raise MobileNotFound("path is required")
        if Path(target).is_absolute():
            raise MobileNotFound(f"path is outside the session workdir: {target}")
        relative = normalize_workdir_path(target, workdir=workdir)
        if relative is None:
            raise MobileNotFound(f"path is outside the session workdir: {target}")
        absolute = Path(workdir).resolve() / relative

        from ..change_file_types import non_text_change_kind
        if non_text_change_kind(relative):
            return self._unavailable_checkpoint_diff(relative, rev)

        available, old = manager.content_at(
            target_path=str(absolute),
            workdir=workdir,
            revision=rev,
        )
        if not available or (old is not None and "\x00" in old):
            return self._unavailable_checkpoint_diff(relative, rev)

        try:
            try:
                with open_regular_file(absolute) as stream:
                    raw = stream.read(MAX_DIFF_FILE_BYTES + 1)
            except FileNotFoundError:
                raw = b""
        except OSError as exc:
            raise MobileNotFound(f"file not found: {relative}") from exc
        if b"\x00" in raw:
            return self._unavailable_checkpoint_diff(relative, rev)
        if len(raw) > MAX_DIFF_FILE_BYTES:
            return self._unavailable_checkpoint_diff(relative, rev, truncated=True)

        old_text = old or ""
        new_text = raw.decode("utf-8", errors="replace")
        if old_text == new_text:
            raise MobileNotFound(f"file has no changes at checkpoint: {relative}")
        additions, deletions = count_line_changes(old_text, new_text)
        hunks, truncated = build_diff_hunks(old_text, new_text)
        return {
            "path": relative,
            "additions": additions,
            "deletions": deletions,
            "source": "checkpoint",
            "truncated": truncated,
            "from": rev,
            "hunks": hunks,
        }

    @staticmethod
    def _unavailable_checkpoint_diff(
        path: str,
        revision: str,
        *,
        truncated: bool = False,
    ) -> dict[str, Any]:
        return {
            "path": path,
            "additions": 0,
            "deletions": 0,
            "source": "unavailable",
            "truncated": bool(truncated),
            "from": revision,
            "hunks": [],
        }

    def _require_available_profile(self, profile: str):
        if self.available_profiles is not None and profile not in self.available_profiles():
            raise MobileInvalid("Запустите исполнитель этого профиля в настройке хранилища")

    def create_session(self, *, profile: str = "", cwd: str = "", idempotency_key: str = "", activate_profile: bool = False) -> dict[str, Any]:
        with self._approval_lock:
            key = f"session:{idempotency_key}" if idempotency_key else ""
            existing = self.store.lookup_request(key) if key else None
            if existing and existing.get("session_id"):
                return self.get_session(existing["session_id"])
            effective_profile = str(profile or self.profile).strip()
            if not effective_profile:
                raise MobileInvalid("Выберите профиль для новой сессии")
            if activate_profile:
                candidate = Path(cwd or self.workdir).expanduser()
                if not candidate.is_absolute() or not candidate.is_dir():
                    raise MobileInvalid("Рабочая папка должна существовать на компьютере и иметь абсолютный путь")
                self.activate_profile(effective_profile)
            self._require_available_profile(effective_profile)
            workdir = Path(cwd or self.workdir).expanduser()
            if not workdir.is_absolute() or not workdir.is_dir():
                raise MobileInvalid("Рабочая папка должна существовать на компьютере и иметь абсолютный путь")
            session_id = generate_session_name()
            _write_session_meta(_session_path(session_id), session_id=session_id,
                                profile=effective_profile, cwd=str(workdir))
            if key:
                self.store.remember_request(key, kind="session", session_id=session_id)
            return self.get_session(session_id)

    def _working_history(self, session_id: str, *, exclude_message_ids: set[str] | None = None) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        after = 0
        while True:
            page = self.store.list_events(session_id=session_id, after_seq=after, limit=500)
            if not page:
                break
            events.extend(page)
            after = page[-1]["seq"]
        return build_working_history(read_history(_session_path(session_id)), events, exclude_message_ids=exclude_message_ids)

    def get_session(self, session_id: str, *, message_limit: int | None = None) -> dict[str, Any]:
        record = self._agent_record(session_id)
        if record is not None:
            return self._agent_session_detail(session_id, record)
        path = _session_path(session_id)
        if not path.exists():
            raise MobileNotFound(f"session not found: {session_id}")
        meta = _read_session_meta(path) or {}
        entries = read_history(path)
        related = self._tasks_by_session().get(session_id, [])
        running = [task for task in related if str(task.get("status")) in ACTIVE_STATUSES]
        pending_message_ids = {
            str(item.get("message_id") or "")
            for items in pending_steer_sessions().values()
            for item in items
            if str(item.get("session_id") or "") == session_id
            and str(item.get("message_id") or "")
        }
        for task in related:
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            if str(task.get("status") or "") != "queued":
                continue
            for message_id in metadata.get("pending_steer_ids") or []:
                if str(message_id or ""):
                    pending_message_ids.add(str(message_id))
        messages = [
            {
                "role": str(entry.get("role") or ""),
                "text": str(entry.get("text") or ""),
                "ts": entry.get("ts"),
                "task_id": entry.get("task_id"),
                "message_id": entry.get("message_id"),
                "status": entry.get("status"),
                "skills": _entry_skills(entry),
                "files": entry.get("files", []),
                **{key: entry[key] for key in ("kind", "history_id", "order", "tool_use_id", "tool_name", "tool_input", "tool_output", "tool_status") if key in entry},
                "queued": bool(
                    entry.get("steer")
                    and str(entry.get("message_id") or "") in pending_message_ids
                ),
            }
            for entry in (entries if message_limit is None else entries[-max(1, int(message_limit)) :])
        ]
        task_attachments = {
            task["task_id"]: public_attachments((task.get("metadata") or {}).get("attachments"))
            for task in related
        }
        for entry, message in zip(
            entries if message_limit is None else entries[-max(1, int(message_limit)) :], messages
        ):
            if message["role"] == "user":
                attachments = entry.get("attachments")
                if attachments is None and not entry.get("steer"):
                    attachments = task_attachments.get(entry.get("task_id"), [])
                message["attachments"] = public_attachments(attachments)
            elif message["role"] == "assistant" and not message["files"]:
                base = Path(meta.get("cwd") or self.workdir).expanduser().resolve()
                task_id = str(entry.get("workspace_task_id") or entry.get("task_id") or "")
                message["files"] = [info for info, _ in collect_files(base, message["text"], entry.get("output_files") or [], task_id)][:10]
        from ...web_ui.server import _profile_context_window, _profile_model
        context_model = _profile_model(str(meta.get("profile") or "")) or str(meta.get("model") or "")
        from ..opencode_go_catalog import canonical_opencode_go_model, is_opencode_go_url
        try:
            profile_name = str(meta.get('profile') or '')
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', profile_name):
                raise ValueError('Invalid profile name')
            config = json.loads(get_profile_path(profile_name).read_text(encoding='utf-8'))
            go_profile = is_opencode_go_url(str(config.get('base_url') or ''))
        except (OSError, ValueError, TypeError, AttributeError):
            go_profile = False
        if go_profile:
            meta = dict(meta)
            for field in ('model', 'model_override'):
                if isinstance(meta.get(field), str):
                    meta[field] = canonical_opencode_go_model(meta[field])
        return {
            "context_window": _profile_context_window(str(meta.get("profile") or ""), context_model),
            "model": context_model,
            "session_id": session_id,
            "goal": get_goal(session_id),
            "isolation": self._isolation_status_value(str(meta.get("profile") or "")),
            "meta": meta,
            "messages": messages,
            "running_task_id": running[0]["task_id"] if running else None,
            "tasks": [
                {
                    "task_id": task["task_id"],
                    "status": task["status"],
                    "created_at": task["created_at"],
                    "updated_at": task["updated_at"],
                    "started_at": task.get("started_at"),
                    "completed_at": task.get("completed_at"),
                    "error_message": task["error_message"],
                }
                for task in related[:20]
            ],
        }

    def goal_action(self, session_id: str, action: str, *, objective: str = "",
                    goal_id: str | None = None, token_budget: int | None = None,
                    activate_profile: bool = False) -> dict:
        from .. import thread_goals

        if not _session_path(session_id).exists():
            raise MobileNotFound(f"session not found: {session_id}")
        with self._message_request_lock(session_id, scope="session"):
            if action == "create":
                if activate_profile:
                    meta = _read_session_meta(_session_path(session_id)) or {}
                    self.activate_profile(str(meta.get("profile") or self.profile))
                goal = thread_goals.create_goal(session_id, objective, token_budget=token_budget)
            elif action == "edit":
                goal = thread_goals.edit_goal(session_id, objective, expected_goal_id=goal_id)
            elif action in {"pause", "resume"}:
                goal = thread_goals.update_goal(session_id, "paused" if action == "pause" else "active",
                                                expected_goal_id=goal_id, by_user=True)
            elif action == "clear":
                thread_goals.clear_goal(session_id, expected_goal_id=goal_id)
                goal = None
            else:
                raise ValueError("unknown goal action")
            payload = {"goal": goal}
            seq = self.store.append_event(kind="goal.updated", payload=payload, session_id=session_id)
            self._publish_event(kind="goal.updated", payload=payload, session_id=session_id, task_id=None, seq=seq)
            return payload

    def advance_goals(self) -> int:
        from ..thread_goals import list_active_goals

        return sum(self._advance_goal(goal) for goal in list_active_goals("mobile_bridge"))

    def _advance_goal(self, snapshot: dict) -> int:
        from .. import thread_goals
        from ..prompt_assembly import goal_continuation_prompt

        session_id = snapshot["session_id"]
        with self._message_request_lock(session_id, scope="session"):
            path = _session_path(session_id)
            meta = _read_session_meta(path) if path.exists() else None
            if not meta or meta.get("archived"):
                return 0
            profile = str(meta.get("profile") or self.profile).strip()
            self._require_available_profile(profile)
            workdir = str(meta.get("cwd") or self.workdir)
            history = _load_entries(path)
            working_history = self._working_history(session_id)
            text = goal_continuation_prompt(snapshot["objective"])
            prompt = _build_prompt(history, text, foundation_context=self._foundation_context())
            changed_goal = None
            task_id = None
            created = False
            with open_db(get_db_path()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = thread_goals._read(conn, session_id)
                if (not current or current["status"] != "active"
                        or current["goal_id"] != snapshot["goal_id"]
                        or current["revision"] != snapshot["revision"]):
                    return 0
                placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
                running = conn.execute(
                    f"SELECT task_id, metadata FROM tasks WHERE status IN ({placeholders}) AND json_valid(metadata) "
                    "AND json_extract(metadata, '$.session') = ? LIMIT 1",
                    (*tuple(sorted(ACTIVE_STATUSES)), session_id),
                ).fetchone()
                pending = conn.execute(
                    "SELECT 1 FROM session_steer_messages WHERE session_id = ? AND consumed_at IS NULL LIMIT 1",
                    (session_id,),
                ).fetchone()
                if pending:
                    return 0
                if running:
                    running_metadata = _loads(running[1], {})
                    if running_metadata.get("goal_id") != current["goal_id"]:
                        return 0
                    task_id = str(running[0])
                else:
                    previous = conn.execute(
                        "SELECT status, metadata FROM tasks WHERE json_valid(metadata) "
                        "AND json_extract(metadata, '$.session') = ? AND json_extract(metadata, '$.goal_id') = ? "
                        "ORDER BY rowid DESC LIMIT 1", (session_id, current["goal_id"]),
                    ).fetchone()
                    if previous:
                        previous_metadata = _loads(previous[1], {})
                        if not previous_metadata.get("session_finalized"):
                            return 0
                        if previous[0] != "ok" and previous_metadata.get("goal_revision") == current["revision"]:
                            conn.execute("UPDATE thread_goals SET status = 'blocked', updated_at = ? WHERE session_id = ?",
                                         (int(time.time()), session_id))
                            changed_goal = thread_goals._read(conn, session_id)
                    if changed_goal is None:
                        metadata = {"source": "mobile_bridge", "platform": "desktop", "session": session_id,
                                    "cwd": workdir, "original_message_text": text, "session_user_message": text,
                                    "history_messages": working_history, "structured_session_history": True,
                                    "goal_id": current["goal_id"], "goal_revision": current["revision"],
                                    "goal_continuation": True}
                        if meta.get("model_override"):
                            metadata["model_override"] = meta["model_override"]
                        if meta.get("profile_generation"):
                            metadata["profile_generation"] = meta["profile_generation"]
                        task = create_task(text=prompt, profile=profile, metadata=metadata, _connection=conn)
                        task_id = str(task["task_id"])
                        created = True
            if changed_goal is not None:
                payload = {"goal": changed_goal}
                seq = self.store.append_event(kind="goal.updated", payload=payload, session_id=session_id)
                self._publish_event(kind="goal.updated", payload=payload, session_id=session_id, task_id=None, seq=seq)
                return 0
            payload = {"task_id": task_id, "session_id": session_id, "text": "", "goal_id": snapshot["goal_id"]}
            seq = self.store.append_event(kind="task.submitted", payload=payload, session_id=session_id,
                                          task_id=task_id, src_line=-4)
            if seq is not None:
                self._publish_event(kind="task.submitted", payload=payload, session_id=session_id, task_id=task_id, seq=seq)
            return int(created)

    def session_processes(self, session_id: str) -> list[dict]:
        self._session_workdir(session_id)
        from ..running_commands import list_commands
        return list_commands(session_id)

    def session_sources(self, session_id: str) -> dict:
        self._session_workdir(session_id)
        links, truncated = self._source_index.links(session_id)
        files = [{**info, "kind": "file"} for info in self.list_session_files(session_id)]
        return {"sources": files + links, "truncated": truncated,
                "files_partial": bool(self.session_changes(session_id).get("tracking_partial"))}

    def stop_session_process(self, session_id: str, identity: str) -> dict:
        self._session_workdir(session_id)
        from ..running_commands import request_stop
        if not request_stop(session_id, identity):
            raise MobileNotFound("Процесс уже завершён или недоступен в этой сессии")
        return {"id": identity, "requested": True}

    def get_task(self, task_id: str) -> dict[str, Any]:
        task = get_task(task_id)
        if task is None:
            raise MobileNotFound(f"task not found: {task_id}")
        raw = self._task_row(task_id)
        task["pending_tool_approvals"] = self._pending_tool_approvals(raw) if raw else []
        task["pending_user_input"] = list_pending_user_input_requests(task_id=task_id)
        task["isolation"] = self._isolation_status_value(str(task.get("profile") or ""))
        return task

    def _session_files(self, session_id: str) -> list:
        record = self._agent_record(session_id)
        if record:
            root = str(record.get("root_session_id") or record.get("session_id"))
            base = Path(self._session_workdir(root)).resolve()
            raw = str(record.get("workdir") or "")
            candidate = Path(raw)
            if raw.startswith("/workspace/"):
                candidate = base / raw.removeprefix("/workspace/")
            candidate = candidate.resolve()
            try:
                relative = candidate.relative_to(base)
            except ValueError:
                return []
            if not relative.parts or not all(part.startswith(".delegate_") for part in relative.parts):
                return []
            found = {}
            for event in self.store.list_events(session_id=session_id, limit=1000):
                payload = event.get("payload") or {}
                tool_input = payload.get("tool_input") or {}
                explicit = []
                if payload.get("tool_name") == "Write" and isinstance(tool_input, dict):
                    target = str(tool_input.get("file_path") or "")
                    if target.startswith("/workspace/"):
                        target = str(base / target.removeprefix("/workspace/"))
                    if target:
                        explicit.append(target)
                text = str(payload.get("text") or payload.get("context_output") or "")
                for info, path in collect_files(candidate, text, explicit, visible_base=True):
                    found[str(path)] = (info, path)
            for info, path in collect_files(candidate, str(record.get("summary") or ""), [], visible_base=True):
                found[str(path)] = (info, path)
            return list(found.values())[-100:]
        path = _session_path(session_id)
        if not path.exists():
            raise MobileNotFound("Сессия не найдена")
        meta = _read_session_meta(path) or {}
        base = Path(meta.get("cwd") or self.workdir).expanduser().resolve()
        found = {}
        for entry in _load_entries(path):
            if entry.get("role") != "assistant":
                continue
            task_id = str(entry.get("workspace_task_id") or entry.get("task_id") or "")
            for info, candidate in collect_files(base, str(entry.get("text") or ""), entry.get("output_files") or [], task_id):
                found[str(candidate)] = (info, candidate)
        for task in self._tasks_by_session().get(session_id, []):
            if task["status"] not in FINAL_STATUSES:
                continue
            metadata = task.get("metadata") or {}
            workdir = Path(metadata.get("cwd") or base).expanduser().resolve()
            workspace_id = str(metadata.get("workspace_task_id") or task["task_id"])
            full_task = get_task(task["task_id"]) or {}
            explicit = full_task.get("workspace_files") or []
            if isinstance(explicit, list):
                from ..cli_tasks import resolve_workspace_file_paths
                explicit = resolve_workspace_file_paths(workspace_id, [str(item) for item in explicit])
            else:
                explicit = []
            for info, candidate in collect_files(workdir, task.get("output_text") or "", explicit, workspace_id):
                found[str(candidate)] = (info, candidate)
        for info, candidate in collect_files(base, "", self._source_index.file_paths(session_id), visible_base=True):
            info["path"] = candidate.relative_to(base).as_posix()
            found[str(candidate)] = (info, candidate)
        for changed in self.session_changes(session_id)["files"]:
            relative = str(changed.get("path") or "")
            if not relative or Path(relative).is_absolute():
                continue
            for info, candidate in collect_files(base, "", [relative], visible_base=True):
                info["path"] = candidate.relative_to(base).as_posix()
                found[str(candidate)] = (info, candidate)
        return list(found.values())[-100:]

    def list_session_files(self, session_id: str) -> list[dict]:
        return [info for info, _ in self._session_files(session_id)]

    def open_session_file(self, session_id: str, file_id: str):
        for info, path in self._session_files(session_id):
            if info["id"] == file_id:
                try:
                    return info, open_file_safely(path, info)
                except OSError as exc:
                    raise MobileConflict("Файл изменился или недоступен. Обновите вложения.") from exc
        raise MobileNotFound("Файл недоступен или изменился. Обновите вложения.")

    def open_session_upload(self, session_id: str, upload_id: str):
        cwd = self._session_workdir(session_id)
        attachments = resolve_session_uploads(cwd, session_id, [upload_id])
        if not attachments:
            raise MobileNotFound("Вложение недоступно")
        attachment = attachments[0]
        try:
            path = session_upload_dir(Path(cwd).resolve(), session_id) / (
                upload_id + Path(attachment["name"]).suffix
            )
            if path != Path(attachment["path"]):
                raise MobileNotFound("Вложение недоступно")
            info = describe_file(path)
            if info["size"] > MAX_UPLOAD_BYTES:
                raise MobileNotFound("Вложение слишком большое")
            info["name"] = attachment["name"]
            mime = attachment["mime_type"]
            info["mime_type"] = mime if mime in {
                "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/avif"
            } else "application/octet-stream"
            return info, open_file_safely(path, info)
        except OSError as exc:
            raise MobileNotFound("Вложение недоступно") from exc

    def _task_row(self, task_id: str) -> dict[str, Any] | None:
        with open_db(get_db_path()) as conn:
            row = conn.execute(
                "SELECT task_id, parent_id, status, profile, input_text, output_text, metadata, "
                "approval_blocked_tools, created_at, updated_at, started_at, completed_at, error_message "
                "FROM tasks WHERE task_id = ?",
                (str(task_id or ""),),
            ).fetchone()
        if row is None:
            return None
        return {
            "task_id": row[0],
            "parent_id": row[1],
            "status": row[2],
            "profile": row[3],
            "input_text": row[4] or "",
            "output_text": row[5] or "",
            "metadata": _loads(row[6], {}),
            "approval_blocked_tools": _loads(row[7], []),
            "created_at": int(row[8] or 0),
            "updated_at": int(row[9] or 0),
            "started_at": row[10],
            "completed_at": row[11],
            "error_message": row[12] or "",
        }

    def _blocked_tasks(self, *, session_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Pending tasks. Filter by JSON — performed in SQL, not in Python."""
        query = (
            "WITH task_sessions AS ("
            "  SELECT tasks.*, rowid AS task_rowid, "
            "    CASE WHEN json_valid(metadata) "
            "      THEN NULLIF(CAST(json_extract(metadata, '$.session') AS TEXT), '') "
            "      ELSE NULL END AS session_key "
            "  FROM tasks"
            "), ranked AS ("
            "  SELECT task_sessions.*, "
            "    ROW_NUMBER() OVER ("
            "      PARTITION BY COALESCE(session_key, task_id) "
            "      ORDER BY created_at DESC, task_rowid DESC"
            "    ) AS session_rank "
            "  FROM task_sessions"
            ") "
            "SELECT task_id, parent_id, status, profile, input_text, output_text, metadata, "
            "approval_blocked_tools, created_at, updated_at, started_at, completed_at, error_message "
            "FROM ranked "
            "WHERE session_rank = 1 "
            + _FOREIGN_APPROVAL_FILTER_SQL
            + "  AND (status = 'waiting_approval' "
            "    OR (status = 'fail' AND COALESCE(approval_blocked_tools, '[]') NOT IN ('', '[]'))) "
        )
        params: list[Any] = []
        if session_id:
            query += " AND session_key = ?"
            params.append(str(session_id))
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with open_db(get_db_path()) as conn:
            rows = conn.execute(query, params).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            task = {
                "task_id": row[0],
                "status": row[2],
                "profile": row[3],
                "input_text": row[4] or "",
                "output_text": row[5] or "",
                "metadata": _loads(row[6], {}),
                "approval_blocked_tools": _loads(row[7], []),
                "created_at": int(row[8] or 0),
                "updated_at": int(row[9] or 0),
                "error_message": row[12] or "",
            }
            tasks.append(task)
        return tasks

    def _all_blocked_mobile_tasks(self) -> list[dict[str, Any]]:
        with open_db(get_db_path()) as conn:
            rows = conn.execute(
                "SELECT task_id FROM tasks WHERE status IN ('fail', 'waiting_approval') "
                "AND COALESCE(approval_blocked_tools, '[]') NOT IN ('', '[]') "
                "AND json_valid(metadata) "
                "AND COALESCE(json_extract(metadata, '$.source'), '') = 'mobile_bridge' "
                "ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            task = self._task_row(str(row[0] or ""))
            if task is not None:
                tasks.append(task)
        return tasks

    def save_upload(
        self,
        session_id: str,
        *,
        filename: str,
        mime_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        """Save the phone attachment to the session working folder."""
        path = _session_path(session_id)
        if not path.exists():
            raise MobileNotFound(f"session not found: {session_id}")
        meta = _read_session_meta(path) or {}
        cwd = str(meta.get("cwd") or self.workdir).strip()
        if not cwd:
            raise MobileInvalid("у сессии не задана рабочая директория")
        base = Path(cwd).expanduser()
        if not base.is_absolute() or not base.is_dir():
            raise MobileInvalid("рабочая директория сессии недоступна")
        try:
            return save_session_upload(
                base,
                session_id,
                filename=filename,
                mime_type=mime_type,
                data=data,
            )
        except (OSError, ValueError) as exc:
            raise MobileInvalid(str(exc)) from exc

    @contextmanager
    def _message_request_lock(self, key: str, *, scope: str = "request"):
        if not key:
            yield
            return
        from ..platform_support import exclusive_lock
        directory = Path(get_db_path()).parent / "mobile-request-locks"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = hashlib.sha256((scope + "\0" + key).encode("utf-8")).hexdigest() + ".lock"
        descriptor = exclusive_lock(directory / name, blocking=True)
        try:
            yield
        finally:
            os.close(descriptor)

    def send_message(self, session_id, text, *, activate_profile=False, **kwargs):
        request_key = str(kwargs.get('idempotency_key') or '').strip()
        with self._approval_lock, self._message_request_lock(str(session_id or ""), scope="session"), self._message_request_lock(request_key):
            from . import demo
            command = str(text or '').strip()
            if command == '/demo':
                if kwargs.get('upload_ids') or kwargs.get('input_files') or kwargs.get('skill_ids'):
                    raise MobileInvalid("Отправьте /demo отдельным сообщением, без вложений и скиллов.")
                return demo.start(self, session_id, key=str(kwargs.get('idempotency_key') or ''),
                                  language=str(kwargs.get('demo_language') or 'ru'))
            demo_state = (_read_session_meta(_session_path(session_id)) or {}).get('demo')
            if isinstance(demo_state, dict) and demo_state.get('status') == 'running':
                raise MobileConflict("Сначала дождитесь демонстрации или остановите её кнопкой «Стоп».")
            kwargs.pop('demo_language', None)
            key = str(kwargs.get('idempotency_key') or '').strip()
            existing = self.store.lookup_request(key) if key else None
            if activate_profile and not (existing and existing.get('task_id')):
                self.get_session(session_id)
                meta = _read_session_meta(_session_path(session_id)) or {}
                profile = str(kwargs.get('profile') or meta.get('profile') or self.profile).strip()
                self.activate_profile(profile)
            return self._send_message(session_id, text, **kwargs)

    def operation_receipt(self, key: str, session_id: str = '') -> dict:
        with self._approval_lock:
            record = self.store.lookup_request(key) or self.store.lookup_request('session:'+key)
            if not record or (session_id and record.get('session_id') != session_id):
                return {'recorded': False, 'status': 'unknown'}
            if record.get('session_id'):
                self.get_session(record['session_id'])
            return {'recorded': True, 'status': 'accepted', 'kind': record['kind'],
                    'session_id': record.get('session_id') or '', 'task_id': record.get('task_id') or '',
                    'receipt': record.get('receipt') or {}}

    def _recover_keyed_steer(self, session_id: str, key: str, *, duplicate: bool = True) -> dict | None:
        recovery = self.store.keyed_steer(key, session_id)
        if recovery is None:
            return None
        task_id, message_id = recovery['task_id'], recovery['message_id']
        payload = recovery['payload']
        path = _session_path(session_id)
        self.get_session(session_id)
        if not any(entry.get('message_id') == message_id for entry in _load_entries(path)):
            extra = {'message_id': message_id, 'steer': True}
            if payload['attachments']:
                extra['attachments'] = payload['attachments']
            if payload['selected_skills']:
                extra['skills'] = payload['selected_skills']
            _append_entry(path, role='user', text=payload['entry_text'], task_id=task_id,
                          status='queued', extra=extra)
        event_payload = {k: v for k, v in payload.items() if k != 'entry_text'}
        seq = self.store.record_keyed_steer_event(key)
        if seq is not None:
            self._publish_event(kind='task.message_added', payload=event_payload,
                                session_id=session_id, task_id=task_id, seq=seq)
        receipt = {'task_id': task_id, 'message_id': message_id, 'status': 'queued', 'steered': True}
        self.store.remember_request(key, kind='message', session_id=session_id, task_id=task_id, receipt=receipt)
        return {**receipt, 'duplicate': duplicate}

    def _recover_keyed_submission(self, session_id: str, key: str) -> dict | None:
        with open_db(get_db_path()) as conn:
            row = conn.execute(
                "SELECT task_id, metadata FROM tasks WHERE json_valid(metadata) "
                "AND json_extract(metadata, '$.idempotency_key') = ? "
                "AND json_extract(metadata, '$.session') = ? "
                "AND json_extract(metadata, '$.submission.message_id') IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1", (key, session_id),
            ).fetchone()
        if row is None:
            return None
        task_id = str(row[0])
        submission = _loads(row[1], {}).get('submission', {})
        message_id = submission['message_id']
        entry_text = submission['text']
        selected_skills = submission['skills']
        visible_attachments = submission['attachments']
        attachment_names = submission['attachment_names']
        path = _session_path(session_id)
        self.get_session(session_id)
        if not any(entry.get('message_id') == message_id for entry in _load_entries(path)):
            extra = {'message_id': message_id}
            if visible_attachments:
                extra['attachments'] = visible_attachments
            if selected_skills:
                extra['skills'] = selected_skills
            _append_entry(path, role='user', text=entry_text, task_id=task_id, status='queued', extra=extra)
        payload = {'task_id': task_id, 'message_id': message_id, 'session_id': session_id,
                   'text': entry_text, 'selected_skills': selected_skills,
                   'attachment_names': attachment_names, 'attachments': visible_attachments}
        self.store.append_event(kind='task.submitted', payload=payload, session_id=session_id,
                                task_id=task_id, src_line=-4)
        receipt = {'task_id': task_id, 'message_id': message_id, 'status': 'queued', 'steered': False}
        self.store.remember_request(key, kind='message', session_id=session_id, task_id=task_id, receipt=receipt)
        return {**receipt, 'duplicate': True}

    def _send_message(
        self,
        session_id: str,
        text: str,
        *,
        idempotency_key: str | None = None,
        cwd: str | None = None,
        profile: str | None = None,
        input_files: list[str] | None = None,
        upload_ids: list[str] | None = None,
        skill_ids: list[str] | None = None,
        plan_mode: bool = False,
        schedule_run: dict | None = None,
        demo_step: int | None = None,
        desktop_ui: bool = False,
    ) -> dict[str, Any]:
        message = str(text or "").strip()
        key = str(idempotency_key or "").strip()
        if key:
            recovered = self._recover_keyed_steer(session_id, key)
            if recovered is not None:
                return recovered
            recovered = self._recover_keyed_submission(session_id, key)
            if recovered is not None:
                return recovered
            existing = self.store.lookup_request(key)
            if existing and existing.get("task_id"):
                return {**existing.get('receipt', {}), "task_id": existing["task_id"], "status": "queued", "duplicate": True}

        path = _session_path(session_id)
        meta = _read_session_meta(path) or {}
        effective_profile = str(profile or meta.get("profile") or self.profile).strip()
        self._require_available_profile(effective_profile)
        if not effective_profile:
            raise MobileInvalid("profile is not configured for this session")

        if plan_mode and self._profile_backend(effective_profile) != "cc-backend":
            raise MobileInvalid("plan mode is not supported by this profile")

        selected_skills = self._validate_selected_skills(list(skill_ids or []), effective_profile)

        workdir = str(cwd or meta.get("cwd") or self.workdir)
        demo_state = meta.get("demo")
        if isinstance(demo_state, dict):
            workdir = str(meta["cwd"])
        attachments = resolve_session_uploads(workdir, session_id, upload_ids)
        if not message and not attachments:
            raise MobileInvalid("text is required")
        activation = "\n".join(f"/{skill_id}" for skill_id in selected_skills)
        task_message = "\n\n".join(part for part in (activation, message) if part).strip()
        if attachments:
            task_message = f"{task_message}\n\n{format_attachments_block(attachments)}".strip()

        entries = _load_entries(path)
        history = entries
        working_history = self._working_history(session_id)
        prompt = _build_prompt(history, task_message, foundation_context=self._foundation_context())

        metadata: dict[str, Any] = {
            "source": "mobile_bridge",
            "platform": "android",
            "created_via": "mobile_bridge.send_message",
            "session": session_id,
            "cwd": workdir,
            "original_message_text": message,
            "selected_skills": selected_skills,
            "history_messages": working_history,
            "structured_session_history": True,
            "session_user_message": task_message,
        }
        goal = get_goal(session_id)
        if goal and goal["status"] == "active" and goal["surface"] == "mobile_bridge":
            metadata.update(goal_id=goal["goal_id"], goal_revision=goal["revision"])
        if meta.get('profile_generation'):
            metadata['profile_generation'] = meta['profile_generation']
        if effective_profile == meta.get('profile') and meta.get('model_override'):
            metadata['model_override'] = meta['model_override']
        if desktop_ui:
            metadata['desktop_ui'] = True
            metadata['platform'] = 'desktop'
        if isinstance(demo_state, dict):
            metadata['demo_workspace'] = True
            from .demo import SAFE_TOOLS
            metadata['restrict_tools_to'] = sorted(SAFE_TOOLS)
        if demo_step is not None:
            from .demo import LABELS, TOOLS
            metadata['demo_run'] = True
            metadata['auto_approve'] = True
            metadata['demo_step'] = demo_step
            metadata['restrict_tools_to'] = [TOOLS[demo_step]] if TOOLS[demo_step] else ['Read', 'Glob']
            metadata['history_messages'] = []
        if schedule_run is not None:
            metadata["schedule_run"] = dict(schedule_run)
        session_rules = self._approved_session_rules_for_session(session_id)
        if session_rules:
            metadata["approved_session_rules"] = session_rules
        if plan_mode:
            metadata["plan_mode"] = True
        if key:
            metadata["idempotency_key"] = key
        if attachments:
            metadata["attachments"] = attachments

        current_input_files = list(input_files or []) + [
            str(item["path"]) for item in attachments
        ]
        entry_text = message
        if demo_step is not None:
            entry_text = f"{demo_step + 1}/{len(TOOLS)} · {LABELS[demo_state['language']][demo_step]}"
        if not entry_text and attachments:
            entry_text = "[вложения: " + ", ".join(str(item["name"]) for item in attachments) + "]"
        attachment_names = [str(item["name"]) for item in attachments]
        visible_attachments = public_attachments(attachments)
        message_id = str(uuid.uuid4())

        # Match desktop chat: queue messages on the active session task and let
        # its executor consume them without allocating a new task ID.
        active_task_id = self._active_task_id_for_session(session_id)
        if active_task_id:
            active_task = get_task(active_task_id) or {}
            active_metadata = active_task.get("metadata") if isinstance(active_task.get("metadata"), dict) else {}
            if plan_mode or active_metadata.get("plan_mode") is True:
                raise MobileConflict("wait for the current task before changing plan mode")
            if key:
                payload = {'task_id': active_task_id, 'message_id': message_id,
                           'session_id': session_id, 'text': message, 'entry_text': entry_text,
                           'selected_skills': selected_skills, 'attachment_names': attachment_names,
                           'attachments': visible_attachments}
                self.store.enqueue_keyed_steer(key, session_id, active_task_id, message_id,
                                               task_message, current_input_files, selected_skills, payload)
                return self._recover_keyed_steer(session_id, key, duplicate=False)
            entry_extra: dict[str, Any] = {"message_id": message_id, "steer": True}
            if visible_attachments:
                entry_extra["attachments"] = visible_attachments
            if selected_skills:
                entry_extra["skills"] = selected_skills
            enqueue_steer(
                session_id=session_id,
                task_id=active_task_id,
                text=task_message,
                message_id=message_id,
                input_files=current_input_files,
                selected_skills=selected_skills,
            )
            _append_entry(
                path,
                role="user",
                text=entry_text,
                task_id=active_task_id,
                status="queued",
                extra=entry_extra,
            )
            if key:
                self.store.remember_request(
                    key, kind="message", session_id=session_id, task_id=active_task_id,
                    receipt={'task_id':active_task_id,'message_id':message_id,'status':'queued','steered':True}
                )
            self.store.append_event(
                kind="task.message_added",
                payload={
                    "task_id": active_task_id,
                    "message_id": message_id,
                    "session_id": session_id,
                    "text": message,
                    "selected_skills": selected_skills,
                    "attachment_names": attachment_names,
                    "attachments": visible_attachments,
                    "pending": pending_steer_count(active_task_id),
                },
                session_id=session_id,
                task_id=active_task_id,
            )
            self._publish_event(
                kind="task.message_added",
                payload={
                    "task_id": active_task_id,
                    "message_id": message_id,
                    "session_id": session_id,
                    "text": message,
                    "selected_skills": selected_skills,
                    "attachments": visible_attachments,
                },
                session_id=session_id,
                task_id=active_task_id,
            )
            return {
                "task_id": active_task_id,
                "status": "queued",
                "duplicate": False,
                "steered": True,
                "message_id": message_id,
            }

        resolved_input_files = list(
            dict.fromkeys(
                _historical_session_images(
                    history, session_id=session_id, workdir=workdir
                )
                + current_input_files
            )
        )
        if key:
            metadata['submission'] = {'message_id': message_id, 'text': entry_text,
                                      'skills': selected_skills, 'attachments': visible_attachments,
                                      'attachment_names': attachment_names}
        created = create_task(
            text=prompt,
            profile=effective_profile,
            metadata=metadata,
            input_files=resolved_input_files,
        )
        task_id = str(created["task_id"])
        task_id_extra: dict[str, Any] = {"message_id": message_id}
        if visible_attachments:
            task_id_extra["attachments"] = visible_attachments
        if selected_skills:
            task_id_extra["skills"] = selected_skills
        # Skills are a structured field; the journal text must stay the plain message.
        _append_entry(
            path,
            role="user",
            text=entry_text,
            task_id=task_id,
            status="queued",
            extra=task_id_extra,
        )
        self.store.append_event(
            kind="task.submitted",
            payload={
                "task_id": task_id,
                "message_id": message_id,
                "session_id": session_id,
                "text": entry_text,
                "selected_skills": selected_skills,
                "attachment_names": attachment_names,
                "attachments": visible_attachments,
            },
            session_id=session_id,
            task_id=task_id,
            src_line=-4 if key else None,
        )
        if key:
            self.store.remember_request(key, kind="message", session_id=session_id, task_id=task_id,
                receipt={'task_id':task_id,'message_id':message_id,'status':'queued','steered':False})
        return {
            "task_id": task_id,
            "status": "queued",
            "duplicate": False,
            "steered": False,
            "message_id": message_id,
        }

    def send_message_now(self, session_id: str, task_id: str, message_id: str) -> dict[str, Any]:
        session = str(session_id or "").strip()
        task = str(task_id or "").strip()
        message = str(message_id or "").strip()
        if not session or not task or not message:
            raise MobileInvalid("session_id, task_id and message_id are required")
        row = self._task_row(task)
        if row is None or self._session_of(row) != session:
            raise MobileNotFound("queued message not found in this session")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if str(metadata.get("source") or "") != "mobile_bridge":
            raise MobileConflict("message does not belong to a mobile task")
        if str(row.get("status") or "") not in ACTIVE_STATUSES:
            raise MobileConflict("task is no longer active")
        if not prioritize_steer(task, message, session):
            raise MobileConflict("message is no longer waiting in the task queue")
        self.store.append_event(
            kind="task.message_prioritized",
            payload={
                "task_id": task,
                "message_id": message,
                "session_id": session,
                "pending": pending_steer_count(task),
            },
            session_id=session,
            task_id=task,
        )
        self._publish_event(
            kind="task.message_prioritized",
            payload={"task_id": task, "message_id": message, "session_id": session},
            session_id=session,
            task_id=task,
        )
        return {"task_id": task, "message_id": message, "prioritized": True}

    def cancel_message(self, session_id: str, task_id: str, message_id: str) -> dict[str, Any]:
        """Cancel one queued session message of a running task.

        Only a message still waiting in the steer queue can be removed. A
        message the running task already consumed is reported as ``delivered``
        instead of being silently "cancelled" — a delivered turn is part of the
        task history and cannot be retracted.
        """
        session = str(session_id or "").strip()
        task = str(task_id or "").strip()
        message = str(message_id or "").strip()
        if not session or not task or not message:
            raise MobileInvalid("session_id, task_id and message_id are required")
        row = self._task_row(task)
        if row is None or self._session_of(row) != session:
            raise MobileNotFound("queued message not found in this session")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if str(metadata.get("source") or "") != "mobile_bridge":
            raise MobileConflict("message does not belong to a mobile task")
        if not cancel_steer(task, message, session):
            pending = pending_steer_count(task)
            return {
                "task_id": task,
                "message_id": message,
                "cancelled": False,
                "delivered": True,
                "reason": "message_already_delivered",
                "pending": pending,
            }
        payload = {
            "task_id": task,
            "message_id": message,
            "session_id": session,
            "pending": pending_steer_count(task),
        }
        self.store.append_event(
            kind="task.message_cancelled",
            payload=payload,
            session_id=session,
            task_id=task,
        )
        self._publish_event(
            kind="task.message_cancelled",
            payload=payload,
            session_id=session,
            task_id=task,
        )
        return {
            "task_id": task,
            "message_id": message,
            "cancelled": True,
            "delivered": False,
            "pending": payload["pending"],
        }

    def finalize_turns(self) -> int:
        with self._approval_lock:
            return self._finalize_turns()

    def _account_goal_turn(self, task: dict, metadata: dict) -> None:
        from .. import thread_goals

        goal_id = metadata.get("goal_id")
        session_id = str(metadata.get("session") or "")
        if not isinstance(goal_id, str) or not goal_id or not session_id:
            return
        task_id = str(task.get("task_id") or "")
        with open_db(get_db_path()) as conn:
            row = conn.execute(
                "SELECT usage_prompt, usage_completion, started_at, completed_at FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return
        tokens = max(0, int(row[0] or 0)) + max(0, int(row[1] or 0))
        seconds = max(0, int(row[3] or 0) - int(row[2] or 0)) if row[2] and row[3] else 0
        turn_id = task_id + ":" + str(metadata.get("session_turn_id") or "initial")
        try:
            status = str(task.get("status") or "")
            goal = thread_goals.account_usage(
                session_id, goal_id, turn_id, tokens=tokens, seconds=seconds,
                empty=status == "ok" and not str(task.get("output_text") or "").strip(),
                failed=status != "ok", revision=metadata.get("goal_revision"),
            )
        except thread_goals.GoalConflict:
            return
        line = -100 - int.from_bytes(hashlib.sha256(turn_id.encode()).digest()[:7], "big")
        payload = {"goal": goal}
        seq = self.store.append_event(kind="goal.updated", payload=payload, session_id=session_id,
                                      task_id=task_id, src_line=line)
        if seq is not None:
            self._publish_event(kind="goal.updated", payload=payload, session_id=session_id, task_id=task_id, seq=seq)

    def _finalize_turns(self) -> int:
        """Review memory changes and append completed mobile turns to sessions."""
        finalized = 0
        for task in self._scan_tasks():
            with self._message_request_lock(str(task.get("task_id") or ""), scope="finalize"):
                task = self._task_row(str(task.get("task_id") or "")) or task
                metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                if str(metadata.get("source") or "") != "mobile_bridge":
                    continue
                if metadata.get("session_finalized"):
                    if str(task.get("status") or "") in FINAL_STATUSES:
                        self._account_goal_turn(task, metadata)
                    if metadata.get('demo_run'):
                        from .demo import complete
                        complete(self, task)
                    continue
                status = str(task.get("status") or "")
                if status not in FINAL_STATUSES:
                    continue
                if task.get("approval_blocked_tools"):
                    continue
                session_id = self._session_of(task)
                if not session_id:
                    continue

                path = _session_path(session_id)
                turn_id = str(metadata.get("session_turn_id") or "initial")
                written_entry = next((
                    entry for entry in _load_entries(path)
                    if entry.get("role") == "assistant"
                    and str(entry.get("task_id") or "") == str(task["task_id"])
                    and entry.get("mobile_turn_id") == turn_id
                ), None) if path.exists() else None
                if written_entry is not None:
                    files = written_entry.get("files") or []
                    if files:
                        self.store.append_event(kind="message.files", payload={"files": files},
                            session_id=session_id, task_id=str(task["task_id"]), src_line=-3)
                    self._merge_task_metadata(task["task_id"], {"session_finalized": True})
                    self._account_goal_turn(task, metadata)
                    if metadata.get('demo_run'):
                        from .demo import complete
                        complete(self, task)
                    finalized += 1
                    continue

                raw_text = str(task.get("output_text") or task.get("error_message") or "").strip()
                text = raw_text
                memory_writes: list[dict[str, Any]] = []
                if status == "ok" and not metadata.get('demo_workspace'):
                    text, requested_writes = extract_memory_write_blocks(raw_text)
                    text = strip_memory_status_lines(text)
                    if requested_writes:
                        memory_writes = self._memory_manager.review_turn(
                            user_text=str(metadata.get("original_message_text") or ""),
                            assistant_text=text,
                            profile=str(task.get("profile") or self.profile),
                            data_dir=self.data_dir,
                            legacy_raw_text=raw_text,
                            log_fn=lambda message: logger.info("mobile memory review: %s", message),
                        )
                        changed_writes = [
                            item for item in memory_writes
                            if not item.get("rejected") and (item.get("changed") or item.get("saved"))
                        ]
                        memory_message = self._memory_manager.format_memory_update_message(changed_writes)
                        if memory_message:
                            self.store.append_event(
                                kind="model.round_response",
                                payload={"round": 0, "text": f"▸ {memory_message}"},
                                session_id=session_id,
                                task_id=str(task["task_id"]),
                            )

                files = []
                if path.exists() and text:
                    base = Path(metadata.get("cwd") or self.workdir).expanduser().resolve()
                    workspace_id = str(metadata.get("workspace_task_id") or task["task_id"])
                    files = [info for info, _ in collect_files(base, text, [], workspace_id)][:10]
                    _append_entry(
                        path,
                        role="assistant",
                        text=text,
                        task_id=task["task_id"],
                        status=status,
                        extra={"memory_writes": memory_writes, "files": files, "mobile_turn_id": turn_id},
                    )
                if files:
                    self.store.append_event(kind="message.files", payload={"files": files},
                        session_id=session_id, task_id=str(task["task_id"]),
                        src_line=-3)
                self._merge_task_metadata(task["task_id"], {"session_finalized": True})
                self._account_goal_turn(task, metadata)
                if metadata.get('demo_run'):
                    from .demo import complete
                    with self._approval_lock:
                        complete(self, task)
                finalized += 1
        return finalized

    def resume_pending_steers(self) -> int:
        """Requeue tasks that still have undelivered session messages.

        If a task finished while its message was waiting, reuse the same task ID
        with a prompt rebuilt from session history and the undelivered messages.
        If the task row itself is gone (removed by an operator or by the old
        ``cleanup_stuck_tasks`` that did ``DELETE FROM tasks``), the messages are
        delivered as a brand-new task instead of hanging in the queue forever.
        """
        resumed = 0
        for task_id, queued_items in pending_steer_sessions().items():
            with self._message_request_lock(task_id, scope="finalize"):
                row = self._task_row(task_id)
                if not row:
                    # The task has disappeared from the `tasks` list, but its steer messages remain.
                    # Delivery is no one's responsibility: `finalize_turns` and the main branch below
                    # only operate on task strings. Without this branch, messages would hang in the queue forever.
                    if queued_items and self._deliver_orphan_steers(task_id, queued_items):
                        resumed += 1
                    continue
                status = str(row.get("status") or "")
                if status in ACTIVE_STATUSES:
                    # The active executor will consume these messages itself.
                    continue
                if status not in FINAL_STATUSES:
                    continue
                # _task_row normally returns decoded metadata.
                metadata = row.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = _loads(row.get("metadata"), {})
                if str(metadata.get("source") or "") != "mobile_bridge":
                    continue
                session_id = str(metadata.get("session") or "")
                if not session_id:
                    continue
                path = _session_path(session_id)
                if not path.exists():
                    continue
                if metadata.get("infra_failure") is True:
                    # Infrastructure loss keeps its terminal error visible; the
                    # pending message runs as a new task in the same session.
                    if queued_items and self._deliver_orphan_steers(task_id, queued_items):
                        resumed += 1
                    continue
                if status == "cancelled":
                    # Do not revive a task explicitly cancelled by the user. Journal
                    # entries remain available to the next session turn.
                    drain_steer(task_id)
                    continue
                items = queued_items
                if not items:
                    continue
                undelivered = {
                    str(item.get("message_id") or "")
                    for item in items
                    if str(item.get("message_id") or "")
                }
                entries = _load_entries(path)
                # Exclude queued journal entries from history because they are added
                # below as the new user message.
                history_entries = [
                    entry
                    for entry in entries
                    if not (
                        undelivered
                        and entry.get("steer")
                        and str(entry.get("message_id") or "") in undelivered
                    )
                ]
                history = history_entries
                working_history = self._working_history(session_id, exclude_message_ids=undelivered)
                message_text = "\n\n".join(
                    str(item.get("text") or "").strip()
                    for item in items
                    if str(item.get("text") or "").strip()
                )
                if not message_text:
                    continue
                prompt = _build_prompt(
                    history,
                    message_text,
                    foundation_context=self._foundation_context(),
                )
                metadata["history_messages"] = working_history
                metadata["structured_session_history"] = True
                metadata["session_user_message"] = message_text
                metadata["original_message_text"] = message_text
                metadata["session_finalized"] = False
                metadata["session_turn_id"] = uuid.uuid4().hex
                metadata["pending_steer_resume"] = True
                metadata["pending_steer_ids"] = sorted(undelivered)
                metadata.pop("approved_tool_calls", None)
                now = int(time.time())
                with open_db(get_db_path()) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    current_ids = [row[0] for row in conn.execute(
                        "SELECT id FROM session_steer_messages "
                        "WHERE task_id = ? AND consumed_at IS NULL ORDER BY id", (task_id,)
                    )]
                    if current_ids != [item["id"] for item in items]:
                        continue
                    from ..thread_goals import _read as read_goal

                    current_goal = read_goal(conn, session_id)
                    if current_goal and current_goal["status"] == "active" and current_goal["surface"] == "mobile_bridge":
                        metadata["goal_id"] = current_goal["goal_id"]
                        metadata["goal_revision"] = current_goal["revision"]
                    else:
                        metadata.pop("goal_id", None)
                        metadata.pop("goal_revision", None)
                    cursor = conn.execute(
                        "UPDATE tasks SET input_text = ?, status = 'queued', started_at = NULL, "
                        "completed_at = NULL, error_message = NULL, updated_at = ?, metadata = ? "
                        "WHERE task_id = ? AND status = ?",
                        (prompt, now, json.dumps(metadata, ensure_ascii=False), task_id, status),
                    )
                    if cursor.rowcount <= 0:
                        continue
                    conn.execute(
                        "UPDATE session_steer_messages SET consumed_at = ? "
                        "WHERE task_id = ? AND consumed_at IS NULL", (now, task_id)
                    )
                self.store.append_event(
                    kind="task.requeued",
                    payload={
                        "task_id": task_id,
                        "session_id": session_id,
                        "reason": "pending_steer_messages",
                        "messages": len(items),
                        "message_ids": sorted(undelivered),
                    },
                    session_id=session_id,
                    task_id=task_id,
                )
                resumed += 1
        return resumed

    def _deliver_orphan_steers(
        self, task_id: str, items: list[dict[str, Any]]
    ) -> bool:
        """Deliver steer messages for tasks that no longer exist in `tasks`.

        A new task is created in the same session, and journal entries with the same
        `message_id` are reattached to the new `task_id`; otherwise, the client
        would permanently display them as 'in queue'. The queue is drained only
        after successfully creating the task to prevent message loss in case of failure.
        """
        session_id = next(
            (
                str(item.get("session_id") or "")
                for item in items
                if str(item.get("session_id") or "")
            ),
            "",
        )
        if not session_id:
            return False
        pending_by_id = {int(item["id"]): item for item in items}
        recovered_task_id = ""
        with open_db(get_db_path()) as conn:
            rows = conn.execute(
                "SELECT task_id, metadata FROM tasks WHERE json_valid(metadata) "
                "AND json_extract(metadata, '$.orphan_source_task_id') = ? "
                "AND json_extract(metadata, '$.session') = ? ORDER BY created_at, task_id",
                (task_id, session_id),
            ).fetchall()
        for candidate_id, raw_metadata in rows:
            candidate = _loads(raw_metadata, {})
            saved_ids = candidate.get("orphan_queue_ids") if isinstance(candidate, dict) else None
            if (isinstance(saved_ids, list) and saved_ids
                    and all(isinstance(value, int) and value in pending_by_id for value in saved_ids)):
                recovered_task_id = str(candidate_id)
                items = [pending_by_id[value] for value in saved_ids]
                break
        path = _session_path(session_id)
        if not path.exists():
            return False
        meta = _read_session_meta(path) or {}
        profile = str(meta.get("profile") or self.profile).strip()
        if not profile:
            return False
        workdir = str(meta.get("cwd") or self.workdir)
        message_text = "\n\n".join(
            str(item.get("text") or "").strip()
            for item in items
            if str(item.get("text") or "").strip()
        )
        if not message_text:
            return False
        message_ids = {
            str(item.get("message_id") or "")
            for item in items
            if str(item.get("message_id") or "")
        }
        input_files = [
            str(path_item)
            for item in items
            for path_item in (item.get("input_files") or [])
        ]
        entries = _load_entries(path)
        # Messages already stored in the log as steer messages are excluded from
        # history: below, they will appear in the prompt as a new user message.
        history_entries = [
            entry
            for entry in entries
            if not (
                message_ids
                and entry.get("steer")
                and str(entry.get("message_id") or "") in message_ids
            )
        ]
        history = history_entries
        working_history = self._working_history(session_id, exclude_message_ids=message_ids)
        input_files = list(
            dict.fromkeys(
                _historical_session_images(
                    history, session_id=session_id, workdir=workdir
                )
                + input_files
            )
        )
        prompt = _build_prompt(
            history,
            message_text,
            foundation_context=self._foundation_context(),
        )
        created = None if recovered_task_id else create_task(
            text=prompt,
            profile=profile,
            metadata={
                "source": "mobile_bridge",
                "platform": "android",
                "created_via": "mobile_bridge.resume_pending_steers",
                **({'model_override': meta['model_override']} if meta.get('model_override') else {}),
                "history_messages": working_history,
                "structured_session_history": True,
                "session": session_id,
                "cwd": workdir,
                "original_message_text": message_text,
                "orphan_source_task_id": task_id,
                "orphan_queue_ids": [int(item["id"]) for item in items],
            },
            input_files=input_files,
        )
        new_task_id = recovered_task_id or str(created["task_id"])
        _reassign_journal_task_id(path, message_ids, new_task_id)
        with open_db(get_db_path()) as conn:
            conn.executemany(
                "UPDATE session_steer_messages SET consumed_at = ? "
                "WHERE task_id = ? AND id = ? AND consumed_at IS NULL",
                [(int(time.time()), task_id, int(item["id"])) for item in items],
            )
        self.store.append_event(
            kind="task.requeued",
            payload={
                "task_id": new_task_id,
                "session_id": session_id,
                "reason": "orphan_steer_messages",
                "messages": len(items),
                "message_ids": sorted(message_ids),
            },
            session_id=session_id,
            task_id=new_task_id,
        )
        return True

    def list_approvals(self, *, session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Pending solutions, one per session (the most recent).

        A single task runs per session, so older blocked attempts from the same session are overridden by the new one and do not require a solution.
        """
        record = self._agent_record(session_id) if session_id else None
        agent_filter = session_id if record else None
        if record:
            session_id = str(record.get("root_session_id") or record.get("session_id"))
        approvals: list[dict[str, Any]] = []
        if getattr(self, 'opencode_tasks', None) is not None:
            approvals.extend(self.opencode_tasks.approvals(session_id))
        if session_id is None:
            approvals.extend(pending_requests())
        seen_sessions: set[str] = set()
        for task in self._blocked_tasks(session_id=session_id):
            key = self._session_of(task) or task["task_id"]
            if key in seen_sessions:
                continue
            pending = self._pending_tool_approvals(task)
            if not pending and str(task.get("status")) == "waiting_approval":
                for request in list_pending_user_input_requests(task_id=task["task_id"]):
                    pending.append(self._user_input_approval(task, request))
            if not pending:
                continue
            seen_sessions.add(key)
            approvals.extend(pending)
            if len(approvals) >= max(1, int(limit)):
                break
        with open_db(get_db_path()) as conn:
            input_tasks = conn.execute("SELECT DISTINCT task_id FROM user_input_requests WHERE status = 'pending'").fetchall()
        known = {item.get("request_id") for item in approvals}
        for (input_task_id,) in input_tasks:
            task = self._task_row(input_task_id)
            if task is None or (session_id and self._session_of(task) != session_id):
                continue
            for request in list_pending_user_input_requests(task_id=input_task_id):
                if request["request_id"] not in known:
                    approvals.append(self._user_input_approval(task, request))
                    known.add(request["request_id"])
        if agent_filter:
            approvals = [item for item in approvals if item.get("session_id") == agent_filter]
        return approvals[: max(1, int(limit))]

    def _pending_tool_approvals(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        blocked_list = task.get("approval_blocked_tools")
        if not isinstance(blocked_list, list) or not blocked_list:
            return []
        if str(task.get("status")) not in {"fail", "waiting_approval"}:
            return []
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        resolved_raw = metadata.get("mobile_resolved_approvals")
        resolved = {str(key) for key in resolved_raw} if isinstance(resolved_raw, dict) else set()
        pending: list[dict[str, Any]] = []
        for blocked in blocked_list:
            if not isinstance(blocked, dict):
                continue
            if is_host_op_blocked_record(blocked):
                host_op = self._host_op_approval(task, blocked, resolved)
                if host_op is not None:
                    pending.append(host_op)
                continue
            request_id = str(blocked.get("tool_use_id") or "").strip()
            if not request_id or request_id in resolved:
                continue
            tool = str(blocked.get("tool") or "")
            pending.append(
                {
                    "kind": "tool",
                    "request_id": request_id,
                    "task_id": task["task_id"],
                    "session_id": blocked.get("agent_session_id") or self._session_of(task),
                    "tool": tool,
                    "target": str(blocked.get("target") or ""),
                    "input": blocked.get("input") if isinstance(blocked.get("input"), dict) else {},
                    "signature": str(blocked.get("signature") or ""),
                    "session_rule": blocked.get("session_rule") if isinstance(blocked.get("session_rule"), dict) else None,
                    "reason": str(blocked.get("reason") or ""),
                    "dangerous": tool.lower() in HIGH_RISK_TOOLS,
                    "created_at": int(task.get("updated_at") or 0),
                }
            )
        return pending

    def _host_op_approval(
        self, task: dict[str, Any], blocked: dict[str, Any], resolved: set[str]
    ) -> dict[str, Any] | None:
        """One host-op record as its own approval kind (todo 22).

        The record is parsed host-side; a malformed record is skipped rather than
        mislabelled as a tool approval, and a resolved request disappears exactly
        like a resolved tool call.
        """
        try:
            request = parse_host_op_blocked_record(blocked)
        except HostOpApprovalError as exc:
            logger.warning("mobile bridge: skipping malformed host-op approval (%s)", exc.reason)
            return None
        if request.request_id in resolved:
            return None
        return {
            "kind": HOST_OP_APPROVAL_KIND,
            "request_id": request.request_id,
            "task_id": task["task_id"],
            "session_id": self._session_of(task),
            "tool": request.op,
            "target": request.op,
            "input": dict(request.args),
            "signature": "",
            "session_rule": None,
            "reason": request.reason,
            "dangerous": request.approval_class is HostOpApprovalClass.DANGEROUS_OP,
            "created_at": int(task.get("updated_at") or 0),
            "approval_class": str(request.approval_class),
        }

    def _user_input_approval(self, task: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        server_name = str(context.get("server_name") or "")
        return {
            "kind": "user_input",
            "request_id": str(request.get("request_id") or ""),
            "task_id": task["task_id"],
            "session_id": context.get("agent_session_id") or self._session_of(task),
            "tool": "mcp_access" if server_name else "request_user_input",
            "target": server_name,
            "input": payload,
            "signature": "",
            "session_rule": None,
            "reason": str(context.get("reason") or payload.get("title") or ""),
            "dangerous": bool(server_name),
            "created_at": int(request.get("created_at") or 0),
        }

    def _resolved_approvals(self, task_id: str) -> set[str]:
        task = self._task_row(task_id)
        if task is None:
            return set()
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        resolved = metadata.get("mobile_resolved_approvals")
        if not isinstance(resolved, dict):
            return set()
        return {str(key) for key in resolved}

    def resolve_approval(self, task_id: str, request_id: str, decision: str) -> dict[str, Any]:
        with self._approval_lock:
            return self._resolve_approval(task_id, request_id, decision)

    def _resolve_approval(self, task_id: str, request_id: str, decision: str) -> dict[str, Any]:
        choice = str(decision or "").strip().lower()
        if choice not in {"allow_once", "allow_session", "reject"}:
            raise MobileInvalid("decision must be allow_once, allow_session or reject")
        task = self._task_row(task_id)
        if task is None:
            raise MobileNotFound(f"task not found: {task_id}")

        if str(choice) == "reject" and not self._pending_tool_approvals(task):
            pending_user_input = list_pending_user_input_requests(task_id=task_id)
            if pending_user_input:
                return self.resolve_user_input(task_id, request_id, response=None, cancelled=True)
            raise MobileConflict("approval is no longer pending")

        blocked = self._find_blocked(task, request_id)
        if blocked is None:
            raise MobileConflict("approval is no longer pending")

        if is_host_op_blocked_record(blocked):
            return self._resolve_host_op_approval(task, blocked, request_id, choice)

        if str(choice) == "reject":
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            updated_metadata = dict(metadata)
            resolved = dict(updated_metadata.get("mobile_resolved_approvals") or {})
            resolved[str(request_id)] = "reject"
            updated_metadata["mobile_resolved_approvals"] = resolved
            updated_metadata["last_approval_rejected"] = str(request_id)
            updated_metadata.pop("forced_tool_call", None)
            updated_metadata.pop("approved_tool_calls", None)
            now = int(time.time())
            with open_db(get_db_path()) as conn:
                cursor = conn.execute(
                    "UPDATE tasks SET status = 'fail', metadata = ?, approval_blocked_tools = '[]', "
                    "error_message = 'approval rejected by user', completed_at = ?, updated_at = ? "
                    "WHERE task_id = ? AND status IN ('fail', 'waiting_approval')",
                    (
                        json.dumps(updated_metadata, ensure_ascii=False),
                        now,
                        now,
                        task_id,
                    ),
                )
                if cursor.rowcount <= 0:
                    raise MobileConflict("approval is no longer pending")
            self.store.append_event(
                kind="approval.resolved",
                payload={"task_id": task_id, "request_id": request_id, "decision": "reject"},
                session_id=blocked.get("agent_session_id") or self._session_of(task),
                task_id=task_id,
            )
            if blocked.get("agent_session_id"):
                record = self._agent_record(str(blocked["agent_session_id"]))
                if record:
                    self.store.append_event(kind="subagent.finish", session_id=self._session_of(task), task_id=task_id,
                        payload={"subagent_id": record["subagent_id"], "status": "error", "error": "approval rejected by user"})
            return {"task_id": task_id, "request_id": request_id, "decision": "reject", "resumed_task_id": None}

        signature = str(blocked.get("signature") or "").strip()
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        resume_metadata: dict[str, Any] = dict(metadata)
        resume_metadata.update(
            {
                "source": "mobile_bridge",
                "platform": "android",
                "created_via": "mobile_bridge.resolve_approval",
                "session": self._session_of(task),
                "cwd": str(metadata.get("cwd") or self.workdir),
                "original_message_text": str(metadata.get("original_message_text") or ""),
            }
        )
        approved_calls = [str(item) for item in metadata.get("approved_tool_calls") or []]
        if signature and signature not in approved_calls:
            approved_calls.append(signature)
        resume_metadata["approved_tool_calls"] = approved_calls

        session_rules = list(metadata.get("approved_session_rules") or [])
        if choice == "allow_session":
            rule = _blocked_tool_session_rule(blocked)
            if rule and rule not in session_rules:
                session_rules.append(rule)
        if session_rules:
            resume_metadata["approved_session_rules"] = session_rules

        context_messages = blocked.get("approval_context_messages")
        resume_blocked = blocked
        if isinstance(blocked.get("delegate_resume"), dict):
            resume_blocked = {"tool": "delegate_task", "input": blocked["delegate_resume"],
                              "tool_use_id": "resume-" + request_id}
            context_messages = blocked.get("parent_approval_context_messages")
        forced = _forced_tool_call_for_resume(
            resume_blocked,
            context_messages if isinstance(context_messages, list) else [],
        )
        if forced is not None:
            resume_metadata["forced_tool_call"] = forced

        resolved = dict(resume_metadata.get("mobile_resolved_approvals") or {})
        resolved[str(request_id)] = choice
        resume_metadata["mobile_resolved_approvals"] = resolved
        now = int(time.time())
        with open_db(get_db_path()) as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = 'queued', metadata = ?, approval_blocked_tools = '[]', "
                "started_at = NULL, completed_at = NULL, error_message = NULL, updated_at = ? "
                "WHERE task_id = ? AND status IN ('fail', 'waiting_approval')",
                (json.dumps(resume_metadata, ensure_ascii=False), now, task_id),
            )
            if cursor.rowcount <= 0:
                raise MobileConflict("approval is no longer pending")
        resumed_id = task_id
        self.store.remember_request(
            f"approval:{task_id}:{request_id}",
            kind="approval",
            session_id=self._session_of(task),
            task_id=resumed_id,
        )
        self.store.append_event(
            kind="approval.resolved",
            payload={"task_id": task_id, "request_id": request_id, "decision": choice, "resumed_task_id": resumed_id},
            session_id=blocked.get("agent_session_id") or self._session_of(task),
            task_id=task_id,
        )
        self.store.append_event(
            kind="task.requeued",
            payload={
                "task_id": task_id,
                "session_id": self._session_of(task),
                "reason": "approval_resolved",
                "decision": choice,
            },
            session_id=blocked.get("agent_session_id") or self._session_of(task),
            task_id=task_id,
        )
        return {"task_id": task_id, "request_id": request_id, "decision": choice, "resumed_task_id": resumed_id}

    def _resolve_host_op_approval(
        self, task: dict[str, Any], blocked: dict[str, Any], request_id: str, choice: str
    ) -> dict[str, Any]:
        """Resolve one host-op approval through the broker gate (todo 22).

        An allow executes the typed op exactly once inside the host broker; a
        reject records the refusal on the broker audit trail and performs no host
        action. The task resumes (allow) or ends ``fail`` (reject) without any
        tool-approval metadata - no ``signature``, no ``forced_tool_call``.
        """
        task_id = str(task["task_id"])
        try:
            request = parse_host_op_blocked_record(blocked)
        except HostOpApprovalError as exc:
            raise MobileConflict("approval is no longer pending") from exc
        gate = self._host_op_gate
        if gate is None:
            raise MobileConflict("host-op approvals are unavailable in this runtime")
        try:
            outcome = gate.resolve_request(
                blocked,
                decision=choice,
                resolved_by="mobile_bridge",
                session_id=self._session_of(task),
                task_id=task_id,
            )
        except HostOpApprovalError as exc:
            if exc.code == REASON_ALREADY_RESOLVED:
                raise MobileConflict("approval is no longer pending") from exc
            raise MobileConflict(f"host-op approval failed: {exc.reason}") from exc

        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        updated_metadata = dict(metadata)
        resolved = dict(updated_metadata.get("mobile_resolved_approvals") or {})
        resolved[request.request_id] = choice
        updated_metadata["mobile_resolved_approvals"] = resolved
        now = int(time.time())

        if choice == "reject":
            updated_metadata["last_approval_rejected"] = request.request_id
            with open_db(get_db_path()) as conn:
                cursor = conn.execute(
                    "UPDATE tasks SET status = 'fail', metadata = ?, approval_blocked_tools = '[]', "
                    "error_message = 'approval rejected by user', completed_at = ?, updated_at = ? "
                    "WHERE task_id = ? AND status IN ('fail', 'waiting_approval')",
                    (json.dumps(updated_metadata, ensure_ascii=False), now, now, task_id),
                )
                if cursor.rowcount <= 0:
                    raise MobileConflict("approval is no longer pending")
            self.store.append_event(
                kind="approval.resolved",
                payload={
                    "task_id": task_id,
                    "request_id": request.request_id,
                    "decision": "reject",
                    "kind": HOST_OP_APPROVAL_KIND,
                    "op": request.op,
                    "executed": outcome.executed,
                    "reason": outcome.reason,
                },
                session_id=self._session_of(task),
                task_id=task_id,
            )
            return {
                "task_id": task_id,
                "request_id": request.request_id,
                "decision": "reject",
                "resumed_task_id": None,
                "kind": HOST_OP_APPROVAL_KIND,
                "host_op": outcome.to_record(),
            }

        resume_metadata: dict[str, Any] = dict(updated_metadata)
        resume_metadata.update(
            {
                "source": "mobile_bridge",
                "platform": "android",
                "created_via": "mobile_bridge.resolve_approval",
                "session": self._session_of(task),
                "cwd": str(metadata.get("cwd") or self.workdir),
                "original_message_text": str(metadata.get("original_message_text") or ""),
            }
        )
        resolutions = [
            item
            for item in resume_metadata.get("host_op_resolutions") or []
            if not (isinstance(item, dict) and str(item.get("request_id")) == request.request_id)
        ]
        resolutions.append(outcome.to_record())
        resume_metadata["host_op_resolutions"] = resolutions
        if choice == "allow_session":
            grants = [str(item) for item in resume_metadata.get("host_op_session_grants") or []]
            if request.op not in grants:
                grants.append(request.op)
            resume_metadata["host_op_session_grants"] = grants

        with open_db(get_db_path()) as conn:
            cursor = conn.execute(
                "UPDATE tasks SET status = 'queued', metadata = ?, approval_blocked_tools = '[]', "
                "started_at = NULL, completed_at = NULL, error_message = NULL, updated_at = ? "
                "WHERE task_id = ? AND status IN ('fail', 'waiting_approval')",
                (json.dumps(resume_metadata, ensure_ascii=False), now, task_id),
            )
            if cursor.rowcount <= 0:
                raise MobileConflict("approval is no longer pending")
        self.store.remember_request(
            f"approval:{task_id}:{request.request_id}",
            kind="approval",
            session_id=self._session_of(task),
            task_id=task_id,
        )
        self.store.append_event(
            kind="approval.resolved",
            payload={
                "task_id": task_id,
                "request_id": request.request_id,
                "decision": choice,
                "resumed_task_id": task_id,
                "kind": HOST_OP_APPROVAL_KIND,
                "op": request.op,
                "executed": outcome.executed,
                "reason": outcome.reason,
            },
            session_id=self._session_of(task),
            task_id=task_id,
        )
        self.store.append_event(
            kind="task.requeued",
            payload={
                "task_id": task_id,
                "session_id": self._session_of(task),
                "reason": "approval_resolved",
                "decision": choice,
            },
            session_id=self._session_of(task),
            task_id=task_id,
        )
        return {
            "task_id": task_id,
            "request_id": request.request_id,
            "decision": choice,
            "resumed_task_id": task_id,
            "kind": HOST_OP_APPROVAL_KIND,
            "host_op": outcome.to_record(),
        }

    def approval_settings(self) -> dict[str, Any]:
        return {"enabled": mobile_approvals_enabled()}

    def set_approvals_enabled(self, enabled: bool) -> dict[str, Any]:
        with self._approval_lock:
            state = set_mobile_approvals_enabled(bool(enabled))
            resumed = 0
            if not state:
                for task in self._all_blocked_mobile_tasks():
                    pending = [
                        item
                        for item in self._pending_tool_approvals(task)
                        # Host ops are never auto-approved by disabling mobile
                        # approvals: a privileged host action always needs an
                        # explicit decision (plan todo 22, fail closed).
                        if item.get("kind") != HOST_OP_APPROVAL_KIND
                    ]
                    if not pending:
                        continue
                    request_id = str(pending[0].get("request_id") or "")
                    if not request_id:
                        continue
                    try:
                        self._resolve_approval(task["task_id"], request_id, "allow_once")
                    except MobileConflict:
                        continue
                    resumed += 1
            self.store.append_event(
                kind="approval.settings_changed",
                payload={"enabled": state, "resumed": resumed},
            )
            return {"enabled": state, "resumed": resumed}

    @staticmethod
    def _security_level_payload(resolved: dict[str, Any]) -> dict[str, Any]:
        return {
            "level": str(resolved["level"]),
            "baseline": str(resolved["baseline"]),
            "is_weakened": bool(resolved["is_weakened"]),
            "expires_at": resolved["expires_at"],
            "available_levels": list(security_levels.LEVELS),
        }

    def security_settings(self) -> dict[str, Any]:
        """Current active global level and its boundaries."""
        return self._security_level_payload(security_settings.resolve_security_level())

    def set_security_level(
        self,
        level: Any,
        *,
        ttl_s: Any = None,
        confirm: Any = False,
        actor: str = "",
    ) -> dict[str, Any]:
        """Changes the global level with confirm protection for weakening and auditing.

        Strengthening is applied immediately; weakening is applied only when `confirm is True`
        and requires a mandatory TTL (default 3600 seconds, `full` not exceeding 3600 seconds).
        """
        try:
            normalized = security_levels.normalize_level(level)
        except ValueError as exc:
            raise MobileInvalid(str(exc)) from exc

        current = security_settings.resolve_security_level()
        previous = str(current["level"])
        baseline = str(current["baseline"])
        if security_levels.is_weakened(normalized, baseline) and confirm is not True:
            raise MobileInvalid(
                f"weakening the security level to {normalized!r} requires confirm=true"
            )

        try:
            record = security_settings.set_security_level(normalized, ttl_s=ttl_s)
        except ValueError as exc:
            raise MobileInvalid(str(exc)) from exc

        SecurityStore(get_db_path()).log_level_change(
            normalized,
            previous=previous,
            actor=str(actor or ""),
            expires_at=record.get("expires_at"),
        )
        return self._security_level_payload(security_settings.resolve_security_level())

    def sandbox_settings(self) -> dict[str, Any]:
        """Global `bwrap-sandbox` status (independent of security level)."""
        return dict(sandbox_status())

    def set_sandbox_settings(self, enabled: bool) -> dict[str, Any]:
        set_sandbox_enabled(bool(enabled))
        return dict(sandbox_status())

    def _find_blocked(self, task: dict[str, Any], request_id: str) -> dict[str, Any] | None:
        if str(task.get("status")) not in {"fail", "waiting_approval"}:
            return None
        blocked_list = task.get("approval_blocked_tools")
        if not isinstance(blocked_list, list):
            return None
        resolved = self._resolved_approvals(task["task_id"])
        for blocked in blocked_list:
            if not isinstance(blocked, dict):
                continue
            if is_host_op_blocked_record(blocked):
                candidate = str(blocked.get("request_id") or "").strip()
            else:
                candidate = str(blocked.get("tool_use_id") or "").strip()
            if not candidate or candidate in resolved:
                continue
            if candidate == str(request_id or "").strip():
                return blocked
        return None

    def resolve_user_input(
        self,
        task_id: str,
        request_id: str,
        *,
        response: dict[str, Any] | None,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        if str(task_id).startswith('oc_') and getattr(self, 'opencode_tasks', None) is not None:
            try:
                return self.opencode_tasks.resolve(task_id, request_id, response, cancelled)
            except ValueError as exc:
                raise MobileConflict(str(exc)) from None
        request = get_user_input_request(request_id)
        if request is None or str(request.get("task_id")) != str(task_id):
            raise MobileNotFound("user input request not found for this task")
        if str(request.get("status")) != "pending":
            raise MobileConflict("user input request already resolved")

        payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        server_name = str(context.get("server_name") or "")

        resolve_user_input_request(request_id, response=response, cancelled=cancelled)
        resumed = False
        if not cancelled:
            if server_name:
                from ..mcp_config import accept_mcp_enable_suggestion, mcp_enable_answer_approved

                if mcp_enable_answer_approved(response):
                    accept_mcp_enable_suggestion(self._profile_for_task(task_id), server_name)
            resumed = resume_waiting_approval_task(task_id)
        else:
            now = int(time.time())
            with open_db(get_db_path()) as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'fail', error_message = 'user input rejected by user', "
                    "completed_at = ?, updated_at = ? WHERE task_id = ? AND status = 'waiting_approval'",
                    (now, now, task_id),
                )

        self.store.append_event(
            kind="approval.resolved",
            payload={
                "task_id": task_id,
                "request_id": request_id,
                "decision": "cancel" if cancelled else "answer",
                "server_name": server_name,
            },
            session_id=self._session_of(self._task_row(task_id) or {}),
            task_id=task_id,
        )
        return {"task_id": task_id, "request_id": request_id, "cancelled": bool(cancelled), "resumed": resumed}

    def deliver_opencode_result(self, run: dict) -> bool:
        """Queue one result turn only after the current conversation turn ends."""
        from ..prompt_assembly import opencode_result_prompt
        with self._approval_lock:
            if self._active_task_id_for_session(run['session']):
                return False
            with open_db(get_db_path()) as conn:
                prior = conn.execute("SELECT task_id FROM tasks WHERE json_valid(metadata) AND json_extract(metadata, '$.opencode_result_for') = ? LIMIT 1", (run['id'],)).fetchone()
            if prior:
                return True
            self._require_available_profile(run['profile'])
            message = opencode_result_prompt(run['goal'], run['result'])
            created = create_task(text=message, profile=run['profile'], metadata={
                'source': 'mobile_bridge', 'platform': 'desktop', 'desktop_ui': True,
                'created_via': 'opencode.result', 'session': run['session'], 'cwd': run['source'],
                'original_message_text': run['goal'], 'structured_session_history': True,
                'history_messages': self._working_history(run['session']), 'session_user_message': message,
                'opencode_result_for': run['id'], 'restrict_tools_to': ['Read', 'Grep', 'Glob'],
            })
            self.store.append_event(kind='task.submitted', session_id=run['session'], task_id=created['task_id'],
                                    payload={'task_id': created['task_id'], 'text': ''})
            return True

    def _profile_for_task(self, task_id: str) -> str:
        task = self._task_row(task_id)
        return str((task or {}).get("profile") or self.profile)

    def cancel_task(
        self,
        task_id: str,
        *,
        mode: str = "cancel",
        _emit_events: bool = True,
    ) -> dict[str, Any]:
        choice = str(mode or "cancel").strip().lower()
        if choice not in {"cancel", "stop", "kill"}:
            raise MobileInvalid("mode must be cancel, stop or kill")
        task = self._task_row(task_id)
        if task is None:
            raise MobileNotFound(f"task not found: {task_id}")

        previous_status = str(task.get("status") or "")
        session_id = self._session_of(task)
        if previous_status not in ACTIVE_STATUSES:
            return {
                "task_id": task_id,
                "mode": choice,
                "cancelled": False,
                "status": previous_status,
                "terminal": previous_status in FINAL_STATUSES,
                "process_kill_supported": False,
            }

        cancelled = cancel_task_row(task_id)
        if cancelled:
            logger.warning(
                "mobile_bridge.cancel_requested task_id=%s session_id=%s "
                "source=task_cancel mode=%s previous_status=%s",
                task_id,
                session_id or "(none)",
                choice,
                previous_status,
            )
        if choice in {"stop", "kill"}:
            cancelled = self._force_status(
                task_id,
                "dead_letter",
                f"cancelled by user (mobile {choice})",
            ) or cancelled
        updated = self._task_row(task_id) or task
        status = str(updated.get("status") or previous_status)
        terminal = status in FINAL_STATUSES
        if _emit_events:
            self.store.append_event(
                kind="task.cancel_requested",
                payload={"task_id": task_id, "mode": choice, "previous_status": previous_status},
                session_id=session_id,
                task_id=task_id,
            )
            if terminal:
                self.store.append_event(
                    kind="task.cancelled",
                    payload={
                        "task_id": task_id,
                        "mode": choice,
                        "status": status,
                        "previous_status": previous_status,
                    },
                    session_id=session_id,
                    task_id=task_id,
                )
        return {
            "task_id": task_id,
            "mode": choice,
            "cancelled": bool(cancelled),
            "status": status,
            "terminal": terminal,
            "process_kill_supported": False,
        }

    def cancel_session(self, session_id: str, *, mode: str = "cancel") -> dict[str, Any]:
        choice = str(mode or "cancel").strip().lower()
        if choice not in {"cancel", "stop", "kill"}:
            raise MobileInvalid("mode must be cancel, stop or kill")
        record = self._agent_record(session_id)
        if record:
            return self.cancel_subagent(str(record.get("root_session_id") or record.get("session_id")), record["subagent_id"])
        path = _session_path(session_id)
        if not path.exists():
            raise MobileNotFound(f"session not found: {session_id}")
        from .demo import stop
        with self._approval_lock:
            stop(self, session_id)
        placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
        with open_db(get_db_path()) as conn:
            rows = conn.execute(
                "SELECT task_id FROM tasks "
                f"WHERE status IN ({placeholders}) "
                "AND json_valid(metadata) "
                "AND CAST(json_extract(metadata, '$.session') AS TEXT) = ? "
                "ORDER BY created_at DESC, rowid DESC",
                (*tuple(sorted(ACTIVE_STATUSES)), str(session_id)),
            ).fetchall()
        results = [
            self.cancel_task(str(row[0]), mode=choice, _emit_events=False)
            for row in rows
        ]
        # Parent termination must stop its active children: otherwise,
        # the child process continues to consume budget after the task is marked as cancelled.
        subagent_results: list[dict[str, Any]] = []
        for record in self._active_subagent_ids(session_id):
            subagent_id = str(record.get("subagent_id") or "")
            if not subagent_id:
                continue
            try:
                subagent_results.append(
                    self.cancel_subagent(session_id, subagent_id)
                )
            except (MobileNotFound, MobileInvalid):
                continue
        terminal_count = sum(1 for item in results if item.get("terminal"))
        if results:
            logger.warning(
                "mobile_bridge.cancel_requested session_id=%s source=session_cancel "
                "mode=%s affected=%s",
                session_id,
                choice,
                len(results),
            )
            self.store.append_event(
                kind="session.cancel_requested",
                payload={"session_id": session_id, "mode": choice, "affected": len(results)},
                session_id=session_id,
            )
            if terminal_count == len(results):
                self.store.append_event(
                    kind="session.cancelled",
                    payload={
                        "session_id": session_id,
                        "mode": choice,
                        "affected": len(results),
                    },
                    session_id=session_id,
                )
        return {
            "session_id": session_id,
            "mode": choice,
            "affected": len(results),
            "terminal": terminal_count,
            "tasks": results,
            "subagents": subagent_results,
        }

    def _force_status(self, task_id: str, status: str, error_message: str) -> bool:
        now = int(time.time())
        with open_db(get_db_path()) as conn:
            placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
            cursor = conn.execute(
                "UPDATE tasks SET status = ?, error_message = ?, completed_at = ?, updated_at = ? "
                f"WHERE task_id = ? AND status IN ({placeholders})",
                (status, error_message, now, now, str(task_id), *tuple(sorted(ACTIVE_STATUSES))),
            )
            return cursor.rowcount > 0

    def _mark_approval_resolved(self, task_id: str, request_id: str, decision: str) -> None:
        task = self._task_row(task_id)
        metadata = (task or {}).get("metadata") if isinstance((task or {}).get("metadata"), dict) else {}
        resolved = dict(metadata.get("mobile_resolved_approvals") or {})
        resolved[str(request_id)] = str(decision)
        self._merge_task_metadata(task_id, {"mobile_resolved_approvals": resolved})

    def _merge_task_metadata(self, task_id: str, updates: dict[str, Any]) -> None:
        now = int(time.time())
        with open_db(get_db_path()) as conn:
            row = conn.execute("SELECT metadata FROM tasks WHERE task_id = ?", (str(task_id),)).fetchone()
            metadata = _loads(row[0], {}) if row else {}
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.update(updates)
            conn.execute(
                "UPDATE tasks SET metadata = ?, updated_at = ? WHERE task_id = ?",
                (json.dumps(metadata, ensure_ascii=False), now, str(task_id)),
            )
