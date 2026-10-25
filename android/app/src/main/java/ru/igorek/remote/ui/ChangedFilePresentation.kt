package ru.igorek.remote.ui

import java.util.Locale
import ru.igorek.remote.data.AppLanguage
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.ChangedFilesResponse
import ru.igorek.remote.data.ChangesReview
import ru.igorek.remote.data.ReviewFile

internal fun nonTextChangeLabel(path: String, reason: String = "", language: AppLanguage = AppLocale.current): String? {
    val extension = path.substringAfterLast('/').substringAfterLast('.', "").lowercase(Locale.ROOT)
    val kind = when (extension) {
        "xlsx", "xls", "xlsm", "xlsb", "xlt", "xltx", "xltm", "ods", "ots", "numbers" -> "spreadsheet"
        "mp4", "m4v", "mov", "webm", "mkv", "avi", "mpeg", "mpg", "3gp", "ogv" -> "video"
        "mp3", "wav", "flac", "ogg", "m4a", "aac", "opus", "aiff", "wma" -> "audio"
        "jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif", "avif", "ico" -> "image"
        else -> if (reason == "binary") "binary" else return null
    }
    return when (kind) {
        "spreadsheet" -> if (language == AppLanguage.Russian) "Таблица" else "Spreadsheet"
        "video" -> if (language == AppLanguage.Russian) "Видео" else "Video"
        "audio" -> if (language == AppLanguage.Russian) "Аудио" else "Audio"
        "image" -> if (language == AppLanguage.Russian) "Изображение" else "Image"
        else -> if (language == AppLanguage.Russian) "Бинарный файл" else "Binary file"
    }
}

internal fun textChangeCounts(changes: ChangedFilesResponse): Pair<Int, Int> {
    if (changes.files.none { nonTextChangeLabel(it.path) != null }) return changes.totals.additions to changes.totals.deletions
    val text = changes.files.filter { nonTextChangeLabel(it.path) == null }
    return text.sumOf { it.additions } to text.sumOf { it.deletions }
}

internal fun reviewTextCounts(review: ChangesReview): Pair<Int, Int> {
    if (review.files.none { nonTextChangeLabel(it.path, it.unavailableReason) != null }) return review.totals.additions to review.totals.deletions
    val text = review.files.filter { nonTextChangeLabel(it.path, it.unavailableReason) == null }
    return text.sumOf { it.additions ?: 0 } to text.sumOf { it.deletions ?: 0 }
}

internal fun isNonTextReview(file: ReviewFile): Boolean = nonTextChangeLabel(file.path, file.unavailableReason) != null
