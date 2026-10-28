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

test('the composer ends at the input form without demo or local-session captions', () => {
  const html = fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/index.html'), 'utf8');
  const composer = html.slice(html.indexOf('id="composer-form"'), html.indexOf('<aside class="side-panel"'));
  assert.match(composer, /id="message-input"/);
  assert.match(composer, /id="send-button"/);
  assert.match(composer, /<\/form>\s*<\/div>\s*<\/div>\s*<\/section>\s*$/);
  assert.doesNotMatch(html, /demo-start|composer-caption|composer\.caption|demo\.start/);
  assert.doesNotMatch(source, /demo-start/);
});
