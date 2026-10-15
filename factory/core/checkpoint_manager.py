from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .durable_root_scope import durable_root_within_workdir
from .runtime import get_data_root


logger = logging.getLogger(__name__)

CHECKPOINTS_DIRNAME = "checkpoints"
DEFAULT_MAX_SNAPSHOTS = 50
MAX_FILES = 50_000
GIT_TIMEOUT_S = 30
MAX_CHECKPOINT_PREVIEW_BYTES = 2_000_000
_FULL_REVISION_RE = re.compile(r"[0-9a-f]{40}")


def _checkpoint_text(spec: str, *, root: Path, env: dict) -> tuple[bool, str | None]:
    """Pin a private shadow blob and check its size before requesting its text."""
    options = dict(capture_output=True, text=True, encoding='utf-8', errors='replace',
                   timeout=GIT_TIMEOUT_S, cwd=str(root), env=env)
    try:
        git = _git_executable()
        if git is None:
            return False, None
        resolved = subprocess.run([git, 'rev-parse', '--verify', spec], **options)
        if resolved.returncode:
            return True, None
        oid = resolved.stdout.strip()
        if not _FULL_REVISION_RE.fullmatch(oid):
            return False, None
        measured = subprocess.run([git, 'cat-file', '-s', oid], **options)
        if measured.returncode or not 0 <= int(measured.stdout.strip()) <= MAX_CHECKPOINT_PREVIEW_BYTES:
            return False, None
        shown = subprocess.run([git, 'cat-file', 'blob', oid], **options)
        if shown.returncode or len(shown.stdout.encode('utf-8')) > MAX_CHECKPOINT_PREVIEW_BYTES:
            return False, None
        return True, shown.stdout
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, None

DEFAULT_EXCLUDES = (
    "node_modules/",
    "dist/",
    "build/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".pytest_cache/",
    ".venv/",
    "venv/",
    ".git/",
    ".tool_results/",
    ".checkpoints/",
    "*.log",
)
BASENAME_COMMIT_MESSAGE = "checkpoint: baseline"


def _checkpoint_base(data_root: str | None = None) -> Path:
    base = Path(data_root).expanduser() if data_root else Path(get_data_root())
    base = base / CHECKPOINTS_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def _shadow_repo_path(working_dir: str, data_root: str | None = None) -> Path:
    abs_path = str(Path(working_dir).resolve())
    dir_hash = hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:16]
    return _checkpoint_base(data_root) / dir_hash


def _git_env(shadow_repo: Path, working_dir: str) -> dict[str, str]:
    from .readonly_git import git_environment
    env = git_environment()
    env["GIT_DIR"] = str(shadow_repo)
    env["GIT_WORK_TREE"] = str(Path(working_dir).resolve())
    return env


def _git_executable() -> str | None:
    if os.name != 'nt':
        return 'git' if shutil.which('git') else None
    # Resolve only explicit owner PATH entries, never the writable working folder.
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        if directory and Path(directory).is_absolute():
            candidate = Path(directory) / 'git.exe'
            if candidate.is_file() and not candidate.is_symlink():
                return str(candidate.resolve())
    return None


def _run_git(
    args: list[str],
    *,
    shadow_repo: Path,
    working_dir: str,
    allowed_returncodes: set[int] | None = None,
) -> tuple[bool, str, str, int]:
    git = _git_executable()
    if git is None:
        return False, '', 'git not found', -1
    cmd = [git, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            cwd=str(Path(working_dir).resolve()),
            env=_git_env(shadow_repo, working_dir),
        )
    except subprocess.TimeoutExpired:
        return False, "", f"git timed out after {GIT_TIMEOUT_S}s: {' '.join(cmd)}", -1
    except FileNotFoundError:
        return False, "", "git not found", -1
    except Exception as exc:
        return False, "", str(exc), -1

    ok = result.returncode == 0
    if not ok and result.returncode not in (allowed_returncodes or set()):
        logger.debug(
            "Checkpoint git command failed: %s (rc=%d): %s",
            " ".join(cmd),
            result.returncode,
            result.stderr.strip(),
        )
    return ok, result.stdout.strip(), result.stderr.strip(), result.returncode


