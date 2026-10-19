const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync,mkdtempSync,rmSync,mkdirSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');
const D=require('../error-diagnostics.js');
const {NotificationStore}=require('../notifications.cjs');

test('native/HTTP error classifications match the shared wire fixture',()=>{
  for(const row of JSON.parse(readFileSync(join(__dirname,'../../tests/fixtures/diagnostics/classification.json'),'utf8')))
    assert.equal(D.classify(row.input),row.code);
});
test('incident read state is independent of stable owned recovery and a new episode',()=>{
  const root=mkdtempSync(join(tmpdir(),'igorek-error-contract-'));
  try {
    const store=new NotificationStore(join(root,'history.json'));
    const error=D.observation('NET.CONNECTION_RESET',{component:'web',operation:'read',owner:'fixture-a',endpoint_role:'web_to_bridge'});
    store.add({source:'Fixture',level:'error',detail:'Localized first message',diagnostic:error});store.read();
    store.add({source:'Fixture',level:'error',detail:'Different localized message',diagnostic:{...error,event_id:'second'}});
    assert.equal(store.snapshot().length,1);assert.equal(store.snapshot()[0].count,2);assert.equal(store.snapshot()[0].read,true);
    store.recover({owner:'other',stable_ms:10000});assert.equal(store.snapshot()[0].state,'observed');
    store.recover({owner:'fixture-a',stable_ms:1000});assert.equal(store.snapshot()[0].state,'observed');
    store.recover({owner:'fixture-a',stable_ms:5000});assert.equal(store.snapshot()[0].state,'recovered');
    store.add({source:'Fixture',level:'error',detail:'Again',diagnostic:{...error,event_id:'third'}});
    assert.equal(store.snapshot().length,2);assert.equal(store.snapshot()[0].read,false);
    assert.equal(new NotificationStore(join(root,'history.json')).snapshot().length,2);
    assert.ok(!JSON.stringify(store.report()).includes('Localized first message'));
  } finally {rmSync(root,{recursive:true,force:true})}
});
test('unknown write outcome cannot be cleared by transport recovery',()=>{
  const error=D.observation('NET.CONNECTION_RESET',{component:'web',owner:'fixture',operation:'write'});
  const items=[D.incident([],error)];
  assert.equal(error.outcome,'unknown');assert.equal(D.recover(items,'fixture',10000),false);
  assert.equal(items[0].state,'needs_owner');
});
test('storage failures preserve notification and expose writer health without recursion',()=>{
  const root=mkdtempSync(join(tmpdir(),'igorek-diagnostics-store-'));
  try {
    const path=join(root,'occupied');mkdirSync(path);
    const store=new NotificationStore(path);
    assert.doesNotThrow(()=>store.add({source:'Fixture',level:'error',detail:'Error',diagnostic:D.observation('INTERNAL.UNEXPECTED')}));
    assert.equal(store.snapshot().length,1);assert.equal(store.report().health.state,'write_failed');
  } finally {rmSync(root,{recursive:true,force:true})}
});

