const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path');
const emoji=require('../../factory/web_ui/static/emoji.js');
const catalog=JSON.parse(fs.readFileSync(path.join(__dirname,'../../factory/web_ui/static/emoji/catalog.json'),'utf8'));
const tool=result=>({name:'mcp__igorek__render_emoji',result:JSON.stringify(result)});
test('all sixteen reactions use stable local paths and round-trip tokens',()=>{
 emoji.install(catalog);assert.equal(emoji.list().length,32);
 for(const row of catalog.emojis){assert.deepEqual(emoji.match(':'+row.id+':'),{id:row.id,token:':'+row.id+':'});assert.equal(emoji.match('hello :'+row.id+':',6).id,row.id);}
 assert.equal(emoji.match(':pepe_unknown:'),null);assert.equal(emoji.match(':pepe_think'),null);
});
test('receipt requires confirmed successful versioned emoji output',()=>{
 emoji.install(catalog);
 const valid={ok:true,kind:'igorek.emoji',schema_version:1,emoji_id:'pepe_think'};
 assert.equal(emoji.decode(tool(valid)),'pepe_think');
 for(const result of [{...valid,ok:false},{...valid,kind:'other'},{...valid,schema_version:2},{...valid,emoji_id:'../private'},{...valid,emoji_id:'pepe_missing'}])assert.equal(emoji.decode(tool(result)),null);
 assert.equal(emoji.decode({name:'Read',result:JSON.stringify(valid)}),null);
 assert.equal(emoji.decode({name:'render_emoji',result:'broken-json'}),null);
});
test('catalog installation is atomic and cannot supply remote asset URLs',()=>{
 emoji.install(catalog);
 for(const row of [{...catalog.emojis[0],file:'https://example.invalid/track.gif'},{...catalog.emojis[0],id:'../private'},{...catalog.emojis[0],label:null},{...catalog.emojis[0],id:['pepe_think']}]){
  assert.throws(()=>emoji.install({...catalog,emojis:[row]}));assert.equal(emoji.list().length,32);
 }
 assert.throws(()=>emoji.install({...catalog,emojis:[catalog.emojis[0],catalog.emojis[0]]}));
 const copy=emoji.list();copy[0].id='changed';assert.equal(emoji.match(':'+catalog.emojis[0].id+':').id,catalog.emojis[0].id);
});
test('reactions remain outside folded technical tool groups',()=>{
 const groups=require('../../factory/web_ui/static/tool-groups.js');
 const row={id:'emoji',type:'tool',name:'render_emoji'};
 assert.equal(groups.project([row,{id:'read',type:'tool',name:'Read'}])[0],row);
});
