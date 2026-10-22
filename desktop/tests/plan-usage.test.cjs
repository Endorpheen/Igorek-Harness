const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/app.js'), 'utf8');

function element(tag, className, textContent = '') {
  return {tag, className, textContent, children: [], style: {}, attributes: {},
    append(...children) { this.children.push(...children); },
    setAttribute(name, value) { this.attributes[name] = value; }};
}
function fixture() {
  const state = {session: {meta: {profile: 'fixture', model: 'fixture'}},
    plan: {usage: {input: 0, output: 0, rounds: 0, lastInput: null, lastLatency: 0},
      usageSeqs: new Set(), loading: false}};
  const context = {state, element, Number, Intl, intlLocale: () => 'en',
    t: (key, args) => key + (args ? JSON.stringify(args) : ''), renderPlanSection() {},
    cachedModelInfo: () => ({status: 'ready', context_window: 131072})};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function usageTokens('), source.indexOf('function scanJournalEvent(')), context);
  vm.runInContext(source.slice(source.indexOf('function formatCount('), source.indexOf('// OpenCode Go quota:')), context);
  return {context, state, receive: (usage, seq, extra = {}) => context.noteUsage({usage, ...extra}, seq),
    render: () => context.buildPlanContext(state.plan.usage)};
}
function nodes(root) { return [root, ...root.children.flatMap(nodes)]; }
function byClass(root, name) { return nodes(root).filter(node => node.className === name); }

test('completed usage fills the context bar and preserves real zero counts', () => {
  const f = fixture(); f.receive({input_tokens: 32768, output_tokens: 0}, 1);
  assert.equal(f.state.plan.usage.rounds, 1);
  assert.equal(f.state.plan.usage.output, 0);
  const view = f.render();
  assert.equal(byClass(view, 'plan-context-bar-fill')[0].style.width, '25%');
  assert.equal(byClass(view, 'plan-context-bar')[0].attributes['aria-valuenow'], '25');
  assert.equal(nodes(view).some(node => node.textContent === 'plan.context_unavailable'), false);
});

test('missing usage still counts the round and never displays a false empty context bar', () => {
  const f = fixture(); f.receive({}, 1, {latency_ms: 151807});
  assert.equal(f.state.plan.usage.rounds, 1);
  assert.equal(f.state.plan.usage.input, null);
  assert.equal(f.state.plan.usage.output, null);
  assert.equal(f.state.plan.usage.lastLatency, 151807);
  const view = f.render();
  assert.equal(byClass(view, 'plan-context-bar').length, 0);
  assert.equal(nodes(view).filter(node => node.textContent === 'plan.context_unavailable').length, 3);
  assert.equal(nodes(view).some(node => node.textContent === 'plan.context_usage_unavailable'), true);
});

test('partial statistics leave only the missing counter unavailable', () => {
  const f = fixture(); f.receive({prompt_tokens: 32768}, 1);
  assert.equal(f.state.plan.usage.input, 32768);
  assert.equal(f.state.plan.usage.output, null);
  assert.equal(byClass(f.render(), 'plan-context-bar-fill')[0].style.width, '25%');
});

test('unknown totals stay unknown while a later complete request restores its own context bar', () => {
  const f = fixture(); f.receive({}, 1); f.receive({input_tokens: 32768, output_tokens: 12}, 2);
  f.receive({input_tokens: 32768, output_tokens: 12}, 2);
  assert.equal(f.state.plan.usage.rounds, 2);
  assert.equal(f.state.plan.usage.input, null);
  assert.equal(f.state.plan.usage.output, null);
  assert.equal(f.state.plan.usage.lastInput, 32768);
  assert.equal(byClass(f.render(), 'plan-context-bar-fill')[0].style.width, '25%');
});

test('null or invalid counters do not become measured zeros', () => {
  const f = fixture();
  for (const usage of [{input_tokens: null, output_tokens: null},
    {input_tokens: false, output_tokens: -1}, {input_tokens: '', output_tokens: true}]) {
    const result = f.context.usageTokens(usage);
    assert.equal(result.input, null); assert.equal(result.output, null);
  }
  f.context.noteUsage({round: 0, text: 'A non-model notification.'}, 1);
  assert.equal(f.state.plan.usage.rounds, 0);
});

test('real zero input tokens render a zero context bar', () => {
  const f = fixture();
  f.receive({input_tokens: 0, output_tokens: 4}, 1);
  assert.equal(f.state.plan.usage.lastInput, 0);
  assert.equal(f.state.plan.usage.input, 0);
  assert.equal(f.state.plan.usage.output, 4);
  assert.equal(f.state.plan.usage.rounds, 1);
  const view = f.render();
  assert.equal(byClass(view, 'plan-context-bar').length, 1);
  assert.equal(byClass(view, 'plan-context-bar-fill')[0].style.width, '0%');
  assert.equal(byClass(view, 'plan-context-bar')[0].attributes['aria-valuenow'], '0');
  assert.equal(nodes(view).some(node => node.textContent === 'plan.context_unavailable'), false);
});
