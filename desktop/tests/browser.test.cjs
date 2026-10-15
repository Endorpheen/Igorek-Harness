const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { mkdtempSync, readFileSync, rmSync, statSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const {STATE}=require('../browser-reading.cjs');
const { DesktopBrowser, HEADER_HEIGHT, PAGE, settings, xUrl, pageUrl, publicAddress, resourceAllowed } = require('../browser.cjs');

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'igorek-twitter-test-'));
  const sessions = new Map(), windows = [], notifications = [];
  let clock = 0;
  class Contents extends EventEmitter {
    constructor(session) { super(); this.session = session; this.url = ''; this.dead = false; this.pages = [{auth:'ready',posts:[]}]; this.index = 0; this.scripts = [];this.history=[];this.at=-1;this.reloads=0;this.navigationHistory={canGoBack:()=>this.at>0,canGoForward:()=>this.at<this.history.length-1,goBack:()=>{this.url=this.history[--this.at];},goForward:()=>{this.url=this.history[++this.at];}}; }
    isDestroyed() { return this.dead; } close() { this.dead = true; this.emit('destroyed'); } stop() {}
    getUserAgent() { return 'Chrome/150 Electron/44 Игорёк/0.1'; } setUserAgent(value) { this.ua = value; }
    setWindowOpenHandler(fn) { this.popup = fn; }
    getURL() { return this.url; }
    getTitle(){return 'Test page';} isLoading(){return false;} focus(){} send(){} reload(){this.reloads++;}
    async loadURL(url) { this.url = url; this.index = 0;this.history=this.history.slice(0,this.at+1);this.history.push(url);this.at=this.history.length-1; }
    async loadFile() {}
    async executeJavaScript(script) { this.scripts.push(script); if(script===STATE)return {ready:true,loading:false,menus:0,posts:this.pages[this.index]?.posts?.length||0}; if (script === PAGE) return structuredClone(this.pages[Math.min(this.index, this.pages.length-1)]); this.index++; return true; }
    async executeJavaScriptInIsolatedWorld(){return [];}
  }
  const session = {fromPath:path => {
    if (!sessions.has(path)) sessions.set(path, { path, resolveHost:async()=>({endpoints:[{address:'104.244.42.1'}]}), cookies:{flushStore:async()=>{}}, flushStorageData(){}, on(){},
      setPermissionRequestHandler(fn){this.permission=fn;}, setPermissionCheckHandler(fn){this.check=fn;},
      webRequest:{onBeforeRequest(fn){this.filter=fn;}} });
    return sessions.get(path);
  }};
  class Window extends EventEmitter {
    constructor(options) { super(); this.options=options; this.webContents=new Contents({}); const children=[];this.contentView={children,addChildView:view=>{const index=children.indexOf(view);if(index>=0)children.splice(index,1);children.push(view);},removeChildView:view=>{const index=children.indexOf(view);if(index>=0)children.splice(index,1);}}; this.dead=false; windows.push(this); }
    getContentSize(){return [1180,920];} show(){this.visible=true;} hide(){this.visible=false;} focus(){} isDestroyed(){return this.dead;}
    loadFile(){return Promise.resolve();} close(){this.dead=true;this.emit('closed');}
  }
  class View {constructor(options){this.options=options;this.webContents=new Contents(options.webPreferences.session);} setBounds(value){this.bounds=value;}setBackgroundColor(value){this.background=value;}setVisible(value){this.visible=value;} }
  const options = {root,BrowserWindow:Window,WebContentsView:View,session,notify:r=>notifications.push(r),now:()=>clock,sleep:async ms=>{clock+=ms;}};
  const browser = new DesktopBrowser(options);
  return {root,browser,options,sessions,windows,notifications,advance:ms=>{clock+=ms;},cleanup:()=>rmSync(root,{recursive:true,force:true})};
}
const post = id => ({id:String(id),url:'https://x.com/ai/status/'+id,text:'Модель '+id,author:'AI',links:[],images:[],has_video:false});

