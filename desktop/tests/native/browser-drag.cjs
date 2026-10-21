const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {BrowserInteraction}=require('../../browser-interaction.cjs');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'igorek-browser-drag-'));
const output=process.env.IGOREK_TEST_OUTPUT||path.join(root,'proof');fs.mkdirSync(output,{recursive:true});
app.setPath('userData',path.join(root,'electron'));
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function run(){
 await app.whenReady();
 const window=new BrowserWindow({show:true,width:1000,height:900,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
 const fixture=`<canvas id="drawing" width="400" height="300" style="border:1px solid;touch-action:none"></canvas><script>
 globalThis.events=[];globalThis.shape='line';const canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d');let start;
 canvas.addEventListener('pointerdown',e=>{events.push({type:e.type,buttons:e.buttons,trusted:e.isTrusted});start={x:e.offsetX,y:e.offsetY};ctx.beginPath();ctx.moveTo(e.offsetX,e.offsetY);canvas.setPointerCapture(e.pointerId);});
 canvas.addEventListener('pointermove',e=>{if(e.buttons&1){events.push({type:e.type,buttons:e.buttons,trusted:e.isTrusted});if(shape==='line'){ctx.lineTo(e.offsetX,e.offsetY);ctx.lineWidth=5;ctx.strokeStyle='#62ab40';ctx.stroke();}}});
 canvas.addEventListener('pointerup',e=>{events.push({type:e.type,buttons:e.buttons,trusted:e.isTrusted});if(shape==='ellipse'){ctx.beginPath();ctx.ellipse((start.x+e.offsetX)/2,(start.y+e.offsetY)/2,Math.abs(start.x-e.offsetX)/2,Math.abs(start.y-e.offsetY)/2,0,0,2*Math.PI);ctx.lineWidth=5;ctx.strokeStyle='#62ab40';ctx.stroke();}});
 </script>`;
 const file=path.join(root,'canvas.html');
 fs.writeFileSync(file,'<!doctype html><body style="background:#fff">'+fixture+'<iframe style="display:block;width:450px;height:340px;border:0" srcdoc="'+fixture.replaceAll('&','&amp;').replaceAll('"','&quot;')+'"></iframe>');
 await window.loadFile(file);window.focus();await pause(100);
 const wc=window.webContents,interaction=new BrowserInteraction(()=>wc,url=>url===wc.getURL(),pause);
 const rows=await interaction.snapshot();
 const canvases=rows.filter(row=>row.tag==='canvas');assert.equal(canvases.length,2);
 const results=[];
 for(const target of canvases){
  const frame=interaction.targets.get(target.ref).frame;
  for(const shape of ['line','ellipse']){
   await frame.executeJavaScript(`events=[];shape=${JSON.stringify(shape)};document.querySelector('canvas').getContext('2d').clearRect(0,0,400,300);`);
   const points=shape==='line'?[{x:.1,y:.1},{x:.5,y:.8},{x:.9,y:.2}]:[{x:.15,y:.2},{x:.85,y:.8}];
   const result=await interaction.run({action:'drag',target:target.ref,points,duration_ms:200});assert.equal(result.performed,true);
   const proof=await frame.executeJavaScript(`({events,pixels:[...document.querySelector('canvas').getContext('2d').getImageData(0,0,400,300).data].filter((v,i)=>i%4===3&&v>0).length})`);
   assert.equal(proof.events[0].type,'pointerdown');assert.equal(proof.events.at(-1).type,'pointerup');
   assert.ok(proof.events.some(e=>e.type==='pointermove'&&e.buttons===1));assert.ok(proof.events.every(e=>e.trusted));assert.ok(proof.pixels>100);
   results.push({surface:target.frame_url?'iframe':'main',shape,pixels:proof.pixels,events:proof.events.length});
  }
 }
 fs.writeFileSync(path.join(output,'canvas.png'),(await wc.capturePage()).toPNG());
 fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify({status:'PASS',results,output}));window.destroy();
}
run().then(()=>app.exit(0)).catch(error=>{console.error(error);app.exit(1);});
