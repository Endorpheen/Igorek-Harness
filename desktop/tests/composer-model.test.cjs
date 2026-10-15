const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness(api) {
  const state = { modelInfo: new Map(), session: { meta: { profile: 'local', model: 'old' } } };
  const context = { state, api, encode: encodeURIComponent, renderPlanSection() {}, Date };
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/app.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('function modelInfoKey('), source.indexOf('function renderComposerModel(')), context);
  context.renderComposerModel = () => {};
  return context;
}

test('model discovery keeps fresh cache but task refresh replaces the old server model', async () => {
  let calls = 0;
  const context = harness(async () => ({ model: ++calls === 1 ? 'old-server' : 'Nemotron-Bolt' }));
  assert.equal((await context.loadModelInfo('local', 'old')).model, 'old-server');
  assert.equal((await context.loadModelInfo('local', 'old')).model, 'old-server');
  assert.equal(calls, 1);
  context.refreshComposerModel();
  await context.cachedModelInfo('local', 'old').pending;
  assert.equal(context.cachedModelInfo('local', 'old').model, 'Nemotron-Bolt');
  assert.equal(calls, 2);
});

test('failed model discovery can retry and concurrent refreshes share one request', async () => {
  let resolve, calls = 0;
  const context = harness(() => {
    calls++;
    if (calls === 1) return Promise.reject(new Error('temporary outage'));
    return new Promise(done => { resolve = done; });
  });
  await assert.rejects(context.loadModelInfo('local', 'old'));
  assert.equal(context.cachedModelInfo('local', 'old').status, 'error');
  const first = context.loadModelInfo('local', 'old');
  const second = context.loadModelInfo('local', 'old', true);
  resolve({ model: 'running-model' });
  assert.equal((await first).model, 'running-model');
  assert.equal((await second).model, 'running-model');
  assert.equal(calls, 2);
});

test('expired model cache refreshes without replacing another profile', async () => {
  let calls = 0;
  const context = harness(async () => ({ model: `model-${++calls}` }));
  await context.loadModelInfo('cloud', 'chosen');
  await context.loadModelInfo('local', 'old');
  context.cachedModelInfo('local', 'old').loadedAt -= 31000;
  assert.equal((await context.loadModelInfo('local', 'old')).model, 'model-3');
  assert.equal(context.cachedModelInfo('cloud', 'chosen').model, 'model-1');
});

test('composer renders the discovered model over stale session metadata', async () => {
  const context = harness(async () => ({ model: 'Nemotron-Bolt' }));
  const chip = { children: [], replaceChildren() { this.children = []; }, append(child) { this.children.push(child); }, setAttribute() {} };
  context.ui = { composerModel: chip, composerMeta: { hidden: true } };
  context.element = (tag, className, text = '') => ({ tag, className, text });
  context.t = (key, params) => JSON.stringify(params || key);
  const source = fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/app.js'), 'utf8');
  vm.runInContext(source.slice(source.indexOf('function renderComposerModel('), source.indexOf('function upsert(')), context);
  await context.loadModelInfo('local', 'old');
  context.renderComposerModel(context.state.session);
  assert.equal(chip.children.find(child => child.className === 'composer-model-name').text, 'Nemotron-Bolt');
  assert.match(chip.title, /Nemotron-Bolt/);
  assert.equal(context.ui.composerMeta.hidden, false);
});
