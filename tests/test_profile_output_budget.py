"""Explicit per-profile output budgets survive local and isolated execution."""

import copy
import json
from pathlib import Path

import pytest

from factory.core import cc_backend_executor as executor, runtime
from factory.core.isolation.guest_task import DEFAULT_GUEST_ROOTS, GuestTask, GuestTaskError
from factory.core.isolation.history import build_wire_task_payload
from factory.core.model_backend_capabilities import ModelBackendCapabilities
from test_isolation_history import _host_input, _manager, _stub_vm, _dispatch_payload
from test_worker_isolation_seam import WorkerSeamHarness as _Harness
from test_cc_stream_wire_contracts import wire as wire, chunk, event


@pytest.mark.parametrize("value", [1, 2048, 65536, 98304])
def test_profile_json_loads_an_exact_positive_output_budget(tmp_path, value):
    data = {"name": "fixture", "backend": "cc-backend", "max_output_tokens": value}
    (tmp_path / "fixture.json").write_text(json.dumps(data))
    config = runtime.get_profile_config("fixture", profiles_dir=tmp_path)
    assert runtime.profile_max_output_tokens(config) == value
    assert json.loads((tmp_path / "fixture.json").read_text()) == data


@pytest.mark.parametrize("value", [True, False, 0, -1, 65536.0, "65536", [], {}])
def test_invalid_profile_budget_is_rejected_on_load(tmp_path, value):
    (tmp_path / "fixture.json").write_text(json.dumps({
        "backend": "cc-backend", "max_output_tokens": value,
    }))
    with pytest.raises(ValueError, match="max_output_tokens"):
        runtime.get_profile_config("fixture", profiles_dir=tmp_path)


@pytest.mark.parametrize("config", [{}, {"max_output_tokens": None}])
def test_absent_budget_retains_automatic_behavior(config):
    before = copy.deepcopy(config)
    assert runtime.profile_max_output_tokens(config) is None
    assert config == before


class TestWorkerOutputBudget(_Harness):
    def test_profile_budget_overrides_task_default(self):
        self.worker.config["max_output_tokens"] = 65536
        with self.seam_env(isolation_env="off"):
            self.run_worker()
        assert self.executor.call_args.kwargs["max_output_tokens"] == 65536
        assert self.task["limits_max_tokens"] == 4000

    def test_unconfigured_profile_preserves_executor_kwargs(self):
        with self.seam_env(isolation_env="off"):
            self.run_worker()
        assert "max_output_tokens" not in self.executor.call_args.kwargs

    def test_isolated_dispatch_includes_explicit_profile_budget(self):
        self.worker.config["max_output_tokens"] = 65536
        with self.seam_env(isolation_env="kvm"):
            self.run_worker()
        assert self.manager.calls[0]["payload"]["max_output_tokens"] == 65536
        self.executor.assert_not_called()


@pytest.mark.parametrize("value", [None, 2048, 65536, 98304])
def test_manager_and_guest_preserve_optional_budget(tmp_path, value):
    work = tmp_path / "workspace"
    work.mkdir()
    options = {} if value is None else {"max_output_tokens": value}
    payload = _manager(tmp_path)._wire_task_payload(_stub_vm(work), _dispatch_payload(**options))
    guest = GuestTask.from_wire(payload, guest_roots=DEFAULT_GUEST_ROOTS)
    kwargs = guest.executor_kwargs(lambda _: None)
    if value is None:
        assert "max_output_tokens" not in payload and "max_output_tokens" not in kwargs
    else:
        assert payload["max_output_tokens"] == kwargs["max_output_tokens"] == value


@pytest.mark.parametrize("value", [True, 0, -1, "65536", 1.5])
def test_guest_refuses_malformed_explicit_budget(value):
    payload = build_wire_task_payload(_host_input())
    payload["max_output_tokens"] = value
    with pytest.raises(GuestTaskError, match="max_output_tokens"):
        GuestTask.from_wire(payload, guest_roots=DEFAULT_GUEST_ROOTS)


