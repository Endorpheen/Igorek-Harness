from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'node недоступен')
class ApprovalStateRegressionTests(unittest.TestCase):
    def test_reused_tool_call_id_in_another_task_gets_its_own_confirmation(self):
        source=Path(__file__).resolve().parents[1]/'factory/web_ui/static/app.js'
        script=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),source=fs.readFileSync(process.argv[1],'utf8');
const state={items:[]},ctx={state,pauseTask(){},upsert(item){const old=state.items.find(x=>x.id===item.id);if(old)Object.assign(old,item);else state.items.push(item)}};vm.createContext(ctx);
const a=source.indexOf('function handleEvent(event) {'),b=source.indexOf('\nfunction ',a+1);vm.runInContext(source.slice(a,b),ctx);
const required=task=>({kind:'approval.required',task_id:task,payload:{request_id:'call-1',tool:'calendar_create_event'}});
ctx.handleEvent(required('task-A'));ctx.handleEvent({kind:'approval.resolved',task_id:'task-A',payload:{request_id:'call-1',decision:'reject'}});ctx.handleEvent(required('task-B'));
assert.equal(state.items.length,2);assert.equal(state.items[0].resolved,'reject');assert.equal(state.items[1].resolved,undefined);assert.notEqual(state.items[0].id,state.items[1].id);
const start=source.indexOf('const HISTORY_SUB ='),end=source.indexOf('\nasync function loadSessionJournal',start);vm.runInContext(source.slice(start,end),ctx);const history=ctx.newJournalHistory();
ctx.collectJournalHistory(history,{...required('task-A'),seq:1});ctx.collectJournalHistory(history,{...required('task-B'),seq:2});ctx.collectJournalHistory(history,{kind:'approval.resolved',task_id:'task-A',seq:3,payload:{request_id:'call-1',decision:'reject'}});
assert.equal(history.approvals.size,2);const values=[...history.approvals.values()];assert.equal(values[0].resolved,'reject');assert.equal(values[1].resolved,undefined);
'''
        result=subprocess.run([shutil.which('node'),'-e',script,str(source)],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_live_resolution_and_stale_button_update_current_card(self):
        source = Path(__file__).resolve().parents[1] / 'factory/web_ui/static/app.js'
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync(process.argv[1],'utf8');let posts=0;
const state={items:[{id:'approval-first',type:'approval',request_id:'first',task_id:'task',tool:'Write'},{id:'approval-other',type:'approval',request_id:'other',task_id:'task',tool:'Edit'}]};
const ctx={state,api:async()=>{posts++;},encode:encodeURIComponent,t:x=>x,toast:()=>{},upsert:item=>{const index=state.items.findIndex(row=>row.id===item.id);state.items[index]={...state.items[index],...item};}};
vm.createContext(ctx);for(const name of ['handleEvent(event)','decideApproval(item, decision)']){const start=src.indexOf('function '+name);const end=src.indexOf('\nfunction ',start+1);const code=src.slice(start,end);vm.runInContext(name.startsWith('decide')?'async '+code:code,ctx);}
const event=(id,decision)=>ctx.handleEvent({kind:'approval.resolved',task_id:'task',payload:{request_id:id,decision}});
event('first','allow_once');assert.equal(state.items[0].resolved,'allow_once');assert.equal(state.items[1].resolved,undefined);
event('missing','allow_once');assert.equal(state.items.length,2);event('other','invalid');assert.equal(state.items[1].resolved,undefined);event('other','reject');assert.equal(state.items[1].resolved,'reject');
(async()=>{const stale={...state.items[0],resolved:undefined};state.items[0]={...state.items[0],reason:'updated'};await ctx.decideApproval(stale,'allow_session');assert.equal(state.items[0].resolved,'allow_session');assert.equal(state.items[0].reason,'updated');assert.equal(stale.resolved,undefined);assert.equal(posts,1);})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        subprocess.run([shutil.which('node'), '-e', script, str(source)], check=True, timeout=15)
