import json
import re
from pathlib import Path
from typing import Any

from paroquant.inference import GenerationParams, build_prompt

from factory.api_server_core.file_policy import (
    extract_explicit_absolute_path,
    extract_project_root,
    is_within_project_root,
    sanitize_local_path_candidate,
    temporary_allowed_root_for_path,
)
from factory.api_server_core.policy_engine import policy_list
from factory.api_server_core.user_constraints import extract_explicit_runtime_constraints
from factory.api_server_core.workflow_engine import (
    failure_recovery_state,
    file_investigation_state,
    local_implementation_state,
    project_analysis_state,
    session_recall_state,
    tavily_research_state,
)

_SESSION_PROJECT_ROOTS: dict[str, str] = {}
_SELF_INSPECTION_PROJECT_FILES = {
    "handoff.md": "handoff.md",
    "ledger.md": "Ledger.md",
}


def _is_readonly_explore_subagent(normalized) -> bool:
    prompt_messages = getattr(normalized, "prompt_messages", None)
    if not isinstance(prompt_messages, list):
        return False
    for message in prompt_messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = str(message.get("content", "") or "")
        if (
            "You are a file search specialist for Claude Code" in content
            or "READ-ONLY exploration task" in content
        ):
            return True
    return False


def _should_block_tavily_for_explicit_local_scope(constraints) -> bool:
    return bool(
        getattr(constraints, "explicit_local_scope", False)
        and not getattr(constraints, "explicit_web_research", False)
    )


def _sanitize_history_path_for_project(path: str | None, project_root: str | None) -> str | None:
    candidate = sanitize_local_path_candidate(path)
    if not candidate:
        return None
    if not project_root:
        return None
    try:
        resolved = Path(candidate).expanduser().resolve()
    except Exception:
        return None
    return candidate if is_within_project_root(resolved, project_root) else None


def _extract_session_id(normalized: Any) -> str | None:
    oai_body = getattr(normalized, "oai_body", None)
    if not isinstance(oai_body, dict):
        return None
    raw = oai_body.get("__metadata_user_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    session_id = str(payload.get("session_id", "") or "").strip()
    return session_id or None


def _fallback_non_tavily_intent_for_local_scope(text: str, user_intent_cls) -> str:
    lowered = (text or "").lower()
    if any(
        marker in lowered
        for marker in (
            "создай",
            "сделай",
            "напиши",
            "implement",
            "create",
            "write",
            "modify",
            "edit",
            ".html",
            "файл",
            "file",
        )
    ):
        return user_intent_cls.LOCAL_IMPLEMENTATION
    return user_intent_cls.PROJECT_ANALYSIS


def _local_implementation_error_is_recoverable(error_text: str | None) -> bool:
    text = str(error_text or "")
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "File has not been read yet",
            "String to replace not found",
            "InputValidationError",
            "missing required field:",
            "unexpected fields:",
            "required parameter `content` is missing",
        )
    )


def _resolve_self_inspection_project_file(path: str | None, text: str) -> str | None:
    candidate = sanitize_local_path_candidate(path)
    if not candidate:
        return None
    if not _explicit_self_inspection_skill_requested(text):
        return candidate
    candidate_path = Path(candidate).expanduser()
    if candidate_path.is_absolute():
        return candidate

    project_filename = _SELF_INSPECTION_PROJECT_FILES.get(candidate_path.name.lower())
    if not project_filename:
        return candidate
    fallback: str | None = None
    for root_path in policy_list("file_tools", "additional_allowed_roots", default=[]):
        if not isinstance(root_path, str) or not root_path.strip():
            continue
        root = Path(root_path).expanduser()
        target = root / project_filename
        try:
            if not target.exists() or not target.is_file():
                continue
            resolved_target = str(target.resolve())
            if fallback is None:
                fallback = resolved_target
            if (root / "pyproject.toml").exists() and (root / "factory").is_dir():
                return resolved_target
        except OSError:
            continue
    if fallback:
        return fallback
    return candidate