test('Feed settings and post URLs reject secrets, foreign schemes and origins',()=>{
  assert.equal(xUrl('https://x.com/i/lists/123?foo=1'),'https://x.com/i/lists/123');
  assert.ok(xUrl('https://twitter.com/ai/status/12',true));
  for(const url of ['https://user:secret@x.com/home','https://x.com:444/home','https://x.com.evil/home','http://x.com/home','file:///tmp/a','javascript:1','https://127.0.0.1/home','https://x.com/settings'])assert.equal(xUrl(url),null);
  for(const url of ['http://x.com','https://localhost','https://192.168.1.1','file:///etc/passwd','https://router.local','https://secret@x.com','https://x.com:9999'])assert.equal(resourceAllowed(url),false);
  assert.ok(resourceAllowed('https://pbs.twimg.com/media/image.jpg'));
  assert.throws(()=>settings({feed_url:'https://x.com/home',max_posts:0,max_seconds:600}));
  assert.throws(()=>settings({feed_url:'https://x.com/home',max_posts:100,max_seconds:600,token:'secret'}));
});
test('Dedicated persistent Chromium is sandboxed, has no remote preload and reuses one view',async()=>{
  const f=fixture();try {
    await f.browser.owner('show');await f.browser.owner('show');
    assert.equal(f.windows.length,1);assert.equal(f.sessions.size,1);
    assert.equal(f.browser.view.options.webPreferences.preload,undefined);
    assert.equal(f.browser.view.options.webPreferences.nodeIntegration,false);
    assert.equal(f.browser.view.options.webPreferences.contextIsolation,true);
    assert.equal(f.browser.view.options.webPreferences.sandbox,true);
    assert.equal(f.browser.view.webContents.popup().action,'deny');
    assert.ok(/^[\x20-\x7E]+$/.test(f.browser.view.webContents.ua));assert.ok(!f.browser.view.webContents.ua.includes('Electron'));
    const ses=[...f.sessions.values()][0];assert.equal(ses.path,join(f.root,'profile'));
    assert.equal(statSync(f.root).mode&0o777,0o700);
    let blocked=false;await ses.webRequest.filter({url:'https://127.0.0.1:5177'},value=>blocked=value.cancel);assert.ok(blocked);
    ses.resolveHost=async()=>({endpoints:[{address:'127.0.0.1'}]});
    await ses.webRequest.filter({url:'https://rebind.example.com'},value=>blocked=value.cancel);assert.ok(blocked);
    const wc=f.browser.view.webContents;await f.browser.owner('close');assert.ok(wc.isDestroyed());
    await f.browser.owner('show');assert.equal(f.sessions.size,1);assert.equal(f.windows.length,2);
  } finally {f.cleanup();}
});
test('Manual login owns the browser until explicit handoff; lock hides and stops tools',async()=>{
  const f=fixture();try {
    assert.equal((await f.browser.agent({action:'open'})).error,'browser_needs_owner');
    await f.browser.owner('show');await f.browser.owner('resume');
    assert.equal((await f.browser.agent({action:'read'})).auth,'ready');
    f.browser.lock();assert.equal((await f.browser.agent({action:'scroll'})).error,'desktop_locked');assert.equal(f.browser.window.visible,false);
    f.browser.unlock();assert.equal((await f.browser.agent({action:'scroll'})).auth,'ready');
    await f.browser.owner('pause');assert.equal((await f.browser.agent({action:'open'})).error,'browser_needs_owner');
    assert.equal((await f.browser.agent({action:'eval',text:'document.cookie'})).error,'invalid_request');
    assert.equal((await f.browser.agent({action:'read',url:'file:///etc/passwd'})).error,'invalid_request');
  }finally{f.cleanup();}
});
test('Expired login and Twitter challenge pause structurally without exposing form content',async()=>{
  const f=fixture();try {
    await f.browser.owner('show');await f.browser.owner('resume');
    for(const auth of ['login_required','challenge']){
      f.browser.view.webContents.pages=[{auth,posts:[post(99)]}];
      const value=await f.browser.agent({action:'read'});assert.deepEqual(value.posts,[]);assert.ok(value.paused);
      assert.equal((await f.browser.agent({action:'scroll'})).error,'browser_needs_owner');
      assert.equal((await f.browser.owner('resume')).error,'login_not_verified');
      f.browser.view.webContents.pages=[{auth:'ready',posts:[]}];await f.browser.owner('resume');
    }
    assert.deepEqual(f.notifications,['login_required','challenge']);
    assert.equal(Object.keys(f.browser.records).length,0);
  }finally{f.cleanup();}
});
test('Pinned and unordered posts deduplicate by ID, preserve processing status and survive reopen',async()=>{
  const f=fixture();try{
    await f.browser.owner('configure',{feed_url:'https://x.com/i/lists/123',max_posts:3,max_seconds:600});
    await f.browser.owner('show');
    f.browser.view.webContents.pages=[{auth:'ready',posts:[post(900),post(1)]},{auth:'ready',posts:[post(900),post(2)]}];
    await f.browser.owner('resume');f.browser.records['900'].status='proposed';
    const queued=await f.browser.agent({action:'collect'});assert.ok(queued.job.id);
    await f.browser.queue;
    const result=await f.browser.agent({action:'result'});assert.equal(result.state,'finished');assert.equal(result.posts.length,3);
    assert.deepEqual(result.posts.map(p=>p.id),['900','1','2']);assert.equal(result.reason,'post_limit');
    assert.equal(f.browser.records['900'].status,'proposed');
    const reopened=new DesktopBrowser(f.options);assert.equal(reopened.config.feed_url,'https://x.com/i/lists/123');
    assert.equal(reopened.records['2'].status,'discovered');assert.equal(reopened.manual,false);assert.equal(reopened.last.count,3);
    const saved=JSON.parse(readFileSync(join(f.root,'observed.json')));assert.equal(saved.posts['1'].text,undefined);
    assert.equal(statSync(join(f.root,'observed.json')).mode&0o777,0o600);
  }finally{f.cleanup();}
});
test('Browser supports ordinary public sites; local/private network addresses stay blocked',async()=>{
  assert.ok(pageUrl('https://huggingface.co/models'));assert.ok(pageUrl('https://openai.com/research'));
  for(const ip of ['127.0.0.1','10.1.2.3','172.16.0.1','169.254.169.254','100.64.0.1','::1','fd00::1','2001:db8::1'])assert.equal(publicAddress(ip),false);
  assert.equal(publicAddress('8.8.8.8'),true);
  const f=fixture();try{
    await f.browser.owner('show');await f.browser.owner('resume');
    f.browser.view.webContents.executeJavaScript=async()=>({title:'Research',text:'New model weights',links:[{text:'Models',url:'https://huggingface.co/models'}],login:false});
    const opened=await f.browser.agent({action:'open',url:'https://huggingface.co/models'});
    assert.equal(opened.auth,'not_checked');assert.equal(opened.title,'Research');assert.equal(opened.text,'New model weights');
    assert.equal((await f.browser.agent({action:'open',url:'https://127.0.0.1:5177'})).error,'invalid_url');
  }finally{f.cleanup();}
});
test('Collect stops at time limit and never claims full coverage',async()=>{
  const f=fixture();try{
    await f.browser.owner('configure',{feed_url:'https://x.com/home',max_posts:100,max_seconds:10});
    await f.browser.owner('show');await f.browser.owner('resume');
    f.browser.view.webContents.pages=Array.from({length:100},(_,i)=>({auth:'ready',posts:[post(i+1)]}));
    await f.browser.agent({action:'collect'});await f.browser.queue;
    const result=await f.browser.agent({action:'result'});assert.equal(result.reason,'time_limit');assert.ok(result.posts.length<100);assert.equal(result.coverage,'bounded_view_only');
  }finally{f.cleanup();}
});
test('Concurrent commands serialize, repeated collect attaches to same run and owner can interrupt',async()=>{
  const f=fixture();try{
    await f.browser.owner('show');await f.browser.owner('resume');
    const a=await f.browser.agent({action:'collect'}),b=await f.browser.agent({action:'collect'});
    assert.equal(a.job.id,b.job.id);
    await f.browser.owner('pause');await f.browser.queue;
    assert.equal(f.browser.job.state,'finished');assert.ok(['interrupted','browser_needs_owner'].includes(f.browser.job.reason));
    assert.equal(f.browser.queued,0);
  }finally{f.cleanup();}
});
test('Unknown DOM cannot be accepted as login and foreign post URL never navigates',async()=>{
  const f=fixture();try{
    await f.browser.owner('show');f.browser.view.webContents.pages=[{auth:'unknown',posts:[post(1)]}];
    assert.equal((await f.browser.owner('resume')).error,'login_not_verified');
    f.browser.view.webContents.pages=[{auth:'ready',posts:[]}];await f.browser.owner('resume');
    assert.equal((await f.browser.agent({action:'post',url:'https://evil.test/a/status/1'})).error,'invalid_url');
    assert.equal(f.browser.view.webContents.getURL(),'https://x.com/home');
  }finally{f.cleanup();}
});
test('Browser attaches beside chat without a new window, hides and reattaches the same live profile',async()=>{
  const f=fixture();try{
    const parent=new f.options.BrowserWindow({});
    const browser=new DesktopBrowser({...f.options,parent:()=>parent});
    await browser.owner('show');assert.equal(f.windows.length,1);assert.equal(parent.contentView.children.length,2);
    assert.equal(browser.controlContents(),browser.controls.webContents);assert.equal(browser.view.bounds.y,36+HEADER_HEIGHT);
    assert.equal(browser.controls.options.webPreferences.preload,join(__dirname, '..','browser-preload.cjs'));
    const wc=browser.view.webContents;await browser.owner('resume');await browser.owner('hide');
    assert.equal(parent.contentView.children.length,0);assert.equal(wc.isDestroyed(),false);
    await browser.owner('show');assert.equal(browser.view.webContents,wc);assert.equal(parent.contentView.children.length,2);
    browser.lock();assert.equal(parent.contentView.children.length,0);assert.equal(parent.dead,false);
    await browser.shutdown();assert.equal(wc.isDestroyed(),true);assert.equal(parent.dead,false);
  }finally{f.cleanup();}
});

