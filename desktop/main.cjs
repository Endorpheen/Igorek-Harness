const { app, BrowserWindow, WebContentsView, session: electronSession, Notification, dialog, Menu, shell, ipcMain, nativeTheme, contentTracing } = require('electron');
const { pathToFileURL } = require('node:url');
const { isOwnerFrame, shouldRefreshProfiles, ownedDataLocations, ownerOpenCodeHandler } = require('./owner-policy.cjs');
const { installedImage } = require('./installed-image.cjs');
const { SettingsView } = require('./settings-view.cjs');
const { updateDialog } = require('./release-updates-ui.cjs');
const { createUpdatePresentation } = require('./release-progress.cjs');
const { launchInstallation, showInstallationResult } = require('./release-install.cjs');
let showReleaseUpdates;
const { saveRecovery, verifyRecovery, restoreRecovery } = require('./recovery-files.cjs');
const { access, stat } = require('node:fs/promises');
const { constants, readFileSync } = require('node:fs');
const { windowMenu } = require('./window-menu.cjs');
const {GITHUB_URL}=require('./links.cjs');
let aboutWindow;
let whatsNew;
const {WhatsNew,installationIdentity}=require('./whats-new.cjs');
const { ShortcutStore, matchesShortcut } = require('./shortcuts.cjs');
const { join, resolve } = require('node:path');
const { catalog: navigationCatalog, findRoute: navigationRoute, settingsPanels } = require('./ui-navigation.cjs');
const { OwnedRuntime, loadBundle } = require('./runtime.cjs');
const { NotificationStore } = require('./notifications.cjs');
const errorDiagnostics = require('./error-diagnostics.js');
const { CompletionSound } = require('./completion-sound.cjs');
const {ResourceSampler, ResourceMonitorSettings} = require('./resource-monitor.cjs');
const { settings, sameOrigin, certificateMatches, allowAudio, allowClipboardWrite } = require('./policy.cjs');
let config;
let runtime;
let desktopBrowser;
const browserControlUrl = pathToFileURL(join(__dirname, 'browser.html')).href;
const { DesktopBrowser } = require('./browser.cjs');
const { ChatGPTWorkspace } = require('./chatgpt-workspace.cjs');
const { WorkspaceBrowser } = require('./workspace-browser.cjs');
const { OpenCodeService } = require('./opencode-service.cjs');
const { OpenCodeWorkspace } = require('./opencode-workspace.cjs');
const { OpenCodeTasks, sessionRoute } = require('./opencode-tasks.cjs');
const { GitHubReviews } = require('./github-reviews.cjs');
const { reviewRequest } = require('./github-controller.cjs');
let githubReviews;
let quitting = false;
let installingUpdate = false;
const managed = app.isPackaged || process.argv.slice(2).includes("--managed");
const partition = managed ? "persist:igorek-desktop-owned" : "persist:igorek-desktop-prototype";
let window;
let ownerWindow;
let shortcutsWindow;
let selectedWorkspace;
const chatWindows = new Set();
const chromeWindows = new Map();
const diagnosticWindows = new Map();
let tracing = false;
let traceBusy = false;
let chatgptWorkspace;
let workspaceBrowser;
let opencodeService;
let opencodeWorkspace;
let opencodeTasks;
let opencodeUnlocked=false;
const ownerUrl = pathToFileURL(join(__dirname, 'owner.html')).href;
const trustedContents = new Set();
const APP_ICON=join(__dirname,'assets/igorek.png');

