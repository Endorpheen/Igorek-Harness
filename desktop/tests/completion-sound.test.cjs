const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { CompletionSound } = require('../completion-sound.cjs');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'igorek-completion-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const file = path.join(root, 'sound.json');
  return { file, sound: new CompletionSound(file) };
}
function windowFixture() {
  const target = new EventEmitter(); target.focused = true; target.minimized = false; target.visible = true;
  Object.assign(target, { isDestroyed: () => false, isFocused: () => target.focused,
    isMinimized: () => target.minimized, isVisible: () => target.visible });
  target.sent = [];
  target.webContents = Object.assign(new EventEmitter(), { isDestroyed: () => false,
    getURL: () => target.url || 'https://127.0.0.1:5177/', send: (...args) => target.sent.push(args) });
  return target;
}
test('sound preference persists as an owner-only boolean', t => {
  const { file, sound } = fixture(t);
  assert.equal(sound.enabled, true);
  sound.set(false);
  assert.equal(new CompletionSound(file).enabled, false);
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.throws(() => sound.set('false'));
  sound.set(true); assert.equal(new CompletionSound(file).enabled, true);
});
test('focus and minimize publish state only to the trusted page and detach on close', t => {
  const { sound } = fixture(t), target = windowFixture();
  sound.attach(target, url => url === 'https://127.0.0.1:5177/');
  target.webContents.emit('did-finish-load');
  assert.deepEqual(target.sent.at(-1)[1], { enabled: true, background: false });
  target.focused = false; target.emit('blur');
  assert.equal(target.sent.at(-1)[1].background, true);
  target.minimized = true; target.focused = true; target.emit('minimize');
  assert.equal(target.sent.at(-1)[1].background, true);
  target.url = 'https://foreign.example/'; const count = target.sent.length;
  sound.set(false); assert.equal(target.sent.length, count);
  target.emit('closed'); assert.equal(sound.windows.size, 0); assert.equal(target.listenerCount('blur'), 0);
});
test('background claims deduplicate across chat windows and honor disabling', t => {
  const { sound } = fixture(t), first = windowFixture(), second = windowFixture();
  assert.equal(sound.claim(first, 'task-1'), false);
  first.focused = second.focused = false;
  assert.equal(sound.claim(first, 'task-1'), true);
  assert.equal(sound.claim(second, 'task-1'), false);
  sound.set(false); assert.equal(sound.claim(first, 'task-2'), false);
  sound.set(true); assert.equal(sound.claim(first, 'task-2'), true);
  assert.equal(sound.claim(first, '../task'), false);
});
test('symlink settings never read or overwrite the linked file', t => {
  const { file } = fixture(t), target = file + '.private';
  fs.writeFileSync(target, 'private-canary'); fs.symlinkSync(target, file);
  const sound = new CompletionSound(file); assert.equal(sound.enabled, false);
  sound.set(true); assert.equal(fs.readFileSync(target, 'utf8'), 'private-canary');
  assert.equal(fs.lstatSync(file).isSymbolicLink(), false);
});
test('completion IPC allows trusted reads and claims but only owner settings writes', t => {
  const { sound } = fixture(t), target = windowFixture(); target.focused = false;
  const source = fs.readFileSync(path.join(__dirname, '..', 'main.cjs'), 'utf8');
  const begin = source.indexOf("ipcMain.handle('igorek:completion-sound'");
  const end = source.indexOf("ipcMain.handle('igorek:notifications'", begin);
  let handler;
  vm.runInNewContext(source.slice(begin, end), { completionSound: sound,
    ipcMain: { handle: (_channel, callback) => { handler = callback; } },
    trustedChromeWindow: event => { if (!event.trusted) throw Error('denied'); return target; },
    sessionMcp: { trusted: event => event.chat === true },
    isOwnerFrame: event => event.owner === true, ownerWindow: {}, ownerUrl: 'file:///owner.html' });
  assert.equal(handler({ trusted: true }, 'read').enabled, true);
  assert.throws(() => handler({ trusted: false, owner: true }, 'set', false));
  assert.throws(() => handler({ trusted: true, chat: true }, 'set', false));
  assert.equal(handler({ trusted: true, chat: true }, 'claim', 'task'), true);
  assert.equal(handler({ trusted: true, owner: true }, 'set', false).enabled, false);
});
test('settings toggle uses persisted state and restores it if saving fails', async () => {
  const toggle = { checked: false, disabled: true, addEventListener: (_name, callback) => { toggle.change = callback; } };
  const status = { textContent: '' }; let listener, saved, fail = false;
  const context = { document: { getElementById: id => id === 'completion-sound-enabled' ? toggle : status },
    window: { IgorekCompletionSoundSettings: {
      read: async () => ({ enabled: false }), onChange: callback => { listener = callback; },
      set: async value => { if (fail) throw Error('failed'); saved = value; return { enabled: value }; },
    } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'completion-sound-settings.js'), 'utf8'), context);
  await Promise.resolve(); assert.equal(toggle.checked, false); assert.equal(toggle.disabled, false);
  toggle.checked = true; await toggle.change(); assert.equal(saved, true); assert.equal(toggle.checked, true);
  fail = true; toggle.checked = false; await toggle.change(); assert.equal(toggle.checked, true);
  assert.match(status.textContent, /Не удалось сохранить/);
  listener({ enabled: false }); assert.equal(toggle.checked, false);
});
