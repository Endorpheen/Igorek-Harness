const fs=require('node:fs');
const path=require('node:path');
const {randomUUID,createHash}=require('node:crypto');
const {pathToFileURL}=require('node:url');

class WhatsNewStore{
  constructor(file){
    this.file=file;this.value={enabled:true,lastInstallation:''};
    try{const info=fs.lstatSync(file);if(!info.isFile()||info.isSymbolicLink()||info.size>65536)throw Error('invalid_settings');const saved=JSON.parse(fs.readFileSync(file,'utf8'));this.value={enabled:saved.enabled!==false,lastInstallation:typeof saved.lastInstallation==='string'?saved.lastInstallation.slice(0,128):''};}
    catch(error){if(error.code!=='ENOENT')this.invalid=true;}
  }
  snapshot(){return {...this.value};}
  save(value){
    if(this.invalid)throw Error('Настройка «Что нового» недоступна');
    fs.mkdirSync(path.dirname(this.file),{recursive:true,mode:0o700});
    const temporary=this.file+'.'+randomUUID()+'.tmp';
    try{fs.writeFileSync(temporary,JSON.stringify(value)+'\n',{flag:'wx',mode:0o600});fs.renameSync(temporary,this.file);this.value=value;}
    finally{try{fs.unlinkSync(temporary);}catch(error){if(error.code!=='ENOENT')throw error;}}
    return this.snapshot();
  }
  setEnabled(enabled){if(typeof enabled!=='boolean')throw Error('Некорректная настройка');return this.save({...this.value,enabled});}
  seen(identity){return this.save({...this.value,lastInstallation:identity});}
  shouldShow(identity){return !this.invalid&&this.value.enabled&&this.value.lastInstallation!==identity;}
}

function installationIdentity({root,executable,version,appImage=''}){
  let generation='';let parent=path.resolve(root);
  for(let depth=0;depth<6;depth++){
    try{if(fs.statSync(path.join(parent,'installation.json')).isFile()){generation=fs.readlinkSync(path.join(parent,'active'));break;}}catch{}
    const next=path.dirname(parent);if(next===parent)break;parent=next;
  }
  let stamp='';
  try{const info=fs.statSync(appImage||executable);stamp=[info.ino,info.ctimeMs,info.size].join(':');}catch{}
  return createHash('sha256').update(JSON.stringify([version,generation||stamp])).digest('hex');
}

class WhatsNew{
  constructor({BrowserWindow,ipcMain,root,file,info,identity}){
    Object.assign(this,{BrowserWindow,ipcMain,root,info,identity});this.store=new WhatsNewStore(file);this.window=null;
    this.url=pathToFileURL(path.join(root,'whats-new.html')).href;
    ipcMain.handle('igorek:whats-new',(event,action,value)=>{
      const contents=this.window?.webContents;
      if(!contents||contents.isDestroyed()||event.sender!==contents||event.senderFrame!==contents.mainFrame||event.senderFrame.url!==this.url)throw Error('Доступ запрещён');
      if(action==='read'){const info=this.info();return {version:info.version,changes:info.changes,enabled:this.store.snapshot().enabled};}
      if(action==='set')return {enabled:this.store.setEnabled(value).enabled};
      throw Error('Некорректное действие');
    });
  }
  async show(parent,{automatic=false}={}){
    const info=this.info(),identity=this.identity();
    if(automatic&&(!this.store.shouldShow(identity)||!info.changes?.length))return null;
    if(this.window&&!this.window.isDestroyed()){this.window.focus();return this.window;}
    const target=new this.BrowserWindow({parent:parent&&!parent.isDestroyed()?parent:undefined,modal:Boolean(parent&&!parent.isDestroyed()),width:490,height:380+Math.min(4,info.changes?.length||0)*48,resizable:false,minimizable:false,maximizable:false,show:false,title:'Что нового в Игорьке',backgroundColor:'#1b211e',autoHideMenuBar:true,webPreferences:{preload:path.join(this.root,'whats-new-preload.cjs'),sandbox:true,contextIsolation:true,nodeIntegration:false,webSecurity:true,partition:'igorek-whats-new'}});
    this.window=target;target.setMenuBarVisibility(false);target.on('closed',()=>{if(this.window===target)this.window=null;});
    target.webContents.session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
    target.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    target.webContents.on('will-navigate',event=>event.preventDefault());target.webContents.on('will-attach-webview',event=>event.preventDefault());
    try{await target.loadFile(path.join(this.root,'whats-new.html'));if(target.isDestroyed())return null;target.show();this.store.seen(identity);return target;}
    catch(error){target.close();throw error;}
  }
  dispose(){this.window?.close();this.ipcMain.removeHandler('igorek:whats-new');}
}
module.exports={WhatsNew,WhatsNewStore,installationIdentity};
