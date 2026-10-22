import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.cli_chat import _append_entry, _load_entries, _session_path
from factory.core.cli_tasks import create_task
from factory.core.external_sessions import ExternalSessionRecord
from factory.core.mobile_bridge.service import (
    HIGH_RISK_TOOLS,
    MobileConflict,
    MobileInvalid,
    MobileNotFound,
    MobileService,
)
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core.steer_queue import drain_steer, pending_steer_count
from factory.core.user_input_store import create_user_input_request


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())
        self.service = MobileService(profile="cc-local-8081", workdir=self._tmp.name)
        self.session_id = "chat-20260916-120000-abc123"
        self._write_session(self.session_id, profile="cc-local-8081")

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _write_session(self, session_id: str, *, profile: str = "cc-local-8081") -> Path:
        path = _session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "meta",
                    "session_id": session_id,
                    "created_at": 1_700_000_000,
                    "profile": profile,
                    "model": "deepseek-v4.1-flash",
                    "provider": "opencode",
                    "cwd": "/home/example/AI/IgorekAgentFactory",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _task_row(self, task_id: str) -> dict:
        with sqlite3.connect(get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else {}

    def _set_status(self, task_id: str, status: str) -> None:
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))

    def _task_count(self) -> int:
        with sqlite3.connect(get_db_path()) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    def _make_blocked_task(self, *, request_id: str = "call_blocked_1", signature: str | None = None) -> str:
        signature = signature or 'Bash:{"command":"rm -rf build"}'
        created = create_task(
            text="промпт задачи",
            profile="cc-local-8081",
            metadata={
                "source": "mobile_bridge",
                "platform": "android",
                "session": self.session_id,
                "cwd": "/home/example/AI/IgorekAgentFactory",
            },
        )
        task_id = created["task_id"]
        blocked = [
            {
                "tool": "Bash",
                "input": {"command": "rm -rf build"},
                "target": "rm -rf build",
                "signature": signature,
                "tool_use_id": request_id,
                "source": "tool_call",
                "reason": "деструктивная команда",
                "session_rule": {
                    "rule_type": "exact_tool_call",
                    "tool": "Bash",
                    "signature": signature,
                },
            }
        ]
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'fail', approval_blocked_tools = ?, "
                "output_text = 'Для выполнения этого действия требуется подтверждение.' WHERE task_id = ?",
                (json.dumps(blocked, ensure_ascii=False), task_id),
            )
        return task_id


class FakeSkill:
    def __init__(self, skill_id: str, *, enabled: bool = True, backends: list[str] | None = None):
        self.skill_id = skill_id
        self.name = skill_id
        self.description = f"Описание {skill_id}"
        self.category = "tools"
        self.enabled = enabled
        self.backend_filter = list(backends or [])

    def supports_backend(self, backend: str) -> bool:
        return not self.backend_filter or backend in self.backend_filter

    def resource_dirs(self) -> list[str]:
        return ["scripts"]


class FakeSkillsManager:
    def __init__(self, skills: list[FakeSkill]):
        self.all_skills = {skill.skill_id: skill for skill in skills}

    def load_settings(self):
        return {}

    def load_skills(self):
        return True

    def set_skill_enabled(self, skill_id: str, enabled: bool):
        skill = self.all_skills.get(skill_id)
        if skill is None:
            return False, f"Skill not found: {skill_id}"
        skill.enabled = enabled
        return True, f"Skill '{skill_id}' {'enabled' if enabled else 'disabled'}"


