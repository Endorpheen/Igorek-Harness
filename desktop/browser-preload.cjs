const { contextBridge, ipcRenderer } = require('electron');
ipcRenderer.on('igorek:browser-menu-close',()=>window.dispatchEvent(new Event('igorek:browser-menu-close')));
contextBridge.exposeInMainWorld('IgorekBrowser', Object.freeze({
  action: (action, fields = {}) => ipcRenderer.invoke('igorek:browser', action, fields),
}));
