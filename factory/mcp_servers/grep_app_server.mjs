#!/usr/bin/env node
// allow: SIZE_OK — self-contained stdio MCP server mirroring the single-file
// convention of the other factory MCP servers (coingecko/xsearch/tavily).
/**
 * Local stdio MCP server for grep.app code search (Node).
 *
 * Why a bridge instead of calling the search API directly: grep.app's public
 * search API (https://grep.app/api/search) sits behind a Vercel Security
 * Checkpoint that answers HTTP 429 to every non-browser client from this host.
 * Verified 2026-09-21: Node global fetch, node:http2, and curl (browser UA,
 * --http2) all get 429. The official hosted MCP at https://mcp.grep.app is
 * reachable from Node over streamable HTTP and returns real GitHub hits, so
 * this server exposes one stdio tool (`search_github`) and proxies it to the
 * official MCP's `searchGitHub` tool. The cc-backend MCP client is stdio-only,
 * hence the local stdio wrapper.
 */

import process from "node:process";

const SERVER_NAME = "grep_app";
const SERVER_VERSION = "0.2.1";
const PROTOCOL_VERSION = "2024-11-05";
const UPSTREAM_URL = (process.env.GREP_APP_MCP_URL || "").trim() || "https://mcp.grep.app";
const UPSTREAM_TOOL = (process.env.GREP_APP_UPSTREAM_TOOL || "").trim() || "searchGitHub";
const UPSTREAM_TIMEOUT_MS = Math.max(
  1000,
  Number.parseInt(process.env.GREP_APP_TIMEOUT_MS || "", 10) || 30000,
);

const HEADER = [
  "Ниже реальные результаты поиска по коду из публичных GitHub-репозиториев (grep.app).",
  "Не выдумывай результаты и не делай общую сводку. Цитируй только то, что реально показано ниже.",
  "Для каждого результата покажи: репозиторий, путь, язык (если известен), строки кода с номерами и URL.",
  "URL_COPY_CONTRACT: для каждого HIT обязательно скопируй строку `URL:` полностью и дословно.",
  "Не заменяй URL на `[ссылка]`, `ссылка`, `link`, `тут`, markdown без href или любой сокращённый текст.",
  "Если URL unavailable, так и напиши `URL: unavailable`.",
].join(" ");

function toolDefinitions() {
  return [
    {
      name: "search_github",
      description:
        "Ищет по коду в публичных GitHub-репозиториях через grep.app. " +
        "Используй, чтобы найти реальные примеры кода, сигнатуры функций и паттерны " +
        "использования в чужих проектах. Ищи литеральные фрагменты кода (useState(, " +
        "requests.get), а не ключевые слова.",
      inputSchema: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description: "Литеральный фрагмент кода, например: requests.get или useState(",
          },
          language: {
            description: "Фильтр по языку: строка ('Python') или массив (['TypeScript','TSX'])",
            anyOf: [
              { type: "string" },
              { type: "array", items: { type: "string" } },
            ],
          },
          repo: {
            type: "string",
            description: "Фильтр по репозиторию owner/name (можно частично: 'vercel/')",
          },
          path: {
            type: "string",
            description: "Фильтр по пути внутри репозитория, например: src/components/",
          },
          page: {
            type: "integer",
            description:
              "Принято для совместимости, но игнорируется: официальный grep.app MCP " +
              "не поддерживает пагинацию.",
          },
        },
        required: ["query"],
        additionalProperties: false,
      },
    },
  ];
}

function send(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

function makeResult(id, result) {
  return { jsonrpc: "2.0", id, result };
}

function makeError(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function parseSseMessages(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("{")) {
    try {
      return [JSON.parse(trimmed)];
    } catch {
      // fall through to SSE parsing
    }
  }
  const messages = [];
  for (const block of trimmed.split(/\r?\n\r?\n/)) {
    const dataLines = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim());
    if (!dataLines.length) continue;
    const payload = dataLines.join("\n");
    if (!payload) continue;
    try {
      messages.push(JSON.parse(payload));
    } catch {
      // ignore keepalive / non-JSON events
    }
  }
  return messages;
}

async function upstreamPost(body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const response = await fetch(UPSTREAM_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`upstream HTTP ${response.status}: ${text.slice(0, 200)}`);
    }
    return parseSseMessages(text);
  } finally {
    clearTimeout(timer);
  }
}

