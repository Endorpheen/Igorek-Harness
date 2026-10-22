package ru.igorek.remote.data

import kotlinx.serialization.json.*
import kotlinx.serialization.decodeFromString
import org.junit.Assert.*
import org.junit.Test

class TaskDurationTest {
    private val start = 1792629000L
    private fun event(kind: String, sequence: Long, time: Long?, task: String = "fixture-task", status: String = "ok") =
        BridgeEvent(kind = kind, seq = sequence, ts = time, sessionId = "fixture-session", taskId = task,
            payload = buildJsonObject { put("task_id", task); put("status", status) })
    private fun completed(timeline: SessionTimeline, task: String = "fixture-task") =
        timeline.snapshot().filterIsInstance<TimelineItem.Status>().single { it.id == "success-$task" }

    @Test fun wholeTaskTimeUsesStartAndFinishRatherThanToolTime() {
        val t = SessionTimeline("fixture-session")
        t.apply(event("task.start", 1, start)); t.apply(event("task.finish", 2, start + 134))
        assertEquals(134000L, completed(t).durationMs)
        assertEquals("2 мин 14 с", taskDurationLabel(completed(t).durationMs!!, AppLanguage.Russian))
        assertEquals("2 min 14 s", taskDurationLabel(completed(t).durationMs!!, AppLanguage.English))
    }

    @Test fun missingOrReversedTimesDoNotInventAnElapsedValue() {
        for ((begin, end) in listOf(null to (start + 10), 0L to (start + 10), start to null, start to (start - 1))) {
            val t = SessionTimeline("fixture-session")
            t.apply(event("task.start", 1, begin)); t.apply(event("task.finish", 2, end))
            assertNull(completed(t).durationMs)
        }
    }

    @Test fun interleavedTasksKeepIndependentTimings() {
        val t = SessionTimeline("fixture-session")
        t.apply(event("task.start", 1, start)); t.apply(event("task.start", 2, start + 30, "other"))
        t.apply(event("task.finish", 3, start + 70, "other")); t.apply(event("task.finish", 4, start + 134))
        assertEquals(134000L, completed(t).durationMs); assertEquals(40000L, completed(t, "other").durationMs)
    }

    @Test fun replayAndLaterStatusFramesDoNotExtendTheActualFinishTime() {
        val t = SessionTimeline("fixture-session")
        val events = listOf(event("task.start", 1, start), event("task.finish", 2, start + 134), event("task.status", 3, start + 145))
        events.forEach(t::apply); events.forEach(t::apply)
        assertEquals(134000L, completed(t).durationMs)
    }

    @Test fun serverHistoryRestoresMissingStartWithoutUsingQueueCreationTime() {
        val t = SessionTimeline("fixture-session")
        t.apply(event("task.finish", 2, start + 134))
        assertNull(completed(t).durationMs)
        t.seedTaskTimings(listOf(TaskBriefDto("fixture-task", status = "ok", createdAt = start - 400,
            startedAt = start, completedAt = start + 134)))
        assertEquals(134000L, completed(t).durationMs)
    }

    @Test fun requeuedWorkKeepsTheWholeTaskInterval() {
        val t = SessionTimeline("fixture-session")
        t.apply(event("task.start", 1, start)); t.apply(event("task.finish", 2, start + 50))
        t.apply(event("task.requeued", 3, start + 55)); t.apply(event("task.start", 4, start + 60))
        t.apply(event("task.finish", 5, start + 100))
        assertEquals(100000L, completed(t).durationMs)
    }

    @Test fun alreadyMillisecondEventsAreNotMultipliedAgain() {
        val t = SessionTimeline("fixture-session")
        t.apply(event("task.start", 1, start * 1000)); t.apply(event("task.finish", 2, (start + 134) * 1000))
        assertEquals(134000L, completed(t).durationMs)
    }

    @Test fun legacyTaskMetadataAndCompactLabelsRemainValid() {
        val task = Json.decodeFromString<TaskBriefDto>("""{"task_id":"fixture-task","status":"ok","created_at":100}""")
        assertNull(task.startedAt); assertNull(task.completedAt)
        assertEquals("менее 1 с", taskDurationLabel(400, AppLanguage.Russian))
        assertEquals("1 ч 1 мин 10 с", taskDurationLabel(3670000, AppLanguage.Russian))
        assertEquals("1 min", taskDurationLabel(60000, AppLanguage.English))
    }
}
