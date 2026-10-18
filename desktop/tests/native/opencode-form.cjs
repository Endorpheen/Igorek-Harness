const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

async function verifyOpenCodeForm({BrowserWindow,bundle,clickNative,until,output}){
  const source=fs.readFileSync(path.join(bundle,'packages/factory/web_ui/static/app.js'),'utf8');
  const start=source.indexOf('function renderOpenCodeQuestion(item) {');
  assert(start>=0);
  const code=source.slice(start,source.indexOf('\n}\n',start)+3);
  const view=new BrowserWindow({show:true,width:800,height:800,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  try{
    await view.loadURL('data:text/html,<html><body></body></html>');
    await view.webContents.executeJavaScript(`
      function element(tag,cls='',text=''){const node=document.createElement(tag);node.className=cls;node.textContent=text;return node;}
      const encode=encodeURIComponent;
      const api=async(route,options)=>{window.posted={route,body:JSON.parse(options.body)};return {resolved:true};};
      const upsert=item=>window.resolved=item.resolved;
      const toast=message=>{throw Error(message);};
      const registerTimelineUpdater=(card)=>card;
      ${code}
      document.body.append(renderOpenCodeQuestion({task_id:'oc_fixture',request_id:'frm_fixture',input:{title:'OpenCode',opencode:{fields:[
        {key:'mode',type:'string',title:'Mode',required:true,options:[{value:'advanced',label:'Advanced'}]},
        {key:'count',type:'integer',title:'Count',required:true,when:[{key:'mode',op:'eq',value:'advanced'}]},
        {key:'enabled',type:'boolean',title:'Enabled'},
        {key:'tags',type:'multiselect',title:'Tags',options:[{value:'one',label:'One'},{value:'two',label:'Two'}]},
        {key:'hidden',type:'string',title:'Hidden',required:true,when:[{key:'mode',op:'eq',value:'other'}]}
      ]}}}));
      window.addEventListener('click',event=>{if(event.isTrusted&&event.target.matches('button[type=submit]'))window.nativeSubmit=true;},true);
    `);
    assert.equal(await view.webContents.executeJavaScript("[...document.querySelectorAll('fieldset')].find(node=>node.textContent.includes('Count')).hidden"),true);
    await view.webContents.executeJavaScript(`
      const select=document.querySelector('select');select.value='advanced';select.dispatchEvent(new Event('change',{bubbles:true}));
      const count=document.querySelector('input[type=number]');count.value='7';count.dispatchEvent(new Event('input',{bubbles:true}));
      const choices=document.querySelectorAll('input[type=checkbox]');choices[0].checked=true;choices[2].checked=true;
    `);
    await clickNative(view,'button[type=submit]');
    await until(()=>view.webContents.executeJavaScript('Boolean(window.resolved)'), 'OpenCode native form did not submit');
    const reply=await view.webContents.executeJavaScript('({posted:window.posted,trusted:window.nativeSubmit})');
    assert.equal(reply.trusted,true);assert.equal(reply.posted.route,'/api/tasks/oc_fixture/user-input/frm_fixture');
    assert.deepEqual(reply.posted.body,{response:{answer:{mode:'advanced',count:7,enabled:true,tags:['two']}},cancelled:false});
    await view.webContents.executeJavaScript('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    await new Promise(resolve=>setTimeout(resolve,200));
    fs.writeFileSync(path.join(output,'opencode-form.png'),(await view.webContents.capturePage()).toPNG());
  }finally{view.destroy();}
}
module.exports={verifyOpenCodeForm};