class SessionListingTest(ServiceTestCase):
    def test_session_task_metadata_preserves_execution_boundaries(self):
        task = create_task(text="Fixture", profile="cc-local-8081", metadata={"session": self.session_id})
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status='ok', created_at=?, started_at=?, completed_at=? WHERE task_id=?",
                (1792628600, 1792629000, 1792629134, task["task_id"]))
        row = next(row for row in self.service.get_session(self.session_id)["tasks"] if row["task_id"] == task["task_id"])
        self.assertEqual(1792629000, row["started_at"])
        self.assertEqual(1792629134, row["completed_at"])

    def test_pinned_state_is_durable_and_old_pins_survive_list_limit(self):
        for index in range(55):
            self._write_session('chat-pin-'+str(index))
        result=self.service.update_session(self.session_id,pinned=True)
        self.assertTrue(result['pinned'])
        rows=self.service.sessions_overview(limit=5)['sessions']
        self.assertEqual(rows[0]['session_id'],self.session_id)
        self.assertTrue(rows[0]['pinned'])
        reopened=MobileService(profile='cc-local-8081',workdir=self.service.workdir)
        self.assertTrue(reopened.get_session(self.session_id)['meta']['pinned'])
        with self.assertRaises(MobileInvalid):reopened.update_session(self.session_id,pinned='yes')
        reopened.update_session(self.session_id,pinned=False)
        self.assertFalse(reopened.get_session(self.session_id)['meta']['pinned'])

    def test_list_sessions_marks_active_session(self):
        created = create_task(
            text="вопрос",
            profile="cc-local-8081",
            metadata={"session": self.session_id},
        )
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'processing' WHERE task_id = ?", (created["task_id"],))

        sessions = self.service.list_sessions()

        target = [item for item in sessions if item["session_id"] == self.session_id]
        self.assertEqual(len(target), 1)
        self.assertTrue(target[0]["active"])
        self.assertEqual(target[0]["running_task_id"], created["task_id"])
        self.assertEqual(target[0]["profile"], "cc-local-8081")

    def test_cancelling_session_exposes_exact_activity_status(self):
        created = create_task(
            text="останавливается",
            profile="cc-local-8081",
            metadata={"session": self.session_id},
        )
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'cancelling' WHERE task_id = ?",
                (created["task_id"],),
            )

        target = self.service.list_sessions()[0]

        self.assertTrue(target["active"])
        self.assertEqual(target["active_status"], "cancelling")

    def test_active_only_filters_idle_sessions(self):
        self.assertEqual(self.service.list_sessions(active_only=True), [])

    def test_get_session_returns_messages(self):
        _append_entry(_session_path(self.session_id), role="user", text="привет")
        _append_entry(_session_path(self.session_id), role="assistant", text="и тебе")

        payload = self.service.get_session(self.session_id)

        self.assertEqual(payload["meta"]["profile"], "cc-local-8081")
        self.assertEqual([m["text"] for m in payload["messages"]], ["привет", "и тебе"])
        self.assertIsNone(payload["running_task_id"])

    def test_session_context_window_matches_web_model_info(self):
        with patch("factory.web_ui.server._profile_model", return_value="test-model"), patch(
            "factory.web_ui.server._model_context_window", return_value=200000
        ) as window:
            payload = self.service.get_session(self.session_id)
            self.assertEqual(payload["context_window"], 200000)
            self.assertEqual(payload["model"], "test-model")
            window.assert_called_once_with("test-model")

    def test_get_session_missing_raises(self):
        with self.assertRaises(MobileNotFound):
            self.service.get_session("chat-does-not-exist")

    def test_overview_counts_all_idle_archive_candidates(self):
        active_session = "chat-20260917-130000-active"
        self._write_session(active_session)
        create_task(
            text="работает",
            profile="cc-local-8081",
            metadata={"session": active_session},
        )

        overview = self.service.sessions_overview(limit=1)

        self.assertEqual(len(overview["sessions"]), 1)
        self.assertEqual(overview["total"], 2)
        self.assertEqual(overview["archive_candidate_count"], 1)
        self.assertEqual(overview["active_count"], 1)

    def test_new_mcp_only_session_stays_visible_with_more_than_fifty_older_chats(self):
        from factory.core.cli_chat import _write_session_meta

        for index in range(55):
            path = self._write_session(f"older-{index}")
            with patch("factory.core.cli_chat.time.time", return_value=1_700_000_100 + index):
                _append_entry(path, "user", "ordinary message")
        new_id = "new-mcp-only"
        with patch("factory.core.cli_chat.time.time", return_value=1_700_000_200):
            _write_session_meta(_session_path(new_id), session_id=new_id, profile="cc-local-8081")

        overview = self.service.sessions_overview(limit=50)
        self.assertEqual(overview["sessions"][0]["session_id"], new_id)
        self.assertEqual(overview["sessions"][0]["updated_at"], 1_700_000_200)
        self.assertEqual(overview["sessions"][0]["message_count"], 0)
        self.assertEqual(self.service.get_session(new_id)["messages"], [])
        self.assertEqual(overview["sessions"][1]["session_id"], "older-54")
        self.assertEqual(overview["sessions"][1]["updated_at"], 1_700_000_154)
        self.assertEqual(overview["total"], 57)

    def test_overview_total_counts_filtered_sessions_before_limit(self):
        for index in range(3):
            self._write_session(f"chat-20260917-13100{index}-extra")

        overview = self.service.sessions_overview(limit=1)
        archived = self.service.sessions_overview(limit=1, archived_only=True)

        self.assertEqual(len(overview["sessions"]), 1)
        self.assertEqual(overview["total"], 4)
        self.assertEqual(archived["total"], 0)

    def test_archive_sessions_updates_idle_and_skips_active(self):
        active_session = "chat-20260917-130100-active"
        archived_session = "chat-20260917-130200-archived"
        self._write_session(active_session)
        self._write_session(archived_session)
        create_task(
            text="работает",
            profile="cc-local-8081",
            metadata={"session": active_session},
        )
        self.service.update_session(archived_session, archived=True)

        result = self.service.archive_sessions()

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["skipped_active"], 1)
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(result["session_ids"], [self.session_id])
        active = {item["session_id"] for item in self.service.list_sessions()}
        archived = {item["session_id"] for item in self.service.list_sessions(archived_only=True)}
        self.assertEqual(active, {active_session})
        self.assertEqual(archived, {self.session_id, archived_session})

    def test_session_options_include_saved_profiles_and_existing_recent_workdirs(self):
        profiles_dir = Path(self._tmp.name) / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "cc-glm.json").write_text('{"backend":"cc-backend"}', encoding="utf-8")
        (profiles_dir / "broken.json").write_text('{broken', encoding="utf-8")
        recent_dir = Path(self._tmp.name) / "recent-project"
        recent_dir.mkdir()
        recent_session = "chat-20260917-140000-options"
        path = self._write_session(recent_session, profile="cc-glm")
        lines = path.read_text(encoding="utf-8").splitlines()
        meta = json.loads(lines[0])
        meta["cwd"] = str(recent_dir)
        path.write_text(json.dumps(meta) + "\n", encoding="utf-8")

        options = self.service.session_options()

        self.assertEqual(options["default_profile"], "cc-local-8081")
        # Runtime profiles are prioritized first, followed by bundled profiles from the package
        # (for example, cc-wally from factory/config/profiles).
        self.assertEqual(options["profiles"][0], "cc-local-8081")
        self.assertIn("cc-glm", options["profiles"])
        self.assertIn("cc-wally", options["profiles"])
        self.assertIn(str(recent_dir.resolve()), options["workdirs"])
        self.assertNotIn("broken", options["profiles"])

    def test_import_external_session_creates_continuable_igorek_chat(self):
        workdir = self._tmp.name

        class FakeCatalog:
            def import_messages(self, source, session_id):
                return (
                    ExternalSessionRecord(
                        source=source,
                        session_id=session_id,
                        title="Previous session",
                        cwd=workdir,
                        created_at=1,
                        updated_at=2,
                    ),
                    [
                        {"role": "user", "text": "old question", "ts": 1},
                        {"role": "assistant", "text": "old answer", "ts": 2},
                    ],
                    3,
                )

        self.service.external_sessions_catalog = FakeCatalog()

        imported = self.service.import_external_session(
            "codex",
            "external-1",
            profile="cc-local-8081",
            cwd=self._tmp.name,
            idempotency_key="import-1",
        )

        self.assertFalse(imported["duplicate"])
        self.assertEqual(imported["imported_messages"], 2)
        self.assertEqual(imported["skipped_messages"], 3)
        detail = imported["session"]
        self.assertEqual([item["text"] for item in detail["messages"]], ["old question", "old answer"])
        self.assertEqual(detail["meta"]["origin_source"], "codex")
        self.assertEqual(detail["meta"]["origin_session_id"], "external-1")

    def test_import_tools_survive_reload_and_reach_next_task_context(self):
        workdir = self._tmp.name
        class Catalog:
            def import_messages(self, source, session_id):
                return (
                    ExternalSessionRecord(source=source, session_id=session_id, title="History", cwd=workdir, created_at=1, updated_at=2),
                    [
                        {"role": "user", "text": "original", "ts": 10, "history_id": "u", "order": 0},
                        {"role": "tool", "text": "Bash", "ts": 11, "kind": "tool_call", "history_id": "c", "tool_use_id": "call1", "tool_name": "Bash", "tool_input": {"command": "pytest"}},
                        {"role": "tool", "text": "3 failed", "ts": 12, "kind": "tool_result", "history_id": "r", "tool_use_id": "call1", "tool_output": "3 failed"},
                        {"role": "assistant", "text": "investigate failures", "ts": 13, "history_id": "a"},
                    ], 0,
                )
        self.service.external_sessions_catalog = Catalog()
        imported = self.service.import_external_session("codex", "source", cwd=workdir)
        session_id = imported["session"]["session_id"]
        messages = self.service.get_session(session_id)["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "tool", "tool", "assistant"])
        self.assertEqual([message["ts"] for message in messages], [10, 11, 12, 13])
        result = self.service.send_message(session_id=session_id, text="continue")
        row = self._task_row(result["task_id"])
        metadata = row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        self.assertTrue(metadata["structured_session_history"])
        context = json.dumps(metadata["history_messages"], ensure_ascii=False)
        self.assertIn("pytest", context)
        self.assertIn("3 failed", context)
        self.assertIn("original", context)

    def test_session_reload_keeps_more_than_three_hundred_imported_entries(self):
        path = _session_path(self.session_id)
        for index in range(310):
            _append_entry(path, role="tool", text=str(index), extra={"history_id": str(index), "kind": "tool_result"})
        detail = self.service.get_session(self.session_id)
        self.assertEqual(len(detail["messages"]), 310)


class ExtensionsServiceTest(ServiceTestCase):
    def test_extensions_return_real_skill_and_mcp_metadata(self):
        manager = FakeSkillsManager([FakeSkill("news_rss"), FakeSkill("pdf", enabled=False)])
        mcp = [{"name": "browser", "enabled": True, "tools_hint": ["open"]}]
        with (
            patch("factory.core.mobile_bridge.service.runtime_skills_manager", manager),
            patch("factory.core.mobile_bridge.service.list_mcp_capabilities_for_profile", return_value=mcp),
        ):
            payload = self.service.extensions(profile="cc-local-8081")

        self.assertEqual([item["skill_id"] for item in payload["skills"]], ["news_rss", "pdf"])
        self.assertTrue(payload["skills"][0]["enabled"])
        self.assertFalse(payload["skills"][1]["enabled"])
        self.assertEqual(payload["mcp_servers"], mcp)
        self.assertFalse(payload["hooks_available"])
        self.assertEqual(payload["hooks"], [])

    def test_set_skill_enabled_updates_runtime_state(self):
        manager = FakeSkillsManager([FakeSkill("news_rss", enabled=False)])
        with patch("factory.core.mobile_bridge.service.runtime_skills_manager", manager):
            payload = self.service.set_skill_enabled("news_rss", enabled=True)

        self.assertTrue(payload["skill"]["enabled"])

    def test_set_unknown_skill_is_not_found(self):
        manager = FakeSkillsManager([])
        with patch("factory.core.mobile_bridge.service.runtime_skills_manager", manager):
            with self.assertRaises(MobileNotFound):
                self.service.set_skill_enabled("missing", enabled=True)


class SendMessageTest(ServiceTestCase):
    def test_continue_preserves_large_specification_and_previous_answer(self):
        specification = "Техническое задание\n" + "требование\n" * 30_000
        answer = "Предварительный ответ\n" + "разведка\n" * 1_000
        path = _session_path(self.session_id)
        _append_entry(path, "user", specification)
        _append_entry(path, "assistant", answer)

        result = self.service.send_message(self.session_id, "Продолжай по ТЗ")

        prompt = self._task_row(result["task_id"])["input_text"]
        self.assertIn("user: " + specification.rstrip(), prompt)
        self.assertIn("assistant: " + answer.rstrip(), prompt)
        self.assertIn("Последнее сообщение пользователя:\nПродолжай по ТЗ", prompt)

    def test_explicit_history_character_limit_still_works(self):
        from factory.core.cli_chat import _history_tail

        entries = [{"role": "user", "text": "x" * 20_000},
                   {"role": "assistant", "text": "ответ"}]
        self.assertEqual(_history_tail(entries, 20, 20_000), entries[-1:])
        self.assertEqual(_history_tail(entries, 20, None), entries)

    def test_plan_mode_is_stored_in_task_metadata(self):
        result = self.service.send_message(self.session_id, "составь план", plan_mode=True)

        metadata = json.loads(self._task_row(result["task_id"])["metadata"])
        self.assertIs(metadata["plan_mode"], True)

    def test_default_message_keeps_plan_mode_unset(self):
        result = self.service.send_message(self.session_id, "выполни задачу")

        metadata = json.loads(self._task_row(result["task_id"])["metadata"])
        self.assertNotIn("plan_mode", metadata)

    def test_long_message_is_not_truncated_in_replayed_history(self):
        text = "Длинное сообщение. " * 100
        result = self.service.send_message(self.session_id, text)
        events = self.service.session_events(self.session_id)
        submitted = next(event for event in events if event["kind"] == "task.submitted")
        self.assertEqual(submitted["payload"]["text"], text.strip())
        self.assertEqual(submitted["task_id"], result["task_id"])

    def test_send_creates_task_and_appends_user_entry(self):
        result = self.service.send_message(self.session_id, "сделай отчёт")

        self.assertFalse(result["duplicate"])
        row = self._task_row(result["task_id"])
        self.assertEqual(row["status"], "queued")
        self.assertEqual(row["profile"], "cc-local-8081")
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["session"], self.session_id)
        self.assertEqual(metadata["source"], "mobile_bridge")
        self.assertEqual(metadata["original_message_text"], "сделай отчёт")
        entries = [
            json.loads(line)
            for line in _session_path(self.session_id).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(entries[-1]["role"], "user")
        self.assertEqual(entries[-1]["text"], "сделай отчёт")

    def test_recent_image_attachment_is_replayed_to_next_session_task(self):
        session_path = _session_path(self.session_id)
        meta = json.loads(session_path.read_text(encoding="utf-8").splitlines()[0])
        meta["cwd"] = self._tmp.name
        session_path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
        image_path = Path(self._tmp.name) / "mifit.jpg"
        image_path.write_bytes(b"\xff\xd8\xff\xd9")

        first = self.service.send_message(
            self.session_id,
            "прочитай скриншот",
            input_files=[str(image_path)],
        )
        self._set_status(first["task_id"], "ok")

        second = self.service.send_message(self.session_id, "продолжи по скриншоту")

        self.assertEqual(
            json.loads(self._task_row(second["task_id"])["input_files"]),
            [str(image_path.resolve())],
        )

    def test_selected_skill_is_validated_and_attached_structurally(self):
        manager = FakeSkillsManager([FakeSkill("news_rss")])
        with patch("factory.core.mobile_bridge.service.runtime_skills_manager", manager):
            result = self.service.send_message(
                self.session_id,
                "покажи новости",
                skill_ids=["news_rss"],
            )

        row = self._task_row(result["task_id"])
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["selected_skills"], ["news_rss"])
        self.assertIn("/news_rss", row["input_text"])
        entries = [
            json.loads(line)
            for line in _session_path(self.session_id).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(entries[-1]["text"], "покажи новости")
        self.assertEqual(entries[-1]["skills"], ["news_rss"])

    def test_selected_skills_reach_live_event_and_history(self):
        manager = FakeSkillsManager([FakeSkill("news_rss")])
        with patch("factory.core.mobile_bridge.service.runtime_skills_manager", manager):
            self.service.send_message(
                self.session_id,
                "покажи новости",
                skill_ids=["news_rss"],
            )

        submitted = [
            event for event in self.service.session_events(self.session_id)
            if event["kind"] == "task.submitted"
        ]
        self.assertEqual(submitted[-1]["payload"]["text"], "покажи новости")
        self.assertEqual(submitted[-1]["payload"]["selected_skills"], ["news_rss"])
        history = self.service.get_session(self.session_id)["messages"]
        self.assertEqual(history[-1]["text"], "покажи новости")
        self.assertEqual(history[-1]["skills"], ["news_rss"])

    def test_message_without_skills_keeps_skill_fields_empty(self):
        self.service.send_message(self.session_id, "обычное сообщение")

        submitted = [
            event for event in self.service.session_events(self.session_id)
            if event["kind"] == "task.submitted"
        ]
        self.assertEqual(submitted[-1]["payload"]["selected_skills"], [])
        history = self.service.get_session(self.session_id)["messages"]
        self.assertEqual(history[-1]["text"], "обычное сообщение")
        self.assertEqual(history[-1]["skills"], [])

    def test_disabled_selected_skill_is_rejected(self):
        manager = FakeSkillsManager([FakeSkill("news_rss", enabled=False)])
        with patch("factory.core.mobile_bridge.service.runtime_skills_manager", manager):
            with self.assertRaises(MobileInvalid):
                self.service.send_message(
                    self.session_id,
                    "покажи новости",
                    skill_ids=["news_rss"],
                )

    def test_idempotency_key_prevents_duplicate_task(self):
        first = self.service.send_message(self.session_id, "задача", idempotency_key="key-1")
        second = self.service.send_message(self.session_id, "задача", idempotency_key="key-1")

        self.assertEqual(first["task_id"], second["task_id"])
        self.assertTrue(second["duplicate"])
        with sqlite3.connect(get_db_path()) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(count, 1)

    def test_empty_text_rejected(self):
        with self.assertRaises(MobileInvalid):
            self.service.send_message(self.session_id, "   ")

    def test_missing_profile_rejected(self):
        session_id = "chat-20260916-130000-noProf"
        path = _session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"type": "meta", "session_id": session_id, "profile": ""}) + "\n",
            encoding="utf-8",
        )
        service = MobileService(profile="", workdir="/tmp")

        with self.assertRaises(MobileInvalid):
            service.send_message(session_id, "текст")


