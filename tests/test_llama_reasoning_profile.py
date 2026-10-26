"""Explicit local transport options must not change other provider requests."""

import copy

import pytest

from factory.core import cc_backend_executor as executor, runtime
from factory.core.isolation.guest_task import DEFAULT_GUEST_ROOTS, GuestTask
from factory.core.isolation.history import build_wire_task_payload
from test_isolation_history import _host_input, _manager, _stub_vm, _dispatch_payload
from test_cc_stream_wire_contracts import wire as wire, chunk, event
import json
from test_worker_isolation_seam import WorkerSeamHarness as _Harness


@pytest.mark.parametrize("value", ["auto", "none", "deepseek", "deepseek-legacy"])
def test_explicit_llama_cpp_reasoning_option_is_preserved(value):
    profile = {"llama_cpp": {"reasoning_format": value}}
    before = copy.deepcopy(profile)
    assert runtime.profile_llama_cpp_reasoning_format(profile) == value
    assert profile == before


@pytest.mark.parametrize("profile", [{}, {"provider": "openai"}, {"llama_cpp": {}}, {"llama_cpp": None}])
def test_absent_llama_cpp_options_do_not_opt_other_providers_in(profile):
    assert runtime.profile_llama_cpp_reasoning_format(profile) is None


@pytest.mark.parametrize("value", ["unknown", 1, True, [], {}])
def test_invalid_explicit_format_is_rejected_before_network(value):
    with pytest.raises(ValueError, match="reasoning_format"):
        runtime.profile_llama_cpp_reasoning_format({"llama_cpp": {"reasoning_format": value}})


def test_nonobject_transport_options_are_rejected():
    with pytest.raises(ValueError, match="llama_cpp"):
        runtime.profile_llama_cpp_reasoning_format({"llama_cpp": "deepseek"})


@pytest.mark.parametrize("opt_in", [False, True])
def test_guest_roundtrip_keeps_opt_in_and_default_wire_shape(opt_in):
    options = {"reasoning_format": "deepseek"} if opt_in else {}
    wire = build_wire_task_payload(_host_input(**options))
    parsed = GuestTask.from_wire(wire, guest_roots=DEFAULT_GUEST_ROOTS)
    kwargs = parsed.executor_kwargs(lambda _: None)
    if opt_in:
        assert wire["reasoning_format"] == kwargs["reasoning_format"] == "deepseek"
    else:
        assert "reasoning_format" not in wire and "reasoning_format" not in kwargs


@pytest.mark.parametrize("opt_in", [False, True])
def test_executor_sends_option_only_for_explicit_local_profile(tmp_path, monkeypatch, opt_in):
    requests = []

    def post(url, payload, *args, **kwargs):
        requests.append(copy.deepcopy(payload))
        return {"choices": [{"message": {"role": "assistant", "reasoning_content": "Synthetic private reasoning.", "content": "Verified answer."}, "finish_reason": "stop"}], "usage": {}}, 1

    monkeypatch.setattr(executor, "_http_post_streaming_openai", post)
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    options = {"reasoning_format": "deepseek"} if opt_in else {}
    result = executor.execute_cc_backend_task(
        base_url="http://127.0.0.1:1", api_format="openai", model="fixture-model",
        system_prompt="fixture", user_message="fixture question", workdir=str(work),
        enabled_tools=[], metadata={"model_context_window": 65536}, **options,
    )
    assert result["status"] == "ok" and result["output_text"] == "Verified answer."
    assert len(requests) == 1
    if opt_in:
        assert requests[0]["reasoning_format"] == "deepseek"
    else:
        assert "reasoning_format" not in requests[0]
    assert not any("Synthetic private reasoning." in str(value) for key, value in result.items() if key in ["output_text", "result_text"])


class TestWorkerReasoningOptIn(_Harness):
    def test_openai_local_profile_passes_the_explicit_option(self):
        self.worker.config.update(api_format="openai", llama_cpp={"reasoning_format": "deepseek"})
        with self.seam_env(isolation_env="off"):
            self.run_worker()
        assert self.executor.call_args.kwargs["reasoning_format"] == "deepseek"

    def test_kvm_dispatch_preserves_the_local_profile_option(self):
        self.worker.config.update(api_format="openai", llama_cpp={"reasoning_format": "deepseek"})
        with self.seam_env(isolation_env="kvm"):
            self.run_worker()
        assert self.manager.calls[0]["payload"]["reasoning_format"] == "deepseek"
        self.executor.assert_not_called()

    def test_anthropic_profile_does_not_receive_llama_extension(self):
        self.worker.config.update(api_format="anthropic", llama_cpp={"reasoning_format": "deepseek"})
        with self.seam_env(isolation_env="off"):
            self.run_worker()
        assert "reasoning_format" not in self.executor.call_args.kwargs


@pytest.mark.parametrize("opt_in", [False, True])
def test_real_manager_preserves_optional_format_through_guest_dispatch(tmp_path, opt_in):
    work = tmp_path / "workspace"
    work.mkdir()
    options = {"reasoning_format": "deepseek"} if opt_in else {}
    payload = _manager(tmp_path)._wire_task_payload(_stub_vm(work), _dispatch_payload(**options))
    guest = GuestTask.from_wire(payload, guest_roots=DEFAULT_GUEST_ROOTS)
    kwargs = guest.executor_kwargs(lambda _: None)
    assert ("reasoning_format" in kwargs) is opt_in
    if opt_in:
        assert kwargs["reasoning_format"] == "deepseek"


@pytest.mark.parametrize("opt_in", [False, True])
def test_real_sse_keeps_structured_reasoning_and_literal_code_separate(tmp_path, wire, opt_in):
    answer = "An English answer with `</think>` as a literal."
    wire.chunks = [event(chunk(reasoning="Synthetic private ")), event(chunk(reasoning="reasoning 🐙.")), event(chunk(answer, finish="stop")), b"data: [DONE]\n\n"]
    payload = {"model": "fixture-model", "messages": []}
    if opt_in:
        payload["reasoning_format"] = "deepseek"
    original = copy.deepcopy(payload)
    response, _ = executor._http_post_streaming_openai(wire.url, payload, {}, 3, trace_dir=str(tmp_path))
    message = response["choices"][0]["message"]
    assert payload == original
    assert message["content"] == answer
    assert message["reasoning_content"] == "Synthetic private reasoning 🐙."
    events = [json.loads(line) for line in (tmp_path / "cc_backend_trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "".join(e.get("text", "") for e in events if e["kind"] == "model.text_delta") == answer
    assert "".join(e.get("text", "") for e in events if e["kind"] == "model.reasoning_delta") == "Synthetic private reasoning 🐙."
    assert ("reasoning_format" in wire.requests[0]["body"]) is opt_in


@pytest.mark.parametrize("api_format,value", [("openai", []), ("openai", False), ("openai", "unsupported"), ("anthropic", "deepseek")])
def test_invalid_direct_executor_option_never_makes_a_request(tmp_path, monkeypatch, api_format, value):
    monkeypatch.setattr(executor, "_http_post", lambda *a, **k: pytest.fail("Unexpected network call"))
    monkeypatch.setattr(executor, "_http_post_streaming_openai", lambda *a, **k: pytest.fail("Unexpected network call"))
    with pytest.raises(ValueError, match="reasoning_format"):
        executor.execute_cc_backend_task(base_url="http://127.0.0.1:1", api_format=api_format, reasoning_format=value, model="fixture", system_prompt="fixture", user_message="fixture", workdir=str(tmp_path), enabled_tools=[])
