(() => {
  const bridge=window.IgorekWhatsNew,toggle=document.getElementById('whats-new-enabled'),error=document.getElementById('whats-new-error');
  let enabled=true;
  bridge.read().then(value=>{
    document.getElementById('whats-new-version').textContent='Версия '+String(value.version||'—').slice(0,80);
    const list=document.getElementById('whats-new-changes');
    for(const text of Array.isArray(value.changes)?value.changes.slice(0,4):[]){if(typeof text!=='string'||text.length>160)continue;const item=document.createElement('li');item.textContent=text;list.append(item);}
    enabled=value.enabled!==false;toggle.checked=enabled;toggle.disabled=false;
  }).catch(()=>{error.textContent='Не удалось прочитать описание версии.';});
  toggle.addEventListener('change',async()=>{toggle.disabled=true;error.textContent='';try{const value=await bridge.set(toggle.checked);enabled=value.enabled;toggle.checked=enabled;}catch{toggle.checked=enabled;error.textContent='Не удалось сохранить настройку.';}finally{toggle.disabled=false;}});
  document.getElementById('whats-new-close').addEventListener('click',()=>window.close());
  document.addEventListener('keydown',event=>{if(event.key==='Escape')window.close();});
})();
