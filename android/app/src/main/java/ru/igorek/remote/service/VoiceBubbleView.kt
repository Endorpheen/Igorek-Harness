package ru.igorek.remote.service

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.Paint
import android.graphics.RectF
import android.view.View
import ru.igorek.remote.R

internal class VoiceBubbleView(context: Context) : View(context) {
    private val accent = Color.rgb(141, 204, 240)
    private val backgroundColor = Color.rgb(20, 43, 61)
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val artwork = BitmapFactory.decodeResource(resources, R.drawable.ic_igorek_octopus,
        BitmapFactory.Options().apply { inSampleSize = 4 })
    private val artworkPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG).apply {
        colorFilter = ColorMatrixColorFilter(ColorMatrix(floatArrayOf(
            0f, 0f, 0f, 0f, 141f,
            0f, 0f, 0f, 0f, 204f,
            0f, 0f, 0f, 0f, 240f,
            0f, 0f, 1.15f, 0f, -16f,
        )))
    }
    private var state = VoiceCallState()

    fun updateState(value: VoiceCallState) {
        state = value
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val size = minOf(width, height).toFloat()
        val center = size / 2f
        paint.style = Paint.Style.FILL
        paint.color = if (state.pttRecording) Color.rgb(33, 73, 101) else backgroundColor
        canvas.drawCircle(center, center, center - size / 64f, paint)
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = size / 32f
        paint.color = accent
        canvas.drawCircle(center, center, center - paint.strokeWidth / 2f, paint)
        val inset = size * .08f
        canvas.drawBitmap(artwork, null, RectF(inset, inset, size - inset, size - inset), artworkPaint)
        val marker = when {
            state.pttRecording -> "●"
            state.approval -> "!"
            state.micMuted -> "∕"
            else -> ""
        }
        if (marker.isNotEmpty()) {
            paint.style = Paint.Style.FILL
            paint.color = accent
            val x = size * .79f
            val y = size * .79f
            canvas.drawCircle(x, y, size * .14f, paint)
            paint.color = backgroundColor
            paint.textSize = size * .22f
            paint.textAlign = Paint.Align.CENTER
            paint.isFakeBoldText = true
            canvas.drawText(marker, x, y - (paint.ascent() + paint.descent()) / 2f, paint)
        }
    }
}
