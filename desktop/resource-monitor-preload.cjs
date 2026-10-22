const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('IgorekResourceMonitor', Object.freeze({
  snapshot: () => ipcRenderer.invoke('igorek:resource-monitor', 'snapshot'),
  close: () => ipcRenderer.invoke('igorek:resource-monitor', 'close'),
}));
