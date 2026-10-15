import shutil
import subprocess
from pathlib import Path


def test_project_sidebar_pin_and_navigation_contracts():
    node=shutil.which('node')
    assert node
    script=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
class E {
 constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.attrs={};this.dataset={};this.value='';this.open=false;this.hidden=false;this.disabled=false;this._text='';this.classes=new Set();this.classList={contains:x=>this.classes.has(x),add:x=>this.classes.add(x),remove:x=>this.classes.delete(x)};}
 set textContent(value){this._text=String(value);this.children=[];}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
 append(...items){this.children.push(...items);}replaceChildren(...items){this.children=[...items];this._text='';}
 setAttribute(k,v){this.attrs[k]=String(v);}removeAttribute(k){delete this.attrs[k];}
 addEventListener(k,fn){this.listeners[k]=fn;}click(){return this.listeners.click?.({stopPropagation(){},preventDefault(){}});}
 showModal(){this.open=true;}close(){this.open=false;this.listeners.close?.();}
}
const nodes=new Map(),byId=id=>{if(!nodes.has(id))nodes.set(id,new E());return nodes.get(id);};
const element=(tag,cls,text)=>{const n=new E(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const row=new E(),selected=[],messages=[],state={paired:true,selected:'a',sessionTotal:4,sessions:[
 {session_id:'a',title:'First',cwd:'/tmp/a/Project',profile:'demo',updated_at:4,pinned:true},
 {session_id:'b',title:'Second',cwd:'/tmp/a/Project/',profile:'demo',updated_at:3},
 {session_id:'c',title:'Third',cwd:'/tmp/b/Project',profile:'demo',updated_at:2},
 {session_id:'d',title:'Legacy',cwd:'',profile:'demo',updated_at:1}],workspaceView:'main'};
const ui={workspace:new E(),sessions:new E(),sessionCount:new E(),title:new E(),panelButton:new E(),cwd:new E(),message:new E()};
ui.title.textContent='First';ui.message.value='unsent draft';
const context=vm.createContext({state,ui,byId,element,console,Intl,Map,Set,Promise,Error,Number,String,Boolean,Math,JSON,
 t:key=>key,shortDate:()=>'',encode:encodeURIComponent,toast:value=>messages.push(value),selectSession:id=>selected.push(id),
 openNewSession(){},sessionCwdCombo:{setValue(value){context.pickedProject=value;}},startRename(){},loadSessions:async()=>{},intlLocale:()=> 'ru-RU',
 document:{body:new E(),createElement:tag=>new E(tag),querySelector:()=>row},window:{clearTimeout(){},setTimeout(){return 1;},confirm:()=>true},
 api:async(path,options)=>path==='/api/schedules'?{available:true,running:true,schedules:[]}:options?{pinned:JSON.parse(options.body).pinned}:{}});
vm.runInContext(source.slice(source.indexOf('function projectKey('),source.indexOf('function startRename(')),context);
vm.runInContext('renderSessions()',context);
let groups=ui.sessions.children.filter(n=>n.className==='session-project');
assert.equal(groups.length,3);assert.notEqual(groups[0].dataset.project,groups[1].dataset.project);
assert.equal(groups[0].children[0].children[0].title,'/tmp/a/Project');
assert.ok(ui.sessions.children.some(n=>n.textContent==='Закреплённые'));assert.equal(ui.sessionCount.textContent,'4');
groups[0].children[0].children[0].click();
groups=ui.sessions.children.filter(n=>n.className==='session-project');assert.equal(groups[0].children.length,1);
(async()=>{
 await context.toggleSessionPin('b',true,new E());assert.equal(state.sessions[1].pinned,true);assert.deepEqual(selected,[]);
 context.api=async()=>({});await context.toggleSessionPin('c',true,new E());assert.equal(state.sessions[2].pinned,undefined);assert.equal(messages.length,1);
 vm.runInContext(source.slice(source.indexOf('function showMainView('),source.indexOf('async function loadOptions(')),context);
 context.api=async()=>({available:true,running:true,schedules:[]});
 await context.showScheduledView();assert.equal(row.hidden,true);assert.equal(byId('scheduled-view').hidden,false);
 context.showMainView();assert.equal(row.hidden,false);assert.equal(state.selected,'a');assert.equal(ui.message.value,'unsent draft');assert.equal(ui.title.textContent,'First');
 state.schedulesData={available:true,running:true,schedules:[{schedule_id:'one',name:'Daily',payload:'Inspect',profile:'demo',cwd:'/tmp/a',schedule_type:'daily',time_value:'09:00',timezone:'Europe/Moscow',enabled:true,ready:false,last_status:'queued',run_count:1,next_run_at:2000000000,last_session_id:'run-chat'}]};
 context.renderSchedules();assert.match(byId('schedules-list').textContent,/В очереди/);
 const card=byId('schedules-list').children[0],actions=card.children[card.children.length-1];assert.equal(actions.children[2].disabled,true);
 actions.children[actions.children.length-1].click();assert.equal(selected[0],'run-chat');
 console.log('workspace UI contracts PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result=subprocess.run([node,'-e',script,str(Path(__file__).resolve().parents[1]/'factory/web_ui/static/app.js')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