class SteeredMessagesTest(ServiceTestCase):
    """Force-ahead and cancellation of a session message placed in the queue."""

    def _queued_task(self) -> tuple[str, str]:
        task_id = self._make_blocked_task()
        self._set_status(task_id, "waiting_approval")
        queued = self.service.send_message(self.session_id, "второе сообщение")
        self.assertTrue(queued["steered"])
        return task_id, queued["message_id"]

    def test_send_now_emits_prioritized_event_and_reports_pending(self):
        task_id, message_id = self._queued_task()

        result = self.service.send_message_now(self.session_id, task_id, message_id)

        self.assertTrue(result["prioritized"])
        events = [event for event in self.service.session_events(self.session_id)
                  if event["kind"] == "task.message_prioritized"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["message_id"], message_id)
        # Priority messages remain in the queue and are delivered first.
        self.assertEqual(pending_steer_count(task_id), 1)
        delivered = drain_steer(task_id)
        self.assertEqual([item["message_id"] for item in delivered], [message_id])

    def test_cancel_message_removes_it_and_emits_event(self):
        task_id, message_id = self._queued_task()

        result = self.service.cancel_message(self.session_id, task_id, message_id)

        self.assertTrue(result["cancelled"])
        self.assertEqual(pending_steer_count(task_id), 0)
        kinds = [event["kind"] for event in self.service.session_events(self.session_id)]
        self.assertEqual(kinds.count("task.message_cancelled"), 1)

    def test_delivered_message_is_reported_not_cancelled(self):
        task_id, message_id = self._queued_task()
        drain_steer(task_id)

        result = self.service.cancel_message(self.session_id, task_id, message_id)

        self.assertFalse(result["cancelled"])
        self.assertTrue(result["delivered"])
        self.assertEqual(result["reason"], "message_already_delivered")
        kinds = [event["kind"] for event in self.service.session_events(self.session_id)]
        self.assertEqual(kinds.count("task.message_cancelled"), 0)

    def test_cancel_message_rejects_foreign_session_and_task(self):
        task_id, message_id = self._queued_task()

        with self.assertRaises(MobileNotFound):
            self.service.cancel_message("chat-чужой", task_id, message_id)
        with self.assertRaises(MobileNotFound):
            self.service.cancel_message(self.session_id, "task-нет", message_id)

    def test_cancel_message_requires_exact_ids(self):
        with self.assertRaises(MobileInvalid):
            self.service.cancel_message(self.session_id, "", "")


