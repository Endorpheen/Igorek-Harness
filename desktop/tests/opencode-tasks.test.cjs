const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const {OpenCodeTasks,sessionRoute}=require('../opencode-tasks.cjs');

async function fixture(t){
  const root=fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(),'igorek-oc-task-')));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const source=path.join(root,'source'),workspaceRoot=path.join(root,'tasks'),runID='oc_'+'a'.repeat(32);
  const workspace=path.join(workspaceRoot,runID,'workspace');fs.mkdirSync(source);fs.mkdirSync(workspace,{recursive:true});
  const state={outcome:null,running:true,forms:[],messages:[],answers:[],prompts:[],shown:[],requests:[]};
  const bridge=process.platform==='win32'?'\\\\.\\pipe\\igorek-oc-'+require('node:crypto').randomUUID():root;
  const socket=path.join(bridge,'api.sock');
  const server=http.createServer(async(req,res)=>{
    let raw='';for await(const part of req)raw+=part;
    const body=raw?JSON.parse(raw):null;state.requests.push([req.method,req.url,body]);
    if(req.method==='POST'&&req.url==='/api/session')return res.end(JSON.stringify({data:{id:'ses_fixture'}}));
    if(req.url.endsWith('/prompt')){state.prompts.push(body);return res.end(JSON.stringify({data:{id:body.id}}));}
    if(req.url==='/api/session/active')return res.end(JSON.stringify({data:state.running?{ses_fixture:{type:'running'}}:{}}));
    if(req.url==='/api/session/ses_fixture')return res.end(JSON.stringify({data:{outcome:state.outcome}}));
    if(req.url.endsWith('/form'))return res.end(JSON.stringify({data:state.forms}));
    if(req.url.endsWith('/permission')||req.url.endsWith('/inbox'))return res.end(JSON.stringify({data:[]}));
    if(req.url.includes('/message?'))return res.end(JSON.stringify({data:state.messages,cursor:{}}));
    if(req.url.endsWith('/reply')){state.answers.push(body);state.forms=[];res.writeHead(204);return res.end();}
    if(req.url.includes('/interrupt')){state.running=false;res.writeHead(200);return res.end(JSON.stringify({interrupted:true}));}
    res.writeHead(404);res.end();
  });
  await new Promise(resolve=>server.listen(socket,resolve));t.after(()=>new Promise(resolve=>server.close(resolve)));
  const service={state:'ready',bridge,password:'fixture',
    profilesGrant:{profiles:[{name:'fixture',model:'fixture-model'}]},config:{projects:[]},
    async addProject(folder){this.config.projects=[folder];},async start(){},async stop(){this.state='stopped';}};
  const manual={config:{projects:[source]},status:()=>({installed:true})};
  const controller=new OpenCodeTasks({manual,root:path.join(root,'service'),workspaceRoot,createService:(folder,workspace,consumer)=>{state.created={folder,workspace,consumer};return service;},
    show:async job=>state.shown.push(job),allowed:()=>true,platform:'linux'});
  const input={action:'start',run_id:runID,source,execution_mode:'direct',title:'Fix fixture',goal:'Change a fixture',profile:'fixture',session:'chat-fixture'};
  return {controller,state,manual,service,input};
}

test('OpenCode 2 assignment links use its server-scoped route, not legacy directory routes',()=>{
  const route=sessionRoute({service:{origin:'http://127.0.0.1:4096'},sessionID:'ses_fixture'});
  assert.equal(route,'/server/'+Buffer.from('http://127.0.0.1:4096').toString('base64url')+'/session/ses_fixture');
  assert(!route.includes('/workspace'));
});

test('delegation admits one prompt, shows the same session, and leaves manual grants unchanged',async t=>{
  const f=await fixture(t),reply=await f.controller.handle(f.input);
  assert.deepEqual(reply,{session_id:'ses_fixture',execution_mode:'direct'});
  assert.deepEqual(await f.controller.handle(f.input),reply);
  assert.equal(f.state.prompts.length,1);assert.equal(f.state.shown[0].sessionID,reply.session_id);
  assert.deepEqual(f.manual.config.projects,[f.input.source]);
  const create=f.state.requests.find(row=>row[1]==='/api/session');
  assert.equal(create[2].title,'Поручение: Fix fixture');
  assert.equal(create[2].location.directory,f.input.source);
  assert.deepEqual(f.service.config.projects,[f.input.source]);
});

test('every form is relayed and an answer uses its exact session and form without model mediation',async t=>{
  const f=await fixture(t);await f.controller.handle(f.input);
  f.state.forms=[{id:'frm_fixture',sessionID:'ses_fixture',title:'Choose',fields:[{key:'choice',type:'boolean'}]}];
  const status=await f.controller.handle({action:'status',run_id:f.input.run_id});
  assert.equal(status.inputs[0].id,'frm_fixture');assert.equal(status.outcome,null);
  assert.deepEqual(await f.controller.handle({action:'answer',run_id:f.input.run_id,request_id:'frm_fixture',response:{answer:{choice:true}}}),{resolved:true});
  assert.deepEqual(f.state.answers,[{answer:{choice:true}}]);
  await f.controller.handle({action:'answer',run_id:f.input.run_id,request_id:'frm_fixture',response:{answer:{choice:false}}});
  assert.equal(f.state.answers.length,1);
});

