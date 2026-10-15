import json
import re
from typing import Any

from paroquant.inference import GenerationParams, build_prompt

from factory.api_server_core.prompts import agent_tool_rules
from factory.api_server_core.prompt_rules import http_curl_prompt_rules
from factory.api_server_core.tool_parsing import (
    coerce_tool_plan_payload,
    extract_json_objects,
    parse_json_object,
    parse_textual_tool_use,
    parse_tool_plan,
    validated_named_tool_use,
)


def _file_read_overflow_requires_bounded_retry(state: Any) -> bool:
    if getattr(state, "current_workflow", None) != "file_investigation":
        return False
    error_text = str(getattr(state, "last_workflow_error", "") or "")
    if not error_text:
        return False
    return (
        "exceeds maximum allowed tokens" in error_text
        and "Use offset and limit parameters" in error_text
    )


def _read_plan_has_explicit_bounds(plan: dict[str, Any] | None) -> bool:
    if not isinstance(plan, dict) or plan.get("tool") != "Read":
        return False
    tool_input = plan.get("input")
    if not isinstance(tool_input, dict):
        return False
    offset = tool_input.get("offset")
    limit = tool_input.get("limit")
    return isinstance(offset, int) and offset > 0 and isinstance(limit, int) and limit > 0


def _user_tool_restrictions_prompt(state: Any) -> str:
    constraints = getattr(state, "explicit_runtime_constraints", None)
    if constraints is None:
        return ""
    rules: list[str] = []
    if constraints.no_subagents:
        rules.append("The user explicitly forbids subagents. You MUST NOT choose Agent.")
    if constraints.no_background:
        rules.append(
            "The user explicitly forbids background execution. "
            "You MUST NOT choose background-task style tools such as Agent, Task, TaskCreate, or VerifyPlanExecution."
        )
    if not rules:
        return ""
    return "USER TOOL RESTRICTIONS:\n" + "\n".join(f"- {rule}" for rule in rules) + "\n"


def _subagent_tool_restrictions_prompt(state: Any) -> str:
    if not getattr(state, "is_readonly_explore_subagent", False):
        return ""
    return (
        "SUBAGENT TOOL RESTRICTIONS:\n"
        "- You are inside the Explore read-only codebase exploration profile.\n"
        "- For repo exploration, use local tools only: Glob, Grep, Read, and local Bash commands such as ls or pwd when needed.\n"
        "- You MUST NOT use WebSearch, WebFetch, Tavily search, curl to external URLs, or any other internet/network lookup.\n"
        "- You MUST inspect the local workspace, not the web.\n"
    )


