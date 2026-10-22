const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {execFileSync} = require('node:child_process');

async function verifyArtifactPreview({BrowserWindow, session, output, python}) {
  const isolated=session.fromPartition('artifact-preview-'+Date.now()),requests=[];
  const urls=['https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js','https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js','https://unpkg.com/three@0.128.0/build/three.min.js'];
  const drawing=`
    const renderer=new THREE.WebGLRenderer({antialias:false});renderer.setSize(240,180);document.body.append(renderer.domElement);
    const scene=new THREE.Scene();scene.background=new THREE.Color(0x15252f);
    const camera=new THREE.PerspectiveCamera(50,240/180,.1,100);camera.position.z=4;
    const cube=new THREE.Mesh(new THREE.BoxGeometry(1.6,1.6,1.6),new THREE.MeshNormalMaterial());scene.add(cube);
    window.probe={revision:THREE.REVISION,node:typeof require,frames:0,hashes:[],parentBlocked:false};
    try{parent.document.body;}catch(_){probe.parentBlocked=true;}
    fetch('/api/private-fixture').catch(()=>{});fetch('https://untrusted.invalid/private-fixture').catch(()=>{});
    function draw(){cube.rotation.x+=.055;cube.rotation.y+=.07;renderer.render(scene,camera);probe.frames++;
      if(probe.hashes.length<16){const pixels=new Uint8Array(240*180*4),gl=renderer.getContext();gl.readPixels(0,0,240,180,gl.RGBA,gl.UNSIGNED_BYTE,pixels);let hash=0;for(let i=0;i<pixels.length;i+=13)hash=(hash*31+pixels[i])>>>0;probe.hashes.push(hash);probe.glError=gl.getError();}
      requestAnimationFrame(draw);}draw();
  `;
  const documents=urls.map(url=>{
    const html='<!doctype html><body style="margin:0;background:#15252f"><script src="'+url+'"></script><script>'+drawing+'</script><script src="https://untrusted.invalid/disallowed.js"></script>';
    return JSON.parse(execFileSync(python,['-I','-c','import json,sys; from factory.web_ui.server import _PREVIEW_CSP; from factory.web_ui.artifact_preview import prepare_html_preview; print(json.dumps(dict(csp=_PREVIEW_CSP,html=prepare_html_preview(sys.stdin.buffer.read()).decode())))'],{input:html,encoding:'utf8',maxBuffer:16*1024*1024}));
  });
  isolated.protocol.handle('https',request=>{requests.push(request.url);return new Response('Blocked fixture',{status:403});});
  let privateRequests=0,window;
  const server=http.createServer((request,response)=>{
    if(request.url.startsWith('/preview/')){
      const doc=documents[Number(request.url.split('/').pop())];
      response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':doc.csp,'X-Frame-Options':'SAMEORIGIN','Referrer-Policy':'no-referrer'});response.end(doc.html);
    }else if(request.url.startsWith('/api/')){privateRequests++;response.writeHead(403);response.end();}
    else{response.setHeader('Content-Type','text/html');response.end('<!doctype html><body style="background:#171b1a;color:white">Offline Three.js preview<iframe sandbox="allow-scripts" src="/preview/0" style="display:block;width:300px;height:240px"></iframe>');}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{
    window=new BrowserWindow({width:600,height:420,show:true,webPreferences:{session:isolated,sandbox:true,contextIsolation:true,nodeIntegration:false,webSecurity:true}});
    await window.loadURL('http://127.0.0.1:'+server.address().port);
    const results=[];
    for(let index=0;index<documents.length;index++){
      if(index)await window.webContents.executeJavaScript(`document.querySelector('iframe').src='/preview/${index}';void 0;`);
      const deadline=Date.now()+20000;let probe;
      while(Date.now()<deadline){
        const frame=window.webContents.mainFrame.frames.find(frame=>frame.url.endsWith('/preview/'+index));
        if(frame)probe=await frame.executeJavaScript('window.probe || null');
        if(probe?.frames>=16)break;
        await new Promise(resolve=>setTimeout(resolve,80));
      }
      assert.ok(probe?.frames>=16,'Offline preview did not animate: '+JSON.stringify(probe));
      assert.equal(probe.revision,'128');assert.equal(probe.glError,0);
      assert.ok(new Set(probe.hashes).size>=8,'Rendered pixels do not change');
      assert.equal(probe.node,'undefined');assert.equal(probe.parentBlocked,true);
      results.push({frames:probe.frames,distinctFrames:new Set(probe.hashes).size});
      if(index===0)fs.writeFileSync(path.join(output,'artifact-webgl.png'),(await window.webContents.capturePage()).toPNG());
    }
    assert.equal(privateRequests,0);assert.deepEqual(requests,[]);
    const result={status:'passed',offlineThreeRevision:'128',origins:results,networkRequests:0,interfaceRequests:0,parentIsolated:true,nodeUnavailable:true};
    fs.writeFileSync(path.join(output,'artifact-preview.json'),JSON.stringify(result,null,2));
    return result;
  }finally{
    window?.destroy();isolated.protocol.unhandle('https');
    await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
  }
}
module.exports={verifyArtifactPreview};
