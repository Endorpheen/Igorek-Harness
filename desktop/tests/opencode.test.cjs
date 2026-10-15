const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),http=require('node:http'),{EventEmitter}=require('node:events');
const {OpenCodeService,sandboxArgv,projectPath}=require('../opencode-service.cjs');
const {OpenCodeWorkspace}=require('../opencode-workspace.cjs');
const {publicTarget,egressServer}=require('../opencode-egress.cjs');
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
function root(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-oc-test-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;}
function fixture(t){
  const dir=root(t),service=new OpenCodeService(path.join(dir,'service'));service.origin='http://127.0.0.1:45123';service.start=async()=>service.origin;
  const ses=Object.assign(new EventEmitter(),{getUserAgent:()=> 'Electron/44',setUserAgent(){},webRequest:{onBeforeRequest(fn){ses.before=fn},onBeforeSendHeaders(fn){ses.headers=fn}},setPermissionCheckHandler(fn){ses.check=fn},setPermissionRequestHandler(fn){ses.permission=fn},setDevicePermissionHandler(fn){ses.device=fn},flushStorageData(){},cookies:{flushStore:async()=>{}}});
  class View{constructor(options){this.options=options;this.webContents=Object.assign(new EventEmitter(),{session:ses,url:'',isDestroyed(){return Boolean(this.dead)},close(){this.dead=true;this.emit('destroyed')},isLoading:()=>false,getURL(){return this.url},loadURL:async function(value){this.url=value;this.emit('did-navigate',{},value)},setIgnoreMenuShortcuts(){},setWindowOpenHandler(fn){this.open=fn},navigationHistory:{canGoBack:()=>false,canGoForward:()=>false}});}setVisible(value){this.visible=value}setBounds(value){this.bounds=value}}
  const parent=Object.assign(new EventEmitter(),{isDestroyed:()=>false,getContentSize:()=>[1200,900],contentView:{addChildView(){},removeChildView(){}},webContents:Object.assign(new EventEmitter(),{executeJavaScript:async()=>({x:750,y:140,width:450,height:760}),send(){}})});
  const workspace=new OpenCodeWorkspace({root:path.join(dir,'profile'),service,WebContentsView:View,session:{fromPath:()=>ses},dialog:{showMessageBox:async()=>({response:1})},shell:{openExternal:async()=>{throw Error('unexpected external')}}});
  return {workspace,parent,ses,service};
}
test('sandbox isolates network, processes, filesystem and environment with no host credentials',t=>{
  const dir=root(t),args=sandboxArgv({root:dir,bridge:'/tmp/oc-bridge',projects:['/tmp/oc-project'],script:'/test/bridge.py'});
  for(const flag of ['--unshare-all','--die-with-parent','--clearenv','--cap-drop'])assert(args.includes(flag));
  assert(!args.includes('--share-net'));assert(!args.includes('--dev-bind'));assert(!args.includes(os.homedir()));
  assert.deepEqual(args.slice(-4),['--','/usr/bin/python3','-I','/opt/igorek/opencode-sandbox.py']);
});
test('project grants reject home, system roots and integration control data',t=>{
  const dir=root(t);for(const item of ['/',os.homedir(),'/usr','/etc/ssl','/tmp',dir])assert.throws(()=>projectPath(item,dir));
  const project=path.join(dir,'outside');fs.mkdirSync(project);assert.throws(()=>projectPath(project,dir));
  const control=path.join(dir,'control');fs.mkdirSync(control);assert.equal(projectPath(project,control),project);
});
test('public egress rejects loopback, LAN, mixed DNS and metadata while pinning public answers',async()=>{
  for(const address of ['127.0.0.1','10.2.3.4','192.168.1.1','169.254.169.254','::1','::ffff:127.0.0.1','fd00::1'])await assert.rejects(publicTarget('example.com',async()=>[{address,family:4}]));
  await assert.rejects(publicTarget('example.com',async()=>[{address:'8.8.8.8',family:4},{address:'127.0.0.1',family:4}]));
  assert.deepEqual(await publicTarget('example.com',async()=>[{address:'8.8.8.8',family:4}]),{address:'8.8.8.8',family:4});
});
test('egress proxy refuses arbitrary ports and local HTTP requests',async t=>{
  const proxy=egressServer();await listen(proxy);t.after(()=>proxy.shutdown());
  for(const target of ['http://127.0.0.1/','http://169.254.169.254/','http://example.com:8080/']){
    const status=await new Promise((resolve,reject)=>{http.get({host:'127.0.0.1',port:proxy.address().port,path:target},res=>{res.resume();resolve(res.statusCode)}).on('error',reject)});assert.equal(status,403);
  }
});
test('gateway requires its private capability and exact Host/Origin, forwards streams with separate server authentication',async t=>{
  const dir=root(t),service=new OpenCodeService(path.join(dir,'service'));service.bridge=process.platform==='win32'?'\\\\.\\pipe\\igorek-test-'+require('node:crypto').randomUUID():dir;let received;
  const upstream=http.createServer((req,res)=>{received=req.headers.authorization;res.writeHead(200,{'content-type':'text/event-stream'});res.write('data: first\n\n');setTimeout(()=>res.end('data: second\n\n'),10)});
  await new Promise(resolve=>upstream.listen(path.join(service.bridge,'api.sock'),resolve));t.after(()=>upstream.close());await service.gateway();t.after(()=>service.stop());
  assert.equal((await fetch(service.origin+'/api/info')).status,403);
  assert.equal((await fetch(service.origin+'/api/info',{headers:{Authorization:'Bearer '+service.token,Origin:'https://evil.example'}})).status,403);
  const reply=await fetch(service.origin+'/api/event',{headers:{Authorization:'Bearer '+service.token}});assert.equal(reply.status,200);assert.match(await reply.text(),/first[\s\S]*second/);
  assert.equal(received,'Basic '+Buffer.from('opencode:'+service.password).toString('base64'));assert(!JSON.stringify(service.status()).includes(service.password));assert(!JSON.stringify(service.status()).includes(service.token));
});
test('OpenCode view has no preload/Node bridge, denies devices/downloads and restricts all network requests',async t=>{
  const {workspace:w,parent,ses,service}=fixture(t);w.unlock();await w.show(parent);
  const prefs=w.view.options.webPreferences;assert.equal(prefs.sandbox,true);assert.equal(prefs.contextIsolation,true);assert.equal(prefs.nodeIntegration,false);assert.equal(prefs.preload,undefined);assert.equal(prefs.webSecurity,true);
  assert.equal(ses.check(),false);assert.equal(ses.device(),false);
  for(const url of ['http://127.0.0.1:45124/','file:///etc/passwd','https://evil.example/','http://localhost:45123/'])ses.before({url},value=>assert.equal(value.cancel,true));
  ses.before({url:service.origin+'/api/event'},value=>assert.equal(value.cancel,false));
  ses.before({url:'ws://127.0.0.1:45123/api/pty'},value=>assert.equal(value.cancel,false));
  ses.headers({url:service.origin+'/',requestHeaders:{authorization:'attacker'}},value=>assert.equal(value.requestHeaders.Authorization,'Bearer '+service.token));
  let denied=false;ses.emit('will-download',{preventDefault(){denied=true}});assert.equal(denied,true);w.lock();
});
test('switching tabs preserves the same session view; lock destroys it and next show restores saved route',async t=>{
  const {workspace:w,parent}=fixture(t);w.unlock();await w.show(parent);const view=w.view;
  view.webContents.emit('did-navigate-in-page',{},w.service.origin+'/project/session/one');w.hide();await w.show(parent);assert.equal(w.view,view);assert.equal(view.visible,true);
  w.lock();assert.equal(view.webContents.isDestroyed(),true);w.unlock();await w.show(parent);assert(w.view.webContents.getURL().endsWith('/project/session/one'));w.lock();
});
test('hide and lock cancel a pending asynchronous show without losing the service state',async t=>{
  const {workspace:w,parent,service}=fixture(t);let finish;service.start=()=>new Promise(resolve=>finish=resolve);w.unlock();const pending=w.show(parent);w.hide();finish(service.origin);await pending;assert.equal(w.view,null);assert.equal(w.attached,false);
  const locked=w.show(parent);w.lock();finish(service.origin);await locked;assert.equal(w.view,null);
});
test('native navigation cannot leave the pinned OpenCode origin or open privileged popups',async t=>{
  const {workspace:w,parent}=fixture(t);w.unlock();await w.show(parent);let denied=false;
  w.view.webContents.emit('will-navigate',{preventDefault(){denied=true}},'file:///etc/passwd');assert.equal(denied,true);
  assert.deepEqual(w.view.webContents.open({url:'javascript:alert(1)'}),{action:'deny'});w.lock();
});
test('disabled integration refuses startup and keeps persisted project/state data',async t=>{
  const dir=root(t),service=new OpenCodeService(path.join(dir,'service'));await service.disable();await assert.rejects(service.start(),/Включи/);assert.equal(new OpenCodeService(path.join(dir,'service')).status().enabled,false);
});
test('renderer failure can reconnect without stopping the independent OpenCode service',async t=>{
  const {workspace:w,parent}=fixture(t);w.unlock();await w.show(parent);const old=w.view;
  old.webContents.emit('render-process-gone');assert.equal(w.view,null);assert.equal(w.status().state,'error');assert.equal(w.blocked,false);
  await w.show(parent);assert.notEqual(w.view,old);assert.notEqual(w.status().state,'error');w.lock();
});
test('corrupt optional integration settings do not prevent the desktop runtime from starting',t=>{
  const dir=root(t);fs.writeFileSync(path.join(dir,'connection.json'),'{broken');const service=new OpenCodeService(dir);assert.equal(service.status().state,'error');assert.deepEqual(service.status().projects,[]);
});
test('crashed renderer is detected even when Electron omits the process-gone event',async t=>{
  const {workspace:w,parent}=fixture(t);w.unlock();await w.show(parent);w.view.webContents.isCrashed=()=>true;
  await new Promise(resolve=>setTimeout(resolve,1100));assert.equal(w.view,null);assert.equal(w.status().state,'error');
});
test('host-service delegation mounts only a read-only socket, capability and stdio client',t=>{
  const dir=root(t),args=sandboxArgv({root:dir,bridge:'/tmp/bridge',projects:['/tmp/project'],script:'/test/bridge.py',hostServices:{socket:'/private/api.sock'}});
  assert(args.includes('--unshare-all'));assert(!args.includes('--share-net'));
  const index=args.indexOf('/private/api.sock');assert.equal(args[index-1],'--ro-bind');assert.equal(args[index+1],'/run/igorek-host-services.sock');
  const config=JSON.parse(args[args.indexOf('OPENCODE_CONFIG_CONTENT')+1]);assert.equal(config.mcp.servers.igorek.type,'local');assert.equal(config.mcp.servers.igorek.codemode,false);
  assert(!args.some(value=>value.includes('/dev/nvidia')));assert(!JSON.stringify(args).includes('wangp.json'));
});
test('enabling host services is explicit, validated and persists without exposing capability tokens',async t=>{
  const dir=root(t),service=new OpenCodeService(dir);assert.deepEqual(service.status().host_services,[]);
  await service.configureHostServices(['demo']);assert.deepEqual(new OpenCodeService(dir).status().host_services,['demo']);
  await assert.rejects(service.configureHostServices(['../other']));assert(!JSON.stringify(service.status()).includes('token'));
});

test('profile bridge grants only its readonly socket and revokes its capability on stop',async t=>{
  const dir=root(t),calls=[];
  const service=new OpenCodeService(dir,{profilesControl:async(operation,value)=>{calls.push([operation,value]);return {};}});
  const args=sandboxArgv({root:dir,bridge:'/tmp/bridge',projects:['/tmp/project'],script:'/test/bridge.py',profiles:{socket:'/private/profiles.sock'}});
  const i=args.indexOf('/private/profiles.sock');assert.equal(args[i-1],'--ro-bind');assert.equal(args[i+1],'/run/igorek-profiles.sock');
  assert(!args.includes('--share-net'));
  service.profilesGrant={token:'fixture-capability',profiles:[{name:'local-test'}]};
  assert(!JSON.stringify(service.status()).includes('fixture-capability'));
  await service.stop();assert.deepEqual(calls,[['revoke',{token:'fixture-capability'}]]);assert.equal(service.profilesGrant,null);
});
