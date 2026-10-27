#!/usr/bin/env python3
"""Regression tests for the feature-flagged isolation seam in the worker.

Wave-0 gate: with ``IGOREK_ISOLATION_MODE != kvm`` the worker must call
``execute_cc_backend_task`` with exactly the same kwargs, through exactly the
same seam sequence, and return exactly the same dict keys as before the seam
existed. ``OffModeBaselineTest`` is written so it also passes against the
pre-change ``worker.py`` (the manager factory patch uses ``create=True`` and the
off-mode branch never consults it) - that is what makes it a captured baseline
rather than a restatement of the new code.

Kvm-mode: a fake manager must receive the documented dispatch payload, and a
manager raising ``IsolationError`` must become a task result with
``status="fail"`` and a populated ``error`` field (the field ``update_task``
maps to ``error_message``) without crashing the worker loop and without any
host-side escalation.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core import worker as worker_module  # noqa: E402
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.worker import Worker  # noqa: E402

TOOL_SCHEMAS = [
    {"name": "Read"},
    {"name": "Write"},
    {"name": "request_user_input"},
]

EXECUTOR_RESULT = {
    "status": "ok",
    "output_text": "done",
    "usage": {"prompt": 1, "completion": 2, "lat_ms_p50": 3},
    "tool_calls_count": 1,
}

# Dict keys the executor receives today (captured pre-change baseline).
EXPECTED_EXECUTOR_KEYS = frozenset(
    {
        "base_url",
        "model",
        "system_prompt",
        "user_message",
        "input_files",
        "workdir",
        "artifact_workdir",
        "filesystem_mode",
        "guard_mode",
        "checkpoint_config",
        "current_session",
        "source_platform",
        "source_chat_id",
        "max_tokens",
        "timeout_s",
        "log_fn",
        "enabled_tools",
        "metadata",
        "history_messages",
        "trailing_context_text",
        "api_format",
        "request_headers",
        "task_id",
        "profile",
        "pending_steer_provider",
        "priority_steer_provider",
        "approvals_enabled_provider",
    }
)

# Dict keys the worker-shaped result has today (captured pre-change baseline).
EXPECTED_WORKER_KEYS = frozenset(
    {
        "task_id",
        "status",
        "output",
        "usage",
        "usage_ext",
        "cost_usd",
        "approval_blocked_tools",
    }
)

# Ordered seam sequence of the off-mode path (captured pre-change baseline).
BASELINE_SEAM_SEQUENCE = [
    "reload_enabled_mcp_servers",
    "get_tool_schemas",
    "default_cc_backend_toolsets",
    "stable_prompt_prefix",
    "build_cc_backend_system_prompt",
    "read_project_instructions",
    "execute_cc_backend_task",
]


class FakeManager:
    """Fake isolation manager recording every ``run_task`` invocation."""

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = dict(result if result is not None else EXECUTOR_RESULT)
        self.error = error
        self.calls: list[dict] = []

    def run_task(
        self,
        *,
        task: dict,
        workdir: str,
        payload: dict,
        cancel_check=None,
    ) -> dict:
        self.calls.append(
            {
                "task": task,
                "workdir": workdir,
                "payload": payload,
                "cancel_check": cancel_check,
            }
        )
        if self.error is not None:
            raise self.error
        return dict(self.result)


class WorkerSeamHarness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-seam-")
        self.root = Path(self._tmp.name)
        self.workdir = self.root / "workdir"
        self.workdir.mkdir()
        self.runtime_workdir = self.root / "runtime"
        self.runtime_workdir.mkdir()

        self.worker = Worker.__new__(Worker)
        self.worker.profile = "cc-seam-test"
        self.worker.backend = "cc-backend"
        self.worker.model = "seam-model"
        self.worker.config = {"provider": "seam-provider"}

        self.task = {
            "task_id": "task-seam",
            "metadata": {"session": "sess-seam", "source": "cli"},
            "limits_max_tokens": 4000,
            "limits_timeout_s": 1200,
        }
        self.task_text = "Задача пользователя:\nнапиши отчёт"
        self.skills_text = "SKILLS"
        self.foundation = "FOUNDATION"

        self.recorder: list[str] = []
        self.build_prompt_calls: list[dict] = []
        self.executor = Mock(side_effect=self._execute_backend_task)
        self.escalation = Mock(return_value={"status": "ok"})
        self.manager = FakeManager()
        self.manager_factory = Mock(return_value=self.manager)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _record(self, name: str, value):
        def _fn(*args, **kwargs):
            self.recorder.append(name)
            return value

        return _fn

    def _record_prompt(self, *args, **kwargs):
        self.recorder.append("build_cc_backend_system_prompt")
        self.build_prompt_calls.append(dict(kwargs))
        return "SYSTEM PROMPT"

    def _execute_backend_task(self, *args, **kwargs):
        self.recorder.append("execute_cc_backend_task")
        return dict(EXECUTOR_RESULT)

    @contextlib.contextmanager
    def seam_env(self, *, isolation_env: str = "off", escalation_route=None):
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(Worker, "_materialize_vault_env", return_value={})
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "_temporary_env",
                    lambda updates: contextlib.nullcontext(),
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "_reload_enabled_mcp_servers_for_cc_backend",
                    self._record("reload_enabled_mcp_servers", []),
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "get_tool_schemas",
                    self._record("get_tool_schemas", TOOL_SCHEMAS),
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "_default_cc_backend_toolsets",
                    self._record("default_cc_backend_toolsets", []),
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "_stable_prompt_prefix",
                    self._record("stable_prompt_prefix", False),
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module, "build_cc_backend_system_prompt", self._record_prompt
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "list_mcp_capabilities_for_profile",
                    return_value=[],
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "_read_project_instructions",
                    self._record("read_project_instructions", ""),
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module, "_resolve_cc_backend_auth_headers", return_value={}
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "_effective_cc_backend_model",
                    return_value="seam-model",
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "_resolve_level_runtime",
                    return_value={
                        "filesystem_mode": "workspace",
                        "guard_mode": "on",
                        "security_mode": "observe",
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module, "resolve_escalation_route",
                    return_value=escalation_route,
                )
            )
            stack.enter_context(patch.object(worker_module, "SKILLS_AVAILABLE", False))
            stack.enter_context(
                patch(
                    "factory.core.cc_backend_executor.execute_cc_backend_task",
                    self.executor,
                )
            )
            stack.enter_context(
                patch.object(
                    worker_module,
                    "_get_isolation_manager",
                    self.manager_factory,
                    create=True,
                )
            )
            stack.enter_context(
                patch.object(Worker, "_execute_codex_escalation", self.escalation)
            )
            stack.enter_context(
                patch.dict(os.environ, {"IGOREK_ISOLATION_MODE": isolation_env})
            )
            yield

    def run_worker(self) -> dict:
        return self.worker._execute_cc_backend(
            self.task,
            str(self.workdir),
            str(self.runtime_workdir),
            self.task_text,
            self.skills_text,
            foundation_context=self.foundation,
            start_time=None,
        )


class OffModeBaselineTest(WorkerSeamHarness):
    """Off mode must be byte-identical: same seams, same kwargs, same keys."""

    def test_demo_limits_tools_and_scope_without_foundation_memory(self):
        self.task['metadata'].update({'demo_workspace': True, 'restrict_tools_to': ['Read']})
        with self.seam_env():
            self.run_worker()
        kwargs = self.executor.call_args.kwargs
        self.assertEqual([tool['name'] for tool in kwargs['enabled_tools']], ['Read'])
        self.assertEqual(kwargs['filesystem_mode'], 'workspace')
        self.assertEqual(self.build_prompt_calls[0]['foundation_context'], '')
        self.assertEqual(self.build_prompt_calls[0]['skills_text'], '')

    def test_opencode_go_selected_model_routes_to_its_protocol(self):
        self.worker.config = {'provider': 'custom', 'base_url': 'https://opencode.ai/zen/go/v1', 'api_format': 'openai'}
        self.task['metadata']['model_override'] = 'selected-model'
        resolve_model = worker_module._effective_cc_backend_model
        for api_format, base in [('openai', 'https://opencode.ai/inference/go/openai/v1'),
                                 ('opencode-responses', 'https://opencode.ai/inference/go/openai/v1'),
                                 ('anthropic', 'https://opencode.ai/inference/go/anthropic/v1'),
                                 ('google', 'https://opencode.ai/inference/go/google/v1beta')]:
            with self.subTest(api_format=api_format), self.seam_env():
                with patch.object(worker_module, '_effective_cc_backend_model', side_effect=resolve_model), \
                     patch('factory.core.opencode_go_catalog.get_opencode_go_routes',
                           return_value={'selected-model': (api_format, base, 'Selected')}):
                    self.run_worker()
                    kwargs = self.executor.call_args.kwargs
                    self.assertEqual((kwargs['api_format'], kwargs['base_url'], kwargs['model']),
                                     (api_format, base, 'selected-model'))

    def test_desktop_navigation_instruction_preserves_the_normal_toolset(self):
        with self.seam_env():
            self.run_worker()
        baseline = self.executor.call_args.kwargs['enabled_tools']
        self.task['metadata']['desktop_ui'] = True
        with self.seam_env():
            self.run_worker()
        kwargs = self.executor.call_args.kwargs
        self.assertIn('igorek://ui/notifications', kwargs['system_prompt'])
        self.assertIn('do not read the repository', kwargs['system_prompt'])
        self.assertEqual(kwargs['enabled_tools'], baseline)
        self.assertEqual(self.build_prompt_calls[0]['foundation_context'], self.foundation)

    def test_bridge_advertises_interactive_tool_and_enables_matching_prompt(self):
        self.task["metadata"].update(source="mobile_bridge", platform="android")
        self.worker.config.update(api_format="openai-responses", provider="openai")
        with self.seam_env(isolation_env="off"):
            self.run_worker()
        kwargs = self.executor.call_args.kwargs
        self.assertIn("request_user_input", {tool["name"] for tool in kwargs["enabled_tools"]})
        self.assertTrue(self.build_prompt_calls[-1]["interactive_input_enabled"])
        self.assertEqual(kwargs["task_id"], self.task["task_id"])

    def test_headless_tasks_still_exclude_interactive_tool(self):
        for source in ("telegram", "scheduler", "cli"):
            with self.subTest(source=source):
                self.task["metadata"]["source"] = source
                with self.seam_env(isolation_env="off"):
                    self.run_worker()
                self.assertNotIn("request_user_input", {tool["name"] for tool in self.executor.call_args.kwargs["enabled_tools"]})
                self.assertFalse(self.build_prompt_calls[-1]["interactive_input_enabled"])

    def test_off_mode_matches_the_pre_change_baseline(self) -> None:
        with self.seam_env(isolation_env="off"):
            result = self.run_worker()

        self.assertEqual(self.recorder, BASELINE_SEAM_SEQUENCE)
        self.executor.assert_called_once()
        self.assertEqual(
            set(self.executor.call_args.kwargs), EXPECTED_EXECUTOR_KEYS
        )
        self.assertEqual(set(result), EXPECTED_WORKER_KEYS)
        self.manager_factory.assert_not_called()

    def test_off_mode_passes_identical_executor_kwargs(self) -> None:
        with self.seam_env(isolation_env="off"):
            self.run_worker()

        kwargs = dict(self.executor.call_args.kwargs)
        log_fn = kwargs.pop("log_fn")
        self.assertTrue(callable(log_fn))
        self.assertEqual(
            kwargs,
            {
                "base_url": "http://127.0.0.1:8001",
                "model": "seam-model",
                "system_prompt": "SYSTEM PROMPT",
                "user_message": "напиши отчёт",
                "input_files": [],
                "workdir": str(self.workdir),
                "artifact_workdir": str(self.runtime_workdir),
                "filesystem_mode": "workspace",
                "guard_mode": "on",
                "checkpoint_config": {"enabled": True, "max_snapshots": 50},
                "current_session": "sess-seam",
                "source_platform": None,
                "source_chat_id": None,
                "max_tokens": 4000,
                "timeout_s": 1200,
                "enabled_tools": [{"name": "Read"}, {"name": "Write"}],
                "metadata": {"session": "sess-seam", "source": "cli"},
                "history_messages": None,
                "trailing_context_text": "",
                "api_format": "anthropic",
                "request_headers": {},
                "task_id": "task-seam",
                "profile": "cc-seam-test",
                "pending_steer_provider": None,
                "priority_steer_provider": None,
                "approvals_enabled_provider": None,
            },
        )
        self.assertEqual(
            self.build_prompt_calls[0]["workdir"], str(self.workdir)
        )

    def test_off_mode_returns_the_baseline_result_values(self) -> None:
        with self.seam_env(isolation_env="off"):
            result = self.run_worker()

        self.assertEqual(result["task_id"], "task-seam")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output"], {"text": "done"})
        self.assertEqual(result["usage"], EXECUTOR_RESULT["usage"])
        self.assertEqual(
            result["usage_ext"], {"input_tokens": 1, "output_tokens": 2}
        )
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertEqual(result["approval_blocked_tools"], [])

    def test_failed_model_result_retains_failure_identity_and_details(self) -> None:
        from factory.core.diagnostics import observation
        diagnostic=observation('NET.READ_TIMEOUT',component='model',operation='model_round',endpoint_role='runtime_to_provider',profile='fixture',model='org/model')
        with self.seam_env(isolation_env='off'):
            self.executor.side_effect=lambda **kwargs:{**EXECUTOR_RESULT,'status':'fail','failure_reason':'http_error','failure_details':'fixture timeout','diagnostic':diagnostic}
            result=self.run_worker()
        self.assertEqual(result['error'],'fixture timeout')
        self.assertEqual(result['failure_reason'],'http_error')
        self.assertEqual(result['diagnostic'],diagnostic)


class KvmModeDispatchTest(WorkerSeamHarness):
    def test_opencode_go_selected_routes_reach_isolation_manager(self):
        self.worker.isolation_manager = self.manager
        self.worker.config = {'provider': 'custom', 'base_url': 'https://opencode.ai/zen/go/v1', 'api_format': 'openai'}
        self.task['metadata']['model_override'] = 'selected-model'
        resolve_model = worker_module._effective_cc_backend_model
        for api_format, base in [('openai', 'https://opencode.ai/inference/go/openai/v1'),
                                 ('opencode-responses', 'https://opencode.ai/inference/go/openai/v1'),
                                 ('anthropic', 'https://opencode.ai/inference/go/anthropic/v1'),
                                 ('google', 'https://opencode.ai/inference/go/google/v1beta')]:
            self.manager.calls.clear()
            with self.subTest(api_format=api_format), self.seam_env():
                with patch.object(worker_module, '_effective_cc_backend_model', side_effect=resolve_model), \
                     patch('factory.core.opencode_go_catalog.get_opencode_go_routes',
                           return_value={'selected-model': (api_format, base, 'Selected')}), \
                     patch('factory.core.cc_backend_executor.resolve_host_context_window', return_value=128000):
                    self.run_worker()
                    payload = self.manager.calls[-1]['payload']
                    self.assertEqual((payload['api_format'], payload['base_url'], payload['model']),
                                     (api_format, base, 'selected-model'))

    def test_injected_desktop_manager_routes_to_guest_with_global_mode_off(self):
        self.worker.isolation_manager = self.manager
        with self.seam_env(isolation_env="off"):
            result = self.run_worker()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(self.manager.calls), 1)
        self.manager_factory.assert_not_called()
        self.executor.assert_not_called()
        self.assertEqual(self.build_prompt_calls[0]["workdir"], "/workspace")

    def test_injected_desktop_failure_never_executes_on_host(self):
        self.worker.isolation_manager = self.manager
        self.manager.error = IsolationError("isolation_unavailable:test")
        with self.seam_env(isolation_env="off"):
            result = self.run_worker()
        self.assertEqual(result["status"], "fail")
        self.executor.assert_not_called()
        self.manager_factory.assert_not_called()
        self.escalation.assert_not_called()

    """Kvm mode hands the turn to the isolation manager, host-side work stays put."""

    def test_kvm_mode_dispatches_to_the_manager_with_the_documented_payload(
        self,
    ) -> None:
        with self.seam_env(isolation_env="kvm"):
            result = self.run_worker()

        self.executor.assert_not_called()
        self.manager_factory.assert_called_once()
        self.assertEqual(len(self.manager.calls), 1)
        call = self.manager.calls[0]
        self.assertIs(call["task"], self.task)
        self.assertEqual(call["workdir"], str(self.workdir))
        # The host stop signal (mobile Stop -> `cancelling`) reaches the manager.
        self.assertTrue(callable(call["cancel_check"]))

        payload = call["payload"]
        for key in (
            "workdir",
            "system_prompt",
            "skills_text",
            "foundation_context",
            "metadata",
            "profile",
        ):
            self.assertIn(key, payload, key)
        self.assertEqual(payload["system_prompt"], "SYSTEM PROMPT")
        self.assertEqual(payload["skills_text"], "SKILLS")
        self.assertEqual(payload["foundation_context"], "FOUNDATION")
        # The host resolves the context window for the current model and adds it
        # to the isolated metadata; the task metadata itself is not mutated.
        metadata = payload["metadata"]
        self.assertEqual(
            {key: value for key, value in metadata.items() if key != "model_context_window"},
            self.task["metadata"],
        )
        self.assertIsInstance(metadata.get("model_context_window"), int)
        self.assertNotIn("model_context_window", self.task["metadata"])
        self.assertEqual(payload["profile"], "cc-seam-test")
        self.assertEqual(payload["task_id"], "task-seam")
        self.assertEqual(payload["user_message"], "напиши отчёт")
        self.assertEqual(payload["model"], "seam-model")
        self.assertEqual(payload["max_tokens"], 4000)
        self.assertEqual(payload["timeout_s"], 1200)
        # The guest must see its own workspace path, never the host one.
        self.assertEqual(self.build_prompt_calls[0]["workdir"], "/workspace")

    def test_structured_history_reaches_guest_without_duplicate_prompt(self) -> None:
        history = [{"role": "assistant", "content": "verified tool evidence", "history_id": "external:1"}]
        self.task["metadata"].update({
            "structured_session_history": True,
            "history_messages": history,
            "session_user_message": "Продолжи работу",
        })
        with self.seam_env(isolation_env="kvm"):
            self.run_worker()
        payload = self.manager.calls[0]["payload"]
        self.assertEqual(payload["history_messages"], history)
        self.assertEqual(payload["user_message"], "Продолжи работу")
        self.assertEqual(self.build_prompt_calls[0]["wrapped_task_context"], "")

    def test_kvm_mobile_task_passes_steer_provider_to_the_manager(self) -> None:
        provider = Mock(return_value=[])
        self.task["metadata"] = {
            "session": "sess-seam",
            "source": "mobile_bridge",
            "platform": "android",
        }
        with patch("factory.core.steer_queue.make_steer_provider", return_value=provider):
            with self.seam_env(isolation_env="kvm"):
                self.run_worker()

        payload = self.manager.calls[0]["payload"]
        self.assertIs(payload["pending_steer_provider"], provider)

    def test_kvm_mode_returns_the_same_worker_result_shape(self) -> None:
        with self.seam_env(isolation_env="kvm"):
            result = self.run_worker()

        self.assertEqual(set(result), EXPECTED_WORKER_KEYS)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output"], {"text": "done"})
        self.assertEqual(result["usage"], EXECUTOR_RESULT["usage"])
        self.assertEqual(
            result["usage_ext"], {"input_tokens": 1, "output_tokens": 2}
        )
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertEqual(result["approval_blocked_tools"], [])

    def test_kvm_mode_never_escalates_to_a_host_backend(self) -> None:
        self.manager.result = {
            **EXECUTOR_RESULT,
            "status": "fail",
            "failure_reason": "http_error",
        }
        with self.seam_env(isolation_env="kvm", escalation_route=object()):
            result = self.run_worker()

        self.escalation.assert_not_called()
        self.assertNotIn("escalation", result)
        self.assertEqual(result["status"], "fail")
        # A contained kvm failure keeps its reason in the worker shape, so
        # update_task writes a clear error_message instead of an empty one; it
        # is never normalized into a success.
        self.assertEqual(set(result), {"task_id", "status", "error", "usage", "failure_reason"})
        self.assertIn("http_error", result["error"])

    def test_kvm_mode_isolation_error_becomes_a_fail_result(self) -> None:
        self.manager.error = IsolationError(
            "isolation_unavailable:capability_unavailable:kvm_missing",
            "no /dev/kvm on this host",
        )
        with self.seam_env(isolation_env="kvm"):
            result = self.run_worker()

        self.executor.assert_not_called()
        self.assertEqual(result["task_id"], "task-seam")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(set(result), {"task_id", "status", "error", "usage"})
        self.assertIn(
            "isolation_unavailable:capability_unavailable:kvm_missing",
            result["error"],
        )
        self.assertIn("no /dev/kvm on this host", result["error"])
        self.escalation.assert_not_called()

    def test_non_kvm_mode_values_stay_on_the_local_path(self) -> None:
        for mode in ("", "off", "bwrap", "none"):
            with self.subTest(mode=mode):
                self.executor.reset_mock()
                self.manager.calls.clear()
                with self.seam_env(isolation_env=mode):
                    self.run_worker()
                self.executor.assert_called_once()
                self.assertEqual(self.manager.calls, [])


class BackendScopeDispatchTest(unittest.TestCase):
    """claude/codex dispatch never opens the isolation manager (no VM, todo 35)."""

    def _assert_dispatch_never_opens_the_isolation_manager(self, backend: str, desktop_ui: bool = False) -> None:
        class _CommandBuilt(Exception):
            pass

        worker = Worker.__new__(Worker)
        worker.profile = f"{backend}-scope"
        worker.backend = backend
        worker.model = "scope-model"
        worker.config = {"provider": "scope-provider", "env": {}}
        task = {
            "task_id": f"task-{backend}-scope",
            "parent_id": None,
            "stage": "main",
            "input_text": "scope",
            "input_files": [],
            "capabilities": [],
            "metadata": {"desktop_ui": True} if desktop_ui else {},
            "limits_max_tokens": 100,
            "limits_timeout_s": 10,
            "limits_budget_usd": 0.0,
        }
        manager_factory = Mock(
            side_effect=AssertionError(
                f"{backend} dispatch must not open the isolation manager"
            )
        )
        with tempfile.TemporaryDirectory(prefix="isolation-dispatch-") as tmp:
            with contextlib.ExitStack() as stack:
                prepend = stack.enter_context(patch.object(worker_module, '_prepend_foundation_context', wraps=worker_module._prepend_foundation_context))
                stack.enter_context(patch.object(worker_module, "WORKSPACE_ROOT", tmp))
                stack.enter_context(
                    patch.object(
                        worker_module, "_consume_host_op_resolutions", return_value=None
                    )
                )
                stack.enter_context(
                    patch.object(
                        worker_module, "_foundation_context_block", return_value=""
                    )
                )
                stack.enter_context(
                    patch.object(worker_module, "SKILLS_AVAILABLE", False)
                )
                stack.enter_context(
                    patch.object(worker_module, "_get_isolation_manager", manager_factory)
                )
                stack.enter_context(
                    patch.object(Worker, "_materialize_vault_env", return_value={})
                )
                stack.enter_context(
                    patch.object(
                        worker_module, "build_backend_command", side_effect=_CommandBuilt
                    )
                )
                stack.enter_context(
                    patch.object(
                        worker_module,
                        "materialize_profile_mcp_config",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch.object(
                        worker_module, "get_profile_mcp_servers", return_value=[]
                    )
                )
                stack.enter_context(
                    patch.object(
                        worker_module,
                        "resolve_codex_launch_settings",
                        return_value={
                            "env": {},
                            "provider": "scope-provider",
                            "model": "scope-model",
                        },
                    )
                )
                stack.enter_context(
                    patch.object(
                        worker_module,
                        "sync_codex_mcp_for_profile",
                        return_value={"status": "ok"},
                    )
                )
                with self.assertRaises(_CommandBuilt):
                    worker.execute_task(task)
                self.assertEqual('igorek://ui/models' in prepend.call_args.args[1], desktop_ui)
        manager_factory.assert_not_called()

    def test_desktop_ui_map_reaches_native_backends_without_starting_a_process(self):
        for backend in ('claude', 'codex'):
            with self.subTest(backend=backend):
                self._assert_dispatch_never_opens_the_isolation_manager(backend, desktop_ui=True)

    def test_codex_dispatch_never_opens_the_isolation_manager(self) -> None:
        self._assert_dispatch_never_opens_the_isolation_manager("codex")

    def test_claude_dispatch_never_opens_the_isolation_manager(self) -> None:
        self._assert_dispatch_never_opens_the_isolation_manager("claude")


if __name__ == "__main__":
    unittest.main()


class WebSearchProviderScopeTest(WorkerSeamHarness):
    def test_provider_spans_schema_assembly_and_executor_then_is_cleared(self):
        from types import SimpleNamespace
        from factory.core import cc_backend_tools
        from factory.core.web_tools_runtime import active_web_tools
        provider = SimpleNamespace(available=lambda: True, request=lambda *_: {"results": []})
        self.worker.web_tools_provider = provider
        def execute(**kwargs):
            self.assertIs(active_web_tools(), provider)
            self.assertTrue({"web_search", "web_extract"} <= {tool["name"] for tool in kwargs["enabled_tools"]})
            result = cc_backend_tools.execute_tool("web_search", {"query": "fixture"}, str(self.workdir))
            self.assertIn('"success": true', result)
            return dict(EXECUTOR_RESULT)
        self.executor.side_effect = execute
        with self.seam_env(), patch.object(worker_module, "get_tool_schemas", cc_backend_tools.get_tool_schemas):
            self.assertEqual(self.run_worker()["status"], "ok")
        self.assertIsNone(active_web_tools())

    def test_provider_is_cleared_after_executor_error(self):
        from types import SimpleNamespace
        from factory.core.web_tools_runtime import active_web_tools
        self.worker.web_tools_provider = SimpleNamespace(available=lambda: True)
        self.executor.side_effect = RuntimeError("fixture-failure")
        with self.seam_env(), self.assertRaisesRegex(RuntimeError, "fixture-failure"):
            self.run_worker()
        self.assertIsNone(active_web_tools())
