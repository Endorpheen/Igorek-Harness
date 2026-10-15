const { open } = require('node:fs/promises');
const { constants } = require('node:fs');
const MAX_BYTES = 16 * 1024 * 1024;

async function readRecovery(path) {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > MAX_BYTES) throw new Error('invalid_file');
    const buffer = Buffer.alloc(MAX_BYTES + 1);
    let length = 0;
    while (length < buffer.length) {
      const { bytesRead } = await file.read(buffer, length, buffer.length - length, null);
      if (!bytesRead) break;
      length += bytesRead;
    }
    if (!length || length > MAX_BYTES) throw new Error('invalid_file');
    return buffer.subarray(0, length);
  } finally { await file.close(); }
}

async function verifyRecovery(runtime, path, recovery_phrase) {
  const backup = await readRecovery(path);
  return runtime.requestOwner('verify_recovery', { backup: backup.toString('base64'), recovery_phrase });
}

async function restoreRecovery(runtime, path, fields) {
  const backup = await readRecovery(path);
  return runtime.requestOwner('restore_recovery', { backup: backup.toString('base64'),
    recovery_phrase: fields.recovery_phrase, new_password: fields.new_password });
}

async function saveRecovery(runtime, path, fields) {
  const reply = await runtime.requestOwner('export_recovery', fields);
  if (reply.error) return reply;
  const encoded = reply.result?.backup;
  if (typeof encoded !== 'string' || encoded.length > 22369624 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded))
    throw new Error('invalid_backup');
  const backup = Buffer.from(encoded, 'base64');
  if (!backup.length || backup.length > MAX_BYTES) throw new Error('invalid_backup');
  const file = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
  try { await file.writeFile(backup); await file.sync(); }
  finally { await file.close(); }
  try {
    const verified = await verifyRecovery(runtime, path, fields.recovery_phrase);
    if (verified.error || verified.result?.verified !== true) return { error: 'saved_not_verified', path };
    return { result: { ...verified.result, path } };
  } catch { return { error: 'saved_not_verified', path }; }
}
module.exports = { readRecovery, verifyRecovery, saveRecovery, restoreRecovery, MAX_BYTES };
