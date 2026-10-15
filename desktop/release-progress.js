window.renderUpdate=value=>{
  document.getElementById('message').textContent=value.message;
  document.getElementById('detail').textContent=value.detail||'';
  const loading=document.getElementById('loading'),progress=document.getElementById('progress');
  loading.hidden=!Object.hasOwn(value,'progress');
  if(typeof value.progress==='number')progress.value=value.progress;else progress.removeAttribute('value');
  document.getElementById('percent').textContent=typeof value.progress==='number'?Math.floor(value.progress*100)+'%':'';
  const buttons=document.getElementById('buttons');buttons.replaceChildren();
  for(const [index,label] of (value.buttons||[]).entries()){
    const button=document.createElement('button');button.textContent=label;
    button.addEventListener('click',()=>{for(const item of buttons.children)item.disabled=true;window.IgorekUpdate.choose(index);});
    buttons.appendChild(button);
  }
};
