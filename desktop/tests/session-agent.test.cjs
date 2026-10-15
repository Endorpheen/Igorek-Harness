const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function harness(){
 const source=fs.readFileSync(path.join(__dirname, '..','main.cjs'),'utf8'),a=source.indexOf("ipcMain.handle('igorek:session-agent'"),b=source.indexOf("ipcMain.handle('igorek:session-plugins'",a);let handler;
 const calls=[],requests=new Map();let reply={result:{agent:{running:false,profile:''},profiles:['fixture-model'],secret:'must-not-leak'}};
 vm.runInNewContext(source.slice(a,b),{sessionMcp:{trusted:e=>e.trusted===true},runtime:{requests,requestOwner:async(action,fields)=>{calls.push({action,fields});return typeof reply==='function'?reply():reply}},ipcMain:{handle:(name,fn)=>{if(name==='igorek:session-agent')handler=fn}}});
 return {calls,requests,reply:r=>reply=r,call:(fields,event={trusted:true})=>handler(event,fields)};
}
test('Chat starts only a named verified profile through the existing owner runtime',async()=>{
 const h=harness();const status=await h.call({operation:'status',profile:'fixture-model'});assert.equal(status.agent.running,false);assert.equal(status.secret,undefined);
 await h.call({operation:'start',profile:'fixture-model'});assert.equal(h.calls[1].action,'session_agent');assert.equal(h.calls[1].fields.operation,'start');
 assert.equal((await h.call({operation:'catalog',profile:''})).profiles[0],'fixture-model');
 for(const fields of [{operation:'stop',profile:'fixture'},{operation:'start',profile:''},{operation:'start',profile:'fixture',credential_id:'cid'}])assert.equal((await h.call(fields)).error,'invalid_request');
 await assert.rejects(h.call({operation:'start',profile:'fixture'},{}));assert.equal(h.calls.length,3);
});
test('Chat profile startup refuses busy runtime and replies to navigated frames',async()=>{
 const h=harness();h.requests.set('busy',{});assert.equal((await h.call({operation:'start',profile:'fixture'})).error,'operation_busy');h.requests.clear();
 let finish;h.reply(()=>new Promise(resolve=>finish=resolve));const event={trusted:true};const pending=h.call({operation:'start',profile:'fixture'},event);await Promise.resolve();event.trusted=false;finish({result:{agent:{running:true,profile:'fixture'}}});
 assert.equal((await pending).error,'vault_locked');
});
