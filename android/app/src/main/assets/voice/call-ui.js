(() => {
  "use strict";
  let sequence = 0;
  const requests = new Map();
  let starting = false;
  let connectedAt = null;
  let phase = "connecting";
  let speaking = false;
  let notice = "";
  let waiting = false;
  let pendingCount = 0;
  let recording = false;
  let busy = false;
  let held = false;
  let micMode = "ptt";
  const byId = (id) => document.getElementById(id);
  const timer = byId("call-timer");
  const conversation = byId("conversation");

  window.nativeReply = (id, status, payload) => {
    const pending = requests.get(id);
    if (!pending) return;
    requests.delete(id);
    pending.resolve({ ok: status >= 200 && status < 300, status, json: async () => payload });
    if (pending.path === "/api/voice/config" && !starting) {
      starting = true;
      if (status >= 200 && status < 300 && payload.enabled) {
        setTimeout(() => window.IgorekVoice.begin().catch(() => Native.unavailable("Не удалось начать разговор")), 0);
      } else {
        Native.unavailable(payload.reason || payload.error || "Голосовой режим не настроен");
      }
    }
  };
  window.fetch = (path, options = {}) => new Promise((resolve) => {
    const id = ++sequence;
    requests.set(id, { resolve, path });
    Native.request(id, path, options.body || "");
  });
  window.sessionEvent = (event) => window.IgorekVoice.onSessionEvent(event);
  window.initialize = (sessionId) => { window.IgorekVoice.init(); window.IgorekVoice.onSessionChanged(sessionId); };

  function renderStatus() {
    byId("voice-status").textContent = recording ? "Говори — микрофон открыт" : busy ? (held ? "Готовлю микрофон…" : "Отправляю фразу…") : speaking ? "Говорю" : waiting && pendingCount > 0 ? "Жду первого ответа Игорька…" : notice;
    document.body.classList.toggle("speaking", speaking);
    document.body.classList.toggle("details-open", conversation.open);
    document.body.classList.toggle("ptt-mode", micMode === "ptt");
    if (connectedAt === null) timer.textContent = phase === "error" ? "Нет связи" : "Соединяем…";
    else {
      const elapsed = Math.max(0, Math.floor((performance.now() - connectedAt) / 1000));
      timer.textContent = Math.floor(elapsed / 60).toString().padStart(2, "0") + ":" + (elapsed % 60).toString().padStart(2, "0");
    }
  }
  window.IgorekCall = {
    stateChanged(state) {
      phase = state.phase;
      notice = state.notice;
      waiting = state.waitingForAgent;
      pendingCount = state.pendingCount || 0;
      recording = Boolean(state.pttRecording);
      busy = Boolean(state.pttBusy);
      held = Boolean(state.pttHeld);
      micMode = state.micMode || "ptt";
      if (state.connected && connectedAt === null) connectedAt = performance.now();
      conversation.classList.toggle("approval", state.approval);
      if (state.approval) conversation.open = true;
      byId("conversation-summary").textContent = state.approval ? "Нужно подтверждение" : "Разговор";
      const micLabel = micMode === "ptt" ? (recording ? "Отпусти, чтобы отправить" : "Зажми, чтобы говорить") : (state.micMuted ? "Включить микрофон" : "Выключить микрофон");
      byId("voice-mic").setAttribute("aria-label", micLabel);
      byId("voice-mic-label").textContent = micMode === "ptt" ? (recording ? "Говори" : "Зажми и говори") : "Микрофон";
      byId("voice-speaker").setAttribute("aria-label", state.speakerMuted ? "Включить звук" : "Выключить звук");
      Native.state(JSON.stringify(state));
      renderStatus();
    },
    trace(event) { Native.trace(JSON.stringify(event)); },
    ended() { Native.ended(); },
  };
  conversation.addEventListener("toggle", renderStatus);
  setInterval(renderStatus, 500);

  const meterContext = new AudioContext();
  const meter = meterContext.createAnalyser();
  meter.fftSize = 256;
  const samples = new Uint8Array(meter.fftSize);
  let meterStream = null;
  let meterSource = null;
  let lastSound = -1000;
  byId("voice-button").addEventListener("click", () => meterContext.resume().catch(() => {}));
  const originalBegin = window.IgorekVoice.begin;
  window.IgorekVoice.begin = () => { meterContext.resume().catch(() => {}); return originalBegin(); };
  setInterval(() => {
    const audio = document.querySelector("audio");
    const stream = audio?.srcObject;
    if (stream !== meterStream) {
      meterSource?.disconnect(); meterSource = null; meterStream = stream;
      if (stream?.getAudioTracks().length) { meterSource = meterContext.createMediaStreamSource(stream); meterSource.connect(meter); }
    }
    let energy = 0;
    if (meterSource && !audio.muted && !audio.paused && meterContext.state === "running") {
      meter.getByteTimeDomainData(samples);
      for (const value of samples) energy += ((value - 128) / 128) ** 2;
      if (Math.sqrt(energy / samples.length) > .015) lastSound = performance.now();
    }
    const active = !!meterSource && !audio.muted && !audio.paused && performance.now() - lastSound < 220;
    if (active !== speaking) { speaking = active; Native.speaking(active); renderStatus(); }
  }, 80);
})();
