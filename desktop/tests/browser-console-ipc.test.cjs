'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function fixture(){
 const source=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8'),calls=[];
 const parent={},sender={};let trusted=true,handler;
 const context={ipcMain:{handle(name,fn){assert.equal(name,'igorek:workspace-browser');handler=fn;}},sessionMcp:{trusted:()=>trusted},BrowserWindow:{fromWebContents:wc=>{assert.equal(wc,sender);return parent;}},workspaceBrowser:{action:async(...args)=>{calls.push(args);return {value:'fixture'};}},runtime:{requestOwner(){throw Error('console must not unlock the interface')}}};
 vm.runInNewContext(source.slice(source.indexOf("ipcMain.handle('igorek:workspace-browser'"),source.indexOf('async function completeQuotaReply')),context);
 return {calls,parent,event:{sender},handler,untrust(){trusted=false;}};
}
test('the production Desktop IPC entry routes each console operation to the guarded browser manager',async()=>{
 const f=fixture();
 for(const operation of ['console','console_messages','console_clear']){
  const fields={tab_id:'fixture-tab',...(operation==='console'?{expression:'40+2'}:{})};
  assert.deepEqual(await f.handler(f.event,operation,fields),{value:'fixture'});
  assert.deepEqual(f.calls.at(-1),[f.parent,operation,fields]);
 }
});
test('the console IPC entry retains the trusted-renderer boundary and rejects unknown operations',async()=>{
 const f=fixture();f.untrust();
 await assert.rejects(f.handler(f.event,'console',{tab_id:'fixture-tab',expression:'1'}));assert.equal(f.calls.length,0);
 const other=fixture();await assert.rejects(other.handler(other.event,'host_shell',{}));assert.equal(other.calls.length,0);
});
