const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function harness() {
 const source=fs.readFileSync(path.join(__dirname, '..','main.cjs'),'utf8'),start=source.indexOf("ipcMain.handle('igorek:session-plugins'"),end=source.indexOf("\napp.on('web-contents-created'",start);
 const calls=[],opened=[],browser=[];let handler,reply={result:{plugins:{openai_docs:{enabled:true}}}};
 const context=vm.createContext({URL,shell:{openExternal:async url=>browser.push(url)},sessionMcp:{trusted:e=>e.trusted===true},runtime:{requests:new Map(),requestOwner:async(action,fields)=>{calls.push({action,fields});return typeof reply==='function'?reply():reply}},openOwnerWindow:(panel,plugin)=>opened.push(plugin?{panel,plugin}:panel),ipcMain:{handle:(_,fn)=>handler=fn}});
 assert.ok(start>=0&&end>start);vm.runInContext(source.slice(start,end),context);
 return {context,calls,opened,browser,call:(action,fields,event={trusted:true})=>handler(event,action,fields),reply:value=>reply=value};
}
test('Plugins page IPC refuses foreign frames, raw paths, credentials and unknown actions',async()=>{
 const h=harness();await assert.rejects(h.call('status',{},{}));assert.equal(h.calls.length,0);
 for(const [action,fields] of [['status',{password:'fixture'}],['action',{plugin:'openai_docs',operation:'connect',source:'/tmp/private'}],['action',{plugin:'other',operation:'connect'}],['action',{plugin:'openai_docs',operation:'configure'}],['settings',{panel:'secrets'}]])assert.equal((await h.call(action,fields)).error,'invalid_request');
 assert.equal(h.calls.length,0);assert.equal(h.opened.length,0);
});
test('Plugins IPC forwards only public actions and disables Calendar without a setup detour',async()=>{
 const h=harness();assert.equal((await h.call('status',{})).plugins.openai_docs.enabled,true);
 assert.equal(h.calls[0].action,'plugins_status');
 await h.call('action',{plugin:'openai_docs',operation:'check'});assert.equal(h.calls[1].action,'plugin_action');
 await h.call('action',{plugin:'google_calendar',operation:'disconnect'});assert.deepEqual(h.opened,[]);assert.equal(h.calls[2].action,'calendar_disable');
 h.context.runtime.requests.set('fixture',{});assert.equal((await h.call('status',{})).error,'operation_busy');assert.equal(h.calls.length,3);
});
test('Calendar connect opens only the real Google consent URL without returning it to renderer',async()=>{
 const h=harness();h.reply({result:{authorization_url:'https://accounts.google.com/o/oauth2/v2/auth?state=fixture'}});
 const reply=await h.call('calendar_connect',{profile:'fixture-model'});
 assert.equal(reply.opened,true);assert.equal(reply.authorization_url,undefined);assert.equal(h.calls[0].fields.profile,'fixture-model');assert.equal(h.browser.length,1);assert.equal(h.opened.length,0);
 for(const url of ['https://other.test/oauth','https://accounts.google.com/other','javascript:alert(1)']){
  h.reply({result:{authorization_url:url}});assert.equal((await h.call('calendar_connect',{profile:'fixture-model'})).error,'invalid_request');
 }
 assert.equal(h.browser.length,1);
 assert.equal((await h.call('calendar_connect',{profile:'fixture-model',password:'fixture'})).error,'invalid_request');
});
test('Calendar consent cannot be started by a foreign frame or opened after navigation',async()=>{
 const h=harness();await assert.rejects(h.call('calendar_connect',{profile:'fixture'},{}));assert.equal(h.calls.length,0);
 let finish;h.reply(()=>new Promise(resolve=>finish=resolve));const event={trusted:true};const pending=h.call('calendar_connect',{profile:'fixture'},event);
 await Promise.resolve();event.trusted=false;finish({result:{authorization_url:'https://accounts.google.com/o/oauth2/v2/auth?state=fixture'}});
 assert.equal((await pending).error,'vault_locked');assert.equal(h.browser.length,0);
});
test('Late plugin reply does not reach a navigated frame',async()=>{
 const h=harness(),event={trusted:true};let finish;h.reply(()=>new Promise(resolve=>finish=resolve));
 const pending=h.call('status',{},event);await Promise.resolve();event.trusted=false;finish({result:{plugins:{google_calendar:{calendar_id:'private-calendar'}}}});
 const result=await pending;assert.equal(result.error,'vault_locked');assert.ok(!JSON.stringify(result).includes('private-calendar'));
});
test('Model permission shortcut carries only an allowlisted plugin to owner settings',async()=>{
 const h=harness();
 assert.equal((await h.call('settings',{panel:'mcp',plugin:'openai_docs'})).opened,true);
 assert.equal(h.opened[0].panel,'mcp');assert.equal(h.opened[0].plugin,'openai_docs');
 for(const fields of [{panel:'mcp',plugin:'other'},{panel:'plugins',plugin:'openai_docs'},{panel:'mcp',plugin:'openai_docs',password:'fixture'}])assert.equal((await h.call('settings',fields)).error,'invalid_request');
 assert.equal(h.opened.length,1);assert.equal(h.calls.length,0);
});
