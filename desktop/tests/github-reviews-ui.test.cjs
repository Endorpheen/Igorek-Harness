const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; this._text = ''; this.disabled = false; }
  set textContent(value) { this._text = String(value); this.replaceChildren(); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get firstChild() { return this.children[0] || null; }
  set value(value) { this._value = String(value); }
  get value() { return this._value ?? (this.tag === 'select' ? this.firstChild?.value || '' : this.tag === 'option' ? this.textContent : ''); }
  append(...children) { for (const child of children) { if (child.parent) child.parent.children = child.parent.children.filter(n => n !== child); child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parent = null; this.children = []; this.append(...children); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  async emit(name) { if (name === 'click' && this.disabled) return; await Promise.all((this.listeners[name] || []).map(handler => handler({target: this, preventDefault() {}}))); }
}
function all(root) { return [root, ...root.children.flatMap(all)]; }
function byClass(root, name) { return all(root).filter(node => (node.className || '').split(' ').includes(name)); }
function button(root, text) { const n = all(root).find(node => node.tag === 'button' && node.textContent === text); assert.ok(n, 'Missing button: ' + text); return n; }
function label(root, text) { const n = all(root).find(node => node.attributes['aria-label'] === text); assert.ok(n, 'Missing control: ' + text); return n; }
function deferred() { let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject}; }
async function settle() { await new Promise(resolve => setImmediate(resolve)); }
const actor = {id: 17, login: 'reviewer'};
const url = number => 'https://github.com/example/project/pull/' + number;
function detail(number = 1, extra = {}) {
  return {url: url(number), number, title: 'PR ' + number, owner: 'example', repo: 'project', author: 'author',
    state: 'open', draft: false, pinned: false, mergeable: true, merge_state: 'clean', session_id: '',
    body: 'Description ' + number, created: '2026-10-20T10:00:00Z', additions: 4, deletions: 2, changed_files: 1,
    head: {sha: String(number).repeat(40), ref: 'feature'}, base: {ref: 'main'}, account: actor,
    reviews: {items: [], error: '', more: false}, checks: {items: [], error: '', more: false},
    statuses: {items: [], error: '', more: false}, ...extra};
}
function fixture() {
  const host = new Element('host'), calls = [], responses = {}, chats = [], created = [], sites = [], markdown = [], copied = [], locations = [];
  let now = 100000;
  class Clock extends Date { static now() { return now; } }
  const document = {createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag)};
  const module = {exports: {}};
  const filename = path.join(__dirname, '../../factory/web_ui/static/github-reviews.js');
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {document, module, URL, Date: Clock,
    navigator: {clipboard: {writeText: async value => copied.push(value)}}}, {filename});
  const defaults = {
    status: () => ({account: actor, teams: []}), identity: () => ({account: actor}),
    list: () => ({account: actor, items: [detail(1)], more: false}),
    detail: args => detail(Number(args.url.split('/').at(-1))), timeline: () => ({account: actor, items: [], more: false}),
    files: args => ({sha: args.sha, items: [], more: false}),
    pin: () => ({pinned: true}), bind: args => ({session_id: args.session_id}), merge: () => ({cancelled: true}),
  };
  const view = module.exports.mount({host,
    bridge: async (operation, args) => { calls.push({operation, args: JSON.parse(JSON.stringify(args))}); const response = Object.hasOwn(responses, operation) ? responses[operation] : defaults[operation]; return typeof response === 'function' ? response(args) : response; },
    show() {}, chat: (...args) => chats.push(args), create: (...args) => created.push(args), sessions: () => [{session_id: 'existing-chat', title: 'Existing chat'}],
    markdown: (text, node) => {markdown.push(text); node.textContent = text;}, openSite: value => sites.push(value), locationChanged: () => locations.push(view.location())});
  return {host, view, responses, calls, chats, created, sites, markdown, copied, locations, advance: value => {now += value;}};
}

