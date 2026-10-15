"""
Pydantic models, dataclasses, and type definitions for the API server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from factory.api_server_core.user_constraints import ExplicitRuntimeConstraints


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    messages: list[ChatMessage]
    max_tokens: int = 32000
    temperature: float = 0.6
    top_p: float = 1.0
    top_k: int = -1
    repetition_penalty: float = 1.0
    stream: bool = False


class AnthropicTextBlock(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "text"
    text: str


class AnthropicMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: str | list[dict[str, Any]]


class AnthropicMessagesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    system: str | list[dict[str, Any]] | None = None
    messages: list[AnthropicMessage]
    max_tokens: int = 32000
    temperature: float = 0.6
    top_p: float = 1.0
    stream: bool = False


class CountTokensRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    system: str | list[dict[str, Any]] | None = None
    messages: list[AnthropicMessage]


# ---------------------------------------------------------------------------
# Prompt mode and user intent constants
# ---------------------------------------------------------------------------

class PromptMode:
    NORMAL = "normal"
    STARTUP_REMINDER = "startup_reminder"
    SUGGESTION = "suggestion"
    TOOL_RESULT = "tool_result"


class UserIntent:
    EXECUTE_COMMAND = "execute_command"
    PROJECT_ANALYSIS = "project_analysis"
    FILE_INVESTIGATION = "file_investigation"
    LOCAL_IMPLEMENTATION = "local_implementation"
    SESSION_RECALL = "session_recall"
    TAVILY_RESEARCH = "tavily_research"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Dataclasses for internal state
# ---------------------------------------------------------------------------

@dataclass
class NormalizedAnthropicRequest:
    mode: str
    oai_body: dict[str, Any]
    prompt_messages: list[dict[str, str]]
    prompt: str
    compaction: dict[str, Any] | None = None


@dataclass
class ToolTurnDecision:
    tool_use: dict[str, Any]
    source: str
    preamble: str = ""


@dataclass
class AnthropicTurnState:
    req: AnthropicMessagesRequest
    normalized: NormalizedAnthropicRequest
    session_id: str | None
    tools: list[dict[str, Any]]
    tool_map: dict[str, dict[str, Any]]
    latest_user_text: str
    latest_dialogue: str
    explicit_runtime_constraints: ExplicitRuntimeConstraints
    is_readonly_explore_subagent: bool
    explicit_user_file: str | None
    explicit_user_absolute_path: str | None
    last_tool_file: str | None
    last_successful_read_file: str | None
    current_shell_cwd: str | None
    project_root: str | None
    temporary_allowed_root: str | None
    active_filename: str | None
    resolved_active_path: str | None
    active_file_exists: bool | None
    direct_bash_command: str | None
    explicit_read: bool
    explicit_write: bool
    explicit_edit: bool
    requires_tool: bool
    missing_file_target: bool
    current_workflow: str | None
    workflow_step: str | None
    workflow_step_count: int
    workflow_started_by: str | None
    collected_targets: list[str]
    collected_directories: list[str]
    covered_directories: list[str]
    remaining_directories: list[str]
    candidate_deep_branches: list[str]
    covered_deep_branches: list[str]
    remaining_deep_branches: list[str]
    candidate_related_files: list[str]
    collected_summaries: list[str]
    last_workflow_error: str | None
    policy_denial: str | None = None


@dataclass
class AnthropicResponseState:
    message_id: str
    input_tokens: int
    raw_output: str = ""
    emitted_output: str = ""
    started_text_stream: bool = False
