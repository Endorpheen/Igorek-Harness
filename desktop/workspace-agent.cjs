const {DesktopBrowser,pageUrl}=require('./browser.cjs');
const {BrowserInteraction}=require('./browser-interaction.cjs');
const path=require('node:path');
class WorkspaceAgent extends DesktopBrowser {
  constructor(manager){
    super({...manager.options,root:path.join(manager.options.root,'agent-tools'),parent:()=>manager.parent});
    this.manager=manager;this.manual=false;this.blocked=true;this.requestQueue=Promise.resolve();
  }
  bind(){
    const tab=this.manager.tabs.get(this.manager.active),view=tab?.view;
    if(view!==this.view){this.interaction=new BrowserInteraction(()=>this.view.webContents,pageUrl,this.sleep);this.paused='';this.auth='unknown';}
    this.view=view||null;this.window=this.manager.parent;this.attached=Boolean(tab?.attached);
    this.blocked=this.manager.blocked;this.manual=this.manager.requireHandoff&&this.manager.manual;
    this.stopping=this.blocked||this.manual;
  }
  ensure(){if(!this.view?.webContents||this.view.webContents.isDestroyed())throw Error('browser_closed');}
  consoleAccess(contents){
    if(this.manager.blocked)return 'desktop_locked';
    if(this.manager.requireHandoff&&this.manager.manual)return 'browser_needs_owner';
    if(this.manager.tabs.get(this.manager.active)?.view?.webContents!==contents)return 'browser_changed';
    return super.consoleAccess(contents);
  }
  status(){
    const manager=this.manager,tabs=[...manager.tabs].filter(([,tab])=>!tab.view.webContents.isDestroyed()).map(([id,tab])=>({id,title:tab.view.webContents.getTitle().slice(0,160)||'Новая вкладка',url:tab.view.webContents.getURL(),active:id===manager.active}));
    const current=tabs.find(tab=>tab.active)||tabs.find(tab=>tab.id===manager.lastActive);
    return {open:tabs.length>0,manual:manager.requireHandoff&&manager.manual,require_handoff:manager.requireHandoff,current_url:current?.url||'',page_title:current?.title||'',tabs,paused:this.paused,auth:this.auth,job:this.job?{id:this.job.id,state:this.job.state,count:this.job.posts.length,reason:this.job.reason}:null};
  }
  request(parent,fields){
    const run=async()=>{
      this.bind();
      if(this.blocked)return {error:'desktop_locked'};
      if(!fields||typeof fields!=='object'||Array.isArray(fields))return {error:'invalid_request'};
      if(fields.action==='status')return super.agent(fields);
      if(this.manual)return {error:'browser_needs_owner',reason:'manual_control'};
      if(fields.action==='open'&&Object.keys(fields).every(key=>['action','url'].includes(key))&&(!('url'in fields)||pageUrl(fields.url))){
        await this.manager.prepareAgentTab(parent,fields.url);this.bind();
        if(this.blocked||this.manual)return {error:this.blocked?'desktop_locked':'browser_needs_owner'};
      }
      if(!this.view&&this.manager.tabs.has(this.manager.lastActive)){await this.manager.prepareAgentTab(parent);this.bind();if(this.blocked||this.manual)return {error:this.blocked?'desktop_locked':'browser_needs_owner'};}
      if(!this.view?.webContents||this.view.webContents.isDestroyed())return {error:'browser_no_active_tab',tabs:this.status().tabs};
      if(this.paused&&!this.manual){const page=await this.read();if(['ready','not_checked'].includes(page.auth)){this.paused='';this.manual=false;}else return {error:'browser_needs_owner',reason:this.paused||'login_required',auth:this.auth};}
      const result=await super.agent(fields);
      this.manager.publish();return result;
    };
    const next=this.requestQueue.then(run).catch(()=>({error:'browser_operation_failed'}));this.requestQueue=next.then(()=>{});return next;
  }
}
module.exports={WorkspaceAgent};