def build_turn_state(
    req,
    normalized,
    *,
    turn_state_cls,
    extract_tools_fn,
    extract_last_user_text_fn,
    extract_latest_user_request_text_fn,
    find_explicit_filename_fn,
    extract_last_tool_file_fn,
    extract_last_successful_read_file_fn,
    extract_last_shell_cwd_fn,
    extract_recent_dialogue_fn,
    strip_system_reminder_fn,
    read_result_looks_successful_fn,
    file_investigation_related_files_fn,
    continued_read_input_fn,
):
    latest_user_text = extract_last_user_text_fn(req)
    latest_user_request = extract_latest_user_request_text_fn(req)
    constraints_source_text = latest_user_request or latest_user_text
    explicit_runtime_constraints = extract_explicit_runtime_constraints(constraints_source_text)
    is_readonly_explore_subagent = _is_readonly_explore_subagent(normalized)
    explicit_user_file = _resolve_self_inspection_project_file(
        find_explicit_filename_fn(latest_user_text),
        latest_user_text,
    )
    explicit_user_absolute_path = sanitize_local_path_candidate(extract_explicit_absolute_path(latest_user_text))
    session_id = _extract_session_id(normalized)
    project_root = extract_project_root(req) or (session_id and _SESSION_PROJECT_ROOTS.get(session_id))
    if project_root and session_id:
        _SESSION_PROJECT_ROOTS[session_id] = project_root
    last_tool_file = _sanitize_history_path_for_project(extract_last_tool_file_fn(req), project_root)
    last_successful_read_file = _sanitize_history_path_for_project(extract_last_successful_read_file_fn(req), project_root)
    current_shell_cwd = _sanitize_history_path_for_project(extract_last_shell_cwd_fn(req, initial_cwd=project_root), project_root)
    temporary_allowed_root = temporary_allowed_root_for_path(explicit_user_absolute_path, project_root)
    active_filename = explicit_user_absolute_path or explicit_user_file
    tools = extract_tools_fn(req)
    tool_map = {str(tool.get("name", "")): tool for tool in tools}
    requires_tool = bool(tools)
    workflow_state = (None, None, 0, [], [], [], [], [], [], [], [], [], None)
    should_infer_history_workflow = normalized.mode == "tool_result"
    if should_infer_history_workflow:
        workflow_state = session_recall_state(req, strip_system_reminder_fn)
        local_impl_candidate = local_implementation_state(req)
        prefer_local_implementation = (
            local_impl_candidate[0] == "local_implementation"
            and _local_implementation_error_is_recoverable(local_impl_candidate[-1])
        )
        if (
            workflow_state[0] is None
            and not is_readonly_explore_subagent
            and not _should_block_tavily_for_explicit_local_scope(explicit_runtime_constraints)
        ):
            workflow_state = tavily_research_state(req, strip_system_reminder_fn)
        if workflow_state[0] is None:
            if prefer_local_implementation:
                workflow_state = local_impl_candidate
            else:
                workflow_state = file_investigation_state(
                    req,
                    read_result_looks_successful_fn,
                    file_investigation_related_files_fn,
                    continued_read_input_fn,
                )
        if workflow_state[0] is None:
            workflow_state = local_impl_candidate
        if workflow_state[0] is None:
            workflow_state = failure_recovery_state(req)
    (
        current_workflow,
        workflow_step,
        workflow_step_count,
        collected_targets,
        collected_directories,
        covered_directories,
        remaining_directories,
        candidate_deep_branches,
        covered_deep_branches,
        remaining_deep_branches,
        candidate_related_files,
        collected_summaries,
        last_workflow_error,
    ) = workflow_state
    latest_dialogue = extract_recent_dialogue_fn(req)
    may_carry_history_file = bool(
        normalized.mode == "tool_result" or current_workflow is not None
    )
    if not active_filename and may_carry_history_file:
        active_filename = last_successful_read_file or last_tool_file
    if normalized.mode == "tool_result" and current_workflow == "file_investigation":
        active_target = active_filename or (collected_targets[0] if collected_targets else "")
        latest_summary = collected_summaries[-1] if collected_summaries else ""
        scoped_parts = [
            "ACTIVE WORKFLOW:\nfile_investigation",
            f"ORIGINAL USER REQUEST:\n{latest_user_request or latest_user_text or 'Continue the file investigation task.'}",
            f"ACTIVE TARGET:\n{active_target or '(unknown)'}",
        ]
        if latest_summary:
            scoped_parts.append(f"COLLECTED FILE EVIDENCE:\n{latest_summary}")
        if last_workflow_error:
            scoped_parts.append(f"LATEST WORKFLOW ERROR:\n{last_workflow_error}")
        if (
            last_workflow_error
            and "exceeds maximum allowed tokens" in last_workflow_error
            and "Use offset and limit parameters" in last_workflow_error
        ):
            scoped_parts.append(
                "CONTINUATION POLICY:\n"
                "The next Read must use explicit positive integer offset and limit values.\n"
                "Do not repeat a file_path-only Read.\n"
                "Continue from the next unread chunk after the latest successful range."
            )
        if candidate_related_files:
            scoped_parts.append(
                "ALLOWED RELATED FILES:\n" + "\n".join(candidate_related_files[:20])
            )
        latest_dialogue = "\n\n".join(scoped_parts)
    return turn_state_cls(
        req=req,
        normalized=normalized,
        session_id=session_id,
        tools=tools,
        tool_map=tool_map,
        latest_user_text=latest_user_text,
        latest_dialogue=latest_dialogue,
        explicit_runtime_constraints=explicit_runtime_constraints,
        is_readonly_explore_subagent=is_readonly_explore_subagent,
        explicit_user_file=explicit_user_file,
        explicit_user_absolute_path=explicit_user_absolute_path,
        last_tool_file=last_tool_file,
        last_successful_read_file=last_successful_read_file,
        current_shell_cwd=current_shell_cwd,
        project_root=project_root,
        temporary_allowed_root=temporary_allowed_root,
        active_filename=active_filename,
        resolved_active_path=None,
        active_file_exists=None,
        direct_bash_command=None,
        explicit_read=False,
        explicit_write=False,
        explicit_edit=False,
        requires_tool=requires_tool,
        missing_file_target=False,
        current_workflow=current_workflow,
        workflow_step=workflow_step,
        workflow_step_count=workflow_step_count,
        workflow_started_by=current_workflow,
        collected_targets=collected_targets,
        collected_directories=collected_directories,
        covered_directories=covered_directories,
        remaining_directories=remaining_directories,
        candidate_deep_branches=candidate_deep_branches,
        covered_deep_branches=covered_deep_branches,
        remaining_deep_branches=remaining_deep_branches,
        candidate_related_files=candidate_related_files,
        collected_summaries=collected_summaries,
        last_workflow_error=last_workflow_error,
        policy_denial=None,
    )


