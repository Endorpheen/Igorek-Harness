const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');
function fixture(api){
 const window={addEventListener(){},IgorekDesktop:{}};
 const context=vm.createContext({window,document:{addEventListener(){}},ui:{send:{disabled:false}},state:{sending:false,selected:'chat'},api,encode:encodeURIComponent,Promise,Map,Set});
 vm.runInContext(readFileSync(join(__dirname,'../../factory/web_ui/static/composer-profiles.js'),'utf8'),context);
 const source=readFileSync(join(__dirname,'../../factory/web_ui/static/app.js'),'utf8');
 const start=source.indexOf('async function followDesktopProfile('),end=source.indexOf('\n// The directory selection',start);
 assert.ok(start>=0&&end>start);vm.runInContext(source.slice(start,end),context);
 return {window,context,follow:(session,options,valid=()=>true)=>context.followDesktopProfile(session,options,valid)};
}
const session=()=>({session_id:'chat',meta:{profile:'old',model:'old-model'},messages:[{text:'retained'}]});
const options={default_profile:'new',profiles:['new'],profile_switch_supported:true};
test('settings synchronization uses the running profile with a stale-profile guard and preserves the server result',async()=>{
 const calls=[],changed={...session(),meta:{profile:'new',model:'new-model'}};
 const f=fixture(async(route,request)=>{calls.push({route,request});assert.equal(f.window.IgorekComposerProfiles.busy('chat'),true);assert.equal(f.context.ui.send.disabled,true);return changed;});
 assert.equal(await f.follow(session(),options),changed);
 assert.equal(calls[0].route,'/api/sessions/chat/profile');
 assert.deepEqual(JSON.parse(calls[0].request.body),{profile:'new',expected_profile:'old'});
 assert.equal(f.window.IgorekComposerProfiles.busy('chat'),false);
 assert.equal(f.context.ui.send.disabled,false);
});
test('opening a chat waits for its in-flight settings synchronization instead of restoring a stale snapshot',async()=>{
 let release,posts=0;const changed={...session(),meta:{profile:'new',model:'new-model'}};
 const f=fixture(async()=>{posts++;return new Promise(resolve=>release=resolve);});
 const first=f.follow(session(),options);await Promise.resolve();
 const reopened=f.follow(session(),null);
 release(changed);assert.equal(await first,changed);assert.equal(await reopened,changed);assert.equal(posts,1);
});
test('late catalog replies cannot reroute a chat after navigation or a manual switch',async()=>{
 let release,valid=true;const routes=[];
 const f=fixture(async route=>{routes.push(route);return new Promise(resolve=>release=resolve);});
 const original=session(),pending=f.follow(original,null,()=>valid);await Promise.resolve();valid=false;release(options);
 assert.equal(await pending,original);assert.deepEqual(routes,['/api/session-options']);
});
test('failed synchronization releases the composer and allows a subsequent attempt',async()=>{
 let attempts=0;const f=fixture(async()=>{if(!attempts++)throw Error('provider unavailable');return session();});
 await assert.rejects(f.follow(session(),options),/provider unavailable/);
 assert.equal(f.window.IgorekComposerProfiles.busy('chat'),false);assert.equal(f.context.ui.send.disabled,false);
 await f.follow(session(),options);assert.equal(attempts,2);
});
test('same-profile model overrides, busy tasks, special chats and non-desktop clients retain their current binding',async()=>{
 let requests=0;const f=fixture(async()=>{requests++;throw Error('unexpected request');});
 for(const patch of [{running_task_id:'task'},{meta:{profile:'old',agent_session:true}},{meta:{profile:'old',demo:true}}]){
  const original={...session(),...patch};assert.equal(await f.follow(original,options),original);
 }
 const original=session();original.meta.model_override='selected-model';
 assert.equal(await f.follow(original,{...options,default_profile:'old',profiles:['old']}),original);
 assert.equal(original.meta.model_override,'selected-model');
 f.window.IgorekDesktop=null;assert.equal(await f.follow(original,options),original);assert.equal(requests,0);
});
