"""Skill installation, configuration and task-local resources stay coherent."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import skills_manager as module
from factory.core.schema import initialize_database


@pytest.fixture
def skills(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    database = root / "factory.db"
    initialize_database(str(database))
    package_root = root / "skills"
    package_root.mkdir()
    legacy = root / "legacy"
    monkeypatch.setattr(module, "DB_PATH", str(database))
    monkeypatch.setattr(module, "FACTORY_DIR", root)
    monkeypatch.setattr(module, "DEFAULT_LEGACY_SKILLS_PATH", str(legacy))
    for key in ("AGENTFACTORY_SKILLS_DIRS", "AGENTFACTORY_SKILLS_PATH"):
        monkeypatch.delenv(key, raising=False)
    manager = module.SkillsManager(skills_path=str(package_root))
    return SimpleNamespace(
        root=root,
        database=database,
        packages=package_root,
        legacy=legacy,
        manager=manager,
    )


def package(
    root, name="fixture-skill", *, body="Use fixture resources", backend="openai"
):
    folder = root / name
    folder.mkdir(parents=True)
    source = folder / "SKILL.md"
    source.write_text(
        f"---\nname: {name}\ndescription: Fixture skill\ntriggers: [fixture]\nbackends: [{backend}]\n---\n{body}\n"
    )
    return folder


def test_default_roots_respect_explicit_multi_and_single_directory_options(
    skills, monkeypatch
):
    monkeypatch.setenv("AGENTFACTORY_SKILLS_DIRS", " /first : : /second ")
    monkeypatch.setenv("AGENTFACTORY_SKILLS_PATH", " /single ")
    assert module._default_skills_dirs() == ["/first", "/second"]
    assert module._default_skills_path() == "/single"
    monkeypatch.delenv("AGENTFACTORY_SKILLS_DIRS")
    assert module._default_skills_dirs() == ["/single"]
    monkeypatch.delenv("AGENTFACTORY_SKILLS_PATH")
    assert module._default_skills_dirs() == [str(skills.packages)]
    skills.legacy.mkdir()
    assert module._default_skills_dirs() == [str(skills.packages), str(skills.legacy)]
    assert module._default_skills_path() == str(skills.packages)
    skills.packages.rmdir()
    assert module._default_skills_path() == str(skills.legacy)
    skills.legacy.rmdir()
    assert module._default_skills_dirs() == [str(skills.packages)]


def test_empty_explicit_roots_keep_the_default_writable_alias(skills):
    manager = module.SkillsManager(skills_dirs=[])
    assert manager.skills_dirs == []
    assert manager.skills_path == str(skills.packages)
    assert not manager.load_skills()


def test_discovery_skips_internal_directories_and_preserves_package_first_precedence(
    skills,
):
    first = package(skills.packages, "duplicate")
    (skills.packages / "duplicate.md").write_text("# Legacy duplicate\n")
    for hidden in (".git", ".github", ".hub", "__pycache__"):
        package(skills.packages / hidden, hidden + "-skill")
    (skills.packages / "SKILL.MD").write_text("root metadata is not a flat skill")
    second_root = skills.root / "second"
    second = package(second_root, "duplicate", body="Second body")
    manager = module.SkillsManager(
        skills_dirs=[
            str(skills.root / "missing"),
            str(skills.packages),
            str(second_root),
        ]
    )
    assert manager.load_skills()
    assert list(manager.all_skills) == ["duplicate"]
    skill = manager.all_skills["duplicate"]
    assert skill.body_path == str(first / "SKILL.md")
    assert skill.source_dir == str(skills.packages)
    assert skill.package_dir != str(second)
    assert skill.content == ""
    assert manager.get_active_count() == 1


def test_empty_directory_does_not_report_loaded_skills(skills):
    assert not skills.manager.load_skills()
    assert skills.manager.get_metadata() == []
    assert skills.manager.list_skills() == []


def test_enabled_state_persists_across_reload_and_rebuilds_visible_catalog(skills):
    package(skills.packages)
    manager = skills.manager
    assert manager.load_skills()
    assert manager.set_skill_enabled("fixture-skill", False)[0]
    assert manager.get_active_count() == 0
    assert manager.get_skill_by_name("fixture-skill") is None
    assert manager.find_skill_by_name("fixture-skill").enabled is False
    assert manager.load_skills()
    assert not manager.all_skills["fixture-skill"].enabled
    assert manager.set_skill_enabled("fixture-skill", True)[0]
    assert manager.get_active_count() == 1
    assert manager.get_skill_by_name("fixture-skill").enabled
    assert manager.find_skill_by_name("absent") is None
    assert manager.get_metadata()[0]["skill_id"] == "fixture-skill"
    assert manager.list_skills()[0]["content"] == "Use fixture resources"


@pytest.mark.parametrize(
    "name,detail", [("", "Invalid skill_id"), ("unknown", "Skill not found")]
)
def test_invalid_enable_request_does_not_change_catalog(skills, name, detail):
    result, message = skills.manager.set_skill_enabled(name, False)
    assert not result and detail in message
    assert skills.manager.all_skills == {}


@pytest.mark.parametrize("name", ["", ".md", "..", "bad\\name", "x" * 101])
def test_invalid_install_name_creates_no_skill(skills, name):
    result, message = skills.manager.install_skill_from_markdown(name, "fixture")
    assert not result and message == "Invalid skill name"
    assert list(skills.packages.iterdir()) == []


def test_install_rejects_missing_destination(skills):
    skills.packages.rmdir()
    ok, message = skills.manager.install_skill_from_markdown("fixture", "body")
    assert not ok and "does not exist" in message


def test_install_overwrite_is_explicit_and_state_is_enabled(skills):
    manager = skills.manager
    ok, path = manager.install_skill_from_markdown("fixture.md", "# Fixture\nfirst")
    assert ok and Path(path) == skills.packages / "fixture.md"
    assert Path(path).read_text() == "# Fixture\nfirst"
    manager._save_skill_state("fixture", False)
    ok, message = manager.install_skill_from_markdown("fixture", "second")
    assert not ok and "already exists" in message
    assert Path(path).read_text().endswith("first")
    assert manager.install_skill_from_markdown(
        "fixture", "# Fixture\nsecond", overwrite=True
    ) == (True, path)
    assert manager.load_skills()
    assert manager.all_skills["fixture"].enabled
    assert manager.get_skill_by_name("Fixture").load_content().endswith("second")


def test_install_write_error_is_reported_without_false_success(skills, monkeypatch):
    monkeypatch.setattr(
        module, "open", Mock(side_effect=OSError("fixture read-only")), raising=False
    )
    ok, message = skills.manager.install_skill_from_markdown("fixture", "body")
    assert not ok and "fixture read-only" in message
    assert not (skills.packages / "fixture.md").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "plain body",
        "---\nmissing terminator",
        "---\n[not, a, map]\n---\nbody",
        "---\nname: [\n---\nbody",
    ],
)
def test_invalid_frontmatter_preserves_original_text(skills, raw):
    assert skills.manager._parse_frontmatter(raw) == ({}, raw)


def test_frontmatter_and_legacy_metadata_are_indexed_without_loading_bodies(skills):
    folder = package(skills.packages / "category", body="body")
    parsed = skills.manager._parse_skill_file(
        folder / "SKILL.md", base_dir=skills.packages
    )
    assert parsed.skill_id == "fixture-skill" and parsed.category == "category"
    assert parsed.source_file == str(Path("category/fixture-skill/SKILL.md"))
    assert parsed.backend_filter == ["openai"] and parsed.content == ""
    legacy = skills.packages / "legacy-name.md"
    legacy.write_text(
        "# 🤖 Legacy skill\n\n**Типичные фразы-триггеры:**\n- **first trigger**\n2. second trigger\n\n**Используйте этот скилл, когда:**\n- Useful fixture\n"
    )
    parsed = skills.manager._parse_skill_file(legacy)
    assert parsed.name == "Legacy skill"
    assert parsed.triggers == ["first trigger", "second trigger"]
    assert parsed.description == "Useful fixture"
    assert parsed.package_dir == "" and parsed.category == ""
    assert skills.manager._parse_skill_file(skills.packages / "missing.md") is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("first, second;third\nfourth", ["first", "second", "third", "fourth"]),
        (["first", " ", 2], ["first", "2"]),
        (None, []),
        (12, []),
    ],
)
def test_trigger_normalization_accepts_existing_metadata_shapes(
    skills, value, expected
):
    assert skills.manager._normalize_trigger_list(value) == expected


def test_legacy_metadata_falls_back_to_filename_and_bounds_description(skills):
    content = "**Используйте этот скилл, когда:** " + "x" * 300
    name, description, triggers = skills.manager._legacy_skill_metadata(
        content, "legacy_name"
    )
    assert name == "legacy name" and description == "x" * 240 + "..." and triggers == []


def test_settings_roundtrip_updates_all_roots_and_detects_later_changes(skills):
    manager = skills.manager
    directories = [str(skills.packages), str(skills.legacy)]
    manager.save_settings(
        str(skills.packages),
        auto_load=False,
        smart_selection=False,
        skills_dirs=directories,
    )
    loaded = manager.load_settings()
    assert loaded["skills_auto_load"] == loaded["skills_smart_selection"] == "false"
    assert json.loads(loaded["skills_dirs"]) == directories
    assert manager.skills_dirs == directories and manager.skills_path == directories[0]
    manager._last_mtime = manager._compute_skills_mtime(skills.packages)
    assert not manager.needs_reload()
    os.utime(skills.database, (manager._last_settings_mtime + 10,) * 2)
    assert manager.needs_reload()


def test_legacy_single_directory_setting_remains_supported(skills):
    manager = skills.manager
    manager.save_settings("/fixture/legacy", auto_load=True)
    loaded = manager.load_settings()
    assert loaded["skills_path"] == "/fixture/legacy"
    assert (
        manager.skills_dirs == ["/fixture/legacy"]
        and manager.skills_path == "/fixture/legacy"
    )


@pytest.mark.parametrize("raw", ["not JSON", "{}", "[]", "null"])
def test_invalid_multidir_settings_keep_current_roots(skills, raw):
    manager = skills.manager
    manager.save_settings(str(skills.packages))
    with module.open_db(module.DB_PATH) as conn:
        conn.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES ('skills_dirs',?,1)",
            (raw,),
        )
        conn.commit()
    assert manager.load_settings()["skills_dirs"] == raw
    assert manager.skills_dirs == [str(skills.packages)]


def test_source_file_change_triggers_reload(skills):
    folder = package(skills.packages)
    manager = skills.manager
    assert manager.load_skills()
    manager.load_settings()
    assert not manager.needs_reload()
    new_time = manager._last_mtime + 10
    os.utime(folder / "SKILL.md", (new_time, new_time))
    assert manager.needs_reload()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
@pytest.mark.parametrize("force_copy", [False, True])
def test_resource_replacement_supports_links_and_copy_fallback(
    skills, monkeypatch, kind, force_copy
):
    source = skills.root / "source"
    target = skills.root / "runtime/target"
    target.parent.mkdir()
    if kind == "directory":
        source.mkdir()
        (source / "fixture.txt").write_text("new")
        target.mkdir()
        (target / "obsolete.txt").write_text("old")
    else:
        source.write_text("new")
        if kind == "symlink":
            target.symlink_to(skills.root / "missing")
        else:
            target.write_text("old")
    if force_copy:
        monkeypatch.setattr(
            module.os, "symlink", Mock(side_effect=OSError("fixture links unavailable"))
        )
    skills.manager._link_or_copy_path(source, target)
    result = target / "fixture.txt" if kind == "directory" else target
    assert result.read_text() == "new"
    assert not (target / "obsolete.txt").exists()
    assert target.is_symlink() is not force_copy


def test_materialized_package_exposes_resources_but_only_injects_env_values(skills):
    folder = package(skills.packages)
    for name, content in {
        "scripts/run.py": "print('fixture')",
        "references/help.md": "help",
        "assets/image.txt": "image",
        "notes.md": "notes",
        "env/keys.env": "export FIXTURE_TOKEN='fixture-value'\n# comment\nignored\n =missing-key\nQUOTED=\"hello world\"\n",
    }.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    manager = skills.manager
    assert manager.load_skills()
    workspace = skills.root / "task"
    prompt, env = manager.build_skill_runtime_context(
        "$fixture-skill", backend="openai", workdir=str(workspace)
    )
    runtime = workspace / ".skill_runtime/fixture-skill"
    assert (runtime / "scripts/run.py").read_text() == "print('fixture')"
    assert (runtime / "notes.md").read_text() == "notes"
    assert not (runtime / "env").exists()
    assert env == {"FIXTURE_TOKEN": "fixture-value", "QUOTED": "hello world"}
    assert "FIXTURE_TOKEN" in prompt and "fixture-value" not in prompt
    assert "runtime_dir: " + str(runtime) in prompt
    assert "resource_dirs: scripts, references, assets" in prompt
    with module.open_db(module.DB_PATH) as conn:
        assert (
            conn.execute(
                "SELECT selected_count FROM skills_usage WHERE skill_id='fixture-skill'"
            ).fetchone()[0]
            == 1
        )
    second_prompt = manager.get_skills_for_prompt("$fixture-skill", backend="openai")
    assert "runtime_dir: " + str(folder) in second_prompt
    with module.open_db(module.DB_PATH) as conn:
        assert (
            conn.execute(
                "SELECT selected_count FROM skills_usage WHERE skill_id='fixture-skill'"
            ).fetchone()[0]
            == 2
        )


def test_flat_skill_runtime_and_empty_selection_require_no_resource_directory(skills):
    flat = module.Skill("flat", "flat", "", [], content="Fixture body")
    assert skills.manager._materialize_skill_runtime(flat, str(skills.root)) == {
        "runtime_dir": "",
        "bundled_files": [],
        "env_vars": {},
        "resource_dirs": [],
    }
    skills.manager.is_loaded = True
    skills.manager.skills = {"flat": flat}
    prompt, env = skills.manager.build_skill_runtime_context("unrelated")
    assert (
        "ДОСТУПНЫЕ SKILLS" in prompt and "ВЫБРАННЫЕ SKILLS" not in prompt and env == {}
    )
    assert skills.manager.build_skill_runtime_context("$flat")[0].endswith(
        "Fixture body\n"
    )
    skills.manager.skills.clear()
    assert skills.manager.build_skill_runtime_context("unrelated") == ("", {})


def test_available_summary_filters_backend_and_limits_alphabetically(skills):
    skills.manager.skills = {
        str(i): module.Skill(
            str(i), name, "", [], description="", backend_filter=["openai"]
        )
        for i, name in enumerate(["Zulu", "alpha", "Beta"])
    }
    result = skills.manager.get_available_skills_summary("openai", limit=2)
    assert result.index("alpha") < result.index("Beta")
    assert (
        "Описание не указано" in result
        and "ещё 1 skills" in result
        and "Zulu" not in result
    )
    assert skills.manager.get_available_skills_summary("anthropic") == ""


def test_one_unreadable_env_file_does_not_discard_other_files(skills, monkeypatch):
    folder = package(skills.packages)
    env = folder / "env"
    env.mkdir()
    (env / "first.env").write_text("FIRST=fixture")
    (env / "second.env").write_text("SECOND=available")
    skill = skills.manager._parse_skill_file(folder / "SKILL.md")
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == env / "first.env":
            raise OSError("fixture unreadable env")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert skills.manager._load_env_vars_from_skill(skill) == {"SECOND": "available"}


@pytest.mark.parametrize(
    "operation",
    [
        "_ensure_skills_tables",
        "_load_enabled_states",
        "_save_skill_state",
        "needs_reload",
        "load_skills",
        "save_settings",
        "load_settings",
        "_record_skill_selection",
    ],
)
def test_database_outages_follow_existing_best_effort_contract(
    skills, monkeypatch, operation, capsys
):
    def failed(*args, **kwargs):
        raise OSError("fixture database unavailable")

    monkeypatch.setattr(module, "open_db", failed)
    manager = skills.manager
    args = {
        "_save_skill_state": ("fixture", True),
        "save_settings": ("/fixture",),
        "_record_skill_selection": ([module.Skill("fixture", "fixture", "", [])],),
    }.get(operation, ())
    if operation == "needs_reload":
        monkeypatch.setattr(manager, "_compute_skills_mtime", failed)
        assert not manager.needs_reload()
    else:
        result = getattr(manager, operation)(*args)
        if operation in {"_load_enabled_states", "load_settings"}:
            assert result == {}
        elif operation == "load_skills":
            assert result is False
        else:
            assert result is None
        assert "fixture database unavailable" in capsys.readouterr().out


@pytest.mark.parametrize(
    "trigger_line",
    [
        "- **first trigger**",
        "* **first trigger**",
        "1. **first trigger**",
        "**first trigger**",
    ],
)
def test_legacy_bold_trigger_keeps_words_without_markdown_markers(skills, trigger_line):
    content = "**Типичные фразы-триггеры:**\n" + trigger_line + "\n\n"
    assert skills.manager._legacy_skill_metadata(content, "fixture")[2] == [
        "first trigger"
    ]