def apply_workflow_state(state, workflow_state) -> None:
    (
        state.current_workflow,
        state.workflow_step,
        state.workflow_step_count,
        state.collected_targets,
        state.collected_directories,
        state.covered_directories,
        state.remaining_directories,
        state.candidate_deep_branches,
        state.covered_deep_branches,
        state.remaining_deep_branches,
        state.candidate_related_files,
        state.collected_summaries,
        state.last_workflow_error,
    ) = workflow_state
    state.workflow_started_by = state.current_workflow


# Known tool names from schemas and the workflow they belong to.
# This maps exact tool identifiers to the intent whose workflow supports them.
_EXPLICIT_TOOL_INTENT_MAP: dict[str, str] = {
    "SessionSearch": "session_recall",
}


def _explicit_self_inspection_skill_requested(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?<![\w-])(?:skill\s+|скилл\s+|навык\s+|/)?igorek-self-inspection(?![\w-])",
            text or "",
        )
    )


def _structural_intent_reroute(text: str, chosen_intent: str, user_intent_cls) -> str | None:
    """Check if the user explicitly named a tool that the chosen intent's workflow
    does not support. If so, return the correct intent. Otherwise return None.

    This is structural validation, not a verbal heuristic: it matches exact tool
    identifiers from the schema against the chosen workflow's allowed tool classes.
    """
    if chosen_intent in {user_intent_cls.OTHER, user_intent_cls.EXECUTE_COMMAND}:
        return None
    if _explicit_self_inspection_skill_requested(text):
        return user_intent_cls.LOCAL_IMPLEMENTATION
    for tool_name, correct_intent in _EXPLICIT_TOOL_INTENT_MAP.items():
        if tool_name in text and chosen_intent != correct_intent:
            return correct_intent
    return None


