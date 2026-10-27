const {test} = require('node:test');
const assert = require('node:assert/strict');

class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.hidden = false; this.value = ''; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute(name, value) { this[name] = value; }
  click() { if (!this.disabled) this.listeners.click?.(); }
}
global.document = {createElement: tag => new Node(tag)};
const panel = require('../../factory/web_ui/static/goal-panel.js');
const flush = () => new Promise(resolve => setImmediate(resolve));
const flatten = node => [node, ...node.children.flatMap(flatten)];
const find = (host, text) => flatten(host).find(node => node.textContent === text);

test('goal card shows actual objective, state, usage, and explicit actions', () => {
  const host = new Node('section');
  const view = panel.mount(host, {language: () => 'ru', action: async () => {}, error: assert.fail});
  view.setGoal(null); assert.equal(host.hidden, true);
  view.setGoal({goal_id: 'g1', objective: '<unsafe>', status: 'active', tokens_used: 7, token_budget: 20});
  assert.equal(host.hidden, false);
  assert.ok(find(host, '<unsafe>')); assert.ok(find(host, 'Выполняется'));
  assert.ok(find(host, 'Токены: 7 / 20')); assert.ok(find(host, 'Пауза'));
  assert.ok(find(host, 'Изменить')); assert.ok(find(host, 'Убрать'));
});

test('pause and resume use the displayed goal identity', async () => {
  const calls = []; const host = new Node('section');
  const view = panel.mount(host, {language: () => 'en', action: async (...args) => calls.push(args), error: assert.fail});
  view.setGoal({goal_id: 'g1', objective: 'Work', status: 'active'});
  find(host, 'Pause').click(); await flush();
  assert.deepEqual(calls, [['pause', {goal_id: 'g1'}]]);
  view.setGoal({goal_id: 'g2', objective: 'Other', status: 'paused'});
  find(host, 'Resume').click(); await flush();
  assert.deepEqual(calls[1], ['resume', {goal_id: 'g2'}]);
});

test('editing sends the changed objective and clear remains explicit', async () => {
  const calls = []; const host = new Node('section');
  const view = panel.mount(host, {language: () => 'en', action: async (...args) => calls.push(args), error: assert.fail});
  view.setGoal({goal_id: 'g1', objective: 'Old', status: 'active'});
  find(host, 'Edit').click();
  const input = flatten(host).find(node => node.tag === 'textarea');
  assert.equal(input.value, 'Old'); input.value = '  New objective  ';
  find(host, 'Save').click(); await flush();
  assert.deepEqual(calls[0], ['edit', {goal_id: 'g1', objective: 'New objective'}]);
  find(host, 'Clear').click(); await flush();
  assert.deepEqual(calls[1], ['clear', {goal_id: 'g1'}]);
});

test('errors remain visible and terminal goals are not presented as resumable', async () => {
  const errors = []; const host = new Node('section');
  const view = panel.mount(host, {language: () => 'en', action: async () => { throw Error('offline'); }, error: e => errors.push(e.message)});
  view.setGoal({goal_id: 'g', objective: 'Work', status: 'active'});
  find(host, 'Pause').click(); await flush();
  assert.deepEqual(errors, ['offline']); assert.equal(find(host, 'Pause').disabled, false);
  view.setGoal({goal_id: 'g', objective: 'Work', status: 'budget_limited'});
  assert.equal(find(host, 'Resume'), undefined); assert.ok(find(host, 'Token limit'));
});
