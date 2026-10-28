const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { SettingsView } = require('../settings-view.cjs');
const { isOwnerFrame } = require('../owner-policy.cjs');

function harness() {
  let destroyed = false, parentDestroyed = false, closes = 0, focuses = 0;
  const contents = new EventEmitter();
  Object.assign(contents, {
    session: {
      setPermissionRequestHandler(fn) { this.request = fn; },
      setPermissionCheckHandler(fn) { this.check = fn; },
    },
    isDestroyed: () => destroyed,
    setWindowOpenHandler(fn) { this.popup = fn; },
    mainFrame: { url: 'file:///owner.html' },
    async loadURL(url) { this.loaded = url; },
    focus() { focuses++; },
    close() { closes++; destroyed = true; this.emit('destroyed'); },
  });
  let options, view;
  class View {
    constructor(value) { options = value; this.webContents = contents; view = this; }
    setBackgroundColor(value) { this.background = value; }
    setBounds(value) { this.bounds = value; }
    setVisible(value) { this.visible = value; }
  }
  const parent = new EventEmitter();
  Object.assign(parent, {
    size: [1280, 900],
    isDestroyed: () => parentDestroyed,
    getContentSize: () => parent.size,
    webContents: { draft: 'Retained draft', history: ['Original chat'], reload() { throw Error('Chat must not reload'); } },
    show() {}, focus() {},
    contentView: {
      children: [],
      addChildView(child) { this.children = this.children.filter(item => item !== child).concat(child); },
      removeChildView(child) { this.children = this.children.filter(item => item !== child); },
    },
  });
  const panel = new SettingsView({ parent, WebContentsView: View, preload: '/owner-preload.cjs', url: 'file:///owner.html' });
  return { panel, parent, contents, view, options, get closes() { return closes; }, get focuses() { return focuses; },
    closeParent() { parentDestroyed = true; parent.emit('closed'); } };
}

test('settings use a separate privileged renderer inside the existing chat window', async () => {
  const h = harness();
  assert.deepEqual(h.options.webPreferences, {
    preload: '/owner-preload.cjs', nodeIntegration: false, contextIsolation: true,
    sandbox: true, webSecurity: true, partition: 'igorek-desktop-owner-settings',
  });
  assert.deepEqual(h.parent.contentView.children, [h.view]);
  await h.panel.loadURL('file:///owner.html');
  h.panel.close();
  assert.equal(h.parent.webContents.draft, 'Retained draft');
  assert.deepEqual(h.parent.webContents.history, ['Original chat']);
  assert.deepEqual(h.parent.contentView.children, []);
  assert.equal(h.closes, 1);
});

test('chat and forged subframes cannot use the embedded settings identity', () => {
  const h = harness(), url = 'file:///owner.html';
  assert.equal(isOwnerFrame({sender: h.contents, senderFrame: h.contents.mainFrame}, h.panel.webContents, url), true);
  assert.equal(isOwnerFrame({sender: h.parent.webContents, senderFrame: h.contents.mainFrame}, h.panel.webContents, url), false);
  assert.equal(isOwnerFrame({sender: h.contents, senderFrame: {url}}, h.panel.webContents, url), false);
  h.panel.close();
  assert.equal(isOwnerFrame({sender: h.contents, senderFrame: h.contents.mainFrame}, h.panel.webContents, url), false);
});

test('settings deny navigation, redirects, embedded webviews, popups and media', async () => {
  const h = harness();
  for (const event of ['will-navigate', 'will-redirect', 'will-attach-webview']) {
    let prevented = false;
    h.contents.emit(event, {preventDefault() { prevented = true; }});
    assert.equal(prevented, true);
  }
  assert.deepEqual(h.contents.popup({url: 'https://example.com'}), {action: 'deny'});
  let allowed;
  h.contents.session.request(h.contents, 'media', value => { allowed = value; });
  assert.equal(allowed, false);
  assert.equal(h.contents.session.check(h.contents, 'media'), false);
  await assert.rejects(h.panel.loadURL('https://example.com'));
  assert.equal(h.contents.loaded, undefined);
});

test('resizing preserves the main title bar and repeated focus does not duplicate the view', () => {
  const h = harness();
  assert.deepEqual(h.view.bounds, {x: 0, y: 36, width: 1280, height: 864});
  h.parent.size = [900, 600]; h.parent.emit('resize');
  assert.deepEqual(h.view.bounds, {x: 0, y: 36, width: 900, height: 564});
  h.panel.focus(); h.panel.focus();
  assert.equal(h.parent.contentView.children.length, 1);
  assert.equal(h.view.visible, true);
});

test('back cleanup is idempotent and releases parent listeners and privileged contents', () => {
  const h = harness(); let closed = 0;
  h.panel.on('closed', () => closed++);
  h.panel.close(); h.panel.close(); h.parent.emit('resize');
  assert.equal(closed, 1);
  assert.equal(h.closes, 1);
  assert.equal(h.parent.listenerCount('resize'), 0);
  assert.equal(h.parent.listenerCount('closed'), 0);
  assert.equal(h.panel.isDestroyed(), true);
});

test('closing the parent or losing the privileged renderer releases the view once', () => {
  const h = harness(); let closed = 0;
  h.panel.on('closed', () => closed++);
  h.closeParent(); h.panel.close();
  assert.equal(closed, 1);
  assert.equal(h.closes, 1);
  const crashed = harness();
  crashed.contents.close();
  assert.equal(crashed.panel.isDestroyed(), true);
  assert.deepEqual(crashed.parent.contentView.children, []);
  assert.equal(crashed.parent.webContents.draft, 'Retained draft');
});

test('an old load completion cannot focus a closed settings panel', async () => {
  const h = harness(); let finish;
  h.contents.loadURL = () => new Promise(resolve => { finish = resolve; });
  const loading = h.panel.loadURL('file:///owner.html');
  h.panel.close(); finish(); await loading;
  assert.equal(h.focuses, 0);
  assert.deepEqual(h.parent.contentView.children, []);
});

test('settings relayout respects the distinct workspace-browser API', async () => {
  const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
  const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  const begin = source.indexOf('function settingsOverlayActive(');
  const end = source.indexOf('function openOwnerWindow(', begin);
  const calls = [], parent = {};
  const context = vm.createContext({
    sessionOverlays: new WeakSet(), ownerWindow: {parent, isDestroyed: () => false},
    desktopBrowser: {layout: () => calls.push('agent')},
    chatgptWorkspace: {layout: async () => calls.push('chatgpt')},
    workspaceBrowser: {parent, action: async (target, action) => { assert.equal(target, parent); assert.equal(action, 'layout'); calls.push('browser'); }},
    opencodeWorkspace: {layout: async () => calls.push('opencode')},
  });
  vm.runInContext(source.slice(begin, end), context);
  assert.equal(context.settingsOverlayActive(parent), true);
  assert.equal(context.settingsOverlayActive({}), false);
  context.refreshEmbeddedSurfaces();
  await Promise.resolve();
  assert.deepEqual(calls, ['agent', 'chatgpt', 'browser', 'opencode']);
  context.ownerWindow = null;
  assert.equal(context.settingsOverlayActive(parent), false);
  context.sessionOverlays.add(parent);
  assert.equal(context.settingsOverlayActive(parent), true);
});
