const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { catalog, findRoute, settingsPanels } = require('../ui-navigation.cjs');

test('the shared UI catalog maps only real settings panels and known actions', () => {
  const source = fs.readFileSync(path.join(__dirname, '../owner-ui.js'), 'utf8');
  const panels = source.slice(source.indexOf('const PANELS ='), source.indexOf('\n];', source.indexOf('const PANELS =')));
  assert.equal(new Set(catalog.map(route => route.id)).size, catalog.length);
  for (const route of catalog) {
    assert.equal(findRoute(route.id), route);
    if (route.kind === 'settings') assert.match(panels, new RegExp("id:\\s*'" + route.target + "'"));
  }
  assert.equal(findRoute('models').target, 'provider');
  assert.ok(settingsPanels.has('notifications'));
  const panelIds = [...panels.matchAll(/id:\s*'([^']+)'/g)].map(match => match[1]);
  assert.deepEqual([...settingsPanels].sort(), panelIds.sort());
  for (const value of ['logout', '../keys', 'plugins?command=run', 'igorek://ui/plugins', {}, null]) assert.equal(findRoute(value), undefined);
  const html = fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/index.html'), 'utf8');
  const chrome = fs.readFileSync(path.join(__dirname, '../window-chrome.js'), 'utf8');
  const notifications = fs.readFileSync(path.join(__dirname, '../notification-ui.js'), 'utf8');
  const app = fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/app.js'), 'utf8');
  const tabs = app.slice(app.indexOf('const PANEL_TAB_CAPTION_KEYS ='), app.indexOf('\n};', app.indexOf('const PANEL_TAB_CAPTION_KEYS =')));
  for (const key of [...tabs.matchAll(/\s+(\w+):/g)].map(match => match[1])) assert.ok(catalog.some(route => route.kind === 'panel' && route.target === key), key);
  for (const route of catalog.filter(route => route.kind === 'workspace')) assert.ok([html, chrome, notifications].some(source => source.includes(route.target)), route.id);
});

test('navigation IPC requires the trusted chat and opens only the selected fixed destination', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  const start = source.indexOf("ipcMain.handle('igorek:ui-navigation'");
  const end = source.indexOf("ipcMain.handle('igorek:session-plugins'", start);
  let handler; const opened = [];
  vm.runInNewContext(source.slice(start, end), { ipcMain: { handle: (_, fn) => handler = fn },
    sessionMcp: { trusted: event => event.trusted }, navigationCatalog: catalog, navigationRoute: findRoute,
    openOwnerWindow: panel => opened.push(panel), openDesktopBrowser: async () => { opened.push('browser'); return true; },
    openAbout: () => opened.push('about'), openShortcuts: () => opened.push('shortcuts') });
  await assert.rejects(handler({}, 'catalog'));
  assert.equal((await handler({ trusted: true }, 'catalog')).routes.length, catalog.length);
  assert.equal((await handler({ trusted: true }, 'open', 'notifications')).opened, true);
  assert.deepEqual(opened, ['notifications']);
  assert.equal((await handler({ trusted: true }, 'open', 'plugins')).kind, 'workspace');
  await handler({ trusted: true }, 'open', 'browser-window');
  assert.deepEqual(opened, ['notifications', 'browser']);
  for (const id of ['logout', 'file:///tmp/keys', 'plugins?x=1', undefined]) assert.equal((await handler({ trusted: true }, 'open', id)).error, 'invalid_request');
  assert.equal((await handler({ trusted: true }, 'catalog', 'unexpected')).error, 'invalid_request');
});

class Node {
  constructor(tag, text = '') { this.tagName = tag; this._text = text; this.children = []; this.dataset = {}; this.events = {}; }
  append(...children) { for (const child of children) { if (child.tagName === '#fragment') this.append(...child.children); else { child.parent = this; this.children.push(child); } } }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  replaceChildren(...children) { this._text = ''; this.children = []; this.append(...children); }
  addEventListener(kind, fn) { this.events[kind] = fn; }
  replaceWith(node) { const index = this.parent.children.indexOf(this); node.parent = this.parent; this.parent.children[index] = node; }
  querySelectorAll(selector) { return this.children.flatMap(child => [ ...(child.tagName === 'span' && child.dataset.uiRoute ? [child] : []), ...child.querySelectorAll(selector)]); }
}
function renderer() {
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/render.js'), 'utf8'), {
    window, document: { createElement: tag => new Node(tag), createTextNode: text => new Node('#text', text), createDocumentFragment: () => new Node('#fragment') },
  });
  return window.IgorekMarkdown;
}
function descendants(node, tag) { return node.children.flatMap(child => [ ...(child.tagName === tag ? [child] : []), ...descendants(child, tag)]); }

test('only explicit allowlisted links activate; normal words, code and arbitrary URLs never become UI actions', () => {
  const md = renderer(), container = new Node('div'), calls = [];
  md.renderMarkdown('Плагины и уведомления. [Открыть плагины](igorek://ui/plugins). [Bad](igorek://ui/logout). ` [Code](igorek://ui/models) `', container);
  assert.equal(descendants(container, 'a').length, 0);
  md.activateNavigation(container, catalog, id => calls.push(id));
  const links = descendants(container, 'a');
  assert.equal(links.length, 1); assert.equal(links[0].textContent, 'Открыть плагины');
  assert.equal(links[0].href, '#'); assert.equal(calls.length, 0);
  let prevented = false; links[0].events.click({ preventDefault: () => prevented = true });
  assert.ok(prevented); assert.deepEqual(calls, ['plugins']);
  md.renderMarkdown('[Open](igorek://ui/plugins?command=logout) and [Site](https://example.org)', container);
  md.activateNavigation(container, catalog, id => calls.push(id));
  assert.equal(descendants(container, 'a').length, 1);
  assert.equal(descendants(container, 'a')[0].href, 'https://example.org');
});

test('without a desktop catalog internal links remain inert, including in fenced samples', () => {
  const md = renderer(), container = new Node('div');
  md.renderMarkdown('[Settings](igorek://ui/models)\n\n```text\n[Sample](igorek://ui/plugins)\n```', container);
  md.activateNavigation(container, [], () => { throw Error('must not open'); });
  assert.equal(descendants(container, 'a').length, 0);
});
