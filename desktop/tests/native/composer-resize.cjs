const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-composer-resize-'));
const output=process.env.IGOREK_TEST_OUTPUT||path.join(root,'proof');fs.mkdirSync(output,{recursive:true});
app.setPath('userData',path.join(root,'electron'));
let window;
async function run(){
  const repo=path.resolve(__dirname,'../../..');
  await app.whenReady();
  const [width,height]=(process.env.IGOREK_TEST_VIEWPORT||'1200x850').split('x').map(Number);
  assert.ok(Number.isFinite(width)&&Number.isFinite(height)&&width>=900&&height>=600);
  window=new BrowserWindow({show:true,width,height,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  const html=fs.readFileSync(path.join(repo,'factory/web_ui/static/index.html'),'utf8');
  const start=html.indexOf('<div class="composer-wrap">'),end=html.indexOf('<div class="composer-caption">',start);
  assert.ok(start>0&&end>start);
  const css=fs.readFileSync(path.join(repo,'factory/web_ui/static/app.css'),'utf8')+fs.readFileSync(path.join(repo,'desktop/window-chrome.css'),'utf8');
  const file=path.join(root,'layout.html');
  fs.writeFileSync(file,'<!doctype html><html lang="ru"><meta charset="utf-8"><style>'+css+'</style><body><div class="app-shell" style="display:block"><section class="conversation-view" style="display:flex;position:fixed;left:20px;top:40px;bottom:20px;width:900px;justify-content:flex-end">'+html.slice(start,end)+'</div></section></div></body></html>');
  await window.loadFile(file);
  await window.webContents.executeJavaScript(`
    document.body.classList.add('igorek-window-framed','igorek-browser-attached');
    document.querySelector('#message-input').value='Retained draft during browser resizing';
    const button=document.querySelector('#composer-model');
    button.replaceChildren();
    for(const [className,text] of [['composer-model-dot',''],['composer-model-profile','qwen-next-64k'],['composer-model-separator','·'],['composer-model-name','qwen3.8-flash-next-iq3']]) {
      const span=document.createElement('span');span.className=className;span.textContent=text;button.append(span);
    }
    document.querySelector('#composer-meta').hidden=false;
    for(const id of ['mic-button','voice-button'])document.getElementById(id).hidden=false;
    for(const button of document.querySelectorAll('button'))button.disabled=false;
    const access=document.createElement('button');access.type='button';access.className='composer-access-button';access.textContent='◉';
    document.querySelector('.composer-left').append(access);
    const clone=document.querySelector('#composer-form').cloneNode(true);
    clone.classList.add('workspace-chat-compose');
    for(const node of clone.querySelectorAll('[id]'))node.removeAttribute('id');
    clone.removeAttribute('id');
    const side=document.createElement('section');side.id='resize-side-fixture';side.className='workspace-special';
    side.style.cssText='position:fixed;right:0;top:36px;bottom:auto;height:auto;background:#171a1c;container-type:inline-size';
    const sideWrap=document.createElement('div');sideWrap.className='composer-wrap workspace-chat-composer-wrap';sideWrap.append(clone);side.append(sideWrap);document.body.append(side);
    window.__resizeSide=side;
  `);
  const samples=[];
  const widths=[900,800,760,720,700,680,660,640,621,620,619,600,560,480,400,341,340,339,300,260,240,260,300,339,340,341,400,480,560,600,619,620,621,640,660,680,700,720,760,800,900];
  for(const busy of [false,true]) {
    for(const width of widths) {
      const result=await window.webContents.executeJavaScript(`(async()=>{
        const desired=${width},busy=${busy};
        const chat=document.querySelector('.conversation-view');
        chat.style.width=desired+'px';
        const panel=window.innerWidth-desired-60;
        document.documentElement.style.setProperty('--igorek-browser-width',panel+'px');
        window.__resizeSide.style.width=Math.max(240,panel)+'px';
        for(const button of document.querySelectorAll('.stop-button'))button.hidden=!busy;
        document.body.classList.add('igorek-browser-resizing');
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        const rect=e=>{const b=e.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,width:b.width,height:b.height}};
        const failures=[];
        for(const form of document.querySelectorAll('#composer-form,.workspace-chat-compose')) {
          const bounds=rect(form),visible=e=>{const r=rect(e);return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
          const items=[...form.querySelectorAll('.composer-left > *,.composer-actions > *')].filter(visible);
          for(const item of items){const r=rect(item);if(r.left<bounds.left-1||r.right>bounds.right+1||r.top<bounds.top-1||r.bottom>bounds.bottom+1)failures.push({kind:'outside',form:form.id||'workspace',item:item.className,bounds,rect:r});}
          for(let i=0;i<items.length;i++)for(let j=i+1;j<items.length;j++){
            const a=rect(items[i]),b=rect(items[j]);
            if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)failures.push({kind:'overlap',items:[items[i].className,items[j].className],rects:[a,b]});
          }
          for(const button of form.querySelectorAll('.send-button,.stop-button'))if(visible(button)){
            const b=rect(button),point=document.elementFromPoint(b.left+b.width/2,b.top+b.height/2);
            if(!button.contains(point))failures.push({kind:'covered',button:button.className,coveredBy:point?.className,viewport:{width:innerWidth,height:innerHeight},rect:b});
          }
        }
        return {width:chat.getBoundingClientRect().width,busy,failures,draft:document.querySelector('#message-input').value};
      })()`);
      samples.push(result);
      if(result.failures.length) {
        fs.writeFileSync(path.join(output,'resize-failure.png'),(await window.webContents.capturePage()).toPNG());
        fs.writeFileSync(path.join(output,'measurements.json'),JSON.stringify(samples,null,2));
        assert.deepEqual(result.failures,[],JSON.stringify(result));
      }
      assert.equal(result.draft,'Retained draft during browser resizing');
      if(busy&&[640,620,340].includes(width))fs.writeFileSync(path.join(output,'resize-'+width+'.png'),(await window.webContents.capturePage()).toPNG());
    }
  }
  fs.writeFileSync(path.join(output,'measurements.json'),JSON.stringify(samples,null,2));
  console.log(JSON.stringify({status:'PASS',samples:samples.length,output}));
}
run().then(()=>app.exit(0)).catch(error=>{console.error(error);app.exit(1);});
