const { assertPrivatePermissions } = require('./helpers/private-permissions.cjs');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { readRecovery, saveRecovery, verifyRecovery, restoreRecovery, MAX_BYTES } = require('../recovery-files.cjs');

test('save reads the actual file for verification, sets private permissions and refuses overwrite', async () => {
  const root = await fs.mkdtemp(join(tmpdir(), 'igorek-backup-files-'));
  const path = join(root, 'backup.json'), bytes = Buffer.from('{"encrypted":"fixture"}');
  const calls = [];
  const runtime = { requestOwner: async (action, fields) => {
    calls.push(action);
    if (action === 'export_recovery') return { result: { backup: bytes.toString('base64') } };
    assert.equal(fields.backup, (await fs.readFile(path)).toString('base64'));
    assert.equal(fields.recovery_phrase, 'phrase');
    return { result: { verified: true, credential_count: 2 } };
  } };
  try {
    const result = await saveRecovery(runtime, path, { password: 'password', recovery_phrase: 'phrase' });
    assert.equal(result.result.verified, true);
    assert.deepEqual(calls, ['export_recovery', 'verify_recovery']);
    assertPrivatePermissions(path, 0o600);
    await assert.rejects(saveRecovery(runtime, path, { password: 'password', recovery_phrase: 'phrase' }), { code: 'EEXIST' });
    assert.deepEqual(await fs.readFile(path), bytes);
    assert.equal((await verifyRecovery(runtime, path, 'phrase')).result.credential_count, 2);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('export failure creates no file and a failed verification never reports success', async () => {
  const root = await fs.mkdtemp(join(tmpdir(), 'igorek-backup-errors-')), path = join(root, 'backup.json');
  try {
    const denied = { requestOwner: async () => ({ error: 'authentication_failed' }) };
    assert.equal((await saveRecovery(denied, path, {})).error, 'authentication_failed');
    await assert.rejects(fs.stat(path), { code: 'ENOENT' });
    const runtime = { requestOwner: async action => action === 'export_recovery'
      ? { result: { backup: Buffer.from('encrypted').toString('base64') } } : { error: 'recovery_failed' } };
    const result = await saveRecovery(runtime, path, { recovery_phrase: 'phrase' });
    assert.equal(result.error, 'saved_not_verified');
    assert.equal(result.path, path);
    assert.equal(await fs.readFile(path, 'utf8'), 'encrypted');
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('verification rejects symlinks, directories, empty and oversized files', async () => {
  const root = await fs.mkdtemp(join(tmpdir(), 'igorek-backup-limits-'));
  try {
    const path = join(root, 'file');
    await fs.writeFile(path, '');
    await assert.rejects(readRecovery(path));
    await fs.writeFile(path, 'fixture');
    const link = join(root, 'link'); await fs.symlink(path, link);
    await assert.rejects(readRecovery(link));
    await assert.rejects(readRecovery(root));
    const handle = await fs.open(path, 'w'); await handle.truncate(MAX_BYTES + 1); await handle.close();
    await assert.rejects(readRecovery(path));
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('restore sends bounded file contents and new password, never a destination path', async () => {
  const root = await fs.mkdtemp(join(tmpdir(), 'igorek-restore-file-'));
  try {
    const path = join(root, 'backup.json'); await fs.writeFile(path, 'encrypted-fixture');
    let calls = 0;
    const runtime = { requestOwner: async (action, fields) => {
      calls++;
      assert.equal(action, 'restore_recovery');
      assert.deepEqual(fields, { backup: Buffer.from('encrypted-fixture').toString('base64'),
        recovery_phrase: 'phrase', new_password: 'new-password' });
      return { result: { restored: true, state: 'unlocked', credential_count: 1 } };
    } };
    assert.equal((await restoreRecovery(runtime, path, { recovery_phrase: 'phrase', new_password: 'new-password' })).result.restored, true);
    await assert.rejects(restoreRecovery(runtime, join(root, 'missing'), { recovery_phrase: 'phrase', new_password: 'new-password' }));
    assert.equal(calls, 1);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
