const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('IgorekUpdate',{choose:index=>{
  if(Number.isInteger(index)&&index>=0&&index<4)ipcRenderer.send('igorek:update-choice',index);
}});