class ApprovalsTest(ServiceTestCase):
    def test_concurrent_decisions_resume_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading

        task_id = self._make_blocked_task()
        start = threading.Barrier(2)

        def decide():
            start.wait(timeout=5)
            try:
                return self.service.resolve_approval(task_id, "call_blocked_1", "allow_once")
            except MobileConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: decide(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)

    def test_non_pending_task_cannot_resume_saved_tool(self):
        task_id = self._make_blocked_task()
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'ok' WHERE task_id = ?", (task_id,))
        with self.assertRaises(MobileConflict):
            self.service.resolve_approval(task_id, "call_blocked_1", "allow_once")

    def test_pending_tool_approval_is_listed(self):
        task_id = self._make_blocked_task()

        approvals = self.service.list_approvals()

        self.assertEqual(len(approvals), 1)
        approval = approvals[0]
        self.assertEqual(approval["kind"], "tool")
        self.assertEqual(approval["request_id"], "call_blocked_1")
        self.assertEqual(approval["tool"], "Bash")
        self.assertEqual(approval["task_id"], task_id)
        self.assertTrue(approval["dangerous"])

    def test_approve_once_requeues_same_task_with_signature(self):
        task_id = self._make_blocked_task()
        before_count = self._task_count()

        result = self.service.resolve_approval(task_id, "call_blocked_1", "allow_once")

        self.assertEqual(result["resumed_task_id"], task_id)
        self.assertEqual(self._task_count(), before_count)
        resumed = self._task_row(task_id)
        metadata = json.loads(resumed["metadata"])
        self.assertEqual(metadata["approved_tool_calls"], ['Bash:{"command":"rm -rf build"}'])
        self.assertEqual(metadata["forced_tool_call"]["name"], "Bash")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["input_text"], "промпт задачи")
        self.assertEqual(json.loads(resumed["approval_blocked_tools"]), [])

    def test_approve_session_adds_session_rule(self):
        task_id = self._make_blocked_task()

        result = self.service.resolve_approval(task_id, "call_blocked_1", "allow_session")

        self.assertEqual(result["resumed_task_id"], task_id)
        metadata = json.loads(self._task_row(task_id)["metadata"])
        self.assertEqual(len(metadata["approved_session_rules"]), 1)
        self.assertEqual(metadata["approved_session_rules"][0]["rule_type"], "exact_tool_call")

    def test_approve_session_rule_is_inherited_by_next_message(self):
        task_id = self._make_blocked_task()
        result = self.service.resolve_approval(task_id, "call_blocked_1", "allow_session")
        self._set_status(result["resumed_task_id"], "ok")

        next_turn = self.service.send_message(self.session_id, "продолжай")

        metadata = json.loads(self._task_row(next_turn["task_id"])["metadata"])
        self.assertEqual(len(metadata["approved_session_rules"]), 1)
        self.assertEqual(
            metadata["approved_session_rules"][0]["signature"],
            'Bash:{"command":"rm -rf build"}',
        )

    def test_session_rule_is_not_inherited_from_non_mobile_task(self):
        create_task(
            text="foreign",
            profile="cc-local-8081",
            metadata={
                "source": "telegram",
                "session": self.session_id,
                "approved_session_rules": [
                    {"rule_type": "exact_tool_call", "signature": "Bash:foreign"}
                ],
            },
        )

        next_turn = self.service.send_message(self.session_id, "продолжай")

        metadata = json.loads(self._task_row(next_turn["task_id"])["metadata"])
        self.assertNotIn("approved_session_rules", metadata)

    def test_approval_with_queued_message_keeps_one_task_and_one_pending_steer(self):
        task_id = self._make_blocked_task()
        self._set_status(task_id, "waiting_approval")
        queued = self.service.send_message(self.session_id, "после подтверждения продолжай")

        result = self.service.resolve_approval(task_id, "call_blocked_1", "allow_once")

        self.assertEqual(queued["task_id"], task_id)
        self.assertEqual(result["resumed_task_id"], task_id)
        self.assertEqual(self._task_count(), 1)
        self.assertEqual(pending_steer_count(task_id), 1)
        kinds = [event["kind"] for event in self.service.session_events(self.session_id)]
        self.assertEqual(kinds.count("task.submitted"), 0)
        self.assertEqual(kinds.count("task.message_added"), 1)
        self.assertEqual(kinds.count("approval.resolved"), 1)
        self.assertEqual(kinds.count("task.requeued"), 1)

    def test_stale_approval_cannot_be_reapplied(self):
        task_id = self._make_blocked_task()
        self.service.resolve_approval(task_id, "call_blocked_1", "allow_once")

        with self.assertRaises(MobileConflict):
            self.service.resolve_approval(task_id, "call_blocked_1", "allow_once")

    def test_approvals_are_deduplicated_per_session(self):
        first = self._make_blocked_task(request_id="call_old")
        second = self._make_blocked_task(request_id="call_new")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET created_at = created_at - 60 WHERE task_id = ?", (first,))

        approvals = self.service.list_approvals()

        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["request_id"], "call_new")
        self.assertEqual(approvals[0]["task_id"], second)

    def test_previous_session_approval_is_superseded_by_newer(self):
        self._make_blocked_task(request_id="call_old")
        second = self._make_blocked_task(request_id="call_new")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET created_at = created_at + 10 WHERE task_id = ?", (second,))

        approvals = self.service.list_approvals(session_id=self.session_id)

        self.assertEqual([item["task_id"] for item in approvals], [second])

    def test_resolving_latest_approval_does_not_reveal_older_one(self):
        self._make_blocked_task(request_id="call_old")
        latest = self._make_blocked_task(request_id="call_new")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET created_at = created_at + 10 WHERE task_id = ?", (latest,))

        self.service.resolve_approval(latest, "call_new", "reject")

        self.assertEqual(self.service.list_approvals(session_id=self.session_id), [])

    def test_newer_completed_task_supersedes_old_approval(self):
        blocked = self._make_blocked_task()
        completed = create_task(
            text="следующая задача",
            profile="cc-local-8081",
            metadata={"session": self.session_id},
        )
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET created_at = created_at - 10 WHERE task_id = ?", (blocked,))
            conn.execute("UPDATE tasks SET status = 'ok' WHERE task_id = ?", (completed["task_id"],))

        self.assertEqual(self.service.list_approvals(session_id=self.session_id), [])

    def test_approvals_capped_by_limit(self):
        for index in range(4):
            task_id = self._make_blocked_task(request_id=f"call_{index}")
            session = f"chat-20260916-limit-{index}"
            with sqlite3.connect(get_db_path()) as conn:
                conn.execute(
                    "UPDATE tasks SET metadata = ? WHERE task_id = ?",
                    (json.dumps({"session": session}), task_id),
                )

        approvals = self.service.list_approvals(limit=2)

        self.assertEqual(len(approvals), 2)

    def test_unknown_request_id_is_conflict(self):
        task_id = self._make_blocked_task()

        with self.assertRaises(MobileConflict):
            self.service.resolve_approval(task_id, "call_other", "allow_once")

    def test_reject_finalizes_task_and_marks_resolved(self):
        task_id = self._make_blocked_task()

        result = self.service.resolve_approval(task_id, "call_blocked_1", "reject")

        self.assertIsNone(result["resumed_task_id"])
        row = self._task_row(task_id)
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["mobile_resolved_approvals"]["call_blocked_1"], "reject")
        self.assertEqual(row["status"], "fail")
        with self.assertRaises(MobileConflict):
            self.service.resolve_approval(task_id, "call_blocked_1", "reject")

    def test_reject_waiting_approval_finishes_without_cancelling_state(self):
        task_id = self._make_blocked_task()
        self._set_status(task_id, "waiting_approval")

        result = self.service.resolve_approval(task_id, "call_blocked_1", "reject")

        self.assertIsNone(result["resumed_task_id"])
        row = self._task_row(task_id)
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["error_message"], "approval rejected by user")
        self.assertIsNotNone(row["completed_at"])

    def test_reject_with_queued_message_continues_only_the_new_message(self):
        task_id = self._make_blocked_task()
        self._set_status(task_id, "waiting_approval")
        self.service.send_message(self.session_id, "не выполняй команду, просто продолжай")

        self.service.resolve_approval(task_id, "call_blocked_1", "reject")
        resumed = self.service.resume_pending_steers()

        self.assertEqual(resumed, 1)
        self.assertEqual(self._task_count(), 1)
        row = self._task_row(task_id)
        self.assertEqual(row["status"], "queued")
        self.assertIn("не выполняй команду", row["input_text"])
        metadata = json.loads(row["metadata"])
        self.assertNotIn("forced_tool_call", metadata)
        self.assertNotIn("approved_tool_calls", metadata)
        self.assertEqual(pending_steer_count(task_id), 0)

    def test_invalid_decision_rejected(self):
        task_id = self._make_blocked_task()

        with self.assertRaises(MobileInvalid):
            self.service.resolve_approval(task_id, "call_blocked_1", "maybe")

    def test_resolved_approval_disappears_from_pending(self):
        task_id = self._make_blocked_task()
        self.service.resolve_approval(task_id, "call_blocked_1", "allow_once")

        self.assertEqual(self.service.list_approvals(), [])


