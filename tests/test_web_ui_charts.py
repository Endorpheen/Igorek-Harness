import base64
import io
import json
import re
import subprocess
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer

from test_web_ui import _CHROME, _WEB_STATIC, _ChromeFixtureHandler, _xlsx_fixture_bytes


_PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII=')


def chart_fixture():
    relns = 'http://schemas.openxmlformats.org/package/2006/relationships'
    officens = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    def rels(items):
        return f'<Relationships xmlns="{relns}">' + ''.join(
            f'<Relationship Id="{rid}" Type="{officens}/{kind}" Target="{path}"{extra}/>'
            for rid, kind, path, extra in items) + '</Relationships>'
    chart = '''<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><c:chart>
    <c:title><c:tx><c:rich><a:p><a:r><a:t>&lt;img src=x onerror=alert(1)&gt;</a:t></a:r></a:p></c:rich></c:tx></c:title>
    <c:plotArea><c:lineChart><c:ser><c:tx><c:v>Вес</c:v></c:tx>
    <c:cat><c:strLit><c:ptCount val="3"/><c:pt idx="0"><c:v>01.10</c:v></c:pt><c:pt idx="2"><c:v>04.10</c:v></c:pt></c:strLit></c:cat>
    <c:val><c:numRef><c:f>'Нет листа'!$A$1:$A$3</c:f><c:numCache><c:ptCount val="3"/><c:pt idx="0"><c:v>103.2</c:v></c:pt><c:pt idx="2"><c:v>99.8</c:v></c:pt></c:numCache></c:numRef></c:val>
    </c:ser></c:lineChart></c:plotArea></c:chart></c:chartSpace>'''
    referenced = chart.replace("'Нет листа'!$A$1:$A$3", "'Данные'!$B$1:$B$2")
    unsupported = chart.replace('lineChart', 'radarChart')
    limit = chart.replace('ptCount val="3"', 'ptCount val="1002"')
    files = {
        'xl/worksheets/_rels/sheet1.xml.rels': rels([('d', 'drawing', '../drawings/drawing1.xml', '')]),
        'xl/worksheets/_rels/sheet2.xml.rels': rels([('d', 'drawing', '../drawings/drawing2.xml', '')]),
        'xl/drawings/_rels/drawing1.xml.rels': rels([
            ('c1', 'chart', '../charts/chart1.xml', ''), ('c2', 'chart', '../charts/chart2.xml', ''),
            ('c3', 'chart', '../charts/chart3.xml', ''), ('c4', 'chart', '../charts/chart4.xml', ''),
            ('external', 'image', 'https://example.com/secret.png', ' TargetMode="External"')]),
        'xl/drawings/_rels/drawing2.xml.rels': rels([('i1', 'image', '/xl/media/image1.png', '')]),
        'xl/charts/chart1.xml': chart,
        'xl/charts/chart2.xml': referenced,
        'xl/charts/chart3.xml': unsupported,
        'xl/charts/chart4.xml': limit,
        'xl/media/image1.png': _PNG,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_xlsx_fixture_bytes())) as source, zipfile.ZipFile(output, 'w') as dest:
        for name in source.namelist():
            dest.writestr(name, source.read(name))
        for name, data in files.items():
            dest.writestr(name, data)
    return output.getvalue()


_PAGE = r'''<!doctype html><html><head><meta charset="utf-8">
<script src="/file-viewer.js"></script><script>@UI@</script><script>
const gate = document.createElement("script"); gate.src = "/hold.js"; document.head.append(gate);
</script></head><body><pre id="results">pending</pre><script>
(async () => {
 const results = [];
 const check = (name, ok) => results.push({name, ok: Boolean(ok)});
 try {
  const raw = atob("@BYTES@"); const bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
  const viewer = IgorekFileViewer; const session = viewer.createWorkbookSession();
  const sheets = await session.parse(bytes);
  @CHECKS@
  const page = await session.sheet(0, 0, 0);
  check("grid_preserved", page.rows[0][0] === "Привет");
  check("no_external_requests", performance.getEntriesByType("resource").every(e => new URL(e.name).origin === location.origin));
  session.dispose();
 } catch (error) { results.push({name: "harness", ok: false, detail: String(error.stack || error)}); }
 document.getElementById("results").textContent = "RESULTS:" + btoa(unescape(encodeURIComponent(JSON.stringify(results))));
 await fetch("/done");
})();</script></body></html>'''

