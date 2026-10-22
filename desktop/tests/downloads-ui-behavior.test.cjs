const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.dataset = {}; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  async emit(name) { await Promise.all((this.listeners[name] || []).map(handler => handler({target: this}))); }
}
function all(root) { return [root, ...root.children.flatMap(all)]; }
function byClass(root, name) { return all(root).filter(node => node.className === name); }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject}; }
async function settle() { await Promise.resolve(); await Promise.resolve(); }
function fixture(t, initial = {downloads: []}) {
  const host = new Element('host'), model = {}, calls = [], responses = {};
  const document = {createElement: tag => new Element(tag)};
  const module = {exports: {}};
  const filename = path.join(__dirname, '../../factory/web_ui/static/downloads.js');
  const context = vm.createContext({document, module, Intl, Date});
  vm.runInContext(fs.readFileSync(filename, 'utf8'), context, {filename});
  const api = module.exports;
  const view = api.create({host, model, glyph: name => {const n = new Element('svg'); n.icon = name; return n;},
    call(operation, fields) { calls.push({operation, fields}); return Promise.resolve(operation in responses ? responses[operation] : initial); }});
  return {host, model, calls, responses, view};
}
const at = new Date(2026, 9, 20, 12, 30).getTime();
function item(id, extra = {}) { return {id, name: id + '.txt', at, state: 'completed', can_reveal: true, ...extra}; }

test('downloads page loads an empty history and disables clear', async t => {
  const f = fixture(t); await settle();
  assert.deepEqual(f.calls, [{operation: 'downloads', fields: undefined}]);
  assert.equal(byClass(f.host, 'downloads-empty')[0].textContent, 'Здесь появятся скачанные файлы.');
  assert.equal(byClass(f.host, 'downloads-clear')[0].disabled, true);
});

test('download rows expose only item ids to reveal and remove actions', async t => {
  const f = fixture(t, {downloads: [item('one', {name: 'picture.svg'}), item('two', {can_reveal: false})]}); await settle();
  const rows = byClass(f.host, 'download-row'); assert.equal(rows.length, 2);
  const buttons = all(rows[0]).filter(n => n.tag === 'button');
  assert.equal(all(rows[0]).some(n => n.icon === 'image'), true);
  assert.equal(all(rows[1]).filter(n => n.tag === 'button')[0].disabled, true);
  await buttons[0].emit('click'); await buttons[1].emit('click');
  assert.deepEqual(f.calls.slice(1).map(c => [c.operation, {...c.fields}]), [['openDownload', {id: 'one'}], ['removeDownload', {id: 'one'}]]);
});

test('search filters non-ASCII names and keeps the query across updates', async t => {
  const f = fixture(t, {downloads: [item('one', {name: 'Снимок.png'}), item('two', {name: 'report.pdf'})]}); await settle();
  const input = all(f.host).find(n => n.tag === 'input'); input.value = 'СНИМОК'; await input.emit('input');
  assert.equal(f.model.downloadQuery, 'СНИМОК');
  assert.equal(byClass(f.host, 'download-row').length, 1);
  assert.equal(byClass(f.host, 'download-file-name')[0].textContent, 'Снимок.png');
  input.value = 'missing'; await input.emit('input');
  assert.equal(byClass(f.host, 'downloads-empty')[0].textContent, 'По этому запросу ничего не найдено.');
  f.view.update({downloads: [item('three')]});
  assert.equal(byClass(f.host, 'download-row').length, 0);
});

test('date-group expansion persists after a refresh', async t => {
  const f = fixture(t, {downloads: [item('one')]}); await settle();
  const day = byClass(f.host, 'downloads-day')[0]; assert.equal(day.open, true);
  day.open = false; await day.emit('toggle'); f.view.update({downloads: [item('one')]});
  assert.equal(byClass(f.host, 'downloads-day')[0].open, false);
});

test('progress and terminal states retain readable labels and bounds', async t => {
  const f = fixture(t, {downloads: [item('active', {state: 'progressing', received: 4, total: 10}),
    item('interrupted', {state: 'interrupted'}), item('cancelled', {state: 'cancelled'}), item('unknown', {state: 'unexpected'})], downloads_limit: 4}); await settle();
  const progress = all(f.host).find(n => n.tag === 'progress'); assert.equal(progress.max, 10); assert.equal(progress.value, 4);
  assert.deepEqual(byClass(f.host, 'download-file-state').map(n => n.textContent), ['Загружается', 'Прервано', 'Отменено', 'Состояние неизвестно']);
  assert.equal(byClass(f.host, 'downloads-status').some(n => n.textContent === 'Сохранены последние 4 записей.'), true);
});

test('clear uses the returned history and does not issue overlapping actions', async t => {
  const f = fixture(t, {downloads: [item('one')]}); await settle();
  const pending = deferred(); f.responses.clearDownloads = pending.promise;
  const clear = byClass(f.host, 'downloads-clear')[0]; const running = clear.emit('click');
  assert.equal(clear.disabled, true); await clear.emit('click');
  assert.equal(f.calls.filter(c => c.operation === 'clearDownloads').length, 1);
  pending.resolve({downloads: []}); await running;
  assert.equal(byClass(f.host, 'download-row').length, 0); assert.equal(clear.disabled, true);
});

test('failed actions display the error and allow a later retry', async t => {
  const f = fixture(t, {downloads: [item('one')]}); await settle();
  const pending = deferred(); f.responses.clearDownloads = pending.promise;
  const clear = byClass(f.host, 'downloads-clear')[0], running = clear.emit('click'); pending.reject(new Error('Synthetic request failed')); await running;
  assert.equal(byClass(f.host, 'downloads-status')[0].textContent, 'Synthetic request failed'); assert.equal(clear.disabled, false);
  f.responses.clearDownloads = {downloads: []}; await clear.emit('click'); assert.equal(byClass(f.host, 'download-row').length, 0);
});

test('history load and explicit errors remain visible without fabricated rows', async t => {
  const pending = deferred(), f = fixture(t, pending.promise); pending.reject(new Error('Synthetic history failure')); await settle();
  assert.equal(byClass(f.host, 'downloads-status')[0].textContent, 'Synthetic history failure');
  f.view.error('Another synthetic error'); assert.equal(byClass(f.host, 'downloads-status')[0].textContent, 'Another synthetic error');
  f.view.update({downloads: [], downloads_error: 'History unavailable'}); assert.equal(byClass(f.host, 'downloads-status')[0].textContent, 'History unavailable');
});