@pytest.fixture
def isolated_executor(tmp_path, monkeypatch):
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(executor, "_resolve_backend_context_window", lambda **_: (262144, "fixture"))
    monkeypatch.setattr(executor.LlamaImageInput, "resolve", lambda _: ModelBackendCapabilities())
    return dict(model="fixture-model", system_prompt="fixture", user_message="fixture question",
                workdir=str(tmp_path), enabled_tools=[])


@pytest.mark.parametrize("value,expected", [(None, 32000), (2048, 2048), (65536, 65536), (98304, 98304)])
def test_real_chat_http_receives_exact_budget(isolated_executor, wire, value, expected):
    wire.chunks = [event(chunk("Synthetic answer.", finish="stop")), b"data: [DONE]\n\n"]
    base = wire.url.split("/chat/completions")[0]
    options = {} if value is None else {"max_output_tokens": value}
    result = executor.execute_cc_backend_task(base_url=base, api_format="openai", **isolated_executor, **options)
    assert result["status"] == "ok" and result["output_text"] == "Synthetic answer."
    assert len(wire.requests) == 1
    assert wire.requests[0]["body"]["max_tokens"] == expected
    assert "max_output_tokens" not in wire.requests[0]["body"]


def test_explicit_budget_is_reserved_and_recorded(isolated_executor, wire, monkeypatch):
    observed = []
    evaluate = executor.evaluate_compaction_pressure

    def pressure(**kwargs):
        observed.append(kwargs["reserved_output_tokens"])
        return evaluate(**kwargs)

    monkeypatch.setattr(executor, "evaluate_compaction_pressure", pressure)
    wire.chunks = [event(chunk("Synthetic answer.", finish="stop")), b"data: [DONE]\n\n"]
    result = executor.execute_cc_backend_task(
        base_url=wire.url.split("/chat/completions")[0], api_format="openai",
        max_output_tokens=65536, metadata={"reserved_output_tokens": 8192},
        **isolated_executor,
    )
    assert result["status"] == "ok"
    assert observed and all(value == 65536 for value in observed)
    trace = json.loads((Path(isolated_executor["workdir"]) / "cc_backend_trace.jsonl").read_text().splitlines()[0])
    assert trace["kind"] == "task.start"
    assert trace["max_tokens"] == 65536 and trace["output_budget_source"] == "profile"


def test_each_model_round_keeps_the_explicit_budget(isolated_executor, monkeypatch):
    requests = []

    def post(url, payload, *args, **kwargs):
        requests.append(copy.deepcopy(payload))
        if len(requests) == 1:
            message = {"role": "assistant", "tool_calls": [{
                "id": "fixture-call", "type": "function",
                "function": {"name": "Read", "arguments": json.dumps({"file_path": "fixture.txt"})},
            }]}
        else:
            message = {"role": "assistant", "content": "Synthetic answer."}
        return {"choices": [{"message": message, "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}], "usage": {}}, 1

    monkeypatch.setattr(executor, "_http_post_streaming_openai", post)
    monkeypatch.setattr(executor, "execute_tool", lambda *a, **k: "Synthetic file content.")
    options = {**isolated_executor, "enabled_tools": [{"name": "Read", "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}]}
    result = executor.execute_cc_backend_task(base_url="http://127.0.0.1:1/v1", api_format="openai",
        max_output_tokens=65536, approvals_enabled_provider=lambda: False, **options)
    assert result["status"] == "ok"
    assert len(requests) == 2 and all(p["max_tokens"] == 65536 for p in requests)


@pytest.mark.parametrize("value", [True, 0, -1, "65536", 1.5])
def test_executor_rejects_invalid_budget_before_network(tmp_path, monkeypatch, value):
    monkeypatch.setattr(executor.LlamaImageInput, "resolve", lambda _: pytest.fail("Metadata lookup before validation"))
    with pytest.raises(ValueError, match="max_output_tokens"):
        executor.execute_cc_backend_task(base_url="http://127.0.0.1:1", model="fixture",
            system_prompt="fixture", user_message="fixture", workdir=str(tmp_path),
            enabled_tools=[], max_output_tokens=value)
