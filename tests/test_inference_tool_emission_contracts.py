"""Every emitted tool plan must respect advertised input and filesystem rules."""

import json

import pytest

from inference_api_support import (
    EDIT,
    READ,
    WRITE,
    body,
    inference_app as inference_app,
    inference_server as inference_server,
    state,
)


def output_tool(response, name):
    assert response.status_code == 200, response.text
    tool = next(
        item for item in response.json()["content"] if item["type"] == "tool_use"
    )
    assert tool["name"] == name
    return tool["input"]


def test_forced_read_cannot_bypass_project_root_on_relative_parent_path(
    inference_server,
):
    env = inference_server
    project = env.root / "project"
    project.mkdir()
    outside = env.root / "outside.py"
    outside.write_text("outside fixture data")
    payload = body(env, "Read ../outside.py", [READ])
    payload["system"] = f"Primary working directory: {project}"
    current = state(env, payload)
    assert (
        current.explicit_user_file == "../outside.py"
        and current.project_root == str(project)
    )
    env.generator.outputs.extend(['{"intent":"other"}', "Read fixture file"])
    response = env.client.post("/v1/messages", json=payload)
    assert response.status_code == 403, response.text
    assert "path_outside_project_root" in response.json()["error"]["message"]
    assert outside.read_text() == "outside fixture data"


def test_explicit_absolute_override_keeps_permitted_read(inference_server):
    env = inference_server
    project = env.root / "project"
    project.mkdir()
    outside = env.root / "outside.py"
    outside.write_text("fixture")
    payload = body(env, f"Read {outside}", [READ])
    payload["system"] = f"Primary working directory: {project}"
    env.generator.outputs.extend(
        ['{"intent":"other"}', "Read explicitly requested file"]
    )
    tool = output_tool(env.client.post("/v1/messages", json=payload), "Read")
    assert tool["file_path"] == str(outside)


def test_explicit_empty_write_content_is_preserved_without_regeneration(
    inference_server,
):
    env = inference_server
    path = env.root / "empty.txt"
    env.generator.outputs.extend(
        [
            '{"intent":"local_implementation"}',
            json.dumps(
                {"tool": "Write", "input": {"file_path": str(path), "content": ""}}
            ),
            "Create empty file",
        ]
    )
    tool = output_tool(
        env.client.post("/v1/messages", json=body(env, "Create empty.txt", [WRITE])),
        "Write",
    )
    assert tool == {"file_path": str(path), "content": ""}
    assert len(env.generator.calls) == 3 and not path.exists()


def test_explicit_empty_edit_replacement_is_preserved_without_regeneration(
    inference_server,
):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("old")
    expected = {"file_path": str(path), "old_string": "old", "new_string": ""}
    env.generator.outputs.extend(
        [
            '{"intent":"local_implementation"}',
            json.dumps({"tool": "Edit", "input": expected}),
            "Delete fixture block",
        ]
    )
    tool = output_tool(
        env.client.post(
            "/v1/messages", json=body(env, "Delete old block from sample.py", [EDIT])
        ),
        "Edit",
    )
    assert (
        tool == expected and len(env.generator.calls) == 3 and path.read_text() == "old"
    )


def test_empty_content_generation_cannot_emit_schema_invalid_write(inference_server):
    env = inference_server
    path = env.root / "empty.txt"
    env.generator.outputs.extend(
        [
            '{"intent":"local_implementation"}',
            json.dumps({"tool": "Write", "input": {"file_path": str(path)}}),
            "   ",
        ]
    )
    response = env.client.post(
        "/v1/messages", json=body(env, "Create empty.txt", [WRITE])
    )
    assert response.status_code == 502, response.text
    assert "content" in response.json()["error"]["message"]
    assert not path.exists()


def test_wrong_content_type_is_regenerated_before_tool_emission(inference_server):
    env = inference_server
    path = env.root / "hello.txt"
    env.generator.outputs.extend(
        [
            '{"intent":"local_implementation"}',
            json.dumps(
                {"tool": "Write", "input": {"file_path": str(path), "content": 7}}
            ),
            "Corrected content",
            "Create fixture file",
        ]
    )
    tool = output_tool(
        env.client.post("/v1/messages", json=body(env, "Create hello.txt", [WRITE])),
        "Write",
    )
    assert (
        tool == {"file_path": str(path), "content": "Corrected content"}
        and len(env.generator.calls) == 4
    )


def test_incomplete_edit_is_validated_and_completed_before_emission(inference_server):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("old")
    expected = {"file_path": str(path), "old_string": "old", "new_string": "new"}
    env.generator.outputs.extend(
        [
            '{"intent":"local_implementation"}',
            json.dumps(
                {"tool": "Edit", "input": {"file_path": str(path), "new_string": "new"}}
            ),
            json.dumps(expected),
            "Edit fixture block",
        ]
    )
    tool = output_tool(
        env.client.post("/v1/messages", json=body(env, "Modify sample.py", [EDIT])),
        "Edit",
    )
    assert (
        tool == expected and len(env.generator.calls) == 4 and path.read_text() == "old"
    )


@pytest.mark.parametrize("tool_name", ["MultiEdit", "Update"])
def test_complete_alternative_file_tool_input_is_not_regenerated(
    inference_server, tool_name
):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("old")
    if tool_name == "MultiEdit":
        supplied = {
            "file_path": str(path),
            "edits": [{"old_string": "old", "new_string": "new"}],
        }
        properties = {"file_path": {"type": "string"}, "edits": {"type": "array"}}
    else:
        supplied = {"file_path": str(path), "content": "new"}
        properties = {"file_path": {"type": "string"}, "content": {"type": "string"}}
    tool = {
        "name": tool_name,
        "description": "Update fixture file",
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
    }
    env.generator.outputs.extend(
        [
            '{"intent":"local_implementation"}',
            json.dumps({"tool": tool_name, "input": supplied}),
            "Update fixture file",
        ]
    )
    actual = output_tool(
        env.client.post("/v1/messages", json=body(env, "Modify sample.py", [tool])),
        tool_name,
    )
    assert (
        actual == supplied
        and len(env.generator.calls) == 3
        and path.read_text() == "old"
    )
