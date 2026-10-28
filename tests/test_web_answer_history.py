from pathlib import Path
import shutil
import subprocess


def test_resumed_round_keeps_answer_at_end_and_survives_journal_rebuild():
    source = Path(__file__).resolve().parents[1] / 'factory/web_ui/static/app.js'
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const state={items:[],textStreamRounds:new Map(),textStreams:new Map(),closedStreams:new Map(),lateDeltas:new Map(),knownAssistantText:new Set(),finishedTasks:new Set()};
const ctx={releaseAttachmentPreviews(){},state,setRuntime(){},renderTimeline(){},noteUsage(){},
 upsert(item){const i=state.items.findIndex(x=>x.id===item.id);if(i<0)state.items.push(item);else state.items[i]=item;}};
vm.createContext(ctx);
const start=source.indexOf('function handleEvent(event) {'),end=source.indexOf('\nfunction ',start+1);
vm.runInContext(source.slice(start,end),ctx);
const first={seq:1394,kind:'model.round_response',task_id:'fixture-task',ts:10,payload:{round:1,text:'Проверяю документацию.'}};
const final={seq:1487,kind:'model.round_response',task_id:'fixture-task',ts:20,payload:{round:1,text:'Полный окончательный ответ со ссылкой.'}};
ctx.handleEvent(first);
state.items.push({id:'approval-1',type:'approval'},{id:'tool-after-resume',type:'tool'});
ctx.handleEvent({kind:'model.text_delta',task_id:'fixture-task',payload:{text:final.payload.text}});
ctx.handleEvent(final);
assert.equal(state.items.at(-1).text,final.payload.text,'resumed final answer must stay at the bottom, not replace the first preamble');
assert.equal(state.items.filter(x=>x.type==='assistant').length,2);
assert.equal(new Set(state.items.map(x=>x.id)).size,state.items.length);
ctx.handleEvent(final);assert.equal(state.items.filter(x=>x.text===final.payload.text).length,1,'replayed durable frame must not duplicate the answer');
ctx.handleEvent({seq:1495,kind:'model.round_response',task_id:'other-task',payload:{round:1,text:'Другой ответ'}});
assert.equal(state.items.at(-1).text,'Другой ответ');
Object.assign(ctx,{isolationStarting:()=>false,toMillis:x=>Number(x)*1000,localClock:()=>'',formatDuration:()=>'',sessionMessageItems:()=>[]});
const historyStart=source.indexOf('const HISTORY_SUB ='),historyEnd=source.indexOf('\nasync function loadSessionJournal',historyStart);
vm.runInContext(source.slice(historyStart,historyEnd),ctx);
const history=ctx.newJournalHistory();ctx.collectJournalHistory(history,first);ctx.collectJournalHistory(history,final);
let entries=ctx.buildHistoryEntries({messages:[]},history);
assert.equal(entries.filter(x=>x.type==='assistant').length,2);assert.equal(new Set(entries.map(x=>x.id)).size,entries.length,'journal replay must not create duplicate DOM identities after approvals');
assert.equal(entries.at(-1).text,final.payload.text);
entries=ctx.buildHistoryEntries({messages:[{role:'assistant',text:final.payload.text,task_id:'fixture-task',ts:20}]},history);
assert.equal(entries.filter(x=>x.text===final.payload.text).length,1,'persisted final message and journal round must merge');
assert.equal(entries.at(-1).text,final.payload.text);
console.log('PASS approval resume same round, final answer order, durable dedup, reopened journal and saved history merge');
'''
    result = subprocess.run([shutil.which('node'), '-e', script, str(source)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_session_refresh_waits_for_authoritative_messages_and_ignores_stale_journal():
    source = Path(__file__).resolve().parents[1] / 'factory/web_ui/static/app.js'
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
function fn(name){const start=source.indexOf('async function '+name+'(');const end=source.indexOf('\nfunction ',start+1);return source.slice(start,end);}
(async()=>{
 const state={selected:'old',session:{meta:{session_id:'old'}},selectGeneration:0,items:[],panel:{open:true},sessions:[],
  seenSeq:new Set(),textStreamRounds:new Map(),textStreams:new Map(),reasoningStreams:new Map(),toolItems:new Map(),knownAssistantText:new Set(),closedStreams:new Map(),lateDeltas:new Map(),activeTasks:new Set(),finishedTasks:new Set(),startedTasks:new Set(),taskStarts:new Map(),finishNotes:new Map()};
 const goalUpdates=[];
 const ctx={releaseAttachmentPreviews(){},state,goalPanel:{setGoal(goal){goalUpdates.push(goal)}},window:{dispatchEvent(){},IgorekVoice:{onSessionChanged(){}}},Event:function(){},
  ui:{welcome:{},conversation:{},timeline:{replaceChildren(){}},composer:{},title:{}},t:x=>x,encode:x=>x,
  api:async path=>path.endsWith('/cursor')?{seq:7}:{session_id:'new',meta:{session_id:'new',profile:'codexpro'},messages:[],goal:{objective:'new session goal'}},
  refreshPanel(){assert.equal(state.session.meta.session_id,state.selected,'journal started before the new messages were loaded');},
  renderContext(){},renderTimeline(){},renderSessions(){},renderAgentNavigation(){},setRuntime(){},
  resetPanelData(){},stopStream(){},stopMic(){},showMainView(){},resetMentionState(){},setIndicator(){},renderAttachments(){},clearMessageRail(){},
  document:{body:{classList:{remove(){}}}},sessionMessageItems:()=>[],loadApprovals:async()=>{},startStream(){},loadSessionJournal:async()=>{},loadSubagents:async()=>{},loadOpenCodeUsage:async()=>{},loadSessions:async()=>{},toast(){throw Error('unexpected select error')}};
 vm.createContext(ctx);vm.runInContext(fn('followDesktopProfile'),ctx);vm.runInContext(fn('selectSession'),ctx);await ctx.selectSession('new');
 assert.equal(goalUpdates[0],null);assert.equal(goalUpdates[1].objective,'new session goal');
 let finish,applied=0,scanned=0;state.selected='same';state.plan={loading:false,status:'idle'};
 Object.assign(ctx,{JOURNAL_PAGE_LIMIT:500,newJournalHistory:()=>({}),renderPlanSection(){},api:()=>new Promise(r=>finish=r),
  scanJournalEvent(){scanned++},collectJournalHistory(){scanned++},buildHistoryEntries:()=>[],applyHistoryEntries(){applied++}});
 vm.runInContext(fn('loadSessionJournal'),ctx);const pending=ctx.loadSessionJournal('same');
 const newer=state.plan={loading:false,status:'ready',usage:{}};finish({events:[]});await pending;
 assert.equal(applied,0);assert.equal(scanned,0);assert.equal(newer.status,'ready');
 console.log('PASS session snapshot precedes replay; stale journal cannot overwrite reopened history');
})().catch(error=>{console.error(error);process.exit(1)});
'''
    result = subprocess.run([shutil.which('node'), '-e', script, str(source)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