def _init_shadow_repo(
    shadow_repo: Path,
    working_dir: str,
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
) -> str | None:
    if (shadow_repo / "HEAD").exists():
        return None

    shadow_repo.mkdir(parents=True, exist_ok=True)
    ok, _stdout, stderr, _rc = _run_git(["init"], shadow_repo=shadow_repo, working_dir=working_dir)
    if not ok:
        return f"shadow repo init failed: {stderr}"

    _run_git(["config", "user.email", "igorek-checkpoint@local"], shadow_repo=shadow_repo, working_dir=working_dir)
    _run_git(["config", "user.name", "Igorek Checkpoint"], shadow_repo=shadow_repo, working_dir=working_dir)

    info_dir = shadow_repo / "info"
    info_dir.mkdir(exist_ok=True)
    (info_dir / "exclude").write_text("\n".join(excludes) + "\n", encoding="utf-8")
    (shadow_repo / "WORKDIR").write_text(str(Path(working_dir).resolve()) + "\n", encoding="utf-8")
    _run_git(
        ["commit", "--allow-empty", "-m", BASENAME_COMMIT_MESSAGE],
        shadow_repo=shadow_repo,
        working_dir=working_dir,
    )
    return None


def _dir_file_count(path: str) -> int:
    count = 0
    try:
        for _ in Path(path).rglob("*"):
            count += 1
            if count > MAX_FILES:
                return count
    except (OSError, PermissionError):
        return count
    return count


