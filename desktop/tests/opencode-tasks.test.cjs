const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const {OpenCodeTasks,sessionRoute}=require('../opencode-tasks.cjs');

async function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-oc-task-'));
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
  const controller=new OpenCodeTasks({manual,root:path.join(root,'service'),workspaceRoot,createService:()=>service,
    show:async job=>state.shown.push(job),allowed:()=>true,platform:'linux'});
  const input={action:'start',run_id:runID,source,workspace,title:'Fix fixture',goal:'Change a fixture',profile:'fixture',session:'chat-fixture'};
  return {controller,state,manual,service,input};
}

test('OpenCode 2 assignment links use its server-scoped route, not legacy directory routes',()=>{
  const route=sessionRoute({service:{origin:'http://127.0.0.1:4096'},sessionID:'ses_fixture'});
  assert.equal(route,'/server/'+Buffer.from('http://127.0.0.1:4096').toString('base64url')+'/session/ses_fixture');
  assert(!route.includes('/workspace'));
});

test('delegation admits one prompt, shows the same session, and leaves manual grants unchanged',async t=>{
  const f=await fixture(t),reply=await f.controller.handle(f.input);
  assert.deepEqual(reply,{session_id:'ses_fixture'});
  assert.deepEqual(await f.controller.handle(f.input),reply);
  assert.equal(f.state.prompts.length,1);assert.equal(f.state.shown[0].sessionID,reply.session_id);
  assert.deepEqual(f.manual.config.projects,[f.input.source]);
  const create=f.state.requests.find(row=>row[1]==='/api/session');
  assert.equal(create[2].title,'Поручение: Fix fixture');
  assert.equal(create[2].location.directory,f.input.workspace);
  assert.deepEqual(f.service.config.projects,[f.input.workspace]);
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

test('completion uses explicit outcome and cancellation never requests resume',async t=>{
  const f=await fixture(t);await f.controller.handle(f.input);
  f.state.outcome='succeeded';f.state.running=false;
  f.state.messages=[{id:'msg_result',type:'assistant',time:{completed:123},content:[{type:'text',text:'Fixture result'}]}];
  const status=await f.controller.handle({action:'status',run_id:f.input.run_id});
  assert.equal(status.outcome,'succeeded');assert.equal(status.result,'Fixture result');
  await f.controller.handle({action:'cancel',run_id:f.input.run_id});
  assert(f.state.requests.some(row=>row[1].endsWith('/interrupt?resume=false')));
});

test('ungranted projects and substituted workspaces never receive a prompt',async t=>{
  const f=await fixture(t);
  assert.equal((await f.controller.handle({...f.input,source:f.input.workspace})).error,'opencode_project_not_granted');
  assert.equal((await f.controller.handle({...f.input,workspace:f.input.source})).error,'opencode_workspace_not_owned');
  assert.equal(f.state.prompts.length,0);
});
