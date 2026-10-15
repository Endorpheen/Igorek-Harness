const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

test('the build downloader preserves environment proxy routing after upgrading fetch', async t => {
  let connections = 0;
  const sockets = new Set();
  const proxy = http.createServer();
  proxy.on('connect', (request, socket) => {
    assert.equal(request.url, 'igorek-download.invalid:80');
    connections++;
    sockets.add(socket);socket.once('close', () => sockets.delete(socket));
    socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
    socket.once('data', () => socket.end('HTTP/1.1 200 OK\r\nContent-Length: 13\r\nConnection: close\r\n\r\nproxy-fixture'));
  });
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
  t.after(() => { for (const socket of sockets) socket.destroy();proxy.close(); });
  const address = 'http://127.0.0.1:' + proxy.address().port;
  const script = "require('./electron-builder.cjs');fetch('http://igorek-download.invalid/fixture').then(r=>r.text()).then(t=>{if(t!=='proxy-fixture')process.exit(2);console.log('proxy-ok')}).catch(()=>process.exit(3))";
  const result = await promisify(execFile)(process.execPath, ['-e', script], {
    cwd: path.resolve(__dirname, '..'), timeout: 10000,
    env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot,
      IGOREK_DESKTOP_APP_DIR: path.resolve(__dirname), HTTP_PROXY: address, HTTPS_PROXY: address,
      NO_PROXY: '', no_proxy: '', ELECTRON_GET_NO_PROGRESS: '1' },
  });
  assert.equal(result.stdout.trim(), 'proxy-ok');
  assert.equal(connections, 1);
});
