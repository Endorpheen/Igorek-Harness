const {test}=require('node:test'),assert=require('node:assert/strict');
const api=require('../../factory/web_ui/static/workspace-tabs.js');
const {Model,recentList}=require('../../factory/web_ui/static/workspace-tabs.js');
test('new tabs remain independent and tools reuse an existing tab',()=>{const m=new Model(),start=m.active,a=m.open('tool','changes'),b=m.open('tool','terminal');assert.equal(m.tabs.length,3);assert.equal(m.open('tool','changes'),a);assert.equal(m.tabs.length,3);m.select(b.id);assert.equal(m.active,b);assert.equal(m.select(start.id),start);});
test('file tabs distinguish sessions and preserve their independent viewer state',()=>{const m=new Model(),a=m.open('file','one:file','a.py');a.fileView={text:'one'};const b=m.open('file','two:file','b.py');b.fileView={text:'two'};assert.equal(m.open('file','one:file'),a);assert.equal(m.active.fileView.text,'one');m.close(a.id);assert.equal(m.active,b);assert.equal(b.fileView.text,'two');});
test('closing inactive tabs preserves selection and closing the last opens a start page',()=>{const m=new Model(),start=m.active,b=m.open('browser');m.close(start.id);assert.equal(m.active,b);m.close(b.id);assert.equal(m.tabs.length,1);assert.equal(m.active.kind,'start');});
test('changing the main chat clears session files while keeping browsers and tools',()=>{const m=new Model();m.open('tool','plan');const browser=m.open('browser');m.open('file','session:file');m.resetSession();assert.equal(m.tabs.some(t=>t.kind==='file'),false);assert.ok(m.tabs.includes(browser));assert.ok(m.tabs.some(t=>t.key==='plan'));});
test('recent file metadata rejects invalid shapes and is capped at twenty entries',()=>{const entry={session:'one',file:{id:'file',name:'x.png'},at:1};assert.equal(recentList([entry,{},null]).length,1);assert.equal(recentList(Array(30).fill(entry)).length,20);assert.deepEqual(recentList({}),[]);});
test('tab limit fails without changing the current selection',()=>{const m=new Model();for(let i=1;i<32;i++)m.open('start');const active=m.active;assert.throws(()=>m.open('browser'));assert.equal(m.active,active);assert.equal(m.tabs.length,32);});
test('browser address parsing uses HTTPS and never treats executable schemes as page URLs',()=>{const {normalizeSite,isChatGPT}=require('../../factory/web_ui/static/workspace-tabs.js');assert.equal(normalizeSite('example.com'),'https://example.com/');assert.match(normalizeSite('как открыть файл'),/^https:\/\/www.google.com\/search\?q=/);for(const url of ['http://example.com','https://user@example.com','file:///etc/passwd'])assert.throws(()=>normalizeSite(url));assert.equal(isChatGPT('https://chatgpt.com/images'),true);assert.equal(isChatGPT('https://chatgpt.com.evil/'),false);});
test('Provider login help comes only from structured failed task metadata',()=>{
 const failed={status:'fail',error_message:'chatgpt_login_required'};
 assert.match(api.taskError(failed,'codexpro'),/codexpro/);
 assert.match(api.taskError(failed,'codexpro'),/пароль менять не нужно/);
 assert.equal(api.taskError({status:'done',error_message:'chatgpt_login_required'},'codexpro'),'');
 assert.equal(api.taskError({status:'fail',error_message:'other_error'},'codexpro'),'');
 assert.equal(api.taskError(null,'codexpro'),'');
});
test('only the exact WebRTC diagnostic address is accepted among internal browser URLs',()=>{
 for(const value of ['chrome://webrtc-internals','chrome://webrtc-internals/'])assert.equal(api.normalizeSite(value),'chrome://webrtc-internals/');
 for(const value of ['chrome://settings','chrome://webrtc-internals/?query=1','chrome://webrtc-internals/#x','chrome://webrtc-internals/other','devtools://devtools','file:///tmp/private'])assert.throws(()=>api.normalizeSite(value));
});
test('reordering tabs preserves active selection, object identity and unsent content',()=>{
 const m=new Model(),start=m.active,chat=m.open('chat'),browser=m.open('browser'),file=m.open('file','session:file');chat.draft='Unsent';file.fileView={text:'kept'};m.select(browser.id);const active=m.active;
 assert.equal(m.move(chat.id,3),true);assert.deepEqual(m.tabs,[start,browser,file,chat]);assert.equal(m.active,active);assert.equal(chat.draft,'Unsent');assert.equal(file.fileView.text,'kept');
 assert.equal(m.move(chat.id,0),true);assert.deepEqual(m.tabs,[chat,start,browser,file]);assert.equal(m.active,active);m.close(browser.id);assert.equal(m.active,file);
});
test('invalid reorder positions and missing tabs leave order and selection unchanged',()=>{
 const m=new Model();m.open('chat');const before=m.tabs.slice(),active=m.active;
 for(const [id,index] of [[active.id,-1],[active.id,2],[active.id,0.5],['missing',0],[active.id,NaN],[active.id,Infinity],[active.id,1]]){assert.equal(m.move(id,index),false);assert.deepEqual(m.tabs,before);assert.equal(m.active,active);}
});
