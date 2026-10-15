const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{EventEmitter}=require('node:events');
const {ChatGPTWorkspace,allowedURL,mediaRequest}=require('../chatgpt-workspace.cjs');
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-chatgpt-test-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const ses={getUserAgent:()=> 'Test/1.0',setUserAgent(){},flushStorageData(){},cookies:{flushStore:async()=>{}},setPermissionCheckHandler(fn){this.check=fn},setPermissionRequestHandler(fn){this.request=fn}};
 class View{constructor(options){this.options=options;this.visible=false;this.webContents=Object.assign(new EventEmitter(),{session:ses,url:'',destroyed:false,isDestroyed(){return this.destroyed},getURL(){return this.url},isLoading(){return false},loadURL:async function(url){this.url=url},close(){this.destroyed=true},setIgnoreMenuShortcuts(){},setWindowOpenHandler(fn){this.open=fn},navigationHistory:{canGoBack:()=>false,canGoForward:()=>false}})}setVisible(value){this.visible=value}setBounds(value){this.bounds=value}}
 const parent=Object.assign(new EventEmitter(),{isDestroyed:()=>false,getContentSize:()=>[1280,900],contentView:{addChildView(){},removeChildView(){}},webContents:Object.assign(new EventEmitter(),{executeJavaScript:async()=>({x:328,y:36,width:952,height:864}),send(){}})});
 const workspace=new ChatGPTWorkspace({root,WebContentsView:View,session:{fromPath:()=>ses},dialog:{showMessageBox:async()=>({response:0})}});
 return {workspace,parent,ses};
}
test('ChatGPT panel allows only explicit HTTPS source and sign-in origins',()=>{
 for(const url of ['https://chatgpt.com/images','https://auth.openai.com/','https://accounts.google.com/'])assert.equal(allowedURL(url),true);
 for(const url of ['file:///etc/passwd','http://chatgpt.com/','https://chatgpt.com.evil/','https://user@chatgpt.com/','javascript:alert(1)'])assert.equal(allowedURL(url),false);
});
test('browser User-Agent keeps its actual Chromium version without desktop application tokens',async t=>{
 const {workspace:w,parent:p,ses}=fixture(t);ses.getUserAgent=()=> 'Mozilla/5.0 Chrome/148 Игорёк/1.0 Electron/44';let agent;ses.setUserAgent=value=>agent=value;w.unlock();await w.show(p);assert.equal(agent,'Mozilla/5.0 Chrome/148');
});
test('media permissions require the main ChatGPT frame and explicit audio/video types',()=>{
 assert.equal(mediaRequest('media',{requestingUrl:'https://chatgpt.com/',isMainFrame:true,mediaTypes:['audio']}),true);
 for(const value of [{requestingUrl:'https://evil.example/',isMainFrame:true,mediaTypes:['audio']},{requestingUrl:'https://chatgpt.com/',isMainFrame:false,mediaTypes:['audio']},{requestingUrl:'https://chatgpt.com/',isMainFrame:true,mediaTypes:[]}])assert.equal(mediaRequest('media',value),false);
 assert.equal(mediaRequest('geolocation',{}),false);
});
test('panel is blocked until unlocked and fits the main workspace without covering navigation',async t=>{
 const {workspace:w,parent:p}=fixture(t);assert.deepEqual(await w.show(p),{visible:false});w.unlock();await w.show(p);
 assert.deepEqual(w.view.bounds,{x:328,y:36,width:952,height:864});assert.equal(w.view.options.webPreferences.preload,undefined);assert.equal(w.view.options.webPreferences.nodeIntegration,false);
 const before=w.view;w.hide();assert.equal(before.visible,false);await w.show(p);assert.equal(w.view,before);assert.equal(before.visible,true);
});
test('application modal overlays hide the site and restore the same page afterwards',async t=>{
 const {workspace:w,parent:p}=fixture(t);let overlay=false;w.overlayActive=()=>overlay;w.unlock();await w.show(p);const view=w.view;
 overlay=true;await w.layout();assert.equal(view.visible,false);overlay=false;await w.layout();assert.equal(w.view,view);assert.equal(view.visible,true);
});
test('locking closes remote contents and prevents stale pending layout from reopening them',async t=>{
 const {workspace:w,parent:p}=fixture(t);w.unlock();await w.show(p);const view=w.view;let resolve;p.webContents.executeJavaScript=()=>new Promise(done=>resolve=done);const pending=w.layout();w.lock();resolve({x:0,y:0,width:1280,height:900});await pending;
 assert.equal(w.view,null);assert.equal(view.webContents.isDestroyed(),true);assert.equal(w.attached,false);assert.equal(w.blocked,true);
});
test('microphone permission is confirmed by the user and cannot complete after lock',async t=>{
 const {workspace:w,parent:p,ses}=fixture(t);w.unlock();await w.show(p);let result;
  await ses.request(w.view.webContents,'media',value=>result=value,{isMainFrame:true,requestingUrl:'https://chatgpt.com/',mediaTypes:['audio']});assert.equal(result,true);
  assert.equal(ses.check(w.view.webContents,'media','https://chatgpt.com',{mediaType:'audio'}),true);
 await ses.request(w.view.webContents,'clipboard-read',value=>result=value,{isMainFrame:true,requestingUrl:'https://chatgpt.com/'});assert.equal(result,false);
 let respond;w.dialog.showMessageBox=()=>new Promise(done=>respond=done);const pending=ses.request(w.view.webContents,'media',value=>result=value,{isMainFrame:true,requestingUrl:'https://chatgpt.com/',mediaTypes:['video']});w.lock();respond({response:0});await pending;assert.equal(result,false);
});
test('navigation and popup policy preserve browser isolation',async t=>{
 const {workspace:w,parent:p}=fixture(t);w.unlock();await w.show(p);const wc=w.view.webContents;
 assert.equal(wc.open({url:'https://evil.example/'}).action,'deny');const login=wc.open({url:'https://auth.openai.com/'});assert.equal(login.action,'allow');assert.equal(login.overrideBrowserWindowOptions.webPreferences.nodeIntegration,false);assert.equal(login.overrideBrowserWindowOptions.webPreferences.preload,undefined);
 let prevented=false;wc.emit('will-navigate',{preventDefault(){prevented=true}},'file:///etc/passwd');assert.equal(prevented,true);
});
test('microphone grants are reused and remembered across closing while camera and other origins remain separate',async t=>{
 const {workspace:w,parent:p,ses}=fixture(t);let prompts=0;w.dialog.showMessageBox=async()=>{prompts++;return {response:0,checkboxChecked:true}};w.unlock();await w.show(p);let result;
 const request=()=>ses.request(w.view.webContents,'media',value=>result=value,{isMainFrame:true,requestingUrl:'https://chatgpt.com/',mediaTypes:['audio']});
 await request();assert.equal(prompts,1);await request();assert.equal(prompts,1);w.lock();w.unlock();await w.show(p);await request();assert.equal(result,true);assert.equal(prompts,1);
 assert.equal(ses.check(w.view.webContents,'media','https://evil.example',{mediaType:'audio'}),false);assert.equal(ses.check(w.view.webContents,'media','https://chatgpt.com',{mediaType:'video'}),false);
 w.hide();assert.equal(ses.check(w.view.webContents,'media','https://chatgpt.com',{mediaType:'audio'}),false);
});
test('ChatGPT Home supports buttons, links and titled controls without a specific navigation wrapper',()=>{
 const vm=require('node:vm'),{CHATGPT_HOME_FIX}=require('../chatgpt-workspace.cjs');
 for(const attributes of [{'aria-label':'Home'},{'aria-label':'Главная'},{title:'Home'},{text:'Домой'}]){
  let listener,url='',prevented=0,installed=0;
  const window={addEventListener:(_name,value)=>{listener=value;installed++}};
  const context={window,location:{origin:'https://chatgpt.com',href:'https://chatgpt.com/schedules',assign:value=>url=value},URL};
  vm.runInNewContext(CHATGPT_HOME_FIX,context);vm.runInNewContext(CHATGPT_HOME_FIX,context);assert.equal(installed,1);
  const target={closest:()=>({getAttribute:name=>attributes[name]||null,textContent:attributes.text||'',matches:()=>false})};
  const event={isTrusted:true,target,preventDefault(){prevented++},stopImmediatePropagation(){}};
  for(const extra of [{isTrusted:false},{button:1},{ctrlKey:true},{shiftKey:true},{metaKey:true},{altKey:true},{defaultPrevented:true}])listener({...event,...extra});
  assert.equal(url,'');listener(event);assert.equal(url,'https://chatgpt.com/');assert.equal(prevented,1);
 }
 let installed=false;vm.runInNewContext(CHATGPT_HOME_FIX,{window:{addEventListener(){installed=true}},location:{origin:'https://example.com'}});assert.equal(installed,false);
});
test('unlabelled Home links must point to the ChatGPT root within application navigation',()=>{
 const vm=require('node:vm'),{CHATGPT_HOME_FIX}=require('../chatgpt-workspace.cjs');
 for(const [href,inNavigation,expected] of [['/',true,true],['https://chatgpt.com/',true,true],['/',false,false],['https://evil.example/',true,false],['/images',true,false],['/#section',true,false]]){
  let listener,url='';const context={window:{addEventListener:(_name,value)=>listener=value},location:{origin:'https://chatgpt.com',href:'https://chatgpt.com/images',assign:value=>url=value},URL};vm.runInNewContext(CHATGPT_HOME_FIX,context);
  const button={getAttribute:name=>name==='href'?href:null,textContent:'',matches:()=>true,closest:()=>inNavigation?{}:null};
  listener({isTrusted:true,target:{closest:()=>button},preventDefault(){},stopImmediatePropagation(){}});assert.equal(Boolean(url),expected,href);
 }
});
test('concurrent microphone requests share one permission dialog',async t=>{
 const {workspace:w,parent:p,ses}=fixture(t);let prompts=0,respond;w.dialog.showMessageBox=()=>{prompts++;return new Promise(resolve=>respond=resolve)};w.unlock();await w.show(p);let first,second;
 const details={isMainFrame:true,requestingUrl:'https://chatgpt.com/',mediaTypes:['audio']};const a=ses.request(w.view.webContents,'media',value=>first=value,details),b=ses.request(w.view.webContents,'media',value=>second=value,details);assert.equal(prompts,1);respond({response:0,checkboxChecked:true});await Promise.all([a,b]);assert.equal(first,true);assert.equal(second,true);
});
test('copy shortcuts target the attached browser view and are ignored after hide',async t=>{
 const {workspace:w,parent:p}=fixture(t);w.unlock();await w.show(p);const wc=w.view.webContents;let copied=0,prevented=0;wc.copy=()=>copied++;
 const input={type:'keyDown',control:true,key:'c'};wc.emit('before-input-event',{preventDefault(){prevented++}},input);assert.equal(copied,1);assert.equal(prevented,1);
 wc.emit('before-input-event',{preventDefault(){prevented++}},{...input,shift:true});assert.equal(copied,1);w.hide();wc.emit('before-input-event',{preventDefault(){prevented++}},input);assert.equal(copied,1);
});
test('selected text gets a native Copy action without granting clipboard read access',async t=>{
 const {workspace:w,parent:p}=fixture(t);w.unlock();await w.show(p);const wc=w.view.webContents;let items,copied=0;wc.copy=()=>copied++;wc.url='https://chatgpt.com/';wc.executeJavaScript=async()=>({enabled:false,available:false});w.menu={buildFromTemplate(value){items=value;return{popup(){}}}};
 wc.emit('context-menu',{}, {selectionText:'fixture text',x:20,y:30});await new Promise(r=>setImmediate(r));assert.equal(items[0].label,'Копировать');items[0].click();assert.equal(copied,1);w.hide();items[0].click();assert.equal(copied,1);
});
test('page context adapter is limited to ChatGPT and trusted selected-text events',()=>{
 const {PAGE_TOOLS}=require('../chatgpt-page-tools.cjs'),vm=require('node:vm');let handler,stopped=0;
 const context={location:{origin:'https://chatgpt.com'},window:{addEventListener(name,fn){if(name==='contextmenu')handler=fn},getSelection:()=>({toString:()=> 'selected'})},document:{elementFromPoint:()=>null,querySelectorAll:()=>[]},innerWidth:1000,innerHeight:800,clearInterval(){}};
 vm.runInNewContext(PAGE_TOOLS,context);handler({isTrusted:false,stopImmediatePropagation(){stopped++}});assert.equal(stopped,0);handler({isTrusted:true,stopImmediatePropagation(){stopped++}});assert.equal(stopped,1);assert.equal(context.window.__igorekPageTools.probe(0,0).enabled,false);
 const other={location:{origin:'https://example.com'},window:{}};vm.runInNewContext(PAGE_TOOLS,other);assert.equal(other.window.__igorekPageTools,undefined);
});
test('ChatGPT right clicks open the host menu before page handlers and preserve selection metadata',async t=>{
 const {workspace:w,parent:p}=fixture(t);w.unlock();await w.show(p);const wc=w.view.webContents;wc.url='https://chatgpt.com/';let menus=0,items,prevented=0,code='';wc.getZoomFactor=()=>2;wc.executeJavaScript=async value=>{code=value;return{enabled:false,available:true,selected:false}};w.menu={buildFromTemplate(value){items=value;return{popup(){menus++}}}};
 for(const type of ['mouseDown','mouseUp'])wc.emit('before-mouse-event',{preventDefault(){prevented++}},{type,button:'right',x:200,y:400});await new Promise(r=>setImmediate(r));assert.equal(prevented,2);assert.equal(menus,1);assert.equal(items[0].label,'Осьминог здесь (прототип)');assert.ok(code.endsWith('probe(100,200)'));
 wc.executeJavaScript=async()=>({enabled:false,available:true,selected:true});wc.emit('before-mouse-event',{preventDefault(){}},{type:'mouseUp',button:'right',x:200,y:400});await new Promise(r=>setImmediate(r));assert.equal(items[0].label,'Копировать');assert.equal(items[1].label,'Осьминог здесь (прототип)');
 w.hide();wc.emit('before-mouse-event',{preventDefault(){throw Error('hidden page intercepted')}},{type:'mouseUp',button:'right',x:200,y:400});wc.url='https://example.com/';w.attached=true;wc.emit('before-mouse-event',{preventDefault(){throw Error('other site intercepted')}},{type:'mouseUp',button:'right',x:200,y:400});assert.equal(menus,2);
});