app.setName('Игорёк');
if (process.platform === 'linux') {
  app.setDesktopName('igorek-desktop.desktop');
  app.commandLine.appendSwitch('class', 'igorek-desktop');
}
app.on('browser-window-created', (_, target) => {
  target.once('ready-to-show',()=>{if(!target.isDestroyed())target.setIcon(APP_ICON);});
});
// Keep prototype cookies separate from browsers and the future packaged app.
app.setPath('userData', join(app.getPath('appData'), managed ? 'igorek-desktop-owned' : 'igorek-desktop-prototype'));
const shortcutStore = new ShortcutStore(join(app.getPath('userData'), 'shortcuts.json'));
const notificationStore = new NotificationStore(join(app.getPath('userData'), 'notifications.json'));
const { TextToSpeech } = require('./tts.cjs');
const textToSpeech = new TextToSpeech(join(app.getPath('userData'), 'tts.json'));
const completionSound = new CompletionSound(join(app.getPath('userData'), 'completion-sound.json'));
const resourceSettings = new ResourceMonitorSettings(join(app.getPath('userData'), 'resource-monitor.json'));
let resourceWindow, resourceSampler, resourceMonitorShutdown = false;
const resourceURL = pathToFileURL(join(__dirname, 'resource-monitor.html')).href;
app.on('before-quit', () => { resourceMonitorShutdown = true; });
function publishResourceMonitor() {
  if (ownerWindow && !ownerWindow.isDestroyed() && ownerWindow.webContents.getURL() === ownerUrl)
    ownerWindow.webContents.send('igorek:resource-monitor-state', resourceSettings.state());
}
function openResourceMonitor() {
  if (resourceWindow && !resourceWindow.isDestroyed()) { resourceWindow.show(); return; }
  const target = new BrowserWindow({icon: APP_ICON, title: 'Монитор ресурсов', width: 302, height: 434,
    minWidth: 270, minHeight: 320, frame: false, show: false, skipTaskbar: true, parent: window,
    autoHideMenuBar: true, backgroundColor: '#171d1a',
    webPreferences: {preload: join(__dirname, 'resource-monitor-preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true}});
  resourceWindow = target; resourceSampler = new ResourceSampler();
  const bounds = window.getBounds(); target.setPosition(bounds.x + Math.max(0, bounds.width - 326), bounds.y + 84);
  target.setMenu(null); target.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  target.webContents.on('will-navigate', event => event.preventDefault());
  target.webContents.on('will-redirect', event => event.preventDefault());
  target.webContents.on('will-attach-webview', event => event.preventDefault());
  target.once('ready-to-show', () => { if (resourceSettings.enabled && !target.isDestroyed()) target.showInactive(); });
  target.on('closed', () => {
    if (resourceWindow !== target) return;
    resourceWindow = null; resourceSampler = null;
    if (!resourceMonitorShutdown && window && !window.isDestroyed() && window.isVisible() && resourceSettings.enabled) {
      resourceSettings.set(false); publishResourceMonitor();
    }
  });
  target.loadURL(resourceURL).catch(() => { if (!target.isDestroyed()) target.close(); });
}
ipcMain.handle('igorek:resource-monitor', async (event, action, value) => {
  const monitor = resourceWindow && !resourceWindow.isDestroyed() && event.sender === resourceWindow.webContents
    && event.senderFrame === event.sender.mainFrame && event.senderFrame.url === resourceURL;
  if (monitor && action === 'snapshot' && resourceSettings.enabled) return resourceWindow.isVisible() ? resourceSampler.snapshot() : null;
  if (monitor && action === 'close') {
    resourceSettings.set(false); resourceWindow.close(); publishResourceMonitor(); return resourceSettings.state();
  }
  if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw Error('Доступ запрещён.');
  if (action === 'read') return resourceSettings.state();
  if (action !== 'set' || typeof value !== 'boolean') throw Error('Недопустимое действие.');
  resourceSettings.set(value);
  if (value) openResourceMonitor(); else resourceWindow?.close();
  publishResourceMonitor(); return resourceSettings.state();
});
ipcMain.handle('igorek:tts', async (event, action, value = {}) => {
  const ownerFrame = isOwnerFrame(event, ownerWindow?.webContents, ownerUrl);
  if (!ownerFrame && !sessionMcp.trusted(event)) throw Error('Доступ запрещён.');
  if (action === 'read') return textToSpeech.state();
  if (action === 'cancel') return textToSpeech.cancel(event.sender.id, value.id);
  const status = await runtime.requestOwner('status');
  if (status.result?.state !== 'unlocked' || status.result?.interface_locked) throw Error('Разблокируйте Игорька для озвучивания.');
  if (!ownerFrame && action !== 'speak') throw Error('Доступ запрещён.');
  if (action === 'save') { const config = textToSpeech.save(value); for (const contents of chromeWindows.keys()) if (!contents.isDestroyed()) contents.send('igorek:tts-state', config); return config; }
  if (action === 'models') return textToSpeech.models(value);
  if (action === 'preview') return textToSpeech.speak(event.sender.id, value, true);
  if (action === 'speak') return textToSpeech.speak(event.sender.id, value);
  throw Error('Недопустимое действие озвучивания.');
});
ipcMain.handle('igorek:completion-sound', (event, action, value) => {
  const target = trustedChromeWindow(event);
  if (action === 'read') return completionSound.state(target);
  if (action === 'claim' && sessionMcp.trusted(event)) return completionSound.claim(target, value);
  if (action === 'set' && isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) return completionSound.set(value);
  throw new Error('Доступ запрещён.');
});
ipcMain.handle('igorek:notifications', (event, action, value) => {
  trustedChromeWindow(event);
  let items;
  if (action === 'list') return notificationStore.snapshot();
  if (action === 'snapshot') return notificationStore.report();
  if (action === 'add') items = notificationStore.add(value && value.level === 'error' && !value.diagnostic
    ? {...value,diagnostic:errorDiagnostics.observation('ERROR.UNKNOWN',{component:'desktop',operation:'operation',endpoint_role:'desktop_to_runtime',owner:'desktop-ui:'+errorDiagnostics.scope(value.source || 'application')})} : value);
  else if (action === 'read') items = notificationStore.read();
  else if (action === 'clear') items = notificationStore.clear();
  else if (action === 'recover') items = notificationStore.recover(value);
  else if (action === 'confirm') items = notificationStore.confirm(value);
  else throw new Error('Недопустимое действие.');
  for (const [contents, entry] of chromeWindows) {
    if (!contents.isDestroyed() && entry.urlCheck(contents.getURL())) contents.send('igorek:notifications-changed', items);
  }
  return items;
});

ipcMain.handle('igorek:diagnostic-support', async (event, value) => {
  const target=trustedChromeWindow(event);
  const support=require('./diagnostic-support.cjs');
  if(value?.action==='prepare')return support.createReport(value.report,{version:require('./package.json').version,surface:process.platform});
  if(value?.action!=='save')throw new Error('Invalid diagnostic support action');
  return support.saveAndCompose(value,{
    version:require('./package.json').version,surface:process.platform,documents:app.getPath('documents'),
    chooseFile:options=>dialog.showSaveDialog(target,options),openComposer:url=>shell.openExternal(url),
    isActive:async()=>{try{return trustedChromeWindow(event)===target && await support.previewIsOpen(event.sender,value.report?.report_id);}catch{return false;}},
  });
});
function installWindowChrome(target, urlCheck) {
  const contents = target.webContents;
  completionSound.attach(target, urlCheck);
  chromeWindows.set(contents, { target, urlCheck });
  target.setMenuBarVisibility(false);
  target.on('closed', () => { chromeWindows.delete(contents); diagnosticWindows.delete(contents); });
  target.webContents.on('did-finish-load', async () => {
    if (!urlCheck(target.webContents.getURL())) return;
    try {
      await target.webContents.insertCSS(readFileSync(join(__dirname, 'window-chrome.css'), 'utf8'));
      await target.webContents.executeJavaScript('(()=>{'+readFileSync(join(__dirname, 'profile-cards.cjs'), 'utf8')+'})()');
      await target.webContents.executeJavaScript(readFileSync(join(__dirname, 'window-chrome.js'), 'utf8'));
      await target.webContents.executeJavaScript(readFileSync(join(__dirname, 'notification-ui.js'), 'utf8'));
    } catch { /* Closing a window can interrupt its chrome installation. */ }
  });
}
function trustedChromeWindow(event) {
  const entry = chromeWindows.get(event.sender);
  if (!entry || entry.target.isDestroyed() || event.senderFrame !== event.sender.mainFrame
      || !entry.urlCheck(event.senderFrame.url)) throw new Error('Доступ запрещён.');
  return entry.target;
}
ipcMain.handle('igorek:window-control', (event, action) => {
  const target = trustedChromeWindow(event);
  if (action === 'minimize') target.minimize();
  else if (action === 'maximize') target.isMaximized() ? target.unmaximize() : target.maximize();
  else if (action === 'close') {
    if (event.sender === ownerWindow?.webContents) ownerWindow.close();
    else target.close();
  }
  else throw new Error('Недопустимое действие.');
});
ipcMain.handle('igorek:window-menu', (event, label, x, y) => {
  const target = trustedChromeWindow(event);
  const menu = Menu.getApplicationMenu()?.items.find(item => item.label === label)?.submenu;
  if (!menu || !Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0
      || x > target.getContentBounds().width || y > target.getContentBounds().height) throw new Error('Недопустимое меню.');
  menu.popup({ window: target, x, y });
});
function chatTarget(focused) {
  return chatWindows.has(focused) && !focused.isDestroyed() ? focused : window;
}
function chatScript(focused, script) {
  const target = chatTarget(focused);
  if (!target || target.isDestroyed() || !sameOrigin(target.webContents.getURL(), config.origin)) return;
  target.focus(); target.webContents.executeJavaScript(script).catch(() => {});
}
function focusedChatAction(focused, action, index) {
  if (!chatWindows.has(focused) || focused.isDestroyed() || !focused.webContents.isFocused()) return;
  chatScript(focused, `window.dispatchEvent(new CustomEvent('igorek:chat-shortcut',{detail:${JSON.stringify({ action, index })}}));`);
}
function openShortcuts() {
  if (shortcutsWindow && !shortcutsWindow.isDestroyed()) { shortcutsWindow.focus(); return; }
  const url = pathToFileURL(join(__dirname, 'shortcuts.html')).href;
  const target = new BrowserWindow({ icon: APP_ICON, title: 'Сочетания клавиш', width: 1120, height: 860, minWidth: 600, minHeight: 480,
    frame: false, backgroundColor: '#171b1a', webPreferences: { preload: join(__dirname, 'shortcuts-preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true } });
  shortcutsWindow = target;
  installWindowChrome(target, current => current === url);
  target.webContents.setIgnoreMenuShortcuts(true);
  target.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  target.webContents.on('will-navigate', event => event.preventDefault());
  target.webContents.on('will-redirect', event => event.preventDefault());
  target.webContents.on('will-attach-webview', event => event.preventDefault());
  target.webContents.session.setPermissionRequestHandler((_, __, callback) => callback(false));
  target.on('closed', () => { shortcutsWindow = null; });
  target.loadURL(url).catch(() => target.close());
}
ipcMain.handle('igorek:shortcuts', (event, operation, id, keys) => {
  trustedChromeWindow(event);
  if (operation === 'labels') return shortcutStore.keys('sidebar');
  if (event.sender !== shortcutsWindow?.webContents) throw Error('Доступ запрещён.');
  if (operation === 'list') return shortcutStore.snapshot();
  if (!['set', 'reset'].includes(operation)) throw Error('Недопустимое действие.');
  try {
    const result = operation === 'set' ? shortcutStore.set(id, keys) : shortcutStore.reset();
    updateApplicationMenu();
    for (const target of chatWindows) if (!target.isDestroyed()) target.webContents.send('igorek:shortcuts-changed', shortcutStore.keys('sidebar'));
    return result;
  } catch (error) { return { error: error.message }; }
});
function openDiagnostics(kind) {
  const existing = [...diagnosticWindows].find(([contents, item]) => item.kind === kind && !contents.isDestroyed());
  if (existing) { existing[1].target.focus(); return; }
  const url = pathToFileURL(join(__dirname, 'diagnostics.html')).href;
  const target = new BrowserWindow({ icon:APP_ICON, title: kind === 'tasks' ? 'Диспетчер задач' : 'Статус системы', width: 780, height: 640,
    frame: false, autoHideMenuBar: false, backgroundColor: '#171b19',
    webPreferences: { preload: join(__dirname, 'diagnostics-preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true } });
  diagnosticWindows.set(target.webContents, { target, kind });
  installWindowChrome(target, current => current === url);
  target.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  target.webContents.on('will-navigate', event => event.preventDefault());
  target.webContents.on('will-redirect', event => event.preventDefault());
  target.webContents.on('will-attach-webview', event => event.preventDefault());
  target.webContents.session.setPermissionRequestHandler((_, __, callback) => callback(false));
  target.loadURL(url).catch(() => target.close());
}
ipcMain.handle('igorek:diagnostics', async event => {
  trustedChromeWindow(event);
  const item = diagnosticWindows.get(event.sender);
  if (!item) throw new Error('Доступ запрещён.');
  if (item.kind === 'tasks') {
    const rows = app.getAppMetrics().map(metric => ({ name: (metric.name || metric.type) + ' · PID ' + metric.pid,
      value: 'CPU: ' + metric.cpu.percentCPUUsage.toFixed(1) + '% · память: ' + (metric.memory.workingSetSize / 1024).toFixed(1) + ' МБ' }));
    if (runtime?.child && runtime.child.exitCode === null) {
      let memory = 'недоступно';
      try { memory = (Number(readFileSync('/proc/' + runtime.child.pid + '/status', 'utf8').match(/^VmRSS:\s+(\d+)/m)?.[1]) / 1024).toFixed(1) + ' МБ'; } catch { /* Non-Linux platforms may not expose procfs. */ }
      rows.push({ name: 'Backend и исполнитель · PID ' + runtime.child.pid, value: 'Работает · память: ' + memory });
    }
    return { title: 'Диспетчер задач', note: 'Процессы этого экземпляра Игорька. CPU измеряется между обновлениями; управление системными процессами не выполняется.', rows };
  }
  let owner = null;
  if (runtime && !runtime.requests.size) owner = await runtime.requestOwner('status').catch(() => null);
  return { title: 'Статус системы', note: 'Локальный desktop runtime и подключение этого приложения.', rows: [
    { name: 'Версия Игорька', value: config.factoryVersion || config.factory_version || app.getVersion() },
    { name: 'Платформа', value: process.platform + ' ' + process.arch + ' · Electron ' + process.versions.electron },
    { name: 'Backend', value: runtime ? runtime.child?.exitCode === null ? 'Работает · PID ' + runtime.child.pid : 'Остановлен' : 'Подключение к существующему серверу' },
    { name: 'Соединение', value: config.origin.startsWith('https:') ? 'HTTPS' : 'HTTP' },
    { name: 'Хранилище', value: owner?.result?.state === 'unlocked' ? 'Разблокировано' : owner?.result?.state === 'locked' ? 'Заблокировано' : 'Состояние сейчас недоступно' },
    { name: 'Исполнитель', value: owner?.result?.agent?.running ? 'Работает · ' + owner.result.agent.profile : owner?.result ? 'Остановлен' : 'Состояние сейчас недоступно' },
  ] };
});
async function toggleTrace() {
  if (traceBusy) return;
  traceBusy = true;
  try {
    if (!tracing) {
      await contentTracing.startRecording({ included_categories: ['toplevel', 'blink', 'cc', 'gpu', 'v8'] });
      tracing = true;
    } else {
      const selected = await dialog.showSaveDialog(BrowserWindow.getFocusedWindow() || window, {
        title: 'Сохранить трассировку производительности', defaultPath: 'igorek-performance.json',
        filters: [{ name: 'Трассировка JSON', extensions: ['json'] }],
      });
      if (selected.canceled) return;
      await contentTracing.stopRecording(selected.filePath); tracing = false;
    }
    updateApplicationMenu();
  } catch { dialog.showErrorBox('Трассировка', 'Не удалось начать или сохранить трассировку. Повторите попытку.'); }
  finally { traceBusy = false; }
}
function desktopReleaseInfo(){
  const version=String(config?.factoryVersion||config?.factory_version||app.getVersion());
  let changes=[],channel='stable';
  try{const notes=JSON.parse(readFileSync(join(__dirname,'assets','release-notes.json'),'utf8'));if(notes.version===version&&Array.isArray(notes.changes)){changes=notes.changes.filter(text=>typeof text==='string'&&text.trim()&&text.length<=160).slice(0,4);if(notes.channel==='preview')channel='preview';}}catch{}
  return {version,changes,...(channel==='preview'?{channel}: {})};
}
function openAbout(){
  if(aboutWindow&&!aboutWindow.isDestroyed()){aboutWindow.focus();return;}
  const parent=BrowserWindow.getFocusedWindow()||window;
  const info=desktopReleaseInfo();
  const target=new BrowserWindow({parent,modal:Boolean(parent),width:420,height:340+info.changes.length*28,resizable:false,minimizable:false,maximizable:false,title:'О программе Игорёк',icon:APP_ICON,backgroundColor:'#1b211e',autoHideMenuBar:true,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,webSecurity:true,partition:'igorek-about'}});
  aboutWindow=target;target.setMenuBarVisibility(false);target.on('closed',()=>{if(aboutWindow===target)aboutWindow=null;});
  target.webContents.session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
  target.webContents.setWindowOpenHandler(({url})=>{if(url===GITHUB_URL)shell.openExternal(GITHUB_URL).catch(()=>{});return {action:'deny'};});
  target.webContents.on('will-navigate',event=>event.preventDefault());target.webContents.on('will-attach-webview',event=>event.preventDefault());
  target.loadFile(join(__dirname,'about.html'),{query:{version:info.version,github:GITHUB_URL,changes:JSON.stringify(info.changes)}}).catch(()=>target.close());
}
function updateApplicationMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate(windowMenu({
    newWindow: () => openWindow(),
    newSession: (_, focused) => chatScript(focused, "document.getElementById('new-session')?.click()"),
    openFolder: (_, focused) => chatScript(focused, "document.getElementById('new-session')?.click(); document.getElementById('desktop-choose-workspace')?.click()"),
    settings: openOwnerWindow,
    browser: () => openDesktopBrowser(),
    chatgpt: (_,focused) => chatScript(focused, "window.dispatchEvent(new Event('igorek:open-chatgpt-tab'))"),
    sidebar: (_, focused) => chatScript(focused, "document.getElementById('igorek-toggle-sidebar')?.click()"),
    panel: (_, focused) => chatScript(focused, "document.getElementById('toggle-panel')?.click()"),
    terminal: (_, focused) => chatScript(focused, "if(document.getElementById('side-panel')?.hidden) document.getElementById('toggle-panel')?.click(); document.getElementById('side-tab-terminal')?.click()"),
    shortcuts: openShortcuts,
    focusComposer: (_, focused) => focusedChatAction(focused, 'focusComposer'),
    sendMessage: (_, focused) => focusedChatAction(focused, 'sendMessage'),
    previousChat: (_, focused) => focusedChatAction(focused, 'previousChat'),
    nextChat: (_, focused) => focusedChatAction(focused, 'nextChat'),
    pinChat: (_, focused) => focusedChatAction(focused, 'pinChat'),
    ...Object.fromEntries(Array.from({ length: 9 }, (_, index) => [`recentChat${index + 1}`, (_, focused) => focusedChatAction(focused, 'recentChat', index)])),
    reload: (_, focused) => { if (focused && !focused.isDestroyed()) focused.reload(); },
    fullscreen: (_, focused) => { if (focused && !focused.isDestroyed()) focused.setFullScreen(!focused.isFullScreen()); },
    status: () => openDiagnostics('status'), tasks: () => openDiagnostics('tasks'), trace: toggleTrace,
    about: openAbout,
    whatsNew:()=>whatsNew?.show(BrowserWindow.getFocusedWindow()||window).catch(()=>{}),
  }, tracing, id => shortcutStore.keys(id))));
  for (const { target } of chromeWindows.values()) if (!target.isDestroyed()) target.setMenuBarVisibility(false);
}

function installContextMenu(target, parent = target) {
  target.webContents.on('context-menu', (_, params) => {
    const items = [];
    if (params.isEditable) {
      if (params.inputFieldType !== 'password') items.push(
        { role: 'cut', label: 'Вырезать', enabled: params.editFlags.canCut },
        { role: 'copy', label: 'Копировать', enabled: params.editFlags.canCopy });
      items.push({ role: 'paste', label: 'Вставить', enabled: params.editFlags.canPaste },
        { role: 'selectAll', label: 'Выделить всё' });
    } else if (params.selectionText) items.push({ role: 'copy', label: 'Копировать' });
    if (items.length) Menu.buildFromTemplate(items).popup({ window: parent });
  });
}
function settingsOverlayActive(parent) {
  return sessionOverlays.has(parent) || Boolean(ownerWindow && !ownerWindow.isDestroyed() && ownerWindow.parent === parent);
}
function refreshEmbeddedSurfaces() {
  desktopBrowser?.layout();
  chatgptWorkspace?.layout().catch(() => {});
  if (workspaceBrowser?.parent) workspaceBrowser.action(workspaceBrowser.parent, 'layout').catch(() => {});
  opencodeWorkspace?.layout().catch(() => {});
}
function openOwnerWindow(panel, plugin) {
  if (!managed || !runtime || !window) return;
  const parent = chatTarget(BrowserWindow.getFocusedWindow());
  if (!parent || parent.isDestroyed()) return;
  const selectPanel = target => { if (settingsPanels.has(panel) && target && !target.isDestroyed()) target.webContents.send('igorek:owner-panel', plugin ? {panel,plugin} : panel); };
  if (ownerWindow && !ownerWindow.isDestroyed() && ownerWindow.parent === parent) { ownerWindow.focus(); selectPanel(ownerWindow); return; }
  ownerWindow?.close();
  const target = new SettingsView({ parent, WebContentsView, preload: join(__dirname, 'owner-preload.cjs'), url: ownerUrl });
  ownerWindow = target;
  chromeWindows.set(target.webContents, { target: parent, urlCheck: url => url === ownerUrl });
  installContextMenu(target, parent);
  refreshEmbeddedSurfaces();
  target.on('closed', () => {
    chromeWindows.delete(target.webContents);
    if (ownerWindow === target) ownerWindow = null;
    runtime.requestOwner('private_mcp_clear').catch(() => {});
    refreshEmbeddedSurfaces();
  });
  target.loadURL(ownerUrl).then(() => selectPanel(target)).catch(() => target.close());
}

function refreshChatProfiles() {
  for (const target of chatWindows) {
    if (!target.isDestroyed() && sameOrigin(target.webContents.getURL(), config.origin)) {
      // Send a read-only refresh signal, never vault data or a host API.
      target.webContents.executeJavaScript("window.dispatchEvent(new Event('igorek:profiles-changed'));").catch(() => {});
    }
  }
}
ipcMain.handle('igorek:owner-return-to-chat', event => {
  if (!managed || !isOwnerFrame(event, ownerWindow?.webContents, ownerUrl))
    throw new Error('Доступ запрещён.');
  refreshChatProfiles();
  const parent = ownerWindow.parent;
  ownerWindow.close();
  parent.focus();
  return true;
});
ipcMain.handle('igorek:owner-open-opencode', ownerOpenCodeHandler({
  owner: () => ownerWindow, main: () => ownerWindow?.parent || window, ownerUrl, origin: () => config?.origin, sameOrigin,
  available: () => Boolean(managed && runtime && opencodeService && opencodeWorkspace),
  requestStatus: () => runtime.requestOwner('status'),
}));

ipcMain.handle('igorek:browser-control-settings', async (event,value) => {
  if(!isOwnerFrame(event,ownerWindow?.webContents,ownerUrl)||!workspaceBrowser)throw Error('Доступ запрещён.');
  if(value!==undefined){const reply=await runtime.requestOwner('status');if(!isOwnerFrame(event,ownerWindow?.webContents,ownerUrl)||reply.result?.state!=='unlocked'||reply.result?.interface_locked)throw Error('Разблокируй настройки Игорька.');}
  return workspaceBrowser.controlSettings(value);
});
ipcMain.handle('igorek:release-updates', async event => {
  if (!sessionMcp.trusted(event) || !showReleaseUpdates) throw Error('Доступ запрещён.');
  const parent = BrowserWindow.fromWebContents(event.sender);
  return showReleaseUpdates(parent);
});
ipcMain.handle('igorek:release-info', event => {
  if(!isOwnerFrame(event,ownerWindow?.webContents,ownerUrl)&&!sessionMcp.trusted(event))throw new Error('Доступ запрещён.');
  return desktopReleaseInfo();
});
ipcMain.handle('igorek:owner-locations', event => {
  if (!managed || !runtime || !isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw new Error('Доступ запрещён.');
  return { result: { locations: ownedDataLocations(runtime.dataDir, app.getPath('userData')) } };
});

const { SessionMcp } = require('./mcp-task.cjs');
const sessionMcp = new SessionMcp({
  request: (action, fields) => runtime.requestOwner(action, fields),
  trusted: event => managed && runtime && chatWindows.has(BrowserWindow.fromWebContents(event.sender))
    && event.senderFrame === event.sender.mainFrame && sameOrigin(event.senderFrame.url, config.origin),
  settings: () => openOwnerWindow(),
});
async function openDesktopBrowser({ toggle = true } = {}) {
  if (!managed || !runtime || !workspaceBrowser) return false;
  try {
    const reply = await runtime.requestOwner('status');
    if (reply.result?.state !== 'unlocked' || reply.result?.interface_locked) { openOwnerWindow(); return false; }
    const focused=BrowserWindow.getFocusedWindow();
    const parent=focused&&chatWindows.has(focused)?focused:window;
    if(!parent||parent.isDestroyed()||!chatWindows.has(parent)||!sameOrigin(parent.webContents.getURL(),config.origin))return false;
    workspaceBrowser.unlock();
    return await parent.webContents.executeJavaScript(toggle ? 'window.IgorekOpenBrowserPanel?.()' : 'window.IgorekShowBrowserPanel?.()')===true;
  } catch { dialog.showErrorBox('Браузер Игорька', 'Дождись завершения операции и разблокируй хранилище.'); return false; }
}
async function openChatGPT(parent){
  if(!managed||!runtime||!chatgptWorkspace)return {visible:false};
  try{
    const reply=await runtime.requestOwner('status');
    if(reply.result?.state!=='unlocked'||reply.result?.interface_locked){chatgptWorkspace.lock();openOwnerWindow();return {visible:false};}
    chatgptWorkspace.unlock();return chatgptWorkspace.show(parent);
  }catch{dialog.showErrorBox('ChatGPT','Не удалось открыть панель. Дождись завершения текущей операции и повтори.');return {visible:false};}
}
ipcMain.handle('igorek:session-chatgpt',(event,operation)=>{
  if(!sessionMcp.trusted(event)||!['toggle','hide','layout','back','forward','reload'].includes(operation))throw Error('Доступ запрещён.');
  const parent=BrowserWindow.fromWebContents(event.sender);
  if(operation==='layout'){if(chatgptWorkspace?.parent===parent)chatgptWorkspace.layout();return {visible:!!chatgptWorkspace?.attached};}
  if(['back','forward','reload'].includes(operation)){if(chatgptWorkspace?.parent!==parent)throw Error('Доступ запрещён.');return chatgptWorkspace.navigate(operation);}
  if(operation==='hide'||(operation==='toggle'&&chatgptWorkspace?.attached&&chatgptWorkspace.parent===parent))return chatgptWorkspace?.hide()||{visible:false};
  return openChatGPT(parent);
});
ipcMain.handle('igorek:github',async(event,operation,fields)=>{
  try{return await reviewRequest(githubReviews,{trusted:()=>sessionMcp.trusted(event),confirm:async pr=>{
    const reply=await dialog.showMessageBox(BrowserWindow.fromWebContents(event.sender),{type:'question',title:'Слить Pull Request',message:pr.title,detail:pr.url+'\nКоммит: '+pr.sha+'\nСпособ: '+pr.method+'\n\nСлияние изменит целевую ветку на GitHub.',buttons:['Отмена','Слить PR'],defaultId:0,cancelId:0});
    return reply.response===1;
  }},operation,fields);}catch(error){return {error:error.code?error.message:'Не удалось выполнить запрос GitHub.',code:error.code||'github_unavailable'};}
});
ipcMain.handle('igorek:host-service',async(event,operation,service='')=>{
  if(!sessionMcp.trusted(event)||!['list','start','stop','status'].includes(operation)||typeof service!=='string'||service.length>40)throw Error('Доступ запрещён.');
  const reply=await runtime.requestOwner('host_service',{operation,service});
  return reply.result||{error:reply.error||'host_service_unavailable'};
});
ipcMain.handle('igorek:opencode',async(event,operation)=>{
  if(!sessionMcp.trusted(event)||!['manual','status','show','hide','layout','install','project','disable','reload','sandbox','kvm','hostServices','zoomIn','zoomOut','zoomReset'].includes(operation))throw Error('Доступ запрещён.');
  if(!opencodeService)return {error:'OpenCode недоступен.'};
  const parent=BrowserWindow.fromWebContents(event.sender);
  if(operation==='manual'){opencodeWorkspace.useService(opencodeService);return opencodeWorkspace.status();}
  if(['install','project','disable','sandbox','kvm','hostServices'].includes(operation))opencodeWorkspace.useService(opencodeService);
  opencodeWorkspace.statusWindow=parent;
  if(operation==='status')return opencodeWorkspace.status();
  if(operation==='hide'){opencodeWorkspace.hide();return opencodeWorkspace.status();}
  if(operation==='layout'){await opencodeWorkspace.layout();return opencodeWorkspace.status();}
  const sequence=opencodeWorkspace.generation;
  const reply=await runtime.requestOwner('status');
  const authorized=()=>sessionMcp.trusted(event)&&sequence===opencodeWorkspace.generation;
  if(!authorized()||reply.result?.state!=='unlocked'||reply.result?.interface_locked)throw Error('Разблокируй интерфейс Игорька.');
  opencodeWorkspace.unlock();
  try{
    if(operation==='install')await opencodeService.install();
    else if(operation==='disable'){opencodeWorkspace.lock();await opencodeService.disable();}
    else if(operation==='project'||operation==='kvm'){
      if(opencodeService.state==='ready'){
        const answer=await dialog.showMessageBox(parent,{type:'question',title:'Настройка OpenCode',message:'Сервис будет перезапущен. Заверши текущий запрос OpenCode перед изменением подключения.',buttons:['Продолжить','Отмена'],defaultId:1,cancelId:1});
        if(answer.response!==0||!authorized())return opencodeWorkspace.status();
      }
      const selected=await dialog.showOpenDialog(parent,operation==='project'?{title:'Папка проекта для OpenCode',properties:['openDirectory']}:{title:'Подключение OpenCode в KVM',properties:['openFile'],filters:[{name:'Подключение KVM',extensions:['json']}]});
      if(selected.canceled||!authorized())return opencodeWorkspace.status();
      opencodeWorkspace.lock();
      if(operation==='project')await opencodeService.addProject(selected.filePaths[0]);else await opencodeService.configureKvm(selected.filePaths[0]);
      opencodeWorkspace.unlock();
    }else if(operation==='hostServices'){
      if(opencodeService.config.mode!=='sandbox')throw Error('Локальные сервисы сейчас подключаются в режиме sandbox.');
      const listed=await runtime.requestOwner('host_service',{operation:'list',service:''});
      const services=(listed.result?.services||[]).filter(row=>typeof row.name==='string'&&/^[a-z][a-z0-9_-]{0,39}$/.test(row.name)).slice(0,8);
      if(!services.length)throw Error('В Игорьке нет зарегистрированных локальных сервисов.');
      const answer=await dialog.showMessageBox(parent,{type:'question',title:'Локальные сервисы для OpenCode',message:'Выбери сервис, доступ к которому получит OpenCode.',detail:'Доступ только к утверждённым методам сервиса. Текущий запрос OpenCode нужно завершить: его сервис будет перезапущен. Диск, GPU и локальная сеть хоста остаются закрыты.',buttons:[...services.map(row=>row.label||row.name),'Отключить доступ','Отмена'],defaultId:services.length+1,cancelId:services.length+1});
      if(!authorized()||answer.response===services.length+1)return opencodeWorkspace.status();
      opencodeWorkspace.lock();await opencodeService.configureHostServices(answer.response<services.length?[services[answer.response].name]:[]);opencodeWorkspace.unlock();
    }else if(operation==='sandbox'){opencodeWorkspace.lock();await opencodeService.useSandbox();opencodeWorkspace.unlock();}
    else if(operation==='show'){await opencodeWorkspace.show(parent);}
    else if(operation==='reload'){opencodeWorkspace.navigate('reload');}
    else if(operation.startsWith('zoom')){const wc=opencodeWorkspace.view?.webContents;if(wc&&!wc.isDestroyed())wc.setZoomFactor(operation==='zoomReset'?1:Math.max(.5,Math.min(2,wc.getZoomFactor()+(operation==='zoomIn'?.1:-.1))));}
    return opencodeWorkspace.status();
  }catch(error){return {...opencodeWorkspace.status(),error:error.message};}
});
ipcMain.handle('igorek:workspace-browser' ,async(event,operation,fields)=>{
  if(!sessionMcp.trusted(event)||!['open','select','close','hide','layout','status','navigate','back','forward','reload','control','zoom','find','print','downloads','removeDownload','clearDownloads','openDownload','clear','stopVoice','resetMicrophone','webrtc','addFavorite','removeFavorite','console','console_messages','console_clear'].includes(operation))throw Error('Доступ запрещён.');
  if(!workspaceBrowser)return {tabs:[],history:[],downloads:[]};
  const parent=BrowserWindow.fromWebContents(event.sender);
  if(operation==='open'||operation==='select'||operation==='downloads'||operation==='webrtc'){
    const sequence=workspaceBrowser.sequence;
    const reply=await runtime.requestOwner('status');
    if(sequence!==workspaceBrowser.sequence||!sessionMcp.trusted(event))throw Error('Интерфейс был заблокирован. Повтори открытие после разблокировки.');
    if(reply.result?.state!=='unlocked'||reply.result?.interface_locked){workspaceBrowser.lock();throw Error('Разблокируй интерфейс Игорька.');}
    workspaceBrowser.unlock();
  }
  return workspaceBrowser.action(parent,operation,fields);
});
async function completeQuotaReply(event,profile,reply){
 const result=reply.result||{error:reply.error||'quota_login_failed'};
 if(result.state!=='confirm_account')return result;
 if(result.profile!==profile||typeof result.email!=='string'||result.email.length>256||typeof result.link_id!=='string'||!/^[a-f0-9]{32}$/.test(result.link_id))throw new Error('Некорректный ответ счётчика.');
 trustedChromeWindow(event);
 const answer=await dialog.showMessageBox(BrowserWindow.fromWebContents(event.sender),{type:'question',title:'Подключить счётчик Codex',
  message:'Показывать лимиты этого аккаунта Codex для профиля '+profile+'?',detail:'Аккаунт Codex: '+result.email+'\n\nРабочий вход Игорька сохранится.',buttons:['Подключить','Отмена'],defaultId:0,cancelId:1});
 trustedChromeWindow(event);
 if(answer.response!==0){await runtime.requestOwner('quota_cancel',{profile});return {error:'quota_cancelled'};}
 const confirmed=await runtime.requestOwner('quota_confirm',{profile,link_id:result.link_id});
 return confirmed.result||{error:confirmed.error||'quota_login_failed'};
}
ipcMain.handle('igorek:session-quota',async(event,action,profile)=>{
 if(!managed||!chatWindows.has(BrowserWindow.fromWebContents(event.sender))||event.senderFrame!==event.sender.mainFrame||!sameOrigin(event.senderFrame.url,config.origin)||!['quota_begin','quota_status','quota_cancel'].includes(action)||typeof profile!=='string'||!profile||profile.length>128)throw new Error('Доступ запрещён.');
 const reply=await runtime.requestOwner(action,{profile});
 return completeQuotaReply(event,profile,reply);
});
ipcMain.handle('igorek:session-browser-resize',(event,fields)=>{
  if(!sessionMcp.trusted(event))throw new Error('Доступ запрещён.');
  if(!fields||Object.keys(fields).sort().join(',')!=='phase,screen_x'||typeof fields.phase!=='string'||typeof fields.screen_x!=='number'||!Number.isFinite(fields.screen_x))return {error:'invalid_request'};
  return desktopBrowser?.resizeDock(fields.phase,fields.screen_x)||{error:'browser_closed'};
});
const sessionOverlays = new WeakSet();
ipcMain.handle('igorek:session-overlay', (event, active) => {
  if (!sessionMcp.trusted(event)) throw new Error('Доступ запрещён.');
  if (typeof active !== 'boolean') return { error: 'invalid_request' };
  const parent = BrowserWindow.fromWebContents(event.sender);
  if (active) sessionOverlays.add(parent); else sessionOverlays.delete(parent);
  if (desktopBrowser?.window === parent) desktopBrowser.layout();
  if (chatgptWorkspace?.parent === parent) chatgptWorkspace.layout();
  if (workspaceBrowser?.parent === parent) workspaceBrowser.action(parent,'layout').catch(()=>{});
  if (opencodeWorkspace?.parent === parent) opencodeWorkspace.layout().catch(()=>{});
  return { active };
});
ipcMain.handle('igorek:session-browser', async event => {
  if (!sessionMcp.trusted(event)) throw new Error('Доступ запрещён.');
  return {opened:await openDesktopBrowser()};
});
ipcMain.handle('igorek:browser', async (event, action, fields = {}) => {
  const trusted = () => managed && desktopBrowser?.window && !desktopBrowser.window.isDestroyed()
    && event.sender === desktopBrowser.controlContents() && event.senderFrame === event.sender.mainFrame
    && event.senderFrame.url === browserControlUrl;
  if (!trusted()) throw new Error('Доступ запрещён.');
  if (!['status', 'configure', 'login', 'resume', 'pause', 'hide', 'read_session','navigate','back','forward','reload','limits','menu','tab_new','tab_select','tab_close'].includes(action)
      || !fields || typeof fields !== 'object' || Array.isArray(fields)
      || (!['configure','navigate','limits','menu','tab_new','tab_select','tab_close'].includes(action) && Object.keys(fields).length)) return { error: 'invalid_request' };
  if (action === 'status') return desktopBrowser.status();
  if(action==='menu')return desktopBrowser.owner(action,fields);
  try {
    const owner = await runtime.requestOwner('status');
    if (!trusted() || owner.result?.state !== 'unlocked' || owner.result?.interface_locked) { desktopBrowser.lock(); return {error:'vault_locked'}; }
    desktopBrowser.unlock();
    if (action === 'read_session') {
      if (desktopBrowser.manual || desktopBrowser.paused) return {error:'browser_needs_owner'};
      const result = await runtime.requestOwner('browser_read_session');
      if (!trusted() || result.error) return { error: result.error || 'vault_locked' };
      const id = result.result.session_id;
      chatScript(window, 'selectSession(' + JSON.stringify(id) + ')');
      return desktopBrowser.status();
    }
    return await desktopBrowser.owner(action, fields);
  } catch { return {error:'operation_busy'}; }
});
ipcMain.handle('igorek:session-mcp', (event, action, fields) => sessionMcp.dispatch(event, action, fields));
ipcMain.handle('igorek:session-agent',async(event,fields)=>{
  if(!sessionMcp.trusted(event))throw new Error('Доступ запрещён.');
  if(installingUpdate)throw Error('Идёт подготовка обновления.');
  if(!fields||typeof fields!=='object'||Array.isArray(fields)||Object.keys(fields).length!==2||!['catalog','status','start'].includes(fields.operation)||typeof fields.profile!=='string'||fields.profile.length>64||(!fields.profile.trim()&&fields.operation!=='catalog'))return {error:'invalid_request'};
  if(runtime.requests.size)return {error:'operation_busy'};
  const reply=await runtime.requestOwner('session_agent',fields);
  if(!sessionMcp.trusted(event))return {error:'vault_locked'};
  return reply.error?{error:reply.error}:{agent:reply.result.agent,profiles:reply.result.profiles};
});
ipcMain.handle('igorek:ui-navigation', async (event, action, id) => {
  if (!sessionMcp.trusted(event)) throw new Error('Доступ запрещён.');
  if (action === 'catalog' && id === undefined) return { routes: navigationCatalog };
  const route = action === 'open' ? navigationRoute(id) : undefined;
  if (!route) return { error: 'invalid_request' };
  if (route.unavailable) return { error: 'section_unavailable' };
  if (route.kind === 'settings') openOwnerWindow(route.target);
  else if (route.kind === 'browser') return { opened: await openDesktopBrowser({ toggle: false }), route: route.id, kind: route.kind, target: route.target };
  else if (route.kind === 'window' && route.target === 'about') openAbout();
  else if (route.kind === 'window' && route.target === 'shortcuts') openShortcuts();
  return { opened: true, route: route.id, kind: route.kind, target: route.target };
});
ipcMain.handle('igorek:session-plugins', async (event, action, fields = {}) => {
  if (!sessionMcp.trusted(event)) throw new Error('Доступ запрещён.');
  const shapes = {status: [], action: ['plugin','operation'], calendar_connect: ['profile'], settings: Object.hasOwn(fields || {}, 'plugin') ? ['panel','plugin'] : ['panel']};
  if (!Object.hasOwn(shapes, action) || !fields || typeof fields !== 'object' || Array.isArray(fields)
      || Object.keys(fields).length !== shapes[action].length || shapes[action].some(key => typeof fields[key] !== 'string' || fields[key].length > 64)) return {error:'invalid_request'};
  if (action === 'settings') {
    if (!['plugins','mcp'].includes(fields.panel) || ('plugin' in fields && (fields.panel !== 'mcp' || !['openai_docs','google_calendar'].includes(fields.plugin)))) return {error:'invalid_request'};
    openOwnerWindow(fields.panel,fields.plugin); return {opened:true};
  }
  if (action === 'action' && (!['openai_docs','google_calendar'].includes(fields.plugin) || !['connect','check','disconnect'].includes(fields.operation))) return {error:'invalid_request'};
  if (runtime.requests.size) return {error:'operation_busy'};
  const disabling=action==='action'&&fields.plugin==='google_calendar'&&fields.operation==='disconnect';
  const reply = await runtime.requestOwner(action === 'calendar_connect' ? 'calendar_connect' : disabling ? 'calendar_disable' : action === 'status' ? 'plugins_status' : 'plugin_action', action === 'status'||disabling ? {} : fields);
  if (!sessionMcp.trusted(event)) return {error:'vault_locked'};
  if (action==='calendar_connect'&&reply.result?.authorization_url) {
    const auth=new URL(reply.result.authorization_url);
    if(auth.origin!=='https://accounts.google.com'||auth.pathname!=='/o/oauth2/v2/auth')return {error:'invalid_request'};
    await shell.openExternal(auth.href);return {opened:true};
  }
  return reply.error ? {error:reply.error} : reply.result;
});
app.on('web-contents-created', (_, contents) => contents.on('destroyed', () => sessionMcp.release(contents)));

ipcMain.handle('igorek:session-workspace', async (event) => {
  if (!managed || !chatWindows.has(BrowserWindow.fromWebContents(event.sender))
      || event.senderFrame !== event.sender.mainFrame || !sameOrigin(event.senderFrame.url, config.origin))
    throw new Error('Доступ запрещён.');
  const selected = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender), {
    title: 'Рабочая папка новой сессии', properties: ['openDirectory'],
  });
  if (selected.canceled) return { canceled: true };
  const path = selected.filePaths?.[0];
  if (typeof path !== 'string' || !(await stat(path)).isDirectory()) throw new Error('Папка недоступна.');
  await access(path, constants.R_OK | constants.W_OK | constants.X_OK);
  return { path };
});

ipcMain.handle('igorek:owner-workspace', async (event, action) => {
  if (!managed || !isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw new Error('Доступ запрещён.');
  if (!['choose', 'open-session'].includes(action)) return { error: 'invalid_request' };
  const status = await runtime.requestOwner('status');
  if (status.result?.interface_locked) return { error: 'interface_locked' };
    if (status.result?.state !== 'unlocked') return { error: 'vault_locked' };
  if (action === 'choose') {
    if (action === 'recover_image_file') {
      const confirm = await dialog.showMessageBox(ownerWindow.parent, {
        type: 'question', buttons: ['Отмена', 'Восстановить'], defaultId: 0, cancelId: 0,
        message: 'Восстановить базовый образ из проверенного файла?',
        detail: 'Текущий образ будет сохранён отдельно. История и рабочие файлы не изменятся.',
      });
      if (confirm.response !== 1) return { canceled: true };
    }
    const selected = await dialog.showOpenDialog(ownerWindow.parent, {
      title: 'Папка для работы Игорька', properties: ['openDirectory'],
    });
    if (selected.canceled) return { canceled: true };
    if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw new Error('Owner closed');
    const path = selected.filePaths?.[0];
    try {
      if (typeof path !== 'string' || !(await stat(path)).isDirectory()) return { error: 'file_failed' };
      await access(path, constants.R_OK | constants.W_OK | constants.X_OK);
    } catch { return { error: 'file_failed' }; }
    selectedWorkspace = path;
    return { result: { path } };
  }
  if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw new Error('Owner closed');
  const target = ownerWindow.parent;
  if (!selectedWorkspace) return { error: 'workspace_required' };
  if (!status.result?.agent?.running) return { error: 'agent_not_running' };
  if (!target || target.isDestroyed() || !sameOrigin(target.webContents.getURL(), config.origin))
    return { error: 'operation_failed' };
  const detail = JSON.stringify({ cwd: selectedWorkspace });
  await target.webContents.executeJavaScript(`window.dispatchEvent(new CustomEvent('igorek:workspace-selected', { detail: ${detail} }));`);
  if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw new Error('Owner closed');
  ownerWindow.close(); target.focus();
  return { result: { opened: true } };
});

ipcMain.handle('igorek:owner-vault', async (event, action, fields) => {
  if (!managed || !isOwnerFrame(event, ownerWindow?.webContents, ownerUrl))
    throw new Error('Доступ запрещён.');
  try {
  if(installingUpdate && !['status','image_status'].includes(action))throw Error('Идёт подготовка обновления.');
  if (['gmail_configure_file','calendar_configure_file'].includes(action)) {
    if (!fields || Object.keys(fields).length !== 1 || typeof fields.password !== 'string') return {error:'invalid_request'};
    const status = await runtime.requestOwner('status');
    if (status.result?.interface_locked || status.result?.state !== 'unlocked') return {error:'vault_locked'};
    const selected = await dialog.showOpenDialog(ownerWindow.parent, {title:'Выбрать Google OAuth client типа Desktop app', properties:['openFile'], filters:[{name:'Google OAuth client',extensions:['json']}]});
    if (selected.canceled) return {canceled:true};
    if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) return {error:'vault_locked'};
    return runtime.requestOwner(action === 'calendar_configure_file' ? 'calendar_configure' : 'gmail_configure', {source:selected.filePaths?.[0],password:fields.password});
  }
  if (action === 'choose_env_import') {
    if (fields !== undefined) return {error:'invalid_request'};
    const status = await runtime.requestOwner('status');
    if (status.result?.interface_locked || status.result?.state !== 'unlocked') return {error:'vault_locked'};
    const selected = await dialog.showOpenDialog(ownerWindow.parent, {title:'Выбрать файл переменных .env', properties:['openFile','showHiddenFiles'], filters:[{name:'Файлы переменных',extensions:['env','local','example','txt']},{name:'Все файлы',extensions:['*']}]});
    if (selected.canceled) return {canceled:true};
    if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) return {error:'vault_locked'};
    const reply = await runtime.requestOwner('prepare_env_import', {source:selected.filePaths?.[0]});
    if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) {
      if (reply.result?.env_import?.draft_id) await runtime.requestOwner('cancel_env_import',{draft_id:reply.result.env_import.draft_id}).catch(()=>{});
      return {error:'vault_locked'};
    }
    return reply;
  }
  if (action === 'choose_legacy_profile') {
    if (fields !== undefined) return { error: 'invalid_request' };
    const status = await runtime.requestOwner('status');
    if (status.result?.interface_locked) return { error: 'interface_locked' };
    if (status.result?.state !== 'unlocked') return { error: 'vault_locked' };
    const selected = await dialog.showOpenDialog(ownerWindow.parent, {
      title: 'Перенести параметры старого профиля', properties: ['openFile'],
      filters: [{ name: 'Профиль Игорька', extensions: ['json'] }],
    });
    if (selected.canceled) return { canceled: true };
    if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) return { error: 'vault_locked' };
    const reply = await runtime.requestOwner('read_legacy_profile', { source: selected.filePaths?.[0] });
    if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) return { error: 'vault_locked' };
    return reply;
  }
  if (['prepare_image_file', 'recover_image_file'].includes(action)) {
    if (fields !== undefined) return { error: 'invalid_request' };
    const status = await runtime.requestOwner('status');
    if (status.result?.interface_locked) return { error: 'interface_locked' };
    if (status.result?.state !== 'unlocked') return { error: 'vault_locked' };
    if (status.result?.agent?.running) return { error: 'stop_agent_first' };
    if (action === 'prepare_image_file') {
      const source = await installedImage();
      if (source) {
        if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw new Error('Owner closed');
        return runtime.requestOwner('prepare_image', { source });
      }
    }
    const selected = await dialog.showOpenDialog(ownerWindow.parent, {
      title: 'Выбрать базовый образ Игорька', properties: ['openFile'],
      filters: [{ name: 'Образ KVM', extensions: ['qcow2'] }],
    });
    if (selected.canceled) return { canceled: true };
    if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw new Error('Owner closed');
    return runtime.requestOwner(action === 'recover_image_file' ? 'recover_image' : 'prepare_image', { source: selected.filePaths?.[0] });
  }
  if (['save_recovery', 'verify_recovery_file', 'restore_recovery_file'].includes(action)) {
    const allowedFields = { save_recovery: ['password', 'recovery_phrase'],
      verify_recovery_file: ['recovery_phrase'], restore_recovery_file: ['recovery_phrase', 'new_password'] };
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)
        || Object.keys(fields).some(key => !allowedFields[action].includes(key))
        || typeof fields.recovery_phrase !== 'string' || !fields.recovery_phrase.trim() || fields.recovery_phrase.length > 1024)
      return { error: 'invalid_request' };
    if (action === 'restore_recovery_file' && (typeof fields.new_password !== 'string'
        || !fields.new_password.trim() || fields.new_password.length > 1024)) return { error: 'invalid_request' };
    try {
      const options = { title: action === 'save_recovery' ? 'Сохранить копию восстановления'
        : action === 'restore_recovery_file' ? 'Восстановить хранилище из копии' : 'Проверить копию восстановления',
        filters: [{ name: 'Копия Vault', extensions: ['json'] }] };
      const selected = action === 'save_recovery'
        ? await dialog.showSaveDialog(ownerWindow.parent, { ...options, defaultPath: join(app.getPath('documents'), 'igorek-vault-recovery.json') })
        : await dialog.showOpenDialog(ownerWindow.parent, { ...options, properties: ['openFile'] });
      if (selected.canceled) return { canceled: true };
      if (!isOwnerFrame(event, ownerWindow?.webContents, ownerUrl)) throw new Error('Owner closed');
      const path = selected.filePath || selected.filePaths?.[0];
      return action === 'save_recovery' ? await saveRecovery(runtime, path, fields)
        : action === 'restore_recovery_file' ? await restoreRecovery(runtime, path, fields)
        : await verifyRecovery(runtime, path, fields.recovery_phrase);
    } catch (error) { return { error: error.code === 'EEXIST' ? 'file_exists' : 'file_failed' }; }
  }
  // Raw backup payloads stay in the main process, never in the settings renderer.
  if (!['search_status','save_search','mobile_configure','mobile_start','mobile_status','mobile_stop','mobile_revoke','quota_begin','quota_status','quota_cancel','chatgpt_begin', 'chatgpt_status', 'chatgpt_cancel', 'chatgpt_models', 'chatgpt_disconnect', 'plugins_status', 'plugin_action', 'calendar_connect', 'calendar_disable', 'calendar_begin', 'calendar_disconnect', 'calendar_list', 'calendar_select', 'private_mcp_approve', 'gmail_status', 'gmail_import_host', 'gmail_begin', 'gmail_disconnect', 'private_mcp_start', 'private_mcp_status', 'private_mcp_clear', 'host_mcp_access', 'set_host_mcp_access', 'mcp_access', 'set_mcp_access', 'configure_interface_lock', 'set_interface_pin', 'remove_interface_pin', 'unlock_interface_pin', 'lock_interface', 'unlock_interface', 'status', 'readiness', 'image_status', 'execution_mode', 'select_execution_mode', 'change_password', 'create', 'unlock', 'lock', 'generate_phrase', 'model_catalog', 'delete_profile', 'rename_profile', 'list_profiles', 'commit_env_import', 'cancel_env_import', 'value_access', 'grant_value_access', 'revoke_value_access', 'list_values', 'save_value', 'delete_value', 'reveal_value', 'reveal_profile_key', 'save_profile', 'update_profile', 'check_profile', 'start_agent', 'stop_agent'].includes(action)) throw new Error('Доступ запрещён.');
  if(quitting||installingUpdate)return {error:'desktop_updating'};
  const reply = await runtime.requestOwner(action, fields);
  if(action==='quota_begin'&&reply.result?.authorization_url){const auth=new URL(reply.result.authorization_url);if(auth.origin!=='https://auth.openai.com'||auth.pathname!=='/oauth/authorize')return {error:'invalid_request'};await shell.openExternal(auth.href);return {result:{state:'waiting',profile:fields.profile}};}
  if (['chatgpt_begin', 'chatgpt_status', 'chatgpt_models', 'reveal_profile_key', 'reveal_value', 'private_mcp_status', 'private_mcp_start', 'gmail_begin', 'calendar_begin', 'calendar_connect'].includes(action) && !isOwnerFrame(event, ownerWindow?.webContents, ownerUrl))
    return { error: 'vault_locked' };
  if (action === 'chatgpt_begin' && reply.result?.authorization_url) {
    const auth = new URL(reply.result.authorization_url);
    if (auth.origin !== 'https://auth.openai.com' || auth.pathname !== '/api/accounts/authorize' || auth.searchParams.has('id_token_hint')) return {error:'invalid_request'};
    await shell.openExternal(auth.href);
    return {result:{opened:true}};
  }
  if (['gmail_begin','calendar_begin','calendar_connect'].includes(action) && reply.result?.authorization_url) {
    const auth = new URL(reply.result.authorization_url);
    if (auth.origin !== 'https://accounts.google.com' || auth.pathname !== '/o/oauth2/v2/auth') return {error:'invalid_request'};
    await shell.openExternal(auth.href);
    return {result:{opened:true}};
  }
  if (shouldRefreshProfiles(action, reply)) refreshChatProfiles();
  return reply;
  } catch (error) {
    if (error.code === 'operation_busy') return { error: 'operation_busy' };
    throw error;
  }
});

async function openWindow() {
  const chatWindow = new BrowserWindow({ icon:APP_ICON,
    title: 'Игорёк', width: 1280, height: 900, minWidth: 720, minHeight: 540,
    backgroundColor: '#111315', show: false, frame: !managed, autoHideMenuBar: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true,
      ...(managed ? { preload: join(__dirname, 'session-preload.cjs'), autoplayPolicy: 'no-user-gesture-required' } : {}),
      webSecurity: true, partition },
  });
  window = window && !window.isDestroyed() ? window : chatWindow;
  chatWindows.add(chatWindow);
  chatWindow.on('closed', () => { chatWindows.delete(chatWindow); if (window === chatWindow) window = [...chatWindows][0]; });
  if (managed) installWindowChrome(chatWindow, url => sameOrigin(url, config.origin));
  const contents = chatWindow.webContents;
  installContextMenu(chatWindow);
  trustedContents.add(contents);
  contents.once("destroyed", () => trustedContents.delete(contents));
  const session = contents.session;
  session.setCertificateVerifyProc((request, callback) => {
    if (request.hostname === new URL(config.origin).hostname) {
      callback(certificateMatches(request.certificate.data, config.fingerprint) ? 0 : -2);
    } else { callback(-3); }
  });
  session.setPermissionCheckHandler((sender, permission, origin, details) =>
    chatWindows.has(BrowserWindow.fromWebContents(sender)) && sameOrigin(origin, config.origin)
    && (allowClipboardWrite(permission) || (permission === 'media' && details.mediaType === 'audio')));
  session.setPermissionRequestHandler(async (sender, permission, callback, details) => {
    if (chatWindows.has(BrowserWindow.fromWebContents(sender)) && details.isMainFrame
        && sameOrigin(details.requestingUrl, config.origin) && allowClipboardWrite(permission)) {
      callback(true); return;
    }
    if (!chatWindows.has(BrowserWindow.fromWebContents(sender)) || !details.isMainFrame
        || !sameOrigin(details.requestingUrl, config.origin) || !allowAudio(permission, details)) {
      callback(false); return;
    }
    const result = await dialog.showMessageBox(BrowserWindow.fromWebContents(sender), {
      type: 'question', title: 'Микрофон', message: 'Разрешить Игорьку использовать микрофон для голосового разговора?',
      buttons: ['Разрешить', 'Отмена'], defaultId: 1, cancelId: 1,
    });
    callback(result.response === 0);
  });
  app.on('certificate-error', (event, sender, url, error, certificate, callback) => {
    event.preventDefault();
    callback(trustedContents.has(sender) && sameOrigin(url, config.origin)
      && certificateMatches(certificate.data, config.fingerprint));
  });
  contents.on('will-navigate', (event, url) => {
    if (!sameOrigin(url, config.origin)) event.preventDefault();
  });
  contents.on('will-redirect', (event, url) => {
    if (!sameOrigin(url, config.origin)) event.preventDefault();
  });
  contents.on('will-attach-webview', event => event.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    if (sameOrigin(url, config.origin)) {
      const child = new BrowserWindow({ icon:APP_ICON, parent: chatWindow, width: 1000, height: 760,
        webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true,
          webSecurity: true, partition } });
      trustedContents.add(child.webContents);
      const childContents = child.webContents;
      childContents.once("destroyed", () => trustedContents.delete(childContents));
      childContents.on("will-redirect", (event, target) => {
        if (!sameOrigin(target, config.origin)) event.preventDefault();
      });
      // Artifact windows have no access to host APIs, popups or permissions.
      child.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
      child.webContents.on('will-navigate', (event, target) => {
        if (!sameOrigin(target, config.origin)) event.preventDefault();
      });
      child.loadURL(url).catch(() => child.close());
    } else {
      try {
        const target = new URL(url);
        if (['https:', 'http:'].includes(target.protocol) && !target.username && !target.password) {
          shell.openExternal(url).catch(() => {});
        }
      } catch { /* Invalid links are ignored. */ }
    }
    return { action: 'deny' };
  });
  chatWindow.once('ready-to-show', () => chatWindow.show());
  try {
    if (managed && config.pairingCode) {
      const response = await session.fetch(config.origin + '/api/pair', {
        method: 'POST', headers: { 'Content-Type': 'application/json', Origin: config.origin },
        body: JSON.stringify({ code: config.pairingCode }), credentials: 'include',
      });
      config.pairingCode = null;
      if (!response.ok) throw new Error('Pairing failed');
    }
    await chatWindow.loadURL(config.origin);
    if(managed)await whatsNew?.show(chatWindow,{automatic:true}).catch(()=>{});
  }
  catch {
    dialog.showErrorBox('Не удалось открыть Игорька',
      'Проверьте, что локальный Web UI запущен и сертификат соответствует этому сервису.\n'
      + (managed ? 'Проверьте комплектный backend приложения.' : 'Этот режим подключается к существующему сервису.'));
    app.quit();
  }
}

