#!/usr/bin/env python3
"""
Skills Manager for Agent Factory.

Supports both legacy flat markdown skills and package-style skills with SKILL.md.
Uses progressive disclosure: metadata is indexed eagerly, full body is loaded only
for selected skills at prompt-build time.
"""

import os
import re
import json
import time
import shutil
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import yaml

try:
    from .runtime import get_data_root, get_db_path
    from .schema import initialize_database, open_db
except ImportError:  # pragma: no cover - direct script fallback
    from runtime import get_data_root, get_db_path
    from schema import initialize_database, open_db

FACTORY_DIR = Path(get_data_root())
DB_PATH = get_db_path()
initialize_database(DB_PATH)
DEFAULT_LEGACY_SKILLS_PATH = os.environ.get(
    "AGENTFACTORY_LEGACY_SKILLS_PATH",
    str(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "igorek/legacy-skills"),
)


def _default_skills_path() -> str:
    override = os.environ.get("AGENTFACTORY_SKILLS_PATH", "").strip()
    if override:
        return override
    local_path = FACTORY_DIR / "skills"
    if local_path.exists():
        return str(local_path)
    return DEFAULT_LEGACY_SKILLS_PATH


def _default_skills_dirs() -> list[str]:
    """Return the default list of skills directories to scan.

    Resolution order:
    1. AGENTFACTORY_SKILLS_DIRS env var (colon-separated) — takes full precedence.
    2. AGENTFACTORY_SKILLS_PATH env var (legacy) — single-element list.
    3. Computed default: [DATA_ROOT/skills, OBSIDIAN_PATH] (only dirs that exist).
    """
    override = os.environ.get("AGENTFACTORY_SKILLS_DIRS", "").strip()
    if override:
        return [p.strip() for p in override.split(":") if p.strip()]

    single = os.environ.get("AGENTFACTORY_SKILLS_PATH", "").strip()
    if single:
        return [single]

    dirs: list[str] = []
    local_path = str(FACTORY_DIR / "skills")
    if os.path.isdir(local_path):
        dirs.append(local_path)
    if os.path.isdir(DEFAULT_LEGACY_SKILLS_PATH):
        dirs.append(DEFAULT_LEGACY_SKILLS_PATH)

    return dirs if dirs else [local_path]


def _skill_aliases(skill: "Skill") -> list[str]:
    aliases = [skill.skill_id, skill.name]
    return [alias.strip().lower() for alias in aliases if alias and alias.strip()]


def _explicit_skill_mentioned(text: str, skill: "Skill") -> bool:
    """Return True when the user explicitly names this skill."""
    text_lower = text.lower()
    for alias in _skill_aliases(skill):
        escaped = re.escape(alias)
        patterns = [
            rf"(?<![\w-])skill\s+{escaped}(?![\w-])",
            rf"(?<![\w-])скилл\s+{escaped}(?![\w-])",
            rf"(?<![\w-])навык\s+{escaped}(?![\w-])",
            rf"(?<![\w-])/{escaped}(?![\w-])",
            rf"(?<![\w-])\${escaped}(?![\w-])",
        ]
        if any(re.search(pattern, text_lower, flags=re.IGNORECASE) for pattern in patterns):
            return True
    return False


