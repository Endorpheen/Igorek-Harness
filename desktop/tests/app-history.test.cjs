const {test}=require('node:test'),assert=require('node:assert/strict');
const {History}=require('../../factory/web_ui/static/app-history.js');
const route=(view,session='chat-one',extra={})=>({view,session,...extra});
test('application history restores sections, chats and panels without using browser URLs',async()=>{
 const restored=[],history=new History({restore:async item=>{restored.push(item);}});
 history.record(route('main'));history.record(route('plugins'));history.record(route('scheduled'));history.record(route('main','chat-two',{panel:true,tab:'file-tab'}));
 assert.equal(history.state().canBack,true);assert.equal(history.state().canForward,false);
 await history.back();assert.equal(restored.at(-1).view,'scheduled');await history.back();assert.equal(restored.at(-1).view,'plugins');await history.back();assert.equal(restored.at(-1).session,'chat-one');
 assert.equal(history.state().canBack,false);await history.forward();await history.forward();await history.forward();assert.equal(restored.at(-1).tab,'file-tab');
});
test('new navigation after going back truncates the forward branch and repeated renders do not add entries',async()=>{
 const history=new History({restore:async()=>{}});history.record(route('main'));history.record(route('scheduled'));
 assert.equal(history.record(route('scheduled')),false);await history.back();history.record(route('reviews'));
 assert.equal(history.state().canForward,false);await history.back();assert.equal(history.entries.length,2);
});
test('history rejects unsafe routes, remains bounded, and clones mutable route inputs',()=>{
 const history=new History({restore:async()=>{},limit:3});assert.equal(history.record({view:'javascript:bad'}),false);
 assert.equal(history.record(route('main','../secret')),false);const first=route('main');history.record(first);first.session='changed';
 assert.equal(history.entries[0].session,'chat-one');for(let i=0;i<8;i++)history.record(route('main','chat-'+i));assert.equal(history.entries.length,3);
});
test('failed restores do not move the cursor; a logout reset cancels a pending restore',async()=>{
 let release;const history=new History({restore:async()=>{throw Error('missing chat');}});history.record(route('main'));history.record(route('scheduled'));
 await assert.rejects(history.back(),/missing chat/);assert.equal(history.index,1);assert.equal(history.busy,false);
 history.restore=()=>new Promise(resolve=>release=resolve);const pending=history.back();assert.equal(history.state().canBack,false);
 assert.equal(await history.forward(),false);history.reset();release();assert.equal(await pending,false);assert.equal(history.entries.length,0);
});
