package ru.igorek.remote.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale

// Agent activity indicator in the session header: pulsing dot and moving line.
// Phase is determined solely by structural fields, without text heuristics or timers.

private val WorkGreen = Color(0xFF44D7A8)
private val WorkAmber = Color(0xFFF6B94A)

private val ACTIVE_STATUSES = setOf("queued", "processing", "retry")

internal enum class WorkPhase { Idle, Working, Stopping, WaitingApproval }

internal fun workPhase(
    activeStatus: String,
    pendingApprovals: Int,
    streaming: Boolean,
    hasRunningTask: Boolean,
): WorkPhase {
    val status = activeStatus.trim().lowercase()
    return when {
        status == "waiting_approval" || pendingApprovals > 0 -> WorkPhase.WaitingApproval
        status == "cancelling" -> WorkPhase.Stopping
        status in ACTIVE_STATUSES || streaming || (status.isBlank() && hasRunningTask) -> WorkPhase.Working
        else -> WorkPhase.Idle
    }
}

// Idle state in a pure function: green «online»; amber variant for offline
// uses `connectionDotColor()` when rendering the connection dot.
internal fun workPhaseColor(phase: WorkPhase): Color = when (phase) {
    WorkPhase.Idle, WorkPhase.Working -> WorkGreen
    WorkPhase.Stopping, WorkPhase.WaitingApproval -> WorkAmber
}

internal fun connectionDotColor(connection: String): Color =
    if (connection == "online") WorkGreen else WorkAmber

private val WorkPhase.isAnimated: Boolean
    get() = this == WorkPhase.Working || this == WorkPhase.Stopping

@Composable
internal fun WorkStatusDot(phase: WorkPhase, modifier: Modifier = Modifier) {
    val description = workPhaseDescription(phase)
    if (!phase.isAnimated) {
        Box(
            modifier
                .size(8.dp)
                .background(workPhaseColor(phase), CircleShape)
                .phaseSemantics(description),
        )
        return
    }
    val transition = rememberInfiniteTransition(label = "work-dot")
    val pulse = transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 900, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "work-dot-pulse",
    )
    Box(
        modifier
            .size(8.dp)
            .graphicsLayer {
                // The animated value is only read during the layer phase: frames do not recompose the UI.
                val value = pulse.value
                val scale = 1f + 0.35f * value
                scaleX = scale
                scaleY = scale
                alpha = 1f - 0.65f * value
            }
            .background(workPhaseColor(phase), CircleShape)
            .phaseSemantics(description),
    )
}

@Composable
internal fun WorkSweepLine(phase: WorkPhase, modifier: Modifier = Modifier) {
    val line = modifier.fillMaxWidth().height(2.dp)
    if (!phase.isAnimated) {
        // WaitingApproval — a static highlight without animation; Idle does not draw the line.
        if (phase == WorkPhase.WaitingApproval) {
            Box(line.drawBehind { drawSweep(0.5f, workPhaseColor(phase)) })
        }
        return
    }
    val transition = rememberInfiniteTransition(label = "work-sweep")
    val progress = transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 900, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "work-sweep-progress",
    )
    Box(line.drawBehind { drawSweep(progress.value, workPhaseColor(phase)) })
}

// The highlight moves from left to right; at the ends of the range, it is entirely outside the line,
// so the repetition begins without a jerk.
private fun DrawScope.drawSweep(progress: Float, color: Color) {
    val band = size.width * 0.42f
    val center = -band + progress * (size.width + band * 2f)
    drawRect(
        brush = Brush.linearGradient(
            colors = listOf(Color.Transparent, color, Color.Transparent),
            start = Offset(center - band, 0f),
            end = Offset(center + band, 0f),
        ),
        size = size,
    )
}

// Work phase text without Compose: used in both the indicator's semantics and the status card in the header,
// so it exists as a standalone pure function.
internal fun workPhaseLabel(phase: WorkPhase, language: AppLanguage = AppLocale.current): String? = when (phase) {
    WorkPhase.Idle -> null
    WorkPhase.Working -> if (language == AppLanguage.Russian) "Игорёк работает" else "Igorek is working"
    WorkPhase.Stopping -> if (language == AppLanguage.Russian) "Игорёк останавливается" else "Igorek is stopping"
    WorkPhase.WaitingApproval -> if (language == AppLanguage.Russian) "Игорёк ждёт подтверждения" else "Igorek is waiting for approval"
}

private fun workPhaseDescription(phase: WorkPhase): String? = workPhaseLabel(phase)

private fun Modifier.phaseSemantics(description: String?): Modifier =
    if (description == null) this else semantics { contentDescription = description }