class Skill:
    """Represents a single skill and its lazily-loaded body."""

    def __init__(
        self,
        skill_id: str,
        name: str,
        body_path: str,
        triggers: List[str],
        description: str = "",
        enabled: bool = True,
        source_file: str = "",
        content: str = "",
        package_dir: str = "",
        backend_filter: Optional[List[str]] = None,
        category: str = "",
        source_dir: str = "",
        must_write: bool = False,
    ):
        self.skill_id = skill_id
        self.name = name
        self.body_path = body_path
        self.content = content
        self.triggers = triggers
        self.description = description
        self.enabled = enabled
        self.source_file = source_file
        self.package_dir = package_dir
        self.backend_filter = [item.strip().lower() for item in (backend_filter or []) if str(item).strip()]
        self.category = category
        self.source_dir = source_dir
        self.must_write = must_write

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "content": self.load_content(),
            "triggers": self.triggers,
            "description": self.description,
            "enabled": self.enabled,
            "source_file": self.source_file,
            "package_dir": self.package_dir,
            "backend_filter": self.backend_filter,
            "category": self.category,
            "source_dir": self.source_dir,
        }

    def to_metadata_dict(self) -> dict:
        """Return metadata without full content (for API listing)"""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "triggers_count": len(self.triggers),
            "enabled": self.enabled,
            "source_file": self.source_file,
            "package_dir": self.package_dir,
            "backend_filter": self.backend_filter,
            "category": self.category,
            "source_dir": self.source_dir,
        }

    def load_content(self) -> str:
        if self.content:
            return self.content
        try:
            raw_content = Path(self.body_path).read_text(encoding="utf-8")
            if raw_content.startswith("---\n"):
                marker = "\n---\n"
                end = raw_content.find(marker, 4)
                if end != -1:
                    raw_content = raw_content[end + len(marker):]
            self.content = raw_content.strip()
        except Exception:
            self.content = ""
        return self.content

    def supports_backend(self, backend: str) -> bool:
        if not self.backend_filter:
            return True
        return backend.strip().lower() in self.backend_filter

    def bundled_files(self, limit: int = 24) -> List[str]:
        if not self.package_dir:
            return []
        package_root = Path(self.package_dir)
        if not package_root.exists() or not package_root.is_dir():
            return []
        files: List[str] = []
        for path in sorted(package_root.rglob("*")):
            if not path.is_file():
                continue
            if path.name == "SKILL.md":
                continue
            try:
                rel = str(path.relative_to(package_root))
            except ValueError:
                continue
            files.append(rel)
            if len(files) >= limit:
                break
        return files

    def resource_dirs(self) -> List[str]:
        if not self.package_dir:
            return []
        package_root = Path(self.package_dir)
        names: List[str] = []
        for name in ("scripts", "references", "assets"):
            if (package_root / name).exists():
                names.append(name)
        return names

    def env_files(self) -> List[Path]:
        if not self.package_dir:
            return []
        env_root = Path(self.package_dir) / "env"
        if not env_root.exists() or not env_root.is_dir():
            return []
        return sorted(path for path in env_root.glob("*.env") if path.is_file())

    def matches(self, text: str) -> Tuple[bool, float]:
        """
        Check if skill matches the given text
        Returns (matches, score)
        """
        text_lower = text.lower()
        matches_count = 0
        score = 0.0

        searchable_fields = [trigger.lower() for trigger in self.triggers if trigger.strip()]
        if self.name.strip():
            searchable_fields.append(self.name.lower())
        if self.description.strip():
            searchable_fields.append(self.description.lower())

        total_triggers = len(searchable_fields)

        if total_triggers == 0:
            return False, 0.0

        for trigger_lower in searchable_fields:
            # Exact match has higher score
            if trigger_lower in text_lower:
                matches_count += 1
                score += 1.0
                continue

            trigger_tokens = [token for token in re.split(r"[\s,;/:\-\(\)\[\]]+", trigger_lower) if len(token) >= 4]
            token_hits = sum(1 for token in trigger_tokens if token in text_lower)
            if token_hits:
                matches_count += 1
                score += 0.35 + (0.15 * min(token_hits, 3))

        if matches_count > 0:
            normalized = score / total_triggers
            return True, normalized

        return False, 0.0


