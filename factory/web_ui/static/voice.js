// Igor's voice call: WebRTC media streams directly to OpenAI Realtime, while
// signaling (SDP) and short-lived authentication data are provided by our own server.
// The module is self-contained and CSP-safe: no innerHTML/eval usage; all text is processed via textContent;
// all requests are same-origin with cookies. Interface texts are stored in app.js and fetched via window.IgorekI18n.
(() => {
  "use strict";

  const ICE_TIMEOUT_MS = 2000;
  const RECONNECT_WAIT_MS = 6000;
  const FAILURE_GRACE_MS = 600;
  const VOICE_SIZE_KEY = "igorek-web:voice-size";
  const VOICE_MIN_W = 280;
  const VOICE_MIN_H = 220;
  const ACTIVE_PHASES = ["connecting", "listening", "thinking", "reconnecting"];

  const voice = {
    initialized: false,
    config: null,
    configCheckedAt: 0,
    active: false,
    starting: false,
    callId: "",
    sessionId: "",
    tier: "mini",
    expiresAt: 0,
    phase: "idle",
    notice: "",
    pc: null,
    dc: null,
    micStream: null,
    micTrack: null,
    audioEl: null,
    audioBlocked: false,
    micMuted: false,
    speakerMuted: false,
    responseActive: false,
    responseRequested: false,
    pending: new Map(),
    approval: null,
    approvalBusy: false,
    minimized: false,
    iceRestartUsed: false,
    fullRestartUsed: false,
    reconnecting: false,
    restarting: false,
    exchangeBusy: false,
    generation: 0,
    lastErrorMessage: "",
    assistantSeen: new Set(),
  };
  const dom = {};

  // -------------------------------------------------------------------------
  // Utility helpers: DOM manipulation, fetch requests, identifiers, localization.
  // -------------------------------------------------------------------------
  const byId = (id) => document.getElementById(id);
  const tr = (key, params) => {
    const api = window.IgorekI18n;
    if (api && typeof api.t === "function") return api.t(key, params);
    return key;
  };
  const encodeId = (value) => encodeURIComponent(String(value));
  const makeEl = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const trimMessage = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 300);
  // Call expired: cookie expired (401/410) or server forgot the call_id (404 call_not_found).
  // In both cases, a new /api/voice/session request resolves the issue.
  const isCallExpired = (error) => {
    const status = Number((error && error.status) || 0);
    return status === 401 || status === 410 || status === 404;
  };

  function randomId() {
    const cryptoApi = window.crypto;
    if (cryptoApi && typeof cryptoApi.randomUUID === "function") return cryptoApi.randomUUID();
    const bytes = new Uint8Array(16);
    if (cryptoApi && typeof cryptoApi.getRandomValues === "function") cryptoApi.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  async function requestJson(path, options = {}) {
    const hasBody = options.body !== undefined;
    const response = await fetch(path, {
      method: options.method || (hasBody ? "POST" : "GET"),
      credentials: "same-origin",
      cache: "no-store",
      headers: hasBody ? { "Content-Type": "application/json" } : {},
      body: hasBody ? JSON.stringify(options.body) : undefined,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || ("HTTP " + response.status));
      error.status = response.status;
      error.code = payload.code ? String(payload.code) : "";
      throw error;
    }
    return payload;
  }
  const postJson = (path, body) => requestJson(path, { body });

  // -------------------------------------------------------------------------
  // Initialization: DOM caching, event handlers, configuration. Called once.
  // -------------------------------------------------------------------------
  function init() {
    if (voice.initialized) return;
    voice.initialized = true;
    dom.button = byId("voice-button");
    dom.panel = byId("voice-panel");
    if (!dom.button || !dom.panel) return;
    dom.status = byId("voice-status");
    dom.dot = byId("voice-dot");
    dom.transcript = byId("voice-transcript");
    dom.task = byId("voice-task");
    dom.mic = byId("voice-mic");
    dom.speaker = byId("voice-speaker");
    dom.minimize = byId("voice-minimize");
    dom.hangup = byId("voice-hangup");

    dom.button.addEventListener("click", () => { onButtonClick().catch(() => {}); });
    dom.hangup.addEventListener("click", () => { hangup().catch(() => {}); });
    dom.minimize.addEventListener("click", toggleMinimize);
    dom.mic.addEventListener("click", toggleMic);
    dom.speaker.addEventListener("click", toggleSpeaker);
    dom.resize = byId("voice-resize");
    if (dom.resize) dom.resize.addEventListener("pointerdown", startVoiceResize);
    window.addEventListener("resize", () => { if (!voice.minimized) applyStoredSize(); });

    // Language change: app.js rewrites static labels and updates the lang attribute of <html>.
    // We catch this to ensure the dynamic panel status doesn’t remain in the old language.
    if (typeof MutationObserver === "function") {
      new MutationObserver(() => { renderPhase(); renderTask(); }).observe(
        document.documentElement, { attributes: true, attributeFilter: ["lang"] },
      );
    }
    loadConfig().catch(() => {});
  }

  async function loadConfig() {
    voice.configCheckedAt = Date.now();
    let payload = null;
    try {
      payload = await requestJson("/api/voice/config");
    } catch (error) {
      payload = null;
    }
    voice.config = payload;
    const enabled = Boolean(payload && payload.enabled);
    if (dom.button) dom.button.hidden = false;
    if (enabled) {
      const tier = String(payload.tier || "");
      if (tier === "mini" || tier === "full") voice.tier = tier;
      if (Array.isArray(payload.tiers) && payload.tiers.length && !payload.tiers.includes(voice.tier)) {
        voice.tier = String(payload.tiers[0]);
      }
    }
    return payload;
  }

  // The page may have opened before pairing: when the configuration is off, we attempt to retry
  // once again, but no more than once every 10 seconds, to avoid hammering the /api/voice/config endpoint.
  function refreshConfig() {
    if (voice.config && voice.config.enabled) return;
    if (Date.now() - voice.configCheckedAt < 10000) return;
    loadConfig().catch(() => {});
  }

  function currentSessionId() {
    if (voice.sessionId) return voice.sessionId;
    // Soft fallback: if the `onSessionChanged` hook hasn't been triggered yet, attempt to retrieve
    // the selected session from app.js. Access errors should not break the call.
    try {
      if (typeof state !== "undefined" && state && state.selected) return String(state.selected);
    } catch (error) { /* Unavailable state — no problem */ }
    return "";
  }

  async function onButtonClick() {
    if (voice.active || voice.starting) {
      openPanel();
      if (voice.minimized) toggleMinimize();
      return;
    }
    await loadConfig().catch(() => {});
    if (!voice.config || !voice.config.enabled) {
      openPanel();
      setPhase("error", tr(voice.config && voice.config.reason === "no_key" ? "voice.error.key_setup" : "voice.error.unavailable"));
      if (dom.transcript) dom.transcript.textContent = voice.notice;
      return;
    }
    // A new user call grants the right for one automatic restart.
    voice.fullRestartUsed = false;
    await start();
  }

  // -------------------------------
  // Panel and phases.
  // ------------------------
  function openPanel() {
    if (!dom.panel) return;
    dom.panel.hidden = false;
    applyStoredSize();
  }

  function closePanel() {
    if (dom.panel) {
      dom.panel.hidden = true;
      dom.panel.classList.remove("minimized");
      dom.panel.style.width = "";
      dom.panel.style.height = "";
    }
    voice.minimized = false;
    if (dom.minimize) dom.minimize.setAttribute("aria-expanded", "true");
  }

  function toggleMinimize() {
    voice.minimized = !voice.minimized;
    if (dom.panel) {
      dom.panel.classList.toggle("minimized", voice.minimized);
      if (voice.minimized) {
        dom.panel.style.width = "";
        dom.panel.style.height = "";
      } else {
        applyStoredSize();
      }
    }
    if (dom.minimize) {
      dom.minimize.setAttribute("aria-expanded", String(!voice.minimized));
      dom.minimize.setAttribute("aria-label", tr(voice.minimized ? "voice.restore_aria" : "voice.minimize_aria"));
    }
    return voice.minimized;
  }

  // The panel is attached to the bottom-right corner, so we drag from the top-left corner:
  // moving left/up increases the size. Size is stored in localStorage.
  function voiceLimits() {
    return {
      minW: VOICE_MIN_W,
      minH: VOICE_MIN_H,
      maxW: Math.max(VOICE_MIN_W, window.innerWidth - 24),
      maxH: Math.max(VOICE_MIN_H, window.innerHeight - 24),
    };
  }

  function storedVoiceSize() {
    try {
      const raw = window.localStorage.getItem(VOICE_SIZE_KEY);
      if (!raw) return null;
      const value = JSON.parse(raw);
      if (value && Number.isFinite(value.w) && Number.isFinite(value.h)) return value;
    } catch (error) { /* Local storage unavailable — simply do not save */ }
    return null;
  }

  function applyVoiceSize(w, h) {
    if (!dom.panel) return;
    const limit = voiceLimits();
    const width = Math.min(Math.max(Number(w), limit.minW), limit.maxW);
    const height = Math.min(Math.max(Number(h), limit.minH), limit.maxH);
    dom.panel.style.width = width + "px";
    dom.panel.style.height = height + "px";
  }

  function applyStoredSize() {
    if (!dom.panel) return;
    if (window.innerWidth <= 680) {
      dom.panel.style.width = "";
      dom.panel.style.height = "";
      return;
    }
    const size = storedVoiceSize();
    if (size) applyVoiceSize(size.w, size.h);
  }

  function startVoiceResize(event) {
    if (!dom.panel || voice.minimized) return;
    event.preventDefault();
    const rect = dom.panel.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const startW = rect.width;
    const startH = rect.height;
    const limit = voiceLimits();
    const onMove = (move) => {
      const width = Math.min(Math.max(startW + (startX - move.clientX), limit.minW), limit.maxW);
      const height = Math.min(Math.max(startH + (startY - move.clientY), limit.minH), limit.maxH);
      dom.panel.style.width = width + "px";
      dom.panel.style.height = height + "px";
    };
    const onUp = () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("pointercancel", onUp);
      document.body.style.userSelect = "";
      try {
        const finalRect = dom.panel.getBoundingClientRect();
        window.localStorage.setItem(VOICE_SIZE_KEY, JSON.stringify({
          w: Math.round(finalRect.width), h: Math.round(finalRect.height),
        }));
      } catch (error) { /* No saving */ }
    };
    document.body.style.userSelect = "none";
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
  }

  function setPhase(phase, notice) {
    voice.phase = phase;
    voice.notice = notice ? String(notice) : "";
    renderPhase();
  }

  function setNotice(text) {
    voice.notice = text ? String(text) : "";
    renderPhase();
  }

  function phaseText() {
    switch (voice.phase) {
      case "connecting": return tr("voice.status.connecting");
      case "listening": return tr("voice.status.listening");
      case "thinking": return tr("voice.status.thinking");
      case "reconnecting": return tr("voice.status.reconnecting");
      case "error": return voice.lastErrorMessage || tr("voice.status.error");
      default: return tr("voice.status.idle");
    }
  }

  function renderPhase() {
    if (dom.status) dom.status.textContent = voice.notice || phaseText();
    if (dom.dot) {
      dom.dot.classList.toggle("active", ACTIVE_PHASES.includes(voice.phase));
      dom.dot.classList.toggle("alert", voice.phase === "error");
    }
  }

  // -------------------------------------------------------------------------
  // Call initiation: session → microphone → peer → SDP exchange via our server.
  // -------------------------------------------------------------------------
  async function start() {
    if (voice.active || voice.starting) return;
    if (!voice.config || !voice.config.enabled) return;
    voice.starting = true;
    const gene = voice.generation + 1;
    voice.generation = gene;
    const stale = () => voice.generation !== gene;
    let retryAuth = false;
    try {
      const sessionId = currentSessionId();
      if (!sessionId) {
        openPanel();
        setPhase("error", tr("voice.error.no_session"));
        return;
      }
      voice.sessionId = sessionId;
      voice.callId = "";
      voice.iceRestartUsed = false;
      voice.audioBlocked = false;
      voice.responseActive = false;
      voice.responseRequested = false;
      voice.responseQueued = false;
      voice.lastErrorMessage = "";
      resetTranscript();
      renderTask();
      renderControls();
      openPanel();
      setPhase("connecting");

      const call = await postJson("/api/voice/session", { session_id: sessionId, tier: voice.tier });
      if (stale()) return;
      voice.callId = String(call.call_id || "");
      voice.expiresAt = Number(call.expires_at || 0);
      if (!voice.callId) {
        const error = new Error("empty call_id");
        error.status = 500;
        throw error;
      }

      const established = await establishPeer(gene);
      if (!established || stale()) return;
      voice.active = true;
      if (voice.audioBlocked) setPhase("error", tr("voice.error.audio"));
      else setPhase("listening");
      renderControls();
    } catch (error) {
      if (stale()) return;
      retryAuth = isCallExpired(error) && !voice.fullRestartUsed;
      if (!retryAuth) {
        voice.lastErrorMessage = startErrorMessage(error);
        setPhase("error", voice.lastErrorMessage);
      }
    } finally {
      voice.starting = false;
    }
    if (retryAuth) {
      // Expired signaling token: one full restart with a new session.
      voice.fullRestartUsed = true;
      await cleanupCall();
      await start();
    }
  }

  async function establishPeer(gene) {
    const stream = await getUserMedia();
    if (voice.generation !== gene) {
      stopStream(stream);
      return false;
    }
    voice.micStream = stream;
    voice.micTrack = stream.getAudioTracks()[0] || null;
    voice.micMuted = false;
    voice.speakerMuted = false;
    if (voice.micTrack) voice.micTrack.enabled = true;

    const pc = new RTCPeerConnection();
    voice.pc = pc;
    for (const track of stream.getAudioTracks()) pc.addTrack(track, stream);

    // Data channel created before offer — this is required by the Realtime API.
    const dc = pc.createDataChannel("oai-events");
    voice.dc = dc;
    dc.onopen = () => {
      if (voice.active && !voice.reconnecting) setPhase("listening");
    };
    dc.onmessage = (message) => handleRealtimeMessage(message);
    dc.onclose = () => {
      if (voice.active && !voice.reconnecting && !voice.restarting) scheduleReconnect();
    };
    dc.onerror = () => {};

    pc.ontrack = (event) => {
      const remote = event.streams && event.streams[0];
      const audioEl = ensureAudioEl();
      if (remote && audioEl.srcObject !== remote) audioEl.srcObject = remote;
      audioEl.muted = voice.speakerMuted;
      const playback = audioEl.play();
      if (playback && typeof playback.then === "function") {
        playback.then(() => {
          voice.audioBlocked = false;
          if (voice.phase === "error") setPhase("listening");
        }).catch(() => {
          // Autoplay without gesture is disabled: report this immediately.
          voice.audioBlocked = true;
          setPhase("error", tr("voice.error.audio"));
        });
      }
    };
    pc.onconnectionstatechange = () => {
      const connection = pc.connectionState;
      if (connection === "failed") scheduleReconnect();
      else if (connection === "connected" && voice.active && !voice.reconnecting) setPhase("listening");
    };
    pc.oniceconnectionstatechange = () => {
      if (pc.iceConnectionState === "failed") scheduleReconnect();
    };

    await exchangeOffer(false);
    if (voice.generation !== gene) return false;
    return true;
  }

  async function getUserMedia() {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      const error = new Error("mediaDevices unavailable");
      error.name = "NotSupportedError";
      throw error;
    }
    return navigator.mediaDevices.getUserMedia({ audio: true });
  }

  // SDP exchange with our server: offer → response → remote description.
  // This is used both during the initial connection and during an ICE restart.
  async function exchangeOffer(isRestart) {
    const pc = voice.pc;
    if (!pc || pc.signalingState === "closed") return false;
    if (voice.exchangeBusy) return false;
    voice.exchangeBusy = true;
    try {
      const offer = await pc.createOffer(isRestart ? { iceRestart: true } : undefined);
      await pc.setLocalDescription(offer);
      await waitForIceGathering(pc);
      if (pc.signalingState === "closed") return false;
      const local = pc.localDescription;
      const sdp = (local && local.sdp) || offer.sdp || "";
      const answer = await postJson("/api/voice/sdp", { call_id: voice.callId, sdp });
      await pc.setRemoteDescription({ type: "answer", sdp: String(answer.sdp || "") });
      return true;
    } finally {
      voice.exchangeBusy = false;
    }
  }

  function waitForIceGathering(pc, timeoutMs = ICE_TIMEOUT_MS) {
    return new Promise((resolve) => {
      if (!pc || pc.iceGatheringState === "complete") { resolve(); return; }
      let settled = false;
      let poll = 0;
      let timer = 0;
      const finish = () => {
        if (settled) return;
        settled = true;
        window.clearInterval(poll);
        window.clearTimeout(timer);
        if (typeof pc.removeEventListener === "function") pc.removeEventListener("icegatheringstatechange", onState);
        resolve();
      };
      const onState = () => { if (pc.iceGatheringState === "complete") finish(); };
      poll = window.setInterval(() => { if (pc.iceGatheringState === "complete") finish(); }, 100);
      timer = window.setTimeout(finish, timeoutMs);
      if (typeof pc.addEventListener === "function") pc.addEventListener("icegatheringstatechange", onState);
    });
  }

  function waitForConnected(pc, timeoutMs) {
    return new Promise((resolve) => {
      if (!pc) { resolve(false); return; }
      let settled = false;
      let poll = 0;
      let timer = 0;
      const finish = (ok) => {
        if (settled) return;
        settled = true;
        window.clearInterval(poll);
        window.clearTimeout(timer);
        if (typeof pc.removeEventListener === "function") pc.removeEventListener("connectionstatechange", onState);
        resolve(ok);
      };
      const onState = () => {
        if (pc.connectionState === "connected") finish(true);
        if (pc.connectionState === "failed" || pc.connectionState === "closed") finish(false);
      };
      if (pc.connectionState === "connected") { resolve(true); return; }
      poll = window.setInterval(() => { if (pc.connectionState === "connected") finish(true); }, 250);
      timer = window.setTimeout(() => finish(pc.connectionState === "connected"), timeoutMs);
      if (typeof pc.addEventListener === "function") pc.addEventListener("connectionstatechange", onState);
    });
  }

  // -------------------------------------------------------------------------
  // Data channel messages for oai-events.
  // -------------------------------------------------------------------------
  function handleRealtimeMessage(message) {
    let event = null;
    try {
      event = JSON.parse(message.data);
    } catch (error) {
      return;
    }
    if (!event || typeof event !== "object") return;
    const type = String(event.type || "");
    switch (type) {
      case "conversation.item.input_audio_transcription.completed":
        addTranscriptMessage("user", event.transcript);
        submitTranscript(event.transcript).catch(() => {});
        break;
      case "input_audio_buffer.speech_started":
        setPhase("listening");
        break;
      case "response.created":
        voice.responseActive = true;
        voice.responseRequested = false;
        voice.responseQueued = false;
        setPhase("thinking");
        break;
      case "response.done": {
        voice.responseActive = false;
        voice.responseRequested = false;
        const status = String((event.response && event.response.status) || "");
        if (status === "failed") {
          const details = event.response && event.response.status_details;
          const detailError = details && details.error;
          const message = trimMessage(detailError && detailError.message);
          voice.lastErrorMessage = message || tr("voice.status.failed");
          setPhase("error", voice.lastErrorMessage);
        } else {
          setPhase("listening");
        }
        if (voice.responseQueued) {
          voice.responseQueued = false;
          requestResponse();
        }
        break;
      }
      case "error": {
        const error = event.error || {};
        voice.lastErrorMessage = trimMessage(error.message || error.code) || tr("voice.error.realtime");
        setPhase("error", voice.lastErrorMessage);
        break;
      }
      default:
        break;
    }
  }

  function sendRealtime(payload) {
    const dc = voice.dc;
    if (!dc || dc.readyState !== "open") return false;
    try {
      dc.send(JSON.stringify(payload));
      return true;
    } catch (error) {
      return false;
    }
  }

  function requestResponse() {
    if (voice.responseActive || voice.responseRequested) {
      voice.responseQueued = true;
      return false;
    }
    if (!sendRealtime({ type: "response.create" })) return false;
    voice.responseRequested = true;
    voice.responseQueued = false;
    return true;
  }

  // User speech has been recognized: sending text to Igor’s regular session.
  // All work is handled by his agent (DeepSeek via OpenCode subscription), while
  // the OpenAI voice model only recognizes speech and generates spoken responses.
  async function submitTranscript(transcript) {
    const text = String(transcript || "").trim();
    if (!text) return;
    const sessionId = voice.sessionId || currentSessionId();
    if (!sessionId) return;
    try {
      const payload = await postJson("/api/sessions/" + encodeId(sessionId) + "/messages", {
        text,
        idempotency_key: "voice:" + randomId(),
        plan_mode: false,
      });
      const taskId = String(payload.task_id || "");
      if (taskId) {
        voice.pending.set(taskId, { request: text, spoken: new Set(), startedAt: Date.now() });
        renderTask();
      }
    } catch (error) {
      setNotice(tr("voice.error.task"));
    }
  }

  // Audio playback: send the model text as a message and request it to be read verbatim.
  function speak(text) {
    const clean = sanitizeForSpeech(text);
    if (!clean) return;
    sendRealtime({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text: clean }] },
    });
    requestResponse();
  }

  // Code blocks and tool outputs are not spoken: content of fenced blocks (``` and ~~~)
  // is removed; the unmarked trailing code after the opening marker is discarded.
  // These are structural markdown features, without
  // semantic word-based heuristics. Text length is not limited.
  function sanitizeForSpeech(raw) {
    let text = String(raw || "");
    text = text.replace(/```[\s\S]*?```/g, " ");
    text = text.replace(/~~~[\s\S]*?~~~/g, " ");
    text = text.replace(/(?:```|~~~)[\s\S]*$/g, " ");
    return text.replace(/[ \t]{2,}/g, " ").replace(/\n{3,}/g, "\n\n").trim();
  }

  // -------------------------------------------------------------------------
  // Call dialogue log: user → Igor replies in sequence. Responses are rendered
  // as markdown by the same module as the main chat.
  // -------------------------------------------------------------------------
  function resetTranscript() {
    voice.assistantSeen.clear();
    if (dom.transcript) dom.transcript.replaceChildren();
  }

  function addTranscriptMessage(role, text) {
    const clean = String(text == null ? "" : text);
    if (!clean.trim() || !dom.transcript) return;
    if (role === "assistant") {
      if (voice.assistantSeen.has(clean)) return;
      voice.assistantSeen.add(clean);
    }
    const msg = makeEl("div", "voice-msg voice-msg-" + role);
    const labelKey = role === "user"
      ? "voice.user_label"
      : (role === "assistant" ? "voice.assistant_label" : "voice.system_label");
    msg.append(makeEl("span", "voice-msg-role", tr(labelKey)));
    const body = makeEl("div", "voice-msg-body");
    if (role === "assistant" && window.IgorekMarkdown && typeof window.IgorekMarkdown.renderMarkdown === "function") {
      window.IgorekMarkdown.renderMarkdown(clean, body);
    } else {
      body.textContent = clean;
    }
    msg.append(body);
    dom.transcript.append(msg);
    dom.transcript.scrollTop = dom.transcript.scrollHeight;
  }

  // -------------------------------------------------------------------------
  // Session events: app.js sends each SSE frame here.
  // -------------------------------------------------------------------------
  function onSessionEvent(event) {
    if (!event) return;
    refreshConfig();
    if (!voice.active) return;
    const data = event.payload || {};
    const kind = String(event.kind || "");
    const taskId = String(event.task_id || data.task_id || "");

    if (kind === "approval.required") {
      voice.approval = {
        taskId: String(data.task_id || taskId),
        requestId: String(data.request_id || ""),
        tool: String(data.tool || ""),
        reason: String(data.reason || ""),
      };
      renderTask();
      return;
    }
    if (!taskId || !voice.pending.has(taskId)) return;
    const record = voice.pending.get(taskId);

    switch (kind) {
      case "model.round_response": {
        const text = String(data.text || "").trim();
        if (text) {
          addTranscriptMessage("assistant", text);
          speakOnce(record, text);
        }
        break;
      }
      case "task.error": {
        finalizeFailure(record, taskId, trimMessage(data.error_message));
        break;
      }
      case "task.status": {
        const status = String(data.status || "").toLowerCase();
        if (["fail", "dead_letter", "timeout", "cancelled"].includes(status)) {
          scheduleFailure(record, taskId, status);
        }
        break;
      }
      case "task.finish": {
        const status = String(data.status || "").toLowerCase();
        if (status === "waiting_approval") break;
        if (["ok", "completed", "success"].includes(status)) closePending(taskId);
        else scheduleFailure(record, taskId, status);
        break;
      }
      default:
        break;
    }
  }

  function speakOnce(record, text) {
    const clean = String(text || "").trim();
    if (!clean || record.spoken.has(clean)) return;
    record.spoken.add(clean);
    speak(clean);
  }

  // The `task.status(fail)` notification arrives before `task.error` with a specific reason, as seen in
  // the app.js log stream. A brief delay allows the reason to catch up with the terminal status.
  function scheduleFailure(record, taskId, status) {
    if (record.failTimer) return;
    record.failTimer = window.setTimeout(() => {
      record.failTimer = 0;
      const reason = tr("voice.task_failed");
      if (!record.spoken.size) speak(reason);
      addTranscriptMessage("system", reason);
      closePending(taskId);
    }, FAILURE_GRACE_MS);
  }

  function finalizeFailure(record, taskId, reason) {
    if (record.failTimer) {
      window.clearTimeout(record.failTimer);
      record.failTimer = 0;
    }
    const text = reason || tr("voice.task_failed");
    if (!record.spoken.size) speak(text);
    addTranscriptMessage("system", text);
    closePending(taskId);
  }

  function closePending(taskId) {
    const record = voice.pending.get(taskId);
    if (record && record.failTimer) window.clearTimeout(record.failTimer);
    voice.pending.delete(taskId);
    if (voice.approval && voice.approval.taskId === taskId) voice.approval = null;
    renderTask();
  }

  async function resolveApproval(decision) {
    const approval = voice.approval;
    if (!approval || voice.approvalBusy) return;
    voice.approvalBusy = true;
    renderTask();
    try {
      await postJson(
        "/api/tasks/" + encodeId(approval.taskId) + "/approvals/" + encodeId(approval.requestId),
        { decision },
      );
      if (voice.approval === approval) voice.approval = null;
      setNotice(tr(decision === "reject" ? "approval.resolved_reject" : "approval.resolved_allow_once"));
    } catch (error) {
      setNotice(tr("voice.error.approval"));
    } finally {
      voice.approvalBusy = false;
      renderTask();
    }
  }

  function renderTask() {
    if (!dom.task) return;
    dom.task.replaceChildren();
    const rows = Array.from(voice.pending.entries());
    for (const [taskId, record] of rows.slice(0, 3)) {
      const row = makeEl("div", "voice-task-row");
      row.append(makeEl("b", "", ""), makeEl("span", "", tr("voice.task_running", { task: taskId.slice(0, 8) })));
      row.title = record.request || taskId;
      dom.task.append(row);
    }
    const approval = voice.approval;
    if (approval) {
      const box = makeEl("div", "voice-approval");
      box.append(makeEl("strong", "voice-approval-title", tr("voice.approval_title")));
      const tool = approval.tool || tr("approval.default_tool");
      const reason = approval.reason || tr("approval.default_reason");
      const action = approval.target || (approval.input ? JSON.stringify(approval.input, null, 2) : '');
      box.append(makeEl("p", "voice-approval-text", [tool, action, reason].filter(Boolean).join('\n\n')));
      const actions = makeEl("div", "voice-approval-actions");
      const allow = makeEl("button", "mini-button", tr("approval.btn_allow"));
      allow.type = "button";
      allow.disabled = voice.approvalBusy;
      allow.addEventListener("click", () => { resolveApproval("allow_once").catch(() => {}); });
      const reject = makeEl("button", "mini-button voice-reject", tr("approval.btn_reject"));
      reject.type = "button";
      reject.disabled = voice.approvalBusy;
      reject.addEventListener("click", () => { resolveApproval("reject").catch(() => {}); });
      actions.append(allow, reject);
      box.append(actions);
      dom.task.append(box);
    }
    dom.task.hidden = !dom.task.childElementCount;
  }

  // -------------------------------------------------------------------------
  // **Reconnection Process:** First perform an ICE-restart once, then restart the entire session completely.
  // -------------------------------------------------------------------------
  function scheduleReconnect() {
    if (!voice.active || voice.reconnecting || voice.restarting) return;
    reconnect().catch(() => {});
  }

  async function reconnect() {
    if (!voice.active || voice.reconnecting || voice.restarting) return;
    voice.reconnecting = true;
    const gene = voice.generation;
    setPhase("reconnecting");
    try {
      const pc = voice.pc;
      let recovered = false;
      if (!voice.iceRestartUsed && pc && pc.signalingState !== "closed") {
        voice.iceRestartUsed = true;
        try { pc.restartIce(); } catch (error) { /* The browser will generate the restart offer automatically */ }
        try {
          await exchangeOffer(true);
          recovered = await waitForConnected(pc, RECONNECT_WAIT_MS);
        } catch (error) {
          recovered = false;
        }
      }
      if (voice.generation !== gene || !voice.active) return;
      if (recovered) {
        setPhase("listening");
        return;
      }
      await fullRestart();
    } catch (error) {
      setPhase("error", tr("voice.error.reconnect"));
    } finally {
      voice.reconnecting = false;
    }
  }

  async function fullRestart() {
    if (voice.restarting) return;
    voice.restarting = true;
    try {
      // Before starting a new session, the previous session must be closed—no two calls simultaneously.
      await hangup();
      voice.fullRestartUsed = true;
      await start();
    } finally {
      voice.restarting = false;
    }
  }

  async function cleanupCall() {
    const callId = voice.callId;
    teardownPeer();
    voice.callId = "";
    voice.active = false;
    if (callId) {
      try { await postJson("/api/voice/hangup", { call_id: callId }); } catch (error) { /* The call is already closed */ }
    }
  }

  // -------------------------------------------------------------------------
  // **Control Functions:** Microphone, audio output, termination.
  // -------------------------------------------------------------------------
  function toggleMic() {
    if (!voice.micTrack) {
      setNotice(tr("voice.error.mic_missing"));
      return;
    }
    voice.micMuted = !voice.micMuted;
    voice.micTrack.enabled = !voice.micMuted;
    renderControls();
    setNotice(voice.micMuted ? tr("voice.status.mic_off") : tr("voice.status.mic_on"));
  }

  function toggleSpeaker() {
    voice.speakerMuted = !voice.speakerMuted;
    if (voice.audioEl) voice.audioEl.muted = voice.speakerMuted;
    renderControls();
    if (!voice.speakerMuted && voice.audioEl) {
      const playback = voice.audioEl.play();
      if (playback && typeof playback.then === "function") {
        playback.then(() => {
          voice.audioBlocked = false;
          if (voice.phase === "error") setPhase("listening");
        }).catch(() => {
          voice.audioBlocked = true;
          setPhase("error", tr("voice.error.audio"));
        });
      }
    }
    setNotice(voice.speakerMuted ? tr("voice.status.speaker_off") : tr("voice.status.speaker_on"));
  }

  function renderControls() {
    if (dom.mic) {
      dom.mic.classList.toggle("active", voice.micMuted);
      dom.mic.setAttribute("aria-pressed", String(voice.micMuted));
    }
    if (dom.speaker) {
      dom.speaker.classList.toggle("active", voice.speakerMuted);
      dom.speaker.setAttribute("aria-pressed", String(voice.speakerMuted));
    }
  }

  async function hangup() {
    const callId = voice.callId;
    voice.generation += 1;
    teardownPeer();
    voice.callId = "";
    voice.active = false;
    voice.starting = false;
    voice.responseActive = false;
    voice.responseRequested = false;
    voice.responseQueued = false;
    for (const record of voice.pending.values()) {
      if (record.failTimer) window.clearTimeout(record.failTimer);
    }
    voice.pending.clear();
    voice.approval = null;
    voice.approvalBusy = false;
    voice.lastErrorMessage = "";
    closePanel();
    resetTranscript();
    renderTask();
    renderControls();
    setPhase("idle");
    if (callId) {
      try {
        await postJson("/api/voice/hangup", { call_id: callId });
      } catch (error) { /* The server has forgotten the call—this is not a user error */ }
    }
  }

  function teardownPeer() {
    if (voice.micStream) stopStream(voice.micStream);
    voice.micStream = null;
    voice.micTrack = null;
    if (voice.dc) {
      voice.dc.onopen = null;
      voice.dc.onmessage = null;
      voice.dc.onclose = null;
      voice.dc.onerror = null;
      try { voice.dc.close(); } catch (error) { /* Already closed */ }
    }
    voice.dc = null;
    if (voice.pc) {
      voice.pc.ontrack = null;
      voice.pc.onconnectionstatechange = null;
      voice.pc.oniceconnectionstatechange = null;
      try { voice.pc.close(); } catch (error) { /* Already closed */ }
    }
    voice.pc = null;
    if (voice.audioEl) {
      try { voice.audioEl.srcObject = null; } catch (error) { /* Not critical */ }
    }
    voice.micMuted = false;
    voice.speakerMuted = false;
    voice.exchangeBusy = false;
    voice.audioBlocked = false;
  }

  function stopStream(stream) {
    if (!stream || typeof stream.getTracks !== "function") return;
    for (const track of stream.getTracks()) {
      try { track.stop(); } catch (error) { /* Thread has already been terminated */ }
    }
  }

  function ensureAudioEl() {
    if (voice.audioEl) return voice.audioEl;
    const audio = document.createElement("audio");
    audio.autoplay = true;
    audio.hidden = true;
    audio.className = "voice-audio";
    audio.setAttribute("playsinline", "");
    document.body.append(audio);
    voice.audioEl = audio;
    return audio;
  }

  // ------------ Change of selected session: active call belongs to the old session. ------------
  function onSessionChanged(sessionId) {
    const next = String(sessionId || "");
    const changed = Boolean(next && voice.sessionId && voice.sessionId !== next);
    if (changed && voice.active) hangup().catch(() => {});
    if (next) voice.sessionId = next;
    refreshConfig();
  }

  function isActive() {
    return Boolean(voice.active);
  }

  function startErrorMessage(error) {
    const name = String((error && error.name) || "");
    if (name === "NotAllowedError" || name === "SecurityError") return tr("voice.error.mic_denied");
    if (name === "NotFoundError" || name === "NotReadableError" || name === "NotSupportedError") {
      return tr("voice.error.mic_missing");
    }
    if (isCallExpired(error)) return tr("voice.error.expired");
    const message = trimMessage(error && error.message);
    return tr("voice.error.start", { error: message || "unknown" });
  }

  window.IgorekVoice = {
    init,
    onSessionEvent,
    onSessionChanged,
    isActive,
  };
})();
