(() => {
  const fallback = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="#263b2d"/><stop offset="1" stop-color="#121b16"/></linearGradient></defs><rect width="640" height="360" rx="14" fill="url(#g)"/><circle cx="320" cy="180" r="35" fill="#b9e568" fill-opacity=".15"/><path d="M311 162L337 180L311 198Z" fill="#c2ef78"/></svg>');
  const cache = new Map(), queue = [];
  let active = 0;
  function extract(source) {
    return new Promise(resolve => {
      const probe = document.createElement('video'); let done = false, index = 0, points = [];
      const finish = value => {
        if (done) return; done = true; clearTimeout(timer);
        probe.removeEventListener('loadedmetadata', ready); probe.removeEventListener('seeked', capture); probe.removeEventListener('error', failed);
        probe.pause(); probe.removeAttribute('src'); probe.load(); resolve(value);
      };
      const failed = () => finish(null);
      const timer = setTimeout(failed, 8000);
      function seek() {
        if (index >= points.length) return finish(null);
        try { probe.currentTime = points[index++]; } catch { finish(null); }
      }
      function ready() {
        if (!Number.isFinite(probe.duration) || probe.duration <= 0 || !probe.videoWidth || !probe.videoHeight) return finish(null);
        const end = Math.max(0, probe.duration - 0.01);
        points = [...new Set([Math.min(1, probe.duration * 0.1), probe.duration * 0.25, probe.duration * 0.5].map(value => Math.min(end, value)))]; seek();
      }
      function capture() {
        try {
          const scale = Math.min(480 / probe.videoWidth, 270 / probe.videoHeight, 1), canvas = document.createElement('canvas');
          canvas.width = Math.max(1, Math.round(probe.videoWidth * scale)); canvas.height = Math.max(1, Math.round(probe.videoHeight * scale));
          const context = canvas.getContext('2d', {willReadFrequently: true});
          if (!context) return finish(null);
          context.drawImage(probe, 0, 0, canvas.width, canvas.height);
          const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
          let bright = 0;
          for (let pixel = 0; pixel < pixels.length; pixel += 4) if (Math.max(pixels[pixel], pixels[pixel + 1], pixels[pixel + 2]) > 20) bright++;
          if (bright > canvas.width * canvas.height * 0.005) return finish(canvas.toDataURL('image/jpeg', 0.78));
          seek();
        } catch { finish(null); }
      }
      probe.muted = true; probe.preload = 'metadata'; probe.playsInline = true;
      probe.addEventListener('loadedmetadata', ready); probe.addEventListener('seeked', capture); probe.addEventListener('error', failed);
      probe.src = source; probe.load();
    });
  }
  function drain() {
    while (active < 2 && queue.length) {
      const job = queue.shift(); active++;
      extract(job.source).then(job.resolve).finally(() => { active--; drain(); });
    }
  }
  function poster(source) {
    if (cache.has(source)) return cache.get(source);
    if (queue.length >= 30) return Promise.resolve(null);
    const value = new Promise(resolve => { queue.push({source, resolve}); });
    cache.set(source, value);
    if (cache.size > 32) cache.delete(cache.keys().next().value);
    drain(); return value;
  }
  function attachVideoPoster(player) {
    if (!player || player.tagName?.toLowerCase() !== 'video' || player.poster) return;
    player.poster = fallback;
    let source;
    try { const url = new URL(player.src, location.href); if (url.origin !== location.origin) return; source = url.href; } catch { return; }
    const load = () => {
      if (!player.isConnected || player.src !== source) return;
      if (player.paused && player.currentTime === 0 && player.readyState === 0) {
        player.preload = 'metadata'; player.load();
      }
      poster(source).then(image => { if (image && player.isConnected && player.src === source) player.poster = image; });
    };
    if (typeof IntersectionObserver === 'function') {
      const observer = new IntersectionObserver(entries => {
        if (!player.isConnected) { observer.disconnect(); return; }
        if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); load(); }
      }, {rootMargin: '160px'});
      observer.observe(player);
    } else queueMicrotask(load);
  }
  window.IgorekMediaPreviews = Object.freeze({attachVideoPoster});
})();