class ApprovalSettingsTest(ServiceTestCase):
    def test_approvals_are_enabled_by_default_and_setting_is_durable(self):
        self.assertEqual(self.service.approval_settings(), {"enabled": True})

        disabled = self.service.set_approvals_enabled(False)

        self.assertFalse(disabled["enabled"])
        self.assertEqual(self.service.approval_settings(), {"enabled": False})
        self.assertTrue(self.service.set_approvals_enabled(True)["enabled"])
        self.assertEqual(self.service.approval_settings(), {"enabled": True})

    def test_disabling_approvals_resumes_pending_mobile_task_without_duplicate(self):
        task_id = self._make_blocked_task()
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'waiting_approval', metadata = ? WHERE task_id = ?",
                (
                    json.dumps(
                        {
                            "source": "mobile_bridge",
                            "platform": "android",
                            "session": self.session_id,
                            "cwd": "/home/example/AI/IgorekAgentFactory",
                        }
                    ),
                    task_id,
                ),
            )

        result = self.service.set_approvals_enabled(False)

        self.assertFalse(result["enabled"])
        self.assertEqual(result["resumed"], 1)
        self.assertEqual(self._task_count(), 1)
        self.assertEqual(self._task_row(task_id)["status"], "queued")

    def test_disabling_approvals_resumes_all_legacy_duplicates_in_session(self):
        first = self._make_blocked_task(request_id="call_first")
        second = self._make_blocked_task(request_id="call_second")
        self._set_status(first, "waiting_approval")
        self._set_status(second, "waiting_approval")

        result = self.service.set_approvals_enabled(False)

        self.assertEqual(result["resumed"], 2)
        self.assertEqual(self._task_row(first)["status"], "queued")
        self.assertEqual(self._task_row(second)["status"], "queued")


