/* Spreadsheet worker: SheetJS reads cells and bundled OOXML chart/image parts.
 * The workbook stays here while the viewer is open; only bounded pages,
 * chart references and local raster images are sent to the main thread.
 * Formulas and macros are never executed. No external resources are fetched.
 */
"use strict";

importScripts("../vendor/xlsx.full.min.js");

const MAX_ROWS = 1000;
const MAX_COLS = 100;
const MAX_PAGE_ROWS = 100;

let workbook = null;

function postMessageSafe(message) {
  try { self.postMessage(message); } catch (error) { /* The channel is closed */ }
}

function classifyError(error) {
  const text = String((error && error.message) || error || "");
  if (/password|encrypt/i.test(text)) return "encrypted";
  if (/unsupported|not a spreadsheet|cannot find|corrupt|bad |invalid|zip|signature|namespace|end of file/i.test(text)) {
    return "corrupt";
  }
  return "internal";
}

function errorPayload(id, code, error) {
  return {
    type: "error",
    id: id,
    code: code,
    message: String((error && error.message) || error || code),
  };
}

function boundsOf(sheet) {
  let range = null;
  try {
    range = sheet && sheet["!ref"] ? XLSX.utils.decode_range(sheet["!ref"]) : null;
  } catch (error) {
    range = null;
  }
  const valid = range
    && Number.isFinite(range.s.r) && Number.isFinite(range.s.c)
    && Number.isFinite(range.e.r) && Number.isFinite(range.e.c)
    && range.e.r >= range.s.r && range.e.c >= range.s.c;
  if (!valid) {
    return { rowStart: 0, colStart: 0, rows: 0, cols: 0, totalRows: 0, totalCols: 0, truncated: false };
  }
  const totalRows = range.e.r - range.s.r + 1;
  const totalCols = range.e.c - range.s.c + 1;
  const rows = Math.min(totalRows, MAX_ROWS);
  const cols = Math.min(totalCols, MAX_COLS);
  return {
    rowStart: range.s.r,
    colStart: range.s.c,
    rows: rows,
    cols: cols,
    totalRows: totalRows,
    totalCols: totalCols,
    truncated: rows < totalRows || cols < totalCols,
  };
}

// Cell text: the format (`w`) is more important than the raw value; do not modify the formula cache,
// absence of caching is shown via the formula text itself. sheetStubs provides placeholder cells
// (t:"z") for formulas without saved results — their v=0 instead of the actual value.
function cellText(cell) {
  if (!cell || typeof cell !== "object") return "";
  if (cell.t === "z") return cell.f !== undefined && cell.f !== null ? "=" + String(cell.f) : "";
  if (cell.w !== undefined && cell.w !== null) return String(cell.w);
  if (cell.v !== undefined && cell.v !== null) {
    if (cell.v instanceof Date) {
      const iso = cell.v.toISOString();
      return iso.endsWith("T00:00:00.000Z") ? iso.slice(0, 10) : iso.replace("T", " ").slice(0, 19);
    }
    return String(cell.v);
  }
  if (cell.f !== undefined && cell.f !== null) return "=" + String(cell.f);
  return "";
}

function handleParse(id, message) {
  const raw = message.buffer;
  if (!raw) {
    postMessageSafe(errorPayload(id, "corrupt", "empty file"));
    return;
  }
  const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
  if (!bytes.length) {
    postMessageSafe(errorPayload(id, "corrupt", "empty file"));
    return;
  }
  let book;
  try {
    book = XLSX.read(bytes, {
      type: "array",
      cellFormula: true,
      cellText: true,
      cellDates: false,
      bookFiles: true,
      // Without sheetStubs, formula cells without caching are not included in the sheet at all.
      sheetStubs: true,
    });
  } catch (error) {
    postMessageSafe(errorPayload(id, classifyError(error), error));
    return;
  }
  if (!book || !Array.isArray(book.SheetNames)) {
    postMessageSafe(errorPayload(id, "corrupt", "workbook has no sheets"));
    return;
  }
  workbook = book;
  const sheets = book.SheetNames.map(function (name, index) {
    const sheet = book.Sheets[name];
    const bounds = boundsOf(sheet);
    return {
      index: index,
      name: String(name),
      rowStart: bounds.rowStart,
      colStart: bounds.colStart,
      rows: bounds.rows,
      cols: bounds.cols,
      totalRows: bounds.totalRows,
      totalCols: bounds.totalCols,
      truncated: bounds.truncated,
    };
  });
  const parts = {};
  const media = {};
  let mediaBytes = 0;
  let partBytes = 0;
  let chartLimit = false;
  for (const path of Object.keys(book.files || {})) {
    if (/^xl\/media\//.test(path)) {
      const bytes = book.files[path].content;
      if (!bytes || typeof bytes === "string") continue;
      let mime = "";
      if (bytes[0] === 137 && bytes[1] === 80 && bytes[2] === 78 && bytes[3] === 71) mime = "image/png";
      else if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) mime = "image/jpeg";
      else if (bytes[0] === 71 && bytes[1] === 73 && bytes[2] === 70) mime = "image/gif";
      else if (bytes[0] === 82 && bytes[1] === 73 && bytes[2] === 70 && bytes[3] === 70 && bytes[8] === 87 && bytes[9] === 69 && bytes[10] === 66 && bytes[11] === 80) mime = "image/webp";
      if (!mime || bytes.length > 2 * 1024 * 1024 || mediaBytes + bytes.length > 8 * 1024 * 1024 || Object.keys(media).length >= 16) {
        chartLimit = true;
        continue;
      }
      mediaBytes += bytes.length;
      media[path] = { mime: mime, bytes: new Uint8Array(bytes) };
      continue;
    }
    if (!/^xl\/(workbook\.xml|.*\.rels|charts\/[^/]+\.xml)$/.test(path)) continue;
    const content = book.files[path].content;
    if (!content) continue;
    if (content.length > 512 * 1024 || partBytes + content.length > 4 * 1024 * 1024) {
      chartLimit = true;
      continue;
    }
    partBytes += content.length;
    parts[path] = typeof content === "string" ? content : new TextDecoder("utf-8").decode(content);
  }
  postMessageSafe({ type: "parsed", id: id, sheets: sheets, chartParts: parts, chartMedia: media, chartLimit: chartLimit });
}