def _canonicalize_agent_subtype_plan(state: Any, plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(plan, dict):
        return plan
    tool_name = plan.get("tool")
    tool_input = plan.get("input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return plan
    if tool_name not in {"general-purpose", "statusline-setup", "Explore", "Plan", "claude-code-guide"}:
        return plan
    if "Agent" not in getattr(state, "tool_map", {}):
        return plan
    description = tool_input.get("description")
    prompt = tool_input.get("prompt")
    if not isinstance(description, str) or not isinstance(prompt, str):
        return plan
    return {
        "tool": "Agent",
        "input": {
            "subagent_type": tool_name,
            "description": description,
            "prompt": prompt,
        },
    }


def build_tool_planner_prompt(state, *, require_tool: bool = False, tool_schema_excerpt_fn) -> str:
    user_tool_restrictions = _user_tool_restrictions_prompt(state)
    subagent_tool_restrictions = _subagent_tool_restrictions_prompt(state)
    if state.current_workflow == "failure_recovery" and state.workflow_step == "retry_once" and require_tool:
        return (
            "You are a tool planner for Claude Code inside the failure_recovery workflow.\n"
            "A tool call is required right now.\n"
            "This is a retry after the previous tool attempt failed.\n"
            "You are correcting the previous request, not starting over.\n"
            "You MUST immediately return exactly one replacement tool call.\n"
            "You MUST NOT answer in natural language.\n"
            "You MUST NOT return {\"tool\": null}.\n"
            f"{user_tool_restrictions}"
            f"{subagent_tool_restrictions}"
            f"{agent_tool_rules()}"
            f"{http_curl_prompt_rules()}"
            "Return ONLY valid JSON with this exact shape:\n"
            "{\"tool\": <string>, \"input\": <object>}\n\n"
            f"AVAILABLE TOOLS:\n{json.dumps([{'name': tool.get('name'), 'description': re.sub(r'\\s+', ' ', str(tool.get('description', ''))).strip()[:300], 'input_schema': tool_schema_excerpt_fn(tool)} for tool in state.tools[:24]], ensure_ascii=False, indent=2)}\n\n"
            f"RECENT DIALOGUE:\n{state.latest_dialogue}\n"
        )
    if state.current_workflow == "file_investigation" and require_tool:
        overflow_instruction = ""
        if _file_read_overflow_requires_bounded_retry(state):
            overflow_instruction = (
                "CRITICAL CONTINUATION RULE:\n"
                "The previous full-file Read already failed because the file is too large.\n"
                "Your next Read MUST include explicit positive integer offset and limit fields.\n"
                "You MUST NOT repeat a file_path-only Read.\n"
                "You MUST continue from the next unread chunk after the latest successful range in COLLECTED FILE EVIDENCE.\n"
                "Example: if the latest successful range is 1-400, the next Read must start at offset 401 with the same chunk limit 400.\n"
            )
        return (
            "You are a tool planner for Claude Code inside the file_investigation workflow.\n"
            "A tool call is required right now.\n"
            "You are continuing a structured investigation of one file.\n"
            "You MUST stay inside this workflow and MUST NOT switch to any unrelated task from older session history.\n"
            "You MUST choose exactly one Read tool call.\n"
            "Do NOT choose Bash.\n"
            "Do NOT choose WebSearch.\n"
            "Do NOT choose WebFetch.\n"
            "Do NOT choose Agent.\n"
            "Do NOT return {\"tool\": null}.\n"
            "Do NOT answer in natural language.\n"
            f"{user_tool_restrictions}"
            f"{subagent_tool_restrictions}"
            f"{overflow_instruction}"
            "Return ONLY valid JSON with this exact shape:\n"
            "{\"tool\": <string>, \"input\": <object>}\n\n"
            f"AVAILABLE TOOLS:\n{json.dumps([{'name': tool.get('name'), 'description': re.sub(r'\\s+', ' ', str(tool.get('description', ''))).strip()[:300], 'input_schema': tool_schema_excerpt_fn(tool)} for tool in state.tools[:24]], ensure_ascii=False, indent=2)}\n\n"
            f"WORKFLOW CONTEXT:\n{state.latest_dialogue}\n"
        )
    if state.current_workflow == "local_implementation" and require_tool:
        constraints = getattr(state, "explicit_runtime_constraints", None)
        allow_subagents = bool(getattr(constraints, "allow_subagents", False))
        allowed_tool_classes_rule = (
            "You MUST choose only one of these tool classes: Bash, Read, Write, Edit, Update, or MultiEdit.\n"
            if not allow_subagents
            else "Choose the single best next tool for this workflow. Bash, Read, Write, Edit, Update, and MultiEdit remain preferred for direct local work.\n"
        )
        agent_rule = (
            "The user explicitly requested an agent in this turn. Agent is allowed when it is the best next step.\n"
            + agent_tool_rules()
            if allow_subagents
            else "You MUST NOT choose Agent.\n"
        )
        return (
            "You are a tool planner for Claude Code inside the local_implementation workflow.\n"
            "You are continuing a multi-step local coding task.\n"
            "You MUST stay in this workflow until the local implementation is complete or truly blocked.\n"
            f"{agent_rule}"
            "You MUST NOT choose any Task tool.\n"
            f"{allowed_tool_classes_rule}"
            "Prefer Write for new files and Read before modifying existing files.\n"
            "CRITICAL: Return ONLY tool name and file_path in input. Do NOT include file contents.\n"
            "File contents will be generated in a separate step.\n"
            "GOOD example: {\"tool\": \"Write\", \"input\": {\"file_path\": \"file.html\"}}\n"
            "BAD example (NEVER do this): {\"tool\": \"Write\", \"input\": {\"file_path\": \"file.html\", \"content\": \"<!DOCTYPE html>...\"}}\n"
            "GOOD example: {\"tool\": \"Edit\", \"input\": {\"file_path\": \"main.py\"}}\n"
            "BAD example (NEVER do this): {\"tool\": \"Edit\", \"input\": {\"file_path\": \"main.py\", \"old_string\": \"...\", \"new_string\": \"...\"}}\n"
            "GOOD example: {\"tool\": \"Bash\", \"input\": {\"command\": \"ls -la\"}}\n"
            "GOOD example: {\"tool\": \"Read\", \"input\": {\"file_path\": \"index.html\"}}\n"
            "MULTI-EDIT RULE: If the user asked for multiple changes to a file (e.g., 'make 3 changes', 'do X and Y and Z'), "
            "you must make EACH change as a SEPARATE Edit call, one at a time. "
            "After a successful Edit, do NOT stop — continue with the next Edit until ALL requested changes are done. "
            "Do NOT read the file again between edits unless the previous edit failed. "
            "Do NOT return null until every change the user requested has been applied.\n"
            "NEVER use Bash echo or printf just to display a message to the user — "
            "if you want to say something, simply return the text in your response instead of wrapping it in a Bash command. "
            "Bash should only be used for commands that actually do real work: running scripts, building, testing, installing, curl, ls, cat, etc.\n"
            "NEVER use Bash echo, printf, or inline source dumps to emit HTML/CSS/JS code. "
            "If you are creating or replacing file contents, use Write. If you are modifying existing file contents, use Edit, Update, or MultiEdit.\n"
            "NEVER use Bash with open, xdg-open, or similar commands to open files in a browser — this is a headless server with no GUI.\n"
            "IMPORTANT STOP CONDITION: Look at WORKFLOW CONTEXT. If the file(s) requested by the user have already been "
            "successfully created or modified, AND the user did NOT ask for additional actions like running, building, or testing, "
            "return {\"tool\": null} to signal completion. Do NOT keep modifying files that are already done. "
            "Do NOT invent extra steps the user did not ask for.\n"
            "If the task is genuinely not yet complete, return a tool call.\n"
            "Do NOT answer in natural language.\n"
            f"{user_tool_restrictions}"
            f"{subagent_tool_restrictions}"
            "Return ONLY valid JSON with this exact shape:\n"
            "{\"tool\": <string or null>, \"input\": <object>}\n\n"
            f"AVAILABLE TOOLS:\n{json.dumps([{'name': tool.get('name'), 'description': re.sub(r'\\s+', ' ', str(tool.get('description', ''))).strip()[:300], 'input_schema': tool_schema_excerpt_fn(tool)} for tool in state.tools[:24]], ensure_ascii=False, indent=2)}\n\n"
            f"WORKFLOW CONTEXT:\n{state.latest_dialogue}\n"
        )
    if state.current_workflow == "tavily_research" and state.workflow_step == "search":
        return (
            "You are a tool planner for Claude Code inside the tavily_research workflow.\n"
            "A tool call is required right now.\n"
            "You MUST choose exactly one Bash tool call.\n"
            "For the Tavily search step, use the canonical request shape shown below.\n"
            "Do NOT invent a GET request.\n"
            "Do NOT invent query parameters in the URL.\n"
            "Do NOT invent a different JSON schema.\n"
            "Do NOT use jq.\n"
            "Do NOT return {\"tool\": null}.\n"
            "Do NOT answer in natural language.\n"
            f"{user_tool_restrictions}"
            f"{subagent_tool_restrictions}"
        )
    if state.current_workflow == "session_recall" and state.workflow_step == "search":
        return (
            "You are a tool planner for Claude Code inside the session_recall workflow.\n"
            "A tool call is required right now.\n"
            "You MUST choose exactly one SessionSearch tool call.\n"
            "You MUST NOT choose Bash.\n"
            "You MUST NOT choose web_search.\n"
            "You MUST NOT choose web_extract.\n"
            "You MUST NOT choose Read, Write, Edit, or Agent.\n"
            "If the user explicitly requested a search mode such as lexical, semantic, or hybrid, preserve that mode.\n"
            "If the user did not explicitly request a mode, prefer hybrid.\n"
            "Use the user's latest request to form the SessionSearch query for past-session recall.\n"
            "Do NOT return {\"tool\": null}.\n"
            "Do NOT answer in natural language.\n"
            f"{user_tool_restrictions}"
            f"{subagent_tool_restrictions}"
            "Return ONLY valid JSON with this exact shape:\n"
            "{\"tool\": <string>, \"input\": <object>}\n\n"
            f"AVAILABLE TOOLS:\n{json.dumps([{'name': tool.get('name'), 'description': re.sub(r'\\s+', ' ', str(tool.get('description', ''))).strip()[:300], 'input_schema': tool_schema_excerpt_fn(tool)} for tool in state.tools[:24]], ensure_ascii=False, indent=2)}\n\n"
            f"WORKFLOW CONTEXT:\n{state.latest_dialogue}\n"
        )
    tools = state.tools
    tool_specs = []
    for tool in tools[:24]:
        tool_specs.append(
            {
                "name": tool.get("name"),
                "description": re.sub(r"\s+", " ", str(tool.get("description", ""))).strip()[:300],
                "input_schema": tool_schema_excerpt_fn(tool),
            }
        )

    if require_tool:
        mode_instruction = (
            "A tool call is required right now.\n"
            "Choose the single best next tool step to continue the existing task after the latest tool result.\n"
            "Do NOT return {\"tool\": null}.\n"
            "Do NOT answer in natural language.\n"
        )
    else:
        mode_instruction = (
            "Decide whether the assistant should call one of the provided tools right now.\n"
            "If no tool is needed and a normal text answer is better, return {\"tool\": null}.\n"
        )
    new_file_rule = (
        "IMPORTANT: The user explicitly requested creating a new file, and that file does not exist yet. "
        "You MUST NOT choose Read for that missing file. Prefer Write to create it.\n"
        if getattr(state, "explicit_user_file", None) and not getattr(state, "active_file_exists", False)
        else ""
    )

    return (
        "You are a tool planner for Claude Code.\n"
        f"{mode_instruction}"
        f"{user_tool_restrictions}"
        f"{subagent_tool_restrictions}"
        f"{new_file_rule}"
        f"{agent_tool_rules()}"
        "Prefer a tool call when the user asks to inspect files, run commands, read, write, edit, search, or otherwise act on the local environment.\n"
        "In this local setup, WebSearch and WebFetch are unavailable.\n"
        f"{http_curl_prompt_rules()}"
        "Return ONLY valid JSON with this exact shape:\n"
        "{\"tool\": <string or null>, \"input\": <object>}\n"
        "Do not include markdown fences or any extra text.\n"
        "CRITICAL: For Write/Edit/Update/MultiEdit, the input MUST be a MINIMAL object with ONLY the file path.\n"
        "Do NOT include file contents, old_string, new_string or any other large text in the input.\n"
        "The content will be generated separately.\n"
        "GOOD examples:\n"
        "  {\"tool\": \"Write\", \"input\": {\"file_path\": \"game.html\"}}\n"
        "  {\"tool\": \"Edit\", \"input\": {\"file_path\": \"main.py\"}}\n"
        "  {\"tool\": \"Bash\", \"input\": {\"command\": \"ls -la\"}}\n"
        "  {\"tool\": \"Read\", \"input\": {\"file_path\": \"index.html\"}}\n"
        "BAD examples (NEVER do this):\n"
        "  {\"tool\": \"Write\", \"input\": {\"file_path\": \"game.html\", \"content\": \"<!DOCTYPE html>...\"}} ← content will be generated separately, do NOT include it\n"
        "  {\"tool\": \"Edit\", \"input\": {\"file_path\": \"main.py\", \"old_string\": \"...\", \"new_string\": \"...\"}} ← edit details will be generated separately\n\n"
        f"AVAILABLE TOOLS:\n{json.dumps(tool_specs, ensure_ascii=False, indent=2)}\n\n"
        f"RECENT DIALOGUE:\n{state.latest_dialogue}\n"
    )


async def maybe_model_tool_use(
    request,
    state,
    *,
    generator,
    trace_event_fn,
    require_tool: bool = False,
    tool_schema_excerpt_fn,
    tool_lookup_fn,
    normalize_tool_input_fn,
    validate_tool_input_fn,
    validate_tool_policy_fn,
):
    tools = state.tools
    if not tools:
        return None

    async def run_planner(planner_prompt_text: str, *, retry_reason: str | None = None):
        planner_messages = [
            {"role": "system", "content": "Return only valid JSON."},
            {"role": "user", "content": planner_prompt_text},
        ]
        prompt = build_prompt(generator.tokenizer, planner_messages, False)
        params = GenerationParams(max_tokens=32000, temperature=0.0, top_p=1.0)
        result = await generator.generate(prompt, params)
        plan_candidates = [coerce_tool_plan_payload(payload) for payload in extract_json_objects(result.output_text)]
        plan_candidates = [candidate for candidate in plan_candidates if candidate is not None]
        plan = plan_candidates[0] if plan_candidates else parse_tool_plan(result.output_text)
        plan = _canonicalize_agent_subtype_plan(state, plan)
        trace_payload: dict[str, Any] = {
            "mode": state.normalized.mode,
            "require_tool": require_tool,
            "raw_output": result.output_text,
            "parsed_plan": plan,
            "parsed_plan_count": len(plan_candidates),
            "active_filename": state.active_filename,
        }
        if retry_reason:
            trace_payload["retry_reason"] = retry_reason
        trace_event_fn(request, "planner_result", **trace_payload)
        if not plan or plan["tool"] is None:
            salvaged_tool_use = validated_named_tool_use(
                state.req,
                state,
                parse_textual_tool_use(result.output_text, parse_json_object_fn=parse_json_object),
                tool_lookup=tool_lookup_fn,
                normalize_tool_input_fn=normalize_tool_input_fn,
                validate_tool_input_fn=validate_tool_input_fn,
                validate_tool_policy_fn=validate_tool_policy_fn,
            )
            if salvaged_tool_use is not None:
                trace_event_fn(request, "planner_salvaged_tool_use", mode=state.normalized.mode, tool=salvaged_tool_use.get("name"), tool_input=salvaged_tool_use.get("input"), retry_reason=retry_reason)
                return None, salvaged_tool_use
        return plan, None

    planner_prompt = build_tool_planner_prompt(state, require_tool=require_tool, tool_schema_excerpt_fn=tool_schema_excerpt_fn)
    plan, salvaged_tool_use = await run_planner(planner_prompt)
    if salvaged_tool_use is not None:
        return salvaged_tool_use
    if require_tool and _file_read_overflow_requires_bounded_retry(state) and not _read_plan_has_explicit_bounds(plan):
        retry_prompt = (
            planner_prompt
            + "\nCRITICAL RETRY INSTRUCTION:\n"
            + "Your previous response was invalid because it repeated an unbounded Read after a token-limit overflow.\n"
            + "Reply with exactly one Read tool call that includes explicit positive integer offset and limit fields.\n"
            + "Do not repeat a file_path-only Read.\n"
        )
        plan, salvaged_tool_use = await run_planner(retry_prompt, retry_reason="file_read_overflow_requires_bounds")
        if salvaged_tool_use is not None:
            return salvaged_tool_use
    if require_tool and (not plan or plan["tool"] is None):
        # local_implementation may intentionally return null when the task is complete
        if getattr(state, "current_workflow", None) == "local_implementation":
            return None
        retry_prompt = (
            planner_prompt
            + "\nCRITICAL RETRY INSTRUCTION:\n"
            + "Your previous response was invalid because it did not produce a valid tool call.\n"
            + "Reply with exactly one valid tool call JSON object now.\n"
            + "If you output plain text again, the response is invalid.\n"
        )
        plan, salvaged_tool_use = await run_planner(retry_prompt, retry_reason="missing_required_tool")
        if salvaged_tool_use is not None:
            return salvaged_tool_use
    if not plan or plan["tool"] is None:
        return None
    tool_name = plan["tool"]
    tool = state.tool_map.get(tool_name)
    if not tool:
        return None
    tool_input = plan["input"] if isinstance(plan.get("input"), dict) else {}
    tool_input = normalize_tool_input_fn(tool, tool_input)
    # For Write/Edit/Update/MultiEdit, the planner only returns file_path.
    # Content/old_string/new_string are generated in a separate step,
    # so skip full validation here — just check the file_path is present.
    is_write_or_edit = tool_name in {"Write", "Edit", "Update", "MultiEdit"}
    if is_write_or_edit and isinstance(tool_input.get("file_path"), str) and tool_input["file_path"].strip():
        is_valid = True
    else:
        is_valid, _ = validate_tool_input_fn(tool, tool_input)
        if not is_valid:
            return None
    policy_ok, policy_reason = validate_tool_policy_fn(state, tool_name, tool_input)
    retry_prompt = None
    retry_reason = None
    if (
        not policy_ok
        and require_tool
        and tool_name == "Read"
        and (
            policy_reason == "missing_file_target"
            or str(policy_reason).startswith("explicit_file_not_found:")
            or str(policy_reason).startswith("target_file_not_found:")
        )
    ):
        retry_prompt = (
            planner_prompt
            + "\nCRITICAL RETRY INSTRUCTION:\n"
            + "Your previous response was invalid because you chose Read for a file that does not exist yet.\n"
            + "The user asked to create a new file.\n"
            + "You MUST NOT choose Read for the missing target.\n"
            + "Reply with exactly one valid Write tool call that creates the requested file.\n"
        )
        retry_reason = "read_missing_target_requires_write"
    if retry_prompt is not None:
        plan, salvaged_tool_use = await run_planner(retry_prompt, retry_reason=retry_reason)
        if salvaged_tool_use is not None:
            return salvaged_tool_use
        if not plan or plan["tool"] is None:
            return None
        tool_name = plan["tool"]
        tool = state.tool_map.get(tool_name)
        if not tool:
            return None
        tool_input = plan["input"] if isinstance(plan.get("input"), dict) else {}
        tool_input = normalize_tool_input_fn(tool, tool_input)
        is_valid, _ = validate_tool_input_fn(tool, tool_input)
        if not is_valid:
            return None
        policy_ok, policy_reason = validate_tool_policy_fn(state, tool_name, tool_input)
    if not policy_ok:
        return None
    return {"id": f"toolu_{__import__('uuid').uuid4().hex[:24]}", "name": tool_name, "input": tool_input}