app.whenReady().then(async () => {
  try {
    whatsNew=new WhatsNew({BrowserWindow,ipcMain,root:__dirname,file:join(app.getPath('userData'),'release-notes-settings.json'),info:desktopReleaseInfo,identity:()=>installationIdentity({root:__dirname,executable:process.execPath,appImage:process.env.APPIMAGE||'',version:desktopReleaseInfo().version})});
    showReleaseUpdates = updateDialog({app,dialog,shell,presentationFactory:parent=>createUpdatePresentation(parent,{BrowserWindow,ipcMain}),current:()=>desktopReleaseInfo().version,channel:()=>desktopReleaseInfo().channel,portable:app.isPackaged && process.platform==='linux' && Boolean(process.env.APPIMAGE),
      install:async file=>{
        if(!managed||!runtime)throw Error('unknown_update_installation');
        const {python}=loadBundle(runtime.bundle);
        installingUpdate=true;
        try{return await launchInstallation(file,{app,python,dataDir:runtime.dataDir,prepare:async()=>{
          const status=await runtime.requestOwner('status');
          if(status.error||status.result?.state!=='unlocked'||status.result?.interface_locked)throw Error('update_locked');
          const image=await runtime.requestOwner('image_status');
          if(image.error||image.result?.image?.running)throw Error('update_tasks_active');
          if(['ready','running','starting','stopping','installing'].includes(opencodeService?.status().state))throw Error('update_opencode_active');
          const stopped=await runtime.requestOwner('stop_agent');
          if(stopped.error||stopped.result?.agent?.running)throw Error('update_tasks_active');
        }});}catch(error){installingUpdate=false;throw error;}
      }});
    if (managed) {
      if (!app.requestSingleInstanceLock()) { app.quit(); return; }
      const options = { bundle: join(__dirname, 'runtime'), dataDir: join(app.getPath('appData'), 'igorek-desktop-runtime') };
      const argv = process.argv.slice(app.isPackaged?1:2);
      for (let i = 0; i < argv.length; i++) {
        if (argv[i] === '--managed') continue;
        if (['--runtime', '--data-dir'].includes(argv[i]) && argv[i + 1] && !argv[i + 1].startsWith('--')) {
          options[argv[i] === '--runtime' ? 'bundle' : 'dataDir'] = resolve(argv[++i]);
        } else { throw new Error('Некорректные параметры управляемого запуска.'); }
      }
      const home = app.getPath('home');
      const production = [join(home, '.igorek-agent-factory'), process.env.FACTORY_DATA_DIR, process.env.IGOREK_FACTORY_HOME].filter(Boolean).map(p => resolve(p));
      if (production.includes(resolve(options.dataDir))) throw new Error('Выберите отдельный каталог для прототипа.');
      runtime = new OwnedRuntime(options);
      desktopBrowser = new DesktopBrowser({ root: join(app.getPath('userData'), 'browser-profiles', 'agent'), parent:() => window,
        overlayActive: settingsOverlayActive,
        BrowserWindow, WebContentsView, session: electronSession,
        notify: reason => { if (Notification.isSupported()) new Notification({title:'Браузер Игорька', body:reason === 'challenge' ? 'Сайт просит пройти проверку. Чтение приостановлено.' : 'Сайту нужен вход. Открой браузер Игорька и войди вручную.'}).show(); } });
      desktopBrowser.lock();
      chatgptWorkspace=new ChatGPTWorkspace({root:join(app.getPath('userData'),'browser-profiles','chatgpt'),BrowserWindow,WebContentsView,session:electronSession,dialog,shell,menu:Menu,overlayActive:settingsOverlayActive,sidebarShortcut:(input,parent)=>{if(!matchesShortcut(input,shortcutStore.keys('sidebar')))return false;parent.webContents.executeJavaScript("document.getElementById('igorek-toggle-sidebar')?.click()").catch(()=>{});return true;}});
      workspaceBrowser=new WorkspaceBrowser({root:join(app.getPath('userData'),'browser-profiles','workspace'),BrowserWindow,WebContentsView,session:electronSession,dialog,shell,menu:Menu,overlayActive:settingsOverlayActive,sidebarShortcut:(input,parent)=>{if(!matchesShortcut(input,shortcutStore.keys('sidebar')))return false;parent.webContents.executeJavaScript("document.getElementById('igorek-toggle-sidebar')?.click()").catch(()=>{});return true;}});
      try{
        opencodeService=new OpenCodeService(join(app.getPath('userData'),'integrations','opencode'),{python:loadBundle(options.bundle).python,bundle:options.bundle,profilesControl:async(operation,value)=>{
          const reply=await runtime.requestOwner('opencode_profiles',{operation,config:JSON.stringify(value)});
          if(reply.error)throw Error('Профили Игорька недоступны. Разблокируй хранилище.');
          if(operation==='grant'&&reply.result?.socket!==join(runtime.dataDir,'opencode-profiles','api.sock'))throw Error('Недопустимый канал профилей.');
          if(operation==='grant'&&process.platform==='win32'&&(!Number.isInteger(reply.result?.port)||reply.result.port<1||reply.result.port>65535))throw Error('Недопустимый канал профилей.');
          return reply.result;
        },hostServicesControl:async(operation,value)=>{
          const reply=await runtime.requestOwner('opencode_services',{operation,config:JSON.stringify(value)});
          if(reply.error)throw Error('Доступ к локальным сервисам не разрешён.');
          if(operation==='grant'&&reply.result?.socket!==join(runtime.dataDir,'opencode-services','api.sock'))throw Error('Недопустимый канал локальных сервисов.');
          return reply.result;
        }});
        opencodeWorkspace=new OpenCodeWorkspace({root:join(app.getPath('userData'),'browser-profiles','opencode'),service:opencodeService,BrowserWindow,WebContentsView,session:electronSession,dialog,shell,overlayActive:settingsOverlayActive});
        opencodeTasks=new OpenCodeTasks({manual:opencodeService,root:join(app.getPath('userData'),'integrations','opencode-tasks'),
          allowed:()=>opencodeUnlocked,
          createService:(root,assignedProject,consumer)=>new OpenCodeService(root,{python:opencodeService.python,bundle:opencodeService.bundle,binaryRoot:opencodeService.binaryRoot,assignedProject,
            profilesControl:(operation,value)=>opencodeService.profilesControl(operation,operation==='grant'?{consumer}:value)}),
          show:async job=>{
            const route=sessionRoute(job);
            for(const parent of chatWindows){
              if(parent.isDestroyed())continue;
              const selected=await parent.webContents.executeJavaScript('window.IgorekOpenCodeAssignment?.('+JSON.stringify(job.session)+',true)').catch(()=>false);
              if(selected){
                opencodeWorkspace.useService(job.service,route);
                const opened=await parent.webContents.executeJavaScript('window.IgorekOpenCodeAssignment?.('+JSON.stringify(job.session)+')').catch(()=>false);
                if(opened)await opencodeWorkspace.show(parent);break;
              }
            }
          }});
        runtime.opencodeHandler=fields=>opencodeTasks.handle(fields);
      }catch{opencodeService=null;opencodeWorkspace=null;}
      githubReviews=new GitHubReviews({root:join(app.getPath('userData'),'github-reviews'),authorized:async()=>{const reply=await runtime.requestOwner('status');return reply.result?.state==='unlocked'&&!reply.result?.interface_locked;}});
      runtime.browserHandler = async fields => {
        if(fields?.action==='preview'&&Object.keys(fields).length===1){
          const modern=workspaceBrowser.active||(!desktopBrowser.attached&&workspaceBrowser.tabs.size);
          const result=await (modern?workspaceBrowser.preview():desktopBrowser.preview());
          return result.reason==='desktop_locked'?result:{...result,...workspaceBrowser.mobileState()};
        }
        if(fields?.action==='select_preview_tab'&&Object.keys(fields).length===2&&typeof fields.tab_id==='string'&&/^[-a-zA-Z0-9]{1,64}$/.test(fields.tab_id)){
          if(workspaceBrowser.blocked)return {selected:false,reason:'desktop_locked'};
          if(!workspaceBrowser.tabs.has(fields.tab_id))return {selected:false,reason:'browser_closed'};
          const parent=workspaceBrowser.parent,sequence=workspaceBrowser.sequence;
          if(!parent||parent.isDestroyed()||!chatWindows.has(parent)||!sameOrigin(parent.webContents.getURL(),config.origin))return {selected:false,reason:'browser_hidden'};
          const selected=await parent.webContents.executeJavaScript('window.IgorekSelectBrowserTab?.('+JSON.stringify(fields.tab_id)+')');
          if(workspaceBrowser.blocked)return {selected:false,reason:'desktop_locked'};
          if(sequence!==workspaceBrowser.sequence||parent!==workspaceBrowser.parent||parent.isDestroyed()||!sameOrigin(parent.webContents.getURL(),config.origin))return {selected:false,reason:'browser_changed'};
          return selected===true&&workspaceBrowser.active===fields.tab_id?{selected:true,tab_id:fields.tab_id}:{selected:false,reason:'browser_changed'};
        }
        if(desktopBrowser.attached&&!workspaceBrowser.tabs.size)return desktopBrowser.agent(fields);
        const parent=workspaceBrowser.parent||window;
        if(!parent||parent.isDestroyed()||!chatWindows.has(parent)||!sameOrigin(parent.webContents.getURL(),config.origin))return {error:'desktop_locked'};
        return workspaceBrowser.agent(parent,fields);
      };
      runtime.on('owner-state', state => { opencodeUnlocked=state.state==='unlocked'&&!state.interface_locked; if (!opencodeUnlocked) { desktopBrowser.lock();chatgptWorkspace.lock();workspaceBrowser.lock();opencodeWorkspace?.lock();opencodeService?.stop().catch(()=>{});opencodeTasks?.stop().catch(()=>{});githubReviews?.lock();for(const target of chatWindows)if(!target.isDestroyed())target.webContents.send('igorek:github-locked'); } else {desktopBrowser.unlock();workspaceBrowser.unlock();opencodeWorkspace?.unlock();} });
      runtime.on('failed', () => {
        if (config && !quitting) { dialog.showErrorBox('Игорёк', 'Локальный backend остановился. Закройте и заново откройте приложение.'); app.quit(); }
      });
      config = await runtime.start();
    } else { config = settings(process.argv.slice(2)); }
  }
  catch (error) { dialog.showErrorBox('Настройка Игорька', error.message); app.quit(); return; }
  nativeTheme.themeSource = 'dark';
  updateApplicationMenu();
  await openWindow();
  if (resourceSettings.enabled) openResourceMonitor();
  await showInstallationResult({app,dialog,parent:window});
  if (managed && !quitting) {
    const reply = await runtime.requestOwner('status').catch(() => null);
    if (reply?.result?.state !== 'unlocked') openOwnerWindow();
  }
});
app.on('window-all-closed', () => app.quit());

app.on('before-quit', event => {
  if (runtime && !quitting) {
    event.preventDefault(); quitting = true;
    desktopBrowser?.lock();
    chatgptWorkspace?.lock();
    workspaceBrowser?.lock();
    opencodeWorkspace?.lock();
    githubReviews?.lock();
    Promise.allSettled([desktopBrowser?.shutdown(),opencodeService?.stop(),opencodeTasks?.stop()]).finally(() => runtime.stop().finally(() => app.quit()));
  }
});
