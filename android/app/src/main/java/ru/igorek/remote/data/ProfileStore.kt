package ru.igorek.remote.data

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import kotlin.math.max
import kotlin.math.roundToInt

object ProfileStore {
    private const val MAX_AVATAR_SIDE = 1024
    private val _revision = MutableStateFlow(0L)
    val revision = _revision.asStateFlow()

    fun avatarFile(context: Context): File = File(context.filesDir, "profile/avatar.jpg")

    suspend fun saveAvatar(context: Context, uri: Uri) = withContext(Dispatchers.IO) {
        val bitmap = context.contentResolver.openInputStream(uri)?.use(BitmapFactory::decodeStream)
            ?: throw IOException(AppLocale.text("Не удалось прочитать изображение", "Could not read the image"))
        val longest = max(bitmap.width, bitmap.height)
        val scaled = if (longest > MAX_AVATAR_SIDE) {
            val ratio = MAX_AVATAR_SIDE.toFloat() / longest
            Bitmap.createScaledBitmap(
                bitmap,
                (bitmap.width * ratio).roundToInt().coerceAtLeast(1),
                (bitmap.height * ratio).roundToInt().coerceAtLeast(1),
                true,
            )
        } else {
            bitmap
        }
        val target = avatarFile(context)
        target.parentFile?.mkdirs()
        val temporary = File.createTempFile("avatar-", ".jpg", target.parentFile)
        try {
            temporary.outputStream().use { output ->
                if (!scaled.compress(Bitmap.CompressFormat.JPEG, 90, output)) {
                    throw IOException(AppLocale.text("Не удалось сохранить изображение", "Could not save the image"))
                }
            }
            if (!temporary.renameTo(target)) temporary.copyTo(target, overwrite = true)
            _revision.value = System.currentTimeMillis()
        } finally {
            temporary.delete()
            if (scaled !== bitmap) scaled.recycle()
            bitmap.recycle()
        }
    }
}
