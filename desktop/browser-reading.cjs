const {postsScript}=require('./browser-posts.cjs');
const {randomUUID}=require('node:crypto');
const {join}=require('node:path');
const {mkdirSync,writeFileSync}=require('node:fs');
const SHAPES={wait:['action'],read_post:['action','url'],comments:['action','url'],close_menu:['action'],screenshot:['action']};
function validReading(f){
 if(!f||!Object.hasOwn(SHAPES,f.action))return false;
 const optional={wait:['condition','query','url','timeout_ms'],read_post:['offset','limit'],comments:['cursor','limit'],close_menu:[],screenshot:[]}[f.action];
 if(SHAPES[f.action].some(k=>!Object.hasOwn(f,k))||Object.keys(f).some(k=>!SHAPES[f.action].includes(k)&&!optional.includes(k)))return false;
 if('url'in f&&(typeof f.url!=='string'||f.url.length>2048))return false;
 if('query'in f&&(typeof f.query!=='string'||f.query.length>200))return false;
 if('condition'in f&&!['ready','post','menu','element','idle'].includes(f.condition))return false;
 for(const [key,min,max]of[['timeout_ms',100,15000],['offset',0,1000000],['limit',1,f.action==='comments'?20:6000]])if(key in f&&(!Number.isInteger(f[key])||f[key]<min||f[key]>max))return false;
 return !('cursor'in f)||(typeof f.cursor==='string'&&/^[a-f0-9-]{36}$/.test(f.cursor));
}
const STATE=`(()=>{const visible=n=>{const r=n.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(n).visibility!=='hidden';};return {ready:document.readyState!=='loading',loading:[...document.querySelectorAll('[role="progressbar"],[aria-busy="true"]')].some(visible),menus:[...document.querySelectorAll('[role="menu"],[role="listbox"],[role="dialog"]')].filter(visible).filter(n=>n.getAttribute('role')!=='listbox'||[...document.querySelectorAll('[aria-expanded="true"]')].some(control=>(control.getAttribute('aria-controls')||'').split(/\\s+/).includes(n.id))).length,login:/^(www\\.)?(x\\.com|twitter\\.com)$/i.test(location.hostname)&&[...document.querySelectorAll('input[type="password"],[data-testid="loginButton"],[data-testid="ocfEnterTextTextInput"]')].some(visible),posts:document.querySelectorAll('article[data-testid="tweet"]').length};})()`;
class BrowserReading{
 constructor(browser){this.browser=browser;this.cursors=new Map();}
 async wait(f={}){
  const b=this.browser,end=Date.now()+(f.timeout_ms||8000),condition=f.condition||'ready';let state;
  do{
   state=await b.evaluate(STATE);
   if(state?.login)return {ready:false,reason:'login_required'};
   if(!state||typeof state.ready!=='boolean')return {ready:false,reason:'page_state_unavailable'};
   if(condition==='menu'&&state.menus>0||condition==='idle'&&!b.view.webContents.isLoading()&&!state.loading||condition==='ready'&&state.ready&&!state.loading||condition==='post'&&state.posts>0&&!state.loading)return {ready:true,condition,...state};
   if(condition==='element'&&(await b.interaction.snapshot(f.query||'',f.url||'')).length)return {ready:true,condition,...state};
   await b.sleep(150);
  }while(Date.now()<end&&!b.blocked&&!b.manual);
  return {ready:false,condition,reason:'wait_timeout',...state};
 }
 async post(f){
  const b=this.browser,id=new URL(f.url).pathname.split('/').pop();
  let page=await b.read();if(page.paused||page.error)return page;
  let full=(await b.evaluate(postsScript(id)))?.find?.(post=>post.id===id);
  if(!full&&!(b.pagePosts||[]).some(p=>p.id===id)){
   page=await b.navigate(f.url);if(page.paused||page.error)return page;await this.wait({condition:'post'});
  }
  let expanded=false;
  const controls=await b.interaction.snapshot('',f.url);
  const more=controls.find(e=>e.test_id==='tweet-text-show-more-link');
  if(more){const r=await b.interaction.run({action:'click',target:more.ref});expanded=!r.error;await this.wait({condition:'idle',timeout_ms:3000});}
  page=await b.read();if(page.paused||page.error)return page;
  full=(await b.evaluate(postsScript(id)))?.find?.(post=>post.id===id);
  const p=full||(b.pagePosts||[]).find(p=>p.id===id);
  if(!p)return {error:'post_not_found',hint:'The requested post could not be identified in the rendered DOM. Inspect read/elements or screenshot; this does not prove the post is deleted.',coverage:'visible_dom_only'};
  const offset=f.offset||0,limit=f.limit||6000,body=String(p.text||''),truncated=!!p.truncated;
  const result={auth:b.auth,post:{...p,text:body.slice(offset,offset+limit),images:(p.images||[]).slice(0,4),links:(p.links||[]).slice(0,8)},offset,next_offset:offset+limit<body.length?offset+limit:null,total_characters:body.length,character_limit:200000,text_truncated:offset+limit<body.length||truncated,expanded,elements:(await b.interaction.snapshot('',f.url)).filter(e=>e.test_id||['button','input'].includes(e.kind)).slice(0,12),coverage:'visible_dom_only'};
  while(result.elements.length&&JSON.stringify(result).length>11000)result.elements.pop();return result;
 }
 async comments(f){
  const b=this.browser,id=new URL(f.url).pathname.split('/').pop(),wc=b.view.webContents;
  let cursor=f.cursor?this.cursors.get(f.cursor):null;
  if(f.cursor&&(!cursor||cursor.wc!==wc||cursor.url!==f.url||cursor.page!==wc.getURL()))return {error:'stale_cursor'};
  if(!cursor){const result=await this.post({action:'read_post',url:f.url,limit:1});if(result.error||result.paused)return result;cursor={wc,url:f.url,page:wc.getURL(),seen:new Set([id]),backlog:[]};}
  else if(!cursor.backlog.length){await b.scroll();await this.wait({condition:'idle',timeout_ms:3000});}
  await b.read();
  const limit=f.limit||5,items=[];
  for(const p of b.pagePosts){if(cursor.seen.has(p.id))continue;cursor.seen.add(p.id);cursor.backlog.push(p);}
  for(const p of cursor.backlog.splice(0,Math.min(limit,5))){const relation=p.relation==='reply'&&p.reply_to===id?'reply':p.relation==='recommendation'?'recommendation':'unknown';items.push({...p,text:p.text.slice(0,1000),text_truncated:p.text.length>1000||!!p.truncated,links:p.links.slice(0,2),images:[],relation});}
  const token=randomUUID();this.cursors.set(token,cursor);while(this.cursors.size>30)this.cursors.delete(this.cursors.keys().next().value);
  return {url:f.url,items,replies:items.filter(p=>p.relation==='reply').map(p=>p.id),recommendations:items.filter(p=>p.relation==='recommendation').map(p=>p.id),unclassified:items.filter(p=>p.relation==='unknown').map(p=>p.id),cursor:token,has_more:cursor.backlog.length?true:'unknown',requested_limit:limit,returned:items.length,coverage:'visible_dom_only',note:'relation=unknown не подтверждает, что публикация является ответом. Раскрывай ветку её актуальной кнопкой через elements.'};
 }
 async close(){
  const b=this.browser,before=await b.evaluate(STATE);
  if(before.menus)await b.evaluate(`(()=>{for(const menu of document.querySelectorAll('[role="menu"],[role="listbox"],[role="dialog"]')){const r=menu.getBoundingClientRect();if(r.width&&r.height){const control=menu.querySelector('button,[role="menuitem"],[tabindex]');control?.focus();break;}}return true;})()`);
  await b.interaction.run({action:'press',key:'Escape'});
  let after=await b.evaluate(STATE);
  if(after.menus>=before.menus){
   const controls=await b.interaction.snapshot();const close=controls.filter(e=>['app-bar-close','mask'].includes(e.test_id));
   if(close.length===1){await b.interaction.run({action:'click',target:close[0].ref});after=await b.evaluate(STATE);}
  }
  return {performed:true,closed:after.menus===0,remaining:after.menus,...(after.menus?{reason:'menu_still_open'}:{})};
 }
 async screenshot(){
  const b=this.browser,image=await b.view.webContents.capturePage();if(image.isEmpty())return {error:'screenshot_unavailable'};
  const size=image.getSize(),scaled=size.width>1600?image.resize({width:1600}):image,buffer=scaled.toJPEG(80);if(buffer.length>1300000)return {error:'screenshot_too_large'};
  const id=randomUUID(),directory=join(b.root,'screenshots');mkdirSync(directory,{recursive:true,mode:0o700});const path=join(directory,id+'.jpg');writeFileSync(path,buffer,{mode:0o600});
  return {screenshot:{id,path,mime_type:'image/jpeg',...scaled.getSize()},image_base64:buffer.toString('base64'),coverage:'current_viewport',url:b.view.webContents.getURL()};
 }
}
module.exports={BrowserReading,validReading,STATE};
