package ru.igorek.remote.data

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNames
import kotlinx.serialization.json.JsonObject

@Serializable
data class PairCompleteRequest(
    val code: String,
    val name: String,
    val platform: String = "android",
)

@Serializable
data class PairResponse(
    @SerialName("device_id") val deviceId: String,
    val token: String,
    val name: String = "",
)

@Serializable
data class DeviceDto(
    @SerialName("device_id") val deviceId: String,
    val name: String = "",
    val platform: String = "",
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("last_seen_at") val lastSeenAt: Long? = null,
    @SerialName("revoked_at") val revokedAt: Long? = null,
)

@Serializable
data class DevicesResponse(
    val devices: List<DeviceDto> = emptyList(),
    // ID of the list string corresponding to the current device; such entries
    // cannot be deleted from the application, otherwise it will remain with a token without a record.
    @SerialName("current_device_id") val currentDeviceId: String = "",
)

@Serializable
data class SessionSummary(
    @SerialName("session_id") val sessionId: String,
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("updated_at") val updatedAt: Long = 0,
    val profile: String = "",
    val model: String = "",
    val provider: String = "",
    val cwd: String = "",
    val title: String = "",
    @kotlinx.serialization.Transient val mcpPreview: String = "",
    @kotlinx.serialization.Transient val generatedMcpTitle: Boolean = false,
    @SerialName("message_count") val messageCount: Int = 0,
    val active: Boolean = false,
    @SerialName("active_status") val activeStatus: String = "",
    val archived: Boolean = false,
    val pinned: Boolean = false,
    @SerialName("running_task_id") val runningTaskId: String? = null,
    @SerialName("last_task_status") val lastTaskStatus: String = "",
    @SerialName("pending_approvals") val pendingApprovals: Int = 0,
    val isolation: String? = null,
)

@Serializable
data class SessionsResponse(
    val sessions: List<SessionSummary> = emptyList(),
    @SerialName("archive_candidate_count") val archiveCandidateCount: Int = 0,
    @SerialName("active_count") val activeCount: Int = 0,
)

@Serializable
data class ProfileChoice(
    val name: String,
    val model: String = "",
    val local: Boolean = false,
    val models: List<String> = emptyList(),
    @SerialName("model_labels") val modelLabels: Map<String, String> = emptyMap(),
    @SerialName("models_status") val modelsStatus: String = "",
    @SerialName("provider_icon") val providerIcon: String = "",
)

@Serializable
data class SessionOptionsResponse(
    val profiles: List<String> = emptyList(),
    val workdirs: List<String> = emptyList(),
    @SerialName("default_profile") val defaultProfile: String = "",
    @SerialName("default_cwd") val defaultCwd: String = "",
    @SerialName("profile_catalog") val profileCatalog: List<ProfileChoice> = emptyList(),
    @SerialName("profile_switch_supported") val profileSwitchSupported: Boolean = false,
) {
    val selectableProfiles: List<String> get() = (profileCatalog.map { it.name } + profiles).distinct()
    val switchableProfiles: List<ProfileChoice> get() = if (profileSwitchSupported)
        profileCatalog.filter { it.name.isNotBlank() }.distinctBy { it.name } else emptyList()
}

@Serializable
data class ExternalSessionSummary(
    val source: String,
    @SerialName("session_id") val sessionId: String,
    val title: String = "",
    val cwd: String = "",
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("updated_at") val updatedAt: Long = 0,
    val model: String = "",
    val agent: String = "",
    @SerialName("message_count") val messageCount: Int = 0,
    @SerialName("native_resume") val nativeResume: Boolean = false,
)

@Serializable
data class ExternalSessionsResponse(
    val sessions: List<ExternalSessionSummary> = emptyList(),
    val counts: Map<String, Int> = emptyMap(),
    val total: Int = 0,
    @SerialName("scanned_at") val scannedAt: Long = 0,
)

@Serializable
data class ExternalMessageDto(
    val role: String = "",
    val text: String = "",
    val ts: Long = 0,
    val kind: String = "text",
)

@Serializable
data class ExternalSessionDetail(
    val source: String,
    @SerialName("session_id") val sessionId: String,
    val title: String = "",
    val cwd: String = "",
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("updated_at") val updatedAt: Long = 0,
    val model: String = "",
    val agent: String = "",
    @SerialName("message_count") val messageCount: Int = 0,
    @SerialName("native_resume") val nativeResume: Boolean = false,
    val messages: List<ExternalMessageDto> = emptyList(),
    @SerialName("total_messages") val totalMessages: Int = 0,
    @SerialName("next_offset") val nextOffset: Int? = null,
)