class UserInputTest(ServiceTestCase):
    def _waiting_task_with_request(self) -> tuple[str, str]:
        created = create_task(
            text="нужен доступ",
            profile="cc-local-8081",
            metadata={"session": self.session_id},
        )
        task_id = created["task_id"]
        request = create_user_input_request(
            task_id=task_id,
            payload={"title": "Доступ к MCP", "context": {"server_name": "tavily", "reason": "поиск"}},
        )
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'waiting_approval' WHERE task_id = ?", (task_id,))
        return task_id, str(request["request_id"])

    def test_pending_user_input_listed_as_approval(self):
        task_id, request_id = self._waiting_task_with_request()

        approvals = self.service.list_approvals()

        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["kind"], "user_input")
        self.assertEqual(approvals[0]["request_id"], request_id)
        self.assertEqual(approvals[0]["tool"], "mcp_access")
        self.assertTrue(approvals[0]["dangerous"])

    def test_cancel_user_input_resolves_and_cancels_task(self):
        task_id, request_id = self._waiting_task_with_request()

        result = self.service.resolve_user_input(task_id, request_id, response=None, cancelled=True)

        self.assertTrue(result["cancelled"])
        row = self._task_row(task_id)
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["error_message"], "user input rejected by user")
        self.assertIsNotNone(row["completed_at"])
        with self.assertRaises(MobileConflict):
            self.service.resolve_user_input(task_id, request_id, response={}, cancelled=True)

    def test_answering_plain_user_input_requeues_same_task(self):
        created = create_task(
            text="нужен ответ",
            profile="cc-local-8081",
            metadata={"session": self.session_id},
        )
        task_id = created["task_id"]
        request = create_user_input_request(
            task_id=task_id,
            payload={"title": "Выбор", "questions": []},
        )
        request_id = str(request["request_id"])
        self._set_status(task_id, "waiting_approval")

        result = self.service.resolve_user_input(
            task_id,
            request_id,
            response={"answers": {}},
            cancelled=False,
        )

        self.assertTrue(result["resumed"])
        self.assertEqual(self._task_count(), 1)
        self.assertEqual(self._task_row(task_id)["status"], "queued")

    def test_mcp_enabling_requires_only_the_explicit_affirmative_choice(self):
        cases = [([], False), (['Не включать'], False), (['Включить', 'Не включать'], False),
                 (['unknown'], False), (['Включить'], True)]
        for selected, expected in cases:
            with self.subTest(selected=selected):
                task_id, request_id = self._waiting_task_with_request()
                response = {'answers': {'mcp_enable_decision': {'selected_options': selected}}}
                with patch('factory.core.mcp_config.accept_mcp_enable_suggestion') as enable:
                    result = self.service.resolve_user_input(task_id, request_id, response=response)
                self.assertEqual(enable.called, expected)
                self.assertTrue(result['resumed'])


class CancelTaskTest(ServiceTestCase):
    def test_cancel_queued_task_marks_fail(self):
        created = create_task(text="долгая", profile="cc-local-8081", metadata={"session": self.session_id})

        with self.assertLogs("factory.core.mobile_bridge.service", level="WARNING") as captured:
            result = self.service.cancel_task(created["task_id"])

        self.assertIn("source=task_cancel mode=cancel", "\n".join(captured.output))
        self.assertTrue(result["cancelled"])
        self.assertEqual(self._task_row(created["task_id"])["status"], "fail")
        self.assertTrue(result["terminal"])
        self.assertEqual(
            [event["kind"] for event in self.service.session_events(self.session_id)],
            ["task.cancel_requested", "task.cancelled"],
        )

    def test_stop_forces_dead_letter(self):
        created = create_task(text="зависла", profile="cc-local-8081", metadata={"session": self.session_id})
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'processing' WHERE task_id = ?", (created["task_id"],))

        self.service.cancel_task(created["task_id"], mode="stop")

        row = self._task_row(created["task_id"])
        self.assertEqual(row["status"], "dead_letter")
        self.assertIn("mobile stop", row["error_message"])
        self.assertIsNotNone(row["completed_at"])
        terminal = self.service.session_events(self.session_id)[-1]
        self.assertEqual(terminal["kind"], "task.cancelled")
        self.assertEqual(terminal["payload"]["status"], "dead_letter")

    def test_stop_session_finalizes_every_active_task(self):
        first = create_task(
            text="первая",
            profile="cc-local-8081",
            metadata={"session": self.session_id},
        )
        second = create_task(
            text="вторая",
            profile="cc-local-8081",
            metadata={"session": self.session_id},
        )
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'processing' WHERE task_id = ?",
                (first["task_id"],),
            )
            conn.execute(
                "UPDATE tasks SET status = 'cancelling' WHERE task_id = ?",
                (second["task_id"],),
            )

        result = self.service.cancel_session(self.session_id, mode="stop")

        self.assertEqual(result["affected"], 2)
        self.assertEqual(result["terminal"], 2)
        self.assertEqual(self._task_row(first["task_id"])["status"], "dead_letter")
        self.assertEqual(self._task_row(second["task_id"])["status"], "dead_letter")
        self.assertFalse(self.service.list_sessions()[0]["active"])
        self.assertEqual(
            [event["kind"] for event in self.service.session_events(self.session_id)],
            ["session.cancel_requested", "session.cancelled"],
        )

    def test_invalid_mode_rejected(self):
        created = create_task(text="x", profile="cc-local-8081", metadata={"session": self.session_id})

        with self.assertRaises(MobileInvalid):
            self.service.cancel_task(created["task_id"], mode="explode")

    def test_cancel_unknown_task_raises(self):
        with self.assertRaises(MobileNotFound):
            self.service.cancel_task("no-such-task")


