const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../app/src/main/assets/voice/call-ui.js'), 'utf8');
function harness() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, { textContent: '', open: false, attributes: {}, classList: { toggle() {} }, setAttribute(k, v) { this.attributes[k] = v; }, addEventListener() {} });
    return elements.get(id);
  }
  const calls = []; const intervals = []; const timeouts = []; let now = 0;
  const context = { window: {}, document: { getElementById: element, querySelector: () => null, body: element('body') }, Native: { request: (...args) => calls.push(['request', ...args]), state: () => {}, speaking: () => {}, ended: () => calls.push(['ended']), unavailable: (value) => calls.push(['unavailable', value]) }, performance: { now: () => now }, setTimeout: (cb) => timeouts.push(cb), setInterval: (cb) => intervals.push(cb), AudioContext: class { state = 'running'; createAnalyser() { return { fftSize: 256 }; } resume() { return Promise.resolve(); } }, Uint8Array, Promise, JSON };
  context.window.IgorekVoice = { begin: async () => calls.push(['begin']), init() {}, onSessionChanged() {}, onSessionEvent() {} };
  vm.runInNewContext(script, context);
  return { ...context, calls, intervals, timeouts, element, setNow(value) { now = value; } };
}
test('enabled config starts exactly once after the configuration promise resolves', async () => {
  const h = harness();
  const request = h.window.fetch('/api/voice/config');
  h.window.nativeReply(1, 200, { enabled: true });
  assert.equal((await request).ok, true);
  assert.equal(h.calls.filter(c => c[0] === 'begin').length, 0);
  h.timeouts.shift()();
  await Promise.resolve();
  assert.equal(h.calls.filter(c => c[0] === 'begin').length, 1);
  h.window.fetch('/api/voice/config');
  h.window.nativeReply(2, 200, { enabled: true });
  assert.equal(h.timeouts.length, 0);
});
test('unavailable voice never opens the microphone', async () => {
  const h = harness();
  const request = h.window.fetch('/api/voice/config');
  h.window.nativeReply(1, 200, { enabled: false, reason: 'not_configured' });
  await request;
  assert.deepEqual(h.calls.filter(c => c[0] === 'unavailable'), [['unavailable', 'not_configured']]);
  assert.equal(h.timeouts.length, 0);
});
test('timer uses real connection and survives reconnection, approval remains visible', () => {
  const h = harness();
  const state = { phase: 'listening', connected: false, notice: 'Слушаю', approval: false };
  h.window.IgorekCall.stateChanged(state);
  assert.equal(h.element('call-timer').textContent, 'Соединяем…');
  h.setNow(1000); h.window.IgorekCall.stateChanged({ ...state, connected: true });
  h.setNow(66000); h.intervals[0]();
  assert.equal(h.element('call-timer').textContent, '01:05');
  h.window.IgorekCall.stateChanged({ ...state, phase: 'reconnecting' });
  h.setNow(68000); h.window.IgorekCall.stateChanged({ ...state, connected: true, approval: true, micMuted: true, micMode: 'continuous' });
  assert.equal(h.element('call-timer').textContent, '01:07');
  assert.equal(h.element('conversation').open, true);
  assert.equal(h.element('conversation-summary').textContent, 'Нужно подтверждение');
  assert.equal(h.element('voice-mic').attributes['aria-label'], 'Включить микрофон');
});

test('waiting status distinguishes agent latency from listening and stops at the first answer', () => {
  const h = harness();
  const state = { phase: 'listening', connected: true, notice: 'Слушаю', approval: false, waitingForAgent: true, pendingCount: 1 };
  h.window.IgorekCall.stateChanged(state);
  assert.equal(h.element('voice-status').textContent, 'Жду первого ответа Игорька…');
  h.window.IgorekCall.stateChanged({ ...state, waitingForAgent: false });
  assert.equal(h.element('voice-status').textContent, 'Слушаю');
});
test('PTT labels show hold, recording and release states without enabling the microphone by clicking', () => {
  const h = harness();
  const state = { phase: 'listening', connected: true, notice: 'Слушаю', micMode: 'ptt', pttRecording: false, micMuted: true };
  h.window.IgorekCall.stateChanged(state);
  assert.equal(h.element('voice-mic-label').textContent, 'Зажми и говори');
  assert.equal(h.element('voice-mic').attributes['aria-label'], 'Зажми, чтобы говорить');
  h.window.IgorekCall.stateChanged({ ...state, pttRecording: true, micMuted: false });
  assert.equal(h.element('voice-mic-label').textContent, 'Говори');
  assert.equal(h.element('voice-mic').attributes['aria-label'], 'Отпусти, чтобы отправить');
});
