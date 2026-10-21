const { mkdirSync, chmodSync, readFileSync, writeFileSync, renameSync, appendFileSync, statSync } = require('node:fs');
const { join } = require('node:path');
const { isIP } = require('node:net');
const { BrowserInteraction,validInteraction,compactPage } = require('./browser-interaction.cjs');

const {BrowserReading,validReading} = require('./browser-reading.cjs');
const DEFAULTS = Object.freeze({ feed_url: 'https://x.com/home', max_seconds: 600, max_posts: 100 });
const HEADER_HEIGHT=80;
const X_HOSTS = new Set(['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com']);

function xUrl(raw, post = false) {
  try {
    const url = new URL(raw);
    if (url.protocol !== 'https:' || !X_HOSTS.has(url.hostname) || url.username || url.password || url.port) return null;
    if (post && !/^\/[A-Za-z0-9_]+\/status\/\d+$/.test(url.pathname)) return null;
    if (!post && !['/home', '/i/lists', '/i/flow/login'].some(path => url.pathname === path || (path === '/i/lists' && /^\/i\/lists\/\d+$/.test(url.pathname)))) return null;
    url.search = ''; url.hash = '';
    return url.href;
  } catch { return null; }
}
function resourceAllowed(raw) {
  try {
    const url = new URL(raw);
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    return ['https:', 'wss:'].includes(url.protocol) && !url.port && !url.username && !url.password
      && host.includes('.') && !['localhost', 'local', 'internal', 'lan', 'home', 'test', 'invalid'].some(suffix => host === suffix || host.endsWith('.' + suffix))
      && (!isIP(host) || publicAddress(host));
  } catch { return false; }
}
function publicAddress(raw) {
  if (isIP(raw) === 4) {
    const [a,b] = raw.split('.').map(Number);
    return !(a === 0 || a === 10 || a === 127 || a >= 224 || (a === 100 && b >= 64 && b <= 127)
      || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && [0,168].includes(b))
      || (a === 198 && [18,19,51].includes(b)) || (a === 203 && b === 0));
  }
  return isIP(raw) === 6 && /^[23][0-9a-f]{3}:/i.test(raw) && !/^2001:(?:db8|0:)/i.test(raw);
}
function pageUrl(raw) {
  try { const url = new URL(raw); if (url.protocol !== 'https:' || !resourceAllowed(raw)) return null; url.hash = ''; return url.href; } catch { return null; }
}
function settings(value) {
  if (!value || Object.keys(value).sort().join(',') !== 'feed_url,max_posts,max_seconds'
      || typeof value.feed_url !== 'string' || value.feed_url.length > 2048 || !pageUrl(value.feed_url)
      || !Number.isInteger(value.max_seconds) || value.max_seconds < 10 || value.max_seconds > 3600
      || !Number.isInteger(value.max_posts) || value.max_posts < 1 || value.max_posts > 1000) throw new Error('invalid_settings');
  return { ...value, feed_url: pageUrl(value.feed_url) };
}
const PAGE = `(() => {
  const path = location.pathname;
  const challenge = /^\\/(account\\/(access|locked)|i\\/flow\\/(challenge|consent))/.test(path)
    || !!document.querySelector('iframe[src*="arkoselabs"],iframe[src*="funcaptcha"],iframe[src*="hcaptcha"],[data-testid="ocfEnterTextTextInput"]');
  const login = /^\\/(login|i\\/flow\\/(login|signup))/.test(path)
    || !!document.querySelector('input[autocomplete="current-password"],input[autocomplete="username"],[data-testid="loginButton"]');
  const ready = !!document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"],a[data-testid="AppTabBar_Profile_Link"]');
  const auth = challenge ? 'challenge' : login ? 'login_required' : ready ? 'ready' : 'unknown';
  const posts = auth !== 'ready' ? [] : Array.from(document.querySelectorAll('article[data-testid="tweet"]')).slice(0,1000).flatMap(article => {
    const link = Array.from(article.querySelectorAll('a[href]')).find(a => a.querySelector('time') && /^\\/[^/]+\\/status\\/\\d+$/.test(new URL(a.href).pathname));
    if (!link) return [];
    const url = new URL(link.href); url.search=''; url.hash='';
    const state=(off,on)=>{const a=article.querySelector('[data-testid="'+off+'"]'),b=article.querySelector('[data-testid="'+on+'"]');return !!a===!!b?null:!!b;};
    const text=article.querySelector('[data-testid="tweetText"]')?.textContent||'';
    return [{id:url.pathname.split('/').pop(),url:url.href,
      text:text.slice(0,200000),truncated:text.length>200000||!!article.querySelector('[data-testid="tweet-text-show-more-link"]'),reactions:{like:state('like','unlike'),repost:state('retweet','unretweet'),bookmark:state('bookmark','removeBookmark')},
      relation:article.closest('[data-testid="placementTracking"]')?'recommendation':article.getAttribute('data-reply-to')?'reply':'unknown',reply_to:article.getAttribute('data-reply-to')||null,
      author:(article.querySelector('[data-testid="User-Name"]')?.textContent || '').slice(0,500),
      posted_at:article.querySelector('time')?.dateTime || '',
      social_context:(article.querySelector('[data-testid="socialContext"]')?.textContent || '').slice(0,500),
      links:Array.from(article.querySelectorAll('[data-testid="tweetText"] a[href]')).map(a=>a.href).slice(0,30),
      images:Array.from(article.querySelectorAll('[data-testid="tweetPhoto"] img')).map(i=>i.currentSrc || i.src).slice(0,10),
      has_video:!!article.querySelector('video,[data-testid="videoPlayer"]')}];
  });
  return {auth,posts};
})()`;

