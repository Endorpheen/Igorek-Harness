const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/app.js'), 'utf8');

function harness(text, attachments = []) {
  const calls = [], state = { selected: 'original', attachments, session: { meta: { profile: 'fixture' } } };
  const ui = { message: { value: text, style: {}, focus() {} }, send: {} };
  const context = { state, ui, window: {}, lang: 'ru', crypto: { randomUUID: () => 'fixture-key' },
    toast: (...args) => calls.push(['toast', ...args]), t: key => key,
    planModeActive: () => true, trackedSkillIds: () => [], encode: encodeURIComponent,
    api: async (route, options) => { calls.push(['api', route, JSON.parse(options.body)]); return { demo: true, session_id: 'demo-session' }; },
    releaseAttachmentPreviews() {}, renderAttachments() {},
    mentionModel: () => ({ skillsTracked: { clear() {} } }), closeMentionMenu() {},
    selectSession: async id => calls.push(['select', id]),
    uploadAttachment: async () => { throw Error('Demo must not upload attachments'); },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function sendMessage(event)'), source.indexOf('// Voice input:')), context);
  return { state, ui, calls, send: () => context.sendMessage({ preventDefault() {} }) };
}

test('/demo opens its dedicated session and does not leave the original chat busy', async () => {
  const h = harness('/demo'); await h.send();
  assert.equal(h.calls[0][2].text, '/demo');
  assert.equal(h.calls[0][2].demo_language, 'ru');
  assert.deepEqual(h.calls[1], ['select', 'demo-session']);
  assert.equal(h.ui.message.value, '');
  assert.equal(h.state.sending, false);
  assert.equal(h.ui.send.disabled, false);
});

test('/demo with attachments explains how to start without uploading or clearing the draft', async () => {
  const h = harness('/demo', [{ name: 'private.txt' }]); await h.send();
  assert.deepEqual(h.calls, [['toast', 'demo.separate']]);
  assert.equal(h.ui.message.value, '/demo');
  assert.equal(h.state.attachments.length, 1);
});

test('the demo button preserves an existing draft', () => {
  const begin = source.indexOf("document.getElementById('demo-start')?.addEventListener");
  const end = source.indexOf('\n});', begin) + 4;
  let click, submitted = 0;
  const context = { document: { getElementById: () => ({ addEventListener: (_, callback) => click = callback }) },
    state: { selected: 'fixture', attachments: [] }, ui: { message: { value: 'keep my draft' }, composer: { requestSubmit: () => submitted++ } } };
  vm.runInNewContext(source.slice(begin, end), context);
  click(); assert.equal(submitted, 0); assert.equal(context.ui.message.value, 'keep my draft');
  context.ui.message.value = ''; click(); assert.equal(submitted, 1); assert.equal(context.ui.message.value, '/demo');
});