@Serializable
data class ImportExternalSessionRequest(
    val profile: String,
    val cwd: String,
    @SerialName("idempotency_key") val idempotencyKey: String,
)

@Serializable
data class ImportExternalSessionResponse(
    val session: SessionDetail,
    @SerialName("imported_messages") val importedMessages: Int = 0,
    @SerialName("skipped_messages") val skippedMessages: Int = 0,
    val duplicate: Boolean = false,
    val cwd: String = "",
    @SerialName("cwd_source") val cwdSource: String = "",
    @SerialName("cwd_fallback_reason") val cwdFallbackReason: String = "",
    @SerialName("origin_cwd") val originCwd: String = "",
)

@Serializable
data class SkillDto(
    @SerialName("skill_id") val skillId: String,
    val name: String = "",
    val description: String = "",
    val category: String = "",
    val enabled: Boolean = true,
    val supported: Boolean = true,
    @SerialName("backend_filter") val backendFilter: List<String> = emptyList(),
    @SerialName("resource_dirs") val resourceDirs: List<String> = emptyList(),
)

@Serializable
data class McpServerDto(
    val name: String,
    val enabled: Boolean = false,
    val disabled: Boolean = false,
    val description: String = "",
    val capabilities: List<String> = emptyList(),
    @SerialName("tools_hint") val toolsHint: List<String> = emptyList(),
    @SerialName("requires_env") val requiresEnv: List<String> = emptyList(),
    val type: String = "",
)

@Serializable
data class HookDto(
    val key: String = "",
    val name: String = "",
    val event: String = "",
    val enabled: Boolean = false,
)

@Serializable
data class ExtensionsResponse(
    val profile: String = "",
    val backend: String = "",
    val skills: List<SkillDto> = emptyList(),
    @SerialName("mcp_servers") val mcpServers: List<McpServerDto> = emptyList(),
    val hooks: List<HookDto> = emptyList(),
    @SerialName("hooks_available") val hooksAvailable: Boolean = false,
)

@Serializable
data class UpdateSkillRequest(val enabled: Boolean)

@Serializable
data class UpdateSkillResponse(
    val skill: SkillDto,
    val message: String = "",
)

@Serializable
data class ArchiveSessionsRequest(val archived: Boolean = true)

@Serializable
data class ArchiveSessionsResponse(
    val archived: Boolean = true,
    val updated: Int = 0,
    @SerialName("skipped_active") val skippedActive: Int = 0,
    val unchanged: Int = 0,
    @SerialName("session_ids") val sessionIds: List<String> = emptyList(),
)

@Serializable
data class CreateSessionRequest(
    val profile: String = "",
    val cwd: String = "",
    @SerialName("idempotency_key") val idempotencyKey: String,
    @SerialName("activate_profile") val activateProfile: Boolean = false,
)

@Serializable
data class UpdateSessionRequest(
    val title: String? = null,
    val archived: Boolean? = null,
    val pinned: Boolean? = null,
)

@Serializable
data class MessageDto(
    val role: String = "",
    val text: String = "",
    val ts: Long? = null,
    @SerialName("task_id") val taskId: String? = null,
    @SerialName("message_id") val messageId: String? = null,
    val status: String? = null,
    val skills: List<String> = emptyList(),
    val files: List<AttachmentDto> = emptyList(),
    val queued: Boolean = false,
    val kind: String = "text",
    @SerialName("history_id") val historyId: String = "",
    @SerialName("tool_use_id") val toolUseId: String = "",
    @SerialName("tool_name") val toolName: String = "",
    @SerialName("tool_input") val toolInput: JsonElement = JsonObject(emptyMap()),
    @SerialName("tool_output") val toolOutput: String = "",
    @SerialName("tool_status") val toolStatus: String = "done",
    val attachments: List<UploadAttachmentDto> = emptyList(),
)

@Serializable
data class TaskBriefDto(
    @SerialName("task_id") val taskId: String,
    val status: String = "",
    @SerialName("created_at") val createdAt: Long = 0,
    @SerialName("updated_at") val updatedAt: Long = 0,
    @SerialName("started_at") val startedAt: Long? = null,
    @SerialName("completed_at") val completedAt: Long? = null,
    @SerialName("error_message") val errorMessage: String = "",
)

@Serializable
data class SessionGoal(
    @SerialName("goal_id") val goalId: String,
    val objective: String,
    val status: String,
    val revision: Int = 1,
    @SerialName("token_budget") val tokenBudget: Long? = null,
    @SerialName("tokens_used") val tokensUsed: Long = 0,
    @SerialName("time_used_seconds") val timeUsedSeconds: Long = 0,
)

