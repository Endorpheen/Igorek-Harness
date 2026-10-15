from pathlib import Path
import shutil
import subprocess


def test_chat_start_confirmation_preserves_draft_and_sends_once():
    script=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class E{constructor(){this.children=[];this.listeners={};this.open=false;this.disabled=false;this.value='';this.style={};}append(...v){this.children.push(...v)}setAttribute(){}addEventListener(k,v){this.listeners[k]=v}showModal(){this.open=true}close(){this.open=false}focus(){}click(){if(!this.disabled)return this.listeners.click?.()}}
const body=new E(),events={},calls=[],posts=[],state={selected:'session',session:{meta:{profile:'fixture'}},attachments:[],sending:false,pinned:false},ui={message:new E(),send:new E()};
let running=false,startReply=null,statusError=null;
const window={addEventListener:(k,v)=>events[k]=v,IgorekDesktop:{agent:async(operation,profile)=>{calls.push({operation,profile});if(operation==='status')return statusError?{error:statusError}:{agent:{running,profile:running?'fixture':''}};if(startReply)return startReply();running=true;return {agent:{running:true,profile}}},mcp:async()=>({opened:true})}};
const ctx={releaseAttachmentPreviews(){},window,document:{body,createElement:()=>new E()},state,ui,encode:encodeURIComponent,crypto:{randomUUID:()=> 'fixture-id'},planModeActive:()=>false,trackedSkillIds:()=>[],api:async(path,options)=>{posts.push({path,payload:JSON.parse(options.body)});return {task_id:'task'}},renderAttachments(){},mentionModel:()=>({skillsTracked:{clear(){}}}),closeMentionMenu(){},setIndicator(){},isolationStarting:()=>false,setRuntime(){},loadSessions:async()=>{},toast(message){throw Error(message)}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
const source=fs.readFileSync(process.argv[2],'utf8'),a=source.indexOf('async function sendMessage(event) {'),b=source.indexOf('\nconst MIC_ERROR_KEYS',a);vm.runInContext(source.slice(a,b),ctx);
const dialog=body.children[0],confirm=dialog.children[3].children[1],cancel=dialog.children[3].children[0],tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
const send=()=>ctx.sendMessage({preventDefault(){}});
(async()=>{
 ui.message.value='Сохранённый черновик';const cancelled=send();await tick();assert.ok(dialog.open);assert.equal(posts.length,0);cancel.click();await cancelled;assert.equal(ui.message.value,'Сохранённый черновик');assert.equal(calls.filter(c=>c.operation==='start').length,0);
 const accepted=send();await tick();assert.ok(dialog.open);await confirm.click();await accepted;assert.equal(posts.length,1);assert.equal(posts[0].payload.text,'Сохранённый черновик');assert.equal(ui.message.value,'');assert.equal(calls.filter(c=>c.operation==='start').length,1);
 ui.message.value='Следующий запрос';await send();assert.equal(posts.length,2);assert.ok(!dialog.open);assert.equal(calls.filter(c=>c.operation==='start').length,1);
 running=false;startReply=async()=>({error:'agent_busy'});ui.message.value='Черновик при ошибке';const failed=send();await tick();await confirm.click();assert.ok(dialog.open);assert.ok(dialog.children[2].textContent.includes('Дождитесь'));assert.equal(posts.length,2);cancel.click();await failed;assert.equal(ui.message.value,'Черновик при ошибке');
 let release;startReply=()=>new Promise(resolve=>release=resolve);const stale=send();await tick();const starting=confirm.click();await tick();state.selected='other';events['igorek-session-changed']();release({agent:{running:true,profile:'fixture'}});await starting;await stale;assert.equal(posts.length,2);assert.equal(ui.message.value,'Черновик при ошибке');
 statusError='vault_locked';startReply=null;state.selected='session';const locked=send();await tick();assert.ok(dialog.open);assert.equal(confirm.textContent,'Открыть настройки');await confirm.click();await locked;assert.equal(posts.length,2);assert.equal(ui.message.value,'Черновик при ошибке');
 console.log('PASS chat startup confirm/cancel/error/already-running/session-change/locked; draft preserved and one send');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([shutil.which('node'),'-e',script,str(root/'factory/web_ui/static/session-agent.js'),str(root/'factory/web_ui/static/app.js')],text=True,capture_output=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
