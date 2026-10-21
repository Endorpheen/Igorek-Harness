const {app,BrowserWindow,ipcMain}=require('electron');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const desktop=path.resolve(__dirname,'../..');
const bundle=path.resolve(process.env.IGOREK_TEST_RUNTIME);
const output=path.resolve(process.env.IGOREK_TEST_OUTPUT);
const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-search-native-'));
app.setPath('userData',path.join(fixture,'chromium'));
const report={status:'running',stages:[],network:'synthetic fixed-endpoint provider responses'};
fs.mkdirSync(output,{recursive:true});
let child,window;
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
  const pageErrors=[];window.webContents.on('console-message',details=>{if(details.level==='error')pageErrors.push(details.message);});
  await window.loadFile(path.join(desktop,'owner.html'));
  await until("typeof state!=='undefined' && state==='unlocked'");
  await window.webContents.executeJavaScript("openPanel('search')");
  await until("document.querySelectorAll('#search-provider option').length===4");
  report.stages.push('settings_catalog_three_providers');
  for(const provider of ['exa','serper','tavily']){
    await window.webContents.executeJavaScript(`document.getElementById('search-provider').value=${JSON.stringify(provider)};document.getElementById('search-provider').dispatchEvent(new Event('change'));document.getElementById('search-key').value='test-only-search-key';document.getElementById('search-password').value='test-only-password';document.getElementById('search-form').requestSubmit();`);
    await until("document.getElementById('search-status').textContent==='Подключение проверено и сохранено.'");
    const state=await request('fixture_inspect');
    assert.equal(state.result.selection,provider);assert.equal(state.result.search,true);assert.equal(state.result.result_ok,true);
    assert.equal(state.result.extract,provider!=='serper');
    assert.deepEqual(await window.webContents.executeJavaScript("[document.getElementById('search-key').value,document.getElementById('search-password').value]"),['','']);
    report.stages.push(provider+'_ui_ipc_vault_tool_dispatch');
  }
  await window.webContents.executeJavaScript("document.getElementById('search-password').value='wrong';document.getElementById('search-form').requestSubmit()");
  await until("!document.getElementById('search-save').disabled");
  assert.equal((await request('fixture_inspect')).result.selection,'tavily');
  assert.equal(notifications.at(-1).level,'error');
  assert.equal(await window.webContents.executeJavaScript("document.getElementById('search-status').textContent"),'');
  report.stages.push('failed_save_preserves_selection_and_notifies_only');
  await window.webContents.executeJavaScript("loadSearchSettings()");await pause(250);
  fs.writeFileSync(path.join(output,'search-settings.png'),(await window.webContents.capturePage()).toPNG());
  const layout=await window.webContents.executeJavaScript("(()=>{const node=document.getElementById('search-settings'),form=document.getElementById('search-form');return {visible:node.getBoundingClientRect().width>0,formWidth:form.getBoundingClientRect().width,viewport:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth}})()");
  assert.equal(layout.visible,true);assert.equal(layout.overflow,false);assert.ok(layout.formWidth>200);
  report.layout=layout;report.version=manifest.factory_version;report.pageErrors=pageErrors;
  assert.deepEqual(pageErrors,[]);report.status='passed';
}
run().catch(error=>{report.status='failed';report.error=error.message;process.exitCode=1;}).finally(async()=>{
  fs.writeFileSync(path.join(output,'summary.json'),JSON.stringify(report,null,2));
  window?.destroy();if(child){child.stdin.end();await new Promise(resolve=>{child.once('exit',resolve);setTimeout(()=>{child.kill();resolve();},3000).unref();});}
  console.log(JSON.stringify(report));app.exit(report.status==='passed'?0:1);
});
