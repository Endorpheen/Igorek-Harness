const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {sources}=require('../../factory/web_ui/static/session-summary.js');
test('source refresh discards a late response after switching sessions',async()=>{
 class Node{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.classList={toggle(){}};}
  append(...items){this.children.push(...items);}replaceChildren(){this.children=[];}setAttribute(){}addEventListener(){}
 }
 const globals={document:global.document,window:global.window,setInterval:global.setInterval,clearInterval:global.clearInterval};
 global.document={hidden:false,createElement:tag=>new Node(tag),addEventListener(){}};global.window={addEventListener(){}};global.setInterval=()=>1;global.clearInterval=()=>{};
 try{
  const host=new Node('host'),state={paired:true,selected:'first',session:{session_id:'first',meta:{}},items:[],panel:{open:false,review:{mode:'session'}}};
  let release,language='ru';
  const controller=require('../../factory/web_ui/static/session-summary.js').mount({host,getState:()=>state,getLanguage:()=>language,api:async path=>{
   if(path.includes('/first/sources'))return new Promise(resolve=>release=resolve);
   if(path.endsWith('/sources'))return {sources:[{kind:'web',id:'b'.repeat(64),name:'New session reference',url:'https://example.com/new'}]};
   return {processes:[],totals:{additions:0,deletions:0}};
  },openChanges(){},openSource(){},attach(){}});
  controller.sync();await new Promise(resolve=>setImmediate(resolve));
  state.selected='second';state.session={session_id:'second',meta:{}};controller.sync();await controller.refresh(true);
  release({sources:[{kind:'web',id:'a'.repeat(64),name:'Old session private reference',url:'https://example.com/old'}]});await new Promise(resolve=>setImmediate(resolve));
  const texts=node=>[node.textContent||'',...node.children.flatMap(texts)];
  const visible=texts(host).join(' ');assert.match(visible,/New session reference/);assert.doesNotMatch(visible,/Old session private reference/);
  language='en';controller.sync();assert.match(texts(host).join(' '),/Web sources/);
 }finally{Object.assign(global,globals);}
});
test('source groups separate uploads, generated files, web results and pull requests',()=>{
 const {groups}=require('../../factory/web_ui/static/session-summary.js'),id='a'.repeat(64);
 const result=groups([{role:'user',attachments:[{upload_id:'b'.repeat(32),name:'image.png'}]}],[
  {kind:'file',id,name:'journal.md'}, {kind:'web',id:'c'.repeat(64),url:'https://example.com',name:'Reference'},
  {kind:'pull_request',id:'d'.repeat(64),url:'https://github.com/example/project/pull/1',name:'PR'},
  {kind:'web',id:'e'.repeat(64),url:'javascript:alert(1)'},{kind:'file',id:'../../secret'}, {kind:'file',id,name:'duplicate'},
 ]);
 assert.deepEqual(Object.values(result).map(items=>items.length),[1,1,1,1]);
 assert.equal(result.file[0].name,'journal.md');
});
test('changed-file navigation uses an exact relative path and refuses ambiguous names',async()=>{
 const source=fs.readFileSync(require('node:path').join(__dirname,'../../factory/web_ui/static/app.js'),'utf8'),opened=[],notices=[];
 const context={state:{selected:'session'},ensureSessionFiles:async()=>[
  {id:'wrong',name:'log.md',path:'second/log.md'},{id:'right',name:'log.md',path:'first/log.md'},
 ],openFileViewer:async file=>opened.push(file.id),toast:message=>notices.push(message),t:value=>value};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('async function viewChangedFile('),source.indexOf('// «Open in folder»')),context);
 await context.viewChangedFile('first/log.md');assert.deepEqual(opened,['right']);
 context.ensureSessionFiles=async()=>[{id:'one',name:'log.md'},{id:'two',name:'log.md'}];
 await context.viewChangedFile('third/log.md');assert.equal(opened.length,1);assert.equal(notices.length,1);
});
test('opening an indexed source refreshes a changed download identity and rejects session switches',async()=>{
 const source=fs.readFileSync(require('node:path').join(__dirname,'../../factory/web_ui/static/app.js'),'utf8'),opened=[];
 const context={state:{selected:'session',paired:true},encode:encodeURIComponent,api:async()=>({files:[{id:'current',name:'note.md',path:'folder/note.md'}]}),openFileViewer:async file=>opened.push(file.id),openPanel(){},toast(){throw Error('unexpected missing file');},t:value=>value};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('async function openSummarySource('),source.indexOf('window.IgorekSummary =')),context);
 const file={kind:'file',id:'stale',name:'note.md',path:'folder/note.md'};
 await context.openSummarySource(file,'session');assert.deepEqual(opened,['current']);
 context.api=async()=>{context.state.selected='other';return {files:[{id:'private',name:'note.md',path:'folder/note.md'}]};};
 await context.openSummarySource(file,'session');assert.deepEqual(opened,['current']);
});
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
