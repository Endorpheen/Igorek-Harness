'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const { spawnSync } = require('node:child_process');
const base = process.env.IGOREK_DEPENDENCY_ROOT || path.resolve(__dirname, '..');
const load = createRequire(path.join(base, 'package.json'));
const semver = load('semver');
const uri = load('fast-uri');
const lock = JSON.parse(fs.readFileSync(path.join(base, 'package-lock.json'), 'utf8'));
const unsafe = {
  electron: '<42.11.16 || >=43.0.0-alpha.1 <43.8.1 || >=44.0.0-alpha.1 <44.9.1 || >=45.0.0-alpha.1 <45.0.0-beta.6',
  undici: '>=6.23.0 <6.29.1 || >=7.18.0 <7.30.1 || >=8.0.0 <8.12.2',
  'fast-uri': '<2.4.8 || >=3.0.0 <3.1.9 || >=4.0.0 <4.2.3',
};
test('locked copies exclude the reviewed vulnerable dependency ranges', () => {
  const found = new Set();
  for (const [name, item] of Object.entries(lock.packages)) {
    const key = item.name || name.split('/node_modules/').pop().replace(/^node_modules\//, '');
    if (!unsafe[key]) continue;
    found.add(key);
    assert.ok(!semver.satisfies(item.version, unsafe[key], { includePrerelease: true }), `${name}@${item.version} is vulnerable`);
  }
  assert.deepEqual([...found].sort(), ['electron', 'fast-uri', 'undici']);
});
for (const input of ['%6Aavascript:fixture', '%u006Aavascript:fixture', '%64ata:text/plain,fixture']) {
  test(`encoded scheme cannot become an active scheme: ${input}`, () => {
    let parsed;
    try { parsed = uri.parse(input); } catch { return; }
    assert.ok(parsed.error || !['javascript', 'data', 'vbscript'].includes(parsed.scheme));
    let normalized;
    try { normalized = uri.normalize(input); } catch { return; }
    assert.ok(!/^(?:javascript|data|vbscript):/i.test(normalized));
  });
}
test('encoded and plain HTTP schemes are not equivalent', () => {
  assert.equal(uri.equal('%68ttp://a.example/', 'http://a.example/'), false);
});
for (const reference of ['a/../b', '../x']) {
  test(`relative normalization retains resource identity: ${reference}`, () => {
    const baseUri = 'https://example.invalid/one/two/';
    assert.equal(uri.resolve(baseUri, uri.normalize(reference)), uri.resolve(baseUri, reference));
  });
}
for (const input of ['http://a@b@evil.example/', 'http://exa\tmple.invalid/']) {
  test('ambiguous authority is explicitly rejected', () => {
    assert.ok(uri.parse(input).error);
  });
}
test('suffix and skipEscape keep colon-containing relative paths relative', () => {
  const baseUri = 'https://example.invalid/one/two/';
  for (const reference of ['./a:b', 'a/../a:b']) {
    for (const options of [{ reference: 'suffix', scheme: 'x' }, { skipEscape: true }]) {
      const normalized = uri.normalize(reference, options);
      assert.equal(uri.parse(normalized).scheme, undefined);
      assert.equal(uri.resolve(baseUri, normalized), uri.resolve(baseUri, reference));
    }
  }
  assert.equal(uri.normalize('custom:fixture'), 'custom:fixture');
});
test('ordinary HTTPS and relative references remain supported', () => {
  assert.equal(uri.parse('https://example.invalid/a?b=1').host, 'example.invalid');
  assert.equal(uri.resolve('https://example.invalid/a/', '../b'), 'https://example.invalid/b');
});
const embeddedElectron = load('electron');
test('installed Electron is the patched, locked distribution', () => {
  const result = spawnSync(embeddedElectron, ['-p', 'JSON.stringify(process.versions)'], {
    encoding: 'utf8', timeout: 5000, env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
  });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr);
  const versions = JSON.parse(result.stdout.trim());
  assert.equal(versions.electron, lock.packages['node_modules/electron'].version);
  assert.ok(!semver.satisfies(versions.electron, unsafe.electron, { includePrerelease: true }));
  console.log('Electron runtime versions:', JSON.stringify({ electron: versions.electron, node: versions.node, undici: versions.undici }));
});
const copies = [
  ['download helper', load.resolve('undici'), process.execPath, false],
  ['node-gyp', createRequire(load.resolve('node-gyp/package.json')).resolve('undici'), process.execPath, false],
  ['embedded Electron updater fetch', path.join(base, 'release-updates.cjs'), embeddedElectron, true],
];
const probe = String.raw`
const http = require('node:http');
const zlib = require('node:zlib');
const loaded = require(process.argv[1]);
const fetch = loaded.ReleaseUpdates ? new loaded.ReleaseUpdates({ current: '0.2.0', cache: '.' }).fetch : loaded.fetch;
const mode = process.argv[2];
const server = http.createServer((req, res) => {
  if (mode === 'control') {
    const body = zlib.gzipSync(Buffer.from('fixture-body'));
    res.writeHead(200, { 'content-encoding': 'gzip', 'content-length': body.length });
    res.end(body);
  } else {
    const coding = mode === 'gzip' ? 'gzip' : 'deflate';
    res.writeHead(200, { 'content-encoding': Array(6).fill(coding).join(', '), 'content-length': '1', connection: 'close' });
    res.flushHeaders();
    res.end();
  }
});
server.listen(0, '127.0.0.1', async () => {
  try {
    const response = await fetch('http://127.0.0.1:' + server.address().port, { signal: AbortSignal.timeout(1500) });
    const body = await response.text();
    if (mode === 'control' && body !== 'fixture-body') process.exitCode = 4;
    if (mode !== 'control') process.exitCode = 5;
  } catch {
    if (mode === 'control') process.exitCode = 6;
  } finally {
    setTimeout(() => server.close(() => console.log('owned-probe-completed')), 100);
  }
});
`;
for (const [label, entry, executable, embedded] of copies) {
  for (const mode of ['gzip', 'deflate', 'control']) {
    test(`${label}: ${mode} does not cause an unhandled process error`, () => {
      const result = spawnSync(executable, ['-e', probe, entry, mode], { encoding: 'utf8', timeout: 5000, env: { ...process.env, ...(embedded ? { ELECTRON_RUN_AS_NODE: '1' } : {}) } });
      assert.equal(result.error, undefined);
      assert.equal(result.status, 0, result.stderr);
      assert.match(result.stdout, /owned-probe-completed/);
    });
  }
}
