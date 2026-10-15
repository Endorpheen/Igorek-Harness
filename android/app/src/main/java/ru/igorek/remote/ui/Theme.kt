package ru.igorek.remote.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

private val DarkColors = darkColorScheme(
    primary = Color(0xFF8DCCF0),
    onPrimary = Color(0xFF102334),
    primaryContainer = Color(0xFF2B1E52),
    onPrimaryContainer = Color(0xFFE8DDFF),
    secondary = Color(0xFF35D0BA),
    onSecondary = Color(0xFF041D19),
    secondaryContainer = Color(0xFF123C39),
    onSecondaryContainer = Color(0xFFB9FFF3),
    background = Color(0xFF090E18),
    onBackground = Color(0xFFE7ECF6),
    surface = Color(0xFF101725),
    onSurface = Color(0xFFE7ECF6),
    surfaceVariant = Color(0xFF172132),
    onSurfaceVariant = Color(0xFFAAB5C8),
    outline = Color(0xFF2B3850),
    error = Color(0xFFFF667A),
    errorContainer = Color(0xFF3B1921),
    onErrorContainer = Color(0xFFFFD9DF),
)

private val LightColors = lightColorScheme(
    primary = Color(0xFF2F5FD0),
    secondary = Color(0xFF2A7F72),
)

@Composable
fun IgorekTheme(content: @Composable () -> Unit) {
    val colors = if (isSystemInDarkTheme()) DarkColors else LightColors
    val typography = MaterialTheme.typography.copy(
        titleLarge = TextStyle(fontSize = 21.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold),
        titleMedium = TextStyle(fontSize = 17.sp, lineHeight = 22.sp, fontWeight = FontWeight.SemiBold),
        titleSmall = TextStyle(fontSize = 14.sp, lineHeight = 19.sp, fontWeight = FontWeight.SemiBold),
        bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
        bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 17.sp),
        labelSmall = TextStyle(fontSize = 10.sp, lineHeight = 14.sp, fontWeight = FontWeight.Medium),
    )
    MaterialTheme(colorScheme = colors, typography = typography, content = content)
}
