from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "node недоступен")
class WebReasoningRegressionTests(unittest.TestCase):
    def test_live_chunks_preserve_full_text_and_match_saved_history(self):
        source = Path(__file__).resolve().parents[1] / "factory/web_ui/static/app.js"
        script = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('function handleEvent(event) {');
const end = source.indexOf('\nfunction ', start + 1);
const state = {items: [], reasoningStreams: new Map(), finishedTasks: new Set(),
  textStreamRounds: new Map(),textStreams: new Map(), closedStreams: new Map(), lateDeltas: new Map()};
const context = {state, toMillis: n => Number(n) * 1000, setRuntime: () => {},
  reasoningItem: id => state.items.find(item => item.id === 'reasoning-' + id),
  upsert: item => {const old = state.items.find(row => row.id === item.id);
    if (old) Object.assign(old, item); else state.items.push(item);}};
vm.createContext(context); vm.runInContext(source.slice(start, end), context);
const send = (kind, text, task = 'task') => context.handleEvent({kind, task_id: task, ts: 10, payload: {text}});
const text = task => context.reasoningItem(task).text;
const first = 'Начало рассуждений. '.repeat(500), tail = 'Конец рассуждений.';
send('model.reasoning_delta', first); send('model.reasoning', first);
assert.equal(text('task'), first);
send('model.reasoning_delta', tail); assert.equal(text('task'), first + tail);
send('model.reasoning', tail); send('model.text_delta', 'Ответ');
assert.equal(text('task'), first + tail);
assert.equal(context.reasoningItem('task').streaming, false);
send('model.reasoning', first, 'history'); send('model.reasoning', tail, 'history');
assert.equal(text('task'), text('history'));
send('model.reasoning_delta', 'одна ', 'single'); send('model.reasoning_delta', 'порция', 'single');
send('model.reasoning', 'одна порция', 'single'); assert.equal(text('single'), 'одна порция');
send('model.reasoning_delta', 'без итогового текста', 'empty'); send('model.reasoning', '', 'empty');
assert.equal(text('empty'), 'без итогового текста');
state.finishedTasks.add('task'); send('model.reasoning_delta', 'лишнее');
assert.equal(text('task'), first + tail);
'''
        subprocess.run([shutil.which("node"), "-e", script, str(source)], check=True, timeout=15)

    def test_timeline_retains_existing_nodes_without_replaying_entry_animation(self):
        source = Path(__file__).resolve().parents[1] / "factory/web_ui/static/app.js"
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Node {
 constructor(){this.childNodes=[];this.parentNode=null;this.style={};this.moves=0;this.updates=0;}
 get firstChild(){return this.childNodes[0]||null;} get lastChild(){return this.childNodes.at(-1)||null;}
 get nextSibling(){return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this)+1]||null;}
 remove(){if(this.parentNode){const p=this.parentNode;p.childNodes.splice(p.childNodes.indexOf(this),1);this.parentNode=null;}}
 append(n){this.insertBefore(n,null);}
 insertBefore(n,c){n.remove();this.childNodes.splice(c?this.childNodes.indexOf(c):this.childNodes.length,0,n);n.parentNode=this;n.moves++;}
}
const timeline=new Node(),state={items:[],activeTasks:new Set()},ui={timeline,indicator:{}};
const ctx={window:{},state,ui,document:{createDocumentFragment:()=>new Node()},element:()=>new Node(),statusLineText:()=>'',stickToBottom:()=>{},buildMessageRail:()=>{}};
const render=(item)=>{const n=new Node();ctx.registerTimelineUpdater(n,(next)=>{n.updates++;n.updatedItem=next;});return n;};
Object.assign(ctx,{renderMessage:render,renderTool:render,renderApproval:render,renderReasoning:render,renderCompaction:render});
vm.createContext(ctx);const src=fs.readFileSync(process.argv[1],'utf8'),start=src.indexOf('const timelineNodes ='),end=src.indexOf('\nfunction copyMessage',start);vm.runInContext(src.slice(start,end),ctx);
state.items=[{id:'user',type:'user',text:'hello'}];ctx.renderTimeline();const original=timeline.firstChild,moves=original.moves;
state.items.push({id:'tool',type:'tool',status:'running'});ctx.renderTimeline();assert.equal(timeline.firstChild,original);assert.equal(original.moves,moves);
const tool=timeline.lastChild;
state.items[1].status='done';ctx.renderTimeline();
assert.equal(timeline.firstChild,original);
assert.equal(timeline.lastChild,tool);
assert.equal(tool.updates,1);
assert.equal(tool.updatedItem.status,'done');
assert.equal(tool.style.animation,undefined);
assert.equal(original.moves,moves);
state.items=[];ctx.renderTimeline();assert.equal(timeline.childNodes.length,0);
'''
        subprocess.run([shutil.which("node"), "-e", script, str(source)], check=True, timeout=15)

    def test_timeline_adopts_stream_node_for_committed_round(self):
        source = Path(__file__).resolve().parents[1] / "factory/web_ui/static/app.js"
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Node {
 constructor(){this.childNodes=[];this.parentNode=null;this.style={};this.moves=0;this.updates=0;}
 get firstChild(){return this.childNodes[0]||null;} get lastChild(){return this.childNodes.at(-1)||null;}
 get nextSibling(){return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this)+1]||null;}
 remove(){if(this.parentNode){const p=this.parentNode;p.childNodes.splice(p.childNodes.indexOf(this),1);this.parentNode=null;}}
 append(n){this.insertBefore(n,null);}
 insertBefore(n,c){n.remove();this.childNodes.splice(c?this.childNodes.indexOf(c):this.childNodes.length,0,n);n.parentNode=this;n.moves++;}
}
const timeline=new Node(),state={items:[],activeTasks:new Set()},ui={timeline,indicator:{}};
const ctx={window:{},state,ui,document:{createDocumentFragment:()=>new Node()},element:()=>new Node(),statusLineText:()=>'',stickToBottom:()=>{},buildMessageRail:()=>{}};
const render=(item)=>{const n=new Node();ctx.registerTimelineUpdater(n,(next)=>{n.updates++;n.updatedItem=next;});return n;};
Object.assign(ctx,{renderMessage:render,renderTool:render,renderApproval:render,renderReasoning:render,renderCompaction:render});
vm.createContext(ctx);const src=fs.readFileSync(process.argv[1],'utf8'),start=src.indexOf('const timelineNodes ='),end=src.indexOf('\nfunction copyMessage',start);vm.runInContext(src.slice(start,end),ctx);
state.items=[{id:'stream-task',type:'assistant',text:'частичный ответ',streaming:true}];ctx.renderTimeline();
const streamed=timeline.lastChild;
state.items=[{id:'assistant-task-1',type:'assistant',text:'частичный ответ и продолжение',ts:10,adoptFrom:'stream-task'}];ctx.renderTimeline();
assert.equal(timeline.childNodes.length,1);
assert.equal(timeline.lastChild,streamed);
assert.equal(streamed.style.animation,'none');
assert.equal(streamed.updatedItem.id,'assistant-task-1');
assert.equal(streamed.updatedItem.text,'частичный ответ и продолжение');
assert.equal(state.items[0].adoptFrom,undefined);
'''
        subprocess.run([shutil.which("node"), "-e", script, str(source)], check=True, timeout=15)

    def test_timeline_coalesces_renders_to_one_frame(self):
        source = Path(__file__).resolve().parents[1] / "factory/web_ui/static/app.js"
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
class Node {
 constructor(){this.childNodes=[];this.parentNode=null;this.style={};this.moves=0;this.updates=0;}
 get firstChild(){return this.childNodes[0]||null;} get lastChild(){return this.childNodes.at(-1)||null;}
 get nextSibling(){return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this)+1]||null;}
 remove(){if(this.parentNode){const p=this.parentNode;p.childNodes.splice(p.childNodes.indexOf(this),1);this.parentNode=null;}}
 append(n){this.insertBefore(n,null);}
 insertBefore(n,c){n.remove();this.childNodes.splice(c?this.childNodes.indexOf(c):this.childNodes.length,0,n);n.parentNode=this;n.moves++;}
}
const timeline=new Node(),state={items:[],activeTasks:new Set()},ui={timeline,indicator:{}};
const frames=[];
let rendered=0;
const ctx={window:{},state,ui,document:{createDocumentFragment:()=>new Node()},element:()=>new Node(),statusLineText:()=>'',stickToBottom:()=>{},buildMessageRail:()=>{}};
const render=(item)=>{rendered++;const n=new Node();ctx.registerTimelineUpdater(n,()=>{});return n;};
Object.assign(ctx,{renderMessage:render,renderTool:render,renderApproval:render,renderReasoning:render,renderCompaction:render});
vm.createContext(ctx);const src=fs.readFileSync(process.argv[1],'utf8'),start=src.indexOf('const timelineNodes ='),end=src.indexOf('\nfunction copyMessage',start);vm.runInContext(src.slice(start,end),ctx);
ctx.window={requestAnimationFrame:(cb)=>{frames.push(cb);return frames.length;}};
state.items=[{id:'user',type:'user',text:'hello'}];
ctx.renderTimeline();ctx.renderTimeline();
assert.equal(frames.length,1);
assert.equal(rendered,0);
frames.shift()();
assert.equal(rendered,1);
        ctx.renderTimeline();assert.equal(frames.length,1);
'''
        subprocess.run([shutil.which("node"), "-e", script, str(source)], check=True, timeout=15)

    def test_queued_message_force_and_cancel_call_bridge_endpoints(self):
        source = Path(__file__).resolve().parents[1] / "factory/web_ui/static/app.js"
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync(process.argv[1],'utf8');
const start=src.indexOf('function queuedMessageId');
const end=src.indexOf('\nconst RAIL_MIN_MESSAGES',start);
const calls=[];
const state={selected:'chat-1',items:[{id:'msg-m1',type:'user',queued:true,task_id:'task-1',message_id:'m1'}]};
const ctx={window:{},state,encode:(s)=>String(s),toast:(m)=>{ctx.toastMessage=m;},
  renderTimeline:()=>{ctx.rendered=(ctx.rendered||0)+1;},
  api:async(path,opts)=>{calls.push({path,opts});return {};}};
vm.createContext(ctx);vm.runInContext(src.slice(start,end),ctx);
(async()=>{
  const item=state.items[0];
  await ctx.forceQueuedMessage(item,{disabled:false});
  assert.equal(calls[0].path,'/api/sessions/chat-1/messages/m1/send-now');
  assert.equal(calls[0].opts.method,'POST');
  assert.equal(calls[0].opts.body,'{"task_id":"task-1"}');
  assert.equal(item.prioritized,true);
  await ctx.cancelQueuedMessage(item,{disabled:false});
  assert.equal(calls[1].path,'/api/sessions/chat-1/messages/m1/cancel');
  assert.equal(calls[1].opts.body,'{"task_id":"task-1"}');
  assert.equal(state.items.length,0);
  assert.equal(ctx.rendered,2);
  calls.length=0;
  await ctx.forceQueuedMessage({id:'msg-x',message_id:'x'},{disabled:false});
  assert.equal(calls.length,0);
  assert.equal(ctx.queuedMessageId({id:'msg-abc'}),'abc');
  console.log('ok');
})().catch((error)=>{console.error(error);process.exit(1);});
'''
        subprocess.run([shutil.which("node"), "-e", script, str(source)], check=True, timeout=15)
