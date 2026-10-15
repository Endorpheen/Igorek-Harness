package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

internal const val ISOLATION_KVM = "kvm"

internal fun isolationIndicatorLabel(isolation: String?): String? =
    if (isolation?.trim()?.lowercase() == ISOLATION_KVM) "KVM" else null

@Composable
internal fun IsolationIndicator(isolation: String?, modifier: Modifier = Modifier) {
    val label = isolationIndicatorLabel(isolation) ?: return
    val description = uiText("Изоляция: $label", "Isolation: $label")
    Surface(
        modifier = modifier.semantics { contentDescription = description },
        shape = RoundedCornerShape(50),
        color = MaterialTheme.colorScheme.secondaryContainer,
        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
    ) {
        Row(
            Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(Icons.Default.Shield, contentDescription = null, modifier = Modifier.size(12.dp))
            Text(label, style = MaterialTheme.typography.labelSmall)
        }
    }
}
