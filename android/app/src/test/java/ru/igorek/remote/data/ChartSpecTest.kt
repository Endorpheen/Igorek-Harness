package ru.igorek.remote.data

import org.junit.Assert.*
import org.junit.Test
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject

class ChartSpecTest {
    private val input = """{"type":"bar","title":"Тест","labels":["A","B"],"series":[{"name":"Model","values":[0,8]}],"unit":"баллы"}"""
    private fun item(id: String, chart: String = input) = TimelineItem.ToolCall(id,"render_chart",chart,"done", """{"ok":true,"kind":"igorek.chart","schema_version":1,"chart_id":"same"}""")
    @Test fun exposesActionableValidationFailure() { assertEquals("labels must contain 1 to 40 category names",chartFailure(item("a").copy(resultPreview="""{"error":"labels must contain 1 to 40 category names"}"""))) }
    @Test fun restoresChartFromPersistedCallAndResultMessages() {
        val original = item("saved")
        val timeline = SessionTimeline("chart-history")
        timeline.seedFromHistory(listOf(
            MessageDto(role = "tool", kind = "tool_call", toolUseId = "chart-call", toolName = "render_chart", toolInput = Json.parseToJsonElement(input).jsonObject),
            MessageDto(role = "tool", kind = "tool_result", toolUseId = "chart-call", toolName = "render_chart", toolOutput = original.resultPreview),
        ))
        val restored = timeline.snapshot().filterIsInstance<TimelineItem.ToolCall>().single()
        assertEquals("Тест", chartResult(restored)!!.chart.title)
        assertEquals(listOf(0.0, 8.0), chartResult(restored)!!.chart.series.single().values)
    }
    @Test fun smallValuesDoNotBecomeZeroInLabels() {
        val before = java.util.Locale.getDefault()
        try {
            java.util.Locale.setDefault(java.util.Locale.US)
            assertEquals("0.00003",formatChartValue(0.00003))
            assertEquals("3E-9",formatChartValue(3e-9))
        } finally { java.util.Locale.setDefault(before) }
    }
    @Test fun parsesValidatedDataAndBounds() { val chart = parseChart(input)!!; assertEquals("Тест",chart.title); assertEquals(0.0 to 8.0,chart.bounds()) }
    @Test fun rejectsInvalidLengthsAndNumbers() { for (changed in listOf(input.replace("[0,8]","[0]"),input.replace("[0,8]","[true,8]"),input.replace("[0,8]","[null,8]"),input.replace("[0,8]","[1e99,8]"),input.replace("bar","html"))) assertNull(changed,parseChart(changed)) }
    @Test fun requiresPositivePieTotal() { assertNotNull(parseChart(input.replace("bar","pie"))); assertNull(parseChart(input.replace("bar","pie").replace("[0,8]","[0,0]"))) }
    @Test fun rejectsExplicitEqualBounds() { assertNull(parseChart(input.dropLast(1)+",\"y_min\":0,\"y_max\":0}")) }
    @Test fun requiresZeroForBarScale() { assertNull(parseChart(input.replace("[0,8]","[5,8]").dropLast(1)+",\"y_min\":4}")) }
    @Test fun supportsConstantZeroAndNegativeValues() { assertEquals(-1.0 to 1.0,parseChart(input.replace("[0,8]","[0,0]"))!!.bounds()); assertEquals(-8.0 to 0.0,parseChart(input.replace("[0,8]","[-8,-2]"))!!.bounds()) }
    @Test fun csvKeepsNumbersAndEscapesFormulaLabels() { val chart=parseChart(input.replace("\"A\"","\"=SUM(1,2)\"").replace("[0,8]","[-2,8]"))!!; assertTrue(chart.csv().contains("'=SUM(1,2)")); assertTrue(chart.csv().contains("\"-2.0\"")) }
    @Test fun requiresConfirmedVersionedResult() { assertNotNull(chartResult(item("a")));assertNull(chartResult(item("a").copy(resultPreview="{}")));assertNull(chartResult(item("a").copy(resultPreview=""))) }
    @Test fun updateUsesSamePositionAndPreservesRawHistory() { val first=item("first");val text=TimelineItem.AssistantText("text","answer");val next=item("second",input.replace("Тест","Обновлён"));val raw=listOf(first,text,next);val result=projectCharts(raw);assertEquals(2,result.size);assertEquals("first",result[0].id);assertEquals("Обновлён",chartResult(result[0] as TimelineItem.ToolCall)!!.chart.title);assertEquals(3,raw.size) }
    @Test fun parsesUnicodeLabelsInCodePoints() { assertNotNull(parseChart(input.replace("\"A\"","\""+"🐙".repeat(100)+"\"")));assertNull(parseChart(input.replace("\"A\"","\""+"🐙".repeat(101)+"\""))) }
}