async def route_user_intent(
    request,
    user_text: str,
    *,
    generator,
    prompt_mode_cls,
    user_intent_cls,
    parse_json_object_fn,
    trace_event_fn,
):
    text = user_text.strip()
    if not text:
        return user_intent_cls.OTHER
    explicit_runtime_constraints = extract_explicit_runtime_constraints(text)

    prompt = build_prompt(
        generator.tokenizer,
        [
            {
                "role": "system",
                "content": (
                    "You are an intent router. "
                    "Classify the user's request into exactly one intent. "
                    "Return ONLY JSON: {\"intent\":\"<value>\"}\n\n"
                    "DECISION TREE — evaluate from top to bottom, pick the FIRST match:\n\n"
                    "1. Does the request name the tool SessionSearch,\n"
                    "   or ask about past sessions, previous chats, conversation history,\n"
                    "   what was discussed before, or what we talked about earlier?\n"
                    "   -> session_recall\n\n"
                    "2. Does the request ask to run or execute an explicit shell command?\n"
                    "   -> execute_command\n\n"
                    "3. Does the request ask to create or modify files, write code,\n"
                    "   or implement a feature in the workspace?\n"
                    "   -> local_implementation\n\n"
                    "4. Does the request ask for an overview of the whole project or codebase?\n"
                    "   -> project_analysis\n\n"
                    "5. Does the request ask to inspect, read, or examine one specific file?\n"
                    "   -> file_investigation\n\n"
                    "6. Does the request explicitly ask for web research from multiple sites,\n"
                    "   multi-source comparison, or collecting evidence from multiple web pages?\n"
                    "   -> tavily_research\n\n"
                    "7. Everything else -> other\n\n"
                    "CRITICAL RULES:\n"
                    "- session_recall is checked FIRST. If the request mentions SessionSearch\n"
                    "  or past conversations, it is ALWAYS session_recall, NEVER tavily_research.\n"
                    "- tavily_research is ONLY for web research that requires multiple web pages.\n"
                    "- Short factual questions (weather, prices, exchange rates) are NOT tavily_research -> other.\n"
                    "- If unsure, prefer other over tavily_research.\n\n"
                    "Examples:\n"
                    "\"Используй SessionSearch в режиме semantic и найди прошлые обсуждения\" -> session_recall\n"
                    "\"Что мы обсуждали раньше про security store?\" -> session_recall\n"
                    "\"Найди в прошлых сессиях упоминания approval runtime\" -> session_recall\n"
                    "\"Что пишут разные сайты о новом релизе OpenAI?\" -> tavily_research\n"
                    "\"Сравни мнения источников о рынке биткоина\" -> tavily_research\n"
                    "\"Какая погода в Петербурге?\" -> other\n"
                    "\"Узнай цену ethereum\" -> other\n"
                ),
            },
            {"role": "user", "content": f"User request:\n{text}"},
        ],
        False,
    )
    result = await generator.generate(
        prompt,
        GenerationParams(max_tokens=128, temperature=0.0, top_p=1.0),
    )
    payload = parse_json_object_fn(result.output_text)
    intent = str((payload or {}).get("intent", "")).strip()
    if intent not in {
        user_intent_cls.EXECUTE_COMMAND,
        user_intent_cls.PROJECT_ANALYSIS,
        user_intent_cls.FILE_INVESTIGATION,
        user_intent_cls.LOCAL_IMPLEMENTATION,
        user_intent_cls.SESSION_RECALL,
        user_intent_cls.TAVILY_RESEARCH,
        user_intent_cls.OTHER,
    }:
        intent = user_intent_cls.OTHER
    raw_intent = intent
    if intent == user_intent_cls.TAVILY_RESEARCH and _should_block_tavily_for_explicit_local_scope(explicit_runtime_constraints):
        intent = _fallback_non_tavily_intent_for_local_scope(text, user_intent_cls)
    # Structural post-validation: if the user explicitly named a tool that
    # belongs to a different workflow than what the model chose, reroute.
    # This is NOT a verbal heuristic — it checks exact tool identifiers
    # from the schema against the chosen workflow's allowed_tool_classes.
    rerouted_intent = _structural_intent_reroute(text, intent, user_intent_cls)
    if rerouted_intent is not None:
        trace_event_fn(
            request,
            "intent_rerouted",
            user_text=text,
            from_intent=intent,
            to_intent=rerouted_intent,
            raw_output=result.output_text,
        )
        intent = rerouted_intent
    trace_event_fn(
        request,
        "intent_router",
        user_text=text,
        intent=intent,
        raw_intent=raw_intent,
        raw_output=result.output_text,
        explicit_local_scope=getattr(explicit_runtime_constraints, "explicit_local_scope", False),
        explicit_web_research=getattr(explicit_runtime_constraints, "explicit_web_research", False),
    )
    return intent


