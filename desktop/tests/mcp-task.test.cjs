const {test} = require('node:test');
const assert = require('node:assert/strict');
const {SessionMcp} = require('../mcp-task.cjs');
function fixture() {
  const config = {name:'fixture',api_format:'openai',base_url:'http://127.0.0.1:1234/v1'};
  const state = {servers:[{id:'gmail'}],profiles:[{credential_id:'cid',status:'verified',config}],grants:{gmail:{cid:{access:'local_only',config:{...config}}}}};
  const calls=[];
  const router = new SessionMcp({trusted:e=>e.trusted,settings:()=>{},request:async(name)=>{
    calls.push(name);
    if(name==='mcp_access') return {result:{mcp_access:state}};
    if(name==='gmail_status') return {result:{gmail:{connected:true}}};
    return {result:{private_mcp:{task_id:'task1',running:false,result:'private-result',error:''}}};
  }});
  return {router,calls,state,event:{trusted:true,sender:{}}};
}
test('MCP session refuses untrusted callers, extra fields and changed profile grants', async()=>{
  const {router,state,event,calls}=fixture();
  await assert.rejects(router.dispatch({...event,trusted:false},'list'));
  assert.equal(calls.length,0);
  assert.equal((await router.dispatch(event,'list',{extra:'x'})).error,'invalid_request');
  assert.equal((await router.dispatch(event,'list')).entries.length,1);
  state.profiles[0].config.base_url='https://example.test/v1';
  assert.equal((await router.dispatch(event,'list')).entries.length,0);
});
test('Docs appears in plus after owner grant and disappears on disable without restarting', async()=>{
  const {router,state,event}=fixture();
  state.servers.push({id:'openai_docs',disabled:false});
  assert.deepEqual((await router.dispatch(event,'list')).entries.map(x=>x.server),['gmail']);
  state.grants.openai_docs={cid:{access:'allow',config:{...state.profiles[0].config}}};
  const entries=(await router.dispatch(event,'list')).entries;
  assert.deepEqual(entries.map(x=>x.server),['gmail','openai_docs']);
  assert.equal(entries[1].readonly,true);
  state.servers[1].disabled=true;
  assert.deepEqual((await router.dispatch(event,'list')).entries.map(x=>x.server),['gmail']);
});
test('Only the originating window can retrieve or clear its private result',async()=>{
  const {router,event,calls}=fixture();
  await router.dispatch(event,'start',{server:'gmail',credential_id:'cid',message:'read'});
  assert.equal((await router.dispatch({...event,sender:{}},'status')).error,'task_other_window');
  assert.equal((await router.dispatch(event,'status')).job.result,'private-result');
  router.release(event.sender);
  assert.equal((await router.dispatch(event,'status')).job.result,'');
  assert.ok(calls.includes('private_mcp_clear'));
});

test('MCP chat distinguishes model timeout, provider rejection and Gmail authorization safely', () => {
  const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
  const source=fs.readFileSync(path.join(__dirname, '..','../factory/web_ui/static/mcp-task.js'),'utf8');
  const context=vm.createContext({});
  const begin=source.indexOf('  const errors ='),end=source.indexOf('  function reset()',begin);
  assert.ok(begin>=0&&end>begin);
  vm.runInContext(source.slice(begin,end),context);
  const describe=failure=>vm.runInContext(`message(${JSON.stringify({job:{error:'private_request_failed',failure}})})`,context);
  assert.match(describe({stage:'model_request',reason:'timeout'}),/Сервис модели не ответил вовремя/);
  assert.match(describe({stage:'model_request',reason:'http_error',http_status:400}),/Сервис модели отклонил запрос \(HTTP 400\)/);
  assert.match(describe({stage:'mcp_call',reason:'authentication',http_status:401}),/Повторите вход Google в настройках подключения/);
  assert.match(describe({stage:'model_response',reason:'output_limit'}),/лимит ответа/);
  const unknown=describe({stage:'model_request',reason:'__proto__',http_status:'fixture-private-key'});
  assert.match(unknown,/Внутренняя ошибка/);assert.ok(!unknown.includes('fixture-private-key'));
});

test('Calendar write approval is restricted to the originating MCP window',async()=>{
 const {router,event,calls}=fixture();await router.dispatch(event,'start',{server:'gmail',credential_id:'cid',message:'fixture'});
 const fields={request_id:'fixture-approval',decision:'approve'};
 assert.equal((await router.dispatch({...event,sender:{}},'approve',fields)).error,'task_other_window');
 assert.ok(!calls.includes('private_mcp_approve'));
 assert.equal((await router.dispatch(event,'approve',{...fields,decision:'unknown'})).error,'invalid_request');
 await router.dispatch(event,'approve',fields);assert.ok(calls.includes('private_mcp_approve'));
});
