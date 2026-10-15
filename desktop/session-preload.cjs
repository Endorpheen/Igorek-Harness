const { contextBridge, ipcRenderer } = require('electron');
let completionSoundState = { enabled: false, background: false };
ipcRenderer.on('igorek:completion-sound-state', (_, value) => {
  if (typeof value?.enabled === 'boolean' && typeof value?.background === 'boolean') completionSoundState = value;
});
window.addEventListener('DOMContentLoaded', () => {
  ipcRenderer.invoke('igorek:completion-sound', 'read').then(value => { completionSoundState = value; }).catch(() => {});
  const panel=document.querySelector('.main-panel');
  if(panel)new ResizeObserver(()=>ipcRenderer.invoke('igorek:session-chatgpt','layout').catch(()=>{})).observe(panel);
  document.addEventListener('click',event=>{if(event.target.closest?.('#nav-home,#nav-scheduled,#nav-plugins,#new-session,.session-main,.project-add'))ipcRenderer.invoke('igorek:session-chatgpt','hide').catch(()=>{});},true);
  let previous;
  const update = () => {
    const active = Boolean(document.querySelector('dialog:modal,.workspace-browser-menu:popover-open,#igorek-notifications-panel:not([hidden])'));
    if (active === previous) return;
    previous = active;
    ipcRenderer.invoke('igorek:session-overlay', active).catch(() => {});
  };
  new MutationObserver(update).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'hidden'] });
  document.addEventListener('close', update, true);
  document.addEventListener('toggle', update, true);
  update();
});
const notificationSource = (channel, action) => ({mobile_start:'Сопряжение телефона',mobile_status:'Мобильные устройства',mobile_stop:'Мобильные устройства',mobile_revoke:'Отключение телефона',quota_begin:'Квота Codex',quota_status:'Квота Codex',quota_cancel:'Квота Codex'}[action] || ({'igorek:owner-vault':'Настройки','igorek:session-quota':'Квота Codex','igorek:session-agent':'Исполнитель','igorek:session-plugins':'Плагины','igorek:session-mcp':'Подключения MCP','igorek:session-browser':'Браузер'}[channel]) || 'Приложение');
const invoke = async (channel, ...args) => {
  try {
    const reply = await ipcRenderer.invoke(channel, ...args);
    if (reply?.error) ipcRenderer.invoke('igorek:notifications', 'add', { source: notificationSource(channel, args[0]), level: 'error', detail: String(reply.error) }).catch(() => {});
    return reply;
  } catch (error) {
    ipcRenderer.invoke('igorek:notifications', 'add', { source: notificationSource(channel, args[0]), level: 'error', detail: String(error.message || error) }).catch(() => {});
    throw error;
  }
};
contextBridge.exposeInMainWorld('IgorekNotifications', Object.freeze({
  list: () => ipcRenderer.invoke('igorek:notifications', 'list'),
  add: value => ipcRenderer.invoke('igorek:notifications', 'add', value),
  read: () => ipcRenderer.invoke('igorek:notifications', 'read'),
  clear: () => ipcRenderer.invoke('igorek:notifications', 'clear'),
  onChange: callback => { ipcRenderer.on('igorek:notifications-changed', (_, items) => callback(items)); },
}));
contextBridge.exposeInMainWorld('IgorekDesktop', Object.freeze({
  navigation: (action, id) => invoke('igorek:ui-navigation', action, id),
  opencode: operation => invoke('igorek:opencode',operation),
  onOpenCodeState: callback => {ipcRenderer.on('igorek:opencode-state',(_,value)=>callback(value));},
  hostService: (operation,service='') => ipcRenderer.invoke('igorek:host-service',operation,service),
  releaseInfo: () => ipcRenderer.invoke('igorek:release-info'),
  checkUpdates: () => invoke('igorek:release-updates'),
  completionSoundEnabled: () => completionSoundState.enabled,
  isBackground: () => completionSoundState.background,
  claimCompletionSound: taskId => ipcRenderer.invoke('igorek:completion-sound', 'claim', taskId),
  github: (operation,fields={}) => invoke('igorek:github',operation,fields),
  onGitHubLocked: callback => {ipcRenderer.on('igorek:github-locked',()=>callback());},
  workspaceBrowser: (operation,fields={}) => invoke('igorek:workspace-browser',operation,fields),
  onWorkspacePopup: callback => {ipcRenderer.on('igorek:workspace-popup',(_,url)=>callback(url));},
  onWorkspaceBrowserState: callback => {ipcRenderer.on('igorek:workspace-browser-state',(_,value)=>callback(value));},
  chatgpt: () => invoke('igorek:session-chatgpt','toggle'),
  chatgptNavigate: operation => invoke('igorek:session-chatgpt',operation),
  onChatGPTState: callback => {ipcRenderer.on('igorek:chatgpt-state',(_,value)=>callback(value));},
  quota: (action,profile) => invoke('igorek:session-quota',action,profile),
  browserResize: (phase,screen_x=0) => invoke('igorek:session-browser-resize',{phase,screen_x}),
  browser: () => invoke('igorek:session-browser'),
  chooseWorkspace: () => invoke('igorek:session-workspace'),
  mcp: (action, fields) => invoke('igorek:session-mcp', action, fields),
  plugins: (action, fields) => invoke('igorek:session-plugins', action, fields),
  agent: (operation,profile='') => invoke('igorek:session-agent', {operation,profile}),
}));

contextBridge.exposeInMainWorld('IgorekWindow', Object.freeze({
  shortcutKeys: () => invoke('igorek:shortcuts', 'labels'),
  onShortcutsChanged: callback => { ipcRenderer.on('igorek:shortcuts-changed', (_, keys) => callback(keys)); },
  menu: (label, x, y) => invoke('igorek:window-menu', label, x, y),
  control: action => invoke('igorek:window-control', action),
}));
