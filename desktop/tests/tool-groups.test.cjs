const {test} = require('node:test');
const assert = require('node:assert/strict');
const groups = require('../../factory/web_ui/static/tool-groups.js');
const tool = (id, task='t', status='done') => ({id:'tool-'+task+'-'+id,type:'tool',task_id:task,tool_use_id:id,name:'Edit',status,input:{file_path:'/workspace/'+id}});
const approval = (id, task='t', resolved='allow_once') => ({id:'approval-'+task+'-'+id,type:'approval',task_id:task,request_id:id,tool:'Edit',resolved,target:'/workspace/'+id});

test('consecutive tools and approval status lines collapse between assistant messages without changing stored events',()=>{
  const items=[{id:'intro',type:'assistant',text:'Начинаю'},tool('a'),tool('b'),approval('a'),{id:'pause',type:'status',task_id:'t',key:'task.paused'},{id:'resume',type:'status',task_id:'t',key:'task.requeued_approval'},{id:'text',type:'assistant',text:'Проверяю'},tool('c'),{id:'end',type:'status',key:'task.finished'}];
  const before=JSON.stringify(items),out=groups.project(items);
  assert.deepEqual(out.map(item=>item.type),['assistant','tool_group','assistant','tool_group','status']);
  assert.equal(out[1].rows.length,4);assert.equal(out[1].rows[0].approvals[0].request_id,'a');assert.equal(groups.stats(out[1]).count,2);assert.equal(JSON.stringify(items),before);
});

test('pending approvals and user questions remain visible, with no linking by tool name or path',()=>{
  const t=tool('a'),pending=approval('a','t',''),unknown=approval('different'),question={...approval('a'),id:'question',kind:'user_input'};
  const out=groups.project([t,pending,unknown,question]);
  assert.equal(out[1],pending);assert.equal(out[3],question);assert.equal(out[0].rows[0].approvals.length,0);assert.equal(out[2].rows[0].item,unknown);
});

test('matching call IDs from different tasks and ambiguous duplicates never share permission decisions',()=>{
  const out=groups.project([tool('same','first'),tool('same','second'),approval('same','second')]);
  assert.equal(out.length,2);assert.equal(out[0].rows[0].approvals.length,0);assert.equal(out[1].rows[0].approvals.length,1);
  const duplicate=groups.project([tool('same'),tool('same'),approval('same')]);assert.equal(duplicate[0].rows[0].approvals.length,0);assert.equal(duplicate[0].rows[2].item.type,'approval');
});

test('late results and late decisions update the original group without moving across text boundaries',()=>{
  const a=tool('a'),text={id:'preamble',type:'assistant',text:'Продолжаю'},b=tool('b');
  const first=groups.project([a,text,b]),second=groups.project([{...a,status:'error',result:'failed'},text,b,approval('a')]);
  assert.equal(first[0].id,second[0].id);assert.equal(first[2].id,second[2].id);assert.equal(second[0].rows[0].approvals.length,1);assert.equal(second[2].rows[0].approvals.length,0);
});

test('running, rejected and failed calls are never labelled as completed and one action counts as one failure',()=>{
  const failed=groups.project([tool('a','t','error'),approval('a','t','reject')])[0],running=groups.project([tool('b','t','running')])[0];
  assert.equal(groups.stats(failed).errors,1);assert.equal(groups.stats(failed).done,false);assert.equal(groups.stats(running).running,1);assert.equal(groups.stats(running).done,false);
  assert.equal(groups.stats(groups.project([tool('c')])[0]).done,true);
  assert.equal(groups.stats(groups.project([{...tool('d'),is_error:true}])[0]).errors,1);
  const unknown=groups.stats(groups.project([tool('e','t','exit')])[0]);assert.equal(unknown.done,false);assert.equal(unknown.stopped,0);assert.equal(unknown.errors,1);
  const denied=groups.stats(groups.project([tool('f'),approval('f','t','reject')])[0]);assert.equal(denied.errors,1);assert.equal(denied.failed,0);
});

test('unknown ownership is not attached to another task and generic status events stay in the conversation',()=>{
  const unrelated={id:'pause',type:'status',key:'task.paused'},finish={id:'finish',type:'status',key:'task.finished'};
  const out=groups.project([tool('a'),unrelated,finish]);assert.equal(out.length,3);assert.equal(out[1],unrelated);assert.equal(out[2],finish);
});

test('imported history keeps reused tool call IDs separate across tasks',()=>{
  const vm=require('node:vm'),fs=require('node:fs'),source=fs.readFileSync(require('node:path').join(__dirname, '..','../factory/web_ui/static/app.js'),'utf8');
  const context={t:key=>key};vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function sessionMessageItems('),source.indexOf('function compactionItem(')),context);
  const messages=['first','second'].flatMap(task_id=>[
    {role:'tool',kind:'tool_call',task_id,tool_use_id:'call-1',tool_name:'Read',tool_input:{file_path:'/workspace/'+task_id}},
    {role:'tool',kind:'tool_result',task_id,tool_use_id:'call-1',tool_name:'Read',tool_output:task_id,tool_status:'done'},
  ]);
  const items=context.sessionMessageItems(messages);assert.equal(items.length,2);assert.notEqual(items[0].id,items[1].id);assert.equal(items[0].result,'first');assert.equal(items[1].result,'second');assert.equal(groups.project(items).length,2);
});

test('mixed results count six successful actions and one failed exit independently',()=>{
  const group=groups.project([...Array.from({length:6},(_,i)=>tool(String(i))),tool('failed','t','exit')])[0];
  const stats=groups.stats(group);
  assert.equal(stats.count,7);assert.equal(stats.successful,6);assert.equal(stats.errors,1);assert.equal(stats.failed,1);assert.equal(stats.done,false);assert.equal(stats.unknown,0);
  assert.equal(groups.stats(groups.project([tool('unknown','t','other')])[0]).unknown,1);
  const flagged=groups.stats(groups.project([{...tool('flagged'),is_error:true}])[0]);assert.equal(flagged.successful,0);assert.equal(flagged.errors,1);
});
