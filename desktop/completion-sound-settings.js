(() => {
  const bridge = window.IgorekCompletionSoundSettings;
  const toggle = document.getElementById('completion-sound-enabled');
  const status = document.getElementById('completion-sound-status');
  if (!bridge || !toggle || !status) return;
  let enabled = false;
  const apply = value => {
    if (typeof value?.enabled !== 'boolean') return;
    enabled = value.enabled; toggle.checked = enabled; toggle.disabled = false;
    status.textContent = enabled ? 'Звук включён. Сигнал прозвучит, когда окно Игорька неактивно или свёрнуто.' : 'Звук завершения задачи выключен.';
  };
  bridge.onChange(apply);
  bridge.read().then(apply).catch(() => { status.textContent = 'Не удалось прочитать настройку звука.'; });
  toggle.addEventListener('change', async () => {
    toggle.disabled = true;
    try { apply(await bridge.set(toggle.checked)); }
    catch { toggle.checked = enabled; toggle.disabled = false; status.textContent = 'Не удалось сохранить настройку звука. Повторите попытку.'; }
  });
})();
