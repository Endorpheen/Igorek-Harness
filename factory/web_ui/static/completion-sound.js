(function (root) {
  "use strict";
  function create(options = {}) {
    const hidden = options.hidden || (() => document.visibilityState === "hidden" || root.IgorekDesktop?.isBackground?.() === true);
    const enabled = options.enabled || (() => root.IgorekDesktop?.completionSoundEnabled?.() !== false);
    const claim = options.claim || (taskId => root.IgorekDesktop?.claimCompletionSound?.(taskId) ?? true);
    const makeContext = options.makeContext || (() => new (root.AudioContext || root.webkitAudioContext)());
    const load = options.load || (async () => {
      const response = await fetch("/audio/task-completed.mp3");
      if (!response.ok) throw new Error("Alarm download failed: " + response.status);
      return response.arrayBuffer();
    });
    const report = options.report || ((error) => console.warn("Completion sound unavailable", error));
    const active = new Set(), notified = new Set();
    let context, bufferPromise;
    function unlock() {
      try {
        context ||= makeContext();
        context.resume().catch(report);
        if (!bufferPromise) {
          bufferPromise = load().then(bytes => context.decodeAudioData(bytes)).catch(error => {
            bufferPromise = null;
            report(error);
            return null;
          });
        }
      } catch (error) { report(error); }
    }
    async function play(taskId) {
      if (!context || !bufferPromise) return;
      try {
        const buffer = await bufferPromise;
        if (!buffer || !enabled() || !hidden() || context.state !== "running") return;
        if (!await claim(taskId) || !enabled() || !hidden()) return;
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        source.onended = () => source.disconnect();
        source.start();
      } catch (error) { report(error); }
    }
    function watch(taskId) {
      active.clear();
      if (taskId && !notified.has(String(taskId))) active.add(String(taskId));
    }
    function observe(event) {
      const data = event.payload || {}, id = String(event.task_id || data.task_id || "");
      if (!id) return;
      if (event.kind === "task.submitted") {
        if (!data.parent_task_id && !Number(data.delegate_depth || 0) && !notified.has(id)) active.add(id);
        return;
      }
      // Only the database transition confirms that all execution and retries ended.
      if (event.kind !== "task.status" || !active.has(id)) return;
      if (!["ok", "fail", "dead_letter", "timeout", "cancelled"].includes(data.status)) return;
      active.delete(id);
      if (data.status !== "ok" || notified.has(id)) return;
      notified.add(id);
      if (enabled() && hidden()) return play(id);
    }
    return { unlock, watch, observe };
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { create };
  if (root.document) {
    root.IgorekCompletionSound = create();
    if (root.IgorekDesktop) root.IgorekCompletionSound.unlock();
    for (const event of ["click", "keydown"])
      root.document.addEventListener(event, () => root.IgorekCompletionSound.unlock(), { capture: true });
  }
})(typeof window !== "undefined" ? window : globalThis);
