package ru.igorek.remote.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

@Serializable
data class ScheduledJobDto(
    @SerialName("schedule_id") val scheduleId: String = "",
    val name: String = "",
    val payload: String = "",
    @SerialName("schedule_type") val scheduleType: String = "",
    @SerialName("time_value") val timeValue: String? = null,
    @SerialName("cron_expression") val cronExpression: String? = null,
    val timezone: String = "UTC",
    val enabled: Boolean = false,
    @SerialName("next_run_at") val nextRunAt: Long? = null,
    @SerialName("last_run_at") val lastRunAt: Long? = null,
    @SerialName("run_count") val runCount: Int = 0,
    val profile: String = "",
    val cwd: String = "",
    @SerialName("last_session_id") val lastSessionId: String? = null,
    @SerialName("last_status") val lastStatus: String = "",
    val ready: Boolean = false,
)

@Serializable
data class SchedulesResponse(
    val schedules: List<ScheduledJobDto> = emptyList(),
    val available: Boolean = true,
    val running: Boolean = false,
)

@Serializable
data class ScheduleFields(
    val name: String,
    val payload: String,
    val profile: String,
    val cwd: String,
    @SerialName("schedule_type") val scheduleType: String,
    @SerialName("time_value") val timeValue: String,
    val timezone: String,
)

internal class SchedulesConnection(private val client: BridgeClient, private val current: () -> Boolean) {
    private suspend fun <T> request(action: () -> T): T = withContext(Dispatchers.IO) {
        checkCurrent()
        val result = action()
        checkCurrent()
        result
    }
    private fun checkCurrent() {
        if (!current()) throw BridgeException(409, "Подключение изменилось. Открой расписания заново.", "connection_changed")
    }
    suspend fun list() = request { client.schedules() }
    suspend fun options() = request { client.sessionOptions() }
    suspend fun save(fields: ScheduleFields, scheduleId: String?) = request { client.saveSchedule(fields, scheduleId) }
    suspend fun enabled(id: String, enabled: Boolean) = request { client.setScheduleEnabled(id, enabled) }
    suspend fun run(id: String) = request { client.runSchedule(id) }
    suspend fun delete(id: String) = request { client.deleteSchedule(id) }
}

internal enum class ScheduleStatus(val ru: String, val en: String) {
    Queued("В очереди", "Queued"), Running("Выполняется", "Running"), Approval("Нужно подтверждение", "Approval needed"),
    Cancelling("Останавливается", "Stopping"), Retry("Повтор запуска", "Retrying"), Paused("На паузе", "Paused"),
    Done("Завершено", "Finished"), Failed("Завершено с ошибкой", "Failed"), Waiting("Ждёт исполнителя", "Waiting for executor"),
    Scheduled("Запланировано", "Scheduled"), NoNext("Нет следующего запуска", "No next run"),
}

internal fun scheduleStatus(row: ScheduledJobDto): ScheduleStatus {
    when (row.lastStatus) {
        "queued" -> return ScheduleStatus.Queued
        "processing" -> return ScheduleStatus.Running
        "waiting_approval" -> return ScheduleStatus.Approval
        "cancelling" -> return ScheduleStatus.Cancelling
        "retry" -> return ScheduleStatus.Retry
    }
    if (!row.enabled) return ScheduleStatus.Paused
    if (row.scheduleType == "once" && row.runCount > 0 && row.nextRunAt == null) {
        return if (row.lastStatus in setOf("fail", "dead_letter", "timeout")) ScheduleStatus.Failed else ScheduleStatus.Done
    }
    if (!row.ready) return ScheduleStatus.Waiting
    return if (row.nextRunAt == null) ScheduleStatus.NoNext else ScheduleStatus.Scheduled
}

internal fun scheduleTimestamp(value: Long?, timezone: String, language: AppLanguage): String {
    if (value == null || value <= 0) return "—"
    return runCatching {
        DateTimeFormatter.ofPattern("d MMM, HH:mm", if (language == AppLanguage.Russian) Locale("ru") else Locale.ENGLISH)
            .format(Instant.ofEpochSecond(value).atZone(ZoneId.of(timezone)))
    }.getOrDefault("—")
}

internal fun scheduleFieldsValid(fields: ScheduleFields, profiles: List<String>): Boolean =
    fields.name.isNotBlank() && fields.name.length <= 160 && fields.payload.isNotBlank() && fields.payload.length <= 16000 &&
        fields.profile in profiles && fields.profile.length <= 64 && fields.cwd.isNotBlank() && fields.cwd.length <= 4096 &&
        fields.timezone.isNotBlank() && fields.timezone.length <= 80 && fields.scheduleType in setOf("daily", "once") &&
        Regex("(?:[01]\\d|2[0-3]):[0-5]\\d").matches(fields.timeValue)