@Serializable
data class GoalResponse(val goal: SessionGoal? = null)

@Serializable
data class SessionDetail(
    val model: String = "",
    @SerialName("context_window") val contextWindow: Long? = null,
    @SerialName("session_id") val sessionId: String,
    val meta: JsonObject = JsonObject(emptyMap()),
    val messages: List<MessageDto> = emptyList(),
    @SerialName("running_task_id") val runningTaskId: String? = null,
    val tasks: List<TaskBriefDto> = emptyList(),
    val isolation: String? = null,
    val goal: SessionGoal? = null,
)

@Serializable
data class ApprovalDto(
    val kind: String = "",
    @SerialName("request_id") val requestId: String = "",
    @SerialName("task_id") val taskId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    val tool: String = "",
    val target: String = "",
    val input: JsonElement? = null,
    val signature: String = "",
    val reason: String = "",
    val dangerous: Boolean = false,
    @SerialName("created_at") val createdAt: Long = 0,
)

@Serializable
data class ApprovalsResponse(val approvals: List<ApprovalDto> = emptyList())

// Response to /sessions/{id}/changes. The server calculates the relative file path and line counts;
// the client only draws the chip and list.
@Serializable
data class ChangedFileDto(
    val path: String = "",
    val additions: Int = 0,
    val deletions: Int = 0,
    val status: String = "modified",
)

@Serializable
data class ChangedFilesTotalsDto(
    val files: Int = 0,
    val additions: Int = 0,
    val deletions: Int = 0,
)

@Serializable
data class ChangedFilesResponse(
    val files: List<ChangedFileDto> = emptyList(),
    val totals: ChangedFilesTotalsDto = ChangedFilesTotalsDto(),
)

// Response to /sessions/{id}/changes/diff. The type string: " " — context, "-" — deletion,
// "+" — addition; text — content without prefix.
@Serializable
data class DiffLineDto(
    val type: String = " ",
    val text: String = "",
)

@Serializable
data class DiffHunkDto(
    val header: String = "",
    val lines: List<DiffLineDto> = emptyList(),
)

@Serializable
data class FileDiffResponse(
    val path: String = "",
    val additions: Int = 0,
    val deletions: Int = 0,
    val source: String = "unavailable",
    val truncated: Boolean = false,
    // For the checkpoint diff, the server refers to the starting point (hash or ref).
    // The old /changes/diff field is not returned — an empty string means 'now'.
    @SerialName("from") val from: String = "",
    val hunks: List<DiffHunkDto> = emptyList(),
)

// Response to `/sessions/{id}/checkpoints`. Version history of the working directory: the server itself
// limits the page (limit/offset) and determines whether there are more versions.
@Serializable
data class CheckpointDto(
    val hash: String = "",
    @SerialName("short_hash") val shortHash: String = "",
    val timestamp: String = "",
    val reason: String = "",
)

@Serializable
data class CheckpointListResponse(
    val workdir: String = "",
    val available: Boolean = true,
    val checkpoints: List<CheckpointDto> = emptyList(),
    val limit: Int = 0,
    val offset: Int = 0,
    @SerialName("has_more") val hasMore: Boolean = false,
)

@Serializable
data class ApprovalSettingsRequest(val enabled: Boolean)

@Serializable
data class ApprovalSettingsResponse(
    val enabled: Boolean = true,
    val resumed: Int = 0,
)

@Serializable
data class SecuritySettingsRequest(
    val level: String,
    @SerialName("ttl_s") val ttlS: Long? = null,
    val confirm: Boolean = false,
)

// Response to `/security-settings`. The backend returns the key `baseline`, but in the contract and
// fixtures, `baseline_level` also appears; we accept both names.
@OptIn(ExperimentalSerializationApi::class)
@Serializable
data class SecuritySettingsResponse(
    val level: String = "standard",
    @JsonNames("baseline", "baseline_level") val baselineLevel: String = "standard",
    @SerialName("is_weakened") val isWeakened: Boolean = false,
    @SerialName("expires_at") val expiresAt: Long? = null,
    @SerialName("available_levels") val availableLevels: List<String> = emptyList(),
)

@Serializable
data class SandboxSettingsRequest(val enabled: Boolean)

// Response to `/sandbox-settings`. `enabled` — user setting (default is enabled),
// `available` — is bwrap available, `active` = enabled && available.
@Serializable
data class SandboxSettingsResponse(
    val enabled: Boolean = true,
    val active: Boolean = true,
    val available: Boolean = true,
    val reason: String = "",
)

