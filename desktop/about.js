'use strict';
const parameters=new URLSearchParams(location.search);
document.getElementById('about-version').textContent='Версия '+String(parameters.get('version')||'—').slice(0,80);
try{const url=new URL(parameters.get('github'));if(url.protocol==='https:'&&url.hostname==='github.com'&&!url.username&&!url.password)document.getElementById('about-github').href=url.href;}catch{}
document.getElementById('about-close').addEventListener('click',()=>window.close());
document.addEventListener('keydown',event=>{if(event.key==='Escape')window.close();});

try{const changes=JSON.parse(parameters.get('changes')||'[]');if(Array.isArray(changes))for(const text of changes.slice(0,4)){if(typeof text!=='string'||text.length>160)continue;const item=document.createElement('li');item.textContent=text;document.getElementById('about-changes').append(item);}document.getElementById('about-release').hidden=!document.getElementById('about-changes').childElementCount;}catch{}
