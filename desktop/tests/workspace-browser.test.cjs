const { assertPrivatePermissions } = require('./helpers/private-permissions.cjs');
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),{EventEmitter}=require('node:events');
const {WorkspaceBrowser}=require('../workspace-browser.cjs');
function fixture(t){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-workspace-test-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const ses=Object.assign(new EventEmitter(),{getUserAgent:()=> 'Test/1.0',setUserAgent(){},flushStorageData(){},cookies:{flushStore:async()=>{}},setPermissionCheckHandler(fn){this.check=fn},setPermissionRequestHandler(fn){this.request=fn},clearStorageData:async()=>{ses.cleared=true}});
 class View{constructor(options){this.options=options;this.webContents=Object.assign(new EventEmitter(),{session:ses,url:'',destroyed:false,zoom:1,isDestroyed(){return this.destroyed},getURL(){return this.url},getTitle(){return this.url},isLoading(){return false},loadURL:async function(url){this.url=url;this.emit('did-navigate',{},url)},close(){this.destroyed=true},setIgnoreMenuShortcuts(){},setWindowOpenHandler(fn){this.open=fn},getZoomFactor(){return this.zoom},setZoomFactor(value){this.zoom=value},findInPage(value){this.find=value},stopFindInPage(){this.find=''},print(value){this.printOptions=value},navigationHistory:{canGoBack:()=>false,canGoForward:()=>false}})}setVisible(value){this.visible=value}setBounds(value){this.bounds=value}}
 const parent=Object.assign(new EventEmitter(),{isDestroyed:()=>false,getContentSize:()=>[1280,900],contentView:{addChildView(){},removeChildView(){}},webContents:Object.assign(new EventEmitter(),{executeJavaScript:async()=>({x:700,y:130,width:580,height:770}),send(){}})});
 const options={root,WebContentsView:View,session:{fromPath:()=>ses},dialog:{showMessageBox:async()=>({response:1})},shell:{showItemInFolder(value){options.revealed=value}}};
 const manager=new WorkspaceBrowser(options);return {manager,parent,ses,options};
}
test('workspace browser requires unlock and refuses local and credentialed destinations',async t=>{const {manager:m,parent:p}=fixture(t);await assert.rejects(m.action(p,'open',{id:'one',url:'https://example.com'}));m.unlock();for(const url of ['file:///etc/passwd','http://example.com','https://127.0.0.1','https://user@example.com'])await assert.rejects(m.action(p,'open',{id:'one',url}));assert.equal(m.tabs.size,0);});
test('browser tabs keep their page and isolation while switching and hiding',async t=>{const {manager:m,parent:p}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com/one'});const first=m.tabs.get('one').view;await m.action(p,'open',{id:'two',url:'https://example.org/two'});assert.equal(first.visible,false);await m.action(p,'select',{id:'one'});assert.equal(m.tabs.get('one').view,first);assert.equal(first.visible,true);assert.equal(first.options.webPreferences.preload,undefined);assert.equal(first.options.webPreferences.nodeIntegration,false);m.hide();assert.equal(first.visible,false);assert.equal(first.webContents.isDestroyed(),false);});
test('zoom, find and print affect only the selected page and unrelated windows cannot act',async t=>{const {manager:m,parent:p}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com'});const wc=m.tabs.get('one').view.webContents;await m.action(p,'zoom',{value:1.2});assert.equal(wc.zoom,1.2);await assert.rejects(m.action(p,'zoom',{value:5}));await m.action(p,'find',{text:'needle'});assert.equal(wc.find,'needle');await m.action(p,'print');assert.equal(wc.printOptions.silent,false);await assert.rejects(m.action({...p},'reload'));});
test('history strips query and hash and closing a tab destroys its web contents',async t=>{const {manager:m,parent:p}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com/a?private=secret#section'});assert.equal(m.status().history[0].url,'https://example.com/a');const wc=m.tabs.get('one').view.webContents;await m.action(p,'close',{id:'one'});assert.equal(wc.isDestroyed(),true);assert.equal(m.tabs.size,0);});
test('download list excludes paths and refuses unknown sources and unfinished files',async t=>{const {manager:m,parent:p,ses,options}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com'});const item=Object.assign(new EventEmitter(),{getFilename:()=> 'image.png',getTotalBytes:()=>20,getReceivedBytes:()=>20,getSavePath:()=>'/tmp/image.png'});let prevented=false;ses.emit('will-download',{preventDefault(){prevented=true}},item,{});assert.equal(prevented,true);ses.emit('will-download',{preventDefault(){throw Error('unexpected')}},item,m.tabs.get('one').view.webContents);const id=m.status().downloads[0].id;await assert.rejects(m.action(p,'openDownload',{id}));item.emit('done',{},'completed');assert.equal(m.status().downloads[0].path,undefined);await m.action(p,'openDownload',{id});assert.equal(options.revealed,'/tmp/image.png');});
test('lock destroys tabs, clears private activity and rejects a late clear confirmation',async t=>{const {manager:m,parent:p,options,ses}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com'});const wc=m.tabs.get('one').view.webContents;let respond;options.dialog.showMessageBox=()=>new Promise(done=>respond=done);const clear=m.action(p,'clear');m.lock();m.unlock();respond({response:0});await clear;assert.equal(ses.cleared,undefined);assert.equal(wc.isDestroyed(),true);assert.deepEqual(m.status().history,[]);assert.deepEqual(m.status().downloads,[]);});
test('explicit voice stop destroys the microphone-owning page before reopening the same address',async t=>{const {manager:m,parent:p}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://chatgpt.com/c/test'});const previous=m.tabs.get('one').view.webContents;await m.action(p,'stopVoice');assert.equal(previous.isDestroyed(),true);assert.notEqual(m.tabs.get('one').view.webContents,previous);assert.equal(m.tabs.get('one').view.webContents.getURL(),'https://chatgpt.com/c/test');});
test('website popup links become workspace tabs while sign-in retains an isolated opener popup',async t=>{const {manager:m,parent:p}=fixture(t);let sent;p.webContents.send=(channel,value)=>{if(channel==='igorek:workspace-popup')sent=value};m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com'});const wc=m.tabs.get('one').view.webContents;assert.equal(wc.open({url:'https://example.org/next'}).action,'deny');assert.equal(sent,'https://example.org/next');assert.equal(wc.open({url:'https://accounts.google.com/'}).action,'allow');});
test('resetting microphone permission revokes the saved grant and replaces the active ChatGPT page',async t=>{const {manager:m,parent:p}=fixture(t);m.unlock();m.permissions.setMicrophone(true);await m.action(p,'open',{id:'one',url:'https://chatgpt.com/'});const wc=m.tabs.get('one').view.webContents;await m.action(p,'resetMicrophone');assert.equal(m.permissions.microphone(),false);assert.equal(wc.isDestroyed(),true);assert.notEqual(m.tabs.get('one').view.webContents,wc);});
test('download history survives restart with private permissions and public timestamps',async t=>{
 const {manager:m,parent:p,ses,options}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com'});
 const file=path.join(options.root,'saved-image.webp');fs.writeFileSync(file,'fixture');const item=Object.assign(new EventEmitter(),{getFilename:()=> 'saved-image.webp',getTotalBytes:()=>7,getReceivedBytes:()=>7,getSavePath:()=>file});ses.emit('will-download',{preventDefault(){throw Error('unexpected')}},item,m.tabs.get('one').view.webContents);item.emit('done',{},'completed');
 const history=path.join(options.root,'downloads.json');assertPrivatePermissions(history, 0o600);const next=new WorkspaceBrowser(options);assert.deepEqual(next.status().downloads,[]);next.unlock();await next.action(p,'select',{id:null});const row=next.status().downloads[0];assert.equal(row.name,'saved-image.webp');assert.ok(row.at>0);assert.equal(row.can_reveal,true);assert.equal(row.path,undefined);await next.action(p,'openDownload',{id:row.id});assert.equal(options.revealed,file);
});
test('removing and clearing download records preserves files, site data and microphone grant',async t=>{
 const {manager:m,parent:p,ses,options}=fixture(t);m.unlock();await m.action(p,'select',{id:null});m.permissions.setMicrophone(true);const file=path.join(options.root,'report.txt');fs.writeFileSync(file,'keep');m.downloadHistory.add({id:'record-one',name:'report.txt',state:'completed',received:4,total:4,at:Date.now(),path:file});m.downloads=m.downloadHistory.rows;
 await assert.rejects(m.action({...p},'removeDownload',{id:'record-one'}));assert.equal(m.status().downloads.length,1);await m.action(p,'removeDownload',{id:'record-one'});assert.equal(m.status().downloads.length,0);assert.equal(fs.readFileSync(file,'utf8'),'keep');m.downloadHistory.add({id:'record-two',name:'report.txt',state:'completed',received:4,total:4,at:Date.now(),path:file});m.downloads=m.downloadHistory.rows;await m.action(p,'clearDownloads');assert.equal(m.status().downloads.length,0);assert.equal(fs.existsSync(file),true);assert.equal(ses.cleared,undefined);assert.equal(m.permissions.microphone(),true);
});
test('download completion while locked is saved without exposing history and deleted rows stay deleted',async t=>{
 const {manager:m,parent:p,ses}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com'});const item=Object.assign(new EventEmitter(),{getFilename:()=> 'late.txt',getTotalBytes:()=>7,getReceivedBytes:()=>7,getSavePath:()=>'/tmp/late.txt'});ses.emit('will-download',{preventDefault(){throw Error('unexpected')}},item,m.tabs.get('one').view.webContents);m.lock();item.emit('done',{},'completed');assert.deepEqual(m.status().downloads,[]);m.unlock();assert.equal(m.status().downloads[0].state,'completed');await m.action(p,'select',{id:null});await m.action(p,'clearDownloads');m.downloadHistory.finish({id:'missing',name:'late.txt',state:'completed',at:Date.now(),received:7,total:7,path:'/tmp/late.txt'});assert.deepEqual(m.status().downloads,[]);
});
test('history rejects symlinks and malformed data without overwriting the target',t=>{
 const {options}=fixture(t);const {DownloadHistory}=require('../download-history.cjs');const target=path.join(options.root,'protected.json');fs.writeFileSync(target,'keep');fs.symlinkSync(target,path.join(options.root,'downloads.json'));const history=new DownloadHistory(options.root);assert.equal(history.error,true);history.clear();assert.equal(fs.readFileSync(target,'utf8'),'keep');assert.equal(history.error,true);
});
test('unfinished downloads become interrupted on restart and search groups local calendar dates',t=>{
 const {options}=fixture(t);const {DownloadHistory}=require('../download-history.cjs'),{groups}=require('../../factory/web_ui/static/downloads.js');const history=new DownloadHistory(options.root);history.add({id:'one',name:'Снимок.png',state:'progressing',received:4,total:7,at:new Date(2026,9,10,12).getTime(),path:''});history.add({id:'two',name:'report.pdf',state:'completed',received:7,total:7,at:new Date(2026,9,7,18).getTime(),path:'/tmp/report.pdf'});const rows=new DownloadHistory(options.root).rows;assert.equal(rows.find(r=>r.id==='one').state,'interrupted');assert.equal(groups(rows).length,2);assert.equal(groups(rows,'СНИМОК')[0].items[0].id,'one');assert.equal(groups(rows,'none').length,0);assert.deepEqual(groups(null),[]);
});
test('WebRTC diagnostics opens a fixed isolated window without switching the voice tab or its permission handlers',async t=>{
 const {manager:m,parent:p,options,ses}=fixture(t);let created=0;
 options.BrowserWindow=class extends EventEmitter{constructor(config){super();created++;this.config=config;this.dead=false;this.webContents=Object.assign(new EventEmitter(),{getURL:()=>this.url,setWindowOpenHandler:fn=>this.open=fn});}setMenuBarVisibility(){}isDestroyed(){return this.dead}focus(){this.focused=true}async loadURL(url){this.url=url}destroy(){this.dead=true;this.emit('closed')}};
 await assert.rejects(m.action(p,'webrtc'));m.unlock();await m.action(p,'open',{id:'voice',url:'https://chatgpt.com/'});const voice=m.tabs.get('voice').view,handler=ses.check;
 await m.action(p,'webrtc');const window=m.diagnostics;assert.equal(window.url,'chrome://webrtc-internals/');assert.equal(m.active,'voice');assert.equal(voice.visible,true);assert.equal(ses.check,handler);assert.equal(window.config.webPreferences.session,ses);assert.equal(window.config.webPreferences.nodeIntegration,false);assert.equal(window.config.webPreferences.preload,undefined);assert.equal(window.open({url:'https://example.com'}).action,'deny');
 let blocked=false;window.webContents.emit('will-navigate',{preventDefault(){blocked=true}},'https://example.com');assert.equal(blocked,true);await assert.rejects(m.action({...p},'webrtc'));await m.action(p,'webrtc');assert.equal(created,1);assert.equal(window.focused,true);m.lock();assert.equal(window.dead,true);
});
test('WebRTC dumps may download only from the dedicated diagnostics window',async t=>{
 const {manager:m,parent:p,ses}=fixture(t);m.unlock();await m.action(p,'open',{id:'voice',url:'https://chatgpt.com/'});
 const contents={getURL:()=> 'chrome://webrtc-internals/'};m.diagnostics={isDestroyed:()=>false,webContents:contents,destroy(){}};
 const item=Object.assign(new EventEmitter(),{getFilename:()=> 'webrtc_internals_dump.txt',getTotalBytes:()=>20,getReceivedBytes:()=>20,getSavePath:()=>'/tmp/webrtc.txt'});
 ses.emit('will-download',{preventDefault(){throw Error('diagnostics export blocked')}},item,contents);assert.equal(m.status().downloads[0].name,'webrtc_internals_dump.txt');
 let denied=false;ses.emit('will-download',{preventDefault(){denied=true}},item,{getURL:()=> 'chrome://webrtc-internals/'});assert.equal(denied,true);
});

test('mobile preview reads the selected workspace tab with bounded pixels and private URL fields removed',async t=>{
 const {manager:m,parent:p,options}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com/page?secret=canary#hash'});
 const wc=m.tabs.get('one').view.webContents,raw=Buffer.from([255,216,255,1,2,3]);let captures=0;
 wc.capturePage=async(_rect,opts)=>{captures++;assert.equal(opts.stayHidden,true);return {isEmpty:()=>false,getSize:()=>({width:1920,height:1080}),resize:size=>{assert.deepEqual(size,{width:960,height:540});return {getSize:()=>size,toJPEG:quality=>{assert.equal(quality,60);return raw;}};}};};
 const before=wc.getURL(),result=await m.preview();assert.equal(result.ready,true);assert.equal(result.url,'https://example.com/page');assert.equal(result.width,960);assert.equal(result.height,540);assert.equal(result.frame_id,require('node:crypto').createHash('sha256').update(raw).digest('hex'));assert.equal(result.image_base64,raw.toString('base64'));assert.equal(wc.getURL(),before);assert.equal(captures,1);assert.equal(fs.existsSync(path.join(options.root,'screenshots')),false);
});

test('mobile preview refuses locked, closed, hidden and non-web surfaces without capture',async t=>{
 const {manager:m,parent:p}=fixture(t);assert.equal((await m.preview()).reason,'desktop_locked');m.unlock();assert.equal((await m.preview()).reason,'browser_closed');await m.action(p,'open',{id:'one',url:'https://example.com'});
 const wc=m.tabs.get('one').view.webContents;let captures=0;wc.capturePage=async()=>{captures++;throw Error('unexpected capture')};
 m.hide();assert.equal((await m.preview()).reason,'browser_hidden');await m.action(p,'select',{id:'one'});wc.url='chrome://webrtc-internals/';assert.equal((await m.preview()).reason,'preview_failed');m.lock();assert.equal((await m.preview()).reason,'desktop_locked');assert.equal(captures,0);
});

test('mobile preview discards pending pixels after lock, switching, closing or navigating the tab',async t=>{
 for(const mutation of ['lock','switch','close','navigate']){
  const {manager:m,parent:p}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com/one'});let resolve;const wc=m.tabs.get('one').view.webContents;wc.capturePage=()=>new Promise(done=>resolve=done);const pending=m.preview();
  if(mutation==='lock')m.lock();if(mutation==='switch')await m.action(p,'open',{id:'two',url:'https://example.org/two'});if(mutation==='close')await m.action(p,'close',{id:'one'});if(mutation==='navigate')wc.url='https://example.com/two';resolve({isEmpty:()=>{throw Error('stale pixels must not be encoded')}});
  const result=await pending;assert.equal(result.reason,mutation==='lock'?'desktop_locked':'browser_changed');assert.equal(result.image_base64,undefined);
 }
});

test('mobile preview rejects empty and oversized frames and capture failures',async t=>{
 const {manager:m,parent:p}=fixture(t);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com'});const wc=m.tabs.get('one').view.webContents;
 for(const image of [{isEmpty:()=>true},{isEmpty:()=>false,getSize:()=>({width:0,height:1})},{isEmpty:()=>false,getSize:()=>({width:640,height:480}),toJPEG:()=>Buffer.alloc(320001)}]){wc.capturePage=async()=>image;assert.deepEqual(await m.preview(),{reason:'preview_failed'});}
 wc.capturePage=async()=>{throw Error('capture failed')};assert.deepEqual(await m.preview(),{reason:'preview_failed'});
});

test('mobile routing prefers the current workspace browser and retains guarded legacy fallback',async()=>{
 const vm=require('node:vm'),source=fs.readFileSync(path.join(__dirname, '..','main.cjs'),'utf8'),calls=[];
 const c={runtime:{},workspaceBrowser:{active:'one',tabs:new Map([['one',{}]]),mobileState:()=>({tabs:[{id:'one',title:'Page',url:'https://example.com'}],active_tab_id:'one'}),preview:()=>{calls.push('workspace');return {ready:true};}},desktopBrowser:{attached:false,preview:()=>{calls.push('legacy');return {reason:'manual_control'};},agent:fields=>{calls.push('agent');return fields;}}};
 const parent={isDestroyed:()=>false,webContents:{getURL:()=> 'https://127.0.0.1'}};Object.assign(c,{window:parent,chatWindows:new Set([parent]),config:{origin:'https://127.0.0.1'},sameOrigin:(url,origin)=>new URL(url).origin===origin});c.workspaceBrowser.agent=async(_parent,fields)=>{calls.push('workspace-agent');return fields;};
 vm.createContext(c);vm.runInContext(source.slice(source.indexOf('      runtime.browserHandler = async fields =>'),source.indexOf("      runtime.on('owner-state'")),c);
 assert.equal((await c.runtime.browserHandler({action:'preview'})).ready,true);assert.deepEqual(calls,['workspace']);
 c.workspaceBrowser.active=null;assert.equal((await c.runtime.browserHandler({action:'preview'})).ready,true);c.desktopBrowser.attached=true;assert.equal((await c.runtime.browserHandler({action:'preview'})).reason,'manual_control');
 c.workspaceBrowser.tabs.clear();c.desktopBrowser.attached=false;await c.runtime.browserHandler({action:'preview'});assert.equal(calls.at(-1),'legacy');
 await c.runtime.browserHandler({action:'preview',extra:true});assert.equal(calls.at(-1),'workspace-agent');await c.runtime.browserHandler({action:'status'});assert.equal(calls.at(-1),'workspace-agent');c.desktopBrowser.attached=true;await c.runtime.browserHandler({action:'status'});assert.equal(calls.at(-1),'agent');
});

test('mobile tab metadata includes only open web tabs with bounded public URLs',async t=>{
 const {manager:m,parent:p}=fixture(t);assert.deepEqual(m.mobileState().tabs,[]);m.unlock();await m.action(p,'open',{id:'one',url:'https://example.com/a?secret=canary#hash'});await m.action(p,'open',{id:'two',url:'https://example.org/b'});
 const value=m.mobileState();assert.equal(value.active_tab_id,'two');assert.equal(value.tabs.length,2);assert.equal(value.tabs[0].url,'https://example.com/a');assert.equal(JSON.stringify(value).includes('canary'),false);
 await m.action(p,'close',{id:'one'});assert.equal(m.mobileState().tabs.length,1);m.lock();assert.deepEqual(m.mobileState(),{tabs:[],active_tab_id:''});
});

test('mobile selection changes the same desktop UI tab and refuses closed or locked targets',async()=>{
 const vm=require('node:vm'),source=fs.readFileSync(path.join(__dirname, '..','main.cjs'),'utf8');let selected='';
 const parent={isDestroyed:()=>false,webContents:{getURL:()=> 'https://127.0.0.1/',executeJavaScript:async code=>{assert.ok(code.includes('IgorekSelectBrowserTab'));selected='two';c.workspaceBrowser.active=selected;return true;}}};
 const c={runtime:{},workspaceBrowser:{active:'one',tabs:new Map([['one',{}],['two',{}]]),parent,sequence:1,blocked:false},desktopBrowser:{agent:()=>({error:'invalid_request'})},chatWindows:new Set([parent]),config:{origin:'https://127.0.0.1'},sameOrigin:(url,origin)=>new URL(url).origin===origin};
 vm.createContext(c);vm.runInContext(source.slice(source.indexOf('      runtime.browserHandler = async fields =>'),source.indexOf("      runtime.on('owner-state'")),c);
 assert.equal((await c.runtime.browserHandler({action:'select_preview_tab',tab_id:'two'})).selected,true);assert.equal(selected,'two');assert.equal(c.workspaceBrowser.active,'two');
 assert.equal((await c.runtime.browserHandler({action:'select_preview_tab',tab_id:'missing'})).reason,'browser_closed');c.workspaceBrowser.blocked=true;assert.equal((await c.runtime.browserHandler({action:'select_preview_tab',tab_id:'one'})).reason,'desktop_locked');
 c.workspaceBrowser.blocked=false;parent.webContents.executeJavaScript=async()=>{c.workspaceBrowser.blocked=true;return true;};assert.equal((await c.runtime.browserHandler({action:'select_preview_tab',tab_id:'one'})).reason,'desktop_locked');
});

test('agent sees current workspace tabs and can read by default without legacy handoff',async t=>{
 const {manager:m,parent:p}=fixture(t);m.unlock();await m.action(p,'open',{id:'chat',url:'https://chatgpt.com/'});
 const value=await m.agent(p,{action:'status'});assert.equal(value.open,true);assert.equal(value.manual,false);assert.equal(value.tabs[0].id,'chat');assert.equal(value.current_url,'https://chatgpt.com/');
 m.agentTools.read=async()=>({auth:'not_checked',text:'visible current tab'});assert.equal((await m.agent(p,{action:'read'})).text,'visible current tab');
 m.controlSettings({require_handoff:true});assert.equal((await m.agent(p,{action:'read'})).reason,'manual_control');
 await m.action(p,'control',{manual:false});assert.equal((await m.agent(p,{action:'read'})).text,'visible current tab');await m.action(p,'control',{manual:true});assert.equal((await m.agent(p,{action:'read'})).error,'browser_needs_owner');
 m.controlSettings({require_handoff:false});assert.equal((await m.agent(p,{action:'read'})).text,'visible current tab');m.lock();assert.equal((await m.agent(p,{action:'read'})).error,'desktop_locked');
});
test('agent navigation uses a visible workspace tab and preserves owner pages',async t=>{
 const {manager:m,parent:p}=fixture(t);m.unlock();await m.action(p,'open',{id:'owner',url:'https://chatgpt.com/'});const owned=m.tabs.get('owner').view;
 const layout=p.webContents.executeJavaScript;p.webContents.executeJavaScript=async script=>script.startsWith('window.IgorekShowAgentBrowser')?true:layout(script);
 m.agentTools.navigate=async function(url){await this.view.webContents.loadURL(url);return {url};};
 assert.equal((await m.agent(p,{action:'open',url:'https://example.com/'})).url,'https://example.com/');assert.equal(m.tabs.size,2);assert.equal(owned.webContents.getURL(),'https://chatgpt.com/');assert.ok(m.active.startsWith('agent-'));
 assert.equal((await m.agent(p,{action:'open',url:'https://127.0.0.1/'})).error,'invalid_url');assert.equal(m.tabs.size,2);
});

test('favorite actions require unlocked trusted window and retain bookmarks across manager recreation',async t=>{
 const {manager:m,parent:p,options,ses}=fixture(t);await assert.rejects(m.action(p,'addFavorite',{url:'example.org',title:'Docs'}));m.unlock();await m.action(p,'select',{id:null});
 await assert.rejects(m.action({...p},'addFavorite',{url:'example.org',title:'Docs'}));const state=await m.action(p,'addFavorite',{url:'example.org',title:'Docs'});assert.equal(state.favorites.length,1);assert.equal(m.tabs.size,0);assert.equal(ses.cleared,undefined);
 const id=state.favorites[0].id;m.lock();assert.equal(m.status().favorites,undefined);m.unlock();assert.equal(new WorkspaceBrowser(options).favorites.rows[0].id,id);await m.action(p,'removeFavorite',{id});assert.equal(m.status().favorites.length,0);
});

test('saving an image from a workspace tab uses the existing download history pipeline',async t=>{
 const {manager:m,parent:p,ses,options}=fixture(t);let items,requested='';options.menu={buildFromTemplate(value){items=value;return{popup(){}}}};
 m.unlock();await m.action(p,'open',{id:'one',url:'https://x.com/example/status/1/photo/1'});const wc=m.tabs.get('one').view.webContents;
 const file=path.join(options.root,'saved-image.png');fs.writeFileSync(file,'fixture');
 const item=Object.assign(new EventEmitter(),{getFilename:()=> 'saved-image.png',getTotalBytes:()=>7,getReceivedBytes:()=>7,getSavePath:()=>file});
 wc.downloadURL=url=>{requested=url;ses.emit('will-download',{preventDefault(){throw Error('trusted image source rejected')}},item,wc);};
 const src='https://pbs.twimg.com/media/fixture?format=png&name=large';wc.emit('context-menu',{}, {mediaType:'image',hasImageContents:true,srcURL:src});await new Promise(r=>setImmediate(r));
 const save=items?.find(value=>value.label==='Сохранить изображение как…');assert.ok(save);save.click();assert.equal(requested,src);
 item.emit('done',{},'completed');const row=m.status().downloads[0];assert.equal(row.name,'saved-image.png');assert.equal(row.state,'completed');assert.equal(row.can_reveal,true);assert.equal(row.path,undefined);
});
