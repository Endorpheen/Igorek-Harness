const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

class CompletionSound {
  constructor(file) {
    this.file = file; this.enabled = true; this.windows = new Map(); this.claims = new Set();
    try {
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1024) throw Error('invalid_settings');
      const value = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (value.version !== 1 || typeof value.enabled !== 'boolean') throw Error('invalid_settings');
      this.enabled = value.enabled;
    } catch (error) { if (error.code !== 'ENOENT') this.enabled = false; }
  }
  state(target) {
    return { enabled: this.enabled, background: target.isMinimized() || !target.isVisible() || !target.isFocused() };
  }
  attach(target, allowedURL) {
    const contents = target.webContents;
    const publish = () => {
      if (!target.isDestroyed() && !contents.isDestroyed() && allowedURL(contents.getURL()))
        contents.send('igorek:completion-sound-state', this.state(target));
    };
    const events = ['focus', 'blur', 'show', 'hide', 'minimize', 'restore'];
    this.windows.set(target, publish);
    for (const event of events) target.on(event, publish);
    contents.on('did-finish-load', publish);
    target.once('closed', () => {
      for (const event of events) target.removeListener(event, publish);
      contents.removeListener('did-finish-load', publish);
      this.windows.delete(target);
    });
  }
  set(enabled) {
    if (typeof enabled !== 'boolean') throw Error('invalid_settings');
    const directory = path.dirname(this.file);
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    const temporary = path.join(directory, '.completion-sound-' + randomUUID());
    try {
      fs.writeFileSync(temporary, JSON.stringify({ version: 1, enabled }), { mode: 0o600, flag: 'wx' });
      fs.renameSync(temporary, this.file);
    } finally { try { fs.unlinkSync(temporary); } catch {} }
    this.enabled = enabled;
    for (const publish of this.windows.values()) publish();
    return { enabled };
  }
  claim(target, taskId) {
    if (typeof taskId !== 'string' || !/^[-A-Za-z0-9_]{1,160}$/.test(taskId)) return false;
    const state = this.state(target);
    if (!state.enabled || !state.background || this.claims.has(taskId)) return false;
    this.claims.add(taskId);
    if (this.claims.size > 1000) this.claims.delete(this.claims.values().next().value);
    return true;
  }
}
module.exports = { CompletionSound };
