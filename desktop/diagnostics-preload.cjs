const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('IgorekDiagnostics', Object.freeze({ snapshot: () => ipcRenderer.invoke('igorek:diagnostics') }));
contextBridge.exposeInMainWorld('IgorekWindow', Object.freeze({
  menu: (label, x, y) => ipcRenderer.invoke('igorek:window-menu', label, x, y),
  control: action => ipcRenderer.invoke('igorek:window-control', action),
}));