test('Toolbar renderer recovery preserves page, tabs and ownership with bounded retries',async()=>{
 const f=fixture();try{
  const parent=new f.options.BrowserWindow({}),browser=new DesktopBrowser({...f.options,parent:()=>parent});
  await browser.owner('show');await browser.owner('resume');
  const page=browser.view,controls=browser.controls.webContents;let reloads=0,repaints=0;
  controls.loadFile=async()=>{reloads++;};controls.invalidate=()=>{repaints++;};
  assert.equal(browser.controls.options.webPreferences.backgroundThrottling,false);
  parent.emit('focus');assert.ok(repaints>0);assert.equal(parent.contentView.children.at(-1),browser.controls);
  for(let attempt=0;attempt<4;attempt++){
   controls.emit('render-process-gone',{}, {reason:'crashed'});await new Promise(setImmediate);
  }
  assert.equal(reloads,3);assert.equal(browser.view,page);assert.equal(browser.tabs.length,1);
  assert.equal(page.webContents.isDestroyed(),false);assert.equal(browser.manual,false);
  await browser.shutdown();assert.equal(parent.listenerCount('focus'),0);assert.equal(parent.listenerCount('restore'),0);
 }finally{f.cleanup();}
});

test('Modal settings cover the browser without closing or reloading its tabs',async()=>{
 const f=fixture();try{
  let overlay=true;
  const parent=new f.options.BrowserWindow({}),browser=new DesktopBrowser({...f.options,parent:()=>parent,overlayActive:()=>overlay});
  await browser.owner('show');const first=browser.view;
  assert.equal(first.visible,false);assert.equal(browser.controls.visible,false);
  browser.newTab();assert.equal(browser.view.visible,false);assert.equal(browser.tabs.length,2);
  overlay=false;browser.layout();assert.equal(browser.view.visible,true);assert.equal(browser.controls.visible,true);
  overlay=true;parent.emit('focus');assert.equal(browser.view.visible,false);
  overlay=false;browser.switchTab(browser.tabs[0].id);assert.equal(browser.view,first);assert.equal(first.visible,true);
  assert.equal(first.webContents.isDestroyed(),false);await browser.shutdown();
 }finally{f.cleanup();}
});

