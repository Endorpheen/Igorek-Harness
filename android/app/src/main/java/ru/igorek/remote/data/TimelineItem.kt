package ru.igorek.remote.data

sealed interface TimelineItem {
    val id: String

    data class UserText(
        override val id: String,
        val text: String,
        val skills: List<String> = emptyList(),
        val queued: Boolean = false,
        val taskId: String = "",
        val messageId: String = "",
        val prioritized: Boolean = false,
        val delivered: Boolean = false,
        val attachments: List<UploadAttachmentDto> = emptyList(),
    ) : TimelineItem {
        val displayText: String
            get() {
                if (attachments.isEmpty()) return text
                val names = attachments.joinToString(", ") { it.name }
                for (label in listOf("вложения", "attachments")) {
                    val marker = "[$label: $names]"
                    if (text == marker) return ""
                    if (text.endsWith("\n\n$marker")) return text.removeSuffix("\n\n$marker")
                }
                return text
            }
    }

    data class AssistantText(
        override val id: String,
        val text: String,
        val streaming: Boolean = false,
        val files: List<AttachmentDto> = emptyList(),
    ) : TimelineItem

    data class Reasoning(
        override val id: String,
        val text: String,
        val streaming: Boolean = false,
    ) : TimelineItem

    data class Compaction(
        override val id: String,
        val phase: String,
        val summary: String = "",
        val reason: String = "",
        val chunk: Long = 0,
        val totalChunks: Long = 0,
    ) : TimelineItem

    data class ToolCall(
        override val id: String,
        val name: String,
        val input: String,
        val status: String,
        val resultPreview: String = "",
        val durationMs: Long = 0,
        val storedPath: String? = null,
    ) : TimelineItem

    data class ApprovalNotice(
        override val id: String,
        val requestId: String,
        val taskId: String,
        val tool: String,
        val target: String,
        val reason: String,
        val dangerous: Boolean,
        val resolved: String? = null,
        val input: String = "",
    ) : TimelineItem

    data class Status(
        override val id: String,
        val text: String,
        val level: String = "info",
        val details: String = "",
        val taskId: String = "",
    ) : TimelineItem
}
