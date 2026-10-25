package ru.igorek.remote.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import ru.igorek.remote.data.*

private val chartColors = listOf(Color(0xffc4f26c), Color(0xff66d6b1), Color(0xff9fc9f3), Color(0xffd9b0f3), Color(0xfff4a96c), Color(0xfff17f74))
private fun chartNumber(value: Double): String = formatChartValue(value)

@Composable
fun ChartCard(item: TimelineItem.ToolCall, modifier: Modifier = Modifier) {
    var selectedVersion by rememberSaveable(item.id, item.input) { mutableIntStateOf(item.chartVersions.lastIndex.coerceAtLeast(0)) }
    var versionsOpen by remember { mutableStateOf(false) }
    val visible = item.chartVersions.getOrNull(selectedVersion)?.let { item.copy(input = it) } ?: item
    val result = remember(visible.input, visible.resultPreview) { chartResult(visible) }
    val context = LocalContext.current
    var expanded by rememberSaveable(item.id) { mutableStateOf(false) }
    var data by rememberSaveable(item.id) { mutableStateOf(false) }
    var exportError by remember { mutableStateOf(false) }
    val csv = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { uri ->
        if (uri != null && result != null) runCatching {
            context.contentResolver.openOutputStream(uri)?.use { it.write(("\uFEFF" + result.chart.csv()).toByteArray(Charsets.UTF_8)) } ?: error("cannot open output")
        }.onFailure { exportError = true }
    }
    Card(modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (result == null) {
                Text(if (item.status in listOf("running", "pending", "queued")) AppLocale.text("Строю график…", "Building chart…") else chartFailure(item) ?: AppLocale.text("Не удалось построить график. Проверьте данные и подписи.", "Could not build chart. Check values and labels."))
            } else {
                ChartContent(result.chart, data)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    TextButton(onClick = { data = !data }) { Text(AppLocale.text("Данные", "Data")) }
                    TextButton(onClick = { csv.launch("chart-${result.chartId}.csv") }) { Text("CSV") }
                    TextButton(onClick = { expanded = true }) { Text(AppLocale.text("Развернуть", "Expand")) }
                }
                if (item.chartVersions.size > 1) Box {
                    TextButton(onClick = { versionsOpen = true }) { Text(AppLocale.text("Версия ", "Version ") + "${selectedVersion + 1}/${item.chartVersions.size}") }
                    DropdownMenu(expanded = versionsOpen, onDismissRequest = { versionsOpen = false }) {
                        item.chartVersions.indices.forEach { index -> DropdownMenuItem(text = { Text(AppLocale.text("Версия ", "Version ") + (index + 1)) }, onClick = { selectedVersion = index; versionsOpen = false }) }
                    }
                }
                if (exportError) Text(AppLocale.text("Не удалось сохранить файл", "Could not save file"), color = MaterialTheme.colorScheme.error)
            }
        }
    }
    if (expanded && result != null) Dialog(onDismissRequest = { expanded = false }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(Modifier.fillMaxSize(), shape = MaterialTheme.shapes.large) { Column(Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            TextButton(onClick = { expanded = false }) { Text(AppLocale.text("Закрыть", "Close")) }
            ChartContent(result.chart, true)
        } }
    }
}

@Composable
private fun ChartContent(chart: ChartSpec, data: Boolean) {
    Text(chart.title, style = MaterialTheme.typography.titleMedium)
    if (chart.unit.isNotBlank()) Text(chart.unit, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    val (lo, hi) = chart.bounds()
    val grid = MaterialTheme.colorScheme.outlineVariant
    if (chart.type == "line" || chart.type == "pie") Canvas(Modifier.fillMaxWidth().height(180.dp)) {
        if (chart.type == "pie") {
            val values = chart.series[0].values; val total = values.sum(); var angle = -90f
            val diameter = minOf(size.width, size.height)
            values.forEachIndexed { i,v -> val sweep = (v / total * 360).toFloat(); drawArc(chartColors[i % 6], angle, sweep, true, Offset((size.width-diameter)/2,0f), Size(diameter,diameter)); angle += sweep }
        } else {
            for (i in 0..4) { val y = size.height * i / 4; drawLine(grid,Offset(0f,y),Offset(size.width,y),1f) }
            chart.series.forEachIndexed { s,series ->
                val points = series.values.mapIndexed { i,v -> Offset(if (series.values.size == 1) size.width/2 else 8 + i*(size.width-16)/(series.values.size-1), (8 + (hi-v)/(hi-lo)*(size.height-16)).toFloat()) }
                points.zipWithNext().forEach { (a,b) -> drawLine(chartColors[s%6],a,b,3f) }
                points.forEach { drawCircle(chartColors[s%6],4f,it) }
            }
        }
    }
    chart.labels.forEachIndexed { i,label ->
        Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Text(label, style = MaterialTheme.typography.titleSmall)
            chart.series.forEachIndexed { s,series ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (chart.type != "bar") Box(Modifier.size(8.dp).background(chartColors[(if (chart.type == "pie") i else s) % 6]))
                    Text(series.name, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                    Text(chartNumber(series.values[i]) + if (chart.unit.isNotBlank()) " ${chart.unit}" else "", style = MaterialTheme.typography.bodyMedium)
                }
                if (chart.type == "bar") Canvas(Modifier.fillMaxWidth().height(5.dp)) {
                    drawRoundRect(grid)
                    val zero = ((0-lo)/(hi-lo)*size.width).toFloat()
                    val end = ((series.values[i]-lo)/(hi-lo)*size.width).toFloat()
                    drawRect(chartColors[s%6], Offset(minOf(zero,end),0f), Size(kotlin.math.abs(end-zero),size.height))
                }
            }
        }
        Spacer(Modifier.height(4.dp))
    }
    if (chart.source.isNotBlank()) Text(AppLocale.text("Источник: ", "Source: ") + chart.source, style = MaterialTheme.typography.bodySmall)
    if (data) {
        HorizontalDivider()
        Text(AppLocale.text("Исходные данные", "Source data"), style = MaterialTheme.typography.titleSmall)
        chart.labels.forEachIndexed { i,label -> Text(label + " — " + chart.series.joinToString("; ") { it.name + ": " + chartNumber(it.values[i]) }, style = MaterialTheme.typography.bodySmall) }
    }
}