test('Reopening the dock recreates only toolbar contents and preserves the site',async()=>{
 const f=fixture();try{
  const parent=new f.options.BrowserWindow({}),browser=new DesktopBrowser({...f.options,parent:()=>parent});
  await browser.owner('show');await browser.owner('resume');const page=browser.view,first=browser.controls;
  await browser.owner('hide');await browser.owner('show');
  assert.notEqual(browser.controls,first);assert.equal(first.webContents.isDestroyed(),true);
  assert.equal(browser.view,page);assert.equal(page.webContents.isDestroyed(),false);assert.equal(browser.manual,false);
  assert.equal(parent.contentView.children.at(-1),browser.controls);await browser.shutdown();
 }finally{f.cleanup();}
});

test('Committed readable page survives an aborted load without claiming old pages succeeded',async()=>{
 const f=fixture();try{
  await f.browser.owner('show');await f.browser.owner('resume');const wc=f.browser.view.webContents;
  f.browser.reading.wait=async()=>({ready:true});f.browser.interaction.snapshot=async()=>[];
  wc.executeJavaScript=async script=>script.includes('navigation_ready')?{navigation_ready:true,url:wc.url}:{title:'Loaded page',text:'Visible page content',links:[]};
  wc.loadURL=async url=>{wc.url=url;wc.emit('did-navigate',{},url);throw Object.assign(new Error('private URL must not be logged'),{code:'ERR_ABORTED'});};
  const opened=await f.browser.agent({action:'open',url:'https://www.youtube.com/'});
  assert.equal(opened.error,undefined);assert.equal(opened.navigation.recovered,true);assert.equal(opened.navigation.warning_code,'ERR_ABORTED');
  assert.equal(opened.text,'Visible page content');assert.equal(f.browser.paused,'');
  wc.loadURL=async()=>{throw Object.assign(new Error('unavailable'),{code:'ERR_NAME_NOT_RESOLVED'});};
  const failed=await f.browser.agent({action:'open',url:'https://www.google.com/'});
  assert.equal(failed.error,'navigation_failed');assert.equal(failed.navigation.committed,false);assert.equal(failed.navigation.recovered,false);
  assert.equal(f.browser.paused,'');assert.equal((await f.browser.agent({action:'read'})).text,'Visible page content');
 }finally{f.cleanup();}
});

