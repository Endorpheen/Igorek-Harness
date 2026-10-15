const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {ReleaseUpdates,assetFor,newer,allowedRedirect,REPOSITORY} = require('../release-updates.cjs');
const {updateDialog} = require('../release-updates-ui.cjs');
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
test('no releases is a normal first-publication state; current version needs no asset',async t=>{
  const f=await fixture(t);f.updater.fetch=async()=>new Response(null,{status:404});assert.equal((await f.updater.check()).state,'no_release');
  f.updater.fetch=async()=>Response.json({tag_name:'v0.2.0'});assert.equal((await f.updater.check()).state,'current');
});
test('native cancel never downloads; confirmed download only reveals verified installer',async t=>{
  const f=await fixture(t,release(process.platform));const shown=[];let index=0;
  const parent={isDestroyed:()=>false,setProgressBar:()=>{}};
  const decisions=[{response:2},{response:0},{response:0}];
  const show=updateDialog({app:{getPath:()=>f.root},dialog:{showMessageBox:async()=>decisions[index++]},
    shell:{openExternal:()=>assert.fail('No release link was requested'),showItemInFolder:file=>shown.push(file)},
    session:{fetch:f.fetch},current:()=> '0.2.0',portable:false});
  assert.equal((await show(parent)).state,'cancelled');assert.equal(f.requests.length,1);assert.deepEqual(shown,[]);
  // Match the synthetic release to the host without executing its payload.
  f.fetch=async(url)=>url.includes('/releases/latest')?Response.json(release(process.platform)):new Response(payload);
  const showConfirmed=updateDialog({app:{getPath:()=>f.root},dialog:{showMessageBox:async()=>({response:0})},
    shell:{showItemInFolder:file=>shown.push(file)},session:{fetch:f.fetch},current:()=> '0.2.0',portable:false});
  assert.equal((await showConfirmed(parent)).state,'downloaded');assert.equal(shown.length,1);assert.deepEqual(await fs.readFile(shown[0]),payload);
});
