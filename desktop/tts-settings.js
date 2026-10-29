(() => {
  const bridge = window.IgorekTTSSettings, byId = id => document.getElementById(id);
  if (!bridge || !byId('tts-form')) return;
  const status = byId('tts-status'), form = byId('tts-form');
  const readFields = () => ({ enabled: byId('tts-enabled').checked, url: byId('tts-url').value, model: byId('tts-model').value, voice: byId('tts-voice').value });
  const apply = value => { for (const key of ['url','model','voice']) byId('tts-' + key).value = value[key]; byId('tts-enabled').checked = value.enabled; };
  const player = window.IgorekTTSPlayback.create({ bridge: { speak: value => bridge.preview(value), cancel: value => bridge.cancel(value) } });
  bridge.read().then(apply).catch(error => { status.textContent = error.message; });
  form.addEventListener('submit', async event => {
    event.preventDefault(); player.stop(); status.textContent = 'Сохраняю…';
    try { apply(await bridge.save(readFields())); status.textContent = 'Настройки озвучивания сохранены.'; }
    catch (error) { status.textContent = error.message; }
  });
  byId('tts-models-load').addEventListener('click', async () => {
    status.textContent = 'Получаю список моделей…';
    try {
      const models = await bridge.models(readFields()); byId('tts-model-list').replaceChildren();
      for (const id of models) { const option = document.createElement('option'); option.value = id; byId('tts-model-list').append(option); }
      status.textContent = models.length ? 'Модели загружены. Выберите модель в поле выше.' : 'Сервер вернул пустой список моделей.';
    } catch (error) { status.textContent = error.message; }
  });
  byId('tts-preview').addEventListener('click', () => { status.textContent = ''; player.play('Привет! Это проверка озвучивания сообщений Игорька.', byId('tts-preview'), readFields()); });
  byId('tts-preview').addEventListener('tts-error', event => { status.textContent = event.detail; });
  window.addEventListener('pagehide', () => player.stop());
})();
