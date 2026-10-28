const {app,BrowserWindow,dialog}=require('electron');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {execFileSync}=require('node:child_process');
const repo=process.env.IGOREK_TEST_SOURCE_ROOT?path.resolve(process.env.IGOREK_TEST_SOURCE_ROOT):path.resolve(__dirname,'../../..');
const output=process.env.IGOREK_TEST_OUTPUT||fs.mkdtempSync(path.join(os.tmpdir(),'igorek-switch-proof-'));
const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-main-switch-'));
fs.mkdirSync(output,{recursive:true});
app.setPath('appData',path.join(root,'config'));app.setPath('home',path.join(root,'host'));
process.env.HOME=path.join(root,'host');process.env.XDG_CONFIG_HOME=path.join(root,'host/config');process.env.XDG_DATA_HOME=path.join(root,'host/data');
for(const key of ['FACTORY_EXTERNAL_CODEX_HOME','FACTORY_EXTERNAL_CLAUDE_HOME','FACTORY_EXTERNAL_OPENCODE_DB','FACTORY_WALLY_CREDENTIALS','AGENTFACTORY_LEGACY_SKILLS_PATH','FACTORY_SESSION_EMBEDDINGS_LLAMASERVER_BIN'])process.env[key]=path.join(root,'absent',key);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let server,chat,owner,heldResponse,releaseHeld,dataRoot,succeeded=false;
app.once('will-quit',()=>{if(succeeded)fs.rmSync(root,{recursive:true,force:true});else{console.error('EARLY EXIT',JSON.stringify(failures));fs.writeFileSync(path.join(output,'early-exit.json'),JSON.stringify({failures,succeeded},null,2));app.exit(1);}});
const calls=[],failures=[];
dialog.showErrorBox=(title,message)=>{failures.push({title,message});};
async function until(check,label){for(let i=0;i<300;i++){if(failures.length)throw Error(JSON.stringify(failures));if(await check())return;await pause(50);}throw Error(label);}
async function evaluate(window,code){return window.webContents.executeJavaScript(code);}
async function ownerCall(action,...args){const result=await evaluate(owner,`window.igorekVault[${JSON.stringify(action)}](...${JSON.stringify(args)})`);assert.ok(!result.error,JSON.stringify(result));return result.result;}
async function api(route,body){return evaluate(chat,`(async()=>{const r=await fetch(${JSON.stringify(route)},${JSON.stringify(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});return {status:r.status,body:await r.json()};})()`);}
async function click(selector){const point=await evaluate(chat,`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||e.disabled)return null;e.scrollIntoView({block:'nearest'});const b=e.getBoundingClientRect();return b.width&&b.height?{x:Math.round(b.x+b.width/2),y:Math.round(b.y+b.height/2)}:null})()`);assert.ok(point,selector);chat.show();chat.focus();chat.webContents.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...point});chat.webContents.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,...point});}
async function chooseProfile(name){
 if(!await evaluate(chat,'Boolean(document.querySelector(".composer-profile-menu"))'))await click('#composer-model');
 await until(()=>evaluate(chat,`Boolean(document.querySelector('.composer-profile-option[data-profile="${name}"]'))`),'Profile option missing');
 if(await evaluate(chat,`Boolean(document.querySelector('.composer-profile-option[data-profile="${name}"]').closest('[hidden]'))`)){await click('.composer-profile-toggle');await until(()=>evaluate(chat,'document.querySelector(".composer-profile-toggle").getAttribute("aria-expanded")=="true"'),'Other profiles did not expand');}
 await click(`.composer-profile-option[data-profile="${name}"]`);
 const reading=Promise.all([ownerCall('status'),ownerCall('listProfiles')]);
 await until(()=>evaluate(chat,`state.session.meta.profile===${JSON.stringify(name)}&&!window.IgorekComposerProfiles.busy(state.selected)`),'Composer profile did not switch');
 const [status]=await reading;assert.equal(status.state,'unlocked');
 assert.equal((await ownerCall('listProfiles')).agent.profile,name);
 await until(()=>evaluate(chat,`Boolean(document.querySelector('.composer-profile-option[data-profile="${name}"][aria-checked=true]:not(:disabled)'))`),'Selected profile menu did not finish refreshing');
}
async function run(){
 server=http.createServer((request,response)=>{let raw='';request.on('data',chunk=>raw+=chunk);request.on('end',()=>{
  if(request.method==='GET'){response.setHeader('Content-Type','application/json');response.end(JSON.stringify({data:[{id:'fixture-go-a'},{id:'fixture-go-b'}]}));return;}
  const body=JSON.parse(raw);calls.push({model:body.model,key:request.headers.authorization==='Bearer fixture-only-key'});
  const finish=()=>{response.writeHead(200,{'Content-Type':'text/event-stream'});response.end('data: '+JSON.stringify({choices:[{index:0,delta:{content:'fixture-switch-ready '+body.model},finish_reason:null}]})+'\n\ndata: '+JSON.stringify({choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:4,completion_tokens:2}})+'\n\ndata: [DONE]\n\n');};
  if(JSON.stringify(body.messages).includes('fixture-switch-delayed')){heldResponse=true;releaseHeld=finish;}else finish();
 });});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port+'/v1',data=path.join(root,'runtime'),bundle=path.join(root,'bundle');fs.mkdirSync(bundle);dataRoot=data;
 const python=process.env.IGOREK_TEST_PYTHON||path.join(repo,'.venv/bin/python');
 const bootstrap=`import os,sys,json\nfrom pathlib import Path\nrepo,root,base=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]\nos.environ.update(IGOREK_FACTORY_HOME=str(root),FACTORY_DATA_DIR=str(root),HOME=str(root/'home'))\nsys.path[:0]=[str(repo),str(repo/'desktop')]\nfrom vault_owner import OwnerVault\nfrom factory.core.runtime import get_db_path\nfrom factory.core.schema import initialize_database\nfrom factory.core.mobile_bridge.service import MobileService\nfrom factory.core.cli_chat import _append_entry,_session_path\ninitialize_database(get_db_path());owner=OwnerVault(root);owner.service.create('fixture-password');saved={}\n(root/'profiles').mkdir();(root/'workspace').mkdir()\nfor name,model,url in [('codex','fixture-codex',base),('opencode','fixture-go-a','https://opencode.ai/zen/go/v1')]:\n p=owner.providers.save(name,model,url,'openai','fixture-only-key');owner.service.check_provider_profile(p['credential_id'],lambda *_:{'status':'verified','reason':'fixture'});saved[name]=p['credential_id'];(root/'profiles'/f'{name}.json').write_text(json.dumps(p['config']))\ns=MobileService(profile='codex',workdir=str(root/'workspace'));sid=s.create_session(profile='codex',cwd=str(root/'workspace'))['session_id'];_append_entry(_session_path(sid),'user','Retained fixture history');_append_entry(_session_path(sid),'assistant','Retained fixture response');from factory.core.scheduler import TaskScheduler\nscheduler=TaskScheduler(get_db_path());schedule=scheduler.create_schedule(name='Fixture due job',agent_id='opencode',payload='fixture-switch-delayed',schedule_type='hourly',time_value='00:00',tz='UTC',metadata={'cwd':str(root/'workspace'),'deliver_session':sid,'quiet':False});scheduler.update_schedule(schedule,enabled=False)\nowner.close();print(json.dumps({'session':sid,'profiles':saved,'schedule':schedule}))`;
 const fixture=JSON.parse(execFileSync(python,['-c',bootstrap,repo,data,base],{encoding:'utf8'}).trim().split('\n').at(-1));
 fs.writeFileSync(path.join(bundle,'python'),`#!/bin/sh\nexec '${python.replaceAll("'","'\\''")}' "$@"\n`,{mode:0o755});
 fs.writeFileSync(path.join(bundle,'backend.py'),`import sys,os,runpy\nfrom pathlib import Path\nrepo=Path(${JSON.stringify(repo)})\nsys.path[:0]=[str(repo),str(repo/'desktop')]\nos.environ.update(IGOREK_FACTORY_HOME=sys.argv[1],FACTORY_DATA_DIR=sys.argv[1],FACTORY_SESSION_EMBEDDINGS_ENABLED='off')\nprotocol_output=sys.stdout\nsys.stdout=sys.stderr\nimport factory.core.opencode_go_catalog as catalog\ncatalog.OPENCODE_GO_MODELS_URL=${JSON.stringify(base+'/models')}\ncatalog.get_opencode_go_routes=lambda:{'fixture-go-a':('openai',${JSON.stringify(base)},'Fixture Go A'),'fixture-go-b':('openai',${JSON.stringify(base)},'Fixture Go B')}\nfrom factory.core.mcp_bridge import set_desktop_access_check\nset_desktop_access_check(lambda *_:False)\nfrom factory.core.worker import Worker\noriginal_init=Worker.__init__\ndef fixture_init(self,profile,*args,**kwargs):\n marker=Path(sys.argv[1])/'pause-start'\n if profile=='codex' and marker.exists():\n  import time\n  marker.with_name('entered-start').write_text('fixture')\n  while marker.exists():time.sleep(0.01)\n return original_init(self,profile,*args,**kwargs)\nWorker.__init__=fixture_init\noriginal_claim=Worker.claim_task\ndef fixture_claim(self,conn):\n if (Path(sys.argv[1])/'pause-claim').exists():return None\n return original_claim(self,conn)\nWorker.claim_task=fixture_claim\nfrom factory.core import cc_backend_executor as executor\noriginal_post=executor._http_post_streaming_openai\ndef fixture_post(url,*args,**kwargs):\n if url.startswith('https://opencode.ai/zen/go/v1/'):\n  url=${JSON.stringify(base)}+url[len('https://opencode.ai/zen/go/v1'):]\n return original_post(url,*args,**kwargs)\nexecutor._http_post_streaming_openai=fixture_post\nimport factory.core.scheduler as schedules\nschedules.get_scheduler().check_interval=1\nimport factory.web_ui.server as web\nweb._profile_context_window=lambda *_:8192\nsys.stdout=protocol_output\nrunpy.run_path(str(repo/'desktop/backend.py'),run_name='__main__')\n`);
 fs.writeFileSync(path.join(bundle,'manifest.json'),JSON.stringify({schema_version:1,python:'python',entry:'backend.py'}));
 process.argv.splice(2,process.argv.length-2,'--managed','--runtime',bundle,'--data-dir',data);
 require(path.join(repo,'desktop/main.cjs'));
 await until(()=>{const windows=BrowserWindow.getAllWindows();owner=windows.find(w=>w.webContents.getURL().endsWith('/owner.html'));chat=windows.find(w=>w.webContents.getURL().startsWith('https://127.0.0.1:'));return owner&&chat;},'Actual main windows missing');
 await until(()=>evaluate(owner,'Boolean(window.igorekVault)'), 'Owner preload missing');
 assert.equal((await ownerCall('unlock','fixture-password')).state,'unlocked');
 await ownerCall('startAgent',fixture.profiles.codex);
 const route='/api/sessions/'+fixture.session;
 await evaluate(chat,`selectSession(${JSON.stringify(fixture.session)})`);
 assert.equal((await api(route+'/messages',{text:'fixture-history',idempotency_key:'fixture-history'})).status,202);
 await until(async()=>{const x=await api(route);return !x.body.running_task_id&&x.body.messages.some(m=>m.text?.startsWith('fixture-switch-ready'));},'Original worker task did not finish');
 await ownerCall('startAgent',fixture.profiles.opencode);
 owner.hide();chat.show();chat.focus();
 await evaluate(chat,`selectSession(${JSON.stringify(fixture.session)})`);
 assert.equal(await evaluate(chat,'state.session.meta.profile'),'opencode');
 assert.ok((await api(route)).body.messages.some(m=>m.text?.startsWith('fixture-switch-ready')));
 await until(()=>evaluate(chat,'document.querySelector("#timeline").textContent.includes("fixture-switch-ready")'),'Restored history missing');
 console.log('PASS actual main: settings-started worker selects the reopened chat profile');
 await evaluate(chat,"document.querySelector('#message-input').value='Retained draft';document.querySelector('#message-input').dispatchEvent(new Event('input',{bubbles:true}))");
 for(let i=0;i<12;i++){const name=i%2?'opencode':'codex';
  if(i===0){fs.writeFileSync(path.join(data,'pause-start'),'fixture');const switching=chooseProfile(name);
   await until(()=>fs.existsSync(path.join(data,'entered-start')),'Pending composer start was not reached');
   assert.equal((await ownerCall('status')).state,'unlocked');await ownerCall('listProfiles');
   const rejected=await evaluate(owner,`window.igorekVault.startAgent(${JSON.stringify(fixture.profiles.opencode)})`);assert.equal(rejected.error,'agent_busy');
   assert.equal((await ownerCall('status')).state,'unlocked');await ownerCall('listProfiles');
   fs.unlinkSync(path.join(data,'pause-start'));await switching;
  }else await chooseProfile(name);
 }
 assert.equal(await evaluate(chat,'document.querySelector("#message-input").value'),'Retained draft');
 await until(()=>evaluate(chat,'Boolean(document.querySelector(".composer-profile-option[data-model=fixture-go-b]"))'),'Go models did not appear without reopening');
 await click('.composer-profile-option[data-model=fixture-go-b]');
 await until(()=>evaluate(chat,'state.session.meta.model==="fixture-go-b"&&!document.querySelector(".composer-profile-menu")'),'Go model selection did not finish');
 console.log('PASS actual main: repeated composer switches keep Vault responsive, history/draft, and show models immediately');
 const sent=await api(route+'/messages',{text:'fixture-selected-model',idempotency_key:'fixture-selected'});assert.equal(sent.status,202);
 await until(async()=>{const x=await api(route);return !x.body.running_task_id&&x.body.messages.some(m=>m.task_id===sent.body.task_id&&m.text?.startsWith('fixture-switch-ready'));},'Selected model task did not finish');
 assert.deepEqual(calls.at(-1),{model:'fixture-go-b',key:true});
 await until(()=>evaluate(chat,'state.activeTasks.size===0'),'Completed task did not release composer');
 fs.writeFileSync(path.join(data,'pause-claim'),'fixture');
 execFileSync(python,['-c',"import sqlite3,sys,time; c=sqlite3.connect(sys.argv[1]); c.execute('UPDATE schedules SET enabled=1,next_run_at=? WHERE schedule_id=?',(int(time.time())-1,sys.argv[2])); c.commit(); c.close()",path.join(data,'database/factory.db'),fixture.schedule]);
 await until(async()=>{const x=await api('/api/schedules');return x.body.schedules.some(row=>row.schedule_id===fixture.schedule&&row.last_status==='queued'&&row.run_count===1);},'Clock-driven schedule was not queued');
 const refused=await evaluate(owner,`window.igorekVault.startAgent(${JSON.stringify(fixture.profiles.codex)})`);assert.equal(refused.error,'agent_busy');
 assert.equal((await api(route)).body.running_task_id,null);
 await click('#composer-model');await until(()=>evaluate(chat,'Boolean(document.querySelector(".composer-profile-toggle"))'),'Composer menu missing');
 await click('.composer-profile-toggle');await until(()=>evaluate(chat,'document.querySelector(".composer-profile-toggle").getAttribute("aria-expanded")==="true"'),'Other profiles did not expand');
 await click('.composer-profile-option[data-profile=codex]');
 const statusWhileBusy=ownerCall('status');
 await until(()=>evaluate(chat,'document.querySelector(".composer-profile-status")?.getAttribute("role")==="alert"'),'Scheduled profile switch did not report busy');
 assert.equal(await evaluate(chat,'state.session.meta.profile'),'opencode');
 assert.equal((await statusWhileBusy).state,'unlocked');
 assert.equal((await ownerCall('listProfiles')).agent.profile,'opencode');
 fs.unlinkSync(path.join(data,'pause-claim'));
 await until(()=>heldResponse,'Queued scheduled worker did not reach controlled provider');
 releaseHeld();releaseHeld=null;
 let scheduleTask;
 await until(async()=>{const x=await api('/api/schedules');const row=x.body.schedules.find(item=>item.schedule_id===fixture.schedule);scheduleTask=row?.last_task_id;return row?.last_status==='ok'&&row?.delivery_status==='delivered'&&row?.run_count===1;},'Scheduled task did not finish and deliver');
 assert.equal((await api(route)).body.messages.filter(m=>m.message_id==='schedule-'+scheduleTask).length,1);
 assert.deepEqual(calls.at(-1),{model:'fixture-go-a',key:true});
 chat.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});chat.webContents.sendInputEvent({type:'keyUp',keyCode:'Escape'});
 await until(()=>evaluate(chat,'!document.querySelector(".composer-profile-menu")'),'Busy menu did not close');
 await chooseProfile('codex');
 await ownerCall('startAgent',fixture.profiles.opencode);
 await until(()=>evaluate(chat,'state.session.meta.profile==="opencode"&&!document.querySelector(".composer-profile-menu")'),'Settings change did not update the open chat');
 assert.equal((await ownerCall('status')).state,'unlocked');
 assert.equal((await ownerCall('listProfiles')).profiles.length,2);
 const saved=await ownerCall('saveValue',{credential_id:'',name:'SWITCH_FIXTURE',kind:'variable',value:'retained',password:'fixture-password'});
 assert.equal((await ownerCall('revealValue',saved.saved_value.credential_id,'fixture-password')).secret,'retained');
 await ownerCall('deleteValue',saved.saved_value.credential_id,'fixture-password');
 await ownerCall('returnToChat');
 await evaluate(chat,"window.IgorekDesktop.navigation('open','vault')");
 await until(()=>{owner=BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().endsWith('/owner.html'));return !!owner;},'Vault window did not reopen');
 await until(()=>evaluate(owner,'Boolean(window.igorekVault)'), 'Reopened owner preload missing');
 assert.equal((await ownerCall('status')).state,'unlocked');
 assert.equal((await ownerCall('listProfiles')).profiles.length,2);
 await ownerCall('stopAgent');
 await chooseProfile('opencode');
 await until(async()=>(await ownerCall('status')).agent.running,'Stopped current profile did not restart from composer');
 fs.writeFileSync(path.join(output,'profile-switch.png'),(await chat.webContents.capturePage()).toPNG());
 fs.writeFileSync(path.join(output,'summary.json'),JSON.stringify({status:'passed',actual_main:true,actual_owner_pipe:true,actual_workers:true,provider_network:'controlled_fixture',reopened_chat_follows_settings:true,open_chat_follows_settings:true,composer_switches:13,vault_read_write_and_reopen:true,history_and_draft_retained:true,models_without_reopening:true,selected_model_executed:true,busy_rejected_vault_available:true,clock_driven_schedule_in_separate_session:true,scheduled_result_delivered_once:true,settings_rejects_queued_schedule:true,control_reads_during_pending_switch:true,stopped_profile_can_restart_from_composer:true,failures},null,2));
 succeeded=true;console.log('PASS actual main: selected Go model executed; busy switch rejected without losing Vault; settings change updates the open chat');
}
const timer=setTimeout(()=>{console.error('Native profile switch timeout');if(dataRoot)fs.rmSync(path.join(dataRoot,'pause-start'),{force:true});releaseHeld?.();app.once('will-quit',()=>app.exit(1));app.quit();},120000);timer.unref();
run().then(()=>{clearTimeout(timer);server.close();server.closeAllConnections();app.quit();}).catch(async error=>{if(dataRoot)fs.rmSync(path.join(dataRoot,'pause-start'),{force:true});console.error(error.stack);if(chat)console.error('UI PROBE',JSON.stringify(await evaluate(chat,'({profile:state.session?.meta?.profile,selected:state.selected,items:state.items,history:state.session?.messages,journal:state.journal,body:document.body.textContent.slice(-2000),timeline:document.querySelector("#timeline")?.textContent,alerts:[...document.querySelectorAll(".toast")].map(x=>x.textContent)})').catch(()=>null)));fs.writeFileSync(path.join(output,'failure.json'),JSON.stringify({error:error.message,failures},null,2));releaseHeld?.();server?.close();server?.closeAllConnections();app.once('will-quit',()=>app.exit(1));app.quit();});