_CHECKS = r'''
 const imageView = buildSpreadsheetView({sheets:sheets.map((s,i) => i === 1 ? {...s, rows:0, cols:0} : s), sheetIndex:1, sheetStatus:"ready", sheetPage:0});
 document.body.append(imageView);
 check("empty_sheet_displays_embedded_image", imageView.querySelectorAll("img").length === 1 && !imageView.querySelector(".sheet-grid"));
 const charts = sheets[0].charts;
 const chartView = buildSpreadsheetView({sheets:[{...sheets[0], rows:0, cols:0}], sheetIndex:0, sheetStatus:"ready"});
 check("empty_sheet_displays_chart", chartView.querySelectorAll("svg.sheet-chart-svg").length === 3);
 check("charts_bound_to_sheet", charts.length === 4 && sheets[1].charts.length === 0);
 check("cache_and_gaps", JSON.stringify(charts[0].series[0].values) === "[103.2,null,99.8]");
 check("references_use_current_cells", JSON.stringify(charts[1].series[0].values) === "[0.25,null]");
 check("unsupported_explicit", charts[2].unsupported);
 check("point_limit", charts[3].truncated && charts[3].series[0].values.length === 1000);
 const messages = {title:"График", series:"Ряд", unsupported:"Unsupported", empty:"Empty", limit:"Limit"};
 const rendered = viewer.renderSpreadsheetCharts(charts, messages); document.body.append(rendered);
 check("svg_rendered", rendered.querySelectorAll("svg.sheet-chart-svg").length === 3);
 check("gaps_not_connected", !rendered.querySelector("path").getAttribute("d").includes("L"));
 check("tooltips", rendered.querySelector("circle title").textContent.includes("103.2"));
 check("title_is_literal", rendered.textContent.includes("<img src=x") && !rendered.querySelector("img"));
 check("unsupported_label", rendered.textContent.includes("Unsupported"));
 check("images_bound_to_empty_sheet", sheets[1].images.length === 1 && sheets[0].images.length === 0);
 const image = document.createElement("img"); image.src = sheets[1].images[0].src; document.body.append(image);
 await image.decode(); check("embedded_png_decoded", image.naturalWidth === 1);
 const plain = [{name:"Данные"}];
 await viewer.parseChartParts({"xl/workbook.xml":'<!DOCTYPE x [<!ENTITY a "bad">]><x/>'}, plain, () => {throw Error("Unexpected");}, false);
 check("doctype_rejected", !plain[0].charts);
 for (const type of ["barChart", "scatterChart", "areaChart", "pieChart", "doughnutChart"]) {
  const s = {name:"Ряд", type, values:[2,3,4], labels:type === "scatterChart" ? [1,5,10] : ["А","Б","В"], scatter:type === "scatterChart"};
  const node = viewer.renderSpreadsheetCharts([{title:type, series:[s]}], messages);
  check("render_" + type, Boolean(node.querySelector("svg.sheet-chart-svg")) && !node.innerHTML.includes("NaN"));
 }
'''


def run_browser(fixture, checks=_CHECKS):
    server = ThreadingHTTPServer(('127.0.0.1', 0), _ChromeFixtureHandler)
    source = (_WEB_STATIC / 'app.js').read_text()
    ui = source[source.index('function spreadsheetColumnLabel(index) {'):source.index('function buildFileViewer(model) {')]
    stubs = """const FILE_VIEWER = IgorekFileViewer; const FILE_VIEW_ROWS_PER_PAGE = 100;
    function element(tag, cls, text) { const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n; }
    function t(key) { return key; }
    function panelEmpty(a,b) { return element('p','empty',a+b); }
    function panelLoading() { return element('p','loading'); }
    function loadSpreadsheetPage() {}
    """
    server.page = _PAGE.replace('@UI@', stubs + ui).replace('@BYTES@', base64.b64encode(fixture).decode()).replace('@CHECKS@', checks)
    server.hold = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as profile:
            result = subprocess.run([_CHROME, '--headless=new', '--no-sandbox', '--disable-gpu',
                '--disable-dev-shm-usage', '--no-first-run', f'--user-data-dir={profile}',
                '--dump-dom', f'http://127.0.0.1:{server.server_port}/'], capture_output=True, text=True, timeout=90)
    finally:
        server.hold.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    match = re.search(r'RESULTS:([A-Za-z0-9+/=]+)', result.stdout)
    if not match:
        raise AssertionError(result.stdout[-2000:] + result.stderr[-1000:])
    return json.loads(base64.b64decode(match.group(1)))


@unittest.skipUnless(_CHROME, 'headless Chrome недоступен')
class WebUiChartTest(unittest.TestCase):
    def test_charts_images_references_limits_and_safe_rendering(self):
        results = run_browser(chart_fixture())
        self.assertEqual([], [result for result in results if not result['ok']])
        self.assertGreaterEqual(len(results), 20)
