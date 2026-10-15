const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {sources}=require('../../factory/web_ui/static/session-summary.js');
test('sources use exact uploaded IDs, newest first, with no tool output or other roles',()=>{
 const file=n=>({upload_id:String(n).repeat(32),name:'file-'+n+'.png'});
 const result=sources([{type:'user',attachments:[file(1),file(2)]},{type:'assistant',attachments:[file(3)]},{type:'user',attachments:[file(1),file(4),{upload_id:'../../secret'}]}]);
 assert.deepEqual(result.map(item=>item.name),['file-4.png','file-2.png','file-1.png']);
});
test('release notes show only a matching version and bounded plain text',()=>{
 const source=fs.readFileSync(require('node:path').join(__dirname, '..','main.cjs'),'utf8');
 const context={config:{factoryVersion:'0.1.870'},app:{getVersion:()=>''},__dirname:'/test',join:require('node:path').join,readFileSync:()=>JSON.stringify({version:'0.1.870',changes:['One change',12,'Second change']})};vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function desktopReleaseInfo('),source.indexOf('function openAbout(')),context);
 assert.equal(JSON.stringify(context.desktopReleaseInfo()),JSON.stringify({version:'0.1.870',changes:['One change','Second change']}));context.config.factoryVersion='other';assert.equal(context.desktopReleaseInfo().changes.length,0);
});
test('agent-created tab imports native state and waits for queued UI selection',async()=>{
 const {Model}=require('../../factory/web_ui/static/workspace-tabs.js');const state={panel:{open:false,workspace:new Model()}},selected=[];let release;
 const context={window:{},state,workspaceBrowserState:{tabs:[]},workspaceBrowserSequence:new Promise(resolve=>release=resolve),showMainView(){},activateWorkspaceTab(tab){selected.push(context.workspaceBrowserState.tabs.some(item=>item.id===tab.id)?tab.id:null);},openPanel(){state.panel.open=true;}};
 vm.createContext(context);const source=fs.readFileSync(require('node:path').join(__dirname, '..','../factory/web_ui/static/app.js'),'utf8');vm.runInContext(source.slice(source.indexOf('window.IgorekShowAgentBrowser='),source.indexOf('function workspaceAddress(')),context);
 let settled=false;const pending=context.window.IgorekShowAgentBrowser('agent-test',{tabs:[{id:'agent-test'}]}).then(value=>{settled=true;return value;});await Promise.resolve();assert.equal(settled,false);assert.deepEqual(selected,['agent-test']);release();assert.equal(await pending,true);
});
test('globe opens the modern panel in the owned focused chat and respects interface lock',async()=>{
 let opened=0,locked=0;const parent={isDestroyed:()=>false,webContents:{getURL:()=> 'https://127.0.0.1',executeJavaScript:async code=>{assert.equal(code,'window.IgorekOpenBrowserPanel?.()');opened++;return true;}}};
 const context={managed:true,runtime:{requestOwner:async()=>({result:{state:'unlocked'}})},workspaceBrowser:{unlock(){}},BrowserWindow:{getFocusedWindow:()=>parent},window:parent,chatWindows:new Set([parent]),config:{origin:'https://127.0.0.1'},sameOrigin:(url,origin)=>new URL(url).origin===origin,openOwnerWindow(){locked++;},dialog:{showErrorBox(){throw Error('unexpected failure');}}};
 vm.createContext(context);const source=fs.readFileSync(require('node:path').join(__dirname, '..','main.cjs'),'utf8');vm.runInContext(source.slice(source.indexOf('async function openDesktopBrowser('),source.indexOf('async function openChatGPT(')),context);
 assert.equal(await context.openDesktopBrowser(),true);assert.equal(opened,1);context.runtime.requestOwner=async()=>({result:{state:'unlocked',interface_locked:true}});assert.equal(await context.openDesktopBrowser(),false);assert.equal(locked,1);assert.equal(opened,1);
});
