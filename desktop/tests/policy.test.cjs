const { test } = require('node:test');
const assert = require('node:assert/strict');
const { localOrigin, sameOrigin, allowAudio, certificateMatches, allowClipboardWrite } = require('../policy.cjs');
test('clipboard writing does not grant clipboard reading or other permissions', () => {
  assert.equal(allowClipboardWrite('clipboard-sanitized-write'), true);
  for (const permission of ['clipboard-read', 'clipboard-read-write', 'media', 'notifications', undefined])
    assert.equal(allowClipboardWrite(permission), false);
});
test('only exact loopback HTTPS origins are accepted', () => {
  assert.equal(localOrigin('https://127.0.0.1:5177'), 'https://127.0.0.1:5177');
  assert.equal(localOrigin('https://[::1]:5177'), 'https://[::1]:5177');
  for (const value of ['http://localhost', 'https://example.com', 'https://localhost.evil',
    'https://user:pass@localhost', 'https://localhost/api', 'https://localhost/?token=secret']) {
    assert.throws(() => localOrigin(value));
  }
});
test('navigation cannot cross host, scheme or port', () => {
  const origin = 'https://127.0.0.1:5177';
  assert.equal(sameOrigin(origin + '/api/file', origin), true);
  for (const value of ['https://127.0.0.1:5178', 'http://127.0.0.1:5177', 'file:///etc/passwd', 'javascript:alert(1)']) {
    assert.equal(sameOrigin(value, origin), false);
  }
});
test('only explicit audio requests can prompt for permission', () => {
  assert.equal(allowAudio('media', { mediaTypes: ['audio'] }), true);
  for (const types of [[], ['video'], ['audio', 'video'], undefined]) {
    assert.equal(allowAudio('media', { mediaTypes: types }), false);
  }
  assert.equal(allowAudio('display-capture', { mediaTypes: ['audio'] }), false);
});
test('invalid certificate never matches', () => {
  assert.equal(certificateMatches('invalid', 'abc'), false);
});
const { mkdtempSync, readFileSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { execFileSync } = require('node:child_process');
const { fingerprint, settings } = require('../policy.cjs');
test('pin accepts only the configured certificate and settings contain no private key', () => {
  const dir = mkdtempSync(join(tmpdir(), 'igorek-cert-test-'));
  try {
    for (const name of ['a', 'b']) {
      execFileSync('openssl', ['req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
        '-nodes', '-keyout', join(dir, name + '.key'), '-out', join(dir, name + '.pem'),
        '-subj', '/CN=localhost', '-days', '1'], { stdio: 'ignore' });
    }
    const a = readFileSync(join(dir, 'a.pem'));
    const b = readFileSync(join(dir, 'b.pem'));
    assert.equal(certificateMatches(a, fingerprint(a)), true);
    assert.equal(certificateMatches(b, fingerprint(a)), false);
    const config = settings(['--url', 'https://localhost:5177', '--cert', join(dir, 'a.pem')], {});
    assert.deepEqual(config, { origin: 'https://localhost:5177', fingerprint: fingerprint(a) });
    assert.throws(() => settings(['--cert'], {}));
    assert.throws(() => settings(['--unknown'], {}));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
