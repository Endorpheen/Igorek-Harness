import shutil
import subprocess
from pathlib import Path


def test_files_refresh_after_committed_success_and_reject_stale_replies():
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'),requests=[],notices=[],rows=new Map();
const state={paired:true,selected:'session',panel:{workspace:{resetSession(){}},open:true,tab:'files',files:{status:'ready',data:{files:[]}}},
 finishNotes:new Map(),finishedTasks:new Set(),activeTasks:new Set(),textStreamRounds:new Map(),textStreams:new Map(),lateDeltas:new Map(),reasoningStreams:new Map(),items:[],subagents:new Map()};
const context=vm.createContext({state,Date,Map,Set,Number,String,Promise,Boolean,console,
 encode:encodeURIComponent,toMillis:value=>Number(value)||0,renderPanel(){},toast:error=>notices.push(error),finishSuffix:()=>'',
 upsert:row=>rows.set(row.id,row),setIndicator(){},setRuntime(){},loadSessions:async()=>{},
 activateWorkspaceTab:async()=>{},closeFileViewerSession(){},unmountTerminal(){},resetPlanState(){},
 api:path=>new Promise((resolve,reject)=>requests.push({path,resolve,reject}))});
for(const [start,end] of [['function finishTask(', 'function knownSubagentFor('],
 ['function refreshPanel(', 'async function loadChanges('],['async function loadFiles(', 'async function loadCheckpoints('],
 ['function resetPanelData(', 'function openPanel(']]) {
 const begin=source.indexOf(start),finish=source.indexOf(end,begin);assert.ok(begin>=0&&finish>begin);
 vm.runInContext(source.slice(begin,finish),context);
}
const tick=async()=>{await Promise.resolve();await Promise.resolve()};
(async()=>{
 context.handleEvent({kind:'task.finish',task_id:'fixture-task',payload:{status:'ok'}});
 assert.equal(requests.length,1);requests[0].resolve({files:[]});await tick();
 context.handleEvent({kind:'task.status',task_id:'fixture-task',payload:{status:'ok'}});
 assert.equal(requests.length,2,'committed task.status(ok) must re-fetch files');
 requests[1].resolve({files:[{name:'existing-sheet.xlsx',id:'updated'}]});await tick();
 assert.equal(state.panel.files.data.files[0].name,'existing-sheet.xlsx');assert.equal(rows.size,1);
 const old=context.loadFiles('session'),latest=context.loadFiles('session');
 requests[3].resolve({files:[{name:'latest.xlsx'}]});await latest;
 requests[2].resolve({files:[]});await old;
 assert.equal(state.panel.files.data.files[0].name,'latest.xlsx','old response must not overwrite new files');
 const rejected=context.loadFiles('session'),success=context.loadFiles('session');
 requests[5].resolve({files:[{name:'success.xlsx'}]});await success;
 requests[4].reject(Error('old request failed'));await rejected;
 assert.equal(state.panel.files.status,'ready');assert.equal(notices.length,0);
 state.panel.open=false;
 const hidden=context.loadFiles('session');
 context.handleEvent({kind:'task.status',task_id:'second-task',payload:{status:'ok'}});
 requests[6].resolve({files:[]});await hidden;
 assert.equal(state.panel.files.status,'idle','completion must invalidate a closed panel cache');
 state.panel.open=true;context.refreshPanel(false);assert.equal(requests.length,8);
 requests[7].resolve({files:[{name:'after-closed-panel.xlsx'}]});await tick();
 const previous=context.loadFiles('session');
 state.selected='other';context.resetPanelData();state.selected='session';context.resetPanelData();
 requests[8].resolve({files:[{name:'old-session-reply.xlsx'}]});await previous;
 assert.equal(state.panel.files.status,'idle');assert.equal(state.panel.files.data,null);
 console.log('PASS committed status refresh, existing file, reordered responses, stale errors, hidden panel and session reset');
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    node = shutil.which('node')
    assert node
    result = subprocess.run([node, '-e', script, str(Path(__file__).resolve().parents[1] / 'factory/web_ui/static/app.js')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
