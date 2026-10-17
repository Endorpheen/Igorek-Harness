const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {WhatsNewStore,installationIdentity,WhatsNew}=require('../whats-new.cjs');
test('release notes default on, appear once per installation and preserve opt-out across updates',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-news-test-'));try{
  const file=path.join(root,'settings.json');let store=new WhatsNewStore(file);assert.equal(store.shouldShow('install-a'),true);
  store.seen('install-a');store=new WhatsNewStore(file);assert.equal(store.shouldShow('install-a'),false);assert.equal(store.shouldShow('install-b'),true);
  store.setEnabled(false);store=new WhatsNewStore(file);assert.equal(store.shouldShow('install-c'),false);
  store.setEnabled(true);assert.equal(store.shouldShow('install-c'),true);assert.throws(()=>store.setEnabled('false'));
 }finally{fs.rmSync(root,{recursive:true,force:true});}
});
test('unsafe or corrupt preference files are preserved instead of overwritten',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-news-test-'));try{
  const file=path.join(root,'settings.json');fs.writeFileSync(file,'broken');const store=new WhatsNewStore(file);assert.equal(store.shouldShow('a'),false);assert.throws(()=>store.setEnabled(false));assert.equal(fs.readFileSync(file,'utf8'),'broken');
  if(process.platform!=='win32'){const linked=path.join(root,'linked.json');fs.symlinkSync(file,linked);assert.throws(()=>new WhatsNewStore(linked).setEnabled(false));assert.equal(fs.readFileSync(file,'utf8'),'broken');}
 }finally{fs.rmSync(root,{recursive:true,force:true});}
});
test('installation identity survives relaunch but changes for a new Linux generation of the same version',()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-install-key-'));try{
  const executable=path.join(root,'app.bin');fs.writeFileSync(executable,'fixture');const args={root,executable,version:'1.2.3'};const first=installationIdentity(args);assert.equal(installationIdentity(args),first);assert.notEqual(installationIdentity({...args,version:'1.2.4'}),first);
  if(process.platform!=='win32'){fs.writeFileSync(path.join(root,'installation.json'),'{}');fs.symlinkSync('generations/a',path.join(root,'active'));const a=installationIdentity(args);fs.unlinkSync(path.join(root,'active'));fs.symlinkSync('generations/b',path.join(root,'active'));assert.notEqual(installationIdentity(args),a);}
 }finally{fs.rmSync(root,{recursive:true,force:true});}
});
test('what is new IPC accepts only its own main frame and boolean changes',async()=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-news-ipc-')),handlers=new Map();const ipcMain={handle:(key,handler)=>handlers.set(key,handler),removeHandler:key=>handlers.delete(key)};
 class Window{
  constructor(options){this.options=options;this.events={};this.webContents={mainFrame:{url:''},isDestroyed:()=>false,session:{setPermissionRequestHandler(){}},setWindowOpenHandler(){},on(){}};}
  setMenuBarVisibility(){}on(name,fn){this.events[name]=fn;}isDestroyed(){return false;}focus(){}show(){this.shown=true;}close(){this.events.closed?.();}async loadFile(file){this.webContents.mainFrame.url=require('node:url').pathToFileURL(file).href;}
 }
 const notes=new WhatsNew({BrowserWindow:Window,ipcMain,root,file:path.join(root,'settings.json'),info:()=>({version:'1.2.3',changes:['Actual installed change']}),identity:()=> 'installation'});
 try{
  const window=await notes.show(null,{automatic:true});assert.ok(window.shown);assert.equal(window.options.webPreferences.nodeIntegration,false);assert.equal(window.options.webPreferences.sandbox,true);
  const handle=handlers.get('igorek:whats-new'),event={sender:window.webContents,senderFrame:window.webContents.mainFrame};
  assert.equal(handle(event,'read').version,'1.2.3');assert.throws(()=>handle({...event,senderFrame:{url:event.senderFrame.url}},'set',false),/Доступ/);
  assert.throws(()=>handle(event,'set','false'));assert.equal(handle(event,'set',false).enabled,false);window.close();assert.equal(await notes.show(null,{automatic:true}),null);
 }finally{notes.dispose();fs.rmSync(root,{recursive:true,force:true});}
});
