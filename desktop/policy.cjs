const { X509Certificate } = require('node:crypto');
const { readFileSync } = require('node:fs');
const { homedir } = require('node:os');
const { join } = require('node:path');

function localOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Нужен локальный HTTPS-адрес без пароля, пути и параметров.');
  }
  return url.origin;
}
function sameOrigin(value, origin) {
  try { return new URL(value).origin === origin; } catch { return false; }
}
function fingerprint(pem) {
  return new X509Certificate(pem).fingerprint256.replaceAll(':', '').toLowerCase();
}
function certificateMatches(pem, expected) {
  try { return fingerprint(pem) === expected; } catch { return false; }
}
function settings(argv, env = process.env) {
  const opts = { url: 'https://127.0.0.1:5177', cert: join(
    env.IGOREK_FACTORY_HOME || env.FACTORY_DATA_DIR || join(homedir(), '.igorek-agent-factory'),
    'web-ui', 'tls', 'localhost-cert.pem') };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--url' || argv[i] === '--cert') {
      const key = argv[i].slice(2);
      if (!argv[i + 1] || argv[i + 1].startsWith('--')) throw new Error(`Не указано значение --${key}`);
      opts[key] = argv[++i];
    } else { throw new Error(`Неизвестный параметр: ${argv[i]}`); }
  }
  return { origin: localOrigin(opts.url), fingerprint: fingerprint(readFileSync(opts.cert)) };
}
function allowAudio(permission, details) {
  return permission === 'media' && Array.isArray(details.mediaTypes)
    && details.mediaTypes.length > 0 && details.mediaTypes.every(type => type === 'audio');
}
function allowClipboardWrite(permission) {
  return permission === 'clipboard-sanitized-write';
}
module.exports = { localOrigin, sameOrigin, fingerprint, certificateMatches, settings, allowAudio, allowClipboardWrite };