test('opening PR inbox loads the current account and category without remote mutations', async () => {
  const f = fixture(); await f.view.open();
  assert.equal(f.host.hidden, false); assert.ok(f.host.textContent.includes('@reviewer'));
  assert.equal(byClass(f.host, 'gh-pr-row').length, 1);
  assert.deepEqual(f.calls.map(c => c.operation), ['status', 'list']);
  assert.deepEqual(f.calls[1].args, {filter: 'mine', state: 'open', query: '', page: 1, team: ''});
});

test('list pagination appends unique PRs and uses the next page', async () => {
  const f = fixture(); f.responses.list = args => ({account: actor, items: args.page === 1 ? [detail(1)] : [detail(1), detail(2)], more: args.page === 1});
  await f.view.open(); await button(f.host, 'Показать ещё').emit('click');
  assert.equal(byClass(f.host, 'gh-pr-row').length, 2);
  assert.deepEqual(f.calls.filter(c => c.operation === 'list').map(c => c.args.page), [1, 2]);
  assert.equal(button(f.host, 'Показать ещё').hidden, true);
});

test('search submits plain text as a bounded query and PR links as a direct selection', async () => {
  const f = fixture(); await f.view.open();
  const input = label(f.host, 'Найти PR или вставить ссылку'); input.value = 'x'.repeat(300);
  await byClass(f.host, 'gh-search')[0].emit('submit'); await settle();
  assert.equal(f.calls.filter(c => c.operation === 'list').at(-1).args.query, 'x'.repeat(200));
  input.value = url(2) + '/files'; await byClass(f.host, 'gh-search')[0].emit('submit'); await settle();
  assert.equal(f.view.location(), url(2)); assert.equal(f.calls.filter(c => c.operation === 'detail').at(-1).args.url, url(2));
});

test('a late list response cannot replace the result of a newer category selection', async () => {
  const f = fixture(), old = deferred(); f.responses.list = old.promise;
  const opening = f.view.open(); await settle();
  f.responses.list = {account: actor, items: [detail(2)], more: false}; const filter = label(f.host, 'Категория PR'); filter.value = 'requested';
  await filter.emit('change'); await settle(); old.resolve({account: actor, items: [detail(1)], more: false}); await opening;
  assert.equal(byClass(f.host, 'gh-pr-row').length, 1); assert.ok(byClass(f.host, 'gh-pr-row')[0].textContent.includes('PR 2'));
});

test('authentication failures clear the inbox and offer the existing login command', async () => {
  const f = fixture(); f.responses.status = {error: 'Sign in required', code: 'github_auth_required'};
  await f.view.open(); assert.equal(byClass(f.host, 'gh-pr-row').length, 0);
  assert.ok(byClass(f.host, 'gh-notice')[0].textContent.includes('gh auth login --hostname github.com --web'));
});

test('a response belonging to another account is not rendered in the inbox', async () => {
  const f = fixture(); f.responses.list = {account: {id: 99, login: 'other'}, items: [detail(2)], more: false};
  await f.view.open(); assert.equal(byClass(f.host, 'gh-pr-row').length, 0);
  assert.ok(byClass(f.host, 'gh-notice')[0].textContent.includes('Аккаунт GitHub изменился'));
});

test('selecting a PR renders its current head and strips leading hidden description metadata', async () => {
  const f = fixture(); f.responses.detail = detail(1, {body: '<!-- private marker -->\nUseful description'});
  await f.view.open(); await f.view.select(url(1)); await settle();
  assert.equal(f.view.location(), url(1)); assert.ok(f.host.textContent.includes('1111111111'));
  assert.ok(f.markdown.includes('Useful description')); assert.deepEqual(f.locations, [url(1)]);
});

