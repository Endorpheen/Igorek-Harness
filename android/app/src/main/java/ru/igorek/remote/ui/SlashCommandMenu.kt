package ru.igorek.remote.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.SlashCommandSuggestion

@Composable
fun SlashCommandMenu(commands: List<SlashCommandSuggestion>, enabled: Boolean, onSelect: (String) -> Unit) {
    if (commands.isEmpty()) return
    Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.surfaceVariant,
        tonalElevation = 4.dp, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.heightIn(max = 208.dp).verticalScroll(rememberScrollState()).padding(vertical = 4.dp)) {
            commands.forEach { command ->
                Column(Modifier.fillMaxWidth().clickable(enabled = enabled, role = Role.Button) {
                    onSelect(command.completion)
                }.padding(horizontal = 14.dp, vertical = 9.dp)) {
                    Text(command.command, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
                    Text(command.description, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}
