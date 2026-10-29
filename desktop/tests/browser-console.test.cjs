const {test} = require('node:test');
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const vm = require('node:vm');
const {BrowserConsole, validConsole, evaluationScript} = require('../browser-console.cjs');

class Journal {
  constructor() { this.rows = []; this.seq = 0; }
  push(row) { this.rows.push({...row, seq: ++this.seq}); }
  clear() { this.rows = []; }
  read() { return {entries: this.rows.map(row => ({...row})), last_seq: this.seq, dropped: 0}; }
}

function fixture() {
  const wc = new EventEmitter(); wc.isDestroyed = () => false;
  const debug = wc.debugger = new EventEmitter();
  debug.attached = false; debug.calls = [];
  debug.isAttached = () => debug.attached;
  debug.attach = version => { assert.equal(version, '1.3'); debug.attached = true; };
  debug.detach = () => { debug.attached = false; debug.emit('detach'); };
  debug.handler = async () => ({result: {value: JSON.stringify({ok: true, value: 42})}});
  debug.sendCommand = (method, args) => { debug.calls.push({method, args}); return debug.handler(method, args); };
  return {wc, debug, console: new BrowserConsole(wc, {Journal})};
}

test('console contract rejects malformed or mixed action fields', () => {
  for (const value of [null, [], {}, {action: 'console', expression: ''},
    {action: 'console', expression: '1', timeout_ms: true}, {action: 'console', expression: '1', timeout_ms: 5001},
    {action: 'console_messages', after: -1}, {action: 'console_messages', limit: 201},
    {action: 'console_messages', level: 'unknown'}, {action: 'console_clear', expression: '1'}]) {
    assert.equal(validConsole(value), false);
  }
});

test('execution returns the value and releases only its owned debugger', async () => {
  const f = fixture();
  const result = await f.console.run({action: 'console', expression: '6 * 7'});
  assert.equal(result.value, 42); assert.equal(f.debug.attached, false);
  const request = f.debug.calls.find(row => row.method === 'Runtime.evaluate').args;
  assert.equal(request.userGesture, false); assert.equal(request.timeout, 3000);
  assert.equal(request.awaitPromise, true); assert.equal(request.returnByValue, true);
  assert(f.debug.calls.some(row => row.method === 'Runtime.releaseObjectGroup'));
});

test('page wrapper preserves plain values without calling object accessors', async () => {
  const result = await vm.runInNewContext(evaluationScript("({answer:42, get hidden(){throw Error('must not run')}})"));
  assert.deepEqual(JSON.parse(result), {ok: true, value: {answer: 42, hidden: '[accessor]'}});
  const rejected = JSON.parse(await vm.runInNewContext(evaluationScript("Promise.reject(Error('fixture'))")));
  assert.equal(rejected.ok, false); assert.equal(rejected.exception.message, 'fixture');
});

test('page wrapper bounds cyclic and very large values before transport', async () => {
  const result = await vm.runInNewContext(evaluationScript("(()=>{const a={};a.self=a;a.rows=Array.from({length:100000},()=>({text:'x'.repeat(10000)}));return a})()"));
  assert(result.length < 65536);
  const parsed = JSON.parse(result); assert.equal(parsed.value.self, '[circular]');
});

test('document changes discard a pending result instead of leaking stale data', async () => {
  const f = fixture(); let finish;
  f.debug.handler = method => method === 'Runtime.evaluate' ? new Promise(resolve => {finish = resolve;}) : Promise.resolve({});
  const pending = f.console.run({action: 'console', expression: 'privateValue'});
  f.wc.emit('did-start-navigation', {isMainFrame: true});
  finish({result: {value: JSON.stringify({ok: true, value: 'must not escape'})}});
  assert.deepEqual(await pending, {error: 'browser_changed'});
});

test('lock and manual handoff are checked again after evaluation', async () => {
  for (const reason of ['desktop_locked', 'browser_needs_owner']) {
    const f = fixture(); let finish, blocked = null;
    f.debug.handler = method => method === 'Runtime.evaluate' ? new Promise(resolve => {finish = resolve;}) : Promise.resolve({});
    const pending = f.console.run({action: 'console', expression: '1'}, () => blocked);
    blocked = reason; finish({result: {value: JSON.stringify({ok: true, value: 1})}});
    assert.deepEqual(await pending, {error: reason});
  }
});

test('caller timeout returns, terminates its evaluation and releases the debugger', async () => {
  const f = fixture();
  f.debug.handler = method => method === 'Runtime.evaluate' ? new Promise(() => {}) : Promise.resolve({});
  assert.deepEqual(await f.console.run({action: 'console', expression: 'pending', timeout_ms: 100}), {error: 'console_timeout'});
  assert(f.debug.calls.some(row => row.method === 'Runtime.terminateExecution'));
  assert.equal(f.debug.attached, false);
});

test('foreign debugger is not detached and concurrent owner calls are refused', async () => {
  const f = fixture(); f.debug.attached = true;
  assert.deepEqual(await f.console.run({action: 'console', expression: '1'}), {error: 'console_debugger_busy'});
  assert.equal(f.debug.attached, true); assert.equal(f.debug.calls.length, 0);
  f.debug.attached = false; let finish;
  f.debug.handler = method => method === 'Runtime.evaluate' ? new Promise(resolve => {finish = resolve;}) : Promise.resolve({});
  const pending = f.console.run({action: 'console', expression: '1'});
  assert.deepEqual(await f.console.run({action: 'console', expression: '2'}), {error: 'console_busy'});
  finish({result: {value: '{"ok":true,"value":1}'}}); await pending;
});

test('message hooks support Electron details, reset on navigation and detach on destruction', async () => {
  const f = fixture();
  f.wc.emit('console-message', {message: 'current', level: 'error', lineNumber: 4, sourceId: 'https://example.com'});
  assert.equal((await f.console.run({action: 'console_messages'})).entries[0].message, 'current');
  f.wc.emit('did-start-navigation', {isMainFrame: false});
  assert.equal((await f.console.run({action: 'console_messages'})).entries.length, 1);
  f.wc.emit('did-start-navigation', {isMainFrame: true});
  assert.equal((await f.console.run({action: 'console_messages'})).entries.length, 0);
  f.wc.emit('destroyed'); assert.equal(f.wc.listenerCount('console-message'), 0);
  assert.deepEqual(await f.console.run({action: 'console_messages'}), {error: 'browser_changed'});
});


test('Chromium internal error at its deadline is a timeout, while an immediate failure is not', async () => {
  for (const delay of [0, 110]) {
    const f = fixture();
    f.debug.handler = async method => {
      if (method !== 'Runtime.evaluate') return {};
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      throw Error('Internal error');
    };
    const result = await f.console.run({action: 'console', expression: '1', timeout_ms: 100});
    assert.equal(result.error, delay ? 'console_timeout' : 'console_evaluation_failed');
    assert.equal(f.debug.calls.some(row => row.method === 'Runtime.terminateExecution'), Boolean(delay));
    assert.equal(f.debug.attached, false);
  }
});
