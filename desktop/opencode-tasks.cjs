const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const canonicalPath=raw=>process.platform==='win32'?fs.realpathSync.native(raw):fs.realpathSync(raw);

function sessionRoute(job) {
  return '/server/'+Buffer.from(job.service.origin).toString('base64url')+'/session/'+encodeURIComponent(job.sessionID);
}

function request(service, method, route, body) {
  return new Promise((resolve, reject) => {
    if (!service.bridge || service.state !== 'ready') return reject(Error('opencode_service_stopped'));
    const raw = body === undefined ? null : Buffer.from(JSON.stringify(body));
    const req = http.request({...service.transportOptions?.()||{socketPath:path.join(service.bridge,'api.sock')},method,path:route,
      headers:{Authorization:'Basic '+Buffer.from('opencode:'+service.password).toString('base64'),
        'Content-Type':'application/json',...(raw?{'Content-Length':raw.length}:{})}}, res => {
      let size=0;const chunks=[];
      res.on('data',part=>{size+=part.length;if(size>2_000_000)res.destroy(Error('opencode_response_too_large'));else chunks.push(part);});
      res.on('error',reject);
      res.on('end',()=>{
        if(res.statusCode<200||res.statusCode>=300)return reject(Object.assign(Error('opencode_http_'+res.statusCode),{status:res.statusCode}));
        try{resolve(size?JSON.parse(Buffer.concat(chunks).toString('utf8')):null);}catch{reject(Error('opencode_invalid_response'));}
      });
    });
    req.setTimeout(15000,()=>req.destroy(Error('opencode_timeout')));req.on('error',reject);
    req.end(raw);
  });
}

