const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { ShortcutStore, SHORTCUTS, normalizeAccelerator } = require('../shortcuts.cjs');
const { matchesShortcut } = require('../shortcuts.cjs');
test('sidebar shortcut matches physical keys in a website without consuming unrelated shortcuts',()=>{
  const input={type:'keyDown',code:'KeyS',key:'Ы',control:true,shift:true,alt:false,meta:false};
  assert.equal(matchesShortcut(input,['CmdOrCtrl+Shift+S'],'linux'),true);
  assert.equal(matchesShortcut({...input,alt:true},['CmdOrCtrl+Shift+S'],'linux'),false);
  assert.equal(matchesShortcut({...input,code:'KeyK'},['CmdOrCtrl+Shift+S'],'linux'),false);
  assert.equal(matchesShortcut({...input,isAutoRepeat:true},['CmdOrCtrl+Shift+S'],'linux'),false);
});
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'igorek-keys-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return path.join(root, 'keys.json');
}
test('defaults are unique and include sidebar, existing aliases and applicable chat actions', t => {
  const store = new ShortcutStore(fixture(t));
  assert.deepEqual(store.keys('sidebar'), ['CmdOrCtrl+Shift+S']);
  assert.deepEqual(store.keys('newSession'), ['CmdOrCtrl+N', 'CmdOrCtrl+K']);
  assert.deepEqual(store.keys('recentChat9'), ['CmdOrCtrl+Alt+9']);
  assert.equal(new Set(SHORTCUTS.map(item => item.id)).size, SHORTCUTS.length);
  for (const item of store.snapshot().items) assert.ok(item.title && item.description && item.keys.length);
});
test('custom keys, additional aliases and cleared assignments survive restart; reset restores defaults', t => {
  const file = fixture(t), store = new ShortcutStore(file);
  store.set('sidebar', ['Ctrl+Alt+S', 'Alt+Shift+S']);
  store.set('terminal', []);
  const restored = new ShortcutStore(file);
  assert.deepEqual(restored.keys('sidebar'), ['CmdOrCtrl+Alt+S', 'Alt+Shift+S']);
  assert.deepEqual(restored.keys('terminal'), []);
  restored.reset();
  assert.deepEqual(new ShortcutStore(file).keys('terminal'), ['CmdOrCtrl+`']);
});
test('conflicting assignments are rejected before modifying file or active configuration', t => {
  const file = fixture(t), store = new ShortcutStore(file);
  store.set('sidebar', ['Ctrl+Alt+S']); const before = fs.readFileSync(file, 'utf8');
  assert.throws(() => store.set('sidebar', ['Ctrl+N']), /уже назначено/);
  assert.equal(fs.readFileSync(file, 'utf8'), before);
  assert.deepEqual(store.keys('sidebar'), ['CmdOrCtrl+Alt+S']);
});
test('invalid payloads, unknown actions, duplicates and reserved editing keys are rejected', t => {
  const store = new ShortcutStore(fixture(t));
  for (const keys of [null, 'Ctrl+S', ['Ctrl+C'], ['S'], ['Ctrl+Ctrl+S'], ['Ctrl+Alt+S', 'CmdOrCtrl+Alt+S'], Array(5).fill('F1'), ['Ctrl+__proto__']])
    assert.throws(() => store.set('sidebar', keys));
  assert.throws(() => store.set('__proto__', ['Ctrl+Alt+S']));
  assert.equal(normalizeAccelerator('Shift+Ctrl+S'), 'CmdOrCtrl+Shift+S');
});
test('corrupt or conflicting stored configuration safely restores known defaults', t => {
  const file = fixture(t);
  for (const payload of ['invalid', 'null', '[]', '{"sidebar":["Ctrl+N"]}', '{"sidebar":["Ctrl+C"]}']) {
    fs.writeFileSync(file, payload);
    assert.deepEqual(new ShortcutStore(file).keys('sidebar'), ['CmdOrCtrl+Shift+S']);
  }
  fs.writeFileSync(file, '{"unknown":["Alt+S"],"sidebar":["Alt+S"]}');
  assert.deepEqual(new ShortcutStore(file).keys('sidebar'), ['Alt+S']);
});
