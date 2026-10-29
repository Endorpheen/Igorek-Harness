const fs = require('node:fs');
const { dirname } = require('node:path');
const { fetch: safeFetch } = require('undici');

const DEFAULTS = Object.freeze({ enabled: false, url: 'http://127.0.0.1:8092/v1', model: '', voice: 'default' });
function validateSettings(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some(key => !Object.hasOwn(DEFAULTS, key))) throw Error('Недопустимые настройки озвучивания.');
  const result = { ...DEFAULTS, ...value };
  if (typeof result.enabled !== 'boolean') throw Error('Недопустимое состояние озвучивания.');
  for (const key of ['url', 'model', 'voice']) {
    if (typeof result[key] !== 'string') throw Error('Недопустимые настройки озвучивания.');
    result[key] = result[key].trim();
  }
  let url;
  try { url = new URL(result.url); } catch { throw Error('Укажите HTTP(S)-адрес TTS-сервера.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw Error('Адрес сервера должен быть HTTP(S), без пароля и параметров.');
  if (result.url.length > 2048 || result.model.length > 256 || result.voice.length > 128)
    throw Error('Слишком длинное значение настройки.');
  if (result.enabled && (!result.model || !result.voice)) throw Error('Укажите модель и голос.');
  result.url = url.href.replace(/\/+$/, '');
  return result;
}

class TextToSpeech {
  constructor(path, { fetchImpl = safeFetch } = {}) {
    this.path = path; this.fetch = fetchImpl; this.active = new Map(); this.config = { ...DEFAULTS };
    try { this.config = validateSettings(JSON.parse(fs.readFileSync(path, 'utf8'))); } catch { /* Missing or invalid optional settings stay disabled. */ }
  }
  state() { return { ...this.config }; }
  save(value) {
    const next = validateSettings(value);
    fs.mkdirSync(dirname(this.path), { recursive: true });
    const temporary = this.path + '.tmp';
    fs.writeFileSync(temporary, JSON.stringify(next, null, 2) + '\n', { mode: 0o600 });
    fs.renameSync(temporary, this.path);
    for (const job of this.active.values()) job.abort();
    this.config = next; return this.state();
  }
  cancel(owner, id) { this.active.get(owner + ':' + id)?.abort(); return { cancelled: true }; }
  async request(config, suffix, options, signal, limit) {
    const response = await this.fetch(config.url + suffix, { ...options, redirect: 'error',
      signal: AbortSignal.any([signal, AbortSignal.timeout(suffix === '/models' ? 10000 : 180000)]) });
    if (!response.ok) { await response.body?.cancel(); throw Error('TTS-сервер вернул HTTP ' + response.status + '.'); }
    const chunks = []; let size = 0;
    for await (const chunk of response.body) {
      size += chunk.byteLength;
      if (size > limit) { await response.body.cancel().catch(() => {}); throw Error('Ответ TTS-сервера слишком большой.'); }
      chunks.push(Buffer.from(chunk));
    }
    return { bytes: Buffer.concat(chunks), type: response.headers.get('content-type') || '' };
  }
  async models(value) {
    const config = validateSettings({ ...this.config, ...value, enabled: false });
    const result = await this.request(config, '/models', {}, new AbortController().signal, 256 * 1024);
    let payload; try { payload = JSON.parse(result.bytes); } catch { throw Error('Сервер вернул некорректный список моделей.'); }
    if (!Array.isArray(payload.data)) throw Error('Сервер вернул некорректный список моделей.');
    return payload.data.filter(item => typeof item?.id === 'string').slice(0, 500).map(item => item.id);
  }
  async speak(owner, value, preview = false) {
    const config = preview ? validateSettings({ ...value.config, enabled: true }) : this.state();
    if (!config.enabled) throw Error('Включите озвучивание в настройках звука.');
    if (typeof value?.text !== 'string' || !value.text.trim() || value.text.length > 2000
        || typeof value.id !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(value.id)) throw Error('Недопустимый запрос озвучивания.');
    const key = owner + ':' + value.id;
    if (this.active.has(key)) throw Error('Запрос озвучивания уже выполняется.');
    const controller = new AbortController(); this.active.set(key, controller);
    try {
      const result = await this.request(config, '/audio/speech', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: config.model, voice: config.voice, input: value.text, response_format: 'wav' }) }, controller.signal, 20 * 1024 * 1024);
      if (!/^audio\/(?:wav|x-wav|wave)(?:;|$)/i.test(result.type)
          || result.bytes.toString('ascii', 0, 4) !== 'RIFF' || result.bytes.toString('ascii', 8, 12) !== 'WAVE') throw Error('TTS-сервер не вернул WAV-аудио.');
      return { mime: 'audio/wav', data: result.bytes.toString('base64') };
    } finally { this.active.delete(key); }
  }
}
module.exports = { TextToSpeech, validateSettings };
