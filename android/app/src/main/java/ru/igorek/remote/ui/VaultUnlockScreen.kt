package ru.igorek.remote.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.VaultAccess

@Composable
internal fun VaultUnlockScreen(access: VaultAccess, serverScope: String, online: Boolean, busy: Boolean, error: String?) {
    var password by remember(serverScope) { mutableStateOf("") }
    val submit = {
        val value = password
        password = ""
        Bridge.unlockVault(value)
    }
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background,
            contentColor = MaterialTheme.colorScheme.onBackground) {
    Column(
        modifier = Modifier.fillMaxSize().systemBarsPadding().imePadding().padding(24.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(uiText("Хранилище заблокировано", "Vault locked"), style = MaterialTheme.typography.headlineSmall)
        Text(
            when {
                !online -> uiText("Восстанавливаем соединение с компьютером…", "Reconnecting to your computer…")
                access.state == "not_initialized" -> uiText("Сначала настройте хранилище на компьютере.", "Set up the Vault on your computer first.")
                else -> uiText("Введите тот же код, которым открываете хранилище после запуска Desktop.", "Enter the same code you use to open the Vault after starting Desktop.")
            },
            modifier = Modifier.padding(vertical = 16.dp),
        )
        if (access.unlockAvailable) {
            OutlinedTextField(
                value = password,
                onValueChange = { if (it.length <= 1024) password = it },
                label = { Text(uiText("Код хранилища", "Vault code")) },
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true,
                enabled = online && !busy,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { if (online && !busy && password.isNotBlank()) submit() }),
                modifier = Modifier.fillMaxWidth(),
            )
            error?.let {
                Text(
                    when (it) {
                        "incorrect_code" -> uiText("Код не подошёл. Попробуйте ещё раз.", "Incorrect code. Try again.")
                        "rate_limited" -> uiText("Слишком много попыток. Подождите минуту.", "Too many attempts. Wait a minute.")
                        "setup_required" -> uiText("Настройте хранилище на компьютере.", "Set up the Vault on your computer.")
                        else -> uiText("Не удалось подтвердить разблокировку. Проверьте соединение.", "Could not confirm unlock. Check the connection.")
                    },
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(vertical = 12.dp),
                )
            }
            Button(onClick = submit, enabled = online && !busy && password.isNotBlank(), modifier = Modifier.padding(top = 16.dp)) {
                if (busy) CircularProgressIndicator()
                else Text(uiText("Разблокировать", "Unlock"))
            }
        }
    }
    }
}
