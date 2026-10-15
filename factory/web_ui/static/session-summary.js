(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.IgorekSessionSummary=api;})(typeof globalThis==='object'?globalThis:this,function(){
  function sources(items){
    const seen=new Set(),result=[];
    for(const item of items||[])if(item.type==='user'||item.role==='user')for(const file of Array.isArray(item.attachments)?item.attachments:[]){
      if(!/^[0-9a-f]{32}$/.test(String(file?.upload_id||''))||seen.has(file.upload_id))continue;
      seen.add(file.upload_id);result.push(file);
    }
    return result.reverse();
  }
  function mount({host,getState,api,openChanges,openSource,attach}){
    const make=(tag,cls='',text='')=>{const node=document.createElement(tag);node.className=cls;node.textContent=text;return node;};
    const card=make('aside','session-summary');card.id='session-summary';card.setAttribute('aria-label','Сводка сессии');card.hidden=true;host.append(card);
    let session='',generation=0,data=null,processes=[],changesError='',processError='',busy=false,lastChanges=0,expanded=false,signature='',timer=null;
    const stopping=new Set();
    const visible=()=>{const state=getState();return state.paired&&state.selected&&state.session?.session_id===state.selected&&!state.panel.open&&(!state.workspaceView||state.workspaceView==='main')&&!host.hidden;};
    function draw(){
      const state=getState(),files=sources(state.items);
      const next=JSON.stringify([session,state.session?.meta?.cwd,data?.totals,data?.base_label,processes,files,expanded,changesError,processError,[...stopping]]);
      if(next===signature)return;signature=next;card.replaceChildren();
      const path=String(state.session?.meta?.cwd||''),heading=make('strong','summary-project',path.split(/[\\/]/).filter(Boolean).pop()||'Текущая сессия');heading.title=path;card.append(heading);
      const changes=make('button','summary-changes');changes.type='button';changes.append(make('span','','⊞'),make('span','summary-label','Изменения'));
      const totals=data?.totals,counts=make('span','summary-counts');
      if(totals){counts.append(make('span','summary-add','+'+(totals.additions||0)),make('span','summary-delete','−'+(totals.deletions||0)));if(totals.partial)counts.append(make('span','summary-unknown','?'));}
      else counts.textContent=changesError?'—':'…';
      changes.title=changesError||data?.base_label||'Изменения текущей сессии';changes.append(counts);changes.addEventListener('click',openChanges);card.append(changes);
      const processSection=make('section','summary-section');processSection.append(make('h3','','Фоновые процессы'));
      if(!processes.length)processSection.append(make('p','summary-empty',processError?'Не удалось проверить процессы':'Нет запущенных процессов'));
      for(const process of processes){
        const row=make('div','summary-process');row.dataset.processId=process.id;
        const label=make('span','summary-label',process.label||'Команда');label.title=process.label||'';row.append(make('span','summary-process-mark','›_'),label);
        const stop=make('button','summary-stop',stopping.has(process.id)||process.stopping?'…':'■');stop.type='button';stop.disabled=stopping.has(process.id)||process.stopping;stop.title='Остановить процесс';stop.setAttribute('aria-label','Остановить '+(process.label||'процесс'));
        stop.addEventListener('click',async()=>{const id=session;stopping.add(process.id);draw();try{await api('/api/sessions/'+encodeURIComponent(id)+'/activity/'+encodeURIComponent(process.id)+'/stop',{method:'POST',body:'{}'});await refresh(true);}catch(error){if(session===id){processError=error.message;draw();}}finally{if(session===id){stopping.delete(process.id);draw();}}});row.append(stop);processSection.append(row);
      }
      if(processes.length||processError)card.append(processSection);
      const sourceSection=make('section','summary-section summary-sources'),head=make('div','summary-section-head'),add=make('button','summary-add-source','+');add.type='button';add.title='Прикрепить источник к сообщению';add.setAttribute('aria-label',add.title);add.addEventListener('click',attach);head.append(make('h3','','Источники'),add);sourceSection.append(head);
      if(!files.length)sourceSection.append(make('p','summary-empty','Пока нет источников'));
      for(const file of expanded?files:files.slice(0,3)){
        const row=make('button','summary-source');row.type='button';row.dataset.uploadId=file.upload_id;row.title=String(file.name||'Файл');
        const icon=make('span','summary-source-icon');
        if(['image/png','image/jpeg','image/webp','image/gif','image/bmp','image/avif'].includes(file.mime_type)){const image=make('img');image.src='/api/sessions/'+encodeURIComponent(session)+'/uploads/'+encodeURIComponent(file.upload_id);image.alt='';image.loading='lazy';icon.append(image);}else icon.textContent='▧';
        row.append(icon,make('span','summary-label',String(file.name||'Файл')));row.addEventListener('click',()=>openSource(file,session));sourceSection.append(row);
      }
      if(files.length>3){const more=make('button','summary-more',expanded?'Свернуть':'Показать все · '+files.length);more.type='button';more.setAttribute('aria-expanded',String(expanded));more.addEventListener('click',()=>{expanded=!expanded;draw();});sourceSection.append(more);}
      card.append(sourceSection);
    }
    async function refresh(force=false){
      if(!visible()||document.hidden||busy)return;
      const state=getState(),id=session,version=generation;busy=true;
      const updateChanges=force||Date.now()-lastChanges>10000;
      const requests=[api('/api/sessions/'+encodeURIComponent(id)+'/activity')];
      if(updateChanges)requests.push(api('/api/sessions/'+encodeURIComponent(id)+'/changes/review?mode='+encodeURIComponent(state.panel.review.mode)+'&base='+encodeURIComponent(state.panel.review.base||'')+'&include_untracked='+(state.panel.review.includeUntracked?'1':'0')));
      const results=await Promise.allSettled(requests);
      if(session!==id||generation!==version){busy=false;return;}
      busy=false;
      if(results[0].status==='fulfilled'){processes=results[0].value.processes||[];processError='';}else{processes=[];processError=results[0].reason.message;}
      if(updateChanges){lastChanges=Date.now();if(results[1].status==='fulfilled'){data=results[1].value;changesError='';}else{data=null;changesError=results[1].reason.message;}}
      draw();
    }
    function sync(){
      const state=getState(),show=Boolean(visible());card.hidden=!show;host.classList.toggle('summary-visible',show);
      if(!show){clearInterval(timer);timer=null;return;}
      if(session!==state.selected){session=state.selected;generation++;data=null;processes=[];changesError=processError='';lastChanges=0;expanded=false;signature='';stopping.clear();}
      draw();
      if(!timer){refresh();timer=setInterval(()=>refresh(),3000);}
    }
    window.addEventListener('focus',()=>{sync();refresh(true);});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden){sync();refresh(true);}});
    return {sync,refresh,sources:()=>sources(getState().items)};
  }
  return {mount,sources};
});
