"""Application-level events used to coordinate UI actions (port of app_event.rs).

`AppEvent` is the internal message bus between UI components and the top-level
`App` loop. Widgets emit events to request actions that must be handled at the
app layer (e.g. opening pickers, persisting configuration, shutting down the
agent) without needing direct access to `App` internals.

Exit is modelled explicitly via `AppEvent::Exit(ExitMode)` so callers can
request shutdown-first quits without reaching into the app loop or coupling to
shutdown/exit sequencing.

This is a one-to-one Python port of ``codex-rs/tui/src/app_event.rs`` (1051 lines).
Production code only; Rust ``#[cfg(test)]`` modules are not ported; Python regressions are maintained separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Sub-enums and sub-types
# ---------------------------------------------------------------------------

class RealtimeAudioDeviceKind(Enum):
    Microphone = "microphone"
    Speaker = "speaker"

    def title(self) -> str:
        return "Microphone" if self == RealtimeAudioDeviceKind.Microphone else "Speaker"

    def noun(self) -> str:
        return "microphone" if self == RealtimeAudioDeviceKind.Microphone else "speaker"


class ThreadGoalSetModeKind(Enum):
    ConfirmIfExists = "confirm_if_exists"
    ReplaceExisting = "replace_existing"
    UpdateExisting = "update_existing"


@dataclass(frozen=True)
class ThreadGoalSetMode:
    """Port of ``codex::app_event::ThreadGoalSetMode``.

    The Rust enum has a struct variant ``UpdateExisting { status, token_budget }``;
    we model that with ``kind == UpdateExisting`` plus two optional fields.
    """
    kind: ThreadGoalSetModeKind
    status: Optional[Any] = None
    token_budget: Optional[int] = None

    @staticmethod
    def confirm_if_exists() -> "ThreadGoalSetMode":
        return ThreadGoalSetMode(ThreadGoalSetModeKind.ConfirmIfExists)

    @staticmethod
    def replace_existing() -> "ThreadGoalSetMode":
        return ThreadGoalSetMode(ThreadGoalSetModeKind.ReplaceExisting)

    @staticmethod
    def update_existing(status: Any, token_budget: Optional[int] = None) -> "ThreadGoalSetMode":
        return ThreadGoalSetMode(ThreadGoalSetModeKind.UpdateExisting, status=status, token_budget=token_budget)


@dataclass
class HistoryLookupResponse:
    """Port of ``codex::app_event::HistoryLookupResponse``."""
    offset: int
    log_id: int
    entry: Optional[str]


class ConsolidationScrollbackReflow(Enum):
    IfResizeReflowRan = "if_resize_reflow_ran"
    Required = "required"


class WindowsSandboxEnableMode(Enum):
    Elevated = "elevated"
    Legacy = "legacy"


@dataclass
class ConnectorsSnapshot:
    """Port of ``codex::app_event::ConnectorsSnapshot`` (Windows cfg-gated in Rust)."""
    connectors: List[Any] = field(default_factory=list)


class RateLimitRefreshOriginKind(Enum):
    """Discriminator for ``RateLimitRefreshOrigin`` (port of codex enum)."""
    StartupPrefetch = "startup_prefetch"
    StatusCommand = "status_command"


@dataclass(frozen=True)
class RateLimitRefreshOrigin:
    """Distinguishes why a rate-limit refresh was requested so the completion
    handler can route the result correctly.

    A ``StartupPrefetch`` fires once, concurrently with the rest of TUI init,
    and only updates the cached snapshots (no status card to finalize). A
    ``StatusCommand`` is tied to a specific ``/status`` invocation and must
    call ``finish_status_rate_limit_refresh`` when done so the card stops
    showing a "refreshing" state.
    """
    kind: RateLimitRefreshOriginKind
    request_id: int = 0

    @staticmethod
    def startup_prefetch() -> "RateLimitRefreshOrigin":
        return RateLimitRefreshOrigin(RateLimitRefreshOriginKind.StartupPrefetch)

    @staticmethod
    def status_command(request_id: int) -> "RateLimitRefreshOrigin":
        return RateLimitRefreshOrigin(RateLimitRefreshOriginKind.StatusCommand, request_id=request_id)


class KeymapEditIntentKind(Enum):
    ReplaceAll = "replace_all"
    AddAlternate = "add_alternate"
    ReplaceOne = "replace_one"


@dataclass(frozen=True)
class KeymapEditIntent:
    """Port of ``codex::app_event::KeymapEditIntent``.

    Rust enum has a struct variant ``ReplaceOne { old_key: String }``; we
    flatten that into ``kind`` + ``old_key``.
    """
    kind: KeymapEditIntentKind
    old_key: str = ""

    @staticmethod
    def replace_all() -> "KeymapEditIntent":
        return KeymapEditIntent(KeymapEditIntentKind.ReplaceAll)

    @staticmethod
    def add_alternate() -> "KeymapEditIntent":
        return KeymapEditIntent(KeymapEditIntentKind.AddAlternate)

    @staticmethod
    def replace_one(old_key: str) -> "KeymapEditIntent":
        return KeymapEditIntent(KeymapEditIntentKind.ReplaceOne, old_key=old_key)


class ExitModeKind(Enum):
    ShutdownFirst = "shutdown_first"
    Immediate = "immediate"


@dataclass(frozen=True)
class ExitMode:
    """Port of ``codex::app_event::ExitMode``."""
    kind: ExitModeKind

    @staticmethod
    def shutdown_first() -> "ExitMode":
        return ExitMode(ExitModeKind.ShutdownFirst)

    @staticmethod
    def immediate() -> "ExitMode":
        return ExitMode(ExitModeKind.Immediate)


class FeedbackCategory(Enum):
    """Port of ``codex::app_event::FeedbackCategory``."""
    BadResult = "bad_result"
    GoodResult = "good_result"
    Bug = "bug"
    SafetyCheck = "safety_check"
    Other = "other"


class ApprovalRequestKind(Enum):
    Exec = "exec"
    Patch = "patch"
    Generic = "generic"


@dataclass
class PermissionProfileSelection:
    """Port of ``codex::app_event::PermissionProfileSelection``.

    Named profile selection to apply after any required UI guardrails complete.
    """
    profile_id: str
    display_label: str
    approval_policy: Optional[Any] = None
    approvals_reviewer: Optional[Any] = None


@dataclass
class RealtimeWebrtcOffer:
    """Port of ``codex::app_event::RealtimeWebrtcOffer``."""
    offer_sdp: str
    handle: Any  # RealtimeWebrtcSessionHandle analogue (opaque type)


# ---------------------------------------------------------------------------
# AppEvent
# ---------------------------------------------------------------------------

@dataclass
class AppEvent:
    """Port of ``codex::app_event::AppEvent`` (~140 variants, one-to-one).

    The Rust ``AppEvent`` is a single large enum; the Python port represents
    the same surface as a dataclass with a ``kind`` discriminator plus a
    ``data`` payload dictionary. Static-method constructors preserve the
    build API used throughout the TUI code (e.g. ``AppEvent.new_session()``).
    """
    kind: str
    data: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Agent / thread routing
    # ------------------------------------------------------------------

    @staticmethod
    def open_agent_picker() -> "AppEvent":
        return AppEvent("OpenAgentPicker")

    @staticmethod
    def select_agent_thread(thread_id: Any) -> "AppEvent":
        return AppEvent("SelectAgentThread", {"thread_id": thread_id})

    @staticmethod
    def start_side(parent_thread_id: Any, user_message: Any) -> "AppEvent":
        return AppEvent("StartSide", {
            "parent_thread_id": parent_thread_id,
            "user_message": user_message,
        })

    @staticmethod
    def submit_thread_op(thread_id: Any, op: Any) -> "AppEvent":
        return AppEvent("SubmitThreadOp", {"thread_id": thread_id, "op": op})

    @staticmethod
    def thread_history_entry_response(thread_id: Any, event: HistoryLookupResponse) -> "AppEvent":
        return AppEvent("ThreadHistoryEntryResponse", {"thread_id": thread_id, "event": event})

    @staticmethod
    def append_message_history_entry(thread_id: Any, text: str) -> "AppEvent":
        return AppEvent("AppendMessageHistoryEntry", {"thread_id": thread_id, "text": text})

    @staticmethod
    def sync_thread_git_branch(thread_id: Any, branch: str) -> "AppEvent":
        return AppEvent("SyncThreadGitBranch", {"thread_id": thread_id, "branch": branch})

    @staticmethod
    def lookup_message_history_entry(thread_id: Any, offset: int, log_id: int) -> "AppEvent":
        return AppEvent("LookupMessageHistoryEntry", {
            "thread_id": thread_id, "offset": offset, "log_id": log_id,
        })

    @staticmethod
    def new_session() -> "AppEvent":
        return AppEvent("NewSession")

    @staticmethod
    def startup_thread_started(result: Any) -> "AppEvent":
        return AppEvent("StartupThreadStarted", {"result": result})

    @staticmethod
    def clear_ui() -> "AppEvent":
        return AppEvent("ClearUi")

    @staticmethod
    def raw_output_mode_changed(enabled: bool) -> "AppEvent":
        return AppEvent("RawOutputModeChanged", {"enabled": enabled})

    @staticmethod
    def clear_ui_and_submit_user_message(text: str) -> "AppEvent":
        """Plan Mode handoff: clear UI, start fresh session, submit ``text``."""
        return AppEvent("ClearUiAndSubmitUserMessage", {"text": text})

    @staticmethod
    def open_resume_picker() -> "AppEvent":
        return AppEvent("OpenResumePicker")

    @staticmethod
    def resume_session_by_id_or_name(value: str) -> "AppEvent":
        return AppEvent("ResumeSessionByIdOrName", {"value": value})

    @staticmethod
    def archive_current_thread() -> "AppEvent":
        return AppEvent("ArchiveCurrentThread")

    @staticmethod
    def fork_current_session() -> "AppEvent":
        return AppEvent("ForkCurrentSession")

    @staticmethod
    def exit_request(mode: ExitMode) -> "AppEvent":
        return AppEvent("Exit", {"mode": mode})

    @staticmethod
    def logout() -> "AppEvent":
        return AppEvent("Logout")

    @staticmethod
    def fatal_exit_request(reason: str) -> "AppEvent":
        return AppEvent("FatalExitRequest", {"reason": reason})

    @staticmethod
    def codex_op(op: Any) -> "AppEvent":
        """Forward a command to the Agent. Using an AppEvent for this avoids
        bubbling channels through layers of widgets."""
        return AppEvent("CodexOp", {"op": op})

    @staticmethod
    def approve_recent_auto_review_denial(thread_id: Any, id: str) -> "AppEvent":
        return AppEvent("ApproveRecentAutoReviewDenial", {"thread_id": thread_id, "id": id})

    @staticmethod
    def start_file_search(query: str) -> "AppEvent":
        return AppEvent("StartFileSearch", {"query": query})

    @staticmethod
    def file_search_result(query: str, matches: List[Any]) -> "AppEvent":
        return AppEvent("FileSearchResult", {"query": query, "matches": matches})

    @staticmethod
    def refresh_rate_limits(origin: RateLimitRefreshOrigin) -> "AppEvent":
        return AppEvent("RefreshRateLimits", {"origin": origin})

    @staticmethod
    def open_thread_goal_menu(thread_id: Any) -> "AppEvent":
        return AppEvent("OpenThreadGoalMenu", {"thread_id": thread_id})

    @staticmethod
    def open_thread_goal_editor(thread_id: Optional[Any]) -> "AppEvent":
        return AppEvent("OpenThreadGoalEditor", {"thread_id": thread_id})

    @staticmethod
    def set_thread_goal_objective(thread_id: Any, objective: str, mode: ThreadGoalSetMode) -> "AppEvent":
        return AppEvent("SetThreadGoalObjective", {
            "thread_id": thread_id, "objective": objective, "mode": mode,
        })

    @staticmethod
    def set_thread_goal_status(thread_id: Any, status: Any) -> "AppEvent":
        return AppEvent("SetThreadGoalStatus", {"thread_id": thread_id, "status": status})

    @staticmethod
    def clear_thread_goal(thread_id: Any) -> "AppEvent":
        return AppEvent("ClearThreadGoal", {"thread_id": thread_id})

    @staticmethod
    def rate_limits_loaded(origin: RateLimitRefreshOrigin, result: Any) -> "AppEvent":
        return AppEvent("RateLimitsLoaded", {"origin": origin, "result": result})

    @staticmethod
    def send_add_credits_nudge_email(credit_type: Any) -> "AppEvent":
        return AppEvent("SendAddCreditsNudgeEmail", {"credit_type": credit_type})

    @staticmethod
    def add_credits_nudge_email_finished(result: Any) -> "AppEvent":
        return AppEvent("AddCreditsNudgeEmailFinished", {"result": result})

    @staticmethod
    def connectors_loaded(result: Any, is_final: bool) -> "AppEvent":
        return AppEvent("ConnectorsLoaded", {"result": result, "is_final": is_final})

    @staticmethod
    def diff_result(text: str) -> "AppEvent":
        return AppEvent("DiffResult", {"text": text})

    @staticmethod
    def open_app_link(app_id: str, title: str, description: Optional[str],
                      instructions: str, url: str,
                      is_installed: bool, is_enabled: bool) -> "AppEvent":
        return AppEvent("OpenAppLink", {
            "app_id": app_id, "title": title, "description": description,
            "instructions": instructions, "url": url,
            "is_installed": is_installed, "is_enabled": is_enabled,
        })

    @staticmethod
    def open_url_in_browser(url: str) -> "AppEvent":
        return AppEvent("OpenUrlInBrowser", {"url": url})

    # ------------------------------------------------------------------
    # Pets
    # ------------------------------------------------------------------

    @staticmethod
    def pet_selected(pet_id: str) -> "AppEvent":
        return AppEvent("PetSelected", {"pet_id": pet_id})

    @staticmethod
    def pet_disabled() -> "AppEvent":
        return AppEvent("PetDisabled")

    @staticmethod
    def pet_preview_requested(pet_id: str) -> "AppEvent":
        return AppEvent("PetPreviewRequested", {"pet_id": pet_id})

    @staticmethod
    def pet_preview_loaded(request_id: int, result: Any) -> "AppEvent":
        return AppEvent("PetPreviewLoaded", {"request_id": request_id, "result": result})

    @staticmethod
    def pet_selection_loaded(request_id: int, pet_id: str, result: Any) -> "AppEvent":
        return AppEvent("PetSelectionLoaded", {
            "request_id": request_id, "pet_id": pet_id, "result": result,
        })

    @staticmethod
    def configured_pet_loaded(pet_id: str, result: Any) -> "AppEvent":
        return AppEvent("ConfiguredPetLoaded", {"pet_id": pet_id, "result": result})

    # ------------------------------------------------------------------
    # Connectors / Plugins / Hooks
    # ------------------------------------------------------------------

    @staticmethod
    def refresh_connectors(force_refetch: bool) -> "AppEvent":
        return AppEvent("RefreshConnectors", {"force_refetch": force_refetch})

    @staticmethod
    def fetch_connectors_list(force_refetch: bool) -> "AppEvent":
        return AppEvent("FetchConnectorsList", {"force_refetch": force_refetch})

    @staticmethod
    def fetch_plugins_list(cwd: Path) -> "AppEvent":
        return AppEvent("FetchPluginsList", {"cwd": cwd})

    @staticmethod
    def fetch_hooks_list(cwd: Path) -> "AppEvent":
        return AppEvent("FetchHooksList", {"cwd": cwd})

    @staticmethod
    def plugins_loaded(cwd: Path, result: Any) -> "AppEvent":
        return AppEvent("PluginsLoaded", {"cwd": cwd, "result": result})

    @staticmethod
    def hooks_loaded(cwd: Path, result: Any) -> "AppEvent":
        return AppEvent("HooksLoaded", {"cwd": cwd, "result": result})

    @staticmethod
    def open_marketplace_add_prompt() -> "AppEvent":
        return AppEvent("OpenMarketplaceAddPrompt")

    @staticmethod
    def open_marketplace_add_loading(source: str) -> "AppEvent":
        return AppEvent("OpenMarketplaceAddLoading", {"source": source})

    @staticmethod
    def fetch_marketplace_add(cwd: Path, source: str) -> "AppEvent":
        return AppEvent("FetchMarketplaceAdd", {"cwd": cwd, "source": source})

    @staticmethod
    def marketplace_add_loaded(cwd: Path, source: str, result: Any) -> "AppEvent":
        return AppEvent("MarketplaceAddLoaded", {"cwd": cwd, "source": source, "result": result})

    @staticmethod
    def open_marketplace_remove_confirm(marketplace_name: str, marketplace_display_name: str) -> "AppEvent":
        return AppEvent("OpenMarketplaceRemoveConfirm", {
            "marketplace_name": marketplace_name,
            "marketplace_display_name": marketplace_display_name,
        })

    @staticmethod
    def open_marketplace_remove_loading(marketplace_display_name: str) -> "AppEvent":
        return AppEvent("OpenMarketplaceRemoveLoading", {"marketplace_display_name": marketplace_display_name})

    @staticmethod
    def fetch_marketplace_remove(cwd: Path, marketplace_name: str, marketplace_display_name: str) -> "AppEvent":
        return AppEvent("FetchMarketplaceRemove", {
            "cwd": cwd, "marketplace_name": marketplace_name,
            "marketplace_display_name": marketplace_display_name,
        })

    @staticmethod
    def marketplace_remove_loaded(cwd: Path, marketplace_name: str, marketplace_display_name: str,
                                  result: Any) -> "AppEvent":
        return AppEvent("MarketplaceRemoveLoaded", {
            "cwd": cwd, "marketplace_name": marketplace_name,
            "marketplace_display_name": marketplace_display_name, "result": result,
        })

    @staticmethod
    def open_marketplace_upgrade_loading(marketplace_name: Optional[str]) -> "AppEvent":
        return AppEvent("OpenMarketplaceUpgradeLoading", {"marketplace_name": marketplace_name})

    @staticmethod
    def fetch_marketplace_upgrade(cwd: Path, marketplace_name: Optional[str]) -> "AppEvent":
        return AppEvent("FetchMarketplaceUpgrade", {"cwd": cwd, "marketplace_name": marketplace_name})

    @staticmethod
    def marketplace_upgrade_loaded(cwd: Path, result: Any) -> "AppEvent":
        return AppEvent("MarketplaceUpgradeLoaded", {"cwd": cwd, "result": result})

    @staticmethod
    def open_plugin_detail_loading(plugin_display_name: str) -> "AppEvent":
        return AppEvent("OpenPluginDetailLoading", {"plugin_display_name": plugin_display_name})

    @staticmethod
    def fetch_plugin_detail(cwd: Path, params: Any) -> "AppEvent":
        return AppEvent("FetchPluginDetail", {"cwd": cwd, "params": params})

    @staticmethod
    def plugin_detail_loaded(cwd: Path, result: Any) -> "AppEvent":
        return AppEvent("PluginDetailLoaded", {"cwd": cwd, "result": result})

    @staticmethod
    def open_plugin_install_loading(plugin_display_name: str) -> "AppEvent":
        return AppEvent("OpenPluginInstallLoading", {"plugin_display_name": plugin_display_name})

    @staticmethod
    def open_plugin_uninstall_loading(plugin_display_name: str) -> "AppEvent":
        return AppEvent("OpenPluginUninstallLoading", {"plugin_display_name": plugin_display_name})

    @staticmethod
    def fetch_plugin_install(cwd: Path, marketplace_path: Any, plugin_name: str,
                             plugin_display_name: str) -> "AppEvent":
        return AppEvent("FetchPluginInstall", {
            "cwd": cwd, "marketplace_path": marketplace_path,
            "plugin_name": plugin_name, "plugin_display_name": plugin_display_name,
        })

    @staticmethod
    def plugin_install_loaded(cwd: Path, marketplace_path: Any, plugin_name: str,
                              plugin_display_name: str, result: Any) -> "AppEvent":
        return AppEvent("PluginInstallLoaded", {
            "cwd": cwd, "marketplace_path": marketplace_path,
            "plugin_name": plugin_name, "plugin_display_name": plugin_display_name,
            "result": result,
        })

    @staticmethod
    def fetch_plugin_uninstall(cwd: Path, plugin_id: str, plugin_display_name: str) -> "AppEvent":
        return AppEvent("FetchPluginUninstall", {
            "cwd": cwd, "plugin_id": plugin_id, "plugin_display_name": plugin_display_name,
        })

    @staticmethod
    def plugin_uninstall_loaded(cwd: Path, plugin_id: str, plugin_display_name: str,
                                result: Any) -> "AppEvent":
        return AppEvent("PluginUninstallLoaded", {
            "cwd": cwd, "plugin_id": plugin_id,
            "plugin_display_name": plugin_display_name, "result": result,
        })

    @staticmethod
    def set_plugin_enabled(cwd: Path, plugin_id: str, enabled: bool) -> "AppEvent":
        return AppEvent("SetPluginEnabled", {"cwd": cwd, "plugin_id": plugin_id, "enabled": enabled})

    @staticmethod
    def plugin_enabled_set(cwd: Path, plugin_id: str, enabled: bool, result: Any) -> "AppEvent":
        return AppEvent("PluginEnabledSet", {
            "cwd": cwd, "plugin_id": plugin_id, "enabled": enabled, "result": result,
        })

    @staticmethod
    def refresh_plugin_mentions() -> "AppEvent":
        return AppEvent("RefreshPluginMentions")

    @staticmethod
    def plugin_mentions_loaded(plugins: Optional[List[Any]]) -> "AppEvent":
        return AppEvent("PluginMentionsLoaded", {"plugins": plugins})

    @staticmethod
    def plugin_install_auth_advance(refresh_connectors: bool) -> "AppEvent":
        return AppEvent("PluginInstallAuthAdvance", {"refresh_connectors": refresh_connectors})

    @staticmethod
    def plugin_install_auth_abandon() -> "AppEvent":
        return AppEvent("PluginInstallAuthAbandon")

    # ------------------------------------------------------------------
    # MCP
    # ------------------------------------------------------------------

    @staticmethod
    def fetch_mcp_inventory(detail: Any, thread_id: Optional[Any]) -> "AppEvent":
        return AppEvent("FetchMcpInventory", {"detail": detail, "thread_id": thread_id})

    @staticmethod
    def mcp_inventory_loaded(result: Any, detail: Any, thread_id: Optional[Any]) -> "AppEvent":
        return AppEvent("McpInventoryLoaded", {"result": result, "detail": detail, "thread_id": thread_id})

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    @staticmethod
    def skills_list_loaded(result: Any) -> "AppEvent":
        return AppEvent("SkillsListLoaded", {"result": result})

    # ------------------------------------------------------------------
    # History cell / scrollback / streaming consolidation
    # ------------------------------------------------------------------

    @staticmethod
    def begin_initial_history_replay_buffer() -> "AppEvent":
        return AppEvent("BeginInitialHistoryReplayBuffer")

    @staticmethod
    def begin_thread_switch_history_replay_buffer() -> "AppEvent":
        return AppEvent("BeginThreadSwitchHistoryReplayBuffer")

    @staticmethod
    def insert_history_cell(cell: Any) -> "AppEvent":
        return AppEvent("InsertHistoryCell", {"cell": cell})

    @staticmethod
    def end_initial_history_replay_buffer() -> "AppEvent":
        return AppEvent("EndInitialHistoryReplayBuffer")

    @staticmethod
    def consolidate_agent_message(source: str, cwd: Path,
                                  scrollback_reflow: ConsolidationScrollbackReflow,
                                  deferred_history_cell: Optional[Any]) -> "AppEvent":
        return AppEvent("ConsolidateAgentMessage", {
            "source": source, "cwd": cwd,
            "scrollback_reflow": scrollback_reflow,
            "deferred_history_cell": deferred_history_cell,
        })

    @staticmethod
    def consolidate_proposed_plan(source: str) -> "AppEvent":
        return AppEvent("ConsolidateProposedPlan", {"source": source})

    @staticmethod
    def apply_thread_rollback(num_turns: int) -> "AppEvent":
        return AppEvent("ApplyThreadRollback", {"num_turns": num_turns})

    @staticmethod
    def start_commit_animation() -> "AppEvent":
        return AppEvent("StartCommitAnimation")

    @staticmethod
    def stop_commit_animation() -> "AppEvent":
        return AppEvent("StopCommitAnimation")

    @staticmethod
    def commit_tick() -> "AppEvent":
        return AppEvent("CommitTick")

    # ------------------------------------------------------------------
    # Model / reasoning / personality / service tier
    # ------------------------------------------------------------------

    @staticmethod
    def update_reasoning_effort(effort: Optional[Any]) -> "AppEvent":
        return AppEvent("UpdateReasoningEffort", {"effort": effort})

    @staticmethod
    def update_model(model: str) -> "AppEvent":
        return AppEvent("UpdateModel", {"model": model})

    @staticmethod
    def update_personality(personality: Any) -> "AppEvent":
        return AppEvent("UpdatePersonality", {"personality": personality})

    @staticmethod
    def persist_model_selection(model: str, effort: Optional[Any]) -> "AppEvent":
        return AppEvent("PersistModelSelection", {"model": model, "effort": effort})

    @staticmethod
    def persist_personality_selection(personality: Any) -> "AppEvent":
        return AppEvent("PersistPersonalitySelection", {"personality": personality})

    @staticmethod
    def persist_service_tier_selection(service_tier: Optional[str]) -> "AppEvent":
        return AppEvent("PersistServiceTierSelection", {"service_tier": service_tier})

    # ------------------------------------------------------------------
    # Realtime audio
    # ------------------------------------------------------------------

    @staticmethod
    def open_realtime_audio_device_selection(kind: RealtimeAudioDeviceKind) -> "AppEvent":
        return AppEvent("OpenRealtimeAudioDeviceSelection", {"kind": kind})

    @staticmethod
    def persist_realtime_audio_device_selection(kind: RealtimeAudioDeviceKind, name: Optional[str]) -> "AppEvent":
        return AppEvent("PersistRealtimeAudioDeviceSelection", {"kind": kind, "name": name})

    @staticmethod
    def restart_realtime_audio_device(kind: RealtimeAudioDeviceKind) -> "AppEvent":
        return AppEvent("RestartRealtimeAudioDevice", {"kind": kind})

    @staticmethod
    def realtime_webrtc_offer_created(result: Any) -> "AppEvent":
        return AppEvent("RealtimeWebrtcOfferCreated", {"result": result})

    @staticmethod
    def realtime_webrtc_event(event: Any) -> "AppEvent":
        return AppEvent("RealtimeWebrtcEvent", {"event": event})

    @staticmethod
    def realtime_webrtc_local_audio_level(level: int) -> "AppEvent":
        return AppEvent("RealtimeWebrtcLocalAudioLevel", {"level": level})

    # ------------------------------------------------------------------
    # Model picker / Plan mode
    # ------------------------------------------------------------------

    @staticmethod
    def open_reasoning_popup(model: Any) -> "AppEvent":
        return AppEvent("OpenReasoningPopup", {"model": model})

    @staticmethod
    def open_plan_reasoning_scope_prompt(model: str, effort: Optional[Any]) -> "AppEvent":
        return AppEvent("OpenPlanReasoningScopePrompt", {"model": model, "effort": effort})

    @staticmethod
    def open_all_models_popup(models: List[Any]) -> "AppEvent":
        return AppEvent("OpenAllModelsPopup", {"models": models})

    # ------------------------------------------------------------------
    # Approvals / permissions
    # ------------------------------------------------------------------

    @staticmethod
    def open_full_access_confirmation(preset: Any, return_to_permissions: bool,
                                      profile_selection: Optional[PermissionProfileSelection]) -> "AppEvent":
        return AppEvent("OpenFullAccessConfirmation", {
            "preset": preset, "return_to_permissions": return_to_permissions,
            "profile_selection": profile_selection,
        })

    @staticmethod
    def open_world_writable_warning_confirmation(preset: Optional[Any],
                                                 profile_selection: Optional[PermissionProfileSelection],
                                                 sample_paths: List[str],
                                                 extra_count: int,
                                                 failed_scan: bool) -> "AppEvent":
        return AppEvent("OpenWorldWritableWarningConfirmation", {
            "preset": preset, "profile_selection": profile_selection,
            "sample_paths": sample_paths, "extra_count": extra_count,
            "failed_scan": failed_scan,
        })

    @staticmethod
    def open_windows_sandbox_enable_prompt(preset: Any,
                                           profile_selection: Optional[PermissionProfileSelection]) -> "AppEvent":
        return AppEvent("OpenWindowsSandboxEnablePrompt", {
            "preset": preset, "profile_selection": profile_selection,
        })

    @staticmethod
    def open_windows_sandbox_fallback_prompt(preset: Any,
                                             profile_selection: Optional[PermissionProfileSelection]) -> "AppEvent":
        return AppEvent("OpenWindowsSandboxFallbackPrompt", {
            "preset": preset, "profile_selection": profile_selection,
        })

    @staticmethod
    def begin_windows_sandbox_elevated_setup(preset: Any,
                                             profile_selection: Optional[PermissionProfileSelection]) -> "AppEvent":
        return AppEvent("BeginWindowsSandboxElevatedSetup", {
            "preset": preset, "profile_selection": profile_selection,
        })

    @staticmethod
    def begin_windows_sandbox_legacy_setup(preset: Any,
                                           profile_selection: Optional[PermissionProfileSelection]) -> "AppEvent":
        return AppEvent("BeginWindowsSandboxLegacySetup", {
            "preset": preset, "profile_selection": profile_selection,
        })

    @staticmethod
    def begin_windows_sandbox_grant_read_root(path: str) -> "AppEvent":
        return AppEvent("BeginWindowsSandboxGrantReadRoot", {"path": path})

    @staticmethod
    def windows_sandbox_grant_read_root_completed(path: Path, error: Optional[str]) -> "AppEvent":
        return AppEvent("WindowsSandboxGrantReadRootCompleted", {"path": path, "error": error})

    @staticmethod
    def enable_windows_sandbox_for_agent_mode(preset: Any, mode: WindowsSandboxEnableMode,
                                              profile_selection: Optional[PermissionProfileSelection]) -> "AppEvent":
        return AppEvent("EnableWindowsSandboxForAgentMode", {
            "preset": preset, "mode": mode, "profile_selection": profile_selection,
        })

    @staticmethod
    def update_ask_for_approval_policy(policy: Any) -> "AppEvent":
        return AppEvent("UpdateAskForApprovalPolicy", {"policy": policy})

    @staticmethod
    def update_active_permission_profile(profile: Any) -> "AppEvent":
        return AppEvent("UpdateActivePermissionProfile", {"profile": profile})

    @staticmethod
    def select_permission_profile(selection: PermissionProfileSelection) -> "AppEvent":
        return AppEvent("SelectPermissionProfile", {"selection": selection})

    @staticmethod
    def update_approvals_reviewer(reviewer: Any) -> "AppEvent":
        return AppEvent("UpdateApprovalsReviewer", {"reviewer": reviewer})

    # ------------------------------------------------------------------
    # Feature flags / memory
    # ------------------------------------------------------------------

    @staticmethod
    def update_feature_flags(updates: List[Any]) -> "AppEvent":
        return AppEvent("UpdateFeatureFlags", {"updates": updates})

    @staticmethod
    def update_memory_settings(use_memories: bool, generate_memories: bool) -> "AppEvent":
        return AppEvent("UpdateMemorySettings", {
            "use_memories": use_memories, "generate_memories": generate_memories,
        })

    @staticmethod
    def reset_memories() -> "AppEvent":
        return AppEvent("ResetMemories")

    # ------------------------------------------------------------------
    # Warnings / acknowledgements
    # ------------------------------------------------------------------

    @staticmethod
    def update_full_access_warning_acknowledged(acknowledged: bool) -> "AppEvent":
        return AppEvent("UpdateFullAccessWarningAcknowledged", {"acknowledged": acknowledged})

    @staticmethod
    def update_world_writable_warning_acknowledged(acknowledged: bool) -> "AppEvent":
        return AppEvent("UpdateWorldWritableWarningAcknowledged", {"acknowledged": acknowledged})

    @staticmethod
    def update_rate_limit_switch_prompt_hidden(hidden: bool) -> "AppEvent":
        return AppEvent("UpdateRateLimitSwitchPromptHidden", {"hidden": hidden})

    @staticmethod
    def update_plan_mode_reasoning_effort(effort: Optional[Any]) -> "AppEvent":
        return AppEvent("UpdatePlanModeReasoningEffort", {"effort": effort})

    @staticmethod
    def persist_full_access_warning_acknowledged() -> "AppEvent":
        return AppEvent("PersistFullAccessWarningAcknowledged")

    @staticmethod
    def persist_world_writable_warning_acknowledged() -> "AppEvent":
        return AppEvent("PersistWorldWritableWarningAcknowledged")

    @staticmethod
    def persist_rate_limit_switch_prompt_hidden() -> "AppEvent":
        return AppEvent("PersistRateLimitSwitchPromptHidden")

    @staticmethod
    def persist_plan_mode_reasoning_effort(effort: Optional[Any]) -> "AppEvent":
        return AppEvent("PersistPlanModeReasoningEffort", {"effort": effort})

    @staticmethod
    def persist_model_migration_prompt_acknowledged(from_model: str, to_model: str) -> "AppEvent":
        return AppEvent("PersistModelMigrationPromptAcknowledged", {
            "from_model": from_model, "to_model": to_model,
        })

    @staticmethod
    def skip_next_world_writable_scan() -> "AppEvent":
        return AppEvent("SkipNextWorldWritableScan")

    # ------------------------------------------------------------------
    # Approvals / Skills / Apps / Hooks (popups & state)
    # ------------------------------------------------------------------

    @staticmethod
    def open_approvals_popup() -> "AppEvent":
        return AppEvent("OpenApprovalsPopup")

    @staticmethod
    def open_skills_list() -> "AppEvent":
        return AppEvent("OpenSkillsList")

    @staticmethod
    def open_manage_skills_popup() -> "AppEvent":
        return AppEvent("OpenManageSkillsPopup")

    @staticmethod
    def set_skill_enabled(path: Any, enabled: bool) -> "AppEvent":
        return AppEvent("SetSkillEnabled", {"path": path, "enabled": enabled})

    @staticmethod
    def set_app_enabled(id: str, enabled: bool) -> "AppEvent":
        return AppEvent("SetAppEnabled", {"id": id, "enabled": enabled})

    @staticmethod
    def set_hook_enabled(key: str, enabled: bool) -> "AppEvent":
        return AppEvent("SetHookEnabled", {"key": key, "enabled": enabled})

    @staticmethod
    def trust_hook(key: str, current_hash: str) -> "AppEvent":
        return AppEvent("TrustHook", {"key": key, "current_hash": current_hash})

    @staticmethod
    def trust_hooks(updates: List[Any]) -> "AppEvent":
        return AppEvent("TrustHooks", {"updates": updates})

    @staticmethod
    def hook_enabled_set(key: str, enabled: bool, result: Any) -> "AppEvent":
        return AppEvent("HookEnabledSet", {"key": key, "enabled": enabled, "result": result})

    @staticmethod
    def hook_trusted(result: Any) -> "AppEvent":
        return AppEvent("HookTrusted", {"result": result})

    @staticmethod
    def manage_skills_closed() -> "AppEvent":
        return AppEvent("ManageSkillsClosed")

    @staticmethod
    def open_permissions_popup() -> "AppEvent":
        return AppEvent("OpenPermissionsPopup")

    # ------------------------------------------------------------------
    # Voice / recording
    # ------------------------------------------------------------------

    @staticmethod
    def update_recording_meter(id: str, text: str) -> "AppEvent":
        return AppEvent("UpdateRecordingMeter", {"id": id, "text": text})

    # ------------------------------------------------------------------
    # Review picker
    # ------------------------------------------------------------------

    @staticmethod
    def open_review_branch_picker(cwd: Path) -> "AppEvent":
        return AppEvent("OpenReviewBranchPicker", {"cwd": cwd})

    @staticmethod
    def open_review_commit_picker(cwd: Path) -> "AppEvent":
        return AppEvent("OpenReviewCommitPicker", {"cwd": cwd})

    @staticmethod
    def open_review_custom_prompt() -> "AppEvent":
        return AppEvent("OpenReviewCustomPrompt")

    # ------------------------------------------------------------------
    # Collaboration mode / user messages
    # ------------------------------------------------------------------

    @staticmethod
    def submit_user_message_with_mode(text: str, collaboration_mode: Any) -> "AppEvent":
        return AppEvent("SubmitUserMessageWithMode", {
            "text": text, "collaboration_mode": collaboration_mode,
        })

    # ------------------------------------------------------------------
    # Approval overlays / feedback
    # ------------------------------------------------------------------

    @staticmethod
    def full_screen_approval_request(request: Any) -> "AppEvent":
        return AppEvent("FullScreenApprovalRequest", {"request": request})

    @staticmethod
    def open_feedback_note(category: FeedbackCategory, include_logs: bool) -> "AppEvent":
        return AppEvent("OpenFeedbackNote", {"category": category, "include_logs": include_logs})

    @staticmethod
    def open_feedback_consent(category: FeedbackCategory) -> "AppEvent":
        return AppEvent("OpenFeedbackConsent", {"category": category})

    @staticmethod
    def submit_feedback(category: FeedbackCategory, reason: Optional[str],
                        turn_id: Optional[str], include_logs: bool) -> "AppEvent":
        return AppEvent("SubmitFeedback", {
            "category": category, "reason": reason,
            "turn_id": turn_id, "include_logs": include_logs,
        })

    @staticmethod
    def feedback_submitted(origin_thread_id: Optional[Any], category: FeedbackCategory,
                           include_logs: bool, result: Any) -> "AppEvent":
        return AppEvent("FeedbackSubmitted", {
            "origin_thread_id": origin_thread_id, "category": category,
            "include_logs": include_logs, "result": result,
        })

    # ------------------------------------------------------------------
    # External editor / status line / terminal title / syntax theme
    # ------------------------------------------------------------------

    @staticmethod
    def launch_external_editor() -> "AppEvent":
        return AppEvent("LaunchExternalEditor")

    @staticmethod
    def status_line_branch_updated(cwd: Path, branch: Optional[str]) -> "AppEvent":
        return AppEvent("StatusLineBranchUpdated", {"cwd": cwd, "branch": branch})

    @staticmethod
    def status_line_git_summary_updated(cwd: Path, summary: Any) -> "AppEvent":
        return AppEvent("StatusLineGitSummaryUpdated", {"cwd": cwd, "summary": summary})

    @staticmethod
    def status_line_setup(items: List[Any], use_theme_colors: bool) -> "AppEvent":
        return AppEvent("StatusLineSetup", {"items": items, "use_theme_colors": use_theme_colors})

    @staticmethod
    def status_line_setup_cancelled() -> "AppEvent":
        return AppEvent("StatusLineSetupCancelled")

    @staticmethod
    def terminal_title_setup(items: List[Any]) -> "AppEvent":
        return AppEvent("TerminalTitleSetup", {"items": items})

    @staticmethod
    def terminal_title_setup_preview(items: List[Any]) -> "AppEvent":
        return AppEvent("TerminalTitleSetupPreview", {"items": items})

    @staticmethod
    def terminal_title_setup_cancelled() -> "AppEvent":
        return AppEvent("TerminalTitleSetupCancelled")

    @staticmethod
    def syntax_theme_selected(name: str) -> "AppEvent":
        return AppEvent("SyntaxThemeSelected", {"name": name})

    @staticmethod
    def syntax_theme_previewed() -> "AppEvent":
        return AppEvent("SyntaxThemePreviewed")

    # ------------------------------------------------------------------
    # Keymap editor
    # ------------------------------------------------------------------

    @staticmethod
    def open_keymap_action_menu(context: str, action: str) -> "AppEvent":
        return AppEvent("OpenKeymapActionMenu", {"context": context, "action": action})

    @staticmethod
    def open_keymap_replace_binding_menu(context: str, action: str) -> "AppEvent":
        return AppEvent("OpenKeymapReplaceBindingMenu", {"context": context, "action": action})

    @staticmethod
    def open_keymap_capture(context: str, action: str, intent: KeymapEditIntent) -> "AppEvent":
        return AppEvent("OpenKeymapCapture", {"context": context, "action": action, "intent": intent})

    @staticmethod
    def open_keymap_debug() -> "AppEvent":
        return AppEvent("OpenKeymapDebug")

    @staticmethod
    def keymap_captured(context: str, action: str, key: str, intent: KeymapEditIntent) -> "AppEvent":
        return AppEvent("KeymapCaptured", {
            "context": context, "action": action, "key": key, "intent": intent,
        })

    @staticmethod
    def keymap_cleared(context: str, action: str) -> "AppEvent":
        return AppEvent("KeymapCleared", {"context": context, "action": action})


__all__ = [
    "AppEvent",
    "ApprovalRequestKind",
    "ConsolidationScrollbackReflow",
    "ConnectorsSnapshot",
    "ExitMode",
    "ExitModeKind",
    "FeedbackCategory",
    "HistoryLookupResponse",
    "KeymapEditIntent",
    "KeymapEditIntentKind",
    "PermissionProfileSelection",
    "RateLimitRefreshOrigin",
    "RateLimitRefreshOriginKind",
    "RealtimeAudioDeviceKind",
    "RealtimeWebrtcOffer",
    "ThreadGoalSetMode",
    "ThreadGoalSetModeKind",
    "WindowsSandboxEnableMode",
]
