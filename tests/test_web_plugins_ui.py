import shutil
import subprocess
from pathlib import Path


def test_plugins_page_serializes_actions_and_discards_hidden_replies():
    script=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.value='';this.hidden=false;this.disabled=false;this.dataset={};this.textContent='';this.classList={add(){},remove(){},toggle(){}};}
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(n=>n!==this);this.parentNode=null;}
 append(...items){for(const item of items){item.remove();this.children.push(item);item.parentNode=this;}}replaceChildren(...items){for(const item of [...this.children])item.remove();this.append(...items);}
 setAttribute(){}removeAttribute(){}addEventListener(name,fn){const previous=this.listeners[name];this.listeners[name]=previous?(...args)=>{const result=previous(...args);fn(...args);return result;}:fn;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 querySelectorAll(tag){return this.children.flatMap(c=>[...((tag.startsWith('.')?(c.className||'').split(' ').includes(tag.slice(1)):c.tag===tag)?[c]:[]),...c.querySelectorAll(tag)])}
 click(){if(!this.disabled)return this.listeners.click?.()}
}
const nodes=new Map(),byId=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id)},requests=[],state={paired:true,workspaceView:'main'},ui={title:new E(),panelButton:new E()};
ui.title.textContent='Saved chat';
const window={setTimeout:()=>1,clearTimeout(){},IgorekDesktop:{plugins:(action,fields)=>new Promise(resolve=>requests.push({action,fields,resolve}))}};
const context=vm.createContext({state,ui,window,document:{getElementById:byId,createElement:tag=>new E(tag),querySelector:()=>byId('workspace-row'),body:new E(),hidden:false,hasFocus:()=>true},URL,Date,Promise,console,
 t:key=>key,workspaceGlyph:()=>new E(),renderExtensionsSettings:()=>new E(),renderMcpSettings:()=>new E(),api:async path=>path==='/api/session-options'?{profiles:[],default_profile:''}:{skills:[],mcp_servers:[]},
 showMainView(){window.IgorekPlugins?.hide();state.workspaceView='main';ui.title.textContent='Saved chat'}});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const reply=enabled=>({vault_unlocked:true,profiles:[{name:'fixture-model'}],plugins:{openai_docs:{enabled,check:{ok:true,checked_at:1,links:[{url:'javascript:alert(1)',title:'unsafe'},{url:'https://developers.openai.com/api/docs',title:'Docs'}]}},google_calendar:{enabled:false,connected:false,configured:false}}});
const tick=async()=>{await Promise.resolve();await Promise.resolve()};
(async()=>{
 const first=window.IgorekPlugins.show();assert.equal(requests.length,1);requests[0].resolve(reply(false));await first;
 assert.equal(state.workspaceView,'plugins');assert.equal(byId('plugins-cards').children.length,2);assert.equal(byId('plugins-cards').querySelectorAll('a').length,1);
 byId('plugins-refresh').click();await tick();assert.equal(requests.length,2);
 const connect=byId('plugins-cards').children[0].querySelectorAll('button').find(b=>b.textContent==='Подключить');assert.ok(connect.disabled);connect.click();assert.equal(requests.length,2);
 requests[1].resolve(reply(false));await tick();assert.ok(!byId('plugins-cards').children[0].querySelectorAll('button').find(b=>b.textContent==='Подключить').disabled);
 const button=byId('plugins-cards').children[0].querySelectorAll('button').find(b=>b.textContent==='Подключить'),action=button.click();await tick();assert.equal(requests[2].action,'action');assert.equal(requests[2].fields.operation,'connect');assert.ok(byId('plugins-refresh').disabled);
 window.IgorekPlugins.hide();assert.equal(byId('plugins-cards').children.length,0);state.workspaceView='main';requests[2].resolve(reply(true));await action;assert.equal(byId('plugins-cards').children.length,0);
 const reenter=window.IgorekPlugins.show();assert.equal(requests.length,4);window.IgorekPlugins.hide();state.workspaceView='main';requests[3].resolve(reply(true));await reenter;assert.equal(byId('plugins-cards').children.length,0);
 const active=window.IgorekPlugins.show();requests[4].resolve(reply(true));await active;
 assert.ok(byId('plugins-cards').children[0].querySelectorAll('p').some(p=>p.textContent.includes('Доступ модели не выдан')));
 const permissions=byId('plugins-cards').children[0].querySelectorAll('button').find(b=>b.textContent==='Разрешения модели');const opening=permissions.click();
 assert.equal(requests[5].fields.panel,'mcp');assert.equal(requests[5].fields.plugin,'openai_docs');requests[5].resolve({opened:true});await opening;
 const calendar=byId('plugins-cards').children[1];assert.deepEqual(calendar.querySelectorAll('button').filter(b=>b.textContent!=='⋮').map(b=>b.textContent),['Подключить Google Календарь']);
 assert.ok(calendar.querySelectorAll('p').some(p=>p.textContent.includes('fixture-model')));
 const connecting=calendar.querySelectorAll('button').find(b=>b.textContent==='Подключить Google Календарь').click();assert.equal(requests[6].action,'calendar_connect');assert.equal(requests[6].fields.profile,'fixture-model');requests[6].resolve({opened:true});await connecting;
 assert.ok(byId('plugins-status').textContent.includes('автоматически'));assert.equal(requests.length,7);
 byId('plugins-refresh').click();await tick();const failed=reply(true);failed.plugins.google_calendar.error='calendar_api_disabled';failed.plugins.google_calendar.activation_url='https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=123456789';requests[7].resolve(failed);await tick();
 const link=byId('plugins-cards').children[1].querySelectorAll('a')[0];assert.equal(link.href,failed.plugins.google_calendar.activation_url);assert.equal(link.target,'_blank');
 byId('plugins-refresh').click();await tick();failed.plugins.google_calendar.activation_url='https://evil.test/private';requests[8].resolve(failed);await tick();assert.equal(byId('plugins-cards').children[1].querySelectorAll('a').length,0);
 const retry=byId('plugins-cards').children[1].querySelectorAll('button').find(b=>b.textContent==='Подключить Google Календарь').click();requests[9].resolve({error:'calendar_api_disabled'});await retry;await tick();assert.equal(requests[10].action,'status','API error must refresh the activation link immediately');
 failed.plugins.google_calendar.activation_url='https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=123456789';requests[10].resolve(failed);await tick();assert.equal(byId('plugins-cards').children[1].querySelectorAll('a')[0].href,failed.plugins.google_calendar.activation_url);
 console.log('PASS plugin UI states, safe links, serialized read/action, hidden reply discarded, missing model grant and direct permission shortcut');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result=subprocess.run([shutil.which('node'),'-e',script,str(Path(__file__).resolve().parents[1]/'factory/web_ui/static/plugins.js')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