class SkillsManager:
    """Manages skills from local runtime directory or legacy skills path."""

    def __init__(self, skills_path: str | None = None, skills_dirs: list[str] | None = None):
        if skills_dirs is not None:
            self.skills_dirs = list(skills_dirs)
        elif skills_path is not None:
            self.skills_dirs = [skills_path]
        else:
            self.skills_dirs = _default_skills_dirs()

        # Backward-compat alias: first dir is the writable root
        self.skills_path: str = self.skills_dirs[0] if self.skills_dirs else _default_skills_path()
        self.skills: Dict[str, Skill] = {}  # Only enabled skills
        self.all_skills: Dict[str, Skill] = {}  # All skills (including disabled)
        self.is_loaded = False
        self._last_mtime = 0  # For hot-reload: last modified timestamp of the directory
        self._last_settings_mtime = 0  # For hot-reload: last modified timestamp of the database configuration

        # Initialize the `skills_state` table
        self._ensure_skills_tables()

    def _ensure_skills_tables(self):
        """Create state and usage tables for **skills**."""
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            with open_db(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS skills_state (
                        skill_id TEXT PRIMARY KEY,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        updated_at INTEGER NOT NULL
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS skills_usage (
                        skill_id TEXT PRIMARY KEY,
                        selected_count INTEGER NOT NULL DEFAULT 0,
                        last_selected_at INTEGER,
                        updated_at INTEGER NOT NULL
                    )
                """)
                conn.commit()
        except Exception as e:
            print(f"[Skills] Error creating skills tables: {e}")

    def load_skills(self) -> bool:
        """Load all skills from runtime directories with enabled/disabled state."""
        try:
            self.all_skills.clear()
            self.skills.clear()

            enabled_states = self._load_enabled_states()
            any_found = False

            for skills_dir_str in self.skills_dirs:
                if not os.path.exists(skills_dir_str):
                    print(f"[Skills] Skills directory not found, skipping: {skills_dir_str}")
                    continue

                skills_dir = Path(skills_dir_str)
                skill_files = self._discover_skill_files(skills_dir)

                if not skill_files:
                    continue

                print(f"[Skills] Loading {len(skill_files)} skills from {skills_dir_str}")
                any_found = True

                for skill_file in skill_files:
                    skill = self._parse_skill_file(skill_file, base_dir=skills_dir)
                    if skill is None:
                        continue

                    # First-wins: skip if already loaded from an earlier dir
                    if skill.skill_id in self.all_skills:
                        continue

                    if skill.skill_id in enabled_states:
                        skill.enabled = enabled_states[skill.skill_id]
                    else:
                        skill.enabled = True
                        self._save_skill_state(skill.skill_id, skill.enabled)

                    skill.source_dir = skills_dir_str
                    self.all_skills[skill.skill_id] = skill

                    if skill.enabled:
                        self.skills[skill.name] = skill
                        print(f"[Skills] Loaded: {skill.name} (enabled)")
                    else:
                        print(f"[Skills] Loaded: {skill.name} (disabled)")

            self.is_loaded = any_found
            if any_found:
                self._last_mtime = max(
                    (self._compute_skills_mtime(Path(d)) for d in self.skills_dirs if os.path.isdir(d)),
                    default=0,
                )
                enabled_count = sum(1 for s in self.all_skills.values() if s.enabled)
                print(f"[Skills] Loaded {len(self.all_skills)} skills ({enabled_count} enabled) from {len(self.skills_dirs)} dirs")

            return any_found

        except Exception as e:
            print(f"[Skills] Error loading skills: {e}")
            return False

    def _load_enabled_states(self) -> Dict[str, bool]:
        """Load enabled states from the database"""
        states = {}
        try:
            with open_db(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute("SELECT skill_id, enabled FROM skills_state")
                for row in cur.fetchall():
                    states[row[0]] = bool(row[1])
        except Exception as e:
            print(f"[Skills] Error loading enabled states: {e}")
        return states

    def _save_skill_state(self, skill_id: str, enabled: bool):
        """Save skill state to the database"""
        try:
            with open_db(DB_PATH) as conn:
                cur = conn.cursor()
                current_time = int(time.time())
                cur.execute("""
                    INSERT INTO skills_state (skill_id, enabled, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(skill_id) DO UPDATE SET
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                """, (skill_id, int(enabled), current_time))
                conn.commit()
        except Exception as e:
            print(f"[Skills] Error saving skill state: {e}")

    def needs_reload(self) -> bool:
        """Check if skills need to be reloaded based on mtime."""
        try:
            for d in self.skills_dirs:
                if os.path.isdir(d) and self._compute_skills_mtime(Path(d)) > self._last_mtime:
                    return True
            if os.path.exists(DB_PATH):
                if os.path.getmtime(DB_PATH) > self._last_settings_mtime:
                    return True
            return False
        except Exception:
            return False

    def get_metadata(self) -> List[Dict]:
        """Get all skills metadata without full content (for API listing)"""
        return [skill.to_metadata_dict() for skill in self.all_skills.values()]

    def get_active_count(self) -> int:
        """Get count of enabled skills"""
        return sum(1 for s in self.all_skills.values() if s.enabled)

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> Tuple[bool, str]:
        """Set the enabled state for the skill.

        Args:
            skill_id: Skill ID (basename of the .md file without extension)
            enabled: Enabled/disabled state

        Returns:
            (success, message) tuple
        """
        # Sanitize `skill_id`
        safe_id = self._sanitize_filename(skill_id)
        if not safe_id:
            return False, "Invalid skill_id"

        if skill_id not in self.all_skills:
            return False, f"Skill not found: {skill_id}"

        try:
            # Save to the database
            self._save_skill_state(skill_id, enabled)

            # Update in memory
            self.all_skills[skill_id].enabled = enabled

            # Rebuilds the skills list (only enabled ones)
            self.skills.clear()
            for s in self.all_skills.values():
                if s.enabled:
                    self.skills[s.name] = s

            status = "enabled" if enabled else "disabled"
            print(f"[Skills] Skill '{skill_id}' {status}")
            return True, f"Skill '{skill_id}' {status}"

        except Exception as e:
            return False, f"Error updating skill state: {str(e)}"

    def find_skill_by_name(self, name: str) -> Optional[Skill]:
        """Find skill by name (among all skills, including disabled ones)"""
        for skill in self.all_skills.values():
            if skill.name == name:
                return skill
        return None

    def install_skill_from_markdown(self, name: str, content: str, overwrite: bool = False) -> Tuple[bool, str]:
        """
        Install a skill from markdown text.

        Args:
            name: Skill filename (without .md extension)
            content: Markdown content of the skill
            overwrite: If False, return error if file exists

        Returns:
            (success, message_or_path) tuple
        """
        # File name validation to prevent path traversal attacks
        safe_name = self._sanitize_filename(name)
        if not safe_name:
            return False, "Invalid skill name"

        # Ensures that the `skills_path` directory exists and is accessible
        if not os.path.exists(self.skills_path):
            return False, f"Skills directory does not exist: {self.skills_path}"

        # Adds `.md` extension if missing
        filename = safe_name if safe_name.endswith('.md') else f"{safe_name}.md"
        file_path = os.path.join(self.skills_path, filename)
        skill_id = safe_name.replace('.md', '')

        # Checks file existence
        if os.path.exists(file_path) and not overwrite:
            return False, f"Skill file already exists: {filename}. Use overwrite=true to replace."

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)

            # Saves state to the database (default: enabled)
            self._save_skill_state(skill_id, True)

            print(f"[Skills] Installed skill: {filename} (enabled)")
            return True, file_path
        except Exception as e:
            return False, f"Error writing skill file: {str(e)}"

    def _sanitize_filename(self, name: str) -> str:
        """
        Sanitize filename to prevent path traversal attacks.

        Args:
            name: Raw filename from user input

        Returns:
            Safe filename or empty string if invalid
        """
        if not name:
            return ""

        # Remove path and extension
        name = os.path.basename(name)

        # Remove `.md` extension if present (we'll add it manually later)
        if name.endswith('.md'):
            name = name[:-3]

        # Disallow path traversal
        if '..' in name or '/' in name or '\\' in name:
            return ""

        # Remove invalid characters for Unix/Windows
        # Keep only letters, digits, hyphens, underscores, and spaces
        import re
        name = re.sub(r'[<>:"|?*\x00-\x1f]', '', name)
        name = name.strip()

        # Limit the name length
        if len(name) > 100 or len(name) < 1:
            return ""

        return name

    def _discover_skill_files(self, skills_dir: Path) -> List[Path]:
        excluded_dirs = {".git", ".github", ".hub", "__pycache__"}
        package_files = sorted(
            path
            for path in skills_dir.rglob("SKILL.md")
            if path.is_file() and not any(part in excluded_dirs for part in path.parts)
        )
        legacy_files = sorted(path for path in skills_dir.glob("*.md") if path.is_file() and path.name.upper() != "SKILL.MD")
        return package_files + legacy_files

    def _compute_skills_mtime(self, skills_dir: Path) -> float:
        latest = skills_dir.stat().st_mtime
        for skill_file in self._discover_skill_files(skills_dir):
            try:
                latest = max(latest, skill_file.stat().st_mtime)
            except FileNotFoundError:
                continue
        return latest

    def _parse_frontmatter(self, raw_content: str) -> tuple[dict, str]:
        if not raw_content.startswith("---\n"):
            return {}, raw_content
        marker = "\n---\n"
        end = raw_content.find(marker, 4)
        if end == -1:
            return {}, raw_content
        frontmatter_raw = raw_content[4:end]
        body = raw_content[end + len(marker):]
        try:
            data = yaml.safe_load(frontmatter_raw) or {}
        except Exception:
            return {}, raw_content
        if not isinstance(data, dict):
            return {}, raw_content
        return data, body

    def _normalize_trigger_list(self, value) -> List[str]:
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[\n,;]+", value) if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _legacy_skill_metadata(self, content: str, skill_id: str) -> tuple[str, str, List[str]]:
        name_match = re.search(r'^#\s+(.+?)$', content, re.MULTILINE)
        if not name_match:
            name = skill_id.replace("-", " ").replace("_", " ").strip() or skill_id
        else:
            name = name_match.group(1).strip()
            name = re.sub(r'[#🤖📊🎯📈📋🛠️⚙️🔢📁📄🔍✨💡🎨🌟🚀✅❌🔧💻🎪🎭🎯🎪🎨]', '', name).strip()

        triggers: List[str] = []
        trigger_section = re.search(
            r'\*\*Типичные фразы-триггеры:\*\*\s*\n(.*?)(?=\n\n|\n[A-ZА-Я]|\n#[A-ZА-Я]|$)',
            content,
            re.DOTALL
        )
        if trigger_section:
            trigger_lines = trigger_section.group(1).strip().split('\n')
            for line in trigger_lines:
                line = re.sub(r'\*\*([^*]+)\*\*', r'\1', line.strip())
                line = re.sub(r'^[\s\-\*\d\.]+', '', line).strip()
                if line and not line.startswith('#'):
                    triggers.append(line)

        description = ""
        desc_section = re.search(
            r'\*\*Используйте этот скилл, когда:\*\*\s*(.*?)(?=\n\n|\*\*|$)',
            content,
            re.DOTALL
        )
        if desc_section:
            desc_text = desc_section.group(1).strip()
            desc_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', desc_text)
            desc_text = re.sub(r'^\s*[-*]\s*', '', desc_text, flags=re.MULTILINE)
            description = desc_text[:240] + "..." if len(desc_text) > 240 else desc_text
        return name, description, triggers

    def _parse_skill_file(self, file_path: Path, base_dir: Path | None = None) -> Optional[Skill]:
        """Parse a single skill file without eagerly loading everything into prompt."""
        try:
            content = file_path.read_text(encoding='utf-8')
            frontmatter, body = self._parse_frontmatter(content)

            base = base_dir or Path(self.skills_path)
            if file_path.name == "SKILL.md":
                skill_id = file_path.parent.name
                source_file = str(file_path.relative_to(base))
                package_dir = str(file_path.parent)
                rel_parts = Path(source_file).parts
                category = rel_parts[0] if len(rel_parts) >= 3 else ""
            else:
                skill_id = file_path.stem
                source_file = file_path.name
                package_dir = ""
                category = ""

            if frontmatter:
                name = str(frontmatter.get("name", "")).strip() or skill_id
                description = str(frontmatter.get("description", "")).strip()
                triggers = self._normalize_trigger_list(frontmatter.get("triggers"))
                backend_filter = self._normalize_trigger_list(frontmatter.get("backends") or frontmatter.get("backend"))
                must_write = bool(frontmatter.get("must_write"))
            else:
                name, description, triggers = self._legacy_skill_metadata(content, skill_id)
                backend_filter = []
                must_write = False

            return Skill(
                skill_id=skill_id,
                name=name,
                body_path=str(file_path),
                content="",
                triggers=triggers,
                description=description,
                source_file=source_file,
                package_dir=package_dir,
                backend_filter=backend_filter,
                category=category,
                must_write=must_write,
            )

        except Exception as e:
            print(f"[Skills] Error parsing {file_path}: {e}")
            return None

    def find_relevant_skills(self, task_text: str, backend: str = "", max_skills: int = 3) -> List[Skill]:
        """Find most relevant enabled skills for a task."""
        if not self.is_loaded or not self.skills:
            return []

        explicit_skills: List[Skill] = []
        scored_skills = []
        for skill in self.skills.values():
            if backend and not skill.supports_backend(backend):
                continue
            if _explicit_skill_mentioned(task_text, skill):
                explicit_skills.append(skill)
                continue
            matches, score = skill.matches(task_text)
            if matches and score > 0:
                scored_skills.append((skill, score))

        scored_skills.sort(key=lambda x: x[1], reverse=True)
        selected: List[Skill] = []
        seen: set[str] = set()
        for skill in explicit_skills:
            if skill.skill_id not in seen:
                selected.append(skill)
                seen.add(skill.skill_id)
        for skill, _ in scored_skills:
            if len(selected) >= max_skills:
                break
            if skill.skill_id not in seen:
                selected.append(skill)
                seen.add(skill.skill_id)
        return selected[:max_skills]

    def find_explicitly_mentioned_skills(self, task_text: str, backend: str = "") -> List[Skill]:
        """Return enabled skills explicitly named by the user."""
        if not self.is_loaded or not self.skills:
            return []

        explicit_skills: List[Skill] = []
        seen: set[str] = set()
        for skill in self.skills.values():
            if backend and not skill.supports_backend(backend):
                continue
            if skill.skill_id in seen:
                continue
            if _explicit_skill_mentioned(task_text, skill):
                explicit_skills.append(skill)
                seen.add(skill.skill_id)
        return explicit_skills

    def get_skill_by_name(self, name: str) -> Optional[Skill]:
        """Get skill by name"""
        return self.skills.get(name)

    def list_skills(self) -> List[Dict]:
        """List all loaded skills"""
        return [skill.to_dict() for skill in self.skills.values()]

    def _record_skill_selection(self, skills: List[Skill]) -> None:
        if not skills:
            return
        try:
            now = int(time.time())
            with open_db(DB_PATH) as conn:
                cur = conn.cursor()
                for skill in skills:
                    cur.execute("""
                        INSERT INTO skills_usage (skill_id, selected_count, last_selected_at, updated_at)
                        VALUES (?, 1, ?, ?)
                        ON CONFLICT(skill_id) DO UPDATE SET
                            selected_count = selected_count + 1,
                            last_selected_at = excluded.last_selected_at,
                            updated_at = excluded.updated_at
                    """, (skill.skill_id, now, now))
                conn.commit()
        except Exception as e:
            print(f"[Skills] Error recording usage: {e}")

    def get_available_skills_summary(self, backend: str = "", limit: int = 12) -> str:
        enabled_skills = [skill for skill in self.skills.values() if not backend or skill.supports_backend(backend)]
        if not enabled_skills:
            return ""
        lines = []
        for skill in sorted(enabled_skills, key=lambda item: item.name.lower())[:limit]:
            desc = skill.description.strip() or "Описание не указано."
            lines.append(f"- {skill.name}: {desc}")
        if len(enabled_skills) > limit:
            lines.append(f"- ... ещё {len(enabled_skills) - limit} skills")
        return "## ДОСТУПНЫЕ SKILLS\n" + "\n".join(lines)

    def _link_or_copy_path(self, source: Path, target: Path) -> None:
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(source, target, target_is_directory=source.is_dir())
        except Exception:
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)

    def _load_env_vars_from_skill(self, skill: Skill) -> Dict[str, str]:
        env_vars: Dict[str, str] = {}
        for env_file in skill.env_files():
            try:
                for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):].strip()
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip("'").strip('"')
                    if not key:
                        continue
                    env_vars[key] = value
            except Exception as e:
                print(f"[Skills] Error reading env for {skill.skill_id}: {e}")
        return env_vars

    def _materialize_skill_runtime(self, skill: Skill, workdir: str) -> Dict[str, object]:
        if not skill.package_dir:
            return {
                "runtime_dir": "",
                "bundled_files": [],
                "env_vars": {},
                "resource_dirs": [],
            }

        runtime_root = Path(workdir) / ".skill_runtime" / skill.skill_id
        runtime_root.mkdir(parents=True, exist_ok=True)
        package_root = Path(skill.package_dir)

        for name in skill.resource_dirs():
            source = package_root / name
            target = runtime_root / name
            self._link_or_copy_path(source, target)

        # Also expose extra top-level files except SKILL.md and env/.
        bundled_files: List[str] = []
        for relative in skill.bundled_files(limit=64):
            if relative.startswith("env/"):
                continue
            bundled_files.append(relative)
            source = package_root / relative
            target = runtime_root / relative
            if not target.exists() and not target.is_symlink():
                self._link_or_copy_path(source, target)

        return {
            "runtime_dir": str(runtime_root),
            "bundled_files": bundled_files,
            "env_vars": self._load_env_vars_from_skill(skill),
            "resource_dirs": skill.resource_dirs(),
        }

    def build_skill_runtime_context(self, task_text: str, backend: str = "", workdir: str = "") -> Tuple[str, Dict[str, str]]:
        relevant_skills = self.find_relevant_skills(task_text, backend=backend)
        summary = self.get_available_skills_summary(backend=backend)

        sections: List[str] = []
        merged_env: Dict[str, str] = {}
        if summary:
            sections.append(summary)
        if not relevant_skills:
            return (("\n\n" + "\n\n".join(sections) + "\n") if sections else "", merged_env)

        self._record_skill_selection(relevant_skills)
        sections.append("## ВЫБРАННЫЕ SKILLS ДЛЯ ЭТОЙ ЗАДАЧИ")
        for skill in relevant_skills:
            materialized = self._materialize_skill_runtime(skill, workdir) if workdir else {
                "runtime_dir": skill.package_dir,
                "bundled_files": skill.bundled_files(limit=64),
                "env_vars": self._load_env_vars_from_skill(skill),
                "resource_dirs": skill.resource_dirs(),
            }
            env_vars = materialized.get("env_vars")
            if isinstance(env_vars, dict):
                for key, value in env_vars.items():
                    if key and isinstance(value, str):
                        merged_env[key] = value

            skill_sections: List[str] = [f"### {skill.name}"]
            runtime_dir = str(materialized.get("runtime_dir") or "")
            if runtime_dir:
                skill_sections.append(f"runtime_dir: {runtime_dir}")
                bundled = materialized.get("bundled_files") or []
                if bundled:
                    bundled_lines = "\n".join(f"- {item}" for item in bundled[:24])
                    skill_sections.append("bundled_files:\n" + bundled_lines)
                resource_dirs = materialized.get("resource_dirs") or []
                if resource_dirs:
                    skill_sections.append("resource_dirs: " + ", ".join(map(str, resource_dirs)))
                if env_vars:
                    env_names = ", ".join(sorted(str(key) for key in env_vars.keys()))
                    skill_sections.append(
                        f"Runtime already injected environment variables for this skill: {env_names}. "
                        "Не пытайся читать секреты из внешних файлов; используй уже доступное окружение."
                    )
                skill_sections.append(
                    "Важно: работай только с task-local skill runtime внутри runtime_dir. "
                    "Все относительные пути этого skill нужно резолвить от runtime_dir."
                )
                if skill.skill_id == "aa-leaderboard":
                    skill_sections.append(
                        "Прямая команда запуска для этого skill:\n"
                        f"`python3 {runtime_dir}/scripts/aa_fetch.py --top 30`\n"
                        "Примеры:\n"
                        f"- `python3 {runtime_dir}/scripts/aa_fetch.py --top 20`\n"
                        f"- `python3 {runtime_dir}/scripts/aa_fetch.py --range 35 40`\n"
                        f"- `python3 {runtime_dir}/scripts/aa_fetch.py --search qwen`"
                    )
            content = skill.load_content()
            skill_sections.append(content)
            sections.append("\n".join(skill_sections))

        return ("\n\n" + "\n\n".join(sections) + "\n", merged_env)

    def get_skills_for_prompt(self, task_text: str, backend: str = "") -> str:
        """Backward-compatible prompt builder without workdir materialization."""
        prompt, _ = self.build_skill_runtime_context(task_text, backend=backend, workdir="")
        return prompt

    def save_settings(self, skills_path: str, auto_load: bool = True, smart_selection: bool = True, skills_dirs: list[str] | None = None):
        """Save skills manager settings to database"""
        try:
            with open_db(DB_PATH) as conn:
                cur = conn.cursor()

                # Create settings table if not exists
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT,
                        updated_at INTEGER
                    )
                """)

                # Upsert settings
                settings = {
                    'skills_path': skills_path,
                    'skills_auto_load': str(auto_load).lower(),
                    'skills_smart_selection': str(smart_selection).lower()
                }
                if skills_dirs is not None:
                    settings['skills_dirs'] = json.dumps(skills_dirs)

                current_time = int(time.time())
                for key, value in settings.items():
                    cur.execute("""
                        INSERT INTO settings (key, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at
                    """, (key, value, current_time))

                conn.commit()

            print(f"[Skills] Settings saved: {settings}")

        except Exception as e:
            print(f"[Skills] Error saving settings: {e}")

    def load_settings(self) -> Dict[str, str]:
        """Load settings from database"""
        settings = {}
        try:
            with open_db(DB_PATH) as conn:
                cur = conn.cursor()

                cur.execute("SELECT key, value FROM settings WHERE key LIKE 'skills_%'")
                for row in cur.fetchall():
                    settings[row[0]] = row[1]


            # Check for multi-dir config first
            if 'skills_dirs' in settings:
                try:
                    dirs = json.loads(settings['skills_dirs'])
                    if isinstance(dirs, list) and dirs:
                        self.skills_dirs = [str(d).strip() for d in dirs if str(d).strip()]
                        self.skills_path = self.skills_dirs[0]
                except (json.JSONDecodeError, TypeError):
                    pass
            elif 'skills_path' in settings:
                # Legacy single-dir backward compat
                self.skills_path = settings['skills_path']
                self.skills_dirs = [self.skills_path]

            if os.path.exists(DB_PATH):
                self._last_settings_mtime = os.path.getmtime(DB_PATH)

            print(f"[Skills] Settings loaded: {settings}")
            return settings

        except Exception as e:
            print(f"[Skills] Error loading settings: {e}")
            return settings
# Global instance
skills_manager = SkillsManager()
