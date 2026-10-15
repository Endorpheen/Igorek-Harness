const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname, '..','../factory/web_ui/static/app.js'),'utf8');
function fixture(){
 const state={items:[],textStreams:new Map(),textStreamRounds:new Map(),closedStreams:new Map(),knownAssistantText:new Set(),finishedTasks:new Set(),finishNotes:new Map(),startedTasks:new Set(),activeTasks:new Set(),taskStarts:new Map()};
 const context={state,window:{},upsert(item){const index=state.items.findIndex(row=>row.id===item.id);if(index<0)state.items.push(item);else state.items[index]={...state.items[index],...item};},setRuntime(){},setIndicator(){},startTask(){},refreshComposerModel(){},isolationStarting:()=>false,renderTimeline(){},noteUsage(){},requeueText:()=>'',Date};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function resetTextStream('),source.indexOf('function knownSubagentFor(')),context);
 let seq=0;return {state,event:context.handleEvent,send(kind,round,text='',task_id='task'){context.handleEvent({kind,task_id,seq:++seq,payload:{round,text}});},stream:()=>state.items.find(row=>row.id==='stream-task'),answers:()=>state.items.filter(row=>row.type==='assistant'&&!row.streaming)};
}
test('late deltas never turn the suffix of a committed reply into another message',()=>{
 const f=fixture();f.send('model.text_delta',17,'Генерация');f.send('model.round_response',17,'Генерация трека: 67%.');
 for(const text of [' тр','ек: ','67%.'])f.send('model.text_delta',17,text);
 assert.equal(f.stream(),undefined);assert.equal(f.answers().length,1);assert.equal(f.answers()[0].text,'Генерация трека: 67%.');
 f.send('model.round_response',18,'');f.send('model.text_delta',17,'старый хвост');assert.equal(f.stream(),undefined);
});
test('a new round displays its complete beginning even when it matches the preceding answer',()=>{
 const f=fixture();f.send('model.round_response',1,'Проверяю файл.');f.send('model.text_delta',2,'Проверяю ');f.send('model.text_delta',2,'ещё один файл.');
 assert.equal(f.stream().text,'Проверяю ещё один файл.');f.send('model.round_response',2,'Проверяю ещё один файл.');assert.equal(f.stream(),undefined);assert.equal(f.answers().length,2);
});
test('response before any live frame ignores that entire closed round',()=>{
 const f=fixture();f.send('model.round_response',3,'Готово.');f.send('model.text_delta',3,'Го');f.send('model.text_delta',3,'тово.');assert.equal(f.stream(),undefined);
});
test('late response from an earlier round preserves a newer open stream',()=>{
 const f=fixture();f.send('model.text_delta',5,'Новый ответ');f.send('model.round_response',4,'Старый ответ');f.send('model.text_delta',4,'хвост');assert.equal(f.stream().text,'Новый ответ');
 f.send('model.text_delta',5,' целиком');assert.equal(f.stream().text,'Новый ответ целиком');
});
test('approval resume and task restart reset round identity without removing committed history',()=>{
 for(const kind of ['task.start','task.requeued']){
  const f=fixture();f.send('model.round_response',8,'До подтверждения');f.send(kind,0);f.send('model.text_delta',1,'После подтверждения');assert.equal(f.stream().text,'После подтверждения');assert.equal(f.answers().length,1);
 }
});
test('task isolation, finished tasks and old frames without round identity stay deterministic',()=>{
 const f=fixture();f.send('model.round_response',5,'Ответ');f.send('model.text_delta',1,'Другой', 'other');assert.equal(f.state.textStreams.get('other'),'Другой');
 f.send('model.text_delta',undefined,'неизвестный хвост');assert.equal(f.stream(),undefined);
 f.state.finishedTasks.add('task');f.send('model.text_delta',6,'Поздний ответ');assert.equal(f.stream(),undefined);
 const legacy=fixture();legacy.send('model.text_delta',undefined,'Начало');legacy.send('model.text_delta',undefined,' и конец');assert.equal(legacy.stream().text,'Начало и конец');legacy.send('model.round_response',undefined,'Начало и конец');assert.equal(legacy.answers().length,1);
});

test('attachment event updates only the final answer of its own task',()=>{
 const f=fixture();f.send('model.round_response',1,'Преамбула');f.send('model.round_response',2,'Готовый файл');f.send('model.round_response',1,'Другой разговор','other');
 const files=[{id:'a'.repeat(64),name:'song.wav',mime_type:'audio/x-wav'}];
 f.event({kind:'message.files',task_id:'task',payload:{files}});
 assert.equal(f.answers().find(row=>row.text==='Преамбула').files,undefined);
 assert.deepEqual(f.answers().find(row=>row.text==='Готовый файл').files,files);
 assert.equal(f.answers().find(row=>row.text==='Другой разговор').files,undefined);
});
