const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('IgorekWhatsNew',Object.freeze({read:()=>ipcRenderer.invoke('igorek:whats-new','read'),set:enabled=>ipcRenderer.invoke('igorek:whats-new','set',enabled)}));
