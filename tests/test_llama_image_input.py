"""Local image capability gating and cloud transport compatibility."""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest

from test_executor_tool_loop_edges import loop  # noqa: F401
from test_executor_workflow_guards import response


IMAGE = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "Zml4dHVyZQ=="}}
REJECTION = {"error": 'HTTP 500: {"error":{"message":"image input is not supported - hint: if this is unexpected, you may need to provide the mmproj"}}'}


@pytest.fixture
def metadata_server():
    state = {"props": {"build_info": "fixture-build", "model_alias": "fixture", "modalities": {"vision": False}},
             "models": {}, "status": 200, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append((self.path, dict(self.headers)))
            self.send_response(state["status"])
            self.send_header("Location", "https://example.test/never-follow")
            self.end_headers()
            self.wfile.write(json.dumps(state["props"] if "/props" in self.path else state["models"]).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def payload():
    return {"model": "fixture", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "Inspect this"}, copy.deepcopy(IMAGE)]}]}


@pytest.mark.parametrize("vision", [False, True])
def test_live_llama_capabilities_preserve_original_history_and_track_new_tasks(metadata_server, vision):
    from factory.core.llama_image_input import LlamaImageInput
    state = metadata_server
    state["props"]["modalities"]["vision"] = vision
    policy = LlamaImageInput(state["url"], "fixture", {"Authorization": "Bearer fixture-only", "Origin": "ignored"})
    original = payload()
    before = copy.deepcopy(original)
    result = policy.prepare(original)
    assert original == before
    assert ("Zml4dHVyZQ==" in json.dumps(result)) == vision
    assert state["requests"][0][0] == "/props?model=fixture"
    assert state["requests"][0][1]["Authorization"] == "Bearer fixture-only"
    assert "Origin" not in state["requests"][0][1]
    if not vision:
        assert "read/elements" in json.dumps(result, ensure_ascii=False)
    state["props"]["modalities"]["vision"] = not vision
    fresh = LlamaImageInput(state["url"], "fixture", {}).prepare(original)
    assert ("Zml4dHVyZQ==" in json.dumps(fresh)) != vision


@pytest.mark.parametrize("props", [{}, {"modalities": {"vision": False}},
    {"build_info": "fixture", "modalities": {"vision": "false"}},
    {"build_info": "fixture", "modalities": {"vision": None}},
    {"build_info": "fixture", "model_alias": ["invalid"], "modalities": {"vision": False}},
    {"build_info": "fixture", "model_alias": "another-model", "modalities": {"vision": False}}])
def test_missing_malformed_or_wrong_model_capabilities_do_not_disable_images(metadata_server, props):
    from factory.core.llama_image_input import LlamaImageInput
    metadata_server["props"] = props
    original = payload()
    assert LlamaImageInput(metadata_server["url"], "fixture", {}).prepare(original) is original


@pytest.mark.parametrize("owner,model,modalities,filtered", [
    ("llamacpp", "fixture", ["text"], True),
    ("llamacpp", "fixture", ["text", "image"], False),
    ("cloud", "fixture", ["text"], False),
    ("llamacpp", "another", ["text"], False),
    ("llamacpp", "fixture", [], False),
])
def test_model_metadata_fallback_is_bound_to_llamacpp_and_selected_model(metadata_server, owner, model, modalities, filtered):
    from factory.core.llama_image_input import LlamaImageInput
    metadata_server["props"] = {}
    metadata_server["models"] = {"data": [{"id": model, "owned_by": owner,
        "architecture": {"input_modalities": modalities}}]}
    result = LlamaImageInput(metadata_server["url"], "fixture", {}).prepare(payload())
    assert ("Zml4dHVyZQ==" not in json.dumps(result)) == filtered


@pytest.mark.parametrize("url", ["https://api.example.test/v1", "http://192.168.0.171/v1",
    "http://user:secret@localhost/v1", "http://localhost/v1?token=private"])
def test_nonloopback_and_invalid_addresses_keep_cloud_images_and_skip_probes(monkeypatch, url):
    from factory.core.llama_image_input import LlamaImageInput
    request = Mock(side_effect=AssertionError("Unexpected metadata request"))
    monkeypatch.setattr("http.client.HTTPConnection", request)
    monkeypatch.setattr("http.client.HTTPSConnection", request)
    policy = LlamaImageInput(url, "fixture", {})
    original = payload()
    assert policy.prepare(original) is original
    assert policy.recover(original, REJECTION) is None
    request.assert_not_called()


def test_timeout_redirect_and_text_only_requests_are_not_capability_denials(metadata_server, monkeypatch):
    from factory.core.llama_image_input import LlamaImageInput
    state = metadata_server
    state["status"] = 302
    original = payload()
    assert LlamaImageInput(state["url"], "fixture", {}).prepare(original) is original
    assert len(state["requests"]) == 2
    assert all(path.startswith("/") for path, _ in state["requests"])
    monkeypatch.setattr("http.client.HTTPConnection", Mock(side_effect=TimeoutError))
    policy = LlamaImageInput(state["url"], "fixture", {})
    assert policy.prepare(original) is original
    text = {"messages": [{"role": "user", "content": "text only"}]}
    assert policy.prepare(text) is text