async def maybe_attach_project_analysis_workflow(
    request,
    state,
    *,
    route_user_intent_fn,
    extract_latest_user_request_text_fn,
    trace_event_fn,
):
    if state.normalized.mode != "tool_result":
        return
    if state.current_workflow is not None:
        return
    candidate_state = project_analysis_state(state.req)
    if candidate_state[0] is None:
        return
    request_text = extract_latest_user_request_text_fn(state.req)
    intent = await route_user_intent_fn(request, request_text)
    if intent != "project_analysis":
        trace_event_fn(
            request,
            "project_analysis_skipped",
            latest_user_text=request_text,
            intent=intent,
            candidate_workflow=candidate_state[0],
        )
        return
    apply_workflow_state(state, candidate_state)
    trace_event_fn(
        request,
        "project_analysis_attached",
        latest_user_text=request_text,
        workflow=state.current_workflow,
        workflow_step=state.workflow_step,
        workflow_step_count=state.workflow_step_count,
    )


def initial_project_analysis_workflow_state():
    return ("project_analysis", "scan_root", 1, [], [], [], [], [], [], [], [], [], None)


def initial_file_investigation_workflow_state():
    return ("file_investigation", "read_primary", 1, [], [], [], [], [], [], [], [], [], None)


def initial_tavily_research_workflow_state():
    return ("tavily_research", "search", 1, [], [], [], [], [], [], [], [], [], None)


def initial_session_recall_workflow_state():
    return ("session_recall", "search", 1, [], [], [], [], [], [], [], [], [], None)


def initial_local_implementation_workflow_state():
    return ("local_implementation", "continue", 1, [], [], [], [], [], [], [], [], [], None)


async def maybe_start_initial_workflow(
    request,
    state,
    *,
    route_user_intent_fn,
    trace_event_fn,
):
    if state.current_workflow is not None:
        return
    if state.normalized.mode not in {"normal", "startup_reminder"}:
        return
    if not state.requires_tool:
        return

    request_text = state.latest_user_text.strip()
    if not request_text:
        return

    intent = await route_user_intent_fn(request, request_text)
    trace_event_fn(
        request,
        "initial_intent_routed",
        latest_user_text=request_text,
        intent=intent,
        mode=state.normalized.mode,
    )
    if intent == "tavily_research" and getattr(state, "is_readonly_explore_subagent", False):
        trace_event_fn(
            request,
            "tavily_research_blocked_for_explore_subagent",
            latest_user_text=request_text,
            mode=state.normalized.mode,
        )
        return
    if intent == "project_analysis":
        apply_workflow_state(state, initial_project_analysis_workflow_state())
        trace_event_fn(request, "project_analysis_initialized", latest_user_text=request_text, workflow=state.current_workflow, workflow_step=state.workflow_step, workflow_step_count=state.workflow_step_count, mode=state.normalized.mode)
        return
    if intent == "file_investigation":
        apply_workflow_state(state, initial_file_investigation_workflow_state())
        trace_event_fn(request, "file_investigation_initialized", latest_user_text=request_text, workflow=state.current_workflow, workflow_step=state.workflow_step, workflow_step_count=state.workflow_step_count, mode=state.normalized.mode)
        return
    if intent == "session_recall":
        apply_workflow_state(state, initial_session_recall_workflow_state())
        trace_event_fn(request, "session_recall_initialized", latest_user_text=request_text, workflow=state.current_workflow, workflow_step=state.workflow_step, workflow_step_count=state.workflow_step_count, mode=state.normalized.mode)
        return
    if intent == "tavily_research":
        apply_workflow_state(state, initial_tavily_research_workflow_state())
        trace_event_fn(request, "tavily_research_initialized", latest_user_text=request_text, workflow=state.current_workflow, workflow_step=state.workflow_step, workflow_step_count=state.workflow_step_count, mode=state.normalized.mode)
        return
    if intent == "local_implementation":
        apply_workflow_state(state, initial_local_implementation_workflow_state())
        trace_event_fn(request, "local_implementation_initialized", latest_user_text=request_text, workflow=state.current_workflow, workflow_step=state.workflow_step, workflow_step_count=state.workflow_step_count, mode=state.normalized.mode)