test('a late detail response cannot replace the newly selected PR', async () => {
  const f = fixture(), first = deferred(); await f.view.open(); f.responses.detail = args => args.url === url(1) ? first.promise : detail(2);
  const selecting = f.view.select(url(1)); await f.view.select(url(2)); await settle(); first.resolve(detail(1)); await selecting;
  assert.equal(f.view.location(), url(2)); assert.equal(byClass(f.host, 'gh-pr-heading')[0].textContent.includes('PR 2'), true);
});

test('forgetting the inbox discards pending details and requires a fresh status on reopen', async () => {
  const f = fixture(), pending = deferred(); await f.view.open(); f.responses.detail = pending.promise;
  const selecting = f.view.select(url(1)); f.view.hide({forget: true}); pending.resolve(detail(1)); await selecting;
  assert.equal(f.view.location(), ''); assert.equal(byClass(f.host, 'gh-content')[0].textContent, '');
  await f.view.open(); assert.equal(f.calls.filter(c => c.operation === 'status').length, 2);
});

test('reopening a fresh inbox uses its cache and expired data revalidates identity once', async () => {
  const f = fixture(); await f.view.open(); f.view.hide(); await f.view.open();
  assert.equal(f.calls.filter(c => c.operation === 'status').length, 1);
  const pending = deferred(); f.responses.identity = pending.promise; f.advance(60001);
  await f.view.open(); await f.view.open(); assert.equal(f.calls.filter(c => c.operation === 'identity').length, 1);
  pending.resolve({account: actor}); await settle(); assert.equal(f.calls.filter(c => c.operation === 'status').length, 1);
});

test('an identity change clears old PR details and reloads the new account', async () => {
  const f = fixture(); await f.view.open(); await f.view.select(url(1)); await settle();
  const other = {id: 99, login: 'other'}; f.responses.identity = {account: other}; f.responses.status = {account: other, teams: []};
  f.responses.list = {account: other, items: [detail(2)], more: false}; f.advance(60001);
  await f.view.open(); await settle();
  assert.equal(f.view.location(), ''); assert.ok(f.host.textContent.includes('@other'));
  assert.equal(byClass(f.host, 'gh-pr-row').length, 1); assert.ok(byClass(f.host, 'gh-pr-row')[0].textContent.includes('PR 2'));
});

test('expired identity failures remove cached content while keeping a readable notice', async () => {
  const f = fixture(); await f.view.open(); await f.view.select(url(1)); await settle();
  f.responses.identity = {error: 'Synthetic vault lock', code: 'github_locked'}; f.advance(60001);
  await f.view.open(); await settle();
  assert.equal(f.view.location(), ''); assert.equal(byClass(f.host, 'gh-pr-row').length, 0);
  assert.ok(byClass(f.host, 'gh-notice')[0].textContent.includes('Synthetic vault lock'));
});

test('review opens a draft in the bound chat with the exact head and no GitHub mutation', async () => {
  const f = fixture(); f.responses.detail = detail(1, {session_id: 'existing-chat'});
  await f.view.open(); await f.view.select(url(1)); await settle(); await button(f.host, 'Проверить').emit('click');
  assert.equal(f.chats.length, 1); assert.equal(f.chats[0][0], 'existing-chat');
  assert.ok(f.chats[0][2].includes('1'.repeat(40))); assert.ok(f.chats[0][2].includes('Пока не изменяй код'));
  assert.equal(f.created.length, 0); assert.equal(f.calls.some(c => ['merge', 'bind'].includes(c.operation)), false);
});

test('an unbound PR creates a review draft and a conflicting PR creates a fix-only draft', async () => {
  const f = fixture(); f.responses.detail = detail(1, {mergeable: false});
  await f.view.open(); await f.view.select(url(1)); await settle(); await button(f.host, 'Проверить').emit('click');
  assert.equal(f.created.length, 1); assert.ok(f.created[0][1].includes('Пока не изменяй код'));
  await button(f.host, 'Исправить ›').emit('click');
  assert.ok(f.created[1][1].includes('Разреши только конфликт')); assert.ok(f.created[1][1].includes('Сам PR не сливай'));
});