@Serializable
data class EventsResponse(val events: List<BridgeEvent> = emptyList())

@Serializable
data class UploadAttachmentDto(
    @SerialName("upload_id") val uploadId: String,
    val name: String,
    val size: Long = 0,
    @SerialName("mime_type") val mimeType: String = "application/octet-stream",
    val kind: String = "file",
) {
    val isImage: Boolean get() = mimeType.startsWith("image/") || kind == "image"
}

@Serializable
data class AttachmentDto(
    val id: String,
    val name: String,
    val size: Long,
    @SerialName("mime_type") val mimeType: String = "application/octet-stream",
)

@Serializable
data class AttachmentsResponse(val files: List<AttachmentDto> = emptyList())

@Serializable
data class BridgeEvent(
    val kind: String = "",
    val seq: Long? = null,
    val ts: Long? = null,
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("task_id") val taskId: String? = null,
    val payload: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class SendMessageRequest(
    val text: String,
    @SerialName("idempotency_key") val idempotencyKey: String? = null,
    @SerialName("upload_ids") val uploadIds: List<String> = emptyList(),
    @SerialName("skill_ids") val skillIds: List<String> = emptyList(),
    @SerialName("activate_profile") val activateProfile: Boolean = false,
    @SerialName("plan_mode") val planMode: Boolean = false,
)

@Serializable
data class UploadResponse(
    @SerialName("upload_id") val uploadId: String,
    val name: String = "",
    val size: Long = 0,
    @SerialName("mime_type") val mimeType: String = "application/octet-stream",
    val kind: String = "file",
)

@Serializable
data class SendMessageResponse(
    @SerialName("task_id") val taskId: String = "",
    val status: String = "",
    val duplicate: Boolean = false,
    @SerialName("message_id") val messageId: String = "",
    val steered: Boolean = false,
)

@Serializable
data class CancelRequest(val mode: String = "cancel")

@Serializable
data class DecisionRequest(val decision: String)

@Serializable
data class UserInputRequest(
    val response: JsonObject? = null,
    val cancelled: Boolean = false,
)

@Serializable
data class ErrorResponse(val error: String = "", val code: String = "", val diagnostic: ErrorObservation? = null)

// OpenCode Go subscription quota. The phone receives the same numbers as the web panel:
// percent — already used portion of the window, resetsAt — reset moment in ISO-8601 format.
@Serializable
data class QuotaWindowDto(
    val percent: Double? = null,
    val status: String = "",
    val resetsAt: String = "",
)

@Serializable
data class QuotaUsageDto(
    val rolling: QuotaWindowDto? = null,
    val weekly: QuotaWindowDto? = null,
    val monthly: QuotaWindowDto? = null,
)

@Serializable
data class QuotaErrorDto(val code: String = "")

@Serializable
data class QuotaResponse(
    val provider: String = "opencode",
    @SerialName("quota_error") val quotaError: QuotaErrorDto? = null,
    val usage: QuotaUsageDto? = QuotaUsageDto(),
    @SerialName("fetched_at") val fetchedAt: String = "",
)

@Serializable
data class AgentSessionDto(
    @SerialName("session_id") val sessionId: String,
    @SerialName("parent_session_id") val parentSessionId: String? = null,
    @SerialName("root_session_id") val rootSessionId: String = "",
    val title: String = "",
    val role: String = "",
    val status: String = "",
    val activity: String = "",
    val depth: Int = 0,
)

@Serializable
data class AgentSessionsResponse(
    @SerialName("root_session_id") val rootSessionId: String = "",
    val sessions: List<AgentSessionDto> = emptyList(),
)

@Serializable
data class BrowserPreviewResponse(
    val tabs: List<BrowserPreviewTab> = emptyList(),
    @SerialName("active_tab_id") val activeTabId: String = "",
    val available: Boolean = false,
    val ready: Boolean = false,
    val reason: String = "",
    val url: String = "",
    val title: String = "",
    val loading: Boolean = false,
    val unchanged: Boolean = false,
    @SerialName("captured_at") val capturedAt: Long = 0,
    @SerialName("frame_id") val frameId: String = "",
    @SerialName("image_base64") val imageBase64: String = "",
    val width: Int = 0,
    val height: Int = 0,
)

@Serializable
data class BrowserPreviewTab(val id: String = "", val title: String = "", val url: String = "")

@Serializable
data class BrowserTabSelectionResponse(val selected: Boolean = false, val reason: String = "")