class OpenCodeTasks {
  constructor({manual,root,createService,show,allowed,platform=process.platform}) {
    Object.assign(this,{manual,root,createService,show,allowed,platform});
    this.jobs=new Map();this.starting=false;this.generation=0;
  }
  async handle(fields) {
    if(!this.allowed())return {error:'opencode_access_denied'};
    if(!fields||typeof fields!=='object'||!/^oc_[a-f0-9]{32}$/.test(fields.run_id||''))return {error:'invalid_request'};
    try{
      if(fields.action==='start')return await this.start(fields);
      const job=this.jobs.get(fields.run_id);
      if(!job&&fields.action==='status'&&this.starting===fields.run_id)return {outcome:null,inputs:[],messages:[]};
      if(!job||job.service.state!=='ready')return {error:'opencode_task_lost'};
      if(fields.action==='status')return await this.status(job,fields.seen);
      if(fields.action==='cancel'){
        await request(job.service,'POST',job.prefix+'/interrupt?resume=false');
        job.cancelled=true;return {cancelled:true};
      }
      if(fields.action==='answer')return await this.answer(job,fields);
      return {error:'invalid_request'};
    }catch(error){return {error:/^opencode_[a-z0-9_]+$/.test(error.message)?error.message:'opencode_operation_failed'};}
  }
  async start(fields) {
    if(fields.execution_mode!=='direct'||Object.hasOwn(fields,'workspace'))return {error:'opencode_direct_protocol_required'};
    if(this.jobs.has(fields.run_id))return {session_id:this.jobs.get(fields.run_id).sessionID,execution_mode:'direct'};
    if(this.starting)return {error:'opencode_task_already_running'};
    if(typeof fields.goal!=='string'||!fields.goal.trim()||fields.goal.length>24000||typeof fields.title!=='string'||fields.title.length>120)return {error:'invalid_request'};
    if(!['linux','win32'].includes(this.platform)||!this.manual.status().installed)return {error:'opencode_installation_required'};
    const source=canonicalPath(fields.source);
    if(!fs.statSync(source).isDirectory())return {error:'opencode_project_not_granted'};
    if(!this.manual.config.projects.some(folder=>source===canonicalPath(folder)||source.startsWith(canonicalPath(folder)+path.sep)))return {error:'opencode_project_not_granted'};
    this.starting=fields.run_id;const generation=this.generation;
    let service;
    try{
      for(const [id,prior] of this.jobs){
        const state=await this.status(prior).catch(()=>({outcome:'interrupted'}));
        if(!state.outcome)return {error:'opencode_task_already_running'};
        await prior.service.stop();this.jobs.delete(id);
      }
      service=this.createService(path.join(this.root,fields.run_id),source,fields.run_id);
      await service.addProject(source);
      await service.start();
      if(!this.allowed()||generation!==this.generation)throw Error('opencode_access_denied');
      const selected=service.profilesGrant?.profiles.find(profile=>profile.name===fields.profile);
      if(!selected)throw Error('opencode_profile_not_available');
      const created=await request(service,'POST','/api/session',{
        title:'Поручение: '+fields.title.trim(),location:{directory:source},
        model:{providerID:'igorek-'+selected.name,id:selected.model},metadata:{igorek_run_id:fields.run_id},
      });
      const sessionID=created?.data?.id;
      if(typeof sessionID!=='string'||!/^ses[_a-zA-Z0-9-]+$/.test(sessionID))throw Error('opencode_invalid_session');
      const job={service,sessionID,prefix:'/api/session/'+encodeURIComponent(sessionID),runID:fields.run_id,source,session:fields.session};
      this.jobs.set(fields.run_id,job);
      const messageID='msg_'+fields.run_id.slice(3);
      await request(service,'POST',job.prefix+'/prompt',{id:messageID,text:fields.goal});
      if(!this.allowed()||generation!==this.generation)throw Error('opencode_access_denied');
      await this.show(job).catch(()=>{});
      return {session_id:sessionID,execution_mode:'direct'};
    }catch(error){
      const job=this.jobs.get(fields.run_id);
      if(job&&![400,401,403,404,409].includes(error.status))return {session_id:job.sessionID,execution_mode:'direct',uncertain:true};
      this.jobs.delete(fields.run_id);
      if(service)await service.stop().catch(()=>{});
      throw error;
    }finally{this.starting=false;}
  }
  async status(job,seen=[]) {
    if(job.cancelled)return {outcome:'interrupted',inputs:[],messages:[],result:'OpenCode: поручение остановлено.'};
    const [info,active,forms,permissions,inbox]=await Promise.all([
      request(job.service,'GET',job.prefix),request(job.service,'GET','/api/session/active'),
      request(job.service,'GET',job.prefix+'/form'),request(job.service,'GET',job.prefix+'/permission'),
      request(job.service,'GET',job.prefix+'/inbox'),
    ]);
    const known=new Set(Array.isArray(seen)?seen:[]),messages=[];let bytes=0,more=false,lastText='';
    let route=job.prefix+'/message?limit=100&order=asc';
    for(let page=0;route&&page<100;page++){
      const response=await request(job.service,'GET',route);
      for(const message of response.data||[]){
        if(message.type!=='assistant'||!message.time?.completed)continue;
        const text=(message.content||[]).filter(part=>part.type==='text').map(part=>part.text).join('\n');
        if(text){
          lastText=text.slice(0,24000);
          if(!known.has(message.id)){
            const size=Buffer.byteLength(lastText);
            if(bytes+size<=500000){messages.push({id:message.id,text:lastText});bytes+=size;}else more=true;
          }
        }
      }
      route=response.cursor?.next?job.prefix+'/message?cursor='+encodeURIComponent(response.cursor.next):null;
    }
    const inputs=(forms.data||[]).map(form=>({...form,kind:'form'}));
    for(const permission of permissions.data||[])inputs.push({id:permission.id,kind:'permission',
      title:permission.action,fields:[{key:'decision',type:'string',required:true,title:(permission.message||permission.action)+'\n'+permission.resources.join('\n'),
        options:[{value:'once',label:'Разрешить один раз'},{value:'reject',label:'Отклонить'}]}]});
    const running=Object.hasOwn(active.data||{},job.sessionID)||(inbox.data||[]).length>0;
    return {outcome:running||more?null:info.data?.outcome||null,inputs,messages,result:lastText};
  }
  async answer(job,fields) {
    const id=fields.request_id;
    if(typeof id!=='string'||!/^[a-zA-Z0-9_-]{1,150}$/.test(id))throw Error('opencode_invalid_input');
    const pending=await this.status(job),input=pending.inputs.find(item=>item.id===id);
    if(!this.allowed())throw Error('opencode_access_denied');
    if(!input)return {resolved:true};
    if(input.kind==='permission'){
      const decision=fields.cancelled?'reject':fields.response?.answer?.decision;
      if(!['once','reject'].includes(decision))throw Error('opencode_invalid_answer');
      await request(job.service,'POST',job.prefix+'/permission/'+encodeURIComponent(id)+'/reply',{decision});
    }else{
      try{
        await request(job.service,fields.cancelled?'DELETE':'POST',job.prefix+'/form/'+encodeURIComponent(id)+(fields.cancelled?'':'/reply'),
          fields.cancelled?undefined:{answer:fields.response?.answer});
      }catch(error){if(error.status!==409)throw error;}
    }
    return {resolved:true};
  }
  async stop() {
    this.generation++;
    await Promise.allSettled([...this.jobs.values()].map(job=>job.service.stop()));
    this.jobs.clear();
  }
}
module.exports={OpenCodeTasks,request,sessionRoute};