test('pin success updates only the PR that initiated the request', async () => {
  const f = fixture(), pending = deferred(); await f.view.open(); await f.view.select(url(1)); await settle();
  f.responses.pin = pending.promise; const pinning = label(f.host, 'Закрепить PR').emit('click'); await settle();
  await f.view.select(url(2)); await settle(); pending.resolve({pinned: true}); await pinning;
  assert.equal(label(f.host, 'Закрепить PR').attributes['aria-pressed'], 'false');
  assert.deepEqual(f.calls.find(c => c.operation === 'pin').args, {url: url(1), title: 'PR 1', pinned: true, account_id: actor.id});
  f.responses.pin = {pinned: true}; await label(f.host, 'Закрепить PR').emit('click');
  assert.equal(label(f.host, 'Открепить PR').attributes['aria-pressed'], 'true');
});

test('a late pin failure cannot overwrite the notice for a newly selected PR', async () => {
  const f = fixture(), pending = deferred(); await f.view.open(); await f.view.select(url(1)); await settle();
  f.responses.pin = pending.promise; const pinning = label(f.host, 'Закрепить PR').emit('click'); await settle();
  await f.view.select(url(2)); await settle(); pending.reject(new Error('Failure for old PR')); await pinning;
  assert.equal(f.view.location(), url(2)); assert.equal(byClass(f.host, 'gh-notice')[0].textContent, '');
});

test('a pin failure for the current PR remains visible and retry succeeds', async () => {
  const f = fixture(); await f.view.open(); await f.view.select(url(1)); await settle();
  f.responses.pin = {error: 'Synthetic pin failure', code: 'github_unavailable'}; await label(f.host, 'Закрепить PR').emit('click');
  assert.equal(byClass(f.host, 'gh-notice')[0].textContent, 'Synthetic pin failure');
  f.responses.pin = {pinned: true}; await label(f.host, 'Закрепить PR').emit('click');
  assert.equal(label(f.host, 'Открепить PR').attributes['aria-pressed'], 'true');
});

test('file pagination pins the head SHA and renders text, renamed, and binary changes', async () => {
  const f = fixture(); f.responses.files = args => ({sha: args.sha, more: args.page === 1, items: args.page === 1 ?
    [{path: 'main.py', previous: 'old.py', additions: 4, deletions: 2, patch: '@@ -1 +1 @@\n-old\n+new', truncated: true}] :
    [{path: 'image.png', additions: 0, deletions: 0, patch: null}]});
  await f.view.open(); await f.view.select(url(1)); await settle();
  await byClass(f.host, 'gh-detail-tabs')[0].children[1].emit('click'); await settle();
  assert.ok(f.host.textContent.includes('Ранее: old.py')); assert.equal(byClass(f.host, 'gh-add').some(n => n.textContent === '+new\n'), true);
  await button(f.host, 'Ещё файлы').emit('click'); await settle();
  assert.equal(byClass(f.host, 'gh-diff').length, 2); assert.ok(f.host.textContent.includes('двоичный или слишком большой файл'));
  assert.deepEqual(f.calls.filter(c => c.operation === 'files').map(c => c.args), [
    {url: url(1), sha: '1'.repeat(40), page: 1}, {url: url(1), sha: '1'.repeat(40), page: 2}]);
});

test('file loading failures keep their message and can be retried from the changes tab', async () => {
  const f = fixture(); f.responses.files = {error: 'Synthetic files failure', code: 'github_unavailable'};
  await f.view.open(); await f.view.select(url(1)); await settle(); await byClass(f.host, 'gh-detail-tabs')[0].children[1].emit('click'); await settle();
  assert.equal(byClass(f.host, 'gh-notice')[0].textContent, 'Synthetic files failure');
  f.responses.files = {sha: '1'.repeat(40), items: [], more: false}; await byClass(f.host, 'gh-detail-tabs')[0].children[1].emit('click'); await settle();
  assert.equal(f.calls.filter(c => c.operation === 'files').length, 2); assert.equal(byClass(f.host, 'gh-notice')[0].textContent, '');
});

