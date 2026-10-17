(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.IgorekSessionSummary=api;})(typeof globalThis==='object'?globalThis:this,function(){
  function sources(items){
    const seen=new Set(),result=[];
    for(const item of items||[])if(item.type==='user'||item.role==='user')for(const file of Array.isArray(item.attachments)?item.attachments:[]){
      if(!/^[0-9a-f]{32}$/.test(String(file?.upload_id||''))||seen.has(file.upload_id))continue;
      seen.add(file.upload_id);result.push(file);
    }
    return result.reverse();
  }
  function groups(items,references){
    const result={pull_request:[],file:[],web:[],upload:sources(items).map(file=>({...file,kind:'upload'}))},seen=new Set();
    for(const source of Array.isArray(references)?references:[]){
      if(!source||!['file','web','pull_request'].includes(source.kind)||!/^[0-9a-f]{64}$/.test(String(source.id||'')))continue;
      if(source.kind!=='file'){
        try{const url=new URL(source.url);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)continue;}catch{continue;}
      }
      const key=source.kind+':'+source.id;if(seen.has(key))continue;seen.add(key);result[source.kind].push(source);
    }
    return result;
  }
  function mount({host,getState,api,openChanges,openSource,attach,getLanguage=()=> 'ru'}){
    const label=(ru,en)=>getLanguage()==='en'?en:ru;
    const make=(tag,cls='',text='')=>{const node=document.createElement(tag);node.className=cls;node.textContent=text;return node;};
    const card=make('aside','session-summary');card.id='session-summary';card.setAttribute('aria-label','Сводка сессии');card.hidden=true;host.append(card);
    let session='',generation=0,data=null,processes=[],references=[],sourcesError='',truncated=false,filesPartial=false,changesError='',processError='',busy=false,lastChanges=0,expanded=false,signature='',timer=null,inflight=null;
    const stopping=new Set();
    const visible=()=>{const state=getState();return state.paired&&state.selected&&state.session?.session_id===state.selected&&!state.panel.open&&(!state.workspaceView||state.workspaceView==='main')&&!host.hidden;};
    function draw(){
      const state=getState(),grouped=groups(state.items,references);
      const next=JSON.stringify([getLanguage(),session,state.session?.meta?.cwd,data?.totals,data?.base_label,processes,grouped,expanded,changesError,processError,sourcesError,truncated,filesPartial,[...stopping]]);
      if(next===signature)return;signature=next;card.replaceChildren();
      card.setAttribute('aria-label',label('Сводка сессии','Session summary'));
      const path=String(state.session?.meta?.cwd||''),heading=make('strong','summary-project',path.split(/[\\/]/).filter(Boolean).pop()||label('Текущая сессия','Current session'));heading.title=path;card.append(heading);
      const changes=make('button','summary-changes');changes.type='button';changes.append(make('span','','⊞'),make('span','summary-label',label('Изменения','Changes')));
      const totals=data?.totals,counts=make('span','summary-counts');
      if(totals){counts.append(make('span','summary-add','+'+(totals.additions||0)),make('span','summary-delete','−'+(totals.deletions||0)));if(totals.partial)counts.append(make('span','summary-unknown','?'));}
      else counts.textContent=changesError?'—':'…';
      changes.title=changesError||data?.base_label||label('Изменения текущей сессии','Current session changes');changes.append(counts);changes.addEventListener('click',openChanges);card.append(changes);
      const processSection=make('section','summary-section');processSection.append(make('h3','',label('Фоновые процессы','Background processes')));
      if(!processes.length)processSection.append(make('p','summary-empty',processError?label('Не удалось проверить процессы','Unable to check processes'):label('Нет запущенных процессов','No running processes')));
      for(const process of processes){
        const row=make('div','summary-process');row.dataset.processId=process.id;
        const label=make('span','summary-label',process.label||'Команда');label.title=process.label||'';row.append(make('span','summary-process-mark','›_'),label);
        const stop=make('button','summary-stop',stopping.has(process.id)||process.stopping?'…':'■');stop.type='button';stop.disabled=stopping.has(process.id)||process.stopping;stop.title='Остановить процесс';stop.setAttribute('aria-label','Остановить '+(process.label||'процесс'));
        stop.addEventListener('click',async()=>{const id=session;stopping.add(process.id);draw();try{await api('/api/sessions/'+encodeURIComponent(id)+'/activity/'+encodeURIComponent(process.id)+'/stop',{method:'POST',body:'{}'});await refresh(true);}catch(error){if(session===id){processError=error.message;draw();}}finally{if(session===id){stopping.delete(process.id);draw();}}});row.append(stop);processSection.append(row);
      }
      if(processes.length||processError)card.append(processSection);
      const sourcesSection=make('section','summary-section summary-sources'),head=make('div','summary-section-head'),add=make('button','summary-add-source','+');add.type='button';add.title=label('Прикрепить источник к сообщению','Attach a source to the message');add.setAttribute('aria-label',add.title);add.addEventListener('click',attach);head.append(make('h3','',label('Источники','Sources')),add);sourcesSection.append(head);
      for(const [kind,title] of [['pull_request',label('Запросы на включение изменений','Pull requests')],['file',label('Файлы','Files')],['web',label('Веб-источники','Web sources')],['upload',label('Вложения','Uploads')]]){
       const files=grouped[kind];if(!files.length)continue;
       const sourceSection=make('section',kind==='pull_request'?'summary-section':'summary-source-group');sourceSection.dataset.sourceKind=kind;sourceSection.append(make('h3','',title));
       for(const file of expanded?files:files.slice(0,3)){
        const row=make('button','summary-source');row.type='button';row.dataset.sourceId=file.id||file.upload_id;row.title=String(file.path||file.url||file.name||'Файл');
        const icon=make('span','summary-source-icon');
        if(kind==='upload'&&['image/png','image/jpeg','image/webp','image/gif','image/bmp','image/avif'].includes(file.mime_type)){const image=make('img');image.src='/api/sessions/'+encodeURIComponent(session)+'/uploads/'+encodeURIComponent(file.upload_id);image.alt='';image.loading='lazy';icon.append(image);}else icon.textContent=kind==='pull_request'?'⑂':kind==='web'?'↗':'▧';
        row.append(icon,make('span','summary-label',String((kind==='file'&&file.path)||file.name||'Файл')));row.addEventListener('click',()=>openSource(file,session));sourceSection.append(row);
       }
       if(files.length>3){const more=make('button','summary-more',expanded?label('Свернуть','Collapse'):label('Показать все · ','Show all · ')+files.length);more.type='button';more.setAttribute('aria-expanded',String(expanded));more.addEventListener('click',()=>{expanded=!expanded;draw();});sourceSection.append(more);}
       if(kind==='pull_request')card.append(sourceSection);else sourcesSection.append(sourceSection);
      }
      if(sourcesError)sourcesSection.append(make('p','summary-empty',label('Не удалось обновить источники','Unable to refresh sources')));
      else if(!['file','web','upload'].some(kind=>grouped[kind].length))sourcesSection.append(make('p','summary-empty',label('Пока нет источников','No sources yet')));
      if(truncated)sourcesSection.append(make('p','summary-empty',label('Показаны последние 500 ссылок','Showing the latest 500 links')));
      if(filesPartial)sourcesSection.append(make('p','summary-empty',label('Часть файловых изменений недоступна','Some file changes are unavailable')));
      card.append(sourcesSection);
    }
    async function refresh(force=false){
      if(!visible()||document.hidden||busy)return;
      const state=getState(),id=session,version=generation,request=Symbol();busy=true;inflight=request;
      const updateChanges=force||Date.now()-lastChanges>10000;
      const requests=[api('/api/sessions/'+encodeURIComponent(id)+'/activity'),api('/api/sessions/'+encodeURIComponent(id)+'/sources')];
      if(updateChanges)requests.push(api('/api/sessions/'+encodeURIComponent(id)+'/changes/review?mode='+encodeURIComponent(state.panel.review.mode)+'&base='+encodeURIComponent(state.panel.review.base||'')+'&include_untracked='+(state.panel.review.includeUntracked?'1':'0')));
      const results=await Promise.allSettled(requests);
      if(inflight!==request)return;
      busy=false;
      if(session!==id||generation!==version||getState().selected!==id||!getState().paired)return;
      if(results[0].status==='fulfilled'){processes=results[0].value.processes||[];processError='';}else{processes=[];processError=results[0].reason.message;}
      if(results[1].status==='fulfilled'){references=results[1].value.sources||[];truncated=results[1].value.truncated===true;filesPartial=results[1].value.files_partial===true;sourcesError='';}else sourcesError=results[1].reason.message;
      if(updateChanges){lastChanges=Date.now();if(results[2].status==='fulfilled'){data=results[2].value;changesError='';}else{data=null;changesError=results[2].reason.message;}}
      draw();
    }
    function sync(){
      const state=getState(),show=Boolean(visible());card.hidden=!show;host.classList.toggle('summary-visible',show);
      if(!show){clearInterval(timer);timer=null;return;}
      if(session!==state.selected){session=state.selected;generation++;inflight=null;busy=false;data=null;processes=[];references=[];sourcesError='';truncated=false;filesPartial=false;changesError=processError='';lastChanges=0;expanded=false;signature='';stopping.clear();}
      draw();
      if(!timer){refresh();timer=setInterval(()=>refresh(),3000);}
    }
    window.addEventListener('focus',()=>{sync();refresh(true);});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden){sync();refresh(true);}});
    return {sync,refresh,sources:()=>sources(getState().items)};
  }
  return {mount,sources,groups};
});
