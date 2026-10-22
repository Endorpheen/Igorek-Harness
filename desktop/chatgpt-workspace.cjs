const fs=require('node:fs');
const path=require('node:path');
const {ChatGPTPermissions}=require('./chatgpt-permissions.cjs');
const {PAGE_TOOLS}=require('./chatgpt-page-tools.cjs');
const HOME='https://chatgpt.com/';
const CHATGPT_HOME_FIX=`(()=>{
  if(location.origin!=='https://chatgpt.com'||window.__igorekHomeNavigationV2)return;
  Object.defineProperty(window,'__igorekHomeNavigationV2',{value:true});
  window.addEventListener('click',event=>{
    if(!event.isTrusted||event.defaultPrevented||event.button>0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    const button=event.target.closest?.('button,a,[role="button"],[role="link"],[tabindex][aria-label]');
    if(!button)return;
    const labels=[button.getAttribute('aria-label'),button.getAttribute('title'),button.textContent].filter(Boolean).map(value=>value.trim());
    let home=labels.some(label=>['Home','Главная','Домой'].includes(label));
    if(!home&&button.matches('a[href]')&&button.closest('nav,[role="navigation"],[aria-label="App navigation"],[aria-label="Application navigation"],[aria-label="Навигация по приложению"]')){
      try{const url=new URL(button.getAttribute('href'),location.href);home=url.origin===location.origin&&url.pathname==='/'&&!url.hash;}catch{}
    }
    if(!home)return;
    event.preventDefault();event.stopImmediatePropagation();location.assign('https://chatgpt.com/');
  },true);
})()`;
const ORIGINS=new Set(['https://chatgpt.com','https://auth.openai.com','https://accounts.google.com','https://appleid.apple.com']);
function allowedURL(value){try{const url=new URL(value);return !url.username&&!url.password&&ORIGINS.has(url.origin)}catch{return false}}
function mediaRequest(permission,details={}){return permission==='media'&&details.isMainFrame===true&&Array.isArray(details.mediaTypes)&&details.mediaTypes.length>0&&details.mediaTypes.every(type=>['audio','video'].includes(type))&&(()=>{try{return new URL(details.requestingUrl).origin==='https://chatgpt.com'}catch{return false}})()}
class ChatGPTWorkspace{
  constructor({root,BrowserWindow,WebContentsView,session,dialog,shell,menu=null,overlayActive=()=>false,sidebarShortcut=()=>false,startURL=HOME,urlAllowed=allowedURL,surfaceSelector='.main-panel',openPopup=null,permissions=null}){
    this.permissions=permissions||new ChatGPTPermissions(root);this.openPopup=openPopup;this.startURL=startURL;this.urlAllowed=urlAllowed;this.surfaceSelector=surfaceSelector;this.root=root;this.Window=BrowserWindow;this.View=WebContentsView;this.sessions=session;this.dialog=dialog;this.overlayActive=overlayActive;
    this.blocked=true;this.attached=false;this.parent=null;this.view=null;this.popups=new Set();this.generation=0;this.layoutSequence=0;
    this.sidebarShortcut=sidebarShortcut;
    this.mediaGranted=new Set();this.mediaPrompts=new Map();
    this.shell=shell;
    this.menu=menu;
  }
  unlock(){this.blocked=false}
  status(){const wc=this.view?.webContents,open=!!wc&&!wc.isDestroyed();return {visible:this.attached&&!this.blocked,can_back:open&&wc.navigationHistory.canGoBack(),can_forward:open&&wc.navigationHistory.canGoForward(),loading:open&&wc.isLoading(),url:open?wc.getURL():'',title:open?wc.getTitle?.()||'':''}}
  notify(){if(this.parent&&!this.parent.isDestroyed())this.parent.webContents.send('igorek:chatgpt-state',this.status())}
  async contextMenu(contents,params){
      const trusted=()=>!this.blocked&&this.attached&&contents===this.view?.webContents&&!contents.isDestroyed();
      if(!trusted()||!this.menu)return;
      const pageURL=contents.getURL(),generation=this.generation;
      const items=[];let selected=Boolean(params.selectionText),probe=null;
      try{
        if(new URL(contents.getURL()).origin==='https://chatgpt.com'){
          probe=await contents.executeJavaScript(PAGE_TOOLS+';window.__igorekPageTools?.probe('+Number(params.x)+','+Number(params.y)+')');
          selected=selected||Boolean(probe?.selected);
          if(probe&&(probe.enabled||probe.available))items.push({label:probe.enabled?'Вернуть голосовой шар':'Осьминог здесь (прототип)',click:async()=>{
            if(!trusted()||new URL(contents.getURL()).origin!=='https://chatgpt.com')return;
            const image=probe.enabled?'':'data:image/png;base64,'+fs.readFileSync(path.join(__dirname,'assets','evil-octopus.png')).toString('base64');
            const wink=probe.enabled?'':'data:image/png;base64,'+fs.readFileSync(path.join(__dirname,'assets','evil-octopus-wink.png')).toString('base64');
            await contents.executeJavaScript('window.__igorekPageTools?.toggle('+Number(params.x)+','+Number(params.y)+','+JSON.stringify(image)+','+JSON.stringify(wink)+')').catch(()=>{});
          }});
        }
      }catch{}
      const image=params.mediaType==='image'&&params.hasImageContents===true?{srcURL:params.srcURL,hasImageContents:true}:probe?.image;
      if(image?.hasImageContents===true&&typeof image.srcURL==='string'){
        try{
          const source=new URL(image.srcURL),web=source.protocol==='blob:'?new URL(source.pathname):source;
          const allowed=(['http:','https:'].includes(web.protocol)&&!web.username&&!web.password)||(source.protocol==='data:'&&/^data:image\//i.test(image.srcURL));
          const current=()=>trusted()&&generation===this.generation&&contents.getURL()===pageURL;
          if(allowed&&current())items.push({label:'Сохранить изображение как…',click:()=>{if(current())contents.downloadURL(image.srcURL);}});
        }catch{}
      }
      if(selected)items.unshift({label:'Копировать',accelerator:'CmdOrCtrl+C',click:()=>{if(trusted())contents.copy();}});
      if(trusted()&&items.length)this.menu.buildFromTemplate(items).popup({window:this.parent});
  }
  secure(contents,session){
    contents.on('context-menu',(_event,params)=>{this.contextMenu(contents,params).catch(()=>{});});
    contents.on('before-mouse-event',(event,input)=>{
      if(this.blocked||!this.attached||contents!==this.view?.webContents||contents.isDestroyed()||!this.menu||input.button!=='right'||!['mouseDown','mouseUp'].includes(input.type))return;
      try{if(new URL(contents.getURL()).origin!=='https://chatgpt.com')return;}catch{return;}
      event.preventDefault();
      if(input.type==='mouseUp'){
        const zoom=contents.getZoomFactor?.()||1;
        this.contextMenu(contents,{x:input.x/zoom,y:input.y/zoom}).catch(()=>{});
      }
    });
    contents.on('will-navigate',(event,url)=>{if(!this.urlAllowed(url))event.preventDefault()});
    contents.on('will-redirect',(event,url)=>{if(!this.urlAllowed(url))event.preventDefault()});
    contents.on('will-attach-webview',event=>event.preventDefault());
    contents.setWindowOpenHandler(({url})=>{
      if(this.openPopup&&this.urlAllowed(url)&&url!=='about:blank'&&!['https://auth.openai.com','https://accounts.google.com','https://appleid.apple.com'].includes(new URL(url).origin)){
        if(!this.blocked&&this.attached)this.openPopup(url);return {action:'deny'};
      }
      if(this.urlAllowed(url))return {action:'allow',overrideBrowserWindowOptions:{parent:this.parent,webPreferences:{session,sandbox:true,contextIsolation:true,nodeIntegration:false,preload:undefined}}};
      try{
        const target=new URL(url),generation=this.generation;
        if(target.protocol==='https:'&&!target.username&&!target.password&&this.shell&&!this.blocked&&this.attached)this.dialog.showMessageBox(this.parent,{type:'question',title:'Ссылка из ChatGPT',message:'Открыть '+target.hostname+' в обычном браузере?',buttons:['Открыть','Отмена'],defaultId:1,cancelId:1}).then(answer=>{if(answer.response===0&&!this.blocked&&this.attached&&generation===this.generation)return this.shell.openExternal(target.href)}).catch(()=>{});
      }catch{}
      return {action:'deny'};
    });
    contents.on('did-create-window',popup=>{this.popups.add(popup);this.secure(popup.webContents,session);popup.on('closed',()=>this.popups.delete(popup))});
  }
  ensure(){
    if(this.view&&!this.view.webContents.isDestroyed())return;
    const profile=path.join(this.root,'profile');fs.mkdirSync(profile,{recursive:true,mode:0o700});fs.chmodSync(this.root,0o700);fs.chmodSync(profile,0o700);
    const session=this.sessions.fromPath(profile);
    const agent=session.getUserAgent();
    const browserAgent=agent.replace(/[^\x20-\x7e]+/g,'Igorek').replace(/\s+(?:Igorek|Electron)\/[^\s]+/g,'');
    if(browserAgent!==agent)session.setUserAgent(browserAgent);
    this.view=new this.View({webPreferences:{session,sandbox:true,contextIsolation:true,nodeIntegration:false,webSecurity:true,navigateOnDragDrop:false,backgroundThrottling:true}});
    this.view.setVisible(false);
    this.view.webContents.setIgnoreMenuShortcuts(true);
    this.view.webContents.on('before-input-event',(event,input)=>{if(!this.attached||this.blocked)return;if(input.type==='keyDown'&&(input.control||input.meta)&&!input.shift&&!input.alt&&String(input.key).toLowerCase()==='c'){event.preventDefault();this.view.webContents.copy();return;}if(this.parent&&this.sidebarShortcut(input,this.parent))event.preventDefault()});
    this.secure(this.view.webContents,session);
    this.view.webContents.on('dom-ready',()=>{try{const wc=this.view?.webContents;if(wc&&!wc.isDestroyed()&&new URL(wc.getURL()).origin==='https://chatgpt.com')wc.executeJavaScript(CHATGPT_HOME_FIX+';'+PAGE_TOOLS).catch(()=>{});}catch{}});
    for(const event of ['did-navigate','did-navigate-in-page','did-start-loading','did-stop-loading'])this.view.webContents.on(event,()=>this.notify());
  }
  configurePermissions(session){
    session.setPermissionCheckHandler((sender,permission,origin,details={})=>!this.blocked&&this.attached&&origin==='https://chatgpt.com'&&sender===this.view?.webContents&&(permission==='clipboard-sanitized-write'||(permission==='media'&&(this.mediaGranted.has(details.mediaType)||(details.mediaType==='audio'&&this.permissions.microphone())))));
    session.setPermissionRequestHandler(async(sender,permission,callback,details)=>{
      const trusted=()=>!this.blocked&&this.attached&&this.view&&!this.view.webContents.isDestroyed()&&sender===this.view.webContents&&this.parent&&!this.parent.isDestroyed();
      if(!trusted())return callback(false);
      if(permission==='clipboard-sanitized-write'&&details.isMainFrame&&allowedURL(details.requestingUrl)&&new URL(details.requestingUrl).origin==='https://chatgpt.com')return callback(true);
      if(permission==='fullscreen'&&details.isMainFrame&&allowedURL(details.requestingUrl)&&new URL(details.requestingUrl).origin==='https://chatgpt.com')return callback(true);
      if(!mediaRequest(permission,details))return callback(false);
      if(details.mediaTypes.every(type=>this.mediaGranted.has(type)||(type==='audio'&&this.permissions.microphone())))return callback(true);
      try{
        const generation=this.generation;
        const device=details.mediaTypes.includes('video')?(details.mediaTypes.includes('audio')?'камере и микрофону':'камере'):'микрофону';
        const promptKey=details.mediaTypes.slice().sort().join(',');
        let prompt=this.mediaPrompts.get(promptKey);
        if(!prompt){prompt=this.dialog.showMessageBox(this.parent,{type:'question',title:'Разрешение ChatGPT',message:'Разрешить ChatGPT доступ к '+device+'?',detail:'Доступ получит веб-страница chatgpt.com в этой панели.',buttons:['Разрешить','Не разрешать'],defaultId:1,cancelId:1,...(details.mediaTypes.length===1&&details.mediaTypes[0]==='audio'?{checkboxLabel:'Запомнить разрешение микрофона для ChatGPT',checkboxChecked:true}:{})});this.mediaPrompts.set(promptKey,prompt);prompt.finally(()=>{if(this.mediaPrompts.get(promptKey)===prompt)this.mediaPrompts.delete(promptKey);}).catch(()=>{});}
        const answer=await prompt;
        const granted=answer.response===0&&trusted()&&generation===this.generation;
        if(granted){for(const type of details.mediaTypes)this.mediaGranted.add(type);if(answer.checkboxChecked&&details.mediaTypes.length===1&&details.mediaTypes[0]==='audio'){try{if(!this.permissions.microphone())this.permissions.setMicrophone(true);}catch{}}}
        callback(granted);
      }catch{callback(false)}
    });
  }
  async layout(){
    const parent=this.parent,view=this.view,sequence=++this.layoutSequence;
    if(!parent||parent.isDestroyed()||!view||view.webContents.isDestroyed())return;
    if(this.blocked||!this.attached||this.overlayActive(parent)){view.setVisible(false);return}
    try{
      const rect=await parent.webContents.executeJavaScript("(()=>{const node=document.querySelector("+JSON.stringify(this.surfaceSelector)+");if(!node)return null;const r=node.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}})()");
      if(sequence!==this.layoutSequence||parent!==this.parent||view!==this.view||this.blocked||!this.attached||this.overlayActive(parent))return;
      const [width,height]=parent.getContentSize();
      if(!rect||Object.values(rect).some(value=>!Number.isFinite(value))||rect.width<=0||rect.height<=0){view.setVisible(false);return}
      const x=Math.max(0,Math.round(rect.x)),y=Math.max(36,Math.round(rect.y));
      if(x>=width||y>=height){view.setVisible(false);return}
      view.setBounds({x,y,width:Math.max(1,Math.min(Math.round(rect.width),width-x)),height:Math.max(1,Math.min(Math.round(rect.height),height-y))});view.setVisible(true);
    }catch{if(view===this.view&&!view.webContents.isDestroyed())view.setVisible(false)}
  }
  detach(){
    if(this.parent&&!this.parent.isDestroyed()){
      if(this.view)this.parent.contentView.removeChildView(this.view);
      for(const event of ['resize','focus','show','restore'])this.parent.removeListener(event,this.onLayout);
      this.parent.removeListener('closed',this.onClosed);
      this.parent.webContents.removeListener('did-finish-load',this.onLayout);
    }
    this.parent=null;
  }
  async show(parent){
    if(this.blocked||!parent||parent.isDestroyed())return {visible:false};
    this.ensure();this.configurePermissions(this.view.webContents.session);
    if(parent!==this.parent){
      this.hide();this.detach();this.parent=parent;parent.contentView.addChildView(this.view);
      this.onLayout=()=>this.layout();this.onClosed=()=>{this.parent=null;this.lock()};
      for(const event of ['resize','focus','show','restore'])parent.on(event,this.onLayout);
      parent.on('closed',this.onClosed);parent.webContents.on('did-finish-load',this.onLayout);
    }
    this.attached=true;await this.layout();this.notify();
    for(const popup of this.popups)if(!popup.isDestroyed())popup.show();
    if(!this.view.webContents.getURL()&&this.startURL)this.view.webContents.loadURL(this.startURL).catch(()=>{});
    return {visible:true};
  }
  hide(){this.attached=false;if(this.view&&!this.view.webContents.isDestroyed())this.view.setVisible(false);for(const popup of this.popups)if(!popup.isDestroyed())popup.hide();this.notify();return {visible:false}}
  navigate(operation){const wc=this.view?.webContents;if(this.blocked||!this.attached||!wc||wc.isDestroyed())return this.status();if(operation==='back'&&wc.navigationHistory.canGoBack())wc.navigationHistory.goBack();else if(operation==='forward'&&wc.navigationHistory.canGoForward())wc.navigationHistory.goForward();else if(operation==='reload')wc.reload();return this.status()}
  lock(){this.blocked=true;this.mediaGranted.clear();this.mediaPrompts.clear();this.generation++;this.hide();for(const popup of this.popups)if(!popup.isDestroyed())popup.destroy();this.popups.clear();if(this.view){const wc=this.view.webContents;if(!wc.isDestroyed()){const session=wc.session;session.flushStorageData();session.cookies.flushStore().catch(()=>{})}this.detach();if(!wc.isDestroyed())wc.close();this.view=null}}
}
module.exports={ChatGPTWorkspace,allowedURL,mediaRequest,HOME,CHATGPT_HOME_FIX};
