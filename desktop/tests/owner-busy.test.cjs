const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

function ownerHandler(requestOwner, trusted = true) {
  const source = readFileSync(join(__dirname, '../main.cjs'), 'utf8');
  const begin = source.indexOf("ipcMain.handle('igorek:owner-vault'");
  const end = source.indexOf('\nasync function openWindow()', begin);
  assert.ok(begin >= 0 && end > begin);
  let handler;
  vm.runInNewContext(source.slice(begin, end), {
    ipcMain: { handle: (_, value) => { handler = value; } }, managed: true,
    isOwnerFrame: () => trusted, ownerWindow: { webContents: {} }, ownerUrl: 'app://owner',
    installingUpdate: false, quitting: false, runtime: { requestOwner, requests: new Map() },
    shouldRefreshProfiles: () => false, refreshChatProfiles() {},
  });
  return handler;
}

function preload(invokeOwner) {
  const exposed = {}, notifications = [];
  const electron = {
    contextBridge: { exposeInMainWorld: (name, value) => { exposed[name] = value; } },
    ipcRenderer: {
      on() {},
      invoke: async (channel, ...args) => {
        if (channel === 'igorek:notifications') { notifications.push(args); return []; }
        return invokeOwner(channel, ...args);
      },
    },
  };
  vm.runInNewContext(readFileSync(join(__dirname, '../owner-preload.cjs'), 'utf8'), {
    require: name => { assert.equal(name, 'electron'); return electron; },
  });
  return { vault: exposed.igorekVault, notifications };
}

test('owner IPC returns a busy code without losing owner-frame enforcement', async () => {
  const busy = Object.assign(new Error('Дождитесь завершения операции.'), { code: 'operation_busy' });
  const handler = ownerHandler(async () => { throw busy; });
  assert.equal((await handler({}, 'readiness')).error, 'operation_busy');
  await assert.rejects(ownerHandler(async () => {}, false)({}, 'readiness'), /Доступ запрещён/);
});

test('busy settings responses do not become error notifications', async () => {
  const handler = ownerHandler(async () => {
    throw Object.assign(new Error('Дождитесь завершения операции.'), { code: 'operation_busy' });
  });
  const f = preload((_, action, fields) => handler({}, action, fields));
  assert.equal((await f.vault.readiness()).error, 'operation_busy');
  assert.equal(f.notifications.length, 0);
});

test('real settings errors remain visible in notifications', async () => {
  const f = preload(async () => ({ error: 'vault_locked' }));
  assert.equal((await f.vault.status()).error, 'vault_locked');
  assert.equal(f.notifications.length, 1);
  assert.equal(f.notifications[0][1].detail, 'vault_locked');
  const unavailable = preload(async () => { throw new Error('Backend недоступен.'); });
  await assert.rejects(unavailable.vault.status(), /Backend недоступен/);
  assert.equal(unavailable.notifications.length, 1);
  const main = ownerHandler(async () => { throw new Error('Backend недоступен.'); });
  await assert.rejects(main({}, 'readiness'), /Backend недоступен/);
});