test('Navigation can read DOM-ready content without waiting for every resource',async()=>{
 const f=fixture();try{
  await f.browser.owner('show');await f.browser.owner('resume');const wc=f.browser.view.webContents;
  f.browser.reading.wait=async()=>({ready:true});f.browser.interaction.snapshot=async()=>[];
  wc.executeJavaScript=async()=>({title:'Ready',text:'Main document is available',links:[]});
  wc.loadURL=url=>{wc.url=url;queueMicrotask(()=>{wc.emit('did-navigate',{},url);wc.emit('dom-ready');});return new Promise(()=>{});};
  const opened=await f.browser.agent({action:'open',url:'https://www.youtube.com/'});
  assert.equal(opened.text,'Main document is available');assert.equal(opened.navigation.document_ready,true);
  assert.equal(wc.listenerCount('dom-ready'),0);assert.equal(wc.listenerCount('did-navigate'),0);
 }finally{f.cleanup();}
});

test('Native browser diagnostics exclude URL secrets and stay owner-only',()=>{
 const f=fixture();try{
  f.browser.diagnose('navigation_failed',{requested_origin:'https://accounts.google.com/callback?code=private-query',current_origin:'https://user:private-password@example.com/path#private-fragment',error_code:'Bearer private-token',headers:{authorization:'private-header'}});
  const file=join(f.root,'browser-events.jsonl'),value=readFileSync(file,'utf8');
  for(const secret of ['private-query','private-password','private-fragment','private-token','private-header'])assert.ok(!value.includes(secret));
  assert.equal(statSync(file).mode&0o777,0o600);assert.equal(JSON.parse(value).current_origin,'https://example.com');
 }finally{f.cleanup();}
});
test('Chrome navigation reports actual URL/history and preserves the configured pass limits',async()=>{
  const f=fixture();try{
    await f.browser.owner('show');await f.browser.owner('resume');
    let state=await f.browser.owner('navigate',{url:'https://www.google.com/'});
    assert.equal(state.current_url,'https://www.google.com/');assert.ok(state.can_go_back&&state.manual);assert.equal(state.settings.max_posts,100);
    state=await f.browser.owner('back');assert.equal(state.current_url,'https://x.com/home');assert.ok(state.can_go_forward);
    state=await f.browser.owner('forward');assert.equal(state.current_url,'https://www.google.com/');
    await f.browser.owner('reload');assert.equal(f.browser.view.webContents.reloads,1);
    assert.equal((await f.browser.owner('navigate',{url:'https://127.0.0.1/'})).error,'invalid_url');
    assert.equal(f.browser.status().current_url,'https://www.google.com/');
  }finally{f.cleanup();}
});
test('Hidden limits save without navigation, resetting login or changing control mode',async()=>{
  const f=fixture();try{
    await f.browser.owner('show');await f.browser.owner('resume');const wc=f.browser.view.webContents;
    const state=await f.browser.owner('limits',{max_seconds:300,max_posts:50});
    assert.equal(state.settings.max_seconds,300);assert.equal(state.settings.max_posts,50);assert.equal(state.manual,false);assert.equal(wc.history.length,1);
    assert.equal((await f.browser.owner('limits',{max_seconds:0,max_posts:50})).error,'invalid_settings');
    assert.equal((await f.browser.owner('limits',{max_seconds:300,max_posts:50,token:'extra'})).error,'invalid_settings');
    assert.equal(new DesktopBrowser(f.options).config.max_posts,50);
  }finally{f.cleanup();}
});
test('Overflow menu floats above the page without moving it and collapses on detach/lock',async()=>{
  const f=fixture();try{
    const parent=new f.options.BrowserWindow({});const browser=new DesktopBrowser({...f.options,parent:()=>parent});await browser.owner('show');
    const page={...browser.view.bounds};assert.equal(browser.controls.bounds.height,HEADER_HEIGHT);
    await browser.owner('menu',{open:true});assert.ok(browser.controls.bounds.height>HEADER_HEIGHT);assert.deepEqual(browser.view.bounds,page);
    assert.equal(parent.contentView.children.at(-1),browser.controls);assert.equal(browser.controls.background,'#00000000');
    await browser.owner('menu',{open:false});assert.equal(browser.controls.bounds.height,HEADER_HEIGHT);
    await browser.owner('menu',{open:true});browser.lock();assert.equal(browser.menuOpen,false);
    assert.equal((await browser.owner('menu',{open:true})).error,'desktop_locked');await browser.shutdown();
  }finally{f.cleanup();}
});
test('Large feed replies remain bounded while every discovered ID is saved',async()=>{
  const f=fixture();try{
    await f.browser.owner('configure',{feed_url:'https://x.com/home',max_posts:1000,max_seconds:600});
    f.browser.view.webContents.pages=[{auth:'ready',posts:Array.from({length:1000},(_,i)=>({...post(i+1),text:'Новые веса модели '.repeat(1000)}))}];
    await f.browser.owner('resume');const value=await f.browser.agent({action:'read'});
    assert.equal(value.posts.length+value.posts_omitted,1000);assert.equal(Object.keys(f.browser.records).length,1000);
    assert.ok(Buffer.byteLength(JSON.stringify(value),'utf8')<2000000);assert.ok(value.posts[0].truncated);
  }finally{f.cleanup();}
});

