const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {BrowserFavorites,favorite,LIMIT}=require('../browser-favorites.cjs');
function directory(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-favorites-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
test('favorites normalize domains, retain anchors, and reject unsupported or private URLs',()=>{
 const row=favorite({url:' example.org/docs#section ',title:''});assert.equal(row.url,'https://example.org/docs#section');assert.equal(row.title,'example.org');assert.match(row.id,/^[a-f0-9]{64}$/);
 for(const url of ['file:///tmp/test','javascript:alert(1)','http://example.org','https://localhost','https://127.0.0.1','https://user:pass@example.org','not a site'])assert.throws(()=>favorite({url,title:'test'}));
 assert.throws(()=>favorite({url:'https://example.org',title:'x',extra:true}));assert.throws(()=>favorite({url:'https://example.org',title:'x'.repeat(65)}));
});
test('favorites persist, update duplicate URLs, and remove without affecting browser data',t=>{
 const root=directory(t),store=new BrowserFavorites(root);fs.writeFileSync(path.join(root,'other-data.json'),'preserved');store.add({url:'example.org',title:'Docs'});const id=store.rows[0].id;
 store.add({url:'https://example.org/',title:'Reference'});assert.equal(store.rows.length,1);assert.equal(store.rows[0].id,id);assert.equal(new BrowserFavorites(root).rows[0].title,'Reference');
 store.remove({id});assert.deepEqual(new BrowserFavorites(root).rows,[]);assert.equal(fs.readFileSync(path.join(root,'other-data.json'),'utf8'),'preserved');assert.equal(fs.statSync(store.file).mode&0o777,0o600);
});
test('unreadable or symlinked favorites are preserved instead of overwritten',t=>{
 const root=directory(t),file=path.join(root,'favorites.json');fs.writeFileSync(file,'broken');const store=new BrowserFavorites(root);assert.equal(store.error,true);assert.throws(()=>store.add({url:'example.org',title:'Docs'}));assert.equal(fs.readFileSync(file,'utf8'),'broken');
 fs.unlinkSync(file);const target=path.join(root,'target');fs.writeFileSync(target,'keep');fs.symlinkSync(target,file);assert.equal(new BrowserFavorites(root).error,true);assert.equal(fs.readFileSync(target,'utf8'),'keep');
});
test('favorites bounds permit updating an existing item but reject growth past the limit',t=>{
 const store=new BrowserFavorites(directory(t));for(let i=0;i<LIMIT;i++)store.add({url:'https://example.org/'+i,title:'Site '+i});assert.throws(()=>store.add({url:'https://example.org/extra',title:'Extra'}));
 store.add({url:'https://example.org/0',title:'Renamed'});assert.equal(store.rows.length,LIMIT);assert.equal(store.rows[0].title,'Renamed');assert.throws(()=>store.remove({id:store.rows[0].id,extra:true}));
});
