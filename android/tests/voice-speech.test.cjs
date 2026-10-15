const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assets = path.join(__dirname, '../app/src/main/assets/voice');
async function harness({ policyReady = true, mode = "continuous", approvalReply = null } = {}) {
  const elements = new Map();
  class Element {
    hidden = false; style = {}; children = []; textContent = ''; classList = { toggle() {}, remove() {} };
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    get childElementCount() { return this.children.length; }
    listeners = {};
    addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
    fire(type, props = {}) { for (const fn of this.listeners[type] || []) fn({ button: 0, isPrimary: true, pointerId: 1, preventDefault() {}, ...props }); }
    setPointerCapture() {} setAttribute() {}
    getBoundingClientRect() { return { width: 360, height: 640 }; }
  }
  function element(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); }
  const sent = []; const posts = []; const states = []; const traces = [];
  const timers = new Map(); const windows = {}; let clock = 0; let timerId = 0;
  const track = { enabled: true, stop() {} };
  let peer; let constraints; let serial = 0;
  class Peer {
    connectionState = 'connected'; iceGatheringState = 'complete'; signalingState = 'stable'; localDescription = { sdp: 'local' };
    constructor() { peer = this; }
    addTrack() {} createDataChannel() { return this.dc = { readyState: 'open', send: (raw) => sent.push(JSON.parse(raw)), close() {} }; }
    async createOffer() { return { sdp: 'local' }; } async setLocalDescription() {} async setRemoteDescription() {}
    close() { this.connectionState = 'closed'; }
  }
  const context = {
    window: { innerWidth: 360, innerHeight: 640, addEventListener(type, listener) { windows[type] = listener; }, localStorage: { getItem: () => null }, IgorekI18n: { t: (key) => key }, IgorekCall: { stateChanged: (s) => states.push({ ...s }), trace: (e) => traces.push(e), ended() {} } },
    document: { getElementById: element, createElement: () => new Element(), addEventListener() {}, body: new Element() },
    navigator: { mediaDevices: { getUserMedia: async (value) => { constraints = value; return { getAudioTracks: () => [track] }; } } },
    RTCPeerConnection: Peer, crypto: { randomUUID: () => 'utterance-' + (++serial) },
    setInterval: () => 1, clearInterval() {}, setTimeout: (fn) => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: (id) => timers.delete(id), performance: { now: () => clock }, Map, Set, Uint8Array, JSON, Date,
    fetch: async (url, opts = {}) => {
      if (opts.body) posts.push({ url, body: JSON.parse(opts.body) });
      if (url.includes('/approvals/') && approvalReply) return approvalReply();
      const payload = url.endsWith('/config') ? { enabled: true } : url.endsWith('/session') ? { call_id: 'local-call' } : url.endsWith('/sdp') ? { sdp: 'answer' } : url.endsWith('/messages') ? { task_id: 'task-' + posts.filter(p => p.url.endsWith('/messages')).length } : {};
      return { ok: true, json: async () => payload };
    },
  };
  context.window.crypto = context.crypto;
  context.window.setTimeout = context.setTimeout; context.window.clearTimeout = context.clearTimeout;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(assets, 'speech-policy.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(assets, 'voice.js'), 'utf8'), context);
  context.window.IgorekVoice.init(); context.window.IgorekVoice.onSessionChanged('chat-test');
  for (let i = 0; i < 8; i++) await Promise.resolve();
  await context.window.IgorekVoice.begin();
  assert.ok(peer, JSON.stringify(states));
  peer.dc.onopen();
  const event = (value) => peer.dc.onmessage({ data: JSON.stringify(value) });
  const ready = () => event({ type: 'session.updated', session: sent.filter(e => e.type === 'session.update').at(-1).session });
  if (mode === "continuous") context.window.IgorekVoice.setMicMode("continuous");
  if (policyReady) ready();
  async function user(text = 'Мой вопрос', id = 'input-1') {
    event({ type: 'input_audio_buffer.committed', item_id: id });
    event({ type: 'conversation.item.input_audio_transcription.completed', item_id: id, transcript: text });
    for (let i = 0; i < 8; i++) await Promise.resolve();
  }
  const answer = (text, task = 'task-1') => context.window.IgorekVoice.onSessionEvent({ kind: 'model.round_response', task_id: task, payload: { text } });
  const requests = () => sent.filter(e => e.type === 'response.create');
  const created = (request, id = 'response-1') => event({ type: 'response.created', response: { id, metadata: request.response.metadata } });
  const done = (id = 'response-1', status = 'completed', reason) => event({ type: 'response.done', response: { id, status, status_details: { reason }, output: [{ content: [{ type: 'audio' }] }] } });
  return { sent, posts, states, traces, event, ready, user, answer, requests, created, done, constraints, element, track, windows, api: context.window.IgorekVoice, setTime: n => { clock = n; }, flush: () => { for (const [id, fn] of [...timers]) { timers.delete(id); fn(); } } };
}
test('user transcription only submits a task; speech receives only agent answer with no conversation history', async () => {
  const h = await harness();
  await h.user('Привет, Игорёк, ты здесь?');
  assert.equal(h.requests().length, 0);
  h.answer('Здесь, слушаю тебя.');
  const r = h.requests()[0].response;
  assert.equal(r.conversation, 'none');
  assert.deepEqual(r.input, [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Здесь, слушаю тебя.' }] }]);
  assert.equal(JSON.stringify(r).includes('Привет, Игорёк'), false);
  assert.equal(h.sent.some(e => e.type === 'conversation.item.create'), false);
  assert.equal(h.constraints.audio.echoCancellation, true);
});
test('client overrides old server VAD policy and waits for acknowledgment before speaking', async () => {
  const h = await harness({ policyReady: false }); await h.user(); h.answer('Ответ');
  const vad = h.sent.filter(e => e.type === 'session.update').at(-1).session.audio.input.turn_detection;
  assert.deepEqual(vad, { type: 'semantic_vad', create_response: false, interrupt_response: false });
  assert.equal(h.requests().length, 0); h.ready(); assert.equal(h.requests().length, 1);
});
test('queue retains all replies, ignores VAD during reading, and waits for playback drain after generation done', async () => {
  const h = await harness(); await h.user(); h.answer('Первый ответ');
  const first = h.requests()[0]; h.created(first);
  h.event({ type: 'output_audio_buffer.started', response_id: 'response-1' });
  h.answer('Второй ответ'); h.answer('Третий ответ'); h.answer('Второй ответ');
  h.event({ type: 'input_audio_buffer.speech_started' });
  assert.equal(h.sent.some(e => e.type === 'response.cancel'), false);
  h.done(); assert.equal(h.requests().length, 1);
  h.event({ type: 'response.done', response: { id: 'unrelated', status: 'completed' } });
  assert.equal(h.requests().length, 1);
  h.event({ type: 'output_audio_buffer.stopped', response_id: 'response-1' });
  assert.equal(h.requests().length, 2);
  assert.equal(h.requests()[1].response.input[0].content[0].text, 'Второй ответ');
  h.created(h.requests()[1], 'response-2'); h.event({ type: 'output_audio_buffer.started', response_id: 'response-2' }); h.done('response-2'); h.event({ type: 'output_audio_buffer.stopped', response_id: 'response-2' });
  assert.equal(h.requests().length, 3);
  assert.equal(h.requests()[2].response.input[0].content[0].text, 'Третий ответ');
});
test('unsolicited provider response is cancelled without disturbing queued agent speech', async () => {
  const h = await harness(); await h.user(); h.answer('Ответ');
  h.event({ type: 'response.created', response: { id: 'unsolicited', metadata: {} } });
  assert.deepEqual(h.sent.at(-1), { type: 'response.cancel', response_id: 'unsolicited' });
  h.created(h.requests()[0]); h.event({ type: 'output_audio_buffer.started', response_id: 'response-1' }); h.done(); h.event({ type: 'output_audio_buffer.stopped', response_id: 'response-1' });
  assert.equal(h.requests().length, 1);
});
test('waiting lasts until actual answer audio, duplicate transcripts do not duplicate tasks, cancellation has visible reason', async () => {
  const h = await harness(); assert.equal(h.states.at(-1).waitingForAgent, true);
  await h.user(); await h.user('Мой вопрос', 'input-1');
  assert.equal(h.posts.filter(p => p.url.endsWith('/messages')).length, 1);
  h.answer('Ответ'); h.created(h.requests()[0]);
  assert.equal(h.states.at(-1).waitingForAgent, true);
  h.event({ type: 'output_audio_buffer.started', response_id: 'response-1' });
  assert.equal(h.states.at(-1).waitingForAgent, false);
  h.done('response-1', 'cancelled', 'turn_detected');
  h.event({ type: 'output_audio_buffer.cleared', response_id: 'response-1' });
  assert.equal(h.states.at(-1).phase, 'error');
  assert.equal(h.traces.some(e => e.kind === 'speech.done' && e.reason === 'turn_detected'), true);
  assert.equal(JSON.stringify(h.traces).includes('Мой вопрос'), false);
});

test('PTT is closed by default, waits for buffer clear, and commits once only after release', async () => {
  const h = await harness({ mode: 'ptt' });
  assert.equal(h.track.enabled, false);
  assert.equal(h.states.at(-1).micMode, 'ptt');
  assert.equal(h.states.at(-1).notice, 'Зажми микрофон, чтобы говорить');
  assert.equal(h.sent.find(e => e.type === 'session.update').session.audio.input.turn_detection, null);
  h.element('voice-mic').fire('click'); assert.equal(h.track.enabled, false);
  h.element('voice-mic').fire('pointerdown'); assert.equal(h.track.enabled, false);
  h.event({ type: 'input_audio_buffer.cleared' }); assert.equal(h.track.enabled, true);
  h.setTime(1000); h.element('voice-mic').fire('pointerup'); assert.equal(h.track.enabled, false);
  assert.equal(h.sent.filter(e => e.type === 'input_audio_buffer.commit').length, 0);
  h.element('voice-mic').fire('lostpointercapture'); h.flush();
  assert.equal(h.sent.filter(e => e.type === 'input_audio_buffer.commit').length, 1);
  h.element('voice-mic').fire('pointerup'); h.flush();
  assert.equal(h.sent.filter(e => e.type === 'input_audio_buffer.commit').length, 1);
});
test('cancel, blur and late clear acknowledgment cannot leave PTT recording or send a partial turn', async () => {
  const h = await harness({ mode: 'ptt' });
  h.element('voice-mic').fire('pointerdown'); h.event({ type: 'input_audio_buffer.cleared' });
  assert.equal(h.track.enabled, true);
  h.setTime(1000); h.element('voice-mic').fire('pointercancel'); h.flush();
  assert.equal(h.track.enabled, false);
  h.event({ type: 'input_audio_buffer.cleared' }); assert.equal(h.track.enabled, false);
  h.element('voice-mic').fire('pointerdown'); h.event({ type: 'input_audio_buffer.cleared' });
  assert.equal(h.track.enabled, true); h.windows.blur(); h.flush();
  assert.equal(h.track.enabled, false);
  assert.equal(h.sent.some(e => e.type === 'input_audio_buffer.commit'), false);
});
test('rapid re-press preserves the released turn and keeps the next hold closed until its buffer is clear', async () => {
  const h = await harness({ mode: 'ptt' });
  h.api.beginPushToTalk(); h.event({ type: 'input_audio_buffer.cleared' }); h.setTime(1000); h.api.endPushToTalk();
  h.api.beginPushToTalk(); assert.equal(h.track.enabled, false); h.flush();
  assert.equal(h.sent.filter(e => e.type === 'input_audio_buffer.commit').length, 1);
  assert.equal(h.track.enabled, false); h.event({ type: 'input_audio_buffer.cleared' });
  assert.equal(h.track.enabled, true); h.setTime(2000); h.api.endPushToTalk(); h.windows.blur(); h.flush();
  assert.equal(h.sent.filter(e => e.type === 'input_audio_buffer.commit').length, 2);
});
test('continuous mode requires policy acknowledgment and returning to PTT closes it immediately', async () => {
  const h = await harness({ mode: 'ptt' }); h.api.setMicMode('continuous');
  assert.equal(h.track.enabled, false); h.ready(); assert.equal(h.track.enabled, true);
  h.element('voice-mic').fire('click'); assert.equal(h.track.enabled, false);
  h.element('voice-mic').fire('click'); assert.equal(h.track.enabled, true);
  h.api.setMicMode('ptt'); assert.equal(h.track.enabled, false);
  h.event({ type: 'session.updated', session: { audio: { input: { turn_detection: { create_response: false, interrupt_response: false } } } } });
  assert.equal(h.track.enabled, false); h.ready(); assert.equal(h.track.enabled, false);
  h.api.setMicMode('invalid'); assert.equal(h.states.at(-1).micMode, 'ptt');
});
test('PTT rejects unsolicited transcription and submits a released committed turn exactly once', async () => {
  const h = await harness({ mode: 'ptt' });
  h.event({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'unsolicited', transcript: 'Noise' });
  assert.equal(h.posts.filter(p => p.url.endsWith('/messages')).length, 0);
  h.api.beginPushToTalk(); h.event({ type: 'input_audio_buffer.cleared' }); h.setTime(1000); h.api.endPushToTalk(); h.flush();
  await h.user('Моя фраза', 'committed-1'); await h.user('Моя фраза', 'committed-1');
  assert.equal(h.posts.filter(p => p.url.endsWith('/messages')).length, 1);
  assert.equal(h.track.enabled, false);
});
test('short tap and releasing before the clear acknowledgment never open or commit a turn', async () => {
  const h = await harness({ mode: 'ptt' });
  h.api.beginPushToTalk(); h.api.endPushToTalk(); h.event({ type: 'input_audio_buffer.cleared' });
  assert.equal(h.track.enabled, false);
  h.api.beginPushToTalk(); h.event({ type: 'input_audio_buffer.cleared' }); h.setTime(20); h.api.endPushToTalk(); h.flush();
  assert.equal(h.track.enabled, false);
  assert.equal(h.sent.some(e => e.type === 'input_audio_buffer.commit'), false);
});
test('rejected commit cannot authorize an unrelated transcription later', async () => {
  const h = await harness({ mode: 'ptt' });
  h.api.beginPushToTalk(); h.event({ type: 'input_audio_buffer.cleared' }); h.setTime(1000); h.api.endPushToTalk(); h.flush();
  const commit = h.sent.find(e => e.type === 'input_audio_buffer.commit');
  h.event({ type: 'error', error: { event_id: commit.event_id, code: 'input_audio_buffer_commit_empty' } });
  await h.user('Noise', 'unexpected-after-error');
  assert.equal(h.posts.filter(p => p.url.endsWith('/messages')).length, 0);
  assert.equal(h.track.enabled, false);
});

const approvalEvent = (request = 'request-1', task = 'task-1') => ({
  kind: 'approval.required', task_id: task,
  payload: { task_id: task, request_id: request, tool: 'Bash', reason: 'Review command' },
});
const resolutionEvent = (decision = 'allow_once', request = 'request-1', task = 'task-1') => ({
  kind: 'approval.resolved', task_id: task,
  payload: { task_id: task, request_id: request, decision },
});

test('approval resolved in another view clears the voice card for allow and reject', async () => {
  for (const decision of ['allow_once', 'allow_session', 'reject']) {
    const h = await harness();
    h.api.onSessionEvent(approvalEvent());
    assert.equal(h.states.at(-1).approval, true);
    h.api.onSessionEvent(resolutionEvent(decision));
    assert.equal(h.states.at(-1).approval, false);
    assert.equal(h.element('voice-task').children.some(n => n.className === 'voice-approval'), false);
    assert.equal(h.element('voice-transcript').children.at(-1).children.at(-1).textContent,
      'approval.resolved_' + decision);
    assert.equal(h.requests().length, 0);
  }
});
test('approval resolution requires both task and request identity', async () => {
  const h = await harness();
  h.api.onSessionEvent(approvalEvent());
  h.api.onSessionEvent(resolutionEvent('allow_once', 'different-request'));
  h.api.onSessionEvent(resolutionEvent('allow_once', 'request-1', 'different-task'));
  h.api.onSessionEvent({kind:'approval.resolved',task_id:'task-1',payload:{decision:'allow_once'}});
  assert.equal(h.states.at(-1).approval, true);
  h.api.onSessionEvent(resolutionEvent());
  assert.equal(h.states.at(-1).approval, false);
});
test('duplicate or delayed approval events cannot resurrect an already resolved request', async () => {
  const h = await harness();
  h.api.onSessionEvent(approvalEvent());
  h.api.onSessionEvent(resolutionEvent());
  const messages = h.element('voice-transcript').childElementCount;
  h.api.onSessionEvent(resolutionEvent());
  h.api.onSessionEvent(approvalEvent());
  assert.equal(h.states.at(-1).approval, false);
  assert.equal(h.element('voice-transcript').childElementCount, messages);
  h.api.onSessionEvent(approvalEvent('request-2'));
  assert.equal(h.states.at(-1).approval, true);
  h.api.onSessionEvent(resolutionEvent());
  assert.equal(h.states.at(-1).approval, true);
  h.api.onSessionEvent(resolutionEvent('reject', 'request-2'));
  assert.equal(h.states.at(-1).approval, false);
});
test('a resolution received before its delayed required event prevents a stale card', async () => {
  const h = await harness();
  h.api.onSessionEvent(resolutionEvent());
  h.api.onSessionEvent(approvalEvent());
  assert.equal(h.states.at(-1).approval, false);
});
test('the voice approval button resolves locally without waiting for a server event', async () => {
  const h = await harness();
  h.api.onSessionEvent(approvalEvent());
  const card = h.element('voice-task').children.find(n => n.className === 'voice-approval');
  card.children.at(-1).children[0].fire('click');
  for (let i=0;i<15;i++) await Promise.resolve();
  assert.deepEqual(h.posts.find(p=>p.url.endsWith('/approvals/request-1')),
    {url:'/api/tasks/task-1/approvals/request-1',body:{decision:'allow_once'}});
  assert.equal(h.states.at(-1).approval, false);
});

test('a failed approval POST keeps the request actionable until an actual decision arrives', async () => {
  const h = await harness({approvalReply:async()=>({ok:false,status:500,json:async()=>({error:'fixture failure'})})});
  h.api.onSessionEvent(approvalEvent());
  h.element('voice-task').children.find(n=>n.className==='voice-approval').children.at(-1).children[0].fire('click');
  for(let i=0;i<15;i++)await Promise.resolve();
  assert.equal(h.states.at(-1).approval,true);
  assert.equal(h.states.at(-1).notice,'voice.error.approval');
  h.api.onSessionEvent(resolutionEvent());
  assert.equal(h.states.at(-1).approval,false);
});
test('a late POST response cannot clear the next approval or report a stale error', async () => {
  let complete;
  const h = await harness({approvalReply:()=>new Promise(resolve=>{complete=resolve;})});
  h.api.onSessionEvent(approvalEvent());
  h.element('voice-task').children.find(n=>n.className==='voice-approval').children.at(-1).children[0].fire('click');
  h.api.onSessionEvent(resolutionEvent());
  h.api.onSessionEvent(approvalEvent('request-2'));
  complete({ok:false,status:409,json:async()=>({error:'already resolved'})});
  for(let i=0;i<15;i++)await Promise.resolve();
  assert.equal(h.states.at(-1).approval,true);
  assert.notEqual(h.states.at(-1).notice,'voice.error.approval');
  h.api.onSessionEvent(resolutionEvent('reject','request-2'));
  assert.equal(h.states.at(-1).approval,false);
});
