package ru.igorek.remote.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.State
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.addOutline
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.floor

/**
 * A flickering glow along the perimeter of the instrument card's border while the instrument is running.
 *
 * All geometry is considered a pure function of the animation phase, so logic can be
 * covered with standard unit tests without Android runtime.
 */

/**
 * Maximum brightness of the fading tail. The glow's head is always drawn at full brightness, so its alpha value can be used to distinguish it from the tail.
 */
internal const val ToolBorderTailPeakAlpha = 0.85f

/** A single trail segment along the perimeter: coordinates in path distances and brightness. */
internal data class ToolBorderSegment(
    val start: Float,
    val end: Float,
    val alpha: Float,
)

/**
 * Splits the segment [start, end] into parts within [0, total], correctly handling
 * transition across the start of a closed perimeter (seam).
 */
internal fun normalizePerimeterSpan(start: Float, end: Float, total: Float): List<Pair<Float, Float>> {
    if (total <= 0f || end <= start) return emptyList()
    if (end - start >= total) return listOf(0f to total)

    // Shift the segment so that its start falls within [0, total).
    val shift = floor(start / total) * total
    val from = start - shift
    val to = end - shift

    return if (to <= total) {
        listOf(from to to)
    } else {
        listOf(from to total, 0f to (to - total))
    }
}

/**
 * Glow distribution along the perimeter: a fading tail behind a bright head.
 *
 * @param phase phase in fractions of a rotation, any number (normalized modulo 1)
 * @param total perimeter length
 */
internal fun toolBorderTrace(
    phase: Float,
    total: Float,
    tailFraction: Float = 0.30f,
    headFraction: Float = 0.12f,
    steps: Int = 14,
): List<ToolBorderSegment> {
    if (total <= 0f || steps <= 0) return emptyList()

    val turn = phase - floor(phase)
    val head = turn * total
    val tailLength = (total * tailFraction).coerceAtLeast(0f)
    val headLength = (total * headFraction).coerceAtLeast(0f)

    val segments = ArrayList<ToolBorderSegment>(steps + 2)

    if (tailLength > 0f) {
        for (step in 0 until steps) {
            val from = step / steps.toFloat()
            val to = (step + 1) / steps.toFloat()
            val start = head - tailLength + tailLength * from
            val end = head - tailLength + tailLength * to
            // Brightness increases toward the head: the tail fades smoothly backward without competing with the head.
            val alpha = ToolBorderTailPeakAlpha * to * to
            normalizePerimeterSpan(start, end, total)
                .forEach { (a, b) -> segments += ToolBorderSegment(a, b, alpha) }
        }
    }

    if (headLength > 0f) {
        normalizePerimeterSpan(head, head + headLength, total)
            .forEach { (a, b) -> segments += ToolBorderSegment(a, b, 1f) }
    }

    return segments
}

/**
 * A tool card is considered active only based on its structural status `running`
 * (case and leading/trailing spaces are irrelevant), without text heuristics.
 */
internal fun isRunningToolStatus(status: String): Boolean =
    status.trim().lowercase() == "running"

/**
 * A confirmation window is considered awaiting a decision until the decision is
 * finalized either in the timeline (`resolved`) or via a local button click.
 */
internal fun isApprovalTraceActive(resolved: String?, locallyDecided: Boolean): Boolean =
    !locallyDecided && resolved.isNullOrBlank()

/**
 * Infinite phase of a running glow.
 * The state is read only during the draw phase of the modifier [runningToolBorder], so
 * the card does not undergo recomposition per frame—only its rendering is updated.
 */
@Composable
internal fun rememberToolBorderPhase(durationMillis: Int = 1800): State<Float> {
    val transition = rememberInfiniteTransition(label = "tool-border")
    return transition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = durationMillis, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "tool-border-phase",
    )
}

/**
 * Draws a running glow over the content of the modified node.
 * The phase is read during the draw phase, so animation does not trigger recomposition.
 */
@Composable
internal fun Modifier.runningToolBorder(
    shape: Shape = RoundedCornerShape(15.dp),
    color: Color = Color(0xFF5EA7FF),
    phase: State<Float>,
    stroke: Dp = 1.6.dp,
): Modifier = this.drawWithCache {
    // The perimeter trajectory is calculated once per card size, not per frame.
    val perimeter = Path().apply { addOutline(shape.createOutline(size, layoutDirection, this@drawWithCache)) }
    val measure = PathMeasure().apply { setPath(perimeter, false) }
    val total = measure.length
    val strokeStyle = Stroke(width = stroke.toPx(), cap = StrokeCap.Round)
    val segment = Path()

    onDrawWithContent {
        drawContent()
        if (total <= 0f) return@onDrawWithContent

        val turn = phase.value
        toolBorderTrace(turn, total).forEach { part ->
            segment.reset()
            if (measure.getSegment(part.start, part.end, segment, true)) {
                drawPath(
                    path = segment,
                    color = color.copy(alpha = color.alpha * part.alpha),
                    style = strokeStyle,
                )
            }
        }
    }
}
