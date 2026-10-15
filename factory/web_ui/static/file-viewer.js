/* Igorek's right-side file viewer: general limits, type detection, Excel session in
 * Web Worker, and code highlighting. The file is loaded before app.js (see index.html) and
 * does not pull anything externally—all vendor libraries are bundled locally (static/vendor).
 *
 * Security: Excel cell values and source code are injected into the DOM only as text (`textContent`);
 * highlighting is added via highlight.js, which itself escapes input data. Formulas are not evaluated,
 * macros are not executed, and no external requests are made from the worker (CSP: `default-src 'self'`).
 */
"use strict";
(function (root) {
  const LIMITS = Object.freeze({
    // Excel parsing: file limited to 10 MiB (metadata and actual file size are checked);
    // only the first 1000 rows × 100 columns are loaded into the grid, with only 100 rows displayed in the DOM.
    spreadsheetBytes: 10 * 1024 * 1024,
    spreadsheetRows: 1000,
    spreadsheetCols: 100,
    rowsPerPage: 100,
    parseTimeoutMs: 15000,
    // Highlighting: content over 100 KB is not highlighted, but the source remains readable.
    highlightBytes: 100 * 1024,
  });

  const SPREADSHEET_EXTENSIONS = new Set(["xlsx", "xls", "xlsm", "xlsb"]);
  const SPREADSHEET_MIME = new Set([
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.ms-excel.sheet.macroenabled.12",
    "application/vnd.ms-excel.sheet.binary.macroenabled.12",
  ]);
  // Unsupported formats for table embedding (even with 'office' MIME): CSV/TSV remain as text view.
  const SPREADSHEET_EXCLUDED_EXTENSIONS = new Set(["csv", "tsv", "txt"]);

  const FILE_VIEW_MARKDOWN_EXTENSIONS = new Set(["md", "markdown", "mdown", "mkd"]);
  const FILE_VIEW_MARKUP_EXTENSIONS = new Set(["html", "htm", "xhtml", "svg"]);
  const FILE_VIEW_MARKUP_MIME = new Set(["text/html", "application/xhtml+xml", "image/svg+xml"]);
  const FILE_VIEW_IMAGE_MIME = new Set([
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/svg+xml", "image/avif",
  ]);
  const FILE_VIEW_TEXT_MIME = new Set([
    "application/json", "application/xml", "application/javascript", "application/x-sh",
    "application/x-yaml", "application/yaml", "application/toml", "application/sql",
  ]);
  const FILE_VIEW_TEXT_EXTENSIONS = new Set([
    "txt", "log", "md", "markdown", "rst", "csv", "tsv", "json", "jsonl", "ndjson", "yaml", "yml",
    "toml", "ini", "cfg", "conf", "py", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "html", "htm",
    "css", "scss", "sass", "less", "sh", "bash", "zsh", "fish", "rs", "go", "java", "kt", "kts",
    "c", "h", "cc", "cpp", "hpp", "cs", "rb", "php", "pl", "lua", "swift", "dart", "sql", "xml",
    "svg", "graphql", "gql", "env", "diff", "patch",
  ]);

  // Extension → highlight.js grammar from vendor bundle (common).
  // The value must be a valid, registered name: it is checked via `hljs.getLanguage` before highlighting;
  // otherwise, the text remains unhighlighted.
  const CODE_LANGUAGE_ALIASES = {
    py: "python", pyi: "python", pyw: "python",
    js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
    ts: "typescript", tsx: "typescript", mts: "typescript", cts: "typescript",
    json: "json", jsonl: "json", ndjson: "json",
    html: "xml", htm: "xml", xhtml: "xml", svg: "xml", xml: "xml", xsl: "xml", xslt: "xml",
    css: "css", scss: "scss", sass: "scss", less: "less",
    sh: "bash", bash: "bash", zsh: "bash", fish: "bash", shell: "bash", env: "bash",
    yml: "yaml", yaml: "yaml",
    sql: "sql",
    rs: "rust", go: "go", java: "java", kt: "kotlin", kts: "kotlin",
    c: "c", h: "c", cc: "cpp", cpp: "cpp", cxx: "cpp", hpp: "cpp", hh: "cpp", hxx: "cpp",
    cs: "csharp", rb: "ruby", php: "php", pl: "perl", lua: "lua",
    swift: "swift", md: "markdown", markdown: "markdown", mdown: "markdown", mkd: "markdown",
    toml: "ini", ini: "ini", cfg: "ini", conf: "ini",
    diff: "diff", patch: "diff", graphql: "graphql", gql: "graphql",
  };
  // Languages without syntax highlighting: plain text, no auto-detection.
  const PLAIN_LANGUAGES = new Set(["", "txt", "log", "text", "plain", "plaintext", "file", "code"]);

  function extensionOf(file) {
    const name = String((file && file.name) || "").toLowerCase();
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot + 1) : "";
  }

  function kind(file) {
    const rawMime = String((file && file.mime_type) || "").toLowerCase();
    const mime = rawMime.split(";")[0].trim();
    const extension = extensionOf(file);
    if (FILE_VIEW_MARKUP_MIME.has(mime) || FILE_VIEW_MARKUP_EXTENSIONS.has(extension)) return "html";
    if (SPREADSHEET_EXTENSIONS.has(extension)) return "spreadsheet";
    if (SPREADSHEET_MIME.has(mime) && !SPREADSHEET_EXCLUDED_EXTENSIONS.has(extension)) return "spreadsheet";
    if (mime.startsWith("image/") || FILE_VIEW_IMAGE_MIME.has(mime)) return "image";
    if (mime === "text/markdown" || FILE_VIEW_MARKDOWN_EXTENSIONS.has(extension)) return "markdown";
    if (mime.startsWith("text/") || FILE_VIEW_TEXT_MIME.has(mime) || FILE_VIEW_TEXT_EXTENSIONS.has(extension)) return "text";
    return "binary";
  }

  function grammarFor(file) {
    const extension = extensionOf(file);
    if (!extension) return "";
    return grammarForName(extension);
  }

  function grammarForName(name) {
    const key = String(name || "").toLowerCase().trim();
    if (PLAIN_LANGUAGES.has(key)) return "";
    return CODE_LANGUAGE_ALIASES[key] || key;
  }

  // Column headers in A..Z, AA..ZZ format, like in Excel.
  function columnLabel(index) {
    let value = Number(index);
    if (!Number.isFinite(value) || value < 0) value = 0;
    let label = "";
    value += 1;
    while (value > 0) {
      const remainder = (value - 1) % 26;
      label = String.fromCharCode(65 + remainder) + label;
      value = Math.floor((value - 1) / 26);
    }
    return label;
  }

  function byteLength(text) {
    if (typeof TextEncoder === "function") {
      try { return new TextEncoder().encode(String(text)).length; } catch (error) { /* Counting by characters */ }
    }
    return String(text || "").length;
  }

  function spreadsheetTooBig(size) {
    const value = Number(size);
    return Number.isFinite(value) && value > LIMITS.spreadsheetBytes;
  }

  function errorOf(code, message) {
    const error = new Error(message || code);
    error.code = code;
    return error;
  }

  /* ---- Code highlighting (highlight.js) ---------------------------------- */

  let highlightPromise = null;

  // Library loads lazily: 127 KB unused until code is opened.
  // CSP `script-src 'self'` allows only local /vendor/highlight.min.js.
  function ensureHighlightJs() {
    if (root.hljs) return Promise.resolve(root.hljs);
    if (highlightPromise) return highlightPromise;
    if (typeof document === "undefined") return Promise.resolve(null);
    highlightPromise = new Promise(function (resolve) {
      const script = document.createElement("script");
      script.src = "/vendor/highlight.min.js";
      script.async = true;
      script.addEventListener("load", function () { resolve(root.hljs || null); });
      script.addEventListener("error", function () { resolve(null); });
      document.head.append(script);
    });
    return highlightPromise;
  }

  // Returns a promise with the highlighting status. The original text is always placed as textContent first,
  // so readability is preserved even with errors or unknown languages, and copying takes the full original text.
  function applyHighlight(codeElement, text, grammar) {
    if (!codeElement) return Promise.resolve({ highlighted: false, reason: "missing" });
    codeElement.textContent = String(text == null ? "" : text);
    const requested = grammarForName(grammar);
    if (!requested) return Promise.resolve({ highlighted: false, reason: "plain" });
    if (byteLength(text) > LIMITS.highlightBytes) {
      return Promise.resolve({ highlighted: false, reason: "limit", limit: LIMITS.highlightBytes });
    }
    return ensureHighlightJs().then(function (hljs) {
      if (!hljs || typeof hljs.highlightElement !== "function") {
        return { highlighted: false, reason: "unavailable" };
      }
      let language = null;
      try { language = hljs.getLanguage(requested); } catch (error) { language = null; }
      if (!language) return { highlighted: false, reason: "unknown", language: requested };
      try {
        codeElement.className = "language-" + requested;
        // highlightElement reads textContent, escapes and places
        // span classes; raw markup input does not become valid.
        hljs.highlightElement(codeElement);
      } catch (error) {
        codeElement.textContent = String(text == null ? "" : text);
        return { highlighted: false, reason: "error" };
      }
      return { highlighted: true, language: requested };
    });
  }

  function xmlPart(parts, path) {
    const source = parts[path];
    if (!source || /<!DOCTYPE|<!ENTITY/i.test(source)) return null;
    const doc = new DOMParser().parseFromString(source, "application/xml");
    return doc.getElementsByTagName("parsererror").length ? null : doc;
  }

  function children(node, name) {
    return node ? Array.from(node.children || []).filter(function (child) { return child.localName === name; }) : [];
  }

  function first(node, name) { return children(node, name)[0] || null; }
  function descendants(node, name) { return node ? Array.from(node.getElementsByTagNameNS("*", name)) : []; }
  function nodeText(node) { return node ? String(node.textContent || "").slice(0, 300) : ""; }
  function richTitle(node) { return descendants(node, "t").map(nodeText).join("").slice(0, 300); }

  function relationships(parts, owner) {
    const slash = owner.lastIndexOf("/");
    const dir = owner.slice(0, slash + 1);
    const doc = xmlPart(parts, dir + "_rels/" + owner.slice(slash + 1) + ".rels");
    const result = new Map();
    descendants(doc, "Relationship").forEach(function (rel) {
      if (rel.getAttribute("TargetMode") === "External") return;
      const target = rel.getAttribute("Target") || "";
      if (!target || target.includes(":") || target.includes("\\")) return;
      const segments = (target.startsWith("/") ? target.slice(1) : dir + target).split("/");
      const normalized = [];
      for (const segment of segments) {
        if (segment === "..") { if (!normalized.length) return; normalized.pop(); }
        else if (segment && segment !== ".") normalized.push(segment);
      }
      result.set(rel.getAttribute("Id"), { path: normalized.join("/"), type: rel.getAttribute("Type") || "" });
    });
    return result;
  }

  async function parseChartParts(parts, sheets, readValues, limited, media) {
    if (typeof DOMParser !== "function") return;
    const book = xmlPart(parts, "xl/workbook.xml");
    const bookRels = relationships(parts, "xl/workbook.xml");
    const entries = descendants(book, "sheet");
    let chartCount = 0;
    const references = new Map();
    function read(reference) {
      if (!references.has(reference)) references.set(reference, readValues(reference));
      return references.get(reference);
    }
    async function data(node, numeric, chart) {
      if (!node) return [];
      const ref = first(node, "numRef") || first(node, "strRef");
      const formula = nodeText(first(ref, "f"));
      if (formula) {
        const result = await read(formula);
        if (result.values) {
          if (result.truncated) chart.truncated = true;
          return result.values.map(function (cell) { return numeric ? cell.value : cell.text; });
        }
      }
      const cache = first(ref, "numCache") || first(ref, "strCache") || first(node, "numLit") || first(node, "strLit");
      if (!cache) {
        const value = first(node, "v");
        return value ? [numeric ? Number(nodeText(value)) : nodeText(value)] : [];
      }
      const count = Number((first(cache, "ptCount") || {}).getAttribute?.("val")) || 0;
      const values = new Array(Math.min(1000, Math.max(0, count))).fill(null);
      if (count > 1000) chart.truncated = true;
      children(cache, "pt").forEach(function (point) {
        const index = Number(point.getAttribute("idx"));
        if (!Number.isInteger(index) || index < 0 || index >= 1000) return;
        const raw = nodeText(first(point, "v"));
        values[index] = numeric ? (raw.trim() && Number.isFinite(Number(raw)) ? Number(raw) : null) : raw;
      });
      return values;
    }
    for (const entry of entries) {
      const meta = sheets.find(function (sheet) { return sheet.name === entry.getAttribute("name"); });
      if (!meta) continue;
      meta.charts = [];
      meta.images = [];
      meta.chartLimit = Boolean(limited);
      const rid = entry.getAttributeNS("http://schemas.openxmlformats.org/officeDocument/2006/relationships", "id")
        || entry.getAttribute("r:id");
      const owner = bookRels.get(rid);
      if (!owner) continue;
      const paths = [];
      for (const rel of relationships(parts, owner.path).values()) {
        if (rel.type.endsWith("/chart")) paths.push(rel.path);
        if (rel.type.endsWith("/drawing")) {
          for (const drawingRel of relationships(parts, rel.path).values()) {
            if (drawingRel.type.endsWith("/chart")) paths.push(drawingRel.path);
            if (drawingRel.type.endsWith("/image")) {
              const image = (media || {})[drawingRel.path];
              if (!image) { meta.chartLimit = true; continue; }
              let binary = "";
              for (let offset = 0; offset < image.bytes.length; offset += 8192) {
                binary += String.fromCharCode.apply(null, image.bytes.subarray(offset, offset + 8192));
              }
              meta.images.push({ src: "data:" + image.mime + ";base64," + btoa(binary) });
            }
          }
        }
      }
      for (const path of new Set(paths)) {
        if (chartCount >= 32) { meta.chartLimit = true; break; }
        chartCount += 1;
        const doc = xmlPart(parts, path);
        const chartNode = descendants(doc, "chart")[0];
        const plot = first(chartNode, "plotArea");
        const chart = { title: richTitle(first(chartNode, "title")), series: [], unsupported: false, truncated: false,
          xTitle: "", yTitle: "" };
        const xAxis = first(plot, "catAx") || first(plot, "dateAx");
        const yAxis = first(plot, "valAx");
        chart.xTitle = richTitle(first(xAxis, "title"));
        chart.yTitle = richTitle(first(yAxis, "title"));
        const types = new Set(["lineChart", "barChart", "scatterChart", "areaChart", "pieChart", "doughnutChart"]);
        for (const group of Array.from(plot?.children || [])) {
          if (!group.localName.endsWith("Chart")) continue;
          const grouping = (first(group, "grouping") || {}).getAttribute?.("val");
          const horizontal = (first(group, "barDir") || {}).getAttribute?.("val") === "bar";
          if (!types.has(group.localName) || grouping === "stacked" || grouping === "percentStacked" || horizontal) {
            chart.unsupported = true;
            continue;
          }
          for (const series of children(group, "ser")) {
            if (chart.series.length >= 16) { chart.truncated = true; break; }
            const names = await data(first(series, "tx"), false, chart);
            const scatter = group.localName === "scatterChart";
            const values = await data(first(series, scatter ? "yVal" : "val"), true, chart);
            const labels = await data(first(series, scatter ? "xVal" : "cat"), scatter, chart);
            chart.series.push({ name: names[0] || richTitle(first(series, "tx")), type: group.localName,
              labels: labels, values: values, scatter: scatter,
              connected: scatter && /line|smooth/i.test((first(group, "scatterStyle") || {}).getAttribute?.("val") || "") });
          }
        }
        if (!plot || !chart.series.length) chart.unsupported = true;
        if (chart.series.some(function (series) { return series.scatter; }) &&
            chart.series.some(function (series) { return !series.scatter; })) chart.unsupported = true;
        const axes = children(plot, "valAx");
        if (axes.some(function (axis) { return first(first(axis, "scaling"), "logBase"); }) ||
            (!chart.series.some(function (series) { return series.scatter; }) && axes.length > 1)) chart.unsupported = true;
        meta.charts.push(chart);
      }
    }
  }

  function renderSpreadsheetCharts(charts, messages) {
    const wrap = document.createElement("div");
    wrap.className = "sheet-charts";
    const colors = ["#b7eb63", "#67d9c1", "#89b7fa", "#f1bc72", "#d6a0ee", "#ef9299"];
    function svgNode(tag, attrs, text) {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      Object.entries(attrs || {}).forEach(function (entry) { node.setAttribute(entry[0], String(entry[1])); });
      if (text !== undefined) node.textContent = String(text);
      return node;
    }
    function appendText(parent, tag, className, text) {
      const node = document.createElement(tag);
      node.className = className;
      node.textContent = text;
      parent.append(node);
      return node;
    }
    (charts || []).forEach(function (chart, chartIndex) {
      const card = document.createElement("section");
      card.className = "sheet-chart";
      const title = chart.title || messages.title + " " + (chartIndex + 1);
      appendText(card, "h3", "sheet-chart-title", title);
      if (chart.unsupported) {
        appendText(card, "p", "side-note", messages.unsupported);
        wrap.append(card);
        return;
      }
      const series = chart.series || [];
      const points = series.flatMap(function (s) { return s.values.filter(function (v) { return typeof v === "number" && Number.isFinite(v); }); });
      if (!points.length) {
        appendText(card, "p", "side-note", messages.empty);
        wrap.append(card);
        return;
      }
      const svg = svgNode("svg", { viewBox: "0 0 760 340", role: "img", "aria-label": title, class: "sheet-chart-svg" });
      svg.append(svgNode("title", {}, title));
      const pie = series.some(function (s) { return s.type === "pieChart" || s.type === "doughnutChart"; });
      const legend = document.createElement("div");
      legend.className = "sheet-chart-legend";
      function legendItem(name, index) {
        const item = document.createElement("span");
        item.className = "sheet-chart-legend-item";
        const swatch = svgNode("svg", { width: 10, height: 10, "aria-hidden": "true" });
        swatch.append(svgNode("rect", { width: 10, height: 10, rx: 2, fill: colors[index % colors.length] }));
        item.append(swatch, document.createTextNode(name));
        legend.append(item);
      }
      if (pie) {
        const s = series[0];
        const total = s.values.reduce(function (sum, value) { return sum + (value > 0 ? value : 0); }, 0);
        if (s.values.some(function (value) { return value < 0; }) || total <= 0 || series.length !== 1) {
          appendText(card, "p", "side-note", messages.unsupported);
          wrap.append(card);
          return;
        }
        let angle = -Math.PI / 2;
        s.values.forEach(function (value, index) {
          if (!(value > 0)) return;
          const sweep = value / total * Math.PI * 2;
          const end = angle + sweep;
          const name = String(s.labels[index] ?? index + 1);
          const color = colors[index % colors.length];
          const segment = sweep >= Math.PI * 2 - 1e-8
            ? svgNode("circle", { cx: 380, cy: 165, r: 125, fill: color })
            : svgNode("path", { d: "M380 165 L" + (380 + 125 * Math.cos(angle)) + " " + (165 + 125 * Math.sin(angle)) +
              " A125 125 0 " + (sweep > Math.PI ? 1 : 0) + " 1 " + (380 + 125 * Math.cos(end)) + " " + (165 + 125 * Math.sin(end)) + " Z", fill: color });
          segment.append(svgNode("title", {}, name + ": " + value));
          svg.append(segment);
          if (index < 24) legendItem(name, index);
          angle = end;
        });
        if (s.type === "doughnutChart") svg.append(svgNode("circle", { cx: 380, cy: 165, r: 65, class: "sheet-chart-hole" }));
      } else {
        const barSeries = series.filter(function (s) { return s.type === "barChart"; });
        let min = Math.min(...points), max = Math.max(...points);
        if (barSeries.length || series.some(function (s) { return s.type === "areaChart"; })) { min = Math.min(0, min); max = Math.max(0, max); }
        const pad = (max - min || Math.abs(max) * .05 || 1) * .08;
        min -= pad; max += pad;
        const length = Math.max(...series.map(function (s) { return s.values.length; }), 1);
        const scatterPoints = series.filter(function (s) { return s.scatter; }).flatMap(function (s) { return s.labels.filter(function (v) { return typeof v === "number" && Number.isFinite(v); }); });
        const xMin = scatterPoints.length ? Math.min(...scatterPoints) : 0;
        const xMax = scatterPoints.length ? Math.max(...scatterPoints) : Math.max(1, length - 1);
        const x = function (s, index) {
          if (s.scatter) return 72 + ((s.labels[index] ?? xMin) - xMin) / (xMax - xMin || 1) * 650;
          return barSeries.length ? 72 + (index + .5) / length * 650 : 72 + index / Math.max(1, length - 1) * 650;
        };
        const y = function (value) { return 280 - (value - min) / (max - min) * 240; };
        for (let tick = 0; tick <= 4; tick += 1) {
          const value = min + (max - min) * tick / 4;
          const at = y(value);
          svg.append(svgNode("line", { x1: 72, x2: 722, y1: at, y2: at, class: "sheet-chart-grid" }));
          svg.append(svgNode("text", { x: 62, y: at + 4, "text-anchor": "end", class: "sheet-chart-label" }, Number(value.toFixed(2))));
        }
        const labels = series[0].labels;
        for (let index = 0; index < Math.min(length, 6); index += 1) {
          const at = Math.round(index * (length - 1) / Math.max(1, Math.min(length, 6) - 1));
          svg.append(svgNode("text", { x: x(series[0], at), y: 303, "text-anchor": "middle", class: "sheet-chart-label" }, String(labels[at] ?? at + 1).slice(0, 20)));
        }
        if (chart.xTitle) svg.append(svgNode("text", { x: 397, y: 331, "text-anchor": "middle", class: "sheet-chart-label" }, chart.xTitle));
        if (chart.yTitle) svg.append(svgNode("text", { x: 72, y: 20, class: "sheet-chart-label" }, chart.yTitle));
        series.forEach(function (s, seriesIndex) {
          const color = colors[seriesIndex % colors.length];
          legendItem(s.name || messages.series + " " + (seriesIndex + 1), seriesIndex);
          let path = "", connected = false;
          s.values.forEach(function (value, index) {
            if (typeof value !== "number" || !Number.isFinite(value) || (s.scatter && typeof s.labels[index] !== "number")) { connected = false; return; }
            const px = x(s, index), py = y(value);
            path += (connected ? " L" : " M") + px + " " + py;
            connected = true;
            const mark = s.type === "barChart"
              ? svgNode("rect", { x: px - 650 / length * .35 + barSeries.indexOf(s) * 650 / length * .7 / barSeries.length,
                y: Math.min(py, y(0)), width: Math.max(.2, 650 / length * .7 / barSeries.length), height: Math.abs(py - y(0)), fill: color })
              : svgNode("circle", { cx: px, cy: py, r: length > 100 ? 2 : 3.5, fill: color });
            mark.append(svgNode("title", {}, (s.name || messages.series + " " + (seriesIndex + 1)) + " · " + (s.labels[index] ?? index + 1) + ": " + value));
            svg.append(mark);
          });
          if (s.type === "areaChart") {
            let segment = [];
            function fillArea() {
              if (!segment.length) return;
              const polygon = segment.concat([[segment[segment.length - 1][0], y(0)], [segment[0][0], y(0)]]);
              svg.insertBefore(svgNode("polygon", { points: polygon.map(function (p) { return p.join(","); }).join(" "), fill: color, "fill-opacity": .18, "pointer-events": "none" }), svg.firstChild?.nextSibling || null);
              segment = [];
            }
            s.values.forEach(function (value, index) {
              if (typeof value !== "number" || !Number.isFinite(value)) { fillArea(); return; }
              segment.push([x(s, index), y(value)]);
            });
            fillArea();
          }
          if (s.type === "lineChart" || s.type === "areaChart" || s.connected) {
            svg.insertBefore(svgNode("path", { d: path, fill: "none", stroke: color, "stroke-width": 2, "pointer-events": "none" }), svg.firstChild?.nextSibling || null);
          }
        });
      }
      card.append(svg, legend);
      if (chart.truncated) appendText(card, "p", "side-note", messages.limit);
      wrap.append(card);
    });
    return wrap;
  }

  /* ---- Igorek Excel session in Web Worker -- */

  // Protocol: {id, type:"parse", buffer} -> {type:"parsed", sheets}; (buffer transferable)
  //            {id, type:"sheet", index, start, end} -> {type:"sheet", rows};
  //            {type:"error", id, code, message}.
  // The worker holds the parsed workbook until the view is open: switching sheets
  // does not re-read the file. New workbook/close/timer -> terminate().
  function createWorkbookSession(options) {
    const settings = options || {};
    const workerUrl = settings.workerUrl || "/workers/xlsx-worker.js";
    const timeoutMs = Number(settings.timeoutMs) > 0 ? Number(settings.timeoutMs) : LIMITS.parseTimeoutMs;
    const factory = typeof settings.workerFactory === "function"
      ? settings.workerFactory
      : function () { return new Worker(workerUrl); };

    const session = {
      worker: null,
      nextId: 1,
      pending: new Map(),
      disposed: false,
      sheets: [],
      pages: new Map(),
      inflight: new Map(),
      // Tracking for testing and diagnostics: how many actual requests went to the worker.
      requests: 0,
    };

    function rejectAll(error) {
      for (const entry of session.pending.values()) {
        clearTimeout(entry.timer);
        entry.reject(error);
      }
      session.pending.clear();
    }

    function hardStop(code, message) {
      if (session.worker) {
        try { session.worker.terminate(); } catch (error) { /* Worker is already dead */ }
        session.worker = null;
      }
      session.sheets = [];
      session.pages.clear();
      session.inflight.clear();
      rejectAll(errorOf(code || "timeout", message));
    }

    function onMessage(event) {
      const payload = (event && event.data) || {};
      const entry = session.pending.get(payload.id);
      if (!entry) return;
      session.pending.delete(payload.id);
      clearTimeout(entry.timer);
      if (payload.type === "error") {
        entry.reject(errorOf(payload.code || "internal", payload.message || "worker error"));
        return;
      }
      entry.resolve(payload);
    }

    function ensureWorker() {
      if (session.worker) return session.worker;
      const worker = factory();
      worker.onmessage = onMessage;
      worker.onerror = function (event) {
        const message = (event && event.message) || "worker error";
        hardStop("internal", message);
      };
      if (typeof worker.addEventListener === "function") {
        worker.addEventListener("messageerror", function () {
          hardStop("internal", "worker message error");
        });
      }
      session.worker = worker;
      return worker;
    }

    function request(payload, transfer) {
      if (session.disposed) {
        return Promise.reject(errorOf("cancelled", "session disposed"));
      }
      let worker;
      try { worker = ensureWorker(); } catch (error) {
        return Promise.reject(errorOf("internal", (error && error.message) || "worker unavailable"));
      }
      const id = session.nextId;
      session.nextId += 1;
      session.requests += 1;
      return new Promise(function (resolve, reject) {
        const timer = setTimeout(function () {
          hardStop("timeout", "worker timeout");
        }, timeoutMs);
        session.pending.set(id, { resolve: resolve, reject: reject, timer: timer });
        try {
          worker.postMessage(Object.assign({ id: id }, payload), transfer || []);
        } catch (error) {
          clearTimeout(timer);
          session.pending.delete(id);
          reject(errorOf("internal", (error && error.message) || "worker post failed"));
        }
      });
    }

    function parse(bytes) {
      let buffer = bytes;
      if (buffer && buffer.buffer && !(buffer instanceof ArrayBuffer)) {
        // Transfer only significant bytes: the Buffer slice’s .buffer may be wider.
        buffer = buffer.byteOffset === 0 && buffer.byteLength === buffer.buffer.byteLength
          ? buffer.buffer
          : buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
      }
      return request({ type: "parse", buffer: buffer }, [buffer]).then(async function (payload) {
        session.sheets = Array.isArray(payload.sheets) ? payload.sheets : [];
        session.pages.clear();
        await parseChartParts(payload.chartParts || {}, session.sheets, function (reference) {
          return request({ type: "chartValues", reference: reference });
        }, payload.chartLimit, payload.chartMedia || {});
        return session.sheets;
      });
    }

    function sheet(index, start, end) {
      const key = index + ":" + start + ":" + end;
      if (session.pages.has(key)) return Promise.resolve(session.pages.get(key));
      if (session.inflight.has(key)) return session.inflight.get(key);
      const promise = request({ type: "sheet", index: index, start: start, end: end }).then(function (payload) {
        const result = {
          index: index,
          start: Number.isFinite(payload.start) ? payload.start : start,
          end: Number.isFinite(payload.end) ? payload.end : end,
          rows: Array.isArray(payload.rows) ? payload.rows : [],
        };
        session.pages.set(key, result);
        session.inflight.delete(key);
        return result;
      }, function (error) {
        session.inflight.delete(key);
        throw error;
      });
      session.inflight.set(key, promise);
      return promise;
    }

    function dispose() {
      if (session.disposed) return;
      session.disposed = true;
      hardStop("cancelled", "session disposed");
      session.sheets = [];
      session.pages.clear();
      session.inflight.clear();
    }

    return {
      parse: parse,
      sheet: sheet,
      dispose: dispose,
      isDisposed: function () { return session.disposed; },
      sheetMeta: function () { return session.sheets.slice(); },
      requestCount: function () { return session.requests; },
    };
  }

  const api = {
    limits: LIMITS,
    kind: kind,
    extension: extensionOf,
    grammarFor: grammarFor,
    grammarForName: grammarForName,
    columnLabel: columnLabel,
    byteLength: byteLength,
    spreadsheetTooBig: spreadsheetTooBig,
    applyHighlight: applyHighlight,
    ensureHighlightJs: ensureHighlightJs,
    createWorkbookSession: createWorkbookSession,
    parseChartParts: parseChartParts,
    renderSpreadsheetCharts: renderSpreadsheetCharts,
  };

  root.IgorekFileViewer = api;
  if (typeof module !== "undefined" && module && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
