const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {ReleaseUpdates,assetFor,newer,allowedRedirect,REPOSITORY} = require('../release-updates.cjs');
const {updateDialog} = require('../release-updates-ui.cjs');
const {installerEnvironment,installationTarget,launchInstallation,showInstallationResult} = require('../release-install.cjs');
const payload = Buffer.from('synthetic installer; never executed');
function release(platform='win32') {
  const name=platform==='win32'?'igorek-0.2.1-win-x64.exe':'igorek-0.2.1-linux-amd64.deb';
  return {tag_name:'v0.2.1',draft:false,prerelease:false,assets:[{name,size:payload.length,
    digest:'sha256:'+createHash('sha256').update(payload).digest('hex'),
    browser_download_url:`https://github.com/${REPOSITORY}/releases/download/v0.2.1/${name}`}]};
}
async function fixture(t, response=release()) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'igorek-updates-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const requests=[];
  const fetch=async(url,options)=>{requests.push({url,options});return url.includes('/releases/latest')
    ? Response.json(response):new Response(payload);};
  return {root,requests,fetch,updater:new ReleaseUpdates({current:'0.2.0',platform:'win32',arch:'x64',cache:path.join(root,'cache'),fetch})};
}
test('release selection is numeric and binds system, repository, size and digest',()=>{
  assert.ok(newer('v0.2.10','0.2.9'));assert.ok(!newer('0.2.0','0.2.0'));
  for(const value of ['latest','v0.2.1-beta','../../run','1.2.3.4'])assert.throws(()=>newer(value,'0.2.0'));
  assert.equal(assetFor(release(),'win32','x64').name,'igorek-0.2.1-win-x64.exe');
  assert.equal(assetFor(release('linux'),'linux','x64').name,'igorek-0.2.1-linux-amd64.deb');
  for(const mutate of [r=>r.draft=true,r=>r.prerelease=true,r=>r.assets.push({...r.assets[0]}),
    r=>r.assets[0].digest=null,r=>r.assets[0].size=3e9,r=>r.assets[0].browser_download_url='https://evil.invalid/setup.exe']){
    const value=release();mutate(value);assert.throws(()=>assetFor(value,'win32','x64'));
  }
  assert.throws(()=>assetFor(release(),'win32','arm64'));
  assert.ok(allowedRedirect('https://release-assets.githubusercontent.com/asset?temporary=value'));
  for(const url of ['http://github.com/a','https://github.com.evil.invalid/a','https://127.0.0.1/a','https://user:pass@github.com/a','https://github.com:444/a'])assert.ok(!allowedRedirect(url));
});
test('native install requires a second owner confirmation and keeps verified metadata',async t=>{
  const f=await fixture(t,release(process.platform));let launched=0;
  const parent={isDestroyed:()=>false,setProgressBar:()=>{}};
  const choices=[0,2,0,0];
  const show=updateDialog({app:{getPath:()=>f.root},dialog:{showMessageBox:async()=>({response:choices.shift()})},
    shell:{showItemInFolder:()=>assert.fail('Install was selected')},fetch:f.fetch,current:()=> '0.2.0',portable:false,
    install:async file=>{launched++;assert.equal(file.sha256,createHash('sha256').update(payload).digest('hex'));
      assert.equal(file.version,'0.2.1');assert.deepEqual(await fs.readFile(file.path),payload);return {state:'installing'};}});
  assert.equal((await show(parent)).state,'downloaded');assert.equal(launched,0);
  assert.equal((await show(parent)).state,'installing');assert.equal(launched,1);
});
test('update helper receives OS/display paths but no account credentials',()=>{
  assert.deepEqual(installerEnvironment({HOME:'/fixture',PATH:'/bin',DISPLAY:':99',OPENAI_API_KEY:'private',
    ANTHROPIC_AUTH_TOKEN:'private',FACTORY_EXTERNAL_CODEX_HOME:'/private'}),{PATH:'/bin',HOME:'/fixture',DISPLAY:':99'});
});
test('a source checkout cannot become an automatic installed-snapshot target',async t=>{
  const f=await fixture(t);
  await assert.rejects(installationTarget({app:{getAppPath:()=>f.root},platform:'linux',appimage:null}));
});
test('installation waits for helper readiness and refuses a busy owner before spawn', {skip:process.platform!=='linux'},async t=>{
  const f=await fixture(t);f.updater.cache=path.join(f.root,'release-downloads');await f.updater.check();const file=await f.updater.download();
  const appPath=path.join(f.root,'installed/releases/build/app');await fs.mkdir(appPath,{recursive:true});
  await fs.writeFile(path.join(f.root,'installed/installation.json'),JSON.stringify({schema_version:1,kind:'igorek-linux-user-installation'}));
  let quit=0,spawned=0;
  const app={getPath:()=>f.root,getAppPath:()=>appPath,quit:()=>quit++};
  await assert.rejects(launchInstallation(file,{app,python:'/fixture/python',dataDir:f.root,prepare:async()=>{throw Error('update_tasks_active');},
    spawnChild:()=>{spawned++;assert.fail('Busy owner must not spawn installer');}}),/tasks_active/);
  assert.equal(spawned,0);assert.equal(quit,0);
  const second=await f.updater.download();
  const {EventEmitter}=require('node:events');
  const result=await launchInstallation(second,{app,python:'/fixture/python',dataDir:f.root,prepare:async()=>{},spawnChild:(python,args,options)=>{
    spawned++;assert.equal(options.env.OPENAI_API_KEY,undefined);assert.equal(options.detached,true);
    const child=new EventEmitter();child.exitCode=null;child.unref=()=>{};child.kill=()=>assert.fail('Ready helper must survive desktop exit');
    fs.writeFile(args.at(-1).replace(/\.json$/,'.ready'),'ready');return child;}});
  assert.equal(result.state,'installing');assert.equal(spawned,1);assert.equal(quit,1);
});
test('update completion receipt is displayed once and cannot execute a payload',async t=>{
  const f=await fixture(t);const cache=path.join(f.root,'release-downloads');await fs.mkdir(cache);
  await fs.writeFile(path.join(cache,'last-install-result.json'),JSON.stringify({state:'failed'}));
  let shown=0;const options={app:{getPath:()=>f.root},dialog:{showMessageBox:async()=>shown++},parent:{}};
  await showInstallationResult(options);await showInstallationResult(options);assert.equal(shown,1);
});
test('check performs no install or download; verified bytes are stored without credentials',async t=>{
  const f=await fixture(t);assert.equal((await f.updater.check()).state,'available');assert.equal(f.requests.length,1);
  const saved=await f.updater.download();assert.deepEqual(await fs.readFile(saved.path),payload);
  assert.ok(f.requests.every(x=>x.options.credentials==='omit' && !x.options.headers.Authorization));
  assert.ok(f.requests.every(x=>x.options.redirect!=='follow'));
});
test('checksum, truncation and extra bytes fail without leaving a candidate installer',async t=>{
  for(const body of [Buffer.from('tampered installer; never executed'),payload.subarray(0,4),Buffer.concat([payload,Buffer.from('extra')])]){
    const f=await fixture(t);await f.updater.check();f.updater.fetch=async()=>new Response(body);
    await assert.rejects(f.updater.download());assert.deepEqual(await fs.readdir(path.join(f.root,'cache')),[]);
  }
});
test('foreign redirects and stale failed metadata cannot authorize a download',async t=>{
  const f=await fixture(t);await f.updater.check();f.updater.fetch=async()=>new Response(null,{status:302,headers:{location:'https://127.0.0.1/private'}});
  await assert.rejects(f.updater.download(),/redirect_denied/);
  f.updater.fetch=async()=>{throw Error('offline');};await assert.rejects(f.updater.check());await assert.rejects(f.updater.download(),/not_ready/);
});
test('foreign redirect is rejected before any request reaches it',async t=>{
  const f=await fixture(t);await f.updater.check();const requests=[];
  f.updater.fetch=async url=>{requests.push(url);return new Response(null,{status:302,headers:{location:'https://127.0.0.1/private'}});};
  await assert.rejects(f.updater.download(),/redirect_denied/);
  assert.equal(requests.length,1);
  assert.equal(new URL(requests[0]).hostname,'github.com');
});
test('no releases is a normal first-publication state; current version needs no asset',async t=>{
  const f=await fixture(t);f.updater.fetch=async()=>new Response(null,{status:404});assert.equal((await f.updater.check()).state,'no_release');
  f.updater.fetch=async()=>Response.json({tag_name:'v0.2.0'});assert.equal((await f.updater.check()).state,'current');
});
test('preview channel discovers newer preliminary builds while drafts and foreign tags stay excluded',async t=>{
  const f=await fixture(t);f.updater.preview=true;
  const candidate=release();candidate.prerelease=true;
  f.updater.fetch=async url=>{assert.ok(url.endsWith('/releases?per_page=20'));return Response.json([
    {...release(),tag_name:'v9.0.0',draft:true},{tag_name:'android-v9.0.0'},candidate]);};
  assert.equal((await f.updater.check()).version,'0.2.1');
  assert.throws(()=>assetFor(candidate,'win32','x64'));
});
test('a postponed verified download is reused after a busy task instead of being fetched again',async t=>{
  const f=await fixture(t,release(process.platform));let downloads=0,attempts=0;
  const show=updateDialog({app:{getPath:()=>f.root},dialog:{showMessageBox:async()=>({response:0})},shell:{},
    fetch:async(url,options)=>{if(!url.includes('/releases/latest'))downloads++;return f.fetch(url,options);},
    current:()=> '0.2.0',portable:false,install:async()=>{if(!attempts++)throw Error('update_tasks_active');return {state:'installing'};}});
  const parent={isDestroyed:()=>false,setProgressBar:()=>{}};
  assert.equal((await show(parent)).state,'failed');assert.equal((await show(parent)).state,'installing');assert.equal(downloads,1);
});
test('native cancel never downloads; confirmed download only reveals verified installer',async t=>{
  const f=await fixture(t,release(process.platform));const shown=[];let index=0;
  const parent={isDestroyed:()=>false,setProgressBar:()=>{}};
  const decisions=[{response:2},{response:0},{response:0}];
  const show=updateDialog({app:{getPath:()=>f.root},dialog:{showMessageBox:async()=>decisions[index++]},
    shell:{openExternal:()=>assert.fail('No release link was requested'),showItemInFolder:file=>shown.push(file)},
    fetch:f.fetch,current:()=> '0.2.0',portable:false});
  assert.equal((await show(parent)).state,'cancelled');assert.equal(f.requests.length,1);assert.deepEqual(shown,[]);
  // Match the synthetic release to the host without executing its payload.
  f.fetch=async(url)=>url.includes('/releases/latest')?Response.json(release(process.platform)):new Response(payload);
  const showConfirmed=updateDialog({app:{getPath:()=>f.root},dialog:{showMessageBox:async()=>({response:0})},
    shell:{showItemInFolder:file=>shown.push(file)},fetch:f.fetch,current:()=> '0.2.0',portable:false});
  assert.equal((await showConfirmed(parent)).state,'downloaded');assert.equal(shown.length,1);assert.deepEqual(await fs.readFile(shown[0]),payload);
});