test('late files and timeline results do not appear under a different PR', async () => {
  const f = fixture(), files = deferred(), timeline = deferred(); f.responses.timeline = args => args.url === url(1) ? timeline.promise : {account: actor, items: [], more: false};
  await f.view.open(); await f.view.select(url(1)); await settle(); f.responses.files = files.promise;
  await byClass(f.host, 'gh-detail-tabs')[0].children[1].emit('click'); await settle(); await f.view.select(url(2)); await settle();
  files.resolve({sha: '1'.repeat(40), items: [{path: 'OLD-ONLY.txt', additions: 1, deletions: 0, patch: '+old'}], more: false});
  timeline.resolve({account: actor, items: [{id: 'old', event: 'commented', author: 'old', body: 'OLD-ONLY comment'}], more: false}); await settle();
  assert.equal(f.view.location(), url(2)); assert.equal(f.host.textContent.includes('OLD-ONLY'), false);
});

test('timeline pagination deduplicates events and supports independent activity filters', async () => {
  const f = fixture(); const comment = {id: 'one', event: 'commented', author: 'author', body: 'Useful comment', at: '2026-10-20T12:00:00Z'};
  f.responses.timeline = args => ({account: actor, more: args.page === 1, items: args.page === 1 ? [comment] :
    [comment, {id: 'one', event: 'committed', author: 'author', commit: 'a'.repeat(40), message: 'A commit'},
      {id: 'review', event: 'reviewed', author: 'reviewer', state: 'approved', body: 'Looks good'}]});
  await f.view.open(); await f.view.select(url(1)); await settle(); await button(f.host, 'Ещё события').emit('click'); await settle();
  assert.equal(byClass(f.host, 'gh-activity-row').length, 4);
  const filter = label(f.host, 'События PR'); filter.value = 'commits'; await filter.emit('change');
  assert.equal(byClass(f.host, 'gh-activity-row').length, 1); assert.ok(f.host.textContent.includes('A commit'));
  assert.equal(f.host.textContent.includes('Useful comment'), false);
});

test('timeline failures offer retry and never display events from another account', async () => {
  const f = fixture(); f.responses.timeline = {error: 'Synthetic timeline failure', code: 'github_unavailable'};
  await f.view.open(); await f.view.select(url(1)); await settle(); assert.ok(f.host.textContent.includes('Synthetic timeline failure'));
  f.responses.timeline = {account: {id: 99}, items: [{id: 'other', event: 'commented', body: 'Other account content'}], more: false};
  await button(f.host, 'Повторить загрузку').emit('click'); await settle();
  assert.equal(f.host.textContent.includes('Other account content'), false); assert.ok(f.host.textContent.includes('Аккаунт GitHub изменился'));
  f.responses.timeline = {account: actor, items: [], more: false}; await button(f.host, 'Повторить загрузку').emit('click'); await settle();
  assert.equal(f.calls.filter(c => c.operation === 'timeline').length, 3); assert.equal(f.host.textContent.includes('Synthetic timeline failure'), false);
});

test('merge is unavailable for closed, draft, unknown, or conflicting PR state', async () => {
  for (const extra of [{state: 'closed'}, {draft: true}, {mergeable: null}, {mergeable: false}]) {
    const f = fixture(); f.responses.detail = detail(1, extra); await f.view.open(); await f.view.select(url(1)); await settle();
    const merge = button(f.host, 'Merge'); assert.equal(merge.disabled, true); await merge.emit('click');
    assert.equal(f.calls.some(c => c.operation === 'merge'), false);
  }
});