test('Manual tabs share a persistent session and popup returns to its opener',async()=>{
 const f=fixture();try {
  await f.browser.owner('show');const original=f.browser.view,first=f.browser.status().tabs[0];
  const response=original.webContents.popup({url:'https://accounts.google.com/o/oauth2/auth',disposition:'new-window'});
  assert.equal(response.action,'allow');assert.equal(response.overrideBrowserWindowOptions.webPreferences.nodeIntegration,false);
  const child=response.createWindow({webPreferences:{}});assert.equal(child.session,original.webContents.session);
  assert.equal(f.browser.status().tabs.length,2);assert.equal(f.browser.view.webContents,child);
  child.close();assert.equal(f.browser.view,original);assert.equal(f.browser.status().tabs.length,1);
  await f.browser.owner('tab_new');assert.equal(f.browser.status().tabs.length,2);assert.equal(f.browser.view.webContents.session,original.webContents.session);
  await f.browser.owner('tab_select',{id:first.id});assert.equal(f.browser.view,original);
  assert.equal((await f.browser.owner('tab_select',{id:'other'})).error,'invalid_request');
  for(const url of ['file:///etc/passwd','javascript:1','https://127.0.0.1/'])assert.equal(original.webContents.popup({url}).action,'deny');
  await f.browser.owner('resume');assert.equal(original.webContents.popup({url:'https://accounts.google.com/'}).action,'deny');
  await f.browser.owner('close');assert.ok(child.isDestroyed());
 }finally{f.cleanup();}
});

