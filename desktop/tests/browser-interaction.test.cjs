const {test}=require('node:test'),assert=require('node:assert/strict');
const {BrowserInteraction,validInteraction,WORLD}=require('../browser-interaction.cjs');
const {SNAPSHOT,TARGET,compactPage}=require('../browser-interaction.cjs'),vm=require('node:vm');
const target='r-'+'a'.repeat(32)+'-0';
test('Interaction protocol accepts only bounded browser actions and opaque current references',()=>{
  for(const fields of [{action:'click',target},{action:'fill',target,text:'Python official website'},{action:'press',target,key:'Enter'}])assert.ok(validInteraction(fields));
  for(const fields of [{action:'click',target:'#publish'},{action:'fill',target,text:'a'.repeat(16001)},{action:'press',target,key:'Control+L'},{action:'click',target,eval:'document.cookie'},{action:'fill',target,text:3}])assert.ok(!validInteraction(fields));
});
function fixture(inspected){
  const events=[],scripts=[];const wc={executeJavaScriptInIsolatedWorld:async(world,values)=>{assert.equal(world,WORLD);scripts.push(values[0].code);return inspected;},focus:()=>{},insertText:async text=>events.push({text}),sendInputEvent:event=>events.push(event),isLoading:()=>false};
  const interaction=new BrowserInteraction(()=>wc,url=>url.startsWith('https://www.google.com/'),async()=>{});interaction.targets.set(target,{frame:null,wc});return {interaction,events,scripts};
}
test('Click dispatches actual mouse down/up and fill uses native Chromium input',async()=>{
  const f=fixture({url:'https://www.google.com/search',x:20,y:30});
  assert.equal((await f.interaction.run({action:'click',target})).performed,true);
  assert.deepEqual(f.events.map(e=>e.type),['mouseMove','mouseDown','mouseUp']);
  assert.equal(f.events[1].x,20);assert.equal(f.events[2].y,30);
  await f.interaction.run({action:'fill',target,text:'Python'});assert.equal(f.events.at(-1).text,'Python');
  await f.interaction.run({action:'press',target,key:'Enter'});assert.equal(f.events.at(-2).keyCode,'Enter');
});
test('Stale, obscured, protected controls and private URLs never receive input',async()=>{
  for(const error of ['stale_target','protected_input','target_obscured']){
    const f=fixture({error});assert.equal((await f.interaction.run({action:'click',target})).error,error);assert.equal(f.events.length,0);
  }
  const f=fixture({url:'https://127.0.0.1/'});assert.equal((await f.interaction.run({action:'click',target})).error,'invalid_url');assert.equal(f.events.length,0);
});
test('History updates preserve a connected target, while destination/document changes reject it and ordinary POST forms are editable',()=>{
  const form={method:'get',action:'https://example.com/search'};
  const node={tagName:'INPUT',type:'search',form,autocomplete:'off',isConnected:true,disabled:false,readOnly:false,name:'q',
    getBoundingClientRect:()=>({x:0,y:0,left:0,top:0,right:100,bottom:20,width:100,height:20}),closest:()=>null,getAttribute:()=>null,focus(){},select(){},scrollIntoView(){},contains:other=>other===node};
  const document={querySelectorAll:()=>[node],getElementById:()=>null,elementFromPoint:()=>node};node.ownerDocument=document;
  const location={href:'https://example.com/'};
  const context=vm.createContext({document,location,URL,innerHeight:900,innerWidth:1200,getComputedStyle:()=>({visibility:'visible',display:'block'})});
  const items=vm.runInContext(SNAPSHOT('a'.repeat(32)),context),ref=items[0].ref;
  location.href='https://example.com/?history-update=1';
  assert.equal(vm.runInContext(TARGET(ref,'fill'),context).url,'https://example.com/search');
  form.action='https://example.com/changed';assert.equal(vm.runInContext(TARGET(ref,'fill'),context).error,'stale_target');
  form.action='https://example.com/search';form.method='post';assert.equal(vm.runInContext(TARGET(ref,'fill'),context).url,'https://example.com/search');
  form.method='get';node.ownerDocument={};assert.equal(vm.runInContext(TARGET(ref,'fill'),context).error,'stale_target');
});
test('Large pages retain actionable references below the existing 12000-character model-result threshold',()=>{
  const elements=Array.from({length:200},(_,i)=>({ref:'r-'+'a'.repeat(32)+'-'+i,kind:i?'link':'search_input',label:i?'Official Python website '.repeat(8):'Search',url:i?'https://www.google.com/goto?url='+('x'.repeat(1500)):undefined}));
  const value=compactPage({auth:'not_checked',url:'https://www.google.com/search?q=python.org',title:'Google Search',text:'Search results '.repeat(4000),links:elements.slice(1).map(e=>({text:e.label,url:e.url})),elements});
  assert.ok(JSON.stringify(value).length<11000);assert.equal(value.elements[0].ref,elements[0].ref);assert.ok(value.elements.length>10);
  assert.ok(value.elements_omitted>0&&value.text_truncated);assert.equal(value.elements[1].host,'www.google.com');
  const short=compactPage({auth:'not_checked',url:'https://python.org',title:'Python',text:'Hello',links:[{text:'Docs',url:'https://docs.python.org/'}],elements:[]});
  assert.equal(short.text,'Hello');assert.equal(short.links[0].url,'https://docs.python.org/');assert.equal(short.elements_omitted,0);
});

