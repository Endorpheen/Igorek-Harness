const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm');
const {ResourceSampler,ResourceMonitorSettings,parseGPUs}=require('../resource-monitor.cjs');
function system(){let count=0;return{cpus:()=>[{times:{idle:count++?140:100,user:count>1?160:100}}],totalmem:()=>10000,freemem:()=>1000,platform:()=> 'linux'};}
test('GPU CSV retains separate devices, units, unavailable values and clamped load',()=>{
 const rows=parseGPUs('RTX 4070, 4096, 12282, 25, 54, 76.2, 200\n"GPU, second", N/A, 100, 150, [Not Supported], N/A, N/A\nmalformed\n');
 assert.equal(rows.length,2);assert.equal(rows[0].used,4096*1048576);assert.equal(rows[0].power,76.2);assert.equal(rows[1].name,'GPU, second');assert.equal(rows[1].load,100);assert.equal(rows[1].used,null);assert.equal(rows[1].temperature,null);
 assert.deepEqual(parseGPUs(''),[]);assert.equal(parseGPUs('GPU, 0, 0, 0, 0, 0, 0')[0].total,null);
});
test('sampler measures CPU deltas and Linux available memory instead of counting cache as used',async()=>{
 const sampler=new ResourceSampler({system:system(),read:()=> 'MemAvailable: 5 kB\n',gpu:async()=>[],clock:()=>10000});
 const result=await sampler.snapshot();assert.equal(result.cpu,60);assert.deepEqual(result.ram,{total:10000,used:4880});assert.deepEqual(result.gpus,[]);
});
test('missing procfs and unavailable GPU preserve CPU and OS memory',async()=>{
 const sampler=new ResourceSampler({system:system(),read:()=>{throw Error('no procfs')},gpu:async()=>{throw Error('unsupported')}});
 const result=await sampler.snapshot();assert.equal(result.ram.used,9000);assert.deepEqual(result.gpus,[]);
});
test('concurrent and frequent reads share one bounded GPU request and cache',async()=>{
 let finish,count=0,now=0;const sampler=new ResourceSampler({system:system(),gpu:()=>{count++;return new Promise(r=>finish=r)},clock:()=>now});
 const first=sampler.snapshot(),second=sampler.snapshot();assert.equal(count,1);finish([]);assert.deepEqual(await first,await second);await sampler.snapshot();assert.equal(count,1);
 now=1501;const third=sampler.snapshot();assert.equal(count,2);finish([]);await third;
});
test('zero CPU time delta is unknown and memory bounds are retained',async()=>{
 const host={cpus:()=>[{times:{idle:1,user:1}}],totalmem:()=>100,freemem:()=>200,platform:()=> 'win32'};
 const result=await new ResourceSampler({system:host,gpu:async()=>[]}).snapshot();assert.equal(result.cpu,null);assert.equal(result.ram.used,0);
});
test('monitor defaults off, persists toggles and rejects invalid preferences',t=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-monitor-test-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));const file=path.join(root,'monitor.json');const setting=new ResourceMonitorSettings(file);
 assert.equal(setting.enabled,false);setting.set(true);assert.equal(new ResourceMonitorSettings(file).enabled,true);assert.throws(()=>setting.set('true'));assert.equal(setting.enabled,true);
 setting.set(false);assert.equal(new ResourceMonitorSettings(file).enabled,false);fs.writeFileSync(file,'broken');assert.equal(new ResourceMonitorSettings(file).enabled,false);
});
test('monitor preference cannot read or overwrite a symlink target',t=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-monitor-symlink-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));const file=path.join(root,'monitor.json'),target=path.join(root,'private');fs.writeFileSync(target,'retained');fs.symlinkSync(target,file);
 const setting=new ResourceMonitorSettings(file);assert.equal(setting.enabled,false);setting.set(true);assert.equal(fs.readFileSync(target,'utf8'),'retained');assert.equal(fs.lstatSync(file).isSymbolicLink(),false);
});
test('monitor preference saving failure leaves its accepted state unchanged',t=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-monitor-failure-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));const setting=new ResourceMonitorSettings(root);assert.throws(()=>setting.set(true));assert.equal(setting.enabled,false);
});
test('only the settings frame can change preferences and only the visible monitor can sample',async()=>{
 const text=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8'),start=text.indexOf("ipcMain.handle('igorek:resource-monitor'"),end=text.indexOf("ipcMain.handle('igorek:tts'",start);let handler,calls=0,closed=0;
 const contents={mainFrame:{},getURL:()=> 'file:///monitor'};const setting={enabled:true,set(value){this.enabled=value;return this.state()},state(){return{enabled:this.enabled}}};
 const window={isDestroyed:()=>false,isVisible:()=>true,webContents:contents,close(){closed++}};
 const context={ipcMain:{handle:(_,fn)=>handler=fn},resourceWindow:window,resourceURL:'file:///monitor',resourceSettings:setting,resourceSampler:{snapshot:async()=>{calls++;return{cpu:10}}},isOwnerFrame:e=>e.owner===true,ownerWindow:{},ownerUrl:'file:///owner',openResourceMonitor(){},publishResourceMonitor(){}};
 vm.runInNewContext(text.slice(start,end),context);
 await assert.rejects(handler({},'set',false));await assert.rejects(handler({owner:true},'set','yes'));
 const event={sender:contents,senderFrame:contents.mainFrame};event.senderFrame.url='file:///monitor';assert.equal((await handler(event,'snapshot')).cpu,10);assert.equal(calls,1);
 await assert.rejects(handler({...event,senderFrame:{url:'file:///monitor'}},'snapshot'));
 await handler(event,'close');assert.equal(setting.enabled,false);assert.equal(closed,1);await assert.rejects(handler(event,'snapshot'));
 assert.equal((await handler({owner:true},'set',true)).enabled,true);
});
