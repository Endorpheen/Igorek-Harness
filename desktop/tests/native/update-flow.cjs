const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {createHash}=require('node:crypto');
const {spawn}=require('node:child_process');

async function verifyUpdateFlow({appRoot,root,BrowserWindow,ipcMain,python}) {
  const {updateDialog}=require(path.join(appRoot,'release-updates-ui.cjs'));
  const {createUpdatePresentation}=require(path.join(appRoot,'release-progress.cjs'));
  const payload=Buffer.alloc(2048,42);
  const name='igorek-0.2.2-'+(process.platform==='win32'?'win-x64.exe':'linux-amd64.deb');
  const release={tag_name:'v0.2.2',prerelease:true,assets:[{name,size:payload.length,
    digest:'sha256:'+createHash('sha256').update(payload).digest('hex'),
    browser_download_url:'https://github.com/Endorpheen/Igorek-Harness/releases/download/v0.2.2/'+name}]};
  let interval,window,parent;
  const server=http.createServer((request,response)=>{
    if(request.url==='/metadata'){response.setHeader('Content-Type','application/json');response.end(JSON.stringify([release]));}
    else if(request.url==='/redirect'){response.writeHead(302,{location:'https://release-assets.githubusercontent.com/fixture'});response.end();}
    else {let offset=0;interval=setInterval(()=>{response.write(payload.subarray(offset,offset+256));offset+=256;
      if(offset===payload.length){clearInterval(interval);response.end();}},60);}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  try {
    parent=new BrowserWindow({show:false,webPreferences:{sandbox:true,nodeIntegration:false}});
    const show=updateDialog({app:{getPath:()=>path.join(root,'update-cache')},current:()=> '0.2.1',channel:()=> 'preview',shell:{},
      presentationFactory:owner=>{const value=createUpdatePresentation(owner,{BrowserWindow,ipcMain});
        window=BrowserWindow.getAllWindows().find(candidate=>candidate.getParentWindow()===owner);return value;},
      session:{fetch:()=>assert.fail('Chromium fetch must not handle manual update redirects')},
      fetch:(url,options)=>globalThis.fetch(origin+(url.includes('api.github.com')?'/metadata':new URL(url).hostname==='github.com'?'/redirect':'/payload'),options)});
    const result=show(parent);let initial,progress=false,confirmed=false;
    for(let attempt=0;attempt<200;attempt++) {
      await pause(25);
      if(!window||window.isDestroyed())continue;
      const value=await window.webContents.executeJavaScript("({message:document.getElementById('message')?.textContent,percent:document.getElementById('percent')?.textContent})");
      if(!initial&&value.message?.includes('Доступен')) {
        initial=window.id;await window.webContents.executeJavaScript('document.querySelector("button").click()');
      } else if(value.message?.includes('Скачиваем')&&parseInt(value.percent)>0)progress=true;
      else if(value.message?.includes('готов к установке')) {
        assert.equal(window.id,initial);assert.ok(progress);
        await window.webContents.executeJavaScript('document.querySelectorAll("button")[1].click()');confirmed=true;break;
      }
    }
    assert.ok(confirmed,'Persistent update window must reach verified confirmation');
    assert.equal((await result).state,'downloaded');
  } finally {
    clearInterval(interval);if(window&&!window.isDestroyed())window.destroy();if(parent&&!parent.isDestroyed())parent.destroy();
    await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
  }
  if(process.platform==='linux') {
    const directory=path.join(root,'update-lock');fs.mkdirSync(directory);
    const lock=path.join(directory,'run.lock'),receipt=path.join(directory,'result.json');
    const helper=path.join(directory,'helper.py'),launcher=path.join(directory,'launcher.cjs');
    fs.writeFileSync(helper,`import fcntl, importlib.util, json, os, sys, time
from pathlib import Path
spec=importlib.util.spec_from_file_location('update',sys.argv[1])
update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)
update.close_inherited_installation_lock({'kind':'snapshot','target':sys.argv[2]})
time.sleep(0.5)
with (Path(sys.argv[2])/'run.lock').open('r+b') as stream:
    fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (Path(sys.argv[2])/'result.json').write_text(json.dumps({'exclusive':True}))
`);
    fs.writeFileSync(launcher,`const {app}=require('electron');const {spawn}=require('node:child_process');
app.setPath('userData',${JSON.stringify(path.join(directory,'electron'))});
app.whenReady().then(()=>{const child=spawn(${JSON.stringify(python)},['-I','-B',${JSON.stringify(helper)},${JSON.stringify(path.join(appRoot,'release-install.py'))},${JSON.stringify(directory)}],{detached:true,stdio:'ignore'});child.unref();app.exit(0);});`);
    const code=await new Promise((resolve,reject)=>{
      const child=spawn('/usr/bin/flock',['--shared',lock,process.execPath,launcher],{stdio:'ignore'});
      child.once('error',reject);child.once('exit',resolve);
    });
    assert.equal(code,0);
    for(let attempt=0;attempt<100&&!fs.existsSync(receipt);attempt++)await pause(50);
    assert.equal(JSON.parse(fs.readFileSync(receipt,'utf8')).exclusive,true);
  }
  console.log('PASS native update redirects, persistent progress and launcher lock');
}
module.exports={verifyUpdateFlow};