function handleSheet(id, message) {
  if (!workbook) {
    postMessageSafe(errorPayload(id, "corrupt", "workbook is not loaded"));
    return;
  }
  const names = workbook.SheetNames || [];
  const index = Number(message.index);
  const name = names[index];
  if (name === undefined) {
    postMessageSafe(errorPayload(id, "internal", "sheet not found"));
    return;
  }
  const sheet = workbook.Sheets[name];
  const bounds = boundsOf(sheet);
  const requestedStart = Number(message.start);
  const requestedEnd = Number(message.end);
  let start = Number.isFinite(requestedStart) ? Math.max(0, Math.floor(requestedStart)) : 0;
  let end = Number.isFinite(requestedEnd) ? Math.floor(requestedEnd) : start + MAX_PAGE_ROWS - 1;
  if (bounds.rows === 0 || bounds.cols === 0) {
    postMessageSafe({ type: "sheet", id: id, index: index, start: 0, end: -1, rows: [] });
    return;
  }
  start = Math.min(start, bounds.rows - 1);
  end = Math.min(Math.max(end, start), bounds.rows - 1, start + MAX_PAGE_ROWS - 1);
  const rows = [];
  for (let rowOffset = start; rowOffset <= end; rowOffset += 1) {
    const r = bounds.rowStart + rowOffset;
    const row = new Array(bounds.cols);
    for (let col = 0; col < bounds.cols; col += 1) {
      const c = bounds.colStart + col;
      row[col] = cellText(sheet[XLSX.utils.encode_cell({ r: r, c: c })]);
    }
    rows.push(row);
  }
  postMessageSafe({ type: "sheet", id: id, index: index, start: start, end: end, rows: rows });
}

function handleChartValues(id, message) {
  const reference = String(message.reference || "");
  const match = /^(?:'((?:[^']|'')+)'|([^'!]+))!([A-Z]+\$?\d+|\$[A-Z]+\$?\d+)(?::(\$?[A-Z]+\$?\d+))?$/i.exec(reference);
  if (!workbook || !match) {
    postMessageSafe({ type: "chartValues", id: id, values: null });
    return;
  }
  const sheet = workbook.Sheets[match[1] ? match[1].replace(/''/g, "'") : match[2]];
  if (!sheet) {
    postMessageSafe({ type: "chartValues", id: id, values: null });
    return;
  }
  const range = XLSX.utils.decode_range((match[3] + (match[4] ? ":" + match[4] : "")).replace(/\$/g, ""));
  if (range.e.r < range.s.r || range.e.c < range.s.c ||
      (range.e.r !== range.s.r && range.e.c !== range.s.c)) {
    postMessageSafe({ type: "chartValues", id: id, values: null });
    return;
  }
  const values = [];
  const count = range.e.r - range.s.r + range.e.c - range.s.c + 1;
  for (let i = 0; i < Math.min(count, 1000); i += 1) {
    const cell = sheet[XLSX.utils.encode_cell({
      r: range.s.r + (range.e.r !== range.s.r ? i : 0),
      c: range.s.c + (range.e.c !== range.s.c ? i : 0),
    })];
    values.push({ value: cell && cell.t !== "z" && typeof cell.v === "number" && Number.isFinite(cell.v) ? cell.v : null,
      text: cellText(cell) });
  }
  postMessageSafe({ type: "chartValues", id: id, values: values, truncated: count > 1000 });
}

self.onmessage = function (event) {
  const message = (event && event.data) || {};
  const id = message.id;
  try {
    if (message.type === "parse") { handleParse(id, message); return; }
    if (message.type === "chartValues") { handleChartValues(id, message); return; }
    if (message.type === "sheet") { handleSheet(id, message); return; }
    if (message.type === "dispose") { workbook = null; self.close(); return; }
    postMessageSafe(errorPayload(id, "internal", "unknown request type"));
  } catch (error) {
    postMessageSafe(errorPayload(id, classifyError(error), error));
  }
};
