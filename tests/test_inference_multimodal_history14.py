"""Image reinjection must preserve compacted history and literal tool context."""
from types import SimpleNamespace

from inference_api_support import inference_app as inference_app


def image(data="Zml4dHVyZQ==", media="image/png"):
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def request(api, *messages):
    return api.AnthropicMessagesRequest(
        model="fixture", max_tokens=20,
        messages=[api.AnthropicMessage(role=role, content=content) for role, content in messages],
    )


def test_nested_tool_result_images_keep_caption_and_compacted_prefix(inference_app):
    api = inference_app.api
    req = request(api, ("user", [{"type": "tool_result", "tool_use_id": "screen", "content": [
        {"type": "text", "text": "Window caption"}, image(),
    ]}]))
    normalized = SimpleNamespace(prompt_messages=[
        {"role": "system", "content": "Compacted summary"},
        {"role": "assistant", "content": "Existing answer"},
        {"role": "user", "content": "Flattened screen"},
    ])
    result = api._extract_multimodal_messages(req, normalized)
    assert result[:2] == [
        {"role": "system", "content": "Compacted summary"},
        {"role": "model", "content": "Existing answer"},
    ]
    assert result[2] == {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,Zml4dHVyZQ=="}},
        {"type": "text", "text": "Window caption"},
    ]}
    assert normalized.prompt_messages[-1]["content"] == "Flattened screen"


def test_image_only_tool_result_keeps_its_tool_context(inference_app):
    api = inference_app.api
    req = request(api, ("user", [{"type": "tool_result", "tool_use_id": "screen", "content": [image()]}]))
    normalized = SimpleNamespace(prompt_messages=[{"role": "system", "content": "Summary"}])
    result = api._extract_multimodal_messages(req, normalized)
    parts = result[-1]["content"]
    assert result[0] == {"role": "system", "content": "Summary"}
    assert parts[0]["image_url"]["url"].endswith("Zml4dHVyZQ==")
    assert any(part["type"] == "text" and "tool_result" in part["text"] for part in parts)


def test_only_latest_user_images_are_reinjected_after_compaction(inference_app):
    api = inference_app.api
    req = request(api, ("user", [image("b2xk")]), ("assistant", "Earlier answer"),
                  ("user", [{"type": "text", "text": "Latest"}, image("bmV3", "image/jpeg")]))
    normalized = SimpleNamespace(prompt_messages=[
        {"role": "system", "content": "Summary"}, {"role": "user", "content": "Current"},
    ])
    result = api._extract_multimodal_messages(req, normalized)
    assert result[-1]["content"] == [
        {"type": "text", "text": "Latest"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,bmV3"}},
    ]
    assert "b2xk" not in str(result)


def test_assistant_image_does_not_replace_normalized_user_text(inference_app):
    api = inference_app.api
    req = request(api, ("assistant", [image()]), ("user", "Continue"))
    normalized = SimpleNamespace(prompt_messages=[
        {"role": "system", "content": "Summary"}, {"role": "user", "content": "Continue"},
    ])
    assert api._extract_multimodal_messages(req, normalized) == normalized.prompt_messages
    normalized.prompt_messages = [{"role": "user", "content": "Only one"}]
    assert api._extract_multimodal_messages(req, normalized) is None


def test_tool_use_metadata_stays_text_beside_the_image(inference_app):
    api = inference_app.api
    req = request(api, ("user", [image(), {"type": "tool_use", "name": "Inspect", "input": {"label": "пример"}}]))
    normalized = SimpleNamespace(prompt_messages=[{"role": "system", "content": "Summary"}])
    result = api._extract_multimodal_messages(req, normalized)
    assert result[-1]["content"][1] == {
        "type": "text", "text": '[tool_use: Inspect {"label": "пример"}]',
    }


def test_invalid_image_sources_leave_text_only_generation_unchanged(inference_app):
    api = inference_app.api
    req = request(api, ("user", [
        {"type": "text", "text": "Continue"}, image(""),
        {"type": "image", "source": {"type": "url", "url": "https://invalid.example/image"}},
        {"type": "image", "source": None},
    ]))
    normalized = SimpleNamespace(prompt_messages=[{"role": "user", "content": "Continue"}])
    assert api._extract_multimodal_messages(req, normalized) is None
