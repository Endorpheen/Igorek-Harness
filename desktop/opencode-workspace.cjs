const fs=require('node:fs');
const path=require('node:path');
const {ChatGPTWorkspace}=require('./chatgpt-workspace.cjs');
const {pageUrl}=require('./browser.cjs');

class OpenCodeWorkspace extends ChatGPTWorkspace{
  constructor(options){
    super({...options,startURL:'',surfaceSelector:'#workspace-opencode-surface',urlAllowed:()=>false});
    this.service=options.service;this.navigation=path.join(options.root,'navigation.json');this.path='/';
    this.manualService=options.service;this.manualNavigation=this.navigation;
    try{const saved=JSON.parse(fs.readFileSync(this.navigation,'utf8'));if(typeof saved.path==='string'&&saved.path.startsWith('/')&&!saved.path.startsWith('//')&&!saved.path.startsWith('/auth/'))this.path=saved.path;}catch{}
    this.urlAllowed=raw=>this.allowed(raw);
    this.service.on('state',()=>this.notify());
  }
  allowed(raw){try{const url=new URL(raw);return !url.username&&!url.password&&url.origin===this.service.origin;}catch{return false;}}
  useService(service, route=null){
    if(service!==this.service){
      const blocked=this.blocked;
      this.routes??=new Map();this.routes.set(this.service,this.path);this.lock();
      this.service=service;this.path=this.routes.get(service)||'/';this.pageError='';
      this.navigation=service===this.manualService?this.manualNavigation:path.join(service.root,'navigation.json');
      service.on('state',()=>{if(this.service===service)this.notify();});if(!blocked)this.unlock();
    }
    if(route){this.path=route;const wc=this.view?.webContents;if(wc&&!wc.isDestroyed())wc.loadURL(this.service.origin+route).catch(()=>{});}
  }
  status(){return {...this.service.status(),...(this.pageError?{state:'error',error:this.pageError}:{}),visible:this.attached&&!this.blocked,loading:this.view?.webContents.isLoading()||false};}
  notify(){const parent=this.parent||this.statusWindow;if(parent&&!parent.isDestroyed())parent.webContents.send('igorek:opencode-state',this.status());}
  configurePermissions(session){
    session.setPermissionCheckHandler(()=>false);
    session.setPermissionRequestHandler((_sender,_permission,callback)=>callback(false));
    session.setDevicePermissionHandler?.(()=>false);
  }
  pageFailed(){
    if(this.pageError)return;
    this.pageError='Интерфейс OpenCode остановился. Нажми «Подключиться».';
    this.statusWindow=this.parent;this.lock();this.blocked=false;this.notify();
  }
  secure(contents,session){
    const permitted=raw=>{try{const url=new URL(raw);if(['ws:','wss:'].includes(url.protocol))url.protocol=url.protocol==='ws:'?'http:':'https:';return this.allowed(url.href);}catch{return false;}};
    session.webRequest.onBeforeRequest((details,callback)=>callback({cancel:!permitted(details.url)}));
    session.webRequest.onBeforeSendHeaders((details,callback)=>{
      if(!permitted(details.url))return callback({cancel:true});
      const headers={...details.requestHeaders};for(const key of Object.keys(headers))if(key.toLowerCase()==='authorization')delete headers[key];
      headers.Authorization='Bearer '+this.service.token;callback({requestHeaders:headers});
    });
    session.on('will-download',event=>event.preventDefault());
    const external=async raw=>{
      const url=pageUrl(raw),generation=this.generation;
      if(!url||this.blocked||!this.attached)return;
      const reply=await this.dialog.showMessageBox(this.parent,{type:'question',title:'Ссылка из OpenCode',message:'Открыть '+new URL(url).hostname+' в браузере?',buttons:['Открыть','Отмена'],defaultId:1,cancelId:1});
      if(reply.response===0&&!this.blocked&&this.attached&&generation===this.generation)await this.shell.openExternal(url);
    };
    contents.on('will-navigate',(event,url)=>{if(!this.allowed(url)){event.preventDefault();external(url).catch(()=>{});}});
    contents.on('will-redirect',(event,url)=>{if(!this.allowed(url))event.preventDefault();});
    contents.on('will-attach-webview',event=>event.preventDefault());
    contents.setWindowOpenHandler(({url})=>{external(url).catch(()=>{});return {action:'deny'};});
    for(const name of ['did-navigate','did-navigate-in-page'])contents.on(name,(_event,raw)=>{
      if(!this.allowed(raw))return;const url=new URL(raw);
      if(url.pathname.startsWith('/auth/'))return;
      this.path=url.pathname+url.search+url.hash;
      fs.mkdirSync(path.dirname(this.navigation),{recursive:true,mode:0o700});fs.writeFileSync(this.navigation+'.tmp',JSON.stringify({path:this.path}),{mode:0o600});fs.renameSync(this.navigation+'.tmp',this.navigation);
    });
    contents.on('render-process-gone',()=>this.pageFailed());
    const health=setInterval(()=>{if(!contents.isDestroyed()&&contents.isCrashed?.())this.pageFailed();},1000);
    health.unref();contents.once('destroyed',()=>clearInterval(health));
    contents.on('did-fail-load',(_event,code)=>{if(code!==-3){this.pageError='Не удалось загрузить OpenCode. Нажми «Подключиться».';this.hide();this.notify();}});
  }
  async show(parent){
    if(this.blocked)return {visible:false};
    const generation=this.generation,request=this.showRequest=(this.showRequest||0)+1;
    const origin=await this.service.start();
    if(this.blocked||generation!==this.generation||request!==this.showRequest||parent.isDestroyed())return {visible:false};
    this.startURL=origin+this.path;
    const reload=Boolean(this.pageError);this.pageError='';
    if(this.view?.webContents.isDestroyed())this.view=null;
    const result=await super.show(parent);
    if(reload&&this.view&&!this.view.webContents.isDestroyed())this.view.webContents.loadURL(this.startURL).catch(()=>{});
    return result;
  }
  hide(){this.showRequest=(this.showRequest||0)+1;return super.hide();}
}
module.exports={OpenCodeWorkspace};
