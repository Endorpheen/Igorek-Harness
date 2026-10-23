const {test}=require('node:test'),assert=require('node:assert/strict');
const {BrowserReading,validReading,STATE}=require('../browser-reading.cjs');

test('Wait login checks apply to X while optional authentication on public sites is allowed',()=>{
 const vm=require('node:vm'),password={getBoundingClientRect:()=>({width:100,height:30})};
 const document={readyState:'complete',querySelectorAll:selector=>selector.includes('input[type="password"]')?[password]:[]};
 const state=hostname=>vm.runInNewContext(STATE,{document,location:{hostname},getComputedStyle:()=>({visibility:'visible'})});
 assert.equal(state('www.coingecko.com').login,false);
 assert.equal(state('x.com').login,true);
});
test('Extended reading schema is bounded and cannot execute scripts or choose screenshot paths',()=>{
 for(const fields of [{action:'wait',condition:'menu',timeout_ms:1000},{action:'read_post',url:'https://x.com/a/status/1',offset:6000},{action:'comments',url:'https://x.com/a/status/1',limit:5},{action:'screenshot'},{action:'close_menu'}])assert.ok(validReading(fields));
 for(const fields of [{action:'wait',timeout_ms:1e9},{action:'wait',condition:{}},{action:'screenshot',path:'/etc/passwd'},{action:'read_post',url:'x',offset:-1},{action:'comments',url:'x',cursor:'forged'}])assert.equal(validReading(fields),false);
});
test('Wait observes a delayed UI state and timeout never claims readiness',async()=>{
 let attempts=0;const b={evaluate:async()=>({ready:true,loading:++attempts<3,menus:attempts<3?0:1,posts:0}),sleep:async()=>{},view:{webContents:{isLoading:()=>false}}};
 assert.equal((await new BrowserReading(b).wait({condition:'menu',timeout_ms:1000})).ready,true);assert.equal(attempts,3);
 b.evaluate=async()=>({ready:true,loading:true,menus:0});b.sleep=async()=>new Promise(r=>setTimeout(r,10));
 assert.equal((await new BrowserReading(b).wait({condition:'idle',timeout_ms:100})).reason,'wait_timeout');
});
test('Full post text is returned in continuous chunks and comment cursors are page-bound',async()=>{
 const url='https://x.com/a/status/1',text='Long text '.repeat(2000),p={id:'1',url,text,images:[],links:[]},wc={getURL:()=>url};
 const b={auth:'ready',pagePosts:[p],read:async()=>({}),evaluate:async()=>null,view:{webContents:wc},interaction:{snapshot:async()=>[]}};const reader=new BrowserReading(b);
 const a=await reader.post({url,limit:6000}),c=await reader.post({url,limit:6000,offset:a.next_offset});
 assert.equal(a.post.text+c.post.text,text.slice(0,12000));assert.equal(a.next_offset,6000);assert.equal(a.total_characters,20000);
 b.pagePosts=[p,{id:'2',url:'https://x.com/b/status/2',text:'Reply',links:[],images:[],relation:'reply',reply_to:'1'},{id:'3',url:'https://x.com/c/status/3',text:'Unknown',links:[],images:[]}];
 const result=await reader.comments({url,limit:1});assert.deepEqual(result.replies,['2']);assert.equal(result.items.length,1);
 const next=await reader.comments({url,limit:1,cursor:result.cursor});assert.deepEqual(next.unclassified,['3']);
 wc.getURL=()=>url+'?changed=1';assert.equal((await reader.comments({url,cursor:next.cursor})).error,'stale_cursor');
});
test('A failed page read is preserved instead of being relabelled as a missing post',async()=>{
 const error={error:'browser_closed'};
 const reader=new BrowserReading({read:async()=>error,evaluate:async()=>assert.fail('a closed page was inspected')});
 assert.deepEqual(await reader.post({url:'https://x.com/fixture/status/123'}),error);
});
test('A matching rendered post remains readable when the bounded feed index omitted it',async()=>{
 const url='https://x.com/fixture/status/123',post={id:'123',url,text:'Rendered body',links:[],images:[]};
 let navigations=0;
 const browser={auth:'ready',pagePosts:[],read:async()=>({auth:'ready'}),evaluate:async()=>[post],
  navigate:async()=>{navigations++;assert.fail('an identified post was unnecessarily reloaded');},interaction:{snapshot:async()=>[]}};
 const result=await new BrowserReading(browser).post({url});
 assert.equal(result.post.text,'Rendered body');assert.equal(result.post.id,'123');assert.equal(navigations,0);
 assert.equal(result.coverage,'visible_dom_only');
});