test('merge requests carry the displayed head, method, and account and preserve cancellation', async () => {
  const f = fixture(); await f.view.open(); await f.view.select(url(1)); await settle();
  label(f.host, 'Способ слияния').value = 'squash'; await button(f.host, 'Merge').emit('click');
  assert.deepEqual(f.calls.find(c => c.operation === 'merge').args, {url: url(1), sha: '1'.repeat(40), method: 'squash', account_id: actor.id});
  assert.equal(byClass(f.host, 'gh-notice')[0].textContent, 'Слияние отменено.');
  assert.equal(f.calls.filter(c => c.operation === 'detail').length, 1);
});

test('a successful merge reloads authoritative details and a failure stays actionable', async () => {
  const f = fixture(); await f.view.open(); await f.view.select(url(1)); await settle();
  f.responses.merge = {error: 'Synthetic head changed', code: 'github_head_changed'}; await button(f.host, 'Merge').emit('click');
  assert.ok(byClass(f.host, 'gh-notice')[0].textContent.includes('Synthetic head changed')); assert.equal(button(f.host, 'Merge').disabled, false);
  f.responses.merge = () => { f.responses.detail = detail(1, {state: 'merged'}); return {merged: true}; };
  await button(f.host, 'Merge').emit('click'); await settle();
  assert.equal(button(f.host, 'Merge').disabled, true); assert.equal(byClass(f.host, 'gh-notice')[0].textContent, 'PR слит.');
  assert.equal(f.calls.filter(c => c.operation === 'detail').length, 2);
});

test('copy and site actions use the canonical displayed PR URL', async () => {
  const f = fixture(); await f.view.open(); await f.view.select(url(1)); await settle();
  await label(f.host, 'Скопировать ссылку PR').emit('click'); await label(f.host, 'Открыть на GitHub').emit('click');
  assert.deepEqual(f.copied, [url(1)]); assert.deepEqual(f.sites, [url(1)]);
});

test('binding and discussion use the selected chat and current account', async () => {
  const f = fixture(); await f.view.open(); await f.view.select(url(1)); await settle();
  label(f.host, 'Связанный чат PR').value = 'existing-chat'; await button(f.host, 'Связать').emit('click'); await button(f.host, 'Обсудить').emit('click');
  assert.deepEqual(f.calls.find(c => c.operation === 'bind').args, {url: url(1), session_id: 'existing-chat', account_id: actor.id});
  assert.equal(f.chats[0][0], 'existing-chat'); assert.ok(f.chats[0][2].includes(url(1)));
});

test('new chat completion binds it, refreshes PR context, and opens the approved draft', async () => {
  const f = fixture(); const pr = detail(1); await f.view.open();
  await f.view.sessionCreated({pr, draft: 'Approved draft'}, 'created-chat'); await settle();
  assert.deepEqual(f.calls.find(c => c.operation === 'bind').args, {url: url(1), session_id: 'created-chat', account_id: actor.id});
  assert.deepEqual(f.chats.at(-1), ['created-chat', 'PR 1', 'Approved draft']); assert.equal(f.view.location(), url(1));
});

test('partial metadata errors stay visible and resource links keep safe attributes', async () => {
  const f = fixture(); f.responses.detail = detail(1, {body_truncated: true,
    reviews: {items: [{author: 'reviewer', state: 'APPROVED', url: 'https://github.com/example/project/pull/1#review'}], error: '', more: true},
    checks: {items: [], error: 'Synthetic check failure', more: false},
    statuses: {items: [{name: 'Untrusted scheme', state: 'pending', url: 'javascript:alert(1)'}], error: '', more: false}});
  await f.view.open(); await f.view.select(url(1)); await settle();
  assert.ok(f.host.textContent.includes('Synthetic check failure')); assert.ok(f.host.textContent.includes('Описание обрезано'));
  const links = all(f.host).filter(n => n.tag === 'a'); const safe = links.find(n => n.textContent.includes('@reviewer'));
  assert.equal(safe.target, '_blank'); assert.equal(safe.rel, 'noopener noreferrer');
  assert.equal(links.find(n => n.textContent.includes('Untrusted scheme')).href, undefined);
});
