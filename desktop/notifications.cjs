const { readFileSync, mkdirSync, writeFileSync, renameSync, chmodSync, statSync } = require('node:fs');
const { dirname } = require('node:path');
const { randomUUID } = require('node:crypto');
const diagnosticContract = require('./error-diagnostics.js');

function safeText(value, limit = 8000) {
  return String(value ?? '').slice(0, 16000)
    .replace(/\bBearer\s+[^\s"'<>]+/gi, 'Bearer [скрыто]')
    .replace(/\b(?:sk-[\w-]+|eyJ[\w-]+\.[\w-]+\.[\w-]+)\b/g, '[скрыто]')
    .replace(/(["']?(?:access_token|refresh_token|id_token|api[_-]?key|authorization|cookie|password|пароль|token|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi, '$1[скрыто]')
    .replace(/https?:\/\/[^\s<>"']+/gi, raw => {
      try { const url = new URL(raw); url.username = ''; url.password = ''; url.search = ''; url.hash = ''; return url.href; }
      catch { return '[ссылка скрыта]'; }
    }).slice(0, limit);
}

class NotificationStore {
  constructor(path) {
    this.path = path; this.items = []; this.health = {state:'ready',write_failures:0,dropped:0};
    try {
      if(statSync(path).size>524288)throw new Error('Diagnostic history too large');
      const values = JSON.parse(readFileSync(path, 'utf8'));
      if (!Array.isArray(values)) throw new Error('Invalid diagnostic history');
      this.items = values.slice(0, 100).filter(item => item && typeof item.id === 'string')
        .map(item => ({ id: item.id, source: safeText(item.source, 160), detail: safeText(item.detail),
          level: item.level === 'error' ? 'error' : 'info', time: Number(item.time) || 0,
          read: item.read === true, count: Math.min(1e9,Math.max(1, Number(item.count) || 1)),
          ...(item.diagnostic ? {diagnostic:diagnosticContract.clean(item.diagnostic),events:(Array.isArray(item.events)?item.events:[]).slice(-8).map(diagnosticContract.clean).filter(Boolean),state:['observed','recovering','needs_owner','recovered'].includes(item.state) ? item.state : 'observed',recoveryProof:['operation_confirmed','operation_completed','stable_ready'].includes(item.recoveryProof)?item.recoveryProof:'',firstSeen:Number(item.firstSeen)||Number(item.time)||0,recoveredAt:Number(item.recoveredAt)||0} : {}) }));
    } catch(error) { if(error.code!=='ENOENT')this.health.state='history_unavailable'; }
  }
  snapshot() { return JSON.parse(JSON.stringify(this.items)); }
  save() {
    try {
    while(Buffer.byteLength(JSON.stringify(this.items))>500000 && this.items.length>1){this.items.pop();this.health.dropped++;}
    mkdirSync(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = this.path + '.tmp';
    writeFileSync(temporary, JSON.stringify(this.items), { mode: 0o600 });
    chmodSync(temporary, 0o600); renameSync(temporary, this.path);
    this.health.state='ready';
    } catch { this.health.state='write_failed'; this.health.write_failures++; }
    return this.snapshot();
  }
  add(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || typeof value.detail !== 'string' || value.detail.length > 16000
        || (value.level !== 'error' && value.level !== 'info')) throw new Error('Некорректное уведомление.');
    const detail = safeText(value.detail), source = safeText(value.source || 'Приложение', 160), time = Date.now();
    if (!detail.trim()) return this.snapshot();
    if (value.diagnostic) {
      const item = diagnosticContract.incident(this.items, value.diagnostic, time);
      if (!item) return this.snapshot();
      Object.assign(item,{source,level:diagnosticContract.catalog.codes[item.diagnostic.code].severity==='info'?'info':value.level,detail});
      this.items=[item,...this.items.filter(other=>other.id!==item.id)];
      if(this.items.length>100)this.health.dropped+=this.items.length-100;
      this.items=this.items.slice(0,100);
      return this.save();
    }
    const previous = this.items.find(item => item.detail === detail && item.source === source && item.level === value.level && time - item.time < 120000);
    if (previous) {
      previous.count += 1; previous.time = time; previous.read = false;
      this.items = [previous, ...this.items.filter(item => item !== previous)];
    } else this.items.unshift({ id: randomUUID(), source, detail, level: value.level, time, read: false, count: 1 });
    this.items.length = Math.min(100, this.items.length);
    return this.save();
  }
  read() { for (const item of this.items) item.read = true; return this.save(); }
  clear() { this.items = []; return this.save(); }
  recover(value) {
    if (!value || typeof value.owner !== 'string' || !/^[A-Za-z0-9_.:-]{1,160}$/.test(value.owner)) throw new Error('Invalid recovery owner');
    if (diagnosticContract.recover(this.items,value.owner,Number(value.stable_ms)||0,value.proof === 'operation_completed' ? value.proof : 'stable_ready')) return this.save();
    return this.snapshot();
  }
  confirm(value) {
    if(!value || typeof value.client_operation_id!=='string' || !/^[A-Za-z0-9_-]{8,160}$/.test(value.client_operation_id))throw new Error('Invalid operation reference');
    if(diagnosticContract.confirm(this.items,value.client_operation_id,value.task_id)) {
      for(const item of this.items)if(item.diagnostic?.client_operation_id===value.client_operation_id && item.recoveryProof==='operation_confirmed')item.detail='Сервер подтвердил принятие действия. Повторная отправка не выполнялась.';
      return this.save();
    }
    return this.snapshot();
  }
  report() {
    return {schema_version:1,captured_at_ms:Date.now(),health:{...this.health},incidents:this.items.filter(item=>item.diagnostic).map(item=>({incident_id:item.id,state:item.state,first_seen_ms:item.firstSeen,last_seen_ms:item.time,observations:item.count,read:item.read,recovered_at_ms:item.recoveredAt||null,recovery_proof:item.recoveryProof||null,error:diagnosticContract.clean(item.diagnostic),events:(item.events||[]).slice(-8).map(diagnosticContract.clean),history_omitted:Math.max(0,item.count-(item.events||[]).length)}))};
  }
}
module.exports = { NotificationStore, safeText };