test('Public pages with optional login remain readable and permit explicit handoff',async()=>{
 const f=fixture();try{
  await f.browser.owner('show');const wc=f.browser.view.webContents;
  wc.url='https://www.coingecko.com/en/coins/solana';
  wc.executeJavaScript=async()=>({title:'Public market page',text:'Public price information',login:true,links:[]});
  f.browser.interaction.snapshot=async()=>[];
  f.browser.paused='login_required';
  const handed=await f.browser.owner('resume');
  assert.equal(handed.manual,false);assert.equal(handed.paused,'');
  const page=await f.browser.agent({action:'read'});
  assert.equal(page.text,'Public price information');assert.equal(f.browser.manual,false);assert.equal(f.browser.paused,'');
  assert.deepEqual(f.notifications,[]);
 }finally{f.cleanup();}
});

test('Blank new tabs accept handoff before navigation and return an empty page',async()=>{
 const f=fixture();try{
  await f.browser.owner('show');await f.browser.owner('tab_new');
  for(const url of ['','about:blank']){
   f.browser.view.webContents.url=url;f.browser.manual=true;f.browser.paused='login_required';
   const handed=await f.browser.owner('resume');assert.equal(handed.manual,false);assert.equal(handed.paused,'');
   const page=await f.browser.agent({action:'read'});assert.equal(page.url,'about:blank');assert.equal(page.text,'');
  }
 }finally{f.cleanup();}
});

test('Agent browser status omits authentication URL query data',async()=>{
 const f=fixture();try{
  await f.browser.owner('show');f.browser.view.webContents.url='https://accounts.google.com/callback?code=fixture-secret&state=fixture-secret';
  const value=await f.browser.agent({action:'status'});assert.ok(!JSON.stringify(value).includes('fixture-secret'));
 }finally{f.cleanup();}
});

test('Dock width is clamped, saved and restored without changing the browser profile',async()=>{
 const f=fixture();try{
  const parent=new f.options.BrowserWindow({webPreferences:{}}),browser=new DesktopBrowser({...f.options,parent:()=>parent});
  await browser.owner('show');const session=browser.view.webContents.session;
  browser.resizeDock('start',700);browser.resizeDock('move',200);const large=browser.resizeDock('end',200);
  assert.equal(large.width,580);assert.equal(browser.view.bounds.width,574);
  browser.resizeDock('start',200);browser.resizeDock('move',1500);browser.resizeDock('end',1500);
  assert.equal(browser.status().dock_width,320);assert.equal(browser.view.webContents.session,session);
  assert.equal(JSON.parse(readFileSync(join(f.root,'layout.json'))).width,320);
  const restored=new DesktopBrowser({...f.options,parent:()=>parent});assert.equal(restored.dockWidth,320);
  browser.lock();assert.equal(browser.resizeDock('grow').error,'browser_closed');browser.unlock();await browser.owner('close');
 }finally{f.cleanup();}
});

test('Mobile preview captures only agent browser pixels, strips query and never writes screenshot files',async()=>{
 const f=fixture();try{
  await f.browser.owner('show');let captures=0;
  const raw=Buffer.from([0xff,0xd8,0xff,1,2,3]);
  const image={isEmpty:()=>false,getSize:()=>({width:640,height:480}),toJPEG:quality=>{assert.equal(quality,60);return raw;}};
  f.browser.view.webContents.capturePage=async()=>{captures++;return image;};
  assert.equal((await f.browser.preview()).reason,'manual_control');assert.equal(captures,0);
  await f.browser.owner('resume');f.browser.view.webContents.url='https://example.com/page?secret=canary#hash';
  const before=f.browser.view.webContents.url;
  const result=await f.browser.preview();assert.equal(result.ready,true);assert.equal(result.url,'https://example.com/page');
  assert.equal(result.image_base64,raw.toString('base64'));assert.equal(result.frame_id.length,64);assert.equal(captures,1);
  assert.equal(f.browser.view.webContents.url,before);assert.ok(!require('node:fs').existsSync(join(f.root,'screenshots')));
  f.browser.lock();assert.equal((await f.browser.preview()).reason,'desktop_locked');assert.equal(captures,1);
 }finally{f.cleanup();}
});

test('Manual handoff during a mobile capture discards pixels',async()=>{
 const f=fixture();try{
  await f.browser.owner('show');await f.browser.owner('resume');let resolve;
  f.browser.view.webContents.capturePage=()=>new Promise(done=>{resolve=done;});
  const pending=f.browser.preview();f.browser.manual=true;resolve({isEmpty:()=>false});
  assert.deepEqual(await pending,{reason:'manual_control'});
 }finally{f.cleanup();}
});