class DesktopBrowser {
  constructor({ root, BrowserWindow, WebContentsView, session, parent = () => null, overlayActive = () => false, notify = () => {}, now = Date.now, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
    this.root = root; this.Window = BrowserWindow; this.View = WebContentsView; this.sessions = session;
    this.notify = notify; this.now = now; this.sleep = sleep; this.parent = parent;
    this.overlayActive = overlayActive;
    mkdirSync(root, { recursive: true, mode: 0o700 }); chmodSync(root, 0o700);
    this.config = { ...DEFAULTS }; this.records = {}; this.last = null;
    try { this.config = settings(JSON.parse(readFileSync(join(root, 'settings.json'), 'utf8'))); } catch {}
    try { const saved = JSON.parse(readFileSync(join(root, 'observed.json'), 'utf8')); this.records = saved.posts || {}; this.last = saved.last || null; } catch {}
    this.tabs = []; this.window = null; this.view = null; this.controls = null; this.attached = false; this.ownsWindow = true;
    this.manual = true; this.paused = ''; this.auth = 'unknown';
    this.menuOpen=false;this.dockWidth=null;this.dockPixelWidth=0;this.dockResize=null;
    try{const value=JSON.parse(readFileSync(join(root,'layout.json'),'utf8'));if(Number.isInteger(value.width)&&value.width>=320&&value.width<=10000)this.dockWidth=value.width;}catch{}
    try { this.manual = JSON.parse(readFileSync(join(root, 'control.json'), 'utf8')).manual !== false; } catch {}
    this.queue = Promise.resolve(); this.queued = 0; this.job = null; this.stopping = false; this.blocked = false;
    this.interaction=new BrowserInteraction(()=>this.view.webContents,pageUrl,this.sleep);this.reading=new BrowserReading(this);
  }
  save(name, value) {
    const target = join(this.root, name), temporary = target + '.tmp';
    writeFileSync(temporary, JSON.stringify(value), { mode: 0o600 }); renameSync(temporary, target);
  }
  diagnose(event,fields={}) {
    const record={ts_ms:Date.now(),event};
    for(const key of ['requested_origin','current_origin']){
      try{const value=new URL(fields[key]);if(value.protocol==='https:')record[key]=value.origin;}catch{}
    }
    for(const key of ['committed','document_ready','recovered'])if(typeof fields[key]==='boolean')record[key]=fields[key];
    for(const key of ['net_error','exit_code'])if(Number.isInteger(fields[key]))record[key]=fields[key];
    if(['loading','read','document_probe','complete','failed'].includes(fields.phase))record.phase=fields.phase;
    if(['clean-exit','abnormal-exit','killed','crashed','oom','launch-failed','integrity-failure','memory-eviction'].includes(fields.reason))record.reason=fields.reason;
    const code=fields.error_code||fields.warning_code;
    if(typeof code==='string'&&(/^(ERR_[A-Z_]{1,50})$/.test(code)||['NAVIGATION_TIMEOUT','PAGE_TIMEOUT','TIME_LIMIT','DOCUMENT_UNAVAILABLE'].includes(code)))record.code=code;
    try{
      const target=join(this.root,'browser-events.jsonl');
      try{if(statSync(target).size>524288)renameSync(target,join(this.root,'browser-events.previous.jsonl'));}catch{}
      appendFileSync(target,JSON.stringify(record)+'\n',{mode:0o600});
    }catch{}
  }
  status() {
    const wc=this.view?.webContents,open=Boolean(wc&&!wc.isDestroyed());
    return { dock_width:this.dockPixelWidth, settings: this.config, open, manual: this.manual,
      current_url:open?wc.getURL():'',page_title:open?wc.getTitle():'',loading:open&&wc.isLoading(),
      can_go_back:open&&wc.navigationHistory.canGoBack(),can_go_forward:open&&wc.navigationHistory.canGoForward(),
      paused: this.paused, auth: this.auth, queued: this.queued, last_run: this.last, observed: Object.keys(this.records).length,
      job: this.job ? { id: this.job.id, state: this.job.state, count: this.job.posts.length, reason: this.job.reason } : null,
      tabs:this.tabs.filter(tab=>!tab.view.webContents.isDestroyed()).map(tab=>({id:tab.id,title:tab.view.webContents.getTitle().slice(0,160)||'Новая вкладка',url:tab.view.webContents.getURL(),active:tab.view===this.view})),
      profile_path: join(this.root, 'profile') };
  }
  async preview() {
    if(this.blocked)return {reason:'desktop_locked'};
    const wc=this.view?.webContents;
    if(!wc||wc.isDestroyed())return {reason:'browser_closed'};
    if(this.manual)return {reason:'manual_control'};
    if(this.paused)return {reason:'browser_paused'};
    if(!this.attached)return {reason:'browser_hidden'};
    const before=wc.getURL();
    let timer;
    try{
      const image=await Promise.race([wc.capturePage(undefined,{stayHidden:true,stayAwake:false}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('preview_timeout')),3000);})]);
      if(this.blocked)return {reason:'desktop_locked'};
      if(this.manual)return {reason:'manual_control'};
      if(this.paused)return {reason:'browser_paused'};
      if(!this.attached)return {reason:'browser_hidden'};
      if(this.view?.webContents!==wc||wc.isDestroyed())return {reason:'browser_changed'};
      if(wc.getURL()!==before)return {reason:'browser_changed'};
      if(image.isEmpty())return {reason:'preview_failed'};
      const size=image.getSize(),scale=Math.min(1,960/Math.max(size.width,size.height));
      const scaled=scale<1?image.resize({width:Math.max(1,Math.round(size.width*scale)),height:Math.max(1,Math.round(size.height*scale))}):image;
      const buffer=scaled.toJPEG(60);if(buffer.length>320000)return {reason:'preview_failed'};
      let url='';try{const value=new URL(wc.getURL());if(value.protocol==='https:'&&!value.username&&!value.password)url=value.origin+value.pathname;}catch{}
      return {ready:true,url,title:wc.getTitle().slice(0,160),loading:wc.isLoading(),captured_at:Date.now(),...scaled.getSize(),
        frame_id:require('node:crypto').createHash('sha256').update(buffer).digest('hex'),image_base64:buffer.toString('base64')};
    }catch{return {reason:'preview_failed'};}finally{clearTimeout(timer);}
  }
  enqueue(fn) {
    if (this.queued >= 8) return Promise.resolve({ error: 'browser_queue_full' });
    this.queued++;
    const next = this.queue.then(fn).catch(() => ({ error: 'browser_operation_failed' }));
    this.queue = next.finally(() => { this.queued--; });
    return next;
  }
  ensure(control) {
    if (this.view?.webContents && !this.view.webContents.isDestroyed()) { if (control) { this.attach(); this.window.show(); this.window.focus(); } return; }
    const profile = join(this.root, 'profile'); mkdirSync(profile, { recursive: true, mode: 0o700 }); chmodSync(profile, 0o700);
    const ses = this.sessions.fromPath(profile);
    ses.setPermissionRequestHandler((_, __, callback) => callback(false));
    ses.setPermissionCheckHandler(() => false);
    ses.on('will-download', event => event.preventDefault());
    ses.webRequest.onBeforeRequest(async (details, callback) => {
      if (!resourceAllowed(details.url)) { callback({cancel:true}); return; }
      try { const resolved = await ses.resolveHost(new URL(details.url).hostname); callback({cancel:!resolved.endpoints.length || resolved.endpoints.some(e => !publicAddress(e.address))}); }
      catch { callback({cancel:true}); }
    });
    const parent = this.parent(); this.ownsWindow = !parent || parent.isDestroyed();
    this.window = this.ownsWindow ? new this.Window({ title: 'Браузер Игорька', width: 1180, height: 920, minWidth: 760, minHeight: 650, show: !!control,
      webPreferences: { preload: join(__dirname, 'browser-preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, backgroundThrottling:false } }) : parent;
    this.chromeDetached=false;
    this.view = new this.View({ webPreferences: { session: ses, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, navigateOnDragDrop: false, spellcheck: false, backgroundThrottling: false } });
    const win = this.window, view = this.view, wc = view.webContents;
    this.bindTab(view, ses);
    this.bindControls(win);
    this.resize = () => this.layout();for(const event of ['resize','focus','show','restore'])win.on(event,this.resize);
    if(!this.ownsWindow)win.webContents.on('did-finish-load',this.resize);
    this.onClosed = () => { this.stopping = true; ses.flushStorageData(); ses.cookies.flushStore().catch(() => {}); for(const tab of [...this.tabs])if(!tab.view.webContents.isDestroyed())tab.view.webContents.close();this.tabs=[]; if (this.controls?.webContents && !this.controls.webContents.isDestroyed()) this.controls.webContents.close(); if (this.window === win) { this.window = this.view = this.controls = null; this.auth = 'unknown'; this.attached = false; } };
    win.on('closed', this.onClosed);
    this.attach();
  }
  bindControls(win) {
    this.controls = this.ownsWindow ? null : new this.View({ webPreferences: { preload: join(__dirname, 'browser-preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, backgroundThrottling:false } });
    if(this.controls)this.controls.setBackgroundColor('#00000000');
    const controls = this.controlContents();
    controls.setWindowOpenHandler(() => ({ action: 'deny' }));
    controls.on('will-navigate', event => event.preventDefault());
    controls.on('will-redirect', event => event.preventDefault());
    controls.on('will-attach-webview', event => event.preventDefault());
    this.chromeRecoveries=[];this.chromeRecovering=false;
    controls.on('render-process-gone',(_event,details={})=>{
      if(this.window!==win||this.controlContents()!==controls||win.isDestroyed()||controls.isDestroyed()||this.chromeRecovering)return;
      this.diagnose('toolbar_renderer_gone',{reason:details.reason,exit_code:details.exitCode});
      const now=Date.now();this.chromeRecoveries=this.chromeRecoveries.filter(time=>now-time<60000);
      if(this.chromeRecoveries.length>=3){this.paused='controls_failed';return;}
      this.chromeRecoveries.push(now);this.chromeRecovering=true;this.menuOpen=false;
      controls.loadFile(join(__dirname,'browser.html')).then(()=>{
        if(this.window===win&&this.controlContents()===controls){if(this.paused==='controls_failed')this.paused='';this.layout();this.diagnose('toolbar_recovered');}
      }).catch(()=>{if(this.window===win&&this.controlContents()===controls)this.paused='controls_failed';}).finally(()=>{if(this.controlContents()===controls)this.chromeRecovering=false;});
    });
    controls.loadFile(join(__dirname, 'browser.html')).catch(() => { if(this.controlContents()===controls)this.paused = 'controls_failed'; });
  }
  bindTab(view, ses, opener = null) {
    const wc=view.webContents,tab={id:require('node:crypto').randomUUID(),view,opener};this.tabs.push(tab);
    const chrome=wc.getUserAgent().match(/Chrome\/[\d.]+/)?.[0];
    if(chrome)wc.setUserAgent('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '+chrome+' Safari/537.36');
    wc.setWindowOpenHandler(details=>{
      if(!details||!this.manual||this.blocked||!this.attached||this.tabs.length>=32
          || !(pageUrl(details.url)||details.url==='about:blank'&&pageUrl(wc.getURL())))return {action:'deny'};
      return {action:'allow',overrideBrowserWindowOptions:{webPreferences:{session:ses,sandbox:true,contextIsolation:true,nodeIntegration:false,nodeIntegrationInSubFrames:false,webSecurity:true,preload:undefined}},
        createWindow:options=>{
          const child=new this.View({webContents:options.webContents,webPreferences:{...options.webPreferences,session:ses,sandbox:true,contextIsolation:true,nodeIntegration:false,nodeIntegrationInSubFrames:false,preload:undefined,webSecurity:true,backgroundThrottling:false}});
          this.bindTab(child,ses,tab.id);
          if(details.disposition==='background-tab')child.webContents.loadURL(details.url).catch(()=>{});
          else this.switchTab(this.tabs.at(-1).id);
          return child.webContents;
        }};
    });
    wc.on('before-input-event',(event,input)=>{
      if(input.type==='keyDown'&&(input.control||input.meta)&&['t','w'].includes(input.key?.toLowerCase())){
        event.preventDefault();this.owner(input.key.toLowerCase()==='t'?'tab_new':'tab_close',input.key.toLowerCase()==='t'?{}:{id:tab.id}).catch(()=>{});
      }
    });
    wc.on('will-attach-webview',event=>event.preventDefault());
    wc.on('will-navigate',(event,url)=>{if(!resourceAllowed(url))event.preventDefault();});
    wc.on('will-redirect',(event,url)=>{if(!resourceAllowed(url))event.preventDefault();});
    wc.on('did-fail-load',(_event,code,_description,url,main)=>{if(main&&this.view===view)this.diagnose('page_load_failed',{net_error:code,current_origin:url});});
    wc.on('render-process-gone',()=>{if(this.view===view){this.paused='browser_crashed';this.auth='unknown';this.stopping=true;}});
    wc.on('destroyed',()=>{
      const active=this.view===view,index=this.tabs.indexOf(tab);this.tabs=this.tabs.filter(value=>value!==tab);
      if(this.attached&&this.window&&!this.window.isDestroyed())this.window.contentView.removeChildView(view);
      if(active){
        this.view=null;
        if(this.tabs.length)this.switchTab(this.tabs.find(value=>value.id===tab.opener)?.id||this.tabs[Math.max(0,index-1)].id);
        else if(!this.stopping&&!this.blocked&&this.window&&!this.window.isDestroyed())this.newTab();
      }
    });
    return tab;
  }
  switchTab(id) {
    const tab=this.tabs.find(value=>value.id===id&&!value.view.webContents.isDestroyed());
    if(!tab)return false;
    if(this.attached&&this.view&&this.view!==tab.view)this.window.contentView.removeChildView(this.view);
    this.view=tab.view;this.auth='unknown';this.paused='';
    if(this.attached){this.window.contentView.addChildView(this.view);this.layout();if(!this.overlayActive(this.window))this.view.webContents.focus();}
    return true;
  }
  newTab() {
    if(this.tabs.length>=32)return false;
    const ses=this.tabs[0]?.view.webContents.session||this.sessions.fromPath(join(this.root,'profile'));
    const view=new this.View({webPreferences:{session:ses,sandbox:true,contextIsolation:true,nodeIntegration:false,webSecurity:true,backgroundThrottling:false,spellcheck:false}});
    const tab=this.bindTab(view,ses);this.switchTab(tab.id);return tab;
  }
  controlContents() { return this.controls?.webContents || this.window?.webContents; }
  attach() {
    if (!this.window || !this.view?.webContents || this.attached) return;
    if (this.controls && this.chromeDetached) {
      const previous=this.controls;this.bindControls(this.window);previous.webContents.close();
      this.chromeDetached=false;this.diagnose('toolbar_reattached');
    }
    this.window.contentView.addChildView(this.view);
    if (this.controls) this.window.contentView.addChildView(this.controls);
    this.attached = true; this.layout();
  }
  resizeDock(phase,screenX=0) {
    if(this.blocked||!this.attached||this.ownsWindow||!this.window||this.window.isDestroyed())return {error:'browser_closed'};
    if(!['start','move','end','grow','shrink','reset'].includes(phase)||!Number.isFinite(screenX))return {error:'invalid_request'};
    if(phase==='start')this.dockResize={x:screenX,width:this.dockPixelWidth};
    if(phase==='move'&&this.dockResize)this.dockWidth=this.dockResize.width+this.dockResize.x-screenX;
    if(phase==='grow'||phase==='shrink')this.dockWidth=this.dockPixelWidth+(phase==='grow'?24:-24);
    if(phase==='reset')this.dockWidth=null;
    this.layout();
    if(['end','grow','shrink','reset'].includes(phase)){
      this.dockResize=null;this.dockWidth=phase==='reset'?null:this.dockPixelWidth;this.save('layout.json',{width:this.dockWidth});
    }
    return {width:this.dockPixelWidth};
  }
  layout() {
    if (!this.window || this.window.isDestroyed() || !this.attached) return;
    const visible = this.ownsWindow || !this.overlayActive(this.window);
    this.view.setVisible(visible);
    if (this.controls) this.controls.setVisible(visible);
    const [width,height] = this.window.getContentSize();
    const automatic=Math.min(740,Math.max(Math.min(560,width*.55),Math.floor(width*.42)));
    const panel=this.ownsWindow?width:Math.round(Math.max(320,Math.min(Math.max(320,width-600),this.dockWidth??automatic)));
    const x=width-panel,gap=this.ownsWindow?0:6;this.dockPixelWidth=panel;
    if (this.controls) {
      this.window.webContents.executeJavaScript('document.documentElement.style.setProperty("--igorek-browser-width", "'+panel+'px");document.body.classList.add("igorek-browser-attached");document.getElementById("igorek-browser-resizer")?.setAttribute("aria-valuenow","'+panel+'")').catch(()=>{});
    }
    const top=this.ownsWindow?0:36;
    const chrome=this.controls||this.window.contentView.children.find(child=>child.webContents===this.window.webContents);
    if(chrome){chrome.setBackgroundColor('#00000000');chrome.setBounds({x:x+gap,y:top,width:panel-gap,height:Math.min(height-top,this.menuOpen?HEADER_HEIGHT+460:HEADER_HEIGHT)});this.window.contentView.addChildView(chrome);chrome.webContents.invalidate?.();}
    this.view.setBounds({x:x+gap,y:top+HEADER_HEIGHT,width:panel-gap,height:Math.max(1,height-top-HEADER_HEIGHT)});
  }
  detach() {
    if (!this.window || this.window.isDestroyed() || !this.attached) return;
    this.menuOpen=false;this.dockResize=null;this.controlContents()?.send?.('igorek:browser-menu-close');
    if (this.ownsWindow) { this.window.hide(); return; }
    this.chromeDetached=true;
    this.window.contentView.removeChildView(this.view); this.window.contentView.removeChildView(this.controls); this.attached = false;
    this.window.webContents.executeJavaScript('document.documentElement.style.removeProperty("--igorek-browser-width");document.body.classList.remove("igorek-browser-attached","igorek-browser-resizing")').catch(()=>{});
  }
  async evaluate(script, deadline = Infinity) {
    const timeout = Math.min(5000, deadline-this.now());
    if(timeout <= 0) throw Object.assign(new Error('time_limit'),{code:'TIME_LIMIT'});
    let timer;
    try { return await Promise.race([this.view.webContents.executeJavaScript(script),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Object.assign(new Error('page_timeout'),{code:'PAGE_TIMEOUT'})),timeout);})]); }
    finally { clearTimeout(timer); }
  }
  async navigate(url, deadline = Infinity) {
    this.ensure(false);
    if (!resourceAllowed(url)) return { error: 'invalid_url' };
    const wc = this.view.webContents;
    let timer,readyResolve;
    const navigation={phase:'loading',committed:false,document_ready:false,recovered:false};
    const ready=new Promise(resolve=>{readyResolve=resolve;});
    const committed=()=>{navigation.committed=true;};
    const domReady=()=>{if(navigation.committed){navigation.document_ready=true;readyResolve();}};
    const inPage=(_event,_url,main)=>{if(main){committed();domReady();}};
    wc.on('did-navigate',committed);wc.on('did-navigate-in-page',inPage);wc.on('dom-ready',domReady);
    try {
      await Promise.race([wc.loadURL(url).then(()=>{committed();navigation.document_ready=true;}),ready,new Promise((_,reject)=>{timer=setTimeout(()=>{if(!wc.isDestroyed())wc.stop();reject(Object.assign(new Error('navigation_timeout'),{code:'NAVIGATION_TIMEOUT'}));},Math.max(1,Math.min(25000,deadline-this.now())));})]);
      clearTimeout(timer);navigation.phase='read';
      await this.sleep(Math.max(0,Math.min(800,deadline-this.now())));
      if(deadline-this.now()>1000)await this.reading.wait({condition:X_HOSTS.has(new URL(url).hostname)?'post':'ready',timeout_ms:Math.min(8000,deadline-this.now())});
      if(this.view?.webContents!==wc)throw new Error('navigation_interrupted');
      const page=await this.read(deadline);navigation.phase=page.error?'failed':'complete';
      this.diagnose(page.error?'navigation_failed':'navigation_completed',{...navigation,requested_origin:url,current_origin:wc.getURL()});
      return {...page,navigation:{...navigation}};
    } catch(error) {
      clearTimeout(timer);navigation.error_code=error?.code;navigation.phase='document_probe';
      for(let attempt=0;attempt<10&&deadline-this.now()>0;attempt++){
        if(wc.isDestroyed()||this.view?.webContents!==wc)break;
        if(navigation.committed){
          try{
            const state=await this.evaluate(`(()=>({navigation_ready:document.readyState!=='loading'&&!!document.body&&document.body.children.length>0,url:location.href}))()`,deadline);
            if(state?.navigation_ready&&pageUrl(state.url)&&pageUrl(state.url)===pageUrl(wc.getURL())){
              const page=await this.read(deadline);
              if(!page.error){navigation.document_ready=true;navigation.recovered=true;navigation.phase='complete';navigation.warning_code=navigation.error_code;delete navigation.error_code;this.diagnose('navigation_recovered',{...navigation,requested_origin:url,current_origin:wc.getURL()});return {...page,navigation:{...navigation}};}
            }
          }catch{}
        }
        await this.sleep(Math.min(100,Math.max(0,deadline-this.now())));
      }
      navigation.phase='failed';this.diagnose('navigation_failed',{...navigation,requested_origin:url,current_origin:wc.isDestroyed()?'':wc.getURL()});
      return {error:'navigation_failed',navigation:{...navigation}};
    }
    finally {clearTimeout(timer);wc.removeListener('did-navigate',committed);wc.removeListener('did-navigate-in-page',inPage);wc.removeListener('dom-ready',domReady);}
  }
  async read(deadline = Infinity) {
    if (!this.view?.webContents || this.view.webContents.isDestroyed()) return { error: 'browser_closed' };
    const wc = this.view.webContents;this.pagePosts=[];
    if(!wc.getURL()||wc.getURL()==='about:blank'){
      this.auth='not_checked';return compactPage({auth:this.auth,url:'about:blank',title:'Новая вкладка',text:'',links:[],elements:[]});
    }
    if (!X_HOSTS.has(new URL(wc.getURL()).hostname)) {
      if (!pageUrl(wc.getURL())) return {error:'public_page_required'};
      const page = await this.evaluate(`(() => ({title:document.title.slice(0,500),text:(document.body?.innerText || '').slice(0,40000),links:Array.from(document.querySelectorAll('a[href]')).slice(0,150).map(a=>({text:(a.textContent||'').slice(0,200),url:a.href.slice(0,2048)}))}))()`,deadline);
      this.auth='not_checked';return compactPage({auth:this.auth,url:wc.getURL(),title:page.title,text:page.text,links:page.links,elements:await this.interaction.snapshot()});
    }
    const data = await this.evaluate(PAGE,deadline);
    if (!data || !['ready', 'challenge', 'login_required', 'unknown'].includes(data.auth) || !Array.isArray(data.posts)) return { error: 'invalid_page' };
    this.auth = data.auth;
    if (['login_required', 'challenge'].includes(data.auth)) {
      if (this.paused !== data.auth) this.notify(data.auth);
      this.paused = data.auth; this.manual = true;
      this.save('control.json', {manual:true});
      return { auth: data.auth, posts: [], paused: true };
    }
    if (data.auth !== 'ready') return { auth: data.auth, posts: [], reason: 'page_not_ready' };
    const posts = data.posts.filter(p => p && /^\d{1,25}$/.test(p.id) && xUrl(p.url, true) && new URL(p.url).pathname.endsWith('/'+p.id)).slice(0, this.config.max_posts);
    const viewed = new Date(this.now()).toISOString();
    for (const p of posts) {
      const before = this.records[p.id];
      this.records[p.id] = { id: p.id, url: xUrl(p.url, true), first_seen_at: before?.first_seen_at || viewed, last_seen_at: viewed, status: before?.status || 'discovered' };
    }
    this.save('observed.json', { posts: this.records, last: this.last });
    const share=Math.floor(500000/Math.max(1,posts.length));
    this.pagePosts=posts.map(post=>{
      if(JSON.stringify(post).length<=share)return post;
      const value={...post,author:String(post.author||'').slice(0,100),social_context:String(post.social_context||'').slice(0,100),links:[],images:[],truncated:true,text:''};
      value.text=String(post.text||'').slice(0,Math.max(0,share-JSON.stringify(value).length-20));return value;
    });
    const result=compactPage({auth:data.auth,url:wc.getURL(),title:wc.getTitle(),text:'',links:[],posts,elements:await this.interaction.snapshot()});
    return {...result,viewed_at:viewed};
  }
  async scroll(deadline = Infinity) {
    const before = await this.read(deadline);
    if (!['ready','not_checked'].includes(before.auth)) return before;
    await this.evaluate('window.scrollBy(0, Math.max(700, window.innerHeight * 0.85)); true',deadline);
    await this.sleep(Math.max(0,Math.min(900,deadline-this.now())));
    return this.read(deadline);
  }
  async collect(job) {
    const start = this.now(), deadline = start + this.config.max_seconds * 1000;
    const found = new Map(); let idle = 0, reason = 'post_limit';
    try {
      let page = await this.navigate(this.config.feed_url,deadline);
      while (found.size < this.config.max_posts) {
        if (this.stopping || this.manual || this.blocked) { reason = 'interrupted'; break; }
        if (page.auth !== 'ready' || page.error) { reason = page.error || page.auth || 'page_not_ready'; break; }
        const size = found.size;
        for (const post of this.pagePosts||page.posts) if (found.size < this.config.max_posts) found.set(post.id, post);
        job.posts = [...found.values()];
        idle = found.size === size ? idle + 1 : 0;
        if (this.now() >= deadline) { reason = 'time_limit'; break; }
        if (idle >= 8) { reason = 'no_additional_visible_posts'; break; }
        if (found.size >= this.config.max_posts) break;
        if (deadline - this.now() < 6000) { reason = 'time_limit'; break; }
        page = await this.scroll(deadline);
      }
    } catch { reason = this.now() >= deadline ? 'time_limit' : 'browser_operation_failed'; }
    job.state = 'finished'; job.reason = reason;
    this.last = { started_at: new Date(start).toISOString(), finished_at: new Date(this.now()).toISOString(), count: found.size, reason, coverage: 'bounded_view_only' };
    this.save('observed.json', { posts: this.records, last: this.last });
  }
  agent(fields) {
    if (!fields || typeof fields !== 'object' || Array.isArray(fields)) return Promise.resolve({ error: 'invalid_request' });
    const { action } = fields;
    const interact=['click','hover','fill','press','select','check','scroll_element','drag'].includes(action);
    const reading=validReading(fields);
    if(action==='elements'){if(Object.keys(fields).some(key=>!['action','query','url'].includes(key))||typeof(fields.query??'')!=='string'||(fields.query||'').length>200)return Promise.resolve({error:'invalid_request'});}
    if(action!=='elements'&&!reading&&(interact?!validInteraction(fields):(!['status', 'open', 'read', 'scroll', 'post', 'collect', 'result'].includes(action)
        || Object.keys(fields).sort().join(',') !== (action === 'post' || (action === 'open' && 'url' in fields) ? 'action,url' : 'action')))) return Promise.resolve({ error: 'invalid_request' });
    if (action === 'status') {const value=this.status();const clean=raw=>{if(!raw||raw==='about:blank')return 'about:blank';try{const url=new URL(raw);return url.origin+url.pathname;}catch{return '';}};return Promise.resolve({...value,current_url:clean(value.current_url),tabs:value.tabs.map(tab=>({...tab,url:clean(tab.url)}))});}
    if (this.blocked) return Promise.resolve({ error: 'desktop_locked' });
    if (this.manual || this.paused) return Promise.resolve({ error: 'browser_needs_owner', auth: this.auth, reason: this.paused || 'manual_login' });
    if (action === 'result') return Promise.resolve(this.job ? { ...this.job, coverage: 'bounded_view_only' } : { error: 'no_run' });
    if (['post','read_post','comments'].includes(action) && !xUrl(fields.url, true)) return Promise.resolve({ error: 'invalid_url' });
    if (action === 'elements' && 'url' in fields && !xUrl(fields.url,true))return Promise.resolve({error:'invalid_url'});
    if (action === 'open' && 'url' in fields && !pageUrl(fields.url)) return Promise.resolve({error:'invalid_url'});
    if (action === 'collect') {
      if(!X_HOSTS.has(new URL(this.config.feed_url).hostname))return Promise.resolve({error:'twitter_feed_required_for_collect',hint:'Для обычного сайта используй open/read/scroll.'});
      if (this.job?.state === 'running' || this.job?.state === 'queued') return Promise.resolve({ job: { id: this.job.id, state: this.job.state } });
      const job = this.job = { id: require('node:crypto').randomUUID(), state: 'queued', posts: [], reason: '' };
      this.enqueue(async () => { if (this.blocked || this.manual || this.paused) { job.state = 'finished'; job.reason = 'browser_needs_owner'; return; } job.state = 'running'; this.stopping = false; await this.collect(job); });
      return Promise.resolve({ job: { id: job.id, state: job.state } });
    }
    return this.enqueue(async () => {
      if (this.blocked || this.manual || this.paused) return { error: 'browser_needs_owner' };
      if (action === 'open' || action === 'post') return this.navigate(action === 'post' ? xUrl(fields.url, true) : fields.url ? pageUrl(fields.url) : this.config.feed_url);
      if(action==='elements'){this.ensure(false);return compactPage({auth:this.auth,url:this.view.webContents.getURL(),title:this.view.webContents.getTitle(),text:'',links:[],elements:await this.interaction.snapshot(fields.query||'',fields.url?xUrl(fields.url,true):'')});}
      if(reading){this.ensure(false);if(action==='wait')return this.reading.wait(fields);if(action==='screenshot')return this.reading.screenshot();if(action==='close_menu')return this.reading.close();return action==='read_post'?this.reading.post({...fields,url:xUrl(fields.url,true)}):this.reading.comments({...fields,url:xUrl(fields.url,true)});}
      if(interact){const state=await this.interaction.run(fields);return state.error?state:{...state,...await this.read()};}
      return action === 'scroll' ? this.scroll() : this.read();
    });
  }
  owner(action, fields = {}) {
    if (action === 'status') return Promise.resolve(this.status());
    if(action==='hide'){this.detach();return Promise.resolve(this.status());}
    if(action==='menu'){
      if(Object.keys(fields).join(',')!=='open'||typeof fields.open!=='boolean')return Promise.resolve({error:'invalid_request'});
      if(this.blocked&&fields.open)return Promise.resolve({error:'desktop_locked'});
      this.menuOpen=fields.open;this.layout();return Promise.resolve(this.status());
    }
    if (this.blocked) return Promise.resolve({ error: 'desktop_locked' });
    if(['tab_select','tab_close'].includes(action)&&(Object.keys(fields).join(',')!=='id'||typeof fields.id!=='string'||!this.tabs.some(tab=>tab.id===fields.id)))return Promise.resolve({error:'invalid_request'});
    if(action==='tab_new'&&Object.keys(fields).length)return Promise.resolve({error:'invalid_request'});
    if(action==='navigate'&&(Object.keys(fields).join(',')!=='url'||typeof fields.url!=='string'||fields.url.length>2048||!pageUrl(fields.url)))return Promise.resolve({error:'invalid_url'});
    if(action==='limits'){
      if(Object.keys(fields).sort().join(',')!=='max_posts,max_seconds')return Promise.resolve({error:'invalid_settings'});
      try{settings({...this.config,...fields});}catch{return Promise.resolve({error:'invalid_settings'});}
    }
    if (['login', 'pause', 'close', 'configure','navigate','back','forward','reload','tab_new','tab_select','tab_close'].includes(action)) { this.manual = true; this.stopping = true; this.save('control.json', {manual:true}); }
    return this.enqueue(async () => {
      if(action==='tab_new'){this.ensure(true);this.newTab();}
      else if(action==='tab_select'){this.switchTab(fields.id);}
      else if(action==='tab_close'){const tab=this.tabs.find(value=>value.id===fields.id);if(tab){this.stopping=false;tab.view.webContents.close();}}
      else if (action === 'configure') { this.config = settings(fields); this.save('settings.json', this.config); this.ensure(true); this.paused=''; await this.navigate(this.config.feed_url); }
      else if(action==='navigate'){this.config=settings({...this.config,feed_url:fields.url});this.save('settings.json',this.config);this.ensure(true);this.paused='';await this.navigate(this.config.feed_url);if(this.attached)this.view.webContents.focus();}
      else if(action==='limits'){this.config=settings({...this.config,...fields});this.save('settings.json',this.config);}
      else if(['back','forward','reload'].includes(action)){
        if(!this.view?.webContents||this.view.webContents.isDestroyed())return {error:'browser_closed'};
        const wc=this.view.webContents;this.paused='';this.auth='unknown';
        if(action==='back'&&wc.navigationHistory.canGoBack())wc.navigationHistory.goBack();
        else if(action==='forward'&&wc.navigationHistory.canGoForward())wc.navigationHistory.goForward();
        else if(action==='reload')wc.reload();
        wc.focus();
      }
      else if (action === 'login') { this.ensure(true); this.paused = ''; await this.navigate(X_HOSTS.has(new URL(this.config.feed_url).hostname) ? 'https://x.com/i/flow/login' : this.config.feed_url); }
      else if (action === 'show') { this.ensure(true); if (!this.view.webContents.getURL()) await this.navigate(this.config.feed_url); }
      else if (action === 'resume') {
        const state = await this.read();
        if (!['ready','not_checked'].includes(state.auth)) return { error: 'login_not_verified', ...this.status() };
        this.manual = false; this.paused = ''; this.stopping = false;
        this.save('control.json', {manual:false});
        this.view.webContents.session.flushStorageData(); await this.view.webContents.session.cookies.flushStore();
      } else if (action === 'pause') this.manual = true;
      else if (action === 'close') await this.closeWindow();
      else if (action !== 'configure') return { error: 'invalid_request' };
      return this.status();
    });
  }
  lock() { this.blocked = true; this.stopping = true; this.detach(); }
  unlock() { this.blocked = false; }
  async closeWindow() {
    const win = this.window, wc = this.view?.webContents;
    if (!win || win.isDestroyed()) return;
    const destroyed = wc && !wc.isDestroyed() ? new Promise(resolve => wc.once('destroyed', resolve)) : Promise.resolve();
    if(!this.ownsWindow){this.detach();for(const event of ['resize','focus','show','restore'])win.removeListener(event,this.resize);win.webContents.removeListener('did-finish-load',this.resize);win.removeListener('closed',this.onClosed);for(const tab of [...this.tabs])if(!tab.view.webContents.isDestroyed())tab.view.webContents.close();this.tabs=[];this.controls?.webContents?.close();this.window=this.view=this.controls=null;await destroyed;return;}
    const closed = new Promise(resolve => win.once('closed', resolve));
    win.close();
    await closed; await destroyed;
  }
  async shutdown() { this.stopping = true; this.manual = true; await this.queue; if (this.view && !this.view.webContents.isDestroyed()) { const ses = this.view.webContents.session; ses.flushStorageData(); await ses.cookies.flushStore(); } await this.closeWindow(); }
}
module.exports = { DesktopBrowser, DEFAULTS, HEADER_HEIGHT, PAGE, xUrl, pageUrl, publicAddress, resourceAllowed, settings };
