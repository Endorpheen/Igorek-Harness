(function (root) {
  'use strict';
  function splitText(text) {
    const pieces = []; let remaining = String(text).trim();
    while (remaining.length > 1000) {
      let end = remaining.lastIndexOf(' ', 1000); if (end < 400) end = 1000;
      pieces.push(remaining.slice(0, end)); remaining = remaining.slice(end).trim();
    }
    if (remaining) pieces.push(remaining); return pieces;
  }
  function create({ bridge, makeAudio = () => new Audio(), makeURL = blob => URL.createObjectURL(blob), revoke = url => URL.revokeObjectURL(url), decode = value => Uint8Array.from(atob(value), c => c.charCodeAt(0)) } = {}) {
    let epoch = 0, current, request, activeButton;
    function label(button, state) {
      if (!button) return;
      button.textContent = state === 'idle' ? (button.dataset.speakLabel || 'Озвучить') : state === 'loading' ? (button.dataset.loadingLabel || 'Остановить загрузку') : (button.dataset.stopLabel || 'Остановить');
      button.setAttribute('aria-pressed', state === 'idle' ? 'false' : 'true');
    }
    function stop() {
      epoch++;
      if (request) Promise.resolve(bridge.cancel({ id: request })).catch(() => {});
      request = undefined;
      if (current) { current.audio.pause(); current.audio.src = ''; revoke(current.url); current.done(); current = undefined; }
      label(activeButton, 'idle'); activeButton = undefined;
    }
    async function play(text, button, config) {
      if (activeButton === button) { stop(); return; }
      stop(); const token = epoch; activeButton = button; label(button, 'loading');
      try {
        for (const part of splitText(text)) {
          if (epoch !== token) return;
          const id = 'tts_' + Date.now() + '_' + Math.random().toString(36).slice(2); request = id;
          const result = await bridge.speak({ id, text: part, ...(config ? { config } : {}) });
          if (epoch !== token) return;
          if (request === id) request = undefined;
          const url = makeURL(new Blob([decode(result.data)], { type: result.mime }));
          const audio = makeAudio(); audio.src = url; label(button, 'playing');
          await new Promise((resolve, reject) => {
            current = { audio, url, done: resolve };
            audio.onended = resolve; audio.onerror = () => reject(Error('Не удалось воспроизвести аудио.'));
            Promise.resolve(audio.play()).catch(reject);
          });
          if (current?.url === url) { revoke(url); current = undefined; }
          if (epoch === token) label(button, 'loading');
        }
      } catch (error) {
        if (epoch === token && button) { button.title = error.message || 'Ошибка озвучивания.'; button.dispatchEvent?.(new CustomEvent('tts-error', { detail: button.title })); }
      } finally { if (epoch === token) stop(); }
    }
    return { play, stop };
  }
  const api = { create, splitText };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root.IgorekTTSPlayback = api;
    if (root.IgorekDesktop?.tts) {
      root.IgorekMessageSpeech = create({ bridge: { speak: value => root.IgorekDesktop.tts('speak', value), cancel: value => root.IgorekDesktop.tts('cancel', value) } });
      const apply = config => { root.IgorekSpeechState = config; if (!config.enabled) root.IgorekMessageSpeech.stop(); for (const button of document.querySelectorAll('.message-speak')) button.hidden = !config.enabled || button.dataset.streaming === 'true' || button.dataset.hasText !== 'true'; };
      root.IgorekDesktop.tts('read').then(apply).catch(() => {});
      root.IgorekDesktop.onTTSState?.(apply);
      root.addEventListener('pagehide', () => root.IgorekMessageSpeech.stop());
    }
  }
})(typeof window === 'object' ? window : globalThis);
