(() => {
  const bridge = window.IgorekResourceMonitor, host = document.getElementById('monitor-data'), status = document.getElementById('monitor-status');
  const valid = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const bytes = value => valid(value) ? (value / 1073741824).toFixed(1) + ' ГиБ' : '—';
  const ratio = (used, total) => valid(used) && valid(total) && total > 0 ? Math.min(100, 100 * used / total) : null;
  const node = (tag, className, text) => { const item = document.createElement(tag); item.className = className; if (text !== undefined) item.textContent = text; return item; };
  function metric(label, value, detail, display) {
    const item = node('section', 'metric'), head = node('div', 'metric-head');
    head.append(node('span', '', label), node('strong', '', display ?? (valid(value) ? Math.round(value) + '%' : '—'))); item.append(head);
    if (detail) item.append(node('div', 'metric-detail', detail));
    const progress = node('progress', ''); progress.max = 100; progress.value = valid(value) ? Math.min(100, value) : 0;
    progress.setAttribute('aria-label', label); if (!valid(value)) progress.hidden = true;
    item.append(progress); return item;
  }
  function render(value) {
    if (!value) return;
    const rows = [metric('RAM', ratio(value.ram.used, value.ram.total), bytes(value.ram.used) + ' / ' + bytes(value.ram.total)), metric('CPU', value.cpu)];
    for (const gpu of value.gpus) {
      const name = node('div', 'gpu-name', gpu.name); name.title = gpu.name; rows.push(name);
      rows.push(metric('VRAM', ratio(gpu.used, gpu.total), bytes(gpu.used) + ' / ' + bytes(gpu.total)), metric('GPU', gpu.load),
        metric('Температура', null, '', valid(gpu.temperature) ? Math.round(gpu.temperature) + ' °C' : '—'),
        metric('Питание', ratio(gpu.power, gpu.limit), '', valid(gpu.power) ? Math.round(gpu.power) + (valid(gpu.limit) ? ' / ' + Math.round(gpu.limit) : '') + ' Вт' : '—'));
    }
    host.replaceChildren(...rows); status.textContent = value.gpus.length ? 'Этот компьютер · обновление каждые 2 с' : 'Этот компьютер · данные GPU недоступны';
  }
  let timer, busy = false, stopped = false;
  async function refresh() {
    clearTimeout(timer); if (busy || stopped || document.hidden) return; busy = true;
    try { render(await bridge.snapshot()); } catch { status.textContent = 'Не удалось обновить показатели'; }
    finally { busy = false; if (!stopped && !document.hidden) timer = setTimeout(refresh, 2000); }
  }
  document.getElementById('monitor-close').addEventListener('click', () => bridge.close().catch(() => { status.textContent = 'Не удалось выключить монитор'; }));
  document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (!document.hidden) refresh(); });
  window.addEventListener('beforeunload', () => { stopped = true; clearTimeout(timer); }); refresh();
})();