def test_filter_preserves_tool_ids_arguments_and_nested_results(monkeypatch):
    from factory.core.llama_image_input import LlamaImageInput
    monkeypatch.setattr(LlamaImageInput, "_probe", lambda self: False)
    original = {"messages": [{"role": "assistant", "content": None, "tool_calls": [{"id": "one"}]},
        {"role": "user", "content": [
            {"type": "tool_use", "id": "two", "input": {"type": "image", "data": "keep argument"}},
            {"type": "tool_result", "tool_use_id": "one", "content": [copy.deepcopy(IMAGE), {"type": "text", "text": "receipt"}]}]}]}
    before = copy.deepcopy(original)
    result = LlamaImageInput("http://127.0.0.1:1/v1", "fixture", {}).prepare(original)
    assert original == before
    assert result["messages"][0] == original["messages"][0]
    assert result["messages"][1]["content"][0] == original["messages"][1]["content"][0]
    nested = result["messages"][1]["content"][1]
    assert nested["tool_use_id"] == "one" and nested["content"][1]["text"] == "receipt"
    assert "Zml4dHVyZQ==" not in json.dumps(result)


@pytest.mark.parametrize("api", ["openai", "anthropic"])
@pytest.mark.parametrize("vision", [False, True, None])
def test_browser_screenshot_continues_with_text_or_image_for_actual_capability(loop, monkeypatch, api, vision):
    from factory.core import desktop_browser
    from factory.core.llama_image_input import LlamaImageInput
    monkeypatch.setattr(LlamaImageInput, "_probe", lambda self: vision)
    monkeypatch.setattr(desktop_browser, "_IMAGES", {"receipt": copy.deepcopy(IMAGE)})
    tool = {"id": "shot-one", "name": "desktop_browser", "input": {"action": "screenshot"}}
    run = loop(api, [response(api, tool=tool), response(api, "Continued using available page information")],
               names=["desktop_browser"], raw='{"screenshot":{"id":"receipt"}}')
    assert run.result["status"] == "ok" and len(run.calls) == 1 and len(run.requests) == 2
    assert ("Zml4dHVyZQ==" in json.dumps(run.requests[1])) == (vision is not False)
    assert "receipt" not in desktop_browser._IMAGES


@pytest.mark.parametrize("api", ["openai", "anthropic"])
def test_explicit_llama_rejection_retries_only_model_and_filters_later_screenshots(loop, monkeypatch, api):
    from factory.core import desktop_browser
    from factory.core.llama_image_input import LlamaImageInput
    monkeypatch.setattr(LlamaImageInput, "_probe", lambda self: None)
    monkeypatch.setattr(desktop_browser, "_IMAGES", {"receipt": copy.deepcopy(IMAGE)})
    first = {"id": "shot-one", "name": "desktop_browser", "input": {"action": "screenshot"}}
    later = {"id": "read-one", "name": "desktop_browser", "input": {"action": "read"}}
    run = loop(api, [response(api, tool=first), copy.deepcopy(REJECTION), response(api, tool=later), response(api, "Page checked")],
               names=["desktop_browser"], raw='{"screenshot":{"id":"receipt"}}')
    assert run.result["status"] == "ok" and len(run.calls) == 2 and len(run.requests) == 4
    assert "Zml4dHVyZQ==" in json.dumps(run.requests[1])
    assert all("Zml4dHVyZQ==" not in json.dumps(request) for request in run.requests[2:])
    assert sum(event.get("reason") == "llama_image_input_unsupported" for event in run.trace) == 1
    assert not any(event["kind"] == "network.retry" for event in run.trace)


@pytest.mark.parametrize("error", ["HTTP 500: CUDA out of memory", "HTTP 500: internal server error",
    "HTTP 401: unauthorized", "image input is not supported mmproj"])
def test_unrelated_failures_are_not_hidden_or_retried_as_capability_changes(error):
    from factory.core.llama_image_input import LlamaImageInput
    policy = LlamaImageInput("http://127.0.0.1:1/v1", "fixture", {})
    assert policy.recover(payload(), {"error": error}) is None


@pytest.mark.parametrize("api", ["openai", "anthropic", "openai-responses"])
def test_cloud_executor_keeps_attached_images_without_probing(loop, monkeypatch, tmp_path, api):
    from factory.core.llama_image_input import LlamaImageInput
    probe = Mock(side_effect=AssertionError("Cloud API must not be probed"))
    monkeypatch.setattr(LlamaImageInput, "_probe", probe)
    image = tmp_path / "attachment.png"
    image.write_bytes(b"fixture image")
    run = loop(api, [response(api, "Image received")], base_url="https://api.example.test/v1", input_files=[str(image)])
    assert run.result["status"] == "ok"
    assert "Zml4dHVyZSBpbWFnZQ==" in json.dumps(run.requests[0])
    probe.assert_not_called()


@pytest.mark.parametrize("api", ["openai", "anthropic"])
def test_local_attachments_and_resumed_history_images_are_filtered_without_mutation(loop, monkeypatch, tmp_path, api):
    from factory.core.llama_image_input import LlamaImageInput
    monkeypatch.setattr(LlamaImageInput, "_probe", lambda self: False)
    image = tmp_path / "attachment.png"
    image.write_bytes(b"fixture image")
    history = [{"role": "user", "content": [copy.deepcopy(IMAGE)]}, {"role": "assistant", "content": "Previous answer"}]
    original = copy.deepcopy(history)
    run = loop(api, [response(api, "Continued with text")], input_files=[str(image)], history_messages=history)
    sent = json.dumps(run.requests[0])
    assert run.result["status"] == "ok" and history == original
    assert "Zml4dHVyZSBpbWFnZQ==" not in sent and "Zml4dHVyZQ==" not in sent
    assert "read/elements" in sent