test('web stream has one retry owner, ignores retired events and recovers only after stable ready',()=>{
  const vm=require('node:vm'),source=readFileSync(join(__dirname,'../../factory/web_ui/static/app.js'),'utf8');
  const timers=new Map(),streams=[],notices=[],recoveries=[];let next=0;
  class Stream {constructor(url){this.url=url;streams.push(this)}close(){this.closed=true}}
  const c={EventSource:Stream,diagnosticsContract:D,streamRecovery:null,location:{origin:'https://example.test'},
    state:{stream:null,lastSeq:0,activeTasks:new Set()},encode:encodeURIComponent,window:{IgorekNotifications:{recover:async value=>recoveries.push(value)}},
    desktopNotification:(...args)=>notices.push(args),recoverDiagnostics:value=>recoveries.push(value),setRuntime(){},handleEvent(){},console,
    setTimeout:(fn,ms)=>{const id=++next;timers.set(id,{fn,ms});return id},clearTimeout:id=>timers.delete(id)};
  vm.createContext(c);
  vm.runInContext(source.slice(source.indexOf('function stopStream()'),source.indexOf('async function selectSession('))+
    source.slice(source.indexOf('function startStream('),source.indexOf('function isolationStarting(')),c);
  c.startStream('fixture',0);const first=streams[0];
  first.onmessage({data:JSON.stringify({kind:'stream.ready'})});assert.equal([...timers.values()][0].ms,5000);
  first.onerror();assert.equal(timers.size,1);first.onerror();assert.equal(timers.size,1);assert.equal(notices.length,1);
  first.onmessage({data:JSON.stringify({kind:'model.text_delta',seq:999,payload:{text:'late'}})});assert.equal(c.state.lastSeq,0);
  const reconnect=[...timers.values()][0];timers.clear();reconnect.fn();assert.equal(streams.length,2);
  streams[1].onmessage({data:JSON.stringify({kind:'stream.ready'})});assert.equal(recoveries.length,0);
  const stable=[...timers.values()][0];timers.clear();stable.fn();assert.equal(recoveries.length,1);
  c.stopStream();assert.equal(timers.size,0);assert.equal(c.state.stream,null);
});

test('local browser diagnostics survive reload without exporting raw messages',()=>{
  const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
  const first=D.createLocalJournal(storage);
  first.observe(D.observation('NET.CONNECTION_RESET',{component:'web',operation:'read',owner:'fixture'}));
  const second=D.createLocalJournal(storage);assert.equal(second.snapshot().incidents.length,1);
  second.recover({owner:'fixture',stable_ms:5000});assert.equal(second.snapshot().incidents[0].state,'recovered');
});

test('web confirms an admitted write by receipt without sending it a second time',async()=>{
  const vm=require('node:vm'),source=readFileSync(join(__dirname,'../../factory/web_ui/static/app.js'),'utf8');
  const storage=new Map(),notices=[],confirmed=[],requests=[];
  const localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
  const c={diagnosticsContract:D,pendingIdempotentRequests:new Map(),failedReadOwners:new Map(),httpReadySince:0,
    crypto:globalThis.crypto,TextEncoder,Uint8Array,location:{origin:'https://example.test'},state:{selected:'fixture-session',session:null},
    window:{localStorage},desktopNotification:async(...args)=>notices.push(args),confirmDiagnostics:async value=>confirmed.push(value),recoverDiagnostics(){},t:x=>x,showConnection(){},encode:encodeURIComponent,
    fetch:async(path,options)=>{requests.push({path,options});if(options.method==='POST')throw new TypeError('fixture connection reset');return {ok:true,status:200,json:async()=>({recorded:true,kind:'message',session_id:'fixture-session',task_id:'task-fixture',receipt:{task_id:'task-fixture',message_id:'message-fixture',status:'queued',steered:true}})}}};
  vm.createContext(c);
  vm.runInContext(source.slice(source.indexOf('function finishPendingOperation('),source.indexOf('const failedReadOwners'))+
    source.slice(source.indexOf('async function api('),source.indexOf('function toast(')),c);
  const reply=await c.api('/api/sessions/fixture-session/messages',{method:'POST',body:JSON.stringify({text:'not persisted',idempotency_key:'fixture-operation-123'})});
  assert.equal(requests.filter(r=>r.options.method==='POST').length,1);
  assert.equal(reply.task_id,'task-fixture');assert.equal(reply.message_id,'message-fixture');assert.equal(confirmed.length,1);
  assert.equal(notices[0][3].outcome,'unknown');assert.equal(c.pendingIdempotentRequests.size,0);
  assert.equal(localStorage.getItem('igorek.pending-operations.v1'),'{}');
  assert.ok(!Array.from(storage.values()).join('').includes('not persisted'));
});
