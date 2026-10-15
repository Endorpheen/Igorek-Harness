const { assertPrivatePermissions } = require('./helpers/private-permissions.cjs');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mkdtempSync, rmSync, statSync, readFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { NotificationStore, safeText } = require('../notifications.cjs');

test('notification history persists, deduplicates, marks read and clears', () => {
  const root = mkdtempSync(join(tmpdir(), 'igorek-notifications-'));
  const path = join(root, 'history.json');
  try {
    const store = new NotificationStore(path);
    store.add({ source: 'Квота', level: 'error', detail: 'HTTP 401' });
    store.add({ source: 'Квота', level: 'error', detail: 'HTTP 401' });
    assert.equal(store.snapshot().length, 1);
    assert.equal(store.snapshot()[0].count, 2);
    assertPrivatePermissions(path, 0o600);
    assert.equal(new NotificationStore(path).snapshot()[0].detail, 'HTTP 401');
    store.read(); assert.equal(new NotificationStore(path).snapshot()[0].read, true);
    store.add({ source: 'Квота', level: 'error', detail: 'HTTP 401' });
    assert.equal(store.snapshot()[0].read, false);
    for (let index = 0; index < 110; index++) store.add({ level: 'info', detail: String(index) });
    assert.equal(store.snapshot().length, 100);
    store.clear(); assert.deepEqual(new NotificationStore(path).snapshot(), []);
    assert.throws(() => store.add({ level: 'error', detail: {} }));
    assert.throws(() => store.add({ level: 'invalid', detail: 'text' }));
  } finally { rmSync(root, { recursive: true, force: true }); }
});
test('history omits credentials and URL query strings without interpreting error prose', () => {
  const text = safeText('HTTP 401 Bearer abcdef access_token="private-token" password=hidden https://user:pass@example.com/path?code=secret#token');
  assert.match(text, /HTTP 401/);
  for (const secret of ['abcdef', 'private-token', 'hidden', 'user:pass', 'code=secret', '#token']) assert.ok(!text.includes(secret), secret);
  assert.equal(safeText('<script>alert(1)</script>'), '<script>alert(1)</script>');
});
