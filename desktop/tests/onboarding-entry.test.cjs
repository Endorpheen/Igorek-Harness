const { test } = require('node:test');
const assert = require('node:assert/strict');
const { ownerOpenCodeHandler } = require('../owner-policy.cjs');

function fixture(status = { state: 'unlocked', agent: { running: false } }) {
  const calls = [];
  const contents = { isDestroyed: () => false, mainFrame: { url: 'file:///owner.html' } };
  const settings = { webContents: contents, isDestroyed: () => false, close: () => calls.push('close') };
  const target = { isDestroyed: () => false, focus: () => calls.push('focus'), webContents: {
    getURL: () => 'https://127.0.0.1:1234', executeJavaScript: async script => calls.push(script),
  } };
  const options = { owner: () => settings, main: () => target, ownerUrl: contents.mainFrame.url,
    origin: () => target.webContents.getURL(), sameOrigin: (a, b) => a === b,
    available: () => true, requestStatus: async () => { calls.push('status'); return { result: status }; } };
  return { options, settings, target, calls, event: { sender: contents, senderFrame: contents.mainFrame } };
}

test('OpenCode opens with no primary model, worker or completed onboarding', async () => {
  const f = fixture();
  assert.equal(await ownerOpenCodeHandler(f.options)(f.event), true);
  assert.deepEqual(f.calls, ['status', "window.dispatchEvent(new Event('igorek:open-opencode-tab'));", 'close', 'focus']);
});

for (const status of [{ state: 'locked' }, { state: 'not_initialized' }, { state: 'unlocked', interface_locked: true }]) {
  test('OpenCode entry retains Vault/interface protection: ' + JSON.stringify(status), async () => {
    const f = fixture(status);
    await assert.rejects(ownerOpenCodeHandler(f.options)(f.event), /Разблокируйте/);
    assert.deepEqual(f.calls, ['status']);
  });
}

test('foreign owners and child frames cannot open the workspace', async () => {
  for (const foreign of ['sender', 'senderFrame']) {
    const f = fixture(); f.event[foreign] = {};
    await assert.rejects(ownerOpenCodeHandler(f.options)(f.event), /Доступ запрещён/);
    assert.deepEqual(f.calls, []);
  }
});

test('late status replies cannot act on a replaced window', async () => {
  for (const replace of ['owner', 'main']) {
    const f = fixture();
    let current = f.options[replace]();
    f.options[replace] = () => current;
    f.options.requestStatus = async () => { current = {}; return { result: { state: 'unlocked' } }; };
    await assert.rejects(ownerOpenCodeHandler(f.options)(f.event));
    assert.deepEqual(f.calls, []);
  }
});

test('foreign main-window origins are refused before reading status', async () => {
  const f = fixture(); f.options.origin = () => 'https://other.invalid';
  await assert.rejects(ownerOpenCodeHandler(f.options)(f.event), /Доступ запрещён/);
  assert.deepEqual(f.calls, []);
});
