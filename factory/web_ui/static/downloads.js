(function(global){
 'use strict';
 function groups(items,query=''){
  const search=query.trim().toLocaleLowerCase('ru');const values=(Array.isArray(items)?items:[]).filter(item=>item&&typeof item.id==='string'&&typeof item.name==='string'&&Number.isFinite(item.at)&&new Date(item.at).getTime()>0&&(!search||item.name.toLocaleLowerCase('ru').includes(search))).slice().sort((a,b)=>b.at-a.at),result=new Map();
  for(const item of values){const date=new Date(item.at),key=[date.getFullYear(),date.getMonth()+1,date.getDate()].join('-');if(!result.has(key))result.set(key,{key,at:item.at,items:[]});result.get(key).items.push(item);}return [...result.values()];
 }
 const el=(tag,cls='',text='')=>{const node=document.createElement(tag);node.className=cls;node.textContent=text;return node;};
 function create({host,model,call,glyph}){
  model.downloadQuery??='';model.downloadGroups??={};let data={downloads:[]},busy=false;
  const page=el('section','downloads-page'),content=el('div','downloads-content'),crumb=el('p','downloads-crumb','Браузер  ›  История загрузок');page.append(crumb,content);content.append(el('h1','','История загрузок'));
  const search=el('label','downloads-search'),input=el('input');search.append(glyph('search'));input.type='search';input.placeholder='Поиск в истории загрузок';input.setAttribute('aria-label',input.placeholder);input.value=model.downloadQuery;search.append(input);content.append(search);
  const toolbar=el('div','downloads-toolbar'),count=el('span'),clear=el('button','downloads-clear','Очистить всё');clear.type='button';toolbar.append(count,clear);content.append(toolbar);
  const status=el('p','downloads-status'),list=el('div','downloads-days');status.setAttribute('role','status');content.append(status,list);host.append(page);
  async function action(operation,fields={}){if(busy)return;busy=true;clear.disabled=true;status.textContent='';try{update(await call(operation,fields));}catch(error){status.textContent=error.message;}finally{busy=false;clear.disabled=!(data.downloads||[]).length;}}
  clear.addEventListener('click',()=>action('clearDownloads'));input.addEventListener('input',()=>{model.downloadQuery=input.value;render();});
  function iconButton(name,title,run){const button=el('button','downloads-icon');button.type='button';button.title=title;button.setAttribute('aria-label',title);button.append(glyph(name));button.addEventListener('click',run);return button;}
  function render(){
   list.replaceChildren();const rows=data.downloads||[],dates=groups(rows,model.downloadQuery);count.textContent=model.downloadQuery?'Найдено: '+dates.reduce((n,group)=>n+group.items.length,0):'Сохранённые загрузки · '+rows.length;clear.disabled=busy||!rows.length;status.textContent=data.downloads_error||'';
   if(!dates.length){list.append(el('p','downloads-empty',model.downloadQuery?'По этому запросу ничего не найдено.':'Здесь появятся скачанные файлы.'));return;}
   for(const [index,group] of dates.entries()){
    const day=el('details','downloads-day');day.open=Object.hasOwn(model.downloadGroups,group.key)?model.downloadGroups[group.key]:index===0;day.addEventListener('toggle',()=>{model.downloadGroups[group.key]=day.open;});const heading=el('summary');heading.append(el('span','',new Intl.DateTimeFormat('ru',{day:'numeric',month:'short',year:'numeric'}).format(group.at)),glyph('chevron'));day.append(heading);
    for(const item of group.items){const row=el('div','download-row'),icon=el('span','download-file-icon');icon.append(glyph(/\.(png|jpe?g|gif|webp|avif|bmp|svg)$/i.test(item.name)?'image':'file'));const copy=el('div','download-file-copy'),name=el('span','download-file-name',item.name);name.title=item.name;copy.append(name);
     if(item.state!=='completed'){const label={progressing:'Загружается',interrupted:'Прервано',cancelled:'Отменено'}[item.state]||'Состояние неизвестно';copy.append(el('span','download-file-state',label));if(item.state==='progressing'&&item.total>0){const progress=el('progress');progress.max=item.total;progress.value=item.received||0;progress.setAttribute('aria-label','Загрузка '+item.name);copy.append(progress);}}
     const time=el('time','download-time',new Intl.DateTimeFormat('ru',{hour:'2-digit',minute:'2-digit'}).format(item.at));time.dateTime=new Date(item.at).toISOString();const reveal=iconButton('folder','Показать в папке: '+item.name,()=>action('openDownload',{id:item.id}));reveal.disabled=!item.can_reveal;const remove=iconButton('close','Убрать из истории: '+item.name,()=>action('removeDownload',{id:item.id}));row.dataset.downloadId=item.id;row.append(icon,copy,time,reveal,remove);day.append(row);
    }list.append(day);
   }
   if(rows.length>=data.downloads_limit)list.append(el('p','downloads-status','Сохранены последние '+data.downloads_limit+' записей.'));
  }
  function update(value){data=value||{downloads:[]};render();}
  function error(message){status.textContent=message;}
  update(data);call('downloads').then(update).catch(reason=>{status.textContent=reason.message;});return {update,error};
 }
 const api={groups,create};if(typeof module!=='undefined'&&module.exports)module.exports=api;else global.IgorekDownloads=api;
})(typeof window==='undefined'?globalThis:window);
