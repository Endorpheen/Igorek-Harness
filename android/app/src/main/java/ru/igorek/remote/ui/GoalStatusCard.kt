package ru.igorek.remote.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.SessionGoal

@Composable
fun GoalStatusCard(goal: SessionGoal, busy: Boolean, onAction: (String, String?) -> Unit) {
    var editing by remember(goal.goalId) { mutableStateOf(false) }
    var objective by remember(goal.goalId, editing) { mutableStateOf(goal.objective) }
    val status = when (goal.status) {
        "active" -> AppLocale.text("Выполняется", "Active")
        "paused" -> AppLocale.text("Пауза", "Paused")
        "blocked" -> AppLocale.text("Остановлена", "Blocked")
        "complete" -> AppLocale.text("Выполнена", "Complete")
        "budget_limited" -> AppLocale.text("Лимит токенов", "Token limit")
        "usage_limited" -> AppLocale.text("Лимит использования", "Usage limit")
        else -> goal.status
    }
    Surface(modifier = Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 4.dp),
        shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surfaceVariant,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 7.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(AppLocale.text("Цель", "Goal"), style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary)
                Text(status, style = MaterialTheme.typography.labelSmall)
            }
            Text(goal.objective, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            val usage = goal.tokensUsed.toString() + (goal.tokenBudget?.let { " / $it" } ?: "")
            Text(AppLocale.text("Токены: ", "Tokens: ") + usage, style = MaterialTheme.typography.labelSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                if (goal.status == "active") TextButton(enabled = !busy, contentPadding = PaddingValues(horizontal = 4.dp),
                    onClick = { onAction("pause", null) }) { Text(AppLocale.text("Пауза", "Pause")) }
                if (goal.status in setOf("paused", "blocked", "usage_limited")) TextButton(enabled = !busy,
                    contentPadding = PaddingValues(horizontal = 4.dp), onClick = { onAction("resume", null) }) {
                    Text(AppLocale.text("Продолжить", "Resume"))
                }
                TextButton(enabled = !busy, contentPadding = PaddingValues(horizontal = 4.dp), onClick = { editing = true }) {
                    Text(AppLocale.text("Изменить", "Edit"))
                }
                TextButton(enabled = !busy, contentPadding = PaddingValues(horizontal = 4.dp), onClick = { onAction("clear", null) }) {
                    Text(AppLocale.text("Убрать", "Clear"))
                }
            }
        }
    }
    if (editing) AlertDialog(
        onDismissRequest = { editing = false },
        title = { Text(AppLocale.text("Изменить цель", "Edit goal")) },
        text = { OutlinedTextField(value = objective, onValueChange = { if (it.length <= 4000) objective = it },
            modifier = Modifier.fillMaxWidth(), label = { Text(AppLocale.text("Цель", "Goal")) }) },
        confirmButton = { TextButton(enabled = objective.isNotBlank() && !busy, onClick = {
            onAction("edit", objective.trim()); editing = false
        }) { Text(AppLocale.text("Сохранить", "Save")) } },
        dismissButton = { TextButton(onClick = { editing = false }) { Text(AppLocale.text("Отмена", "Cancel")) } },
    )
}
