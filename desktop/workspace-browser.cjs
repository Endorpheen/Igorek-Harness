const path=require('node:path');
const {ChatGPTWorkspace}=require('./chatgpt-workspace.cjs');
const {pageUrl}=require('./browser.cjs');
const {ChatGPTPermissions}=require('./chatgpt-permissions.cjs');
const {DownloadHistory,LIMIT}=require('./download-history.cjs');
const fs=require('node:fs');
const {WorkspaceAgent}=require('./workspace-agent.cjs');
const {BrowserFavorites}=require('./browser-favorites.cjs');
class WorkspaceBrowser {
  constructor(options){this.options=options;this.permissions=new ChatGPTPermissions(options.root);this.tabs=new Map();this.active=null;this.parent=null;this.history=[];this.downloadHistory=new DownloadHistory(options.root);this.downloads=[];this.sessions=new Set();this.blocked=true;this.sequence=0;
    this.favorites=new BrowserFavorites(options.root);
    this.requireHandoff=false;try{this.requireHandoff=JSON.parse(fs.readFileSync(path.join(options.root,'agent-control.json'),'utf8')).require_handoff===true;}catch{}
    this.manual=this.requireHandoff;this.lastActive=null;this.agentTools=new WorkspaceAgent(this);
  }
  unlock(){if(this.blocked)this.downloads=this.downloadHistory.rows;this.blocked=false;}
  publish(){if(this.parent&&!this.parent.isDestroyed())this.parent.webContents.send('igorek:workspace-browser-state',this.status());}
  controlSettings(value){if(value!==undefined){if(!value||Object.keys(value).join()!=="require_handoff"||typeof value.require_handoff!=="boolean")throw Error("Некорректная настройка.");this.requireHandoff=value.require_handoff;this.manual=this.requireHandoff;const file=path.join(this.options.root,"agent-control.json");fs.mkdirSync(this.options.root,{recursive:true,mode:0o700});fs.writeFileSync(file+".tmp",JSON.stringify(value),{mode:0o600});fs.renameSync(file+".tmp",file);this.agentTools.manual=this.manual;this.agentTools.stopping=this.manual;this.publish();}return {require_handoff:this.requireHandoff,manual:this.manual};}
  status(){if(this.blocked)return {active:null,tabs:[],history:[],downloads:[]};return {favorites:this.favorites.rows.map(row=>({...row})),favorites_error:this.favorites.error,require_handoff:this.requireHandoff,manual:this.requireHandoff&&this.manual,downloads_limit:LIMIT,downloads_error:this.downloadHistory.error?'Не удалось прочитать или сохранить историю загрузок.':'',active:this.active,tabs:[...this.tabs].map(([id,tab])=>({id,...tab.status(),zoom:tab.view?.webContents.getZoomFactor?.()||1})),history:this.history.slice(),downloads:this.downloads.map(({id,name,state,received,total,at,path:target})=>({id,name,state,received,total,at,can_reveal:state==='completed'&&Boolean(target)}))};}
  create(id,parent){
    if(this.tabs.size>=24)throw Error('Можно открыть до 24 браузерных вкладок.');
    const tab=new ChatGPTWorkspace({...this.options,permissions:this.permissions,root:path.join(this.options.root,'web'),startURL:'',urlAllowed:value=>Boolean(pageUrl(value))||value==='about:blank',surfaceSelector:'#workspace-browser-surface',openPopup:url=>{if(!this.blocked&&this.parent&&!this.parent.isDestroyed())this.parent.webContents.send('igorek:workspace-popup',url);}});
    tab.notify=()=>this.publish();tab.unlock();tab.ensure();
    const wc=tab.view.webContents,ses=wc.session;
    if(!this.sessions.has(ses)){
      this.sessions.add(ses);
      ses.on('will-download',(event,item,contents)=>{
        if(this.blocked||!([...this.tabs.values()].some(value=>value.view?.webContents===contents)||(this.diagnostics&&!this.diagnostics.isDestroyed()&&this.diagnostics.webContents===contents&&contents.getURL()==='chrome://webrtc-internals/'))){event.preventDefault();return;}
        const record={id:require('node:crypto').randomUUID(),name:item.getFilename(),state:'progressing',received:0,total:item.getTotalBytes(),at:Date.now(),path:''};
        const generation=this.sequence;
        this.downloadHistory.add(record);this.downloads=this.downloadHistory.rows;
        item.on('updated',(_event,state)=>{if(this.blocked||generation!==this.sequence)return;record.state=state;record.received=item.getReceivedBytes();this.publish();});
        item.once('done',(_event,state)=>{record.state=state;record.received=item.getReceivedBytes();if(state==='completed')record.path=item.getSavePath();this.downloadHistory.finish(record);if(!this.blocked&&generation===this.sequence)this.publish();});
        this.publish();
      });
    }
    wc.on('page-title-updated',()=>this.publish());
    wc.on('did-navigate',(_event,url)=>{
      if(!pageUrl(url))return;
      const value=new URL(url);const entry={url:value.origin+value.pathname,title:wc.getTitle(),at:Date.now()};
      this.history=[entry,...this.history.filter(item=>item.url!==entry.url)].slice(0,50);this.publish();
    });
    this.tabs.set(id,tab);return tab;
  }
  async select(id,parent){
    if(this.parent!==parent){
      this.parent?.removeListener('closed',this.onClosed);this.parent=parent;
      this.onClosed=()=>{if(this.parent===parent){this.lock();this.parent=null;}};parent.on('closed',this.onClosed);
    }
    for(const [key,tab] of this.tabs)if(key!==id)tab.hide();
    this.active=id;this.lastActive=this.tabs.has(id)?id:null;
    const tab=this.tabs.get(id);if(tab){tab.unlock();await tab.show(parent);}
    this.publish();return this.status();
  }
  hide(){if(this.active)this.lastActive=this.active;for(const tab of this.tabs.values())tab.hide();this.active=null;this.publish();return this.status();}
  mobileState(){
    if(this.blocked)return {tabs:[],active_tab_id:''};
    const tabs=[...this.tabs].filter(([,tab])=>tab.view&&!tab.view.webContents.isDestroyed()).slice(0,24).map(([id,tab])=>{
      const wc=tab.view.webContents;let url='';try{const value=new URL(wc.getURL());if(pageUrl(value.href))url=(value.origin+value.pathname).slice(0,512);}catch{}
      const title=wc.getTitle();return {id,title:(title===wc.getURL()?url:title).slice(0,120)||'Новая вкладка',url};
    });
    return {tabs,active_tab_id:tabs.some(tab=>tab.id===this.active)?this.active:''};
  }
  async prepareAgentTab(parent,url){
    if(this.blocked||!parent||parent.isDestroyed())throw Error('desktop_locked');
    if(this.parent&&this.parent!==parent)throw Error('untrusted_window');
    let id=url?[...this.tabs].find(([,tab])=>pageUrl(tab.view.webContents.getURL())===pageUrl(url))?.[0]||(this.tabs.get(this.active)?.agentOwned?this.active:null):this.active||this.lastActive;
    if(!this.tabs.has(id)){id='agent-'+require('node:crypto').randomUUID();this.create(id,parent);this.tabs.get(id).agentOwned=true;}
    const sequence=this.sequence;
    const shown=await parent.webContents.executeJavaScript('window.IgorekShowAgentBrowser?.('+JSON.stringify(id)+','+JSON.stringify(this.status())+')');
    if(this.blocked||sequence!==this.sequence||shown!==true)throw Error('browser_hidden');
    await this.select(id,parent);
  }
  agent(parent,fields){return this.agentTools.request(parent,fields);}
  async preview(){
    if(this.blocked)return {reason:'desktop_locked'};
    const id=this.active,tab=this.tabs.get(id),wc=tab?.view?.webContents,sequence=this.sequence;
    if(!tab)return {reason:this.tabs.size?'browser_hidden':'browser_closed'};
    if(!wc||wc.isDestroyed())return {reason:'browser_closed'};
    if(tab.blocked)return {reason:'desktop_locked'};
    if(!tab.attached||!this.parent||this.parent.isDestroyed())return {reason:'browser_hidden'};
    const before=wc.getURL();if(!pageUrl(before))return {reason:'preview_failed'};
    let timer;
    try{
      const image=await Promise.race([wc.capturePage(undefined,{stayHidden:true,stayAwake:false}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('preview_timeout')),3000);})]);
      if(this.blocked)return {reason:'desktop_locked'};
      if(sequence!==this.sequence||this.active!==id||this.tabs.get(id)!==tab||tab.view?.webContents!==wc||wc.isDestroyed())return {reason:'browser_changed'};
      if(tab.blocked)return {reason:'desktop_locked'};
      if(!tab.attached||!this.parent||this.parent.isDestroyed())return {reason:'browser_hidden'};
      if(wc.getURL()!==before)return {reason:'browser_changed'};
      if(image.isEmpty())return {reason:'preview_failed'};
      const size=image.getSize();if(!Number.isInteger(size.width)||!Number.isInteger(size.height)||size.width<1||size.height<1)return {reason:'preview_failed'};
      const scale=Math.min(1,960/Math.max(size.width,size.height));
      const scaled=scale<1?image.resize({width:Math.max(1,Math.round(size.width*scale)),height:Math.max(1,Math.round(size.height*scale))}):image;
      const buffer=scaled.toJPEG(60);if(buffer.length>320000)return {reason:'preview_failed'};
      const url=new URL(before);
      return {ready:true,url:url.origin+url.pathname,title:(wc.getTitle()===before?url.origin+url.pathname:wc.getTitle()).slice(0,160),loading:wc.isLoading(),captured_at:Date.now(),...scaled.getSize(),
        frame_id:require('node:crypto').createHash('sha256').update(buffer).digest('hex'),image_base64:buffer.toString('base64')};
    }catch{return {reason:'preview_failed'};}finally{clearTimeout(timer);}
  }
  async openWebRTC(parent){
    if(this.diagnostics&&!this.diagnostics.isDestroyed()){this.diagnostics.focus();return this.status();}
    const ses=this.tabs.get(this.active)?.view?.webContents.session||this.options.session.fromPath(path.join(this.options.root,'web','profile'));
    const target=new this.options.BrowserWindow({parent,title:'WebRTC — статистика соединений',width:1100,height:800,webPreferences:{session:ses,sandbox:true,contextIsolation:true,nodeIntegration:false,webSecurity:true,preload:undefined}});
    this.diagnostics=target;target.setMenuBarVisibility(false);
    target.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    target.webContents.on('will-navigate',(event,url)=>{if(url!=='chrome://webrtc-internals/')event.preventDefault();});
    target.webContents.on('will-redirect',event=>event.preventDefault());
    target.webContents.on('will-attach-webview',event=>event.preventDefault());
    const close=()=>{if(!target.isDestroyed())target.destroy();};parent.once('closed',close);
    target.on('closed',()=>{parent.removeListener('closed',close);if(this.diagnostics===target)this.diagnostics=null;});
    try{await target.loadURL('chrome://webrtc-internals/');}catch(error){close();throw error;}
    return this.status();
  }
  async action(parent,operation,fields={}){
    if(this.blocked){if(['layout','status','hide'].includes(operation))return this.status();throw Error('Разблокируй интерфейс Игорька.');}
    if(!parent||parent.isDestroyed())throw Error('Окно закрыто.');
    if(!fields||Array.isArray(fields)||typeof fields!=='object')throw Error('Некорректный запрос.');
    if(operation==='addFavorite'||operation==='removeFavorite'){
      if(this.parent&&this.parent!==parent)throw Error('Доступ запрещён.');
      this.favorites[operation==='addFavorite'?'add':'remove'](fields);this.publish();return this.status();
    }
    if(operation==='hide'){if(this.parent===parent)this.hide();return this.status();}
    if(operation==='layout'){if(this.parent===parent)await this.tabs.get(this.active)?.layout();return this.status();}
    if(operation==='status')return this.parent===parent?this.status():{tabs:[],history:[],downloads:[]};
    if(operation==='control'){
      if(this.parent&&this.parent!==parent)throw Error('Доступ запрещён.');
      if(!this.requireHandoff||Object.keys(fields).join()!=='manual'||typeof fields.manual!=='boolean')throw Error('Ручная передача управления выключена.');
      this.manual=fields.manual;this.agentTools.manual=fields.manual;this.agentTools.stopping=fields.manual;
      if(!fields.manual){this.agentTools.paused='';this.agentTools.auth='unknown';}
      this.publish();return this.status();
    }
    if(operation==='webrtc'){if(this.parent&&this.parent!==parent)throw Error('Доступ запрещён.');return this.openWebRTC(parent);}
    if(operation==='downloads'){await this.select(null,parent);return this.status();}
    if(operation==='select'){if(fields.id!==null&&!this.tabs.has(fields.id))throw Error('Вкладка закрыта.');return this.select(fields.id,parent);}
    if(operation==='open'){
      if(typeof fields.id!=='string'||!/^[-a-zA-Z0-9]{1,64}$/.test(fields.id)||!pageUrl(fields.url))throw Error('Нужен публичный HTTPS-адрес.');
      let tab=this.tabs.get(fields.id);if(!tab)tab=this.create(fields.id,parent);
      await this.select(fields.id,parent);tab.view.webContents.loadURL(fields.url).catch(()=>this.publish());return this.status();
    }
    if(this.parent!==parent)throw Error('Доступ запрещён.');
    if(operation==='close'){
      const tab=this.tabs.get(fields.id);tab?.lock();this.tabs.delete(fields.id);if(this.active===fields.id)this.active=null;this.publish();return this.status();
    }
    if(operation==='removeDownload'){
      if(typeof fields.id!=='string'||!this.downloads.some(item=>item.id===fields.id))throw Error('Запись загрузки не найдена.');
      this.downloadHistory.remove(fields.id);this.downloads=this.downloadHistory.rows;this.publish();return this.status();
    }
    if(operation==='clearDownloads'){this.downloadHistory.clear();this.downloads=this.downloadHistory.rows;this.publish();return this.status();}
    if(operation==='openDownload'){
      const item=this.downloads.find(value=>value.id===fields.id&&value.state==='completed'&&value.path);
      if(!item)throw Error('Загрузка ещё не завершена.');
      await this.options.shell.showItemInFolder(item.path);return this.status();
    }
    if(operation==='clear'){
      const generation=this.sequence;
      const answer=await this.options.dialog.showMessageBox(parent,{type:'question',title:'Данные браузера',message:'Удалить историю и данные сайтов этой панели?',detail:'Из аккаунтов сайтов потребуется войти заново.',buttons:['Удалить','Отмена'],defaultId:1,cancelId:1});
      if(answer.response!==0||this.blocked||generation!==this.sequence)return this.status();
      this.hide();for(const tab of this.tabs.values())tab.lock();this.tabs.clear();this.history=[];
      for(const ses of this.sessions)await ses.clearStorageData();this.publish();return this.status();
    }
    const tab=this.tabs.get(this.active),wc=tab?.view?.webContents;
    if(!wc||wc.isDestroyed())throw Error('Выбери браузерную вкладку.');
    if(operation==='resetMicrophone'){this.permissions.setMicrophone(false);for(const tab of this.tabs.values())tab.mediaGranted.clear();operation='stopVoice';}
    if(operation==='stopVoice'){
      const id=this.active,url=wc.getURL();tab.lock();this.tabs.delete(id);
      if(pageUrl(url)){const fresh=this.create(id,parent);await this.select(id,parent);fresh.view.webContents.loadURL(url).catch(()=>this.publish());}
      this.publish();return this.status();
    }
    if(['back','forward','reload'].includes(operation))tab.navigate(operation);
    else if(operation==='navigate'){if(!pageUrl(fields.url))throw Error('Нужен публичный HTTPS-адрес.');wc.loadURL(fields.url).catch(()=>this.publish());}
    else if(operation==='zoom'){if(fields.id&&fields.id!==this.active)throw Error('Вкладка изменилась. Открой меню заново.');if(!Number.isFinite(fields.value)||fields.value<0.5||fields.value>2)throw Error('Масштаб от 50 до 200%.');wc.setZoomFactor(fields.value);}
    else if(operation==='find'){if(typeof fields.text!=='string'||fields.text.length>500)throw Error('Некорректный поиск.');if(fields.text)wc.findInPage(fields.text,{forward:fields.forward!==false});else wc.stopFindInPage('clearSelection');}
    else if(operation==='print')wc.print({silent:false});
    else throw Error('Действие недоступно.');
    this.publish();return this.status();
  }
  lock(){this.blocked=true;this.lastActive=null;this.manual=this.requireHandoff;this.agentTools.blocked=true;this.agentTools.stopping=true;this.agentTools.view=null;this.sequence++;if(this.diagnostics&&!this.diagnostics.isDestroyed())this.diagnostics.destroy();this.diagnostics=null;for(const tab of this.tabs.values())tab.lock();this.tabs.clear();this.active=null;this.history=[];this.downloads=[];this.publish();}
}
module.exports={WorkspaceBrowser};