test('dialog uses Node fetch rather than Chromium manual-redirect cancellation',async t=>{
  const f=await fixture(t,release(process.platform));let redirects=0;
  const show=updateDialog({app:{getPath:()=>f.root},current:()=> '0.2.0',portable:false,
    session:{fetch:()=>assert.fail('Chromium fetch cannot expose manual redirects')},
    fetch:async(url,options)=>{
      if(url.includes('/releases/latest'))return Response.json(release(process.platform));
      assert.equal(options.redirect,'manual');
      if(new URL(url).hostname==='github.com'){
        redirects++;return new Response(null,{status:302,headers:{location:'https://release-assets.githubusercontent.com/fixture'}});
      }
      return new Response(payload);
    },dialog:{showMessageBox:async()=>({response:0})},shell:{showItemInFolder:()=>{}}});
  assert.equal((await show({isDestroyed:()=>false,setProgressBar:()=>{}})).state,'downloaded');
  assert.equal(redirects,1);
});

test('one update presentation stays alive through download, verification and confirmation',async t=>{
  const f=await fixture(t,release(process.platform));const states=[];let created=0;
  const presentation={showMessageBox:async(_parent,options)=>{states.push(options.message);return {response:options.message.includes('готов к установке')?1:0};},
    progress:value=>states.push(value),close:()=>states.push('closed')};
  const show=updateDialog({app:{getPath:()=>f.root},current:()=> '0.2.0',portable:false,fetch:f.fetch,
    presentationFactory:()=>{created++;return presentation;},
    dialog:{showMessageBox:()=>assert.fail('Native dialog must not replace the progress window')},shell:{showItemInFolder:()=>{}}});
  assert.equal((await show({isDestroyed:()=>false,setProgressBar:()=>{}})).state,'downloaded');
  assert.equal(created,1);assert.ok(states.includes(0));assert.ok(states.includes(1));
  assert.ok(states.some(value=>typeof value==='string'&&value.includes('готов к установке')));
  assert.equal(states.at(-1),'closed');
});