test('General UI protocol supports menus, editing, state setting and bounded element offsets',()=>{
 const examples=[{action:'hover',target},{action:'select',target,value:'ru'},{action:'check',target,checked:true},{action:'press',target,key:'Escape'},{action:'press',target,key:'ArrowDown'},{action:'scroll_element',target,direction:'down'},{action:'click',target,button:'right',offset_x:.8,offset_y:.2}];
 for(const fields of examples)assert.ok(validInteraction(fields),JSON.stringify(fields));
 for(const fields of [{action:'click',target,offset_x:2},{action:'click',target,offset_y:NaN},{action:'check',target,checked:'true'},{action:'select',target,value:3},{action:'hover',target,eval:'document.cookie'}])assert.equal(validInteraction(fields),false);
});

test('Snapshot exposes ordinary buttons, POST controls and toggle state without protected values',()=>{
 const make=(tag,attrs={})=>({tagName:tag,type:'button',isConnected:true,disabled:false,readOnly:false,autocomplete:'',innerText:'',value:'',name:'',...attrs,
  getAttribute(k){return this.attributes?.[k]??null;},closest(){return null;},getBoundingClientRect(){return {x:0,y:0,left:0,top:0,right:100,bottom:30,width:100,height:30};},focus(){},select(){},scrollIntoView(){},contains(n){return n===this;}});
 const like=make('BUTTON',{attributes:{'aria-label':'Like','aria-pressed':'false','data-testid':'like'}}),postInput=make('INPUT',{type:'text',form:{method:'post',action:'https://example.com/save'},attributes:{'aria-label':'POST field'}}),secret=make('INPUT',{type:'password',value:'fixture-secret'});
 const nodes=[like,postInput,secret],document={querySelectorAll:()=>nodes,getElementById:()=>null,elementFromPoint:()=>like};for(const n of nodes)n.ownerDocument=document;
 const ctx=vm.createContext({document,location:{href:'https://example.com/'},URL,innerHeight:900,innerWidth:1200,getComputedStyle:()=>({visibility:'visible',display:'block'})});
 const rows=vm.runInContext(SNAPSHOT('a'.repeat(32)),ctx);assert.equal(rows.length,2);assert.equal(rows[0].pressed,'false');assert.ok(!JSON.stringify(rows).includes('fixture-secret'));
 const ref=rows.find(r=>r.test_id==='like').ref;assert.equal(vm.runInContext(TARGET(ref,'check',{checked:true}),ctx).already,false);
 like.attributes['aria-pressed']='true';assert.equal(vm.runInContext(TARGET(ref,'check',{checked:true}),ctx).already,true);
 postInput.type='password';const inputRef=rows.find(r=>r.label==='POST field').ref;assert.equal(vm.runInContext(TARGET(inputRef,'fill'),ctx).error,'protected_input');
});

test('Feed metadata cannot crowd actionable controls out of the model result',()=>{
 const posts=Array.from({length:1000},(_,i)=>({id:String(i),url:'https://x.com/a/status/'+i,text:'text '.repeat(4000),links:['https://example.com/'+ 'x'.repeat(5000)],images:[]}));
 const controls=[{ref:target,kind:'button',label:'Like',test_id:'like',pressed:'false'}];
 const result=compactPage({auth:'ready',url:'https://x.com/home',title:'X',text:'',links:[],posts,elements:controls});
 assert.ok(JSON.stringify(result).length<11000);assert.equal(result.elements[0].ref,target);assert.ok(result.posts_omitted>0);
});

test('Element references survive reads and only a unique replacement in the same post can rebind',()=>{
 const make=(post,id='like')=>{const article={querySelectorAll:()=>[{href:post,querySelector:()=>({})}]};return {tagName:'BUTTON',type:'button',id:'',isConnected:true,disabled:false,autocomplete:'',innerText:'Like',getAttribute:k=>k==='data-testid'?id:null,closest:s=>s==='article'?article:null,getBoundingClientRect:()=>({x:0,y:0,left:0,top:0,width:80,height:30}),focus(){},scrollIntoView(){},contains(n){return n===this;}};};
 let nodes=[make('https://x.com/a/status/1')];const document={querySelectorAll:()=>nodes,getElementById:()=>null,elementFromPoint:()=>nodes[0]};nodes[0].ownerDocument=document;
 const ctx=vm.createContext({document,location:{href:'https://x.com/home'},URL,innerHeight:900,innerWidth:1200,getComputedStyle:()=>({visibility:'visible',display:'block'})});
 const first=vm.runInContext(SNAPSHOT('a'.repeat(32)),ctx)[0];
 assert.equal(vm.runInContext(SNAPSHOT('b'.repeat(32)),ctx)[0].ref,first.ref);
 nodes[0].isConnected=false;nodes=[make('https://x.com/a/status/1')];nodes[0].ownerDocument=document;
 assert.equal(vm.runInContext(TARGET(first.ref,'click'),ctx).rebound,true);
 nodes[0].isConnected=false;nodes=[make('https://x.com/a/status/2')];nodes[0].ownerDocument=document;
 assert.equal(vm.runInContext(TARGET(first.ref,'click'),ctx).error,'stale_target');
 nodes=[make('https://x.com/a/status/1'),make('https://x.com/a/status/1')];for(const n of nodes)n.ownerDocument=document;
 assert.equal(vm.runInContext(TARGET(first.ref,'click'),ctx).error,'stale_target');
});

test('Global Escape and Tab work without clicking an obscured or missing target',async()=>{
 const f=fixture({error:'target_obscured'});
 assert.equal((await f.interaction.run({action:'press',key:'Escape'})).performed,true);
 assert.deepEqual(f.events.map(e=>e.type),['keyDown','keyUp']);
 assert.equal(f.events[0].keyCode,'Escape');
 assert.equal(validInteraction({action:'press',key:'Enter'}),false);
 assert.equal(validInteraction({action:'press',key:'Escape',target:'forged'}),false);
});
