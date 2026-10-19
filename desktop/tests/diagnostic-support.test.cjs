const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {tmpdir}=require('node:os');
const support=require('../diagnostic-support.js');
const {saveAndCompose}=require('../diagnostic-support.cjs');
const fixture=()=>JSON.parse(readFileSync(join(__dirname,'../../tests/fixtures/diagnostics/support-report.json'),'utf8'));

test('support reports retain useful references and exclude arbitrary snapshot fields',()=>{
  const report=support.createReport(fixture(),{version:'0.2.13',surface:'linux'});
  assert.equal(report.local.incidents[0].error.http_request_id,'req_fixture');
  assert.equal(report.local.incidents[0].error.model,'org/model');
  assert.ok(!JSON.stringify(report).includes('EXCLUDED'));
  assert.equal(report.server.available,false);
  assert.deepEqual(support.createReport(report,{version:'0.2.13',surface:'linux'}),report);
  const link=new URL(support.mailto({...report,recipient:'other@example.invalid'}));
  assert.equal(link.pathname,'igorek-dot@tutamail.com');
  assert.ok(!link.searchParams.has('attachment'));
});
test('oversized reports retain recent bounded data and disclose omissions',()=>{
  const input=fixture(),large=input.local.incidents[0];
  const error=Object.fromEntries(['component','operation','owner','phase','http_request_id','client_operation_id','provider_request_id','task_id','session_id','tool_use_id','profile','model','native_code'].map(key=>[key,'a'.repeat(160)]));
  large.events=Array.from({length:8},(_,i)=>({...large.error,...error,event_id:'event-'+i}));
  input.local.incidents=Array.from({length:100},(_,i)=>({...large,incident_id:'incident-'+i,last_seen_ms:i}));
  const report=support.createReport(input);
  assert.ok(Buffer.byteLength(JSON.stringify(report,null,2))<=support.MAX_BYTES);
  assert.equal(report.truncated,true);assert.ok(report.local.omitted_incidents>0);
});
test('malformed and missing snapshots remain local reports without invented availability',()=>{
  for(const local of [null,[],{available:false},{activity:[null,'bad'],incidents:[null]}])assert.doesNotThrow(()=>support.createReport({local}));
  const report=support.createReport({local:{available:false},server:{available:false}});
  assert.equal(report.local.available,false);assert.equal(report.server.available,false);
});
test('native cancellation and stale frames do not write a file or open email',async()=>{
  const root=await fs.mkdtemp(join(tmpdir(),'igorek-support-cancel-'));
  try{
    let opened=0;const file=join(root,'report.json');
    for(const extra of [{chooseFile:async()=>({canceled:true})},{chooseFile:async()=>({filePath:file}),isActive:()=>false}]){
      const result=await saveAndCompose({report:support.createReport(fixture())},{version:'0.2.13',surface:'linux',documents:root,openComposer:async()=>opened++,...extra});
      assert.equal(result.state,'cancelled');
    }
    assert.equal(opened,0);assert.deepEqual(await fs.readdir(root),[]);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});
test('native export saves exactly the reviewed safe report and only opens the fixed composer',async()=>{
  const root=await fs.mkdtemp(join(tmpdir(),'igorek-support-save-'));
  try{
    const file=join(root,'report.json'),report=support.createReport(fixture(),{version:'0.2.13',surface:'linux'});let url;
    const result=await saveAndCompose({report,language:'en'},{version:'0.2.13',surface:'linux',documents:root,chooseFile:async()=>({filePath:file}),openComposer:async value=>{url=value;}});
    assert.equal(result.state,'composer_opened');assert.deepEqual(JSON.parse(await fs.readFile(file,'utf8')),report);
    assert.equal(new URL(url).pathname,'igorek-dot@tutamail.com');assert.ok(!url.includes(root));
    assert.ok(!('delivered' in result));
  }finally{await fs.rm(root,{recursive:true,force:true});}
});
test('existing files are never overwritten and missing mail handlers retain the saved report',async()=>{
  const root=await fs.mkdtemp(join(tmpdir(),'igorek-support-file-'));
  try{
    const file=join(root,'report.json');let opened=0;
    const options={version:'0.2.13',surface:'linux',documents:root,chooseFile:async()=>({filePath:file}),openComposer:async()=>{opened++;throw new Error('No handler');}};
    await fs.writeFile(file,'original');
    assert.equal((await saveAndCompose({report:support.createReport(fixture())},options)).state,'name_conflict');
    assert.equal(await fs.readFile(file,'utf8'),'original');assert.equal(opened,0);
    await fs.unlink(file);
    assert.equal((await saveAndCompose({report:support.createReport(fixture())},options)).state,'saved');
    assert.ok(JSON.parse(await fs.readFile(file,'utf8')).report_id);assert.equal(opened,1);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});

test('closing the owner after saving retains the file without launching an external client',async()=>{
  const root=await fs.mkdtemp(join(tmpdir(),'igorek-support-closed-'));
  try{
    const file=join(root,'report.json');let checks=0,opened=0;
    const result=await saveAndCompose({report:support.createReport(fixture())},{version:'0.2.13',surface:'linux',documents:root,
      chooseFile:async()=>({filePath:file}),isActive:()=>++checks<3,openComposer:async()=>opened++});
    assert.equal(result.state,'saved');assert.equal(opened,0);assert.ok(JSON.parse(await fs.readFile(file,'utf8')).report_id);
  }finally{await fs.rm(root,{recursive:true,force:true});}
});
test('support IPC rejects foreign frames and unknown actions before any external effect',async()=>{
  const vm=require('node:vm'),source=readFileSync(join(__dirname,'../main.cjs'),'utf8');let handler,called=0;
  const target={};
  const begin=source.indexOf("ipcMain.handle('igorek:diagnostic-support'");
  const end=source.indexOf('function installWindowChrome',begin);
  vm.runInNewContext(source.slice(begin,end),{ipcMain:{handle:(_name,callback)=>{handler=callback;}},
    trustedChromeWindow:event=>{if(!event.trusted)throw Error('denied');return target;},
    require:name=>name==='./package.json'?{version:'0.2.13'}:{createReport:support.createReport,saveAndCompose:async()=>{called++;return {state:'saved'};}},
    process:{platform:'linux'},app:{getPath:()=>'/synthetic'},dialog:{showSaveDialog:async()=>{throw Error('unexpected dialog');}},shell:{openExternal:async()=>{throw Error('unexpected email');}}});
  await assert.rejects(handler({trusted:false},{action:'save'}));assert.equal(called,0);
  await assert.rejects(handler({trusted:true},{action:'other'}));assert.equal(called,0);
  const prepared=await handler({trusted:true},{action:'prepare',report:support.createReport(fixture())});
  assert.equal(prepared.surface,'linux');assert.equal(called,0);
});

test('offline browser collection keeps local diagnostics and never invokes a write or retry',async()=>{
  const vm=require('node:vm'),source=readFileSync(join(__dirname,'../../factory/web_ui/static/app.js'),'utf8');
  const requests=[],c={window:{IgorekDiagnosticSupport:{...support,configure(){}}},localDiagnosticJournal:{snapshot:()=>fixture().local},AbortSignal,
    fetch:async(path,options)=>{requests.push({path,options});throw new TypeError('offline');}};
  vm.createContext(c);
  vm.runInContext(source.slice(source.indexOf('async function collectDiagnosticSupport('),source.indexOf('async function copyDiagnostics(')),c);
  const report=await c.collectDiagnosticSupport();
  assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/diagnostics');
  assert.equal(requests[0].options.credentials,'same-origin');assert.ok(requests[0].options.signal);
  assert.equal(report.server.available,false);assert.equal(report.local.available,true);
  assert.ok(!JSON.stringify(report).includes('EXCLUDED'));
});

test('missing source event and incident references are not manufactured during export',()=>{
  const report=support.createReport({local:{incidents:[{error:{code:'NET.CONNECTION_RESET'}}]}});
  const item=report.local.incidents[0];assert.equal(item.incident_id,'');
  assert.ok(!('event_id' in item.error));assert.ok(!('observed_at_ms' in item.error));
});
