package ru.igorek.remote.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class AppLanguage(val code: String) {
    Russian("ru"),
    English("en");

    companion object {
        fun fromCode(code: String): AppLanguage = entries.firstOrNull { it.code == code } ?: Russian
    }
}

object AppLocale {
    private val _language = MutableStateFlow(AppLanguage.Russian)
    val language = _language.asStateFlow()

    val current: AppLanguage
        get() = _language.value

    fun set(language: AppLanguage) {
        _language.value = language
    }

    fun text(russian: String, english: String): String =
        if (current == AppLanguage.Russian) russian else english
}
