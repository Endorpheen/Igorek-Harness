package ru.igorek.remote.data

import kotlinx.serialization.json.*

data class ChartSeries(val name: String, val values: List<Double>)
data class ChartSpec(val type: String, val title: String, val labels: List<String>, val series: List<ChartSeries>, val unit: String = "", val source: String = "", val minimum: Double? = null, val maximum: Double? = null) {
    fun bounds(): Pair<Double, Double> {
        val values = series.flatMap { it.values }
        val low = minimum ?: minOf(0.0, values.min())
        val high = maximum ?: maxOf(0.0, values.max())
        return if (low == high) (low - 1) to (high + 1) else low to high
    }
    fun csv(): String {
        fun cell(value: Any): String {
            val raw = value.toString()
            val safe = if (value is String && raw.firstOrNull() in listOf('=', '+', '-', '@')) "'" + raw else raw
            return "\"" + safe.replace("\"", "\"\"") + "\""
        }
        val rows: List<List<Any>> = listOf(listOf("Категория") + series.map { it.name + if (unit.isNotBlank()) " ($unit)" else "" }) + labels.mapIndexed { i, label -> listOf(label) + series.map { it.values[i] } }
        return rows.joinToString("\r\n") { row -> row.joinToString(",", transform = ::cell) }
    }
}

data class ChartResult(val chartId: String, val chart: ChartSpec)
fun isChartTool(name: String): Boolean = Regex("(?:^|.*__|.*\\.)render_chart$").matches(name)

fun parseChart(input: String): ChartSpec? = runCatching {
    require(input.length <= 24000)
    val root = Json.parseToJsonElement(input).jsonObject
    fun text(e: JsonElement?, max: Int, required: Boolean = false): String {
        val p = e as? JsonPrimitive ?: error("text required")
        require(p.isString)
        val s = p.content.trim()
        require(s.codePointCount(0, s.length) <= max && (!required || s.isNotEmpty()) && s.none { it.code < 32 })
        return s
    }
    val type = text(root["type"], 10, true)
    require(type in listOf("bar", "line", "pie"))
    val title = text(root["title"], 160, true)
    val labels = root.getValue("labels").jsonArray.map { text(it, 100, true) }
    require(labels.size in 1..40 && labels.distinct().size == labels.size)
    val series = root.getValue("series").jsonArray.map { raw ->
        val s = raw.jsonObject
        val values = s.getValue("values").jsonArray.map {
            val n = it.jsonPrimitive
            require(!n.isString && n.booleanOrNull == null)
            n.double.also { v -> require(v.isFinite() && kotlin.math.abs(v) <= 1e12) }
        }
        require(values.size == labels.size)
        ChartSeries(text(s["name"], 100, true), values)
    }
    require(series.size in 1..6 && series.map { it.name }.distinct().size == series.size)
    if (type == "pie") require(series.size == 1 && series[0].values.all { it >= 0 } && series[0].values.any { it > 0 })
    fun bound(key: String): Double? = root[key]?.jsonPrimitive?.let { require(!it.isString && it.booleanOrNull == null); it.double.also { v -> require(v.isFinite() && kotlin.math.abs(v) <= 1e12) } }
    val chart = ChartSpec(type, title, labels, series, root["unit"]?.let { text(it,40) } ?: "", root["source"]?.let { text(it,500) } ?: "", bound("y_min"), bound("y_max"))
    if (chart.minimum != null && chart.maximum != null) require(chart.minimum < chart.maximum)
    val values = series.flatMap { it.values }
    val lo = chart.minimum ?: minOf(0.0, values.min())
    val hi = chart.maximum ?: maxOf(0.0, values.max())
    require(values.all { it in lo..hi })
    if (type == "bar") require(lo <= 0 && hi >= 0)
    chart
}.getOrNull()

fun chartResult(item: TimelineItem.ToolCall): ChartResult? = runCatching {
    require(isChartTool(item.name))
    val result = Json.parseToJsonElement(item.resultPreview).jsonObject
    require(result["ok"]?.jsonPrimitive?.booleanOrNull == true && result["kind"]?.jsonPrimitive?.content == "igorek.chart" && result["schema_version"]?.jsonPrimitive?.intOrNull == 1)
    val id = result.getValue("chart_id").jsonPrimitive.content
    require(Regex("[A-Za-z0-9_-]{1,64}").matches(id))
    ChartResult(id, parseChart(item.input) ?: error("invalid chart"))
}.getOrNull()

fun projectCharts(items: List<TimelineItem>): List<TimelineItem> {
    val result = mutableListOf<TimelineItem>()
    val positions = mutableMapOf<String, Int>()
    for (item in items) {
        val chart = (item as? TimelineItem.ToolCall)?.let(::chartResult)
        val index = chart?.let { positions[it.chartId] }
        if (index != null) {
            val previous = result[index] as TimelineItem.ToolCall
            val versions = previous.chartVersions.ifEmpty { listOf(previous.input) } + (item as TimelineItem.ToolCall).input
            result[index] = item.copy(id = previous.id, chartVersions = versions)
        }
        else { if (chart != null) positions[chart.chartId] = result.size; result += item }
    }
    return result
}

fun chartFailure(item: TimelineItem.ToolCall): String? = runCatching {
    val error = Json.parseToJsonElement(item.resultPreview).jsonObject["error"]?.jsonPrimitive
    if (error?.isString == true) error.content.take(800) else null
}.getOrNull()

internal fun formatChartValue(value: Double): String {
    if (value != 0.0 && kotlin.math.abs(value) < 1e-6) return java.math.BigDecimal.valueOf(value).stripTrailingZeros().toString().replace('.', java.text.DecimalFormatSymbols.getInstance().decimalSeparator)
    return java.text.NumberFormat.getNumberInstance().apply { maximumFractionDigits = 340 }.format(value)
}
