const {app,BrowserWindow,ipcMain}=require('electron');
app.disableHardwareAcceleration();
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const http=require('node:http'),vm=require('node:vm');
const {pathToFileURL}=require('node:url');
const {TextToSpeech}=require('../../tts.cjs');
const {isOwnerFrame}=require('../../owner-policy.cjs');
const desktop=path.resolve(__dirname,'../..');
const bundle=path.resolve(process.env.IGOREK_TEST_RUNTIME);
const output=path.resolve(process.env.IGOREK_TEST_OUTPUT);
const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-tts-native-'));
app.setPath('userData',path.join(fixture,'chromium'));
const report={status:'running',stages:[],network:'synthetic local TTS API and owner account'};
fs.mkdirSync(output,{recursive:true});
let child,window,chat,webChild,ttsServer; let speechRequests=0;
const pending=new Map();let sequence=0;
async function request(action,fields={}){
  const id=String(++sequence);
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(id);reject(Error('fixture timeout'));},10000);
    pending.set(id,value=>{clearTimeout(timer);resolve(value);});
    child.stdin.write(JSON.stringify({id,action,...fields})+'\n');
  });
}
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(script){
  for(let i=0;i<100;i++){if(await window.webContents.executeJavaScript(script))return;await pause(50);}
  throw Error('Search settings did not reach the expected state');
}
async function run(){
  await app.whenReady();
  fs.mkdirSync(path.join(fixture,'home'),{recursive:true});fs.mkdirSync(path.join(fixture,'tmp'),{recursive:true});
  const manifest=JSON.parse(fs.readFileSync(path.join(bundle,'manifest.json')));
  child=spawn(path.join(bundle,manifest.python),['-I',path.join(__dirname,'search-settings-fixture.py'),bundle,path.join(fixture,'data')],{
    env:{...(process.platform==='win32'?{SystemRoot:process.env.SystemRoot||process.env.SYSTEMROOT,SYSTEMROOT:process.env.SYSTEMROOT||process.env.SystemRoot,USERPROFILE:path.join(fixture,'home'),TEMP:path.join(fixture,'tmp')}:{ }),PYTHONUTF8:'1',PATH:process.env.PATH,HOME:path.join(fixture,'home'),FACTORY_DATA_DIR:path.join(fixture,'data'),IGOREK_FACTORY_HOME:path.join(fixture,'home'),FACTORY_ENV_FILES_DIR:path.join(fixture,'env')},stdio:['pipe','pipe','pipe']});
  child.stderr.on('data',()=>{});
  readline.createInterface({input:child.stdout}).on('line',line=>{try{const reply=JSON.parse(line);pending.get(reply.id)?.(reply);pending.delete(reply.id);}catch{}});
  const notifications=[];
  ipcMain.handle('igorek:owner-vault',(_,action,fields)=>request(action,fields));
  ipcMain.handle('igorek:notifications',(_,action,value)=>{if(action==='add')notifications.push(value);return [];});
  ipcMain.handle('igorek:completion-sound',()=>({enabled:false,background:false}));
  ipcMain.handle('igorek:release-info',()=>({version:manifest.factory_version,changes:[]}));
  ipcMain.handle('igorek:browser-control-settings',()=>({require_handoff:false}));
  window=new BrowserWindow({show:true,width:1280,height:1000,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,preload:path.join(desktop,'owner-preload.cjs')}});
  const bytes=Buffer.alloc(44+8820);bytes.write('RIFF');bytes.writeUInt32LE(bytes.length-8,4);bytes.write('WAVE',8);bytes.write('fmt ',12);bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(1,22);bytes.writeUInt32LE(44100,24);bytes.writeUInt32LE(88200,28);bytes.writeUInt16LE(2,32);bytes.writeUInt16LE(16,34);bytes.write('data',36);bytes.writeUInt32LE(8820,40);
  ttsServer=http.createServer((req,res)=>{if(req.url==='/v1/models'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({data:[{id:'fixture-tts'}]}));return;}let body='';req.on('data',part=>body+=part);req.on('end',()=>{const value=JSON.parse(body);assert.equal(value.model,'fixture-tts');assert.equal(value.voice,'default');assert.ok(value.input.length>0);speechRequests++;res.setHeader('Content-Type','audio/wav');res.end(bytes);});});
  await new Promise(resolve=>ttsServer.listen(0,'127.0.0.1',resolve));
  const textToSpeech=new TextToSpeech(path.join(fixture,'tts.json'));
  const source=fs.readFileSync(path.join(desktop,'main.cjs'),'utf8');
  const context={ipcMain,isOwnerFrame,ownerWindow:window,ownerUrl:pathToFileURL(path.join(desktop,'owner.html')).href,sessionMcp:{trusted:event=>Boolean(chat && event.sender===chat.webContents)},textToSpeech,chromeWindows:new Map([[window.webContents,{}]]),runtime:{requestOwner:()=>request('status')}};
  vm.runInNewContext(source.slice(source.indexOf("ipcMain.handle('igorek:tts'"),source.indexOf("ipcMain.handle('igorek:completion-sound'")),context);
  const pageErrors=[];window.webContents.on('console-message',details=>{if(details.level==='error')pageErrors.push(details.message);});
  await window.loadFile(path.join(desktop,'owner.html'));
  await until("typeof state!=='undefined' && state==='unlocked'");
  await window.webContents.executeJavaScript("openPanel('tts')");
  const url='http://127.0.0.1:'+ttsServer.address().port+'/v1';
  await window.webContents.executeJavaScript(`document.getElementById('tts-enabled').checked=true;document.getElementById('tts-url').value=${JSON.stringify(url)};document.getElementById('tts-model').value='fixture-tts';document.getElementById('tts-voice').value='default';document.getElementById('tts-form').requestSubmit();`,true);
  await until("document.getElementById('tts-status').textContent==='Настройки озвучивания сохранены.'");
  assert.equal(textToSpeech.state().model,'fixture-tts');report.stages.push('owner_settings_save_through_production_ipc');
  await window.webContents.executeJavaScript("document.getElementById('tts-models-load').click()",true);
  await until("document.querySelectorAll('#tts-model-list option').length===1");report.stages.push('model_catalog_http');
  await window.webContents.executeJavaScript("document.getElementById('tts-preview').click()",true);
  for(let i=0;i<100&&speechRequests===0;i++)await pause(50);
  assert.equal(speechRequests,1);
  await until("document.getElementById('tts-preview').textContent==='Проверить голос'");
  assert.equal(await window.webContents.executeJavaScript("document.getElementById('tts-status').textContent"),'');
  report.stages.push('preview_audio_playback_and_reset');
  fs.writeFileSync(path.join(output,'tts-settings.png'),(await window.webContents.capturePage()).toPNG());
  const layout=await window.webContents.executeJavaScript("(()=>{const node=document.getElementById('tts-settings'),form=document.getElementById('tts-form');return {visible:node.getBoundingClientRect().width>0,formWidth:form.getBoundingClientRect().width,viewport:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth}})()");
  assert.equal(layout.visible,true);assert.equal(layout.overflow,false);assert.ok(layout.formWidth>200);report.layout=layout;report.pageErrors=pageErrors;
  assert.deepEqual(pageErrors,[]);
  webChild=spawn(path.join(bundle,manifest.python),['-I',path.join(__dirname,'tts-web-fixture.py'),path.join(bundle,'packages')],{env:{...process.env,HOME:path.join(fixture,'home'),IGOREK_FACTORY_HOME:path.join(fixture,'chat-data')},stdio:['pipe','pipe','pipe']});
  webChild.stderr.on('data',()=>{});
  const origin=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('Chat fixture startup timeout')),10000);
    readline.createInterface({input:webChild.stdout}).on('line',line=>{try{const value=JSON.parse(line);if(value.origin){clearTimeout(timer);resolve(value.origin);}}catch{}});
  });
  ipcMain.handle('igorek:ui-navigation',()=>({routes:[]}));
  ipcMain.handle('igorek:shortcuts',()=>({}));
  ipcMain.handle('igorek:session-overlay',()=>({}));
  ipcMain.handle('igorek:session-chatgpt',()=>({}));
  chat=new BrowserWindow({show:true,width:1100,height:800,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,preload:path.join(desktop,'session-preload.cjs'),autoplayPolicy:'no-user-gesture-required'}});
  await chat.loadURL(origin);
  let ready=false;
  for(let i=0;i<100;i++){ready=await chat.webContents.executeJavaScript("typeof renderMessage==='function' && Boolean(window.IgorekMessageSpeech) && window.IgorekSpeechState?.enabled===true");if(ready)break;await pause(50);}
  assert.equal(ready,true,'The HTTP-served chat must load its actual TTS player');
  await chat.webContents.executeJavaScript("(()=>{for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();window.ttsChatErrors=[];const box=document.createElement('section');box.style='position:fixed;inset:20px;background:#171b1a;padding:24px;z-index:999999';box.id='tts-chat-fixture';box.append(renderMessage({id:'tts-answer',type:'assistant',text:'Привет из чата!',streaming:false}));box.querySelector('.message-speak').addEventListener('tts-error',event=>window.ttsChatErrors.push(event.detail));document.body.append(box);})()");
  const visible=await chat.webContents.executeJavaScript("(()=>{const b=document.querySelector('#tts-chat-fixture .message-speak');return Boolean(b&&!b.hidden&&b.getBoundingClientRect().width>0&&Number(getComputedStyle(b).opacity)>0.9)})()");
  assert.equal(visible,true,'A completed assistant message must expose its speech button');
  await chat.webContents.executeJavaScript("document.querySelector('#tts-chat-fixture .message-speak').click()",true);
  for(let i=0;i<100&&speechRequests<2;i++)await pause(50);
  assert.equal(speechRequests,2,'The actual chat button must reach the configured TTS HTTP service');
  for(let i=0;i<100;i++){if(await chat.webContents.executeJavaScript("document.querySelector('#tts-chat-fixture .message-speak').getAttribute('aria-pressed')==='false'"))break;await pause(50);}
  assert.equal(await chat.webContents.executeJavaScript("document.querySelector('#tts-chat-fixture .message-speak').getAttribute('aria-pressed')"),'false');
  assert.deepEqual(await chat.webContents.executeJavaScript('window.ttsChatErrors'),[],'WAV playback must complete without a CSP or audio error');
  fs.writeFileSync(path.join(output,'tts-chat.png'),(await chat.webContents.capturePage()).toPNG());
  report.stages.push('http_served_chat_player','visible_assistant_speech_button','chat_button_ipc_http_audio_playback');
  report.status='passed';
}
run().catch(error=>{report.status='failed';report.error=error.message;process.exitCode=1;}).finally(async()=>{
  fs.writeFileSync(path.join(output,'summary.json'),JSON.stringify(report,null,2));
  chat?.destroy();if(webChild){webChild.stdin.end();await new Promise(resolve=>{webChild.once('exit',resolve);setTimeout(()=>{webChild.kill();resolve();},3000).unref();});}window?.destroy();if(ttsServer){ttsServer.closeAllConnections();ttsServer.close();}if(child){child.stdin.end();await new Promise(resolve=>{child.once('exit',resolve);setTimeout(()=>{child.kill();resolve();},3000).unref();});}
  console.log(JSON.stringify(report));app.exit(report.status==='passed'?0:1);
});
