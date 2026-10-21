package ru.igorek.remote.ui

import android.content.ClipData
import android.widget.Toast
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.produceState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.io.File
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.igorek.remote.BuildConfig
import ru.igorek.remote.data.AppLocale
import ru.igorek.remote.data.Bridge
import ru.igorek.remote.data.DiagnosticSupportDraft
import ru.igorek.remote.data.ErrorCatalog
import ru.igorek.remote.data.prepareDiagnosticSupport

private fun createSupportIntent(context: Context, draft: DiagnosticSupportDraft): Intent {
    val directory=File(context.cacheDir,"diagnostics").apply { check(isDirectory || mkdirs()) }
    val file=File(directory,draft.fileName)
    file.outputStream().use { it.write(draft.json.toByteArray(Charsets.UTF_8)) }
    directory.listFiles()?.filter { it!=file && it.isFile && it.name.startsWith("igorek-diagnostics-") && it.extension=="json" }
        ?.sortedByDescending { it.lastModified() }?.drop(9)?.forEach { it.delete() }
    val uri=FileProvider.getUriForFile(context,context.packageName+".files",file)
    return Intent(Intent.ACTION_SEND).apply {
        type="application/json"
        putExtra(Intent.EXTRA_EMAIL,arrayOf(draft.recipient))
        putExtra(Intent.EXTRA_SUBJECT,draft.subject)
        putExtra(Intent.EXTRA_TEXT,AppLocale.text("Опишите проблему. Получатель: ","Describe the problem. Recipient: ")+draft.recipient)
        putExtra(Intent.EXTRA_STREAM,uri)
        clipData=ClipData.newUri(context.contentResolver,"Igorek diagnostics",uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
}

@Composable
internal fun DiagnosticSupportDialog(onDismiss: () -> Unit) {
    val context=LocalContext.current
    val clipboard=LocalClipboardManager.current
    val scope=rememberCoroutineScope()
    val prepared by produceState<Result<DiagnosticSupportDraft>?>(null) {
        value=withContext(Dispatchers.Default) { runCatching { prepareDiagnosticSupport(Bridge.diagnosticSnapshot(),BuildConfig.VERSION_NAME) } }
    }
    val draft=prepared?.getOrNull()
    val lines=remember(draft) { draft?.json?.lines().orEmpty() }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    val chooser=AppLocale.text("Общий диагностический отчёт","Overall diagnostic report")
    AlertDialog(
        onDismissRequest=onDismiss,
        title={Text(chooser)},
        text={Column(Modifier.heightIn(max=440.dp).verticalScroll(rememberScrollState()),verticalArrangement=Arrangement.spacedBy(10.dp)) {
            SelectionContainer { Text(AppLocale.text("Получатель: ","Recipient: ")+ErrorCatalog.supportEmail) }
            Text(AppLocale.text(
                "Общий отчёт: до 10 активных инцидентов и сводка восстановленных за последние сутки. Не более 32 КиБ. Пропуски отмечены. Переписка, ключи и аргументы инструментов не включаются.",
                "Overall report: up to 10 active incidents and grouped recoveries from the last 24 hours. At most 32 KiB. Omissions are marked. Conversation text, credentials and tool arguments are excluded."))
            if (draft != null) TextButton(onClick = {
                clipboard.setText(AnnotatedString(draft.json))
                Toast.makeText(context, AppLocale.text("Скопирован общий отчёт", "Overall report copied"), Toast.LENGTH_SHORT).show()
            }) { Text(AppLocale.text("Скопировать общий отчёт", "Copy overall report")) }
            Text(AppLocale.text(
                "Выберите почтовое приложение, проверьте получателя и подтвердите отправку в нём. Само приложение письма не отправляет.",
                "Choose an email app, check the recipient and confirm sending there. Igorek does not send the email itself."))
            if(draft==null) {
                if(prepared==null) Text(AppLocale.text("Подготовка отчёта…","Preparing report…"))
                else ErrorNotificationEffect(AppLocale.text("Не удалось подготовить отчёт.","Could not prepare the report."), "diagnostic-report")
            }
            else SelectionContainer { LazyColumn(Modifier.height(220.dp)) { items(lines.size) { index -> Text(lines[index],fontFamily=FontFamily.Monospace,fontSize=11.sp) } } }
            if(status.isNotEmpty())Text(status)
        }},
        confirmButton={TextButton(enabled=!busy && draft!=null,onClick=share@{
            val report=draft ?: return@share
            if(busy)return@share
            busy=true
            scope.launch {
                try {
                    val intent=withContext(Dispatchers.IO) { createSupportIntent(context.applicationContext,report) }
                    context.startActivity(Intent.createChooser(intent,chooser))
                    status=AppLocale.text("Открыто системное окно выбора. Отправку подтверждает выбранное приложение.","System sharing opened. Confirm sending in the selected app.")
                }catch(error:CancellationException){throw error}
                catch(_:Exception){Bridge.reportUiError(AppLocale.text("Не удалось открыть передачу отчёта. Ничего не отправлено.","Could not open sharing. Nothing was sent."), "diagnostic-sharing")}
                finally{busy=false}
            }
        }){Text(AppLocale.text("Поделиться отчётом","Share report"))}},
        dismissButton={TextButton(onClick=onDismiss){Text(AppLocale.text("Закрыть","Close"))}},
    )
}
