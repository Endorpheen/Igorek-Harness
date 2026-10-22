(function(root,factory){
  if(typeof module==='object' && module.exports)module.exports=factory(require('./error-diagnostics.js'));
  else root.IgorekDiagnosticSupport=factory(root.IgorekDiagnosticsContract);
})(typeof globalThis!=='undefined'?globalThis:this,function(D){
  'use strict';
  const MAX_BYTES=512*1024;
  const recipient=D.catalog.support_email;
  const reference=value=>typeof value==='string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(value) && !/^(sk-|gh[pousr]_|github_pat_|eyJ)/.test(value)?value:'';
  const number=value=>Number.isFinite(value)?Math.min(1e15-1,Math.max(0,Math.floor(value))):0;
  const version=value=>typeof value==='string' && /^[0-9][0-9A-Za-z.+-]{0,39}$/.test(value)?value:'unknown';
  const identity=()=>globalThis.crypto?.randomUUID?.().replaceAll('-','') || 'report-'+Date.now();
  function health(value){
    const output={};
    for(const key of ['dropped','write_failures','snapshot_omitted'])if(Number.isFinite(value?.[key]))output[key]=number(value[key]);
    if(['ready','write_failed','history_unavailable'].includes(value?.state))output.state=value.state;
    return output;
  }
  function event(value){
    const result=D.clean(value && typeof value==='object' && !Object.hasOwn(D.catalog.codes,value.code)?{...value,code:'INTERNAL.UNEXPECTED'}:value);
    if(!result)return null;
    for(const [key,item] of Object.entries(result))if(typeof item==='string' && /^(sk-|gh[pousr]_|github_pat_|eyJ)/.test(item))delete result[key];
    return result;
  }
  function snapshot(value){
    if(!value || typeof value!=='object' || Array.isArray(value) || value.available===false || !Array.isArray(value.incidents))return {available:false};
    const values=Array.isArray(value.incidents)?value.incidents:[];
    const incidents=values.slice(-100).map(item=>{
      if(!item || typeof item!=='object')return null;
      const error=event(item.error || item.diagnostic);
      if(!error)return null;
      return {incident_id:reference(item.incident_id || item.id),
        state:['observed','recovering','needs_owner','recovered'].includes(item.state)?item.state:'observed',
        read:item.read===true,observations:number(item.observations ?? item.count) || 1,
        first_seen_ms:number(item.first_seen_ms ?? item.firstSeen),last_seen_ms:number(item.last_seen_ms ?? item.time),
        recovered_at_ms:number(item.recovered_at_ms ?? item.recoveredAt),
        recovery_proof:['stable_ready','operation_completed','operation_confirmed'].includes(item.recovery_proof || item.recoveryProof)?(item.recovery_proof || item.recoveryProof):'',
        error,events:(Array.isArray(item.events)?item.events:[]).slice(-8).map(event).filter(Boolean)};
    }).filter(Boolean).sort((a,b)=>a.last_seen_ms-b.last_seen_ms);
    const activity=(Array.isArray(value.activity)?value.activity:[]).slice(-100).filter(item=>item && typeof item==='object').map(item=>({
      task_id:reference(item.task_id),status:['active','waiting_owner','ok','done','fail','cancelled','unknown'].includes(item.status)?item.status:'unknown',
      phase:['unknown','model','tool','approval','queued'].includes(item.phase)?item.phase:'unknown',
      observed_duration_ms:number(item.observed_duration_ms),since_progress_ms:number(item.since_progress_ms),
      progress_state:['unconfirmed','long_running','observed'].includes(item.progress_state)?item.progress_state:'observed',
    }));
    return {available:true,version:version(value.version),health:health(value.health || value),trace_health:health(value.trace_health),
      omitted_incidents:number(value.omitted_incidents)+Math.max(0,values.length-incidents.length),incidents,activity};
  }
  function createReport(input={},options={}){
    const report={schema_version:1,report_id:reference(input.report_id) || identity(),captured_at_ms:number(input.captured_at_ms) || Date.now(),
      app_version:version(options.version || input.app_version),surface:['web','linux','win32','android','darwin'].includes(options.surface || input.surface)?(options.surface || input.surface):'web',
      local:snapshot(input.local),server:snapshot(input.server),truncated:input.truncated===true};
    while(new TextEncoder().encode(JSON.stringify(report,null,2)).length>MAX_BYTES){
      const group=report.local.incidents?.length>=report.server.incidents?.length?report.local:report.server;
      const selected=group.incidents?.length?group:(report.local.incidents?.length?report.local:report.server);
      if(!selected.incidents?.length)throw new Error('Diagnostic report exceeds its size limit');
      const remove=Math.max(1,Math.ceil(selected.incidents.length/4));
      selected.incidents.splice(0,remove);selected.omitted_incidents+=remove;report.truncated=true;
    }
    return report;
  }
  function fileName(report){return 'igorek-diagnostics-'+reference(report.report_id).replace(/[^A-Za-z0-9_-]/g,'_').slice(0,80)+'.json';}
  function mailto(report,language='ru'){
    if(!/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+$/.test(recipient || ''))throw new Error('Support recipient is unavailable');
    const subject='[Igorek Diagnostics] '+reference(report.report_id);
    const body=language==='en'?'Please describe the problem and attach the saved JSON report.\nReport: '+reference(report.report_id):'Опишите проблему и прикрепите сохранённый JSON-отчёт.\nОтчёт: '+reference(report.report_id);
    return 'mailto:'+recipient+'?subject='+encodeURIComponent(subject)+'&body='+encodeURIComponent(body);
  }
  let collect=null;
  function configure(value){collect=value.collect;}
  async function open(options={}){
    const language=(options.language || document.documentElement.lang)==='en'?'en':'ru';
    const text=(ru,en)=>language==='en'?en:ru;
    const active=document.getElementById('igorek-diagnostic-support');
    if(active?.open){active.focus();return;}
    if(active)active.remove();
    const dialog=document.createElement('dialog');dialog.id='igorek-diagnostic-support';dialog.className='diagnostic-support-dialog';
    const make=(tag,value,cls)=>{const node=document.createElement(tag);node.textContent=value;if(cls)node.className=cls;return node;};
    const title=make('h2',text('Отправить диагностику','Send diagnostics'));title.id='igorek-diagnostic-support-title';dialog.setAttribute('aria-labelledby',title.id);
    const destination=make('p',text('Получатель: ','Recipient: ')+recipient);
    const notice=make('p',text('Проверьте отчёт перед передачей. Он содержит версии, коды ошибок, идентификаторы, профили и модели. Переписка, ключи и аргументы инструментов не включаются.','Review the report before sharing. It includes versions, error codes, identifiers, profiles and models. Conversation text, credentials and tool arguments are excluded.'));
    const instructions=make('p',text('Отчёт сохранится файлом. В открывшемся письме прикрепите этот файл и опишите проблему. Отправку подтверждаете вы в своей почте.','Save the report, attach it to the email and describe the problem. You confirm sending in your own email app.'));
    const preview=make('pre',text('Подготовка отчёта…','Preparing report…'));preview.className='diagnostic-support-preview';preview.tabIndex=0;preview.setAttribute('aria-label',text('Содержимое отчёта','Report contents'));
    const status=make('p','');status.setAttribute('role','status');status.id='diagnostic-support-status';
    const actions=make('div','','diagnostic-support-actions');
    const save=make('button',text('Сохранить и открыть почту','Save and open email'),'primary-button');save.type='button';save.id='diagnostic-support-save';save.disabled=true;
    const email=make('a',text('Открыть почту','Open email'),'secondary-button');email.id='diagnostic-support-mail';email.hidden=true;
    const cancel=make('button',text('Отмена','Cancel'),'secondary-button');cancel.type='button';cancel.id='diagnostic-support-cancel';cancel.addEventListener('click',()=>dialog.close());
    actions.append(save,email,cancel);dialog.append(title,destination,notice,instructions,preview,status,actions);document.body.append(dialog);
    dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();cancel.focus();
    let report;
    try{
      report=createReport(options.report || (collect?await collect():{}),{surface:options.surface,version:options.version});
      if(!dialog.isConnected)return;
      dialog.dataset.reportId=report.report_id;preview.textContent=JSON.stringify(report,null,2);save.disabled=false;
      email.href=mailto(report,language);
    }catch{preview.textContent=text('Не удалось подготовить отчёт. Ничего не отправлено.','Could not prepare the report. Nothing was sent.');return;}
    save.addEventListener('click',async()=>{
      save.disabled=true;status.textContent='';
      try{
        if(window.IgorekNotifications?.support){
          const result=await window.IgorekNotifications.support({action:'save',report,language});
          if(!dialog.isConnected)return;
          if(result.state==='cancelled'){save.disabled=false;return;}
          if(result.state==='name_conflict'){status.textContent=text('Выберите новое имя: существующий файл не перезаписывается.','Choose a new filename: existing files are not overwritten.');save.disabled=false;return;}
          status.textContent=result.state==='composer_opened'?text('Файл сохранён. Прикрепите его к письму; отправку подтвердите в почте.','File saved. Attach it and confirm sending in your email app.'):
            text('Файл сохранён, но почту открыть не удалось. Отправьте файл вручную на ','File saved, but the email app could not be opened. Send it manually to ')+recipient;
          cancel.textContent=text('Закрыть','Close');
        }else{
          const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
          const link=document.createElement('a');link.href=url;link.download=fileName(report);document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
          cancel.textContent=text('Закрыть','Close');email.hidden=false;status.textContent=text('Сохранение файла запрошено. Откройте почту и прикрепите сохранённый файл.','File download requested. Open your email app and attach the saved file.');
        }
      }catch{if(dialog.isConnected){status.textContent=text('Не удалось сохранить отчёт. Ничего не отправлено.','Could not save the report. Nothing was sent.');save.disabled=false;}}
    });
  }
  return {MAX_BYTES,recipient,createReport,fileName,mailto,configure,open};
});