def _is_within_root(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _nearest_git_root(path: Path) -> Path | None:
    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _format_reason(reason: str, target_path: str | None = None) -> str:
    if target_path:
        return f"{reason}: {Path(target_path).name}"
    return reason


class CheckpointManager:
    def __init__(
        self,
        enabled: bool = True,
        max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
        data_root: str | None = None,
    ):
        self.enabled = enabled
        self.max_snapshots = max(1, int(max_snapshots))
        self.data_root = str(data_root).strip() if data_root else None
        self._checkpointed_dirs: set[str] = set()
        self._git_available: bool | None = None

    def _shadow_repo(self, working_dir: str) -> Path:
        """Shadow repo for one workdir; the durable root when one is configured."""
        return _shadow_repo_path(working_dir, self.data_root)

    def _shadow_excludes(self, working_dir: str) -> tuple[str, ...]:
        """Exclude the data root itself when it is configured inside the workdir.

        Isolation/kvm mode points ``data_root`` inside the checkpointed tree;
        off mode keeps exactly :data:`DEFAULT_EXCLUDES`.
        """
        durable_root = durable_root_within_workdir(
            workdir=working_dir, data_root=self.data_root
        )
        if durable_root is None:
            return DEFAULT_EXCLUDES
        return (*DEFAULT_EXCLUDES, f"{durable_root}/")

    def new_turn(self) -> None:
        self._checkpointed_dirs.clear()

    def ensure_checkpoint_for_path(
        self,
        *,
        target_path: str,
        workdir: str,
        reason: str,
        authorized_root: str | None = None,
    ) -> bool:
        checkpoint_root = authorized_root or self._resolve_checkpoint_root(target_path=target_path, workdir=workdir)
        if checkpoint_root is None:
            return False
        return self.ensure_checkpoint(working_dir=checkpoint_root, reason=_format_reason(reason, target_path))

    def ensure_checkpoint(self, *, working_dir: str, reason: str = "auto") -> bool:
        if not self.enabled:
            return False

        if self._git_available is None:
            self._git_available = _git_executable() is not None
            if not self._git_available:
                logger.debug("Checkpoints disabled: git not found")
        if not self._git_available:
            return False

        abs_dir = str(Path(working_dir).resolve())
        if abs_dir in {"/", str(Path.home())}:
            logger.debug("Checkpoint skipped for overly broad directory: %s", abs_dir)
            return False
        if abs_dir in self._checkpointed_dirs:
            return False
        if _dir_file_count(abs_dir) > MAX_FILES:
            logger.debug("Checkpoint skipped for large directory: %s", abs_dir)
            return False

        self._checkpointed_dirs.add(abs_dir)
        try:
            return self._take(abs_dir, reason)
        except Exception as exc:
            logger.debug("Checkpoint failed non-fatally: %s", exc)
            return False

    def list_checkpoints(self, working_dir: str) -> list[dict[str, Any]]:
        abs_dir = str(Path(working_dir).resolve())
        shadow_repo = self._shadow_repo(abs_dir)
        if not (shadow_repo / "HEAD").exists():
            return []

        ok, stdout, _stderr, _rc = _run_git(
            [
                "log",
                "--pretty=format:%H%x09%h%x09%cI%x09%s",
                f"-n{self.max_snapshots}",
            ],
            shadow_repo=shadow_repo,
            working_dir=abs_dir,
        )
        if not ok or not stdout:
            return []

        checkpoints: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            full_hash, short_hash, timestamp, subject = (line.split("\t", 3) + ["", "", "", ""])[:4]
            if subject == BASENAME_COMMIT_MESSAGE:
                continue
            checkpoints.append(
                {
                    "hash": full_hash,
                    "short_hash": short_hash,
                    "timestamp": timestamp,
                    "reason": subject,
                }
            )
        return checkpoints

    def has_snapshot_repo(self, working_dir: str) -> bool:
        """Return True when a shadow snapshot repo exists for ``working_dir``.

        Read-only probe used by the mobile bridge to decide whether checkpoint
        browsing is available at all.
        """
        if not self.enabled:
            return False
        shadow_repo = self._shadow_repo(str(Path(working_dir).resolve()))
        return (shadow_repo / "HEAD").exists()

    def has_revision(self, *, working_dir: str, revision: str) -> bool:
        """Return True only for an existing full 40-hex commit in the shadow repo."""
        if not self.enabled:
            return False
        rev = str(revision or "").strip()
        if not _FULL_REVISION_RE.fullmatch(rev):
            return False
        abs_dir = str(Path(working_dir).resolve())
        shadow_repo = self._shadow_repo(abs_dir)
        if not (shadow_repo / "HEAD").exists():
            return False
        ok, _stdout, _stderr, _rc = _run_git(
            ["cat-file", "-e", f"{rev}^{{commit}}"],
            shadow_repo=shadow_repo,
            working_dir=abs_dir,
        )
        return ok

    def baseline_revision(self, *, target_path: str, workdir: str) -> str:
        """Pin the checkpoint used by a task so later reviews keep its base."""
        root = self._resolve_checkpoint_root(target_path=target_path, workdir=workdir)
        if root is None:
            return ""
        ok, revision, _, _ = _run_git(
            ["rev-parse", "--verify", "HEAD^{commit}"],
            shadow_repo=self._shadow_repo(root), working_dir=root,
        )
        return revision.strip() if ok else ""

    def baseline_content(self, *, target_path: str, workdir: str) -> tuple[bool, str | None]:
        """Return the pre-change content of a path from the latest checkpoint.

        The shadow repo's HEAD is the snapshot taken before the current task's
        first write, so this yields the task baseline for a path.

        Returns:
            ``(available, content)``:
            - ``(False, None)`` when checkpointing is unavailable (no git, no
              snapshot yet, or the path is outside the checkpoint root).
            - ``(True, None)`` when the snapshot is available but the path did
              not exist at that point (a newly added file).
            - ``(True, <text>)`` with the file content at snapshot time.
        """
        if not self.enabled:
            return False, None
        root = self._resolve_checkpoint_root(target_path=target_path, workdir=workdir)
        if root is None:
            return False, None
        resolved_root = Path(root).resolve()
        shadow_repo = self._shadow_repo(root)
        if not (shadow_repo / "HEAD").exists():
            return False, None
        try:
            relative = Path(target_path).resolve().relative_to(resolved_root).as_posix()
        except (ValueError, OSError):
            return False, None

        env = _git_env(shadow_repo, str(resolved_root))
        spec = f"HEAD:{relative}"
        return _checkpoint_text(spec, root=resolved_root, env=env)

    def content_at(
        self,
        *,
        target_path: str,
        workdir: str,
        revision: str,
    ) -> tuple[bool, str | None]:
        """Return the content of a path at an arbitrary shadow-repo revision.

        Read-only counterpart of ``baseline_content`` (which pins HEAD). Returns:
        - ``(False, None)`` when checkpointing is unavailable (bad revision, no
          git, no snapshot, or the path is outside the checkpoint root).
        - ``(True, None)`` when the revision exists but the path did not.
        - ``(True, <text>)`` with the file content at that revision.
        """
        if not self.enabled:
            return False, None
        rev = str(revision or "").strip()
        if not _FULL_REVISION_RE.fullmatch(rev):
            return False, None
        root = self._resolve_checkpoint_root(target_path=target_path, workdir=workdir)
        if root is None:
            return False, None
        resolved_root = Path(root).resolve()
        shadow_repo = self._shadow_repo(root)
        if not (shadow_repo / "HEAD").exists():
            return False, None
        try:
            relative = Path(target_path).resolve().relative_to(resolved_root).as_posix()
        except (ValueError, OSError):
            return False, None

        env = _git_env(shadow_repo, str(resolved_root))
        spec = f"{rev}:{relative}"
        return _checkpoint_text(spec, root=resolved_root, env=env)

    def changed_paths_since(self, *, working_dir: str, revision: str) -> list[dict[str, Any]] | None:
        """Diff ``revision`` against the current work tree as changed-file stats.

        Read-only: ``git diff`` never stages or checks anything out. Returns the
        mobile ``changes`` file entries (path/additions/deletions/status) sorted
        by path, or ``None`` when the shadow repo or revision is unavailable.
        """
        if not self.enabled:
            return None
        rev = str(revision or "").strip()
        if not _FULL_REVISION_RE.fullmatch(rev):
            return None
        abs_dir = str(Path(working_dir).resolve())
        shadow_repo = self._shadow_repo(abs_dir)
        if not (shadow_repo / "HEAD").exists():
            return None

        ok, stdout, _stderr, _rc = _run_git(
            ["diff", "--name-status", rev],
            shadow_repo=shadow_repo,
            working_dir=abs_dir,
        )
        if not ok:
            return None
        statuses: dict[str, str] = {}
        for line in stdout.splitlines():
            parts = line.split("\t")
            code = parts[0][:1] if parts and parts[0] else ""
            if code not in {"A", "M", "D"} or len(parts) < 2:
                continue
            statuses[parts[1]] = {"A": "added", "M": "modified", "D": "deleted"}[code]

        line_counts: dict[str, tuple[int, int]] = {}
        ok, stdout, _stderr, _rc = _run_git(
            ["diff", "--numstat", rev],
            shadow_repo=shadow_repo,
            working_dir=abs_dir,
        )
        if ok:
            for line in stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                additions = int(parts[0]) if parts[0].isdigit() else 0
                deletions = int(parts[1]) if parts[1].isdigit() else 0
                line_counts[parts[2]] = (additions, deletions)

        changed: list[dict[str, Any]] = []
        for path in sorted(statuses):
            additions, deletions = line_counts.get(path, (0, 0))
            changed.append(
                {
                    "path": path,
                    "additions": additions,
                    "deletions": deletions,
                    "status": statuses[path],
                }
            )
        return changed

    def _resolve_checkpoint_root(self, *, target_path: str, workdir: str) -> str | None:
        resolved_target = Path(target_path).resolve()
        resolved_workdir = Path(workdir).resolve()

        if _is_within_root(str(resolved_target), str(resolved_workdir)):
            return str(resolved_workdir)

        git_root = _nearest_git_root(resolved_target.parent if resolved_target.suffix else resolved_target)
        if git_root is not None:
            return str(git_root)

        parent = resolved_target.parent
        if parent.exists():
            return str(parent)
        return None

    def _take(self, working_dir: str, reason: str) -> bool:
        shadow_repo = self._shadow_repo(working_dir)
        init_error = _init_shadow_repo(
            shadow_repo, working_dir, self._shadow_excludes(working_dir)
        )
        if init_error:
            logger.debug("Checkpoint init skipped: %s", init_error)
            return False

        _run_git(["add", "-A"], shadow_repo=shadow_repo, working_dir=working_dir)
        ok, _stdout, _stderr, rc = _run_git(
            ["diff", "--cached", "--quiet"],
            shadow_repo=shadow_repo,
            working_dir=working_dir,
            allowed_returncodes={1},
        )
        if ok or rc == 0:
            if self._has_only_baseline(working_dir):
                ok, _stdout, stderr, _rc = _run_git(
                    ["commit", "--allow-empty", "-m", f"checkpoint: {reason}"],
                    shadow_repo=shadow_repo,
                    working_dir=working_dir,
                )
                if not ok:
                    logger.debug("Checkpoint empty commit failed: %s", stderr)
                    return False
                self._trim_history(working_dir)
                return True
            return False
        if rc != 1:
            return False

        ok, _stdout, stderr, _rc = _run_git(
            ["commit", "-m", f"checkpoint: {reason}"],
            shadow_repo=shadow_repo,
            working_dir=working_dir,
        )
        if not ok:
            logger.debug("Checkpoint commit failed: %s", stderr)
            return False

        self._trim_history(working_dir)
        return True

    def _trim_history(self, working_dir: str) -> None:
        return

    def _has_only_baseline(self, working_dir: str) -> bool:
        shadow_repo = self._shadow_repo(working_dir)
        ok, stdout, _stderr, _rc = _run_git(
            ["log", "--pretty=format:%s", "-n2"],
            shadow_repo=shadow_repo,
            working_dir=working_dir,
        )
        if not ok:
            return False
        subjects = [line.strip() for line in stdout.splitlines() if line.strip()]
        return subjects == [BASENAME_COMMIT_MESSAGE]
