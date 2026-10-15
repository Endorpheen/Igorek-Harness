const { randomUUID } = require('node:crypto');
const WORLD=999;
const KEYS=new Set(['Enter','Space','Tab','Shift+Tab','Escape','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End','PageUp','PageDown','Backspace','Delete']);
const SHAPES={click:['action','target'],hover:['action','target'],fill:['action','target','text'],press:['action','key'],select:['action','target','value'],check:['action','target','checked'],scroll_element:['action','target','direction']};
function validInteraction(fields){
 if(!fields||!Object.hasOwn(SHAPES,fields.action))return false;
 const allowed=[...SHAPES[fields.action],...(fields.action==='click'?['button','offset_x','offset_y']:fields.action==='press'?['target']:[])];
 if(Object.keys(fields).some(k=>!allowed.includes(k))||SHAPES[fields.action].some(k=>!Object.hasOwn(fields,k)))return false;
 if(fields.action!=='press'||'target'in fields){if(typeof fields.target!=='string'||!/^r-[a-f0-9]{32}-\d{1,4}$/.test(fields.target))return false;}else if(!['Escape','Tab','Shift+Tab'].includes(fields.key))return false;
 if(fields.action==='fill'&&(typeof fields.text!=='string'||fields.text.length>16000))return false;
 if(fields.action==='select'&&(typeof fields.value!=='string'||fields.value.length>1024))return false;
 if(fields.action==='check'&&typeof fields.checked!=='boolean')return false;
 if(fields.action==='press'&&!KEYS.has(fields.key))return false;
 if(fields.action==='scroll_element'&&!['up','down','left','right'].includes(fields.direction))return false;
 if('button'in fields&&!['left','right','double'].includes(fields.button))return false;
 return ['offset_x','offset_y'].every(k=>!(k in fields)||(typeof fields[k]==='number'&&Number.isFinite(fields[k])&&fields[k]>=0&&fields[k]<=1));
}
const HELPERS=`
const roots=()=>{const result=[document];for(let i=0;i<result.length&&i<100;i++)for(const node of result[i].querySelectorAll('*'))if(node.shadowRoot)result.push(node.shadowRoot);return result;};
const visible=node=>{const r=node.getBoundingClientRect(),s=getComputedStyle(node);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none';};
const disabled=node=>!!node.disabled||node.getAttribute('aria-disabled')==='true';
const protectedInput=node=>node.type==='password'||node.type==='hidden'||['username','one-time-code','current-password','new-password','cc-number','cc-csc'].some(v=>(node.autocomplete||'').split(/\\s+/).includes(v));
const role=node=>node.getAttribute('role')||'';
const editable=node=>!protectedInput(node)&&!node.readOnly&&(node.isContentEditable||node.tagName==='TEXTAREA'||node.tagName==='INPUT'&&['text','search','email','url','tel','number','date','datetime-local','time'].includes(node.type));
const label=node=>(node.getAttribute('aria-label')||(node.getAttribute('aria-labelledby')||'').split(/\\s+/).map(id=>(node.getRootNode?.()?.getElementById?.(id)||document.getElementById(id))?.textContent||'').join(' ').trim()||Array.from(node.labels||[]).map(l=>l.innerText).join(' ')||node.getAttribute('placeholder')||node.getAttribute('alt')||node.innerText||node.getAttribute('title')||node.name||node.getAttribute('data-testid')||'').trim().slice(0,180);
const context=node=>{const article=node.closest('article');if(!article)return '';const anchor=Array.from(article.querySelectorAll('a[href]')).find(a=>a.querySelector('time'));if(anchor){const u=new URL(anchor.href);u.search='';u.hash='';return u.href;}return article.getAttribute('aria-label')||'';};
const destination=node=>node.tagName==='A'?node.href:node.form?.action||location.href;
const kind=node=>editable(node)?'input':node.tagName==='SELECT'?'select':node.type==='checkbox'||role(node)==='checkbox'||role(node)==='switch'?'checkbox':node.type==='radio'||role(node)==='radio'?'radio':node.tagName==='A'?'link':node.tagName==='IFRAME'||node.tagName==='FRAME'?'frame':role(node)||node.tagName.toLowerCase();
const hit=(x,y)=>{let node=document.elementFromPoint(x,y),previous;while(node?.shadowRoot&&node!==previous){previous=node;node=node.shadowRoot.elementFromPoint(x,y)||node;}return node;};
`;
const SNAPSHOT=(token,offset=0,query='',scope='')=>`(()=>{${HELPERS}
 const selector='a[href],button,input,textarea,select,summary,[contenteditable="true"],[role="button"],[role="menuitem"],[role="menuitemcheckbox"],[role="menuitemradio"],[role="tab"],[role="checkbox"],[role="radio"],[role="switch"],[role="option"],[role="combobox"],[role="listbox"],[role="slider"],[tabindex],[onclick],canvas,video,audio,iframe';
 const candidates=roots().flatMap(root=>Array.from(root.querySelectorAll(selector+(${JSON.stringify(query)}?',div,span,img,svg':'')))).filter((node,i,all)=>all.indexOf(node)===i&&visible(node)&&!protectedInput(node)&&!(node.tagName==='A'&&node.hasAttribute('download')));
 const filtered=candidates.filter(node=>(!${JSON.stringify(scope)}||context(node)===${JSON.stringify(scope)})&&(!${JSON.stringify(query)}||[label(node),kind(node),node.id,node.getAttribute('data-testid'),context(node),...Array.from(node.options||[]).map(o=>o.text+' '+o.value)].some(v=>String(v||'').toLowerCase().includes(${JSON.stringify(query.toLowerCase())}))));
 const priority=node=>{const r=node.getBoundingClientRect();return (node.closest('[role="menu"],[role="dialog"],[role="listbox"]')?100:0)+(node.tagName==='A'?0:20)+(r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth?10:0);};
 filtered.sort((a,b)=>priority(b)-priority(a));const selected=filtered.slice(0,Math.max(0,5000-${offset}));
 if(globalThis.__igorekTargets?.nodes.length>=9000)globalThis.__igorekTargets=null;
 const state=globalThis.__igorekTargets ||= {token:${JSON.stringify(token)},offset:0,nodes:[],refs:new WeakMap()};
 const identity=node=>({tag:node.tagName,role:role(node),test:node.getAttribute('data-testid')||'',id:node.id||'',label:label(node),destination:destination(node),context:context(node)});
 for(const node of selected){if(!state.refs.has(node)){if(state.nodes.length>=9000)break;state.refs.set(node,state.nodes.length);state.nodes.push({node,...identity(node)});}else Object.assign(state.nodes[state.refs.get(node)],identity(node));}
 return selected.filter(node=>state.refs.has(node)).map((node,index)=>{const r=node.getBoundingClientRect(),item={ref:'r-'+state.token+'-'+state.refs.get(node),tag:node.tagName.toLowerCase(),kind:kind(node),label:label(node),disabled:disabled(node),focused:document.activeElement===node||node.getRootNode?.()?.activeElement===node,bounds:{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}};
 item.surface=node.closest('[role="menu"],[role="listbox"]')?'menu':node.closest('[role="dialog"]')?'dialog':'page';
 for(const name of ['pressed','checked','expanded','selected','haspopup']){const value=node.getAttribute('aria-'+name);if(value!==null)item[name]=value;}
 if(node.tagName==='INPUT'&&['checkbox','radio'].includes(node.type))item.checked=node.checked;
 if(node.id)item.dom_id=node.id.slice(0,120);
 if(node.getAttribute('data-testid'))item.test_id=node.getAttribute('data-testid').slice(0,100);
 if(context(node))item.context=context(node).slice(0,240);
 if(node.tagName==='A')item.url=node.href.slice(0,2048);
 if(editable(node)||node.tagName==='SELECT')item.value=String(node.value??node.innerText??'').slice(0,250);
 if(node.tagName==='SELECT'){item.options=Array.from(node.options).filter(o=>!${JSON.stringify(query)}||[o.value,o.text].some(v=>v.toLowerCase().includes(${JSON.stringify(query.toLowerCase())}))).slice(0,12).map(o=>({value:o.value.slice(0,120),label:o.text.slice(0,120),disabled:o.disabled}));item.options_total=node.options.length;}
 return item;}).filter(item=>!${JSON.stringify(query)}||[item.label,item.kind,item.test_id,item.dom_id,item.context,...(item.options||[]).map(o=>o.label+' '+o.value)].some(v=>String(v||'').toLowerCase().includes(${JSON.stringify(query.toLowerCase())})));
})()`;
const TARGET=(ref,action,fields={})=>`(()=>{${HELPERS}
 const parts=${JSON.stringify(ref)}.split('-'),s=globalThis.__igorekTargets;
 if(!s||s.token!==parts[1])return {error:'stale_target'};
 const entry=s.nodes[Number(parts[2])-s.offset];let node=entry?.node,rebound=false;
 if(entry&&node&&!node.isConnected&&(entry.context&&entry.test||entry.id)){const matches=roots().flatMap(root=>Array.from(root.querySelectorAll('*'))).filter(n=>n.tagName===entry.tag&&role(n)===entry.role&&context(n)===entry.context&&destination(n)===entry.destination&&(entry.test?n.getAttribute('data-testid')===entry.test:n.id===entry.id)&&visible(n)&&!disabled(n)&&!protectedInput(n));if(matches.length===1){node=entry.node=matches[0];rebound=true;}}
 if(!node||!node.isConnected||node.ownerDocument!==document||!visible(node)||disabled(node))return {error:'stale_target'};
 if(protectedInput(node))return {error:'protected_input'};
 if(destination(node)!==entry.destination||context(node)!==entry.context||(entry.test&&node.getAttribute('data-testid')!==entry.test))return {error:'stale_target'};
 const action=${JSON.stringify(action)};
 if(action==='fill'&&!editable(node))return {error:'target_not_editable'};
 if(action==='select'&&node.tagName!=='SELECT')return {error:'target_not_select'};
 if(action==='check'&&!(node.type==='checkbox'||['checkbox','switch'].includes(role(node))||['true','false'].includes(node.getAttribute('aria-pressed'))))return {error:'target_not_checkable'};
 node.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
 const r=node.getBoundingClientRect(),x=r.left+r.width*${fields.offset_x??0.5},y=r.top+r.height*${fields.offset_y??0.5},top=hit(x,y);
 if(!top||!node.contains(top))return {error:'target_obscured'};
 node.focus();
 if(action==='fill'){if(node.isContentEditable){const selection=getSelection(),range=document.createRange();range.selectNodeContents(node);selection.removeAllRanges();selection.addRange(range);}else node.select?.();}
 if(action==='select'){const option=Array.from(node.options).find(o=>!o.disabled&&(o.value===${JSON.stringify(fields.value||'')}||o.text===${JSON.stringify(fields.value||'')}));if(!option)return {error:'option_not_found'};if(${JSON.stringify(fields._perform_select===true)}){const setter=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;setter.call(node,option.value);node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));}}
 const checked=node.type==='checkbox'?node.checked:(node.getAttribute('aria-pressed')??node.getAttribute('aria-checked'))==='true';
 return {rebound,url:destination(node),x:Math.round(x),y:Math.round(y),already:action==='check'&&checked===${JSON.stringify(fields.checked??false)}};
})()`;
class BrowserInteraction{
 constructor(contents,allowUrl,sleep=ms=>new Promise(r=>setTimeout(r,ms))){this.contents=contents;this.allowUrl=allowUrl;this.sleep=sleep;this.targets=new Map();}
 async evaluate(code,frame=null){let timer;const wc=this.contents();try{return await Promise.race([frame&&frame!==wc.mainFrame?frame.executeJavaScript(code,true):wc.executeJavaScriptInIsolatedWorld(WORLD,[{code}]),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('page_timeout')),5000)})]);}finally{clearTimeout(timer);}}
 async snapshot(query='',scope=''){
  const wc=this.contents();const result=[];
  for(const frame of wc.mainFrame?.framesInSubtree||[null]){
   if(result.length>=5000)break;
   try{
    if(frame&&frame!==wc.mainFrame&&!this.allowUrl(frame.url)&&!['about:blank','about:srcdoc'].includes(frame.url))continue;
    const rows=await this.evaluate(SNAPSHOT(randomUUID().replaceAll('-',''),0,query,scope),frame);
    for(const row of rows){if(frame&&frame!==wc.mainFrame){try{const url=new URL(frame.url);row.frame_url=(url.origin+url.pathname).slice(0,240);}catch{}}this.targets.set(row.ref,{frame,wc});result.push(row);while(this.targets.size>10000)this.targets.delete(this.targets.keys().next().value);}
   }catch{}
  }
  return result;
 }
 async parentPoint(frame,point){
  const wc=this.contents();
  while(frame&&frame!==wc.mainFrame){const parent=frame.parent;if(!parent)return {error:'stale_target'};const index=parent.frames.indexOf(frame);
   point=await this.evaluate(`(()=>{${HELPERS}const owner=roots().flatMap(r=>Array.from(r.querySelectorAll('iframe,frame'))).find(n=>n.contentWindow===window.frames[${index}]);if(!owner)return {error:'frame_unavailable'};owner.scrollIntoView({block:'center',inline:'center',behavior:'instant'});const r=owner.getBoundingClientRect(),sx=r.width/owner.offsetWidth,sy=r.height/owner.offsetHeight,x=r.left+(owner.clientLeft+${point.x})*sx,y=r.top+(owner.clientTop+${point.y})*sy,top=hit(x,y);return top===owner?{x:Math.round(x),y:Math.round(y)}:{error:'target_obscured'};})()`,parent);
   if(point.error)return point;frame=parent;
  }return point;
 }
 async mouse(wc,event,crossFrame){
  if(!crossFrame||!wc.debugger){wc.sendInputEvent(event);return;}
  if(!wc.debugger.isAttached())wc.debugger.attach('1.3');
  const type={mouseMove:'mouseMoved',mouseDown:'mousePressed',mouseUp:'mouseReleased',mouseWheel:'mouseWheel'}[event.type];
  const params={type,x:event.x,y:event.y};
  if(event.button)params.button=event.button;if(event.clickCount)params.clickCount=event.clickCount;
  if(event.type==='mouseWheel'){params.deltaX=-event.deltaX;params.deltaY=-event.deltaY;}
  await wc.debugger.sendCommand('Input.dispatchMouseEvent',params);
 }
 async press(wc,key,crossFrame){
  const shift=key==='Shift+Tab',name=shift?'Tab':key==='Space'?' ':key;
  if(!crossFrame||!wc.debugger){const keyCode=shift?'Tab':key;const modifiers=shift?['shift']:[];wc.sendInputEvent({type:'keyDown',keyCode,modifiers});wc.sendInputEvent({type:'keyUp',keyCode,modifiers});return;}
  if(!wc.debugger.isAttached())wc.debugger.attach('1.3');
  const codes={Enter:13,' ':32,Tab:9,Escape:27,ArrowLeft:37,ArrowUp:38,ArrowRight:39,ArrowDown:40,Home:36,End:35,PageUp:33,PageDown:34,Backspace:8,Delete:46};
  const event={key:name,code:name===' '?'Space':name,windowsVirtualKeyCode:codes[name],modifiers:shift?8:0};
  await wc.debugger.sendCommand('Input.dispatchKeyEvent',{...event,type:'keyDown',...([' ','Enter'].includes(name)?{text:name==='Enter'?'\r':' '}:{})});
  await wc.debugger.sendCommand('Input.dispatchKeyEvent',{...event,type:'keyUp'});
 }
 async run(fields){
  if(!validInteraction(fields))return {error:'invalid_request'};
  const wc=this.contents();if(fields.action==='press'&&!fields.target){wc.focus();await this.press(wc,fields.key,!!wc.debugger);await this.sleep(300);return {performed:true,action:'press'};}const entry=this.targets.get(fields.target);if(!entry||entry.wc!==wc)return {error:'stale_target'};
  const target=await this.evaluate(TARGET(fields.target,fields.action,fields),entry.frame);if(target.error)return target;
  if(!this.allowUrl(target.url)&&!(['about:blank','about:srcdoc'].includes(target.url)&&this.allowUrl(wc.getURL())))return {error:'invalid_url'};
  const point=await this.parentPoint(entry.frame,target);if(point.error)return point;wc.focus();const crossFrame=!!entry.frame&&entry.frame!==wc.mainFrame;
  if(fields.action==='select'){const value=await this.evaluate(TARGET(fields.target,'select',{...fields,_perform_select:true}),entry.frame);if(value.error)return value;}
  else if(fields.action==='fill'){if(crossFrame&&wc.debugger){if(!wc.debugger.isAttached())wc.debugger.attach('1.3');await wc.debugger.sendCommand('Input.insertText',{text:fields.text});}else await wc.insertText(fields.text);}
  else if(fields.action==='press')await this.press(wc,fields.key,crossFrame);
  else if(fields.action==='scroll_element'){const d=fields.direction;await this.mouse(wc,{type:'mouseMove',x:point.x,y:point.y},crossFrame);await this.mouse(wc,{type:'mouseWheel',x:point.x,y:point.y,deltaX:d==='left'?600:d==='right'?-600:0,deltaY:d==='up'?600:d==='down'?-600:0,canScroll:true},crossFrame);}
  else if(fields.action!=='select'&&!target.already){await this.mouse(wc,{type:'mouseMove',x:point.x,y:point.y},crossFrame);if(fields.action!=='hover'){const button=fields.button==='right'?'right':'left',clickCount=fields.button==='double'?2:1;await this.mouse(wc,{type:'mouseDown',x:point.x,y:point.y,button,clickCount},crossFrame);await this.mouse(wc,{type:'mouseUp',x:point.x,y:point.y,button,clickCount},crossFrame);}}
  await this.sleep(['fill','hover','select'].includes(fields.action)?300:700);const deadline=Date.now()+18000;while(wc.isLoading()&&Date.now()<deadline)await this.sleep(100);
  if(wc.isLoading())return {error:'navigation_timeout',performed:true};return {performed:!target.already,action:fields.action,...(target.rebound?{rebound:true}:{}),...(target.already?{state_matches:true}:{})};
 }
}
function compactPage({auth,url,title,text,links,elements,posts}){
 const result={auth,url,title:String(title||'').slice(0,300),elements:[],text:String(text||'').slice(0,1500),links:[],coverage:'bounded_view_only',text_truncated:String(text||'').length>1500,elements_total:elements.length};
 if(posts){result.posts=[];for(const p of posts.slice(0,10)){const post={id:p.id,url:p.url,text:String(p.text||'').slice(0,650),author:String(p.author||'').slice(0,100),posted_at:p.posted_at||'',social_context:String(p.social_context||'').slice(0,120),has_video:!!p.has_video,reactions:p.reactions||{like:null,repost:null,bookmark:null},controls:elements.filter(e=>e.context===p.url&&e.kind!=='link').slice(0,6).map(e=>({ref:e.ref,kind:e.kind,label:e.label.slice(0,60),test_id:e.test_id,pressed:e.pressed,checked:e.checked,expanded:e.expanded})),text_truncated:String(p.text||'').length>650,truncated:!!p.truncated||String(p.text||'').length>650,links:(p.links||[]).filter(v=>typeof v==='string'&&v.length<=240).slice(0,2),images:(p.images||[]).filter(v=>typeof v==='string'&&v.length<=240).slice(0,1)};if(JSON.stringify(result.posts).length+JSON.stringify(post).length>3500)break;result.posts.push(post);}result.posts_omitted=posts.length-result.posts.length;}

 for(const link of links||[]){if(typeof link.url!=='string'||link.url.length>240)continue;if(JSON.stringify(result.links).length+JSON.stringify(link).length>500)break;result.links.push({text:String(link.text||'').slice(0,80),url:link.url});}
 for(const item of elements){const value={...item,label:String(item.label||'').slice(0,140)};if(value.url&&value.url.length>240){try{value.host=new URL(value.url).hostname}catch{}delete value.url;}result.elements.push(value);if(JSON.stringify(result).length>10500){result.elements.pop();break;}}
 result.elements_omitted=elements.length-result.elements.length;result.open_menu=elements.filter(e=>e.surface==='menu').slice(0,8).map(e=>({ref:e.ref,label:String(e.label||'').slice(0,80),kind:e.kind}));while(result.elements.length&&JSON.stringify(result).length>11000)result.elements.pop();result.elements_omitted=elements.length-result.elements.length;result.open_surfaces=[...new Set(result.elements.filter(e=>e.surface&&e.surface!=='page').map(e=>e.surface))];return result;
}
module.exports={BrowserInteraction,validInteraction,compactPage,SNAPSHOT,TARGET,WORLD,KEYS};
