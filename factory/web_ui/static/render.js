"use strict";
/* Local Markdown rendering for Igorek Web UI agent responses.
 *
 * CSP uses `script-src 'self'`, so CDN Markdown libraries cannot be loaded.
 * Build nodes through DOM APIs (`createElement`/`textContent`), not innerHTML:
 * angle brackets in an agent answer must remain text rather than executable
 * markup.
 *
 * Blocks: fenced code, headings, nested bullet/numbered lists, quotes, tables,
 * horizontal rules and paragraphs. Inline: `code`, **bold**, *italic*,
 * ~~strikethrough~~ and [link](url).
 */
(function (global) {
  const FENCE = /^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$/;
  const HEADING = /^(#{1,6})\s+(.*)$/;
  const BULLET = /^(\s*)([-*+])\s+(.*)$/;
  const ORDERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
  const QUOTE = /^\s*>\s?(.*)$/;
  const RULE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
  const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  const SAFE_LINK = /^(?:https?:\/\/|mailto:|#|\/)/i;

  function el(tag, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function textEl(tag, className, text) {
    const node = el(tag, className);
    node.textContent = text == null ? "" : String(text);
    return node;
  }

  function appendInline(target, text) {
    /* Single pass through the string: code, links, highlights. Rules are scanned
     * left-to-right, so unclosed markup remains plain text. */
    let index = 0;
    let plain = "";
    const flush = () => {
      if (plain) {
        target.append(document.createTextNode(plain));
        plain = "";
      }
    };
    while (index < text.length) {
      const char = text[index];
      if (char === "`") {
        const end = text.indexOf("`", index + 1);
        if (end > index + 1) {
          flush();
          const code = el("code", "md-code-inline");
          code.textContent = text.slice(index + 1, end);
          target.append(code);
          index = end + 1;
          continue;
        }
      }
      if (char === "[") {
        const labelEnd = text.indexOf("](", index);
        if (labelEnd > -1) {
          const urlEnd = text.indexOf(")", labelEnd + 2);
          if (urlEnd > -1) {
            const url = text.slice(labelEnd + 2, urlEnd).trim();
            const internal = /^igorek:\/\/ui\/([a-z][a-z0-9-]*)$/.exec(url);
            if (internal) {
              flush();
              const candidate = el('span');
              candidate.dataset.uiRoute = internal[1];
              candidate.textContent = text.slice(index + 1, labelEnd);
              target.append(candidate);
              index = urlEnd + 1;
              continue;
            }
            if (SAFE_LINK.test(url)) {
              flush();
              const link = el("a", "md-link");
              link.href = url;
              link.title = link.href;
              link.rel = "noopener noreferrer";
              link.target = "_blank";
              appendInline(link, text.slice(index + 1, labelEnd));
              target.append(link);
              index = urlEnd + 1;
              continue;
            }
          }
        }
      }
      const strong = text.startsWith("**", index) || text.startsWith("__", index);
      if (strong) {
        const marker = text.slice(index, index + 2);
        const end = text.indexOf(marker, index + 2);
        if (end > index + 2) {
          flush();
          const node = el("strong");
          appendInline(node, text.slice(index + 2, end));
          target.append(node);
          index = end + 2;
          continue;
        }
      }
      if (text.startsWith("~~", index)) {
        const end = text.indexOf("~~", index + 2);
        if (end > index + 2) {
          flush();
          const node = el("del");
          appendInline(node, text.slice(index + 2, end));
          target.append(node);
          index = end + 2;
          continue;
        }
      }
      if (char === "*" || char === "_") {
        const end = text.indexOf(char, index + 1);
        if (end > index + 1) {
          flush();
          const node = el("em");
          appendInline(node, text.slice(index + 1, end));
          target.append(node);
          index = end + 1;
          continue;
        }
      }
      plain += char;
      index += 1;
    }
    flush();
  }

  function splitRow(line) {
    let row = line.trim();
    if (row.startsWith("|")) row = row.slice(1);
    if (row.endsWith("|")) row = row.slice(0, -1);
    return row.split("|").map((cell) => cell.trim());
  }

  function uiText(key, fallback) {
    /* Interface labels are taken from the app.js dictionary if it is already loaded
     * (render.js is loaded first, so the check is mandatory); otherwise,
     * the Russian fallback version remains. */
    const i18n = global.IgorekI18n;
    if (i18n && typeof i18n.t === "function") {
      const value = i18n.t(key);
      if (value !== key) return value;
    }
    return fallback;
  }

  function buildCode(lines, language) {
    const wrapper = el("div", "md-code");
    const head = el("div", "md-code-head");
    head.append(textEl("span", "md-code-language", language || uiText("viewer.language_code", "код")));
    const copy = el("button", "md-code-copy");
    copy.type = "button";
    copy.textContent = uiText("viewer.copy", "копировать");
    head.append(copy);
    const block = el("pre", "md-pre");
    const code = el("code");
    if (language) code.className = "language-" + language;
    code.textContent = lines.join("\n");
    block.append(code);
    copy.addEventListener("click", () => {
      const text = code.textContent || "";
      const clipboard = global.navigator && global.navigator.clipboard;
      if (clipboard && clipboard.writeText) {
        clipboard.writeText(text).then(
          () => { copy.textContent = uiText("viewer.copied", "скопировано"); },
          () => { copy.textContent = uiText("viewer.copy_unavailable", "недоступно"); },
        );
      } else {
        copy.textContent = uiText("viewer.copy_unavailable", "недоступно");
      }
    });
    wrapper.append(head, block);
    return wrapper;
  }

  function buildList(lines, start) {
    let index = start;
    const stack = [];
    while (index < lines.length) {
      const line = lines[index];
      const bullet = BULLET.exec(line);
      const ordered = bullet ? null : ORDERED.exec(line);
      if (!bullet && !ordered) break;
      const match = bullet || ordered;
      const indent = match[1].replace(/\t/g, "  ").length;
      const level = Math.floor(indent / 2);
      while (stack.length - 1 > level) stack.pop();
      if (stack.length - 1 < level || stack.length === 0) {
        if (stack.length === 0) {
          const tag = bullet ? "ul" : "ol";
          const list = el(tag, "md-list");
          stack.push(list);
        } else {
          const parent = stack[stack.length - 1];
          const last = parent.lastElementChild;
          const tag = bullet ? "ul" : "ol";
          const list = el(tag, "md-list");
          stack.push(list);
          if (last) last.append(list);
          else parent.append(list);
        }
      }
      const list = stack[stack.length - 1];
      const item = el("li", "md-list-item");
      appendInline(item, match[3]);
      list.append(item);
      index += 1;
    }
    return { node: stack[0], next: index };
  }

  function buildTable(lines, start) {
    const header = splitRow(lines[start]);
    const body = [];
    let index = start + 2;
    while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
      body.push(splitRow(lines[index]));
      index += 1;
    }
    const table = el("table", "md-table");
    const thead = el("thead");
    const headRow = el("tr");
    for (const cell of header) headRow.append(textEl("th", "md-th", cell));
    thead.append(headRow);
    const tbody = el("tbody");
    for (const row of body) {
      const tr = el("tr");
      for (const cell of row) tr.append(textEl("td", "md-td", cell));
      tbody.append(tr);
    }
    table.append(thead, tbody);
    return { node: table, next: index };
  }

  function buildFragment(text) {
    const fragment = document.createDocumentFragment();
    const lines = String(text == null ? "" : text).split("\n");
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }
      const fence = FENCE.exec(line);
      if (fence) {
        const marker = fence[1][0];
        const body = [];
        index += 1;
        while (index < lines.length && !new RegExp("^\\s*" + marker + "{3,}\\s*$").test(lines[index])) {
          body.push(lines[index]);
          index += 1;
        }
        index += 1;
        fragment.append(buildCode(body, fence[2]));
        continue;
      }
      const heading = HEADING.exec(line);
      if (heading) {
        const level = Math.min(heading[1].length, 6);
        const node = el("h" + level, "md-heading");
        appendInline(node, heading[2].trim());
        fragment.append(node);
        index += 1;
        continue;
      }
      if (RULE.test(line)) {
        fragment.append(el("hr", "md-rule"));
        index += 1;
        continue;
      }
      if (QUOTE.test(line)) {
        const body = [];
        while (index < lines.length && QUOTE.test(lines[index])) {
          body.push(QUOTE.exec(lines[index])[1]);
          index += 1;
        }
        const quote = el("blockquote", "md-blockquote");
        for (const part of body) {
          const paragraph = el("p", "md-paragraph");
          appendInline(paragraph, part);
          quote.append(paragraph);
        }
        fragment.append(quote);
        continue;
      }
      if (line.includes("|") && index + 1 < lines.length && TABLE_SEPARATOR.test(lines[index + 1])) {
        const table = buildTable(lines, index);
        fragment.append(table.node);
        index = table.next;
        continue;
      }
      if (BULLET.test(line) || ORDERED.test(line)) {
        const list = buildList(lines, index);
        fragment.append(list.node);
        index = list.next;
        continue;
      }
      const paragraph = el("p", "md-paragraph");
      const buffer = [];
      while (
        index < lines.length &&
        lines[index].trim() &&
        !FENCE.test(lines[index]) &&
        !HEADING.test(lines[index]) &&
        !RULE.test(lines[index]) &&
        !QUOTE.test(lines[index]) &&
        !BULLET.test(lines[index]) &&
        !ORDERED.test(lines[index]) &&
        !(lines[index].includes("|") && index + 1 < lines.length && TABLE_SEPARATOR.test(lines[index + 1]))
      ) {
        buffer.push(lines[index]);
        index += 1;
      }
      for (let part = 0; part < buffer.length; part += 1) {
        if (part) paragraph.append(document.createElement("br"));
        appendInline(paragraph, buffer[part]);
      }
      fragment.append(paragraph);
    }
    return fragment;
  }

  function renderMarkdown(text, container) {
    if (!container) return;
    container.replaceChildren(buildFragment(text));
  }

  function activateNavigation(container, routes, navigate) {
    if (!container || typeof navigate !== 'function' || !Array.isArray(routes)) return;
    const allowed = new Set(routes.map(route => route.id));
    for (const candidate of container.querySelectorAll('span[data-ui-route]')) {
      const id = candidate.dataset.uiRoute;
      if (!allowed.has(id)) continue;
      const link = el('a', 'md-link');
      link.href = '#';
      link.dataset.uiRoute = id;
      link.textContent = candidate.textContent;
      link.addEventListener('click', event => { event.preventDefault(); navigate(id); });
      candidate.replaceWith(link);
    }
  }

  global.IgorekMarkdown = { render: renderMarkdown, renderMarkdown, fragment: buildFragment, activateNavigation };
})(window);