class SessionSteerTest(ServiceTestCase):
    """A message sent to a busy session keeps the active task ID."""

    def _set_status(self, task_id: str, status: str) -> None:
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ?",
                (status, task_id),
            )

    def _journal(self) -> list[dict]:
        return [
            json.loads(line)
            for line in _session_path(self.session_id).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _task_count(self) -> int:
        with sqlite3.connect(get_db_path()) as conn:
            return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    def test_message_during_running_task_reuses_same_task_id(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        second = self.service.send_message(self.session_id, "второе сообщение")

        self.assertEqual(second["task_id"], first["task_id"])
        self.assertFalse(second["duplicate"])
        self.assertTrue(second["steered"])
        self.assertEqual(self._task_count(), 1)
        self.assertEqual(pending_steer_count(first["task_id"]), 1)

    def test_steered_message_is_stored_as_separate_journal_entry(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        second = self.service.send_message(self.session_id, "второе сообщение")

        users = [entry for entry in self._journal() if entry.get("role") == "user"]
        self.assertEqual([entry["text"] for entry in users], ["первое сообщение", "второе сообщение"])
        self.assertNotEqual(users[0]["message_id"], users[1]["message_id"])
        self.assertEqual(users[1]["task_id"], first["task_id"])
        self.assertEqual(second["message_id"], users[1]["message_id"])
        self.assertTrue(users[1]["steer"])

    def test_steered_message_emits_added_event_not_new_submission(self):
        self.service.send_message(self.session_id, "первое сообщение")
        self.service.send_message(self.session_id, "второе сообщение")

        events = self.service.session_events(self.session_id)
        submitted = [event for event in events if event["kind"] == "task.submitted"]
        added = [event for event in events if event["kind"] == "task.message_added"]
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["payload"]["text"], "первое сообщение")
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["payload"]["text"], "второе сообщение")
        self.assertEqual(added[0]["payload"]["pending"], 1)

    def test_session_overview_keeps_single_running_task(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        self.service.send_message(self.session_id, "второе сообщение")

        detail = self.service.get_session(self.session_id)
        self.assertEqual(detail["running_task_id"], first["task_id"])
        self.assertEqual(len(detail["tasks"]), 1)
        self.assertEqual([m["text"] for m in detail["messages"] if m["role"] == "user"],
                         ["первое сообщение", "второе сообщение"])

    def test_session_history_restores_pending_marker_until_steer_is_delivered(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        second = self.service.send_message(self.session_id, "второе сообщение")

        pending = self.service.get_session(self.session_id)["messages"][-1]
        self.assertEqual(pending["message_id"], second["message_id"])
        self.assertTrue(pending["queued"])

        drain_steer(first["task_id"])

        delivered = self.service.get_session(self.session_id)["messages"][-1]
        self.assertFalse(delivered["queued"])

    def test_finished_session_starts_new_task(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        self._set_status(first["task_id"], "ok")

        second = self.service.send_message(self.session_id, "новая задача")

        self.assertNotEqual(second["task_id"], first["task_id"])
        self.assertFalse(second["steered"])
        self.assertEqual(self._task_count(), 2)

    def test_pending_steer_requeues_same_task_after_finish(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        self.service.send_message(self.session_id, "второе сообщение")
        self._set_status(first["task_id"], "ok")

        self.assertEqual(self.service.resume_pending_steers(), 1)

        row = self._task_row(first["task_id"])
        self.assertEqual(row["status"], "queued")
        self.assertIn("второе сообщение", row["input_text"])
        self.assertEqual(self._task_count(), 1)
        self.assertEqual(pending_steer_count(first["task_id"]), 0)
        requeued = [
            event for event in self.service.session_events(self.session_id)
            if event["kind"] == "task.requeued"
        ]
        self.assertEqual(len(requeued), 1)
        self.assertEqual(requeued[0]["task_id"], first["task_id"])

    def test_orphan_steer_is_delivered_as_new_task(self):
        """A deleted task line should not leave a message marked 'in queue'.

        This was the incident on 2026-09-23: an old cleanup operation ran
        ``DELETE FROM tasks``, causing client cards to disappear, while a message
        placed in the queue for this task could not be delivered by anyone.
        """
        first = self.service.send_message(self.session_id, "первое сообщение")
        self.service.send_message(self.session_id, "второе сообщение")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (first["task_id"],))
            conn.commit()

        self.assertEqual(self.service.resume_pending_steers(), 1)

        self.assertEqual(pending_steer_count(first["task_id"]), 0)
        with sqlite3.connect(get_db_path()) as conn:
            rows = conn.execute(
                "SELECT task_id, status, input_text FROM tasks"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        new_task_id, status, input_text = rows[0]
        self.assertNotEqual(new_task_id, first["task_id"])
        self.assertEqual(status, "queued")
        self.assertIn("второе сообщение", input_text)

        # The log entry is reassigned to a new task, otherwise the client would always
        # display it as 'in queue'.
        entries = _load_entries(_session_path(self.session_id))
        steered = [
            entry for entry in entries if entry.get("steer") and entry.get("message_id")
        ]
        self.assertEqual(len(steered), 1)
        self.assertEqual(steered[0]["task_id"], new_task_id)

        requeued = [
            event
            for event in self.service.session_events(self.session_id)
            if event["kind"] == "task.requeued"
        ]
        self.assertEqual(len(requeued), 1)
        self.assertEqual(requeued[0]["task_id"], new_task_id)

    def test_infra_failed_task_keeps_error_and_steer_runs_as_new_task(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        self.service.send_message(self.session_id, "второе сообщение")
        self._set_status(first["task_id"], "fail")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET error_message = ? WHERE task_id = ?",
                ("session lost its VM (vm_lost)", first["task_id"]),
            )
            conn.commit()
        self.service._merge_task_metadata(
            first["task_id"], {"infra_failure": True, "failure_reason": "vm_lost"}
        )

        self.assertEqual(self.service.resume_pending_steers(), 1)

        failed = self._task_row(first["task_id"])
        self.assertEqual(failed["status"], "fail")
        self.assertEqual(failed["error_message"], "session lost its VM (vm_lost)")
        self.assertEqual(pending_steer_count(first["task_id"]), 0)
        self.assertEqual(self._task_count(), 2)

        with sqlite3.connect(get_db_path()) as conn:
            rows = conn.execute(
                "SELECT task_id, status, input_text FROM tasks WHERE task_id != ?",
                (first["task_id"],),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        new_task_id, status, input_text = rows[0]
        self.assertEqual(status, "queued")
        self.assertIn("второе сообщение", input_text)

        steered = [
            entry
            for entry in _load_entries(_session_path(self.session_id))
            if entry.get("steer") and entry.get("message_id")
        ]
        self.assertEqual(len(steered), 1)
        self.assertEqual(steered[0]["task_id"], new_task_id)

    def test_ordinary_failed_task_is_still_requeued_in_place(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        self.service.send_message(self.session_id, "второе сообщение")
        self._set_status(first["task_id"], "fail")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET error_message = ? WHERE task_id = ?",
                ("model refused the request", first["task_id"]),
            )
            conn.commit()

        self.assertEqual(self.service.resume_pending_steers(), 1)

        row = self._task_row(first["task_id"])
        self.assertEqual(row["status"], "queued")
        self.assertIsNone(row["error_message"])
        self.assertEqual(self._task_count(), 1)
        self.assertEqual(pending_steer_count(first["task_id"]), 0)

    def test_running_task_is_not_requeued_by_poll(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        self.service.send_message(self.session_id, "второе сообщение")

        # The executor will deliver this while the task remains active.
        self.assertEqual(self.service.resume_pending_steers(), 0)
        self.assertEqual(self._task_row(first["task_id"])["status"], "queued")
        self.assertEqual(pending_steer_count(first["task_id"]), 1)

    def test_idempotent_steer_returns_same_message(self):
        first = self.service.send_message(self.session_id, "первое сообщение")
        steered = self.service.send_message(
            self.session_id, "второе сообщение", idempotency_key="steer-1"
        )
        again = self.service.send_message(
            self.session_id, "второе сообщение", idempotency_key="steer-1"
        )

        self.assertTrue(steered["steered"])
        self.assertEqual(again["task_id"], first["task_id"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(pending_steer_count(first["task_id"]), 1)


class FinalizeTurnsTest(ServiceTestCase):
    def test_assistant_entry_written_once(self):
        result = self.service.send_message(self.session_id, "вопрос")
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'ok', output_text = 'ответ Игорька' WHERE task_id = ?",
                (result["task_id"],),
            )

        self.assertEqual(self.service.finalize_turns(), 1)
        self.assertEqual(self.service.finalize_turns(), 0)
        entries = [
            json.loads(line)
            for line in _session_path(self.session_id).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assistant = [entry for entry in entries if entry.get("role") == "assistant"]
        self.assertEqual(len(assistant), 1)
        self.assertEqual(assistant[0]["text"], "ответ Игорька")

    def test_memory_review_runs_once_and_confirms_only_persisted_change(self):
        prompt = "Удали из долговременной памяти факт «Вес Игоря — 103.4 кг»"
        result = self.service.send_message(self.session_id, prompt)
        raw_output = (
            "Запрос передан на проверку.\n"
            "[[MEMORY_WRITE]]\n"
            "scope: long\n"
            "fact: УДАЛИТЬ из Saved facts запись «Вес Игоря — 103.4 кг».\n"
            "[[/MEMORY_WRITE]]"
        )
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'ok', output_text = ? WHERE task_id = ?",
                (raw_output, result["task_id"]),
            )

        manager = unittest.mock.Mock()
        manager.review_turn.return_value = [
            {
                "changed": True,
                "rejected": False,
                "action": "forget",
                "review_action": "forget",
                "target": "Вес Игоря — 103.4 кг",
                "fact": "Вес Игоря — 103.4 кг",
            }
        ]
        manager.format_memory_update_message.return_value = (
            "Память обновлена:\n- удалено из Saved facts: Вес Игоря — 103.4 кг"
        )
        self.service._memory_manager = manager

        self.assertEqual(self.service.finalize_turns(), 1)
        manager.review_turn.assert_called_once()
        review_kwargs = manager.review_turn.call_args.kwargs
        self.assertEqual(review_kwargs["user_text"], prompt)
        self.assertNotIn("[[MEMORY_WRITE]]", review_kwargs["assistant_text"])
        self.assertEqual(review_kwargs["profile"], "cc-local-8081")
        events = self.service.store.list_events(session_id=self.session_id)
        confirmation = next(event for event in events if event["task_id"] == result["task_id"] and event["kind"] == "model.round_response")
        self.assertEqual(confirmation["kind"], "model.round_response")
        self.assertIn("удалено из Saved facts", confirmation["payload"]["text"])

        entries = [
            json.loads(line)
            for line in _session_path(self.session_id).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assistant = [entry for entry in entries if entry.get("role") == "assistant"]
        self.assertEqual(assistant[-1]["text"], "Запрос передан на проверку.")
        self.assertEqual(self.service.finalize_turns(), 0)
        manager.review_turn.assert_called_once()

    def test_blocked_task_is_not_finalized(self):
        task_id = self._make_blocked_task()
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET metadata = ? WHERE task_id = ?",
                (json.dumps({"session": self.session_id, "source": "mobile_bridge"}), task_id),
            )

        self.assertEqual(self.service.finalize_turns(), 0)


class HighRiskSyncTest(unittest.TestCase):
    def test_high_risk_tools_match_backend_policy(self):
        from factory.core.cc_backend_tools import DANGEROUS_TOOLS

        self.assertEqual(set(HIGH_RISK_TOOLS), set(DANGEROUS_TOOLS))


if __name__ == "__main__":
    unittest.main()


class DesktopProfileAvailabilityTests(ServiceTestCase):
    def test_options_only_offer_running_desktop_profile(self):
        self.service.available_profiles = lambda: ['desktop-test']
        options = self.service.session_options()
        self.assertEqual(options['profiles'], ['desktop-test'])
        self.assertEqual(options['default_profile'], 'desktop-test')
        self.service.available_profiles = lambda: []
        self.assertEqual(self.service.session_options()['profiles'], [])
        self.assertEqual(self.service.session_options()['default_profile'], '')

    def test_stopped_profile_cannot_create_session_or_enqueue_message(self):
        self.service.available_profiles = lambda: []
        with self.assertRaises(MobileInvalid):
            self.service.create_session(profile='cc-local-8081')
        with self.assertRaises(MobileInvalid):
            self.service.send_message(self.session_id, 'test')
        self.assertEqual(self._task_count(), 0)

    def test_running_profile_can_enqueue_and_unknown_profile_cannot(self):
        self.service.available_profiles = lambda: ['cc-local-8081']
        session = self.service.create_session(profile='cc-local-8081')
        self.assertEqual(session['meta']['profile'], 'cc-local-8081')
        with self.assertRaises(MobileInvalid):
            self.service.create_session(profile='other-profile')
        queued = self.service.send_message(session['session_id'], 'Ответь одним словом')
        self.assertEqual(self._task_row(queued['task_id'])['status'], 'queued')
