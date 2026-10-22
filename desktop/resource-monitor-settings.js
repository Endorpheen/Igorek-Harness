(() => {
  const bridge = window.IgorekResourceMonitorSettings, toggle = document.getElementById('resource-monitor-enabled'), status = document.getElementById('resource-monitor-status');
  if (!bridge || !toggle || !status) return;
  let enabled = false;
  const apply = value => {
    if (typeof value?.enabled !== 'boolean') return;
    enabled = value.enabled; toggle.checked = enabled; toggle.disabled = false;
    status.textContent = enabled ? 'Монитор открыт. Перетаскивайте его за заголовок.' : 'Монитор выключен. Сбор показателей не выполняется.';
  };
  bridge.onChange(apply); bridge.read().then(apply).catch(() => { status.textContent = 'Не удалось прочитать настройку монитора.'; });
  toggle.addEventListener('change', async () => {
    toggle.disabled = true;
    try { apply(await bridge.set(toggle.checked)); }
    catch { toggle.checked = enabled; toggle.disabled = false; status.textContent = 'Не удалось сохранить настройку монитора.'; }
  });
})();
