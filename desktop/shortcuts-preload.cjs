const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('IgorekShortcuts', Object.freeze({
  list: () => ipcRenderer.invoke('igorek:shortcuts', 'list'),
  set: async (id, keys) => { const reply = await ipcRenderer.invoke('igorek:shortcuts', 'set', id, keys); if (reply.error) throw Error(reply.error); return reply; },
  reset: async () => { const reply = await ipcRenderer.invoke('igorek:shortcuts', 'reset'); if (reply.error) throw Error(reply.error); return reply; },
}));
contextBridge.exposeInMainWorld('IgorekWindow', Object.freeze({
  menu: (label, x, y) => ipcRenderer.invoke('igorek:window-menu', label, x, y),
  control: action => ipcRenderer.invoke('igorek:window-control', action),
}));