test('completion uses explicit outcome and observation cannot interrupt a session',async t=>{
  const f=await fixture(t);await f.controller.handle(f.input);
  const count=f.state.requests.length;
  assert.equal((await f.controller.handle({action:'cancel',run_id:f.input.run_id})).error,'opencode_observation_read_only');
  assert.equal(f.state.requests.length,count);
  assert(!f.state.requests.some(row=>row[1].includes('/interrupt')));
  f.state.outcome='succeeded';f.state.running=false;
  f.state.messages=[{id:'msg_result',type:'assistant',time:{completed:123},content:[{type:'text',text:'Fixture result'}]}];
  const status=await f.controller.handle({action:'status',run_id:f.input.run_id});
  assert.equal(status.outcome,'succeeded');assert.equal(status.result,'Fixture result');
});

test('ungranted projects and legacy copy protocols never receive a prompt',async t=>{
  const f=await fixture(t);
  assert.equal((await f.controller.handle({...f.input,source:path.dirname(f.input.source)})).error,'opencode_project_not_granted');
  assert.equal((await f.controller.handle({...f.input,workspace:f.input.source})).error,'opencode_direct_protocol_required');
  assert.equal(f.state.prompts.length,0);
});

test('Windows service-folder layout retains the assignment profile consumer identity',async t=>{
  const f=await fixture(t);f.controller.platform='win32';
  const result=await f.controller.handle(f.input);assert(result.session_id);
  assert.equal(f.state.created.consumer,f.input.run_id);
  assert.equal(f.state.created.folder,path.join(f.controller.root,f.input.run_id));
  assert.equal(f.state.created.workspace,f.input.source);
});

test('live status reads actual tools and public partial text using GET only',async t=>{
  const f=await fixture(t);await f.controller.handle(f.input);
  f.state.messages=[{id:'msg_live',type:'assistant',time:{created:1},content:[
    {type:'reasoning',text:'private reasoning fixture'},
    {type:'text',text:'Inspecting the project'},
    {type:'tool',id:'call_read',name:'read',time:{created:1,completed:2},state:{status:'completed',input:{path:'code.py'},content:[{type:'text',text:'private output'}]}},
    {type:'tool',id:'call_shell',name:'shell',time:{created:3,ran:4},state:{status:'running',input:{command:'private command'},metadata:{}}},
  ]}];
  f.state.forms=[{id:'frm_live',title:'Choose',fields:[]}];
  const start=f.state.requests.length;
  const result=await f.controller.handle({action:'status',run_id:f.input.run_id});
  assert.equal(result.outcome,null);assert.equal(result.messages.length,0);
  assert.equal(result.progress.latest_message.text,'Inspecting the project');
  assert.equal(result.progress.latest_message.completed,false);
  assert.deepEqual(result.progress.action_counts,{streaming:0,running:1,completed:1,error:0});
  assert.equal(result.progress.recent_actions[0].target,'code.py');
  assert.equal(result.progress.active_actions[0].tool,'shell');
  assert(!JSON.stringify(result.progress).includes('private'));
  assert(f.state.requests.slice(start).every(row=>row[0]==='GET'));
  assert(Number.isFinite(result.progress.observed_at));
});

test('progress is bounded independently of message delivery and includes failed calls',async t=>{
  const f=await fixture(t);await f.controller.handle(f.input);
  f.state.messages=[{id:'msg_many',type:'assistant',time:{created:1,completed:100},content:[
    {type:'text',text:'Done with this step'},
    ...Array.from({length:25},(_,i)=>({type:'tool',id:'call_'+i,name:'read',time:{created:i},state:{status:i===24?'error':'completed',input:{path:'file-'+i}}})),
  ]}];
  const result=await f.controller.handle({action:'status',run_id:f.input.run_id,seen:['msg_many']});
  assert.equal(result.messages.length,0);
  assert.equal(result.progress.recent_actions.length,10);
  assert.equal(result.progress.recent_actions_truncated,true);
  assert.equal(result.progress.action_counts.error,1);
  assert.equal(result.progress.recent_actions.at(-1).status,'error');
});

test('bounded public text keeps Unicode code points intact',async t=>{
  const f=await fixture(t);await f.controller.handle(f.input);
  f.state.messages=[{id:'msg_unicode',type:'assistant',time:{created:1},content:[{type:'text',text:'x'.repeat(3999)+'🐙 ending'}]}];
  const result=await f.controller.handle({action:'status',run_id:f.input.run_id});
  assert.equal(result.progress.latest_message.text,'x'.repeat(3999)+'🐙');
});

test('provider call IDs reused in different messages do not hide current work',async t=>{
  const f=await fixture(t);await f.controller.handle(f.input);
  f.state.messages=[{id:'msg_first',type:'assistant',time:{created:1,completed:2},content:[
    {type:'tool',id:'call_reused',name:'read',time:{created:1,completed:2},state:{status:'completed',input:{path:'first.py'}}}]},
    {id:'msg_second',type:'assistant',time:{created:3},content:[
    {type:'tool',id:'call_reused',name:'write',time:{created:3},state:{status:'running',input:{path:'second.py'}}}]}];
  const result=await f.controller.handle({action:'status',run_id:f.input.run_id});
  assert.equal(result.progress.action_counts.completed,1);
  assert.equal(result.progress.action_counts.running,1);
  assert.equal(result.progress.active_actions[0].message_id,'msg_second');
  assert.equal(result.progress.active_actions[0].target,'second.py');
});