let requestCounter = 1;
function nextUpstreamId() {
  requestCounter += 1;
  return requestCounter;
}

let upstreamReady = null;
async function ensureUpstream() {
  if (!upstreamReady) {
    upstreamReady = (async () => {
      await upstreamPost({
        jsonrpc: "2.0",
        id: nextUpstreamId(),
        method: "initialize",
        params: {
          protocolVersion: PROTOCOL_VERSION,
          capabilities: {},
          clientInfo: { name: SERVER_NAME, version: SERVER_VERSION },
        },
      });
      await upstreamPost({ jsonrpc: "2.0", method: "notifications/initialized", params: {} });
    })().catch((error) => {
      upstreamReady = null;
      throw error;
    });
  }
  return upstreamReady;
}

function normalizeArguments(raw) {
  const args = raw && typeof raw === "object" ? raw : {};
  const query = String(args.query ?? "").trim();
  if (!query) throw new Error("Поле query обязательно");
  const upstream = { query };
  const language = args.language;
  if (typeof language === "string" && language.trim()) {
    upstream.language = [language.trim()];
  } else if (Array.isArray(language)) {
    const items = language.map((item) => String(item).trim()).filter(Boolean);
    if (items.length) upstream.language = items;
  }
  for (const key of ["repo", "path"]) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) upstream[key] = value.trim();
  }
  for (const key of ["matchCase", "matchWholeWords", "useRegexp"]) {
    if (typeof args[key] === "boolean") upstream[key] = args[key];
  }
  return upstream;
}

async function handleToolCall(arguments_) {
  const upstreamArgs = normalizeArguments(arguments_);
  await ensureUpstream();
  const messages = await upstreamPost({
    jsonrpc: "2.0",
    id: nextUpstreamId(),
    method: "tools/call",
    params: { name: UPSTREAM_TOOL, arguments: upstreamArgs },
  });
  const message = messages.find((item) => item && (item.result || item.error)) || messages[0];
  if (!message) throw new Error("grep.app MCP вернул пустой ответ");
  if (message.error) throw new Error(message.error.message || "grep.app MCP error");
  const content = message.result && Array.isArray(message.result.content) ? message.result.content : [];
  const body = content
    .filter((part) => part && part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n\n");
  if (message.result?.isError === true) {
    return {
      content: [{ type: "text", text: body.trim() || "grep.app MCP returned a tool error" }],
      isError: true,
    };
  }
  const text = body.trim() ? `${HEADER}\n\n${body}` : `${HEADER}\n\nПо запросу результатов не найдено.`;
  return { content: [{ type: "text", text }] };
}

async function dispatch(request) {
  const method = request.method;
  const id = request.id;
  const params = request.params || {};

  if (method === "initialize") {
    return makeResult(id, {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: {} },
      serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
    });
  }
  if (method === "notifications/initialized" || method === "notifications/cancelled") {
    return null;
  }
  if (method === "ping") {
    return makeResult(id, {});
  }
  if (method === "tools/list") {
    return makeResult(id, { tools: toolDefinitions() });
  }
  if (method === "tools/call") {
    const toolName = params.name;
    if (toolName !== "search_github") {
      return makeError(id, -32602, `Неизвестный инструмент: ${toolName}`);
    }
    try {
      return makeResult(id, await handleToolCall(params.arguments || {}));
    } catch (error) {
      return makeResult(id, {
        content: [{ type: "text", text: `Ошибка поиска grep.app: ${error.message}` }],
        isError: true,
      });
    }
  }
  if (id === undefined || id === null) {
    return null;
  }
  return makeError(id, -32601, `Метод не поддерживается: ${method}`);
}

let chain = Promise.resolve();
function enqueue(line) {
  chain = chain.then(async () => {
    let request;
    try {
      request = JSON.parse(line);
    } catch {
      send(makeError(null, -32700, "Некорректный JSON"));
      return;
    }
    try {
      const response = await dispatch(request);
      if (response) send(response);
    } catch (error) {
      send(makeError(request.id ?? null, -32000, String(error && error.message ? error.message : error)));
    }
  });
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (line) enqueue(line);
  }
});
process.stdin.on("end", () => {
  const line = buffer.trim();
  if (line) enqueue(line);
});
