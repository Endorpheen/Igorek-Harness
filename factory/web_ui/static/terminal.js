/* Terminal tab client: xterm.js and a same-origin terminal WebSocket.
 *
 * Structural rules:
 * - No external requests: load xterm.js/addon-fit from same-origin /vendor,
 *   only when the terminal tab is first opened.
 * - Use DOM APIs and textContent, not innerHTML; retain the interface CSP.
 * - Encode keyboard bytes as base64 for the PTY. Resize/ack control messages
 *   have separate branches and are never mixed with input.
 * - Acknowledge output only after xterm has drawn the chunk. This provides
 *   backpressure: the server sends more output after credit is returned.
 * - Keep the last acknowledged seq in sessionStorage so browser reload can
 *   restore the screen instead of starting from an empty terminal.
 */
(function (root) {
  "use strict";

  var XTERM_SRC = "/vendor/xterm.js";
  var FIT_SRC = "/vendor/addon-fit.js";
  var XTERM_CSS_ID = "igorek-xterm-css";
  var TICKET_PATH = "/api/sessions/";
  var WS_PATH = "/api/terminal";
  var STORAGE_PREFIX = "igorek-web:term-seq:";
  var RECONNECT_MIN_MS = 800;
  var RECONNECT_MAX_MS = 8000;

  var loaded = { xterm: null, fit: null };
  var runtime = null;

  function withStyleNonce(callback) {
    var meta = document.querySelector('meta[name="igorek-style-nonce"]');
    if (!meta) return callback();
    var create = document.createElement;
    document.createElement = function (name, options) {
      var element = create.call(document, name, options);
      if (String(name).toLowerCase() === "style") element.nonce = meta.content;
      return element;
    };
    try { return callback(); } finally { document.createElement = create; }
  }

  function t(key, params) {
    if (root.IgorekI18n && typeof root.IgorekI18n.t === "function") {
      return root.IgorekI18n.t(key, params);
    }
    return key;
  }

  function reasonText(reason) {
    var key = "terminal.reason." + String(reason || "unknown");
    var text = t(key);
    return text === key ? String(reason || "") : text;
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var element = document.createElement("script");
      element.src = src;
      element.async = false;
      element.onload = function () { resolve(); };
      element.onerror = function () { reject(new Error("load_failed:" + src)); };
      document.head.appendChild(element);
    });
  }

  function ensureStyles() {
    // xterm.css is included in index.html; a safeguard in case of manual build —
    // styles are added once and only from their own origin.
    if (document.getElementById(XTERM_CSS_ID)) return;
    var link = document.createElement("link");
    link.id = XTERM_CSS_ID;
    link.rel = "stylesheet";
    link.href = "/vendor/xterm.css";
    document.head.appendChild(link);
  }

  function ensureXterm() {
    if (loaded.xterm) return loaded.xterm;
    loaded.xterm = loadScript(XTERM_SRC).then(function () {
      if (!root.Terminal) throw new Error("xterm_missing");
      return root.Terminal;
    });
    return loaded.xterm;
  }

  function ensureFit() {
    if (loaded.fit) return loaded.fit;
    loaded.fit = loadScript(FIT_SRC).then(function () {
      return root.FitAddon && root.FitAddon.FitAddon ? root.FitAddon.FitAddon : null;
    });
    return loaded.fit;
  }

  function encodeBytes(text) {
    var bytes = new TextEncoder().encode(text);
    var binary = "";
    for (var index = 0; index < bytes.length; index += 1) {
      binary += String.fromCharCode(bytes[index]);
    }
    return btoa(binary);
  }

  function decodeBytes(base64) {
    var binary = atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (var index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }

  function storageKey(sessionId) {
    return STORAGE_PREFIX + sessionId;
  }

  function readSeq(sessionId) {
    try {
      var raw = root.sessionStorage ? root.sessionStorage.getItem(storageKey(sessionId)) : null;
      var value = raw === null ? 0 : parseInt(raw, 10);
      return Number.isFinite(value) && value >= 0 ? value : 0;
    } catch (error) {
      return 0;
    }
  }

  function writeSeq(sessionId, seq) {
    try {
      if (root.sessionStorage) root.sessionStorage.setItem(storageKey(sessionId), String(seq));
    } catch (error) {
      /* Private mode: a page reload simply starts with a blank screen. */
    }
  }

  function clearSeq(sessionId) {
    try {
      if (root.sessionStorage) root.sessionStorage.removeItem(storageKey(sessionId));
    } catch (error) {
      /* See above. */
    }
  }

  function buildShell(container, sessionId) {
    container.textContent = "";
    var box = document.createElement("div");
    box.className = "terminal-box";
    var toolbar = document.createElement("div");
    toolbar.className = "terminal-toolbar";
    var status = document.createElement("span");
    status.className = "terminal-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.textContent = t("terminal.ready");
    var restart = document.createElement("button");
    restart.type = "button";
    restart.className = "terminal-restart";
    restart.textContent = t("terminal.connect");
    restart.hidden = false;
    var stop = document.createElement("button");
    stop.type = "button";
    stop.className = "terminal-restart";
    stop.textContent = t("terminal.stop");
    stop.hidden = true;
    toolbar.append(status, restart, stop);
    var screen = document.createElement("div");
    screen.className = "terminal-screen";
    screen.setAttribute("role", "group");
    screen.setAttribute("aria-label", t("terminal.shell_aria"));
    box.append(toolbar, screen);
    container.append(box);
    return { box: box, status: status, restart: restart, stop: stop, screen: screen };
  }

  function mount(container, sessionId, hooks) {
    if (!container || !sessionId) return null;
    unmount();
    ensureStyles();
    var view = buildShell(container, sessionId);
    view.status.dataset.session = sessionId;
    var state = {
      container: container,
      sessionId: sessionId,
      view: view,
      term: null,
      fit: null,
      socket: null,
      alive: true,
      acked: 0,
      pendings: new Map(),
      reconnectTimer: null,
      reconnectDelay: RECONNECT_MIN_MS,
      observer: null,
      hooks: hooks || {},
      onDataDisposable: null,
    };
    runtime = state;
    start(state).catch(function (error) {
      setStatus(state, t("terminal.error.connect", { reason: String(error && error.message || error) }));
    });
    return state;
  }

  function setStatus(state, text) {
    if (!state.view) return;
    state.view.status.textContent = text;
  }

  function fail(state, message) {
    setStatus(state, message);
    if (state.view) state.view.restart.hidden = false;
  }

  async function start(state) {
    var Terminal = await ensureXterm();
    var FitAddon = await ensureFit();
    if (!state.alive) return;
    state.term = new Terminal({
      convertEol: false,
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      fontSize: 13,
      scrollback: 2000,
      theme: { background: "#111315", foreground: "#e8e6e1", cursor: "#a9e34b" },
      allowProposedApi: false,
    });
    if (FitAddon) {
      state.fit = new FitAddon();
      state.term.loadAddon(state.fit);
    }
    withStyleNonce(function () { state.term.open(state.view.screen); });
    state.term.parser.registerOscHandler(52, function () { return true; });
    fit(state);
    state.term.onData(function (data) {
      if (!data) return;
      var bytes = new TextEncoder().encode(data);
      for (var offset = 0; offset < bytes.length; offset += 16384) {
        var chunk = bytes.subarray(offset, offset + 16384);
        var binary = "";
        for (var index = 0; index < chunk.length; index += 1) binary += String.fromCharCode(chunk[index]);
        send(state, "input", { data: btoa(binary) });
      }
    });
    if (FitAddon && root.ResizeObserver) {
      state.observer = new ResizeObserver(function () { fit(state); });
      state.observer.observe(state.view.screen);
    }
    state.view.restart.onclick = function () {
      state.stopped = false;
      state.view.restart.hidden = true;
      connect(state);
    };
    state.view.stop.onclick = function () {
      state.stopped = true;
      send(state, "close", {});
      state.view.stop.hidden = true;
      fail(state, t("terminal.ready"));
    };
  }

  function fit(state) {
    if (!state.fit || !state.term) return;
    try {
      state.fit.fit();
    } catch (error) {
      return;
    }
    send(state, "resize", { rows: state.term.rows, cols: state.term.cols });
  }

  function send(state, kind, payload) {
    if (!state.socket || state.socket.readyState !== 1) return;
    var message = { kind: kind, seq: nextSeq(state), payload: payload };
    try {
      state.socket.send(JSON.stringify(message));
    } catch (error) {
      /* The socket closed between the check and the send operation */
    }
  }

  function nextSeq(state) {
    state.localSeq = (state.localSeq || 0) + 1;
    return state.localSeq;
  }

  function closeSocket(state) {
    if (state.socket) {
      try {
        state.socket.close();
      } catch (error) {
        /* Already closed */
      }
      state.socket = null;
    }
  }

  function connect(state) {
    if (!state.alive) return;
    closeSocket(state);
    setStatus(state, t("terminal.connecting"));
    fetchTicket(state).then(function (ticket) {
      if (!state.alive) return;
      var scheme = root.location && root.location.protocol === "https:" ? "wss://" : "ws://";
      var host = root.location ? root.location.host : "127.0.0.1";
      var socket;
      try {
        socket = new WebSocket(scheme + host + WS_PATH);
      } catch (error) {
        fail(state, t("terminal.error.connect", { reason: String(error && error.message || error) }));
        return;
      }
      state.socket = socket;
      socket.onopen = function () {
        send(state, "open", {
          ticket: ticket,
          rows: state.term ? state.term.rows : 30,
          cols: state.term ? state.term.cols : 100,
          since_seq: state.acked,
        });
      };
      socket.onmessage = function (event) { onMessage(state, event.data); };
      socket.onclose = function (event) {
        if (!state.alive) return;
        if (state.stopped || (event && event.code >= 4400)) return;
        if (event && event.code === 4401) {
          // Single-use ticket: closure via code 4401 is not a 'network failure', but an
          // expired access. Reconnection will use a new ticket.
          setStatus(state, t("terminal.error.ticket", { reason: reasonText("ticket_invalid") }));
        }
        scheduleReconnect(state);
      };
      socket.onerror = function () {
        /* Handling occurs in the onclose event */
      };
    }).catch(function (error) {
      fail(state, t("terminal.error.ticket", { reason: String(error && error.message || error) }));
    });
  }

  function fetchTicket(state) {
    var url = TICKET_PATH + encodeURIComponent(state.sessionId) + "/terminal";
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) throw new Error(body && body.error ? body.error : "http_" + response.status);
        if (!body || typeof body.ticket !== "string") throw new Error("ticket_missing");
        state.executionMode = body.execution_mode;
        return body.ticket;
      });
    });
  }

  function scheduleReconnect(state) {
    if (!state.alive || state.reconnectTimer) return;
    var delay = state.reconnectDelay;
    state.reconnectDelay = Math.min(RECONNECT_MAX_MS, Math.round(delay * 1.8));
    state.reconnectTimer = setTimeout(function () {
      state.reconnectTimer = null;
      connect(state);
    }, delay);
  }

  function onMessage(state, raw) {
    var message;
    try {
      message = JSON.parse(raw);
    } catch (error) {
      return;
    }
    if (!message || typeof message.kind !== "string") return;
    var payload = message.payload || {};
    if (message.kind === "opened") {
      state.view.stop.hidden = false;
      if (state.terminalId !== payload.terminal_id) {
        state.terminalId = payload.terminal_id;
        state.acked = 0;
        if (state.term) state.term.reset();
      }
      state.reconnectDelay = RECONNECT_MIN_MS;
      setStatus(state, t(state.executionMode === "kvm" ? "terminal.connected_kvm" : "terminal.connected"));
      return;
    }
    if (message.kind === "output") {
      var bytes;
      try {
        bytes = decodeBytes(String(payload.data || ""));
      } catch (error) {
        return;
      }
      var seq = typeof message.seq === "number" ? message.seq : 0;
      if (seq && seq <= state.acked) return; // Already rendered in a previous life
      if (!state.term) return;
      state.term.write(bytes, function () {
        // Confirmation is sent after rendering: this is server credit.
        if (seq > state.acked) {
          state.acked = seq;
          writeSeq(state.sessionId, seq);
        }
        send(state, "ack", { seq: state.acked });
      });
      return;
    }
    if (message.kind === "exit") {
      state.stopped = true;
      state.view.restart.textContent = t("terminal.restart");
      var code = typeof payload.code === "number" ? payload.code : -1;
      fail(state, t("terminal.exited", { code: String(code) }));
      return;
    }
    if (message.kind === "error") {
      state.stopped = true;
      fail(state, t("terminal.error.connect", { reason: reasonText(payload.reason) }));
    }
  }

  function unmount() {
    var state = runtime;
    runtime = null;
    if (!state) return;
    state.alive = false;
    if (state.reconnectTimer) {
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
    }
    if (state.observer) {
      try {
        state.observer.disconnect();
      } catch (error) {
        /* ignore */
      }
      state.observer = null;
    }
    closeSocket(state);
    if (state.term) {
      try {
        state.term.dispose();
      } catch (error) {
        /* ignore */
      }
      state.term = null;
    }
    if (state.view && state.view.container) state.view.container.textContent = "";
  }

  function current() {
    return runtime;
  }

  var api = {
    mount: mount,
    unmount: unmount,
    current: current,
    readSeq: readSeq,
    writeSeq: writeSeq,
    clearSeq: clearSeq,
    encodeBytes: encodeBytes,
    decodeBytes: decodeBytes,
    paths: { xterm: XTERM_SRC, fit: FIT_SRC, ws: WS_PATH },
  };
  root.IgorekTerminal = api;
  if (typeof module !== "undefined" && module && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
