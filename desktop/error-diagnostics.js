/* Shared browser/desktop diagnostics. Generated desktop copy is verified in tests. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./error-catalog.json'));
  else root.IgorekDiagnosticsContract = factory(root.IgorekErrorCatalog);
})(globalThis, function (catalog) {
  const codes = catalog.codes;
  const id = value => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(value) ? value : '';
  function classify({native_code = '', status, phase = 'unknown'} = {}) {
    const native = {EAI_AGAIN:'NET.DNS_FAILURE', ENOTFOUND:'NET.DNS_FAILURE', ECONNREFUSED:'NET.CONNECTION_REFUSED',
      ECONNRESET:'NET.CONNECTION_RESET', ECONNABORTED:'NET.CONNECTION_RESET', EPIPE:'NET.CONNECTION_RESET',
      ENETUNREACH:'NET.OFFLINE', ENETDOWN:'NET.OFFLINE', ENOSPC:'STORAGE.FULL',
      EDQUOT:'STORAGE.QUOTA_EXCEEDED', EROFS:'STORAGE.READ_ONLY', EIO:'STORAGE.IO_ERROR',
      SQLITE_FULL:'STORAGE.FULL', SQLITE_READONLY:'STORAGE.READ_ONLY', SQLITE_IOERR:'STORAGE.IO_ERROR',
      SQLITE_BUSY:'DATABASE.BUSY', SQLITE_LOCKED:'DATABASE.BUSY', SQLITE_CORRUPT:'DATABASE.INVALID', SQLITE_NOTADB:'DATABASE.INVALID',
      CERTIFICATE_REJECTED:'TLS.CERTIFICATE_REJECTED', INVALID_RESPONSE:'PROTOCOL.INVALID_RESPONSE', CANCELLED:'OPERATION.CANCELLED',
      TLS_EOF:'TLS.CONNECTION_INTERRUPTED', TRANSPORT_UNKNOWN:'NET.TRANSPORT_FAILURE'};
    if (native[native_code]) return native[native_code];
    if (['TIMEOUT','ETIMEDOUT','ESOCKETTIMEDOUT'].includes(native_code)) return phase === 'connect' ? 'NET.CONNECT_TIMEOUT' : 'NET.READ_TIMEOUT';
    if ([401,403].includes(status)) return 'AUTH.REJECTED';
    if (status === 429) return 'HTTP.RATE_LIMITED';
    if ([408,504].includes(status)) return 'NET.READ_TIMEOUT';
    if ([500,502,503,529].includes(status)) return 'HTTP.SERVICE_UNAVAILABLE';
    if (status >= 400) return 'HTTP.REQUEST_REJECTED';
    return 'INTERNAL.UNEXPECTED';
  }
  const fields = ['component','operation','endpoint_role','owner','phase','delivery','outcome','event_id','http_request_id',
    'client_operation_id','provider_request_id','task_id','session_id','tool_use_id','profile','model','build','native_code','reason_code','tool_name','tool_action','service'];
  function reasonDetail(value) {
    if(typeof value !== 'string')return '';
    return value.slice(0,16000)
      .replace(/\bBearer\s+[^\s"'<>]+/gi,'Bearer [REDACTED]')
      .replace(/(["']?(?:access_token|refresh_token|id_token|api[_-]?key|authorization|cookie|password|token|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi,'$1[REDACTED]')
      .replace(/\b(?:sk[-_][\w-]+|oc_sk_[\w-]+|syt_[\w-]+|gh[pousr]_[\w]+|github_pat_[\w]+|eyJ[\w-]+\.[\w-]+\.[\w-]+)\b/g,'[REDACTED]')
      .replace(/https?:\/\/[^\s<>"']+/g,text=>{try{const url=new URL(text);return url.origin+url.pathname;}catch{return '[URL]';}}).slice(0,512);
  }
  function clean(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const code = Object.hasOwn(codes,value.code) ? value.code : 'INTERNAL.UNEXPECTED';
    const definition = codes[code];
    const result = {schema_version:1,code,category:definition.category,severity:definition.severity,action:definition.action};
    for (const key of fields) {
      if (['profile','model'].includes(key) && typeof value[key] === 'string' && /^[A-Za-z0-9_./:-]{1,160}$/.test(value[key]) && !value[key].includes('://')) result[key]=value[key];
      else if (id(value[key]) && !(key==='reason_code' && /^(sk[-_]|syt_|oc_sk_|gh[pousr]_|github_pat_|eyJ)/.test(value[key]))) result[key] = value[key];
    }
    for (const key of ['observed_at_ms','http_status','attempt','next_retry_at_ms','retry_after_s'])
      if (Number.isFinite(value[key]) && value[key] >= 0 && value[key] < 1e15) result[key] = value[key];
    if (value.reason_detail) result.reason_detail=reasonDetail(value.reason_detail);
    return result;
  }
  function observation(code, details = {}) {
    return clean({code,event_id:globalThis.crypto?.randomUUID?.().replaceAll('-','') || String(Date.now()),
      observed_at_ms:Date.now(),delivery:'unknown',outcome:['write','create','send'].includes(details.operation) && (!details.delivery || ['unknown','accepted'].includes(details.delivery)) ? 'unknown' : 'failed',...details});
  }
  function nativeError(error) {
    let current = error;
    for (let depth=0; current && depth<5; depth++,current=current.cause) {
      if (current.name === 'AbortError') return 'CANCELLED';
      if (current.name === 'TimeoutError') return 'TIMEOUT';
      if (current.code && typeof current.code === 'string') return current.code;
    }
    // Browser fetch does not expose DNS/TLS causes; preserve that uncertainty.
    return error instanceof TypeError ? 'TRANSPORT_UNKNOWN' : '';
  }
  const fingerprint = e => [e.owner,e.endpoint_role,e.component,e.code,e.outcome === 'unknown' ? e.operation : 'read'].map(x=>x || '').join('|');
  function incident(items, error, now = Date.now()) {
    error = clean(error);
    if (!error) return null;
    const key = fingerprint(error);
    const state=error.outcome==='unknown' || !codes[error.code].retryable ? 'needs_owner' : error.next_retry_at_ms ? 'recovering' : 'observed';
    const previous = items.find(item => item.diagnostic && fingerprint(item.diagnostic) === key && item.state !== 'recovered');
    if (previous) {
      if(!(previous.events || [previous.diagnostic]).some(event=>event.event_id===error.event_id)) {
        previous.count = Math.min(1e9,(previous.count || 1)+1);
        previous.events=[...(previous.events || []),error].slice(-8);
      }
      previous.time = now; previous.diagnostic = error; previous.state=state;
      return previous;
    }
    return {id:globalThis.crypto?.randomUUID?.() || String(now),time:now,firstSeen:now,count:1,read:false,state,diagnostic:error,events:[error]};
  }
  function recover(items, owner, stableMs, proof='stable_ready') {
    if (proof !== 'operation_completed' && stableMs < catalog.stable_after_ms) return false;
    let changed=false;
    for (const item of items) if (item.diagnostic?.owner === owner && item.state !== 'recovered' && item.diagnostic.outcome !== 'unknown') {
      item.state='recovered'; item.recoveredAt=Date.now(); item.recoveryProof=proof; changed=true;
    }
    return changed;
  }
  function message(error, language='ru') {
    const value=clean(error) || {code:'INTERNAL.UNEXPECTED'},lang=language==='en'?'en':'ru';
    const context=[value.tool_name,value.service,value.tool_action].filter(Boolean).join(' → ');
    const reason=value.reason_detail || value.reason_code || '';
    const host = value.component === 'bridge' && ['client_to_bridge','runtime_to_tool'].includes(value.endpoint_role) &&
      codes[value.code].category === 'storage' ? (lang === 'ru' ? 'На компьютере с Игорьком: ' : 'On the computer running Igorek: ') : '';
    return [context,host+codes[value.code][lang],reason].filter(Boolean).join('\n');
  }
  function retryDelay(attempt, random=Math.random()) { return Math.ceil(Math.min(10000,500*2**Math.min(Math.max(attempt-1,0),10))*(0.5+0.5*random)); }
  function confirm(items, operationId, taskId='') {
    let changed=false;
    for(const item of items) if(item.diagnostic?.client_operation_id===operationId && item.diagnostic.outcome==='unknown') {
      item.diagnostic={...item.diagnostic,delivery:'accepted',outcome:'accepted',...(id(taskId)?{task_id:taskId}:{})};
      item.state='recovered';item.recoveredAt=Date.now();item.recoveryProof='operation_confirmed';changed=true;
    }
    return changed;
  }
  function scope(value) { let h=2166136261; for (const c of String(value)) h=Math.imul(h^c.charCodeAt(0),16777619); return (h>>>0).toString(16); }
  function createLocalJournal(storage) {
    let items=[];
    const health={state:'ready',write_failures:0,dropped:0};
    try {
      const text=storage.getItem('igorek.diagnostics.v1') || '[]';
      if(text.length>500000)throw new Error('History limit');
      const values=JSON.parse(text);
      if(!Array.isArray(values))throw new Error('History shape');
      items=values.slice(0,100).filter(item=>item && id(item.id) && clean(item.diagnostic)).map(item=>({
        id:item.id,time:Number(item.time)||0,firstSeen:Number(item.firstSeen)||0,count:Math.min(1e9,Math.max(1,Number(item.count)||1)),
        read:item.read===true,recoveredAt:Number(item.recoveredAt)||0,recoveryProof:['operation_completed','stable_ready','operation_confirmed'].includes(item.recoveryProof)?item.recoveryProof:'',state:['observed','recovering','needs_owner','recovered'].includes(item.state)?item.state:'observed',
        diagnostic:clean(item.diagnostic),events:(Array.isArray(item.events)?item.events:[]).slice(-8).map(clean).filter(Boolean)}));
    }catch{health.state='history_unavailable'}
    function save() {
      try {
        while(JSON.stringify(items).length>500000 && items.length>1){items.pop();health.dropped++}
        storage.setItem('igorek.diagnostics.v1',JSON.stringify(items));health.state='ready';
      }catch{health.state='write_failed';health.write_failures++}
    }
    return {
      observe(error){const item=incident(items,error);if(!item)return;items=[item,...items.filter(x=>x.id!==item.id)];if(items.length>100)health.dropped+=items.length-100;items=items.slice(0,100);save()},
      recover(value){if(recover(items,value.owner,value.stable_ms||0,value.proof))save()},
      confirm(value){if(confirm(items,value.client_operation_id,value.task_id))save()},
      snapshot(){return {schema_version:1,captured_at_ms:Date.now(),health:{...health},incidents:JSON.parse(JSON.stringify(items))}},
    };
  }
  return {catalog, classify, clean, observation, nativeError, incident, recover, confirm, message, retryDelay, scope, createLocalJournal};
});
