const { readFileSync, mkdirSync, writeFileSync, renameSync, chmodSync } = require('node:fs');
const { dirname } = require('node:path');
const { randomUUID } = require('node:crypto');

function safeText(value, limit = 8000) {
  return String(value ?? '').slice(0, 16000)
    .replace(/\bBearer\s+[^\s"'<>]+/gi, 'Bearer [скрыто]')
    .replace(/\b(?:sk-[\w-]+|eyJ[\w-]+\.[\w-]+\.[\w-]+)\b/g, '[скрыто]')
    .replace(/(["']?(?:access_token|refresh_token|id_token|api[_-]?key|authorization|cookie|password|пароль|token|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, '$1[скрыто]')
    .replace(/https?:\/\/[^\s<>"']+/gi, raw => {
      try { const url = new URL(raw); url.username = ''; url.password = ''; url.search = ''; url.hash = ''; return url.href; }
      catch { return '[ссылка скрыта]'; }
    }).slice(0, limit);
}

class NotificationStore {
  constructor(path) {
    this.path = path; this.items = [];
    try {
      const values = JSON.parse(readFileSync(path, 'utf8'));
      if (Array.isArray(values)) this.items = values.slice(0, 100).filter(item => item && typeof item.id === 'string')
        .map(item => ({ id: item.id, source: safeText(item.source, 160), detail: safeText(item.detail),
          level: item.level === 'error' ? 'error' : 'info', time: Number(item.time) || 0,
          read: item.read === true, count: Math.max(1, Number(item.count) || 1) }));
    } catch { /* Missing or damaged history starts empty. */ }
  }
  snapshot() { return this.items.map(item => ({ ...item })); }
  save() {
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = this.path + '.tmp';
    writeFileSync(temporary, JSON.stringify(this.items), { mode: 0o600 });
    chmodSync(temporary, 0o600); renameSync(temporary, this.path);
    return this.snapshot();
  }
  add(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || typeof value.detail !== 'string' || value.detail.length > 16000
        || (value.level !== 'error' && value.level !== 'info')) throw new Error('Некорректное уведомление.');
    const detail = safeText(value.detail), source = safeText(value.source || 'Приложение', 160), time = Date.now();
    if (!detail.trim()) return this.snapshot();
    const previous = this.items.find(item => item.detail === detail && item.source === source && item.level === value.level && time - item.time < 120000);
    if (previous) {
      previous.count += 1; previous.time = time; previous.read = false;
      this.items = [previous, ...this.items.filter(item => item !== previous)];
    } else this.items.unshift({ id: randomUUID(), source, detail, level: value.level, time, read: false, count: 1 });
    this.items.length = Math.min(100, this.items.length);
    return this.save();
  }
  read() { for (const item of this.items) item.read = true; return this.save(); }
  clear() { this.items = []; return this.save(); }
}
module.exports = { NotificationStore, safeText };
