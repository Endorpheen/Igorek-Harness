const fs=require('node:fs');
const path=require('node:path');
const {execFile}=require('node:child_process');
const {randomUUID}=require('node:crypto');
const ERRORS={github_auth_required:'Войди в GitHub через gh auth login.',github_missing:'Установи GitHub CLI, затем выполни gh auth login.',github_forbidden:'GitHub не разрешил доступ. Проверь права аккаунта и ограничения API.',github_not_found:'PR не найден или недоступен этому аккаунту.',github_rate_limited:'Достигнут лимит GitHub. Повтори позже.',github_timeout:'GitHub не ответил вовремя. Повтори запрос.',github_unavailable:'Не удалось получить ответ GitHub.',github_invalid_response:'GitHub вернул неподдерживаемый ответ.',github_locked:'Разблокируй Игорька.',github_account_changed:'Аккаунт GitHub изменился. Обнови раздел.',github_head_changed:'PR изменился. Обнови изменения перед слиянием.',github_merge_blocked:'Этот PR сейчас нельзя слить.',github_state_invalid:'Не удалось прочитать локальные настройки PR.',github_invalid_request:'Некорректный запрос PR.'};
class ReviewError extends Error{constructor(code){super(ERRORS[code]||ERRORS.github_unavailable);this.code=code;}}
function parsePR(value){
  try{const u=new URL(value);if(u.protocol!=='https:'||u.hostname!=='github.com'||u.port||u.username||u.password)throw Error();
    const m=u.pathname.match(/^\/([A-Za-z0-9][A-Za-z0-9-]{0,38})\/([A-Za-z0-9_.-]{1,100})\/pull\/([1-9][0-9]{0,9})(?:\/(?:files|commits))?\/?$/);
    if(!m||['.','..'].includes(m[2]))throw Error();const [,owner,repo,n]=m;return {owner,repo,number:Number(n),url:`https://github.com/${owner}/${repo}/pull/${n}`,endpoint:`repos/${owner}/${repo}/pulls/${n}`};
  }catch{throw new ReviewError('github_invalid_request');}
}
function bounded(value,maximum=32000){return typeof value==='string'?value.slice(0,maximum):'';}
function safeLink(value){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password?u.href:'';}catch{return '';}}
function account(value){if(!value||!Number.isSafeInteger(value.id)||value.id<=0||!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/.test(value.login||''))throw new ReviewError('github_invalid_response');return {id:String(value.id),login:value.login};}
function issue(value){
 const pr=parsePR(value.html_url);return {...pr,title:bounded(value.title,500),author:bounded(value.user?.login,40),state:(value.merged_at||value.pull_request?.merged_at)?'merged':value.state==='closed'?'closed':'open',draft:value.draft===true,updated:bounded(value.updated_at,40)};
}
function timelineEvent(value,index){
 const event=bounded(value.event,80)||'unknown';
 return {id:bounded(String(value.id||value.node_id||value.commit_id||event+':'+index),150),event,author:bounded(value.actor?.login||value.user?.login||value.author?.name,100),at:bounded(value.created_at||value.submitted_at||value.author?.date,40),body:bounded(value.body,16000),truncated:typeof value.body==='string'&&value.body.length>16000,state:bounded(value.state,40),commit:bounded(value.commit_id||value.sha,64),message:bounded(value.message,1000),url:safeLink(value.html_url),label:bounded(value.label?.name,100),reviewer:bounded(value.requested_reviewer?.login,40),title:bounded(value.source?.issue?.title,500)};
}
function classify(error,stdout='',stderr=''){
 if(error?.code==='ENOENT')return 'github_missing';if(error?.code==='ERR_CHILD_PROCESS_STDIO_MAXBUFFER')return 'github_invalid_response';if(error?.killed||error?.code==='ETIMEDOUT')return 'github_timeout';
 const status=Number(stderr.match(/\(HTTP (\d{3})\)/)?.[1]);
 return error?.code===4||status===401?'github_auth_required':status===403?'github_forbidden':status===404?'github_not_found':status===429?'github_rate_limited':'github_unavailable';
}
function cliEnvironment(){const result={};for(const key of ['PATH','HOME','USER','LOGNAME','XDG_CONFIG_HOME','GH_CONFIG_DIR','DBUS_SESSION_BUS_ADDRESS','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR','GH_TOKEN','GITHUB_TOKEN'])if(process.env[key])result[key]=process.env[key];return {...result,GH_PROMPT_DISABLED:'1',GH_PAGER:'cat',NO_COLOR:'1'};}
class GitHubReviews{
 constructor({root,authorized,runner=null,now=Date.now}){this.root=root;this.authorized=authorized;this.runner=runner;this.now=now;this.generation=0;this.children=new Set();this.mergeTickets=new Map();this.active=0;}
 lock(){this.generation++;this.mergeTickets.clear();for(const child of this.children)child.kill();this.children.clear();}
 async command(args,input){
  if(this.runner)return this.runner(args,input);
  return new Promise((resolve,reject)=>{const child=execFile('gh',args,{env:cliEnvironment(),timeout:15000,maxBuffer:16*1024*1024,windowsHide:true},(error,stdout,stderr)=>{this.children.delete(child);if(error)reject(new ReviewError(classify(error,stdout,stderr)));else resolve(stdout);});this.children.add(child);child.stdin.end(input||'');});
 }
 async api(endpoint,body=null){const args=['api','--hostname','github.com','--method',body?'PUT':'GET',endpoint,'--header','Accept: application/vnd.github+json','--header','X-GitHub-Api-Version: 2022-11-28'];if(body)args.push('--input','-');const raw=await this.command(args,body?JSON.stringify(body):null);try{return JSON.parse(raw);}catch{throw new ReviewError('github_invalid_response');}}
 async guarded(work,commit=null){
  if(this.active>=4)throw new ReviewError('github_unavailable');this.active++;
  const generation=this.generation;
  const valid=async()=>{if(generation!==this.generation||!(await this.authorized())||generation!==this.generation)throw new ReviewError('github_locked');};
  try{await valid();const user=account(await this.api('user'));await valid();const result=await work(user,generation);await valid();const after=account(await this.api('user'));await valid();if(after.id!==user.id){this.lock();throw new ReviewError('github_account_changed');}if(commit)commit(user,result);return result;}finally{this.active--;}
 }
 local(){
  const file=path.join(this.root,'state.json');try{const stat=fs.lstatSync(file);if(stat.isSymbolicLink()||stat.size>1024*1024)throw Error();const value=JSON.parse(fs.readFileSync(file,'utf8'));if(value.version!==1||!value.accounts||typeof value.accounts!=='object'||Array.isArray(value.accounts))throw Error();return value;}catch(error){if(error.code==='ENOENT')return {version:1,accounts:{}};throw new ReviewError('github_state_invalid');}
 }
 preferences(user){const value=this.local().accounts[user.id]||{};return {pins:Array.isArray(value.pins)?value.pins.filter(item=>{try{parsePR(item.url);return typeof item.title==='string';}catch{return false;}}).slice(0,100):[],sessions:value.sessions&&typeof value.sessions==='object'&&!Array.isArray(value.sessions)?value.sessions:{}};}
 save(user,value){const data=this.local();data.accounts[user.id]=value;fs.mkdirSync(this.root,{recursive:true,mode:0o700});const file=path.join(this.root,'state.json'),temporary=path.join(this.root,'.state-'+randomUUID());try{fs.writeFileSync(temporary,JSON.stringify(data),{mode:0o600,flag:'wx'});fs.renameSync(temporary,file);}finally{try{fs.unlinkSync(temporary);}catch{}}}
 identity(){return this.guarded(async user=>({account:user}));}
 status(){return this.guarded(async user=>{let teams=[],team_error='';try{const data=await this.api('user/teams?per_page=100');if(!Array.isArray(data))throw new ReviewError('github_invalid_response');teams=data.filter(t=>t.organization?.login&&t.slug).map(t=>({id:t.organization.login+'/'+t.slug,name:bounded(t.name,100)}));}catch(error){team_error=error.code||'github_unavailable';}return {account:user,teams,team_error,...this.preferences(user)};});}
 list({filter='mine',query='',state='open',page=1,team=''}){
  if(!['mine','requested','team','all','pinned'].includes(filter)||!['open','closed','all'].includes(state)||typeof query!=='string'||query.length>200||!Number.isInteger(page)||page<1||page>10||typeof team!=='string'||team.length>140)throw new ReviewError('github_invalid_request');
  return this.guarded(async user=>{
   if(filter==='pinned')return {account:user,items:this.preferences(user).pins,total:this.preferences(user).pins.length,more:false};
   const terms=['is:pr',state!=='all'?'is:'+state:'',filter==='mine'?'author:'+user.login:filter==='requested'?'review-requested:'+user.login:filter==='all'?'involves:'+user.login:''];
   if(filter==='team'){if(!/^[A-Za-z0-9-]+\/[A-Za-z0-9_-]+$/.test(team))throw new ReviewError('github_invalid_request');terms.push('team-review-requested:'+team);}
   if(query.trim())terms.push('"'+query.replace(/["\\]/g,' ').trim()+'"');
   const q=new URLSearchParams({q:terms.filter(Boolean).join(' '),sort:'updated',order:'desc',per_page:'30',page:String(page)});
   const data=await this.api('search/issues?'+q);if(!data||!Array.isArray(data.items)||!Number.isInteger(data.total_count))throw new ReviewError('github_invalid_response');
   return {account:user,items:data.items.filter(item=>item.pull_request).map(issue),total:data.total_count,more:page*30<Math.min(1000,data.total_count),incomplete:data.incomplete_results===true};
  });
 }
 detail(url){const ref=parsePR(url);return this.guarded(async user=>{
  const raw=await this.api(ref.endpoint);if(raw.number!==ref.number||!/^([a-f0-9]{40}|[a-f0-9]{64})$/.test(raw.head?.sha||''))throw new ReviewError('github_invalid_response');
  const base=`repos/${ref.owner}/${ref.repo}`,sha=raw.head.sha;
  const endpoints=[`${base}/issues/${ref.number}/comments?per_page=100`,`${ref.endpoint}/reviews?per_page=100`,`${base}/commits/${sha}/check-runs?per_page=100`,`${base}/commits/${sha}/status?per_page=100`];
  const results=await Promise.all(endpoints.map(async endpoint=>{try{return {data:await this.api(endpoint)};}catch(error){return {error:error.code||'github_unavailable'};}}));
  for(const [index,shape] of [Array.isArray,Array.isArray,v=>Array.isArray(v?.check_runs),v=>Array.isArray(v?.statuses)].entries())if(!results[index].error&&!shape(results[index].data))results[index]={error:'github_invalid_response'};
  const comments=results[0],reviews=results[1],checks=results[2],statuses=results[3];
  const response={...issue(raw),created:bounded(raw.created_at,40),comments_count:Number.isInteger(raw.comments)?raw.comments:0,body:bounded(raw.body),body_truncated:typeof raw.body==='string'&&raw.body.length>32000,head:{sha,ref:bounded(raw.head.ref,250),repo:bounded(raw.head.repo?.full_name,150)},base:{sha:bounded(raw.base?.sha,64),ref:bounded(raw.base?.ref,250)},additions:raw.additions,deletions:raw.deletions,changed_files:raw.changed_files,mergeable:typeof raw.mergeable==='boolean'?raw.mergeable:null,merge_state:bounded(raw.mergeable_state,50),account:user,session_id:this.preferences(user).sessions[ref.url]||'',pinned:this.preferences(user).pins.some(item=>item.url===ref.url),
   comments:{error:comments.error||'',more:comments.data?.length===100,items:Array.isArray(comments.data)?comments.data.map(item=>({author:bounded(item.user?.login,40),body:bounded(item.body,8000),url:safeLink(item.html_url),at:bounded(item.created_at,40)})):[]},
   reviews:{error:reviews.error||'',more:reviews.data?.length===100,items:Array.isArray(reviews.data)?reviews.data.map(item=>({author:bounded(item.user?.login,40),state:bounded(item.state,40),body:bounded(item.body,8000),url:safeLink(item.html_url),at:bounded(item.submitted_at,40)})):[]},
   checks:{error:checks.error||'',more:checks.data?.total_count>100,items:Array.isArray(checks.data?.check_runs)?checks.data.check_runs.map(item=>({name:bounded(item.name,250),status:bounded(item.status,40),conclusion:bounded(item.conclusion,40),url:safeLink(item.details_url)})):[]},
   statuses:{error:statuses.error||'',state:bounded(statuses.data?.state,30),more:statuses.data?.total_count>100,items:Array.isArray(statuses.data?.statuses)?statuses.data.statuses.map(item=>({name:bounded(item.context,250),state:bounded(item.state,40),url:safeLink(item.target_url)})):[]}};
  return response;
 });}
 files({url,page=1,sha}){const ref=parsePR(url);if(!Number.isInteger(page)||page<1||page>30||!/^([a-f0-9]{40}|[a-f0-9]{64})$/.test(sha||''))throw new ReviewError('github_invalid_request');return this.guarded(async()=>{const current=await this.api(ref.endpoint);if(current.head?.sha!==sha)throw new ReviewError('github_head_changed');const rows=await this.api(ref.endpoint+'/files?per_page=100&page='+page);const after=await this.api(ref.endpoint);if(after.head?.sha!==sha)throw new ReviewError('github_head_changed');if(!Array.isArray(rows))throw new ReviewError('github_invalid_response');return {sha,items:rows.map(item=>({path:bounded(item.filename,4096),previous:bounded(item.previous_filename,4096),status:bounded(item.status,30),additions:item.additions,deletions:item.deletions,patch:typeof item.patch==='string'?bounded(item.patch,128000):null,truncated:typeof item.patch==='string'&&item.patch.length>128000})),more:rows.length===100&&page<30,limited:rows.length===100&&page===30};});}
 timeline({url,page=1}){const ref=parsePR(url);if(!Number.isInteger(page)||page<1||page>100)throw new ReviewError('github_invalid_request');return this.guarded(async user=>{const data=await this.api(`repos/${ref.owner}/${ref.repo}/issues/${ref.number}/timeline?per_page=100&page=${page}`);if(!Array.isArray(data))throw new ReviewError('github_invalid_response');return {account:user,items:data.map(timelineEvent),more:data.length===100&&page<100,limited:data.length===100&&page===100};});}
 pin({url,title,pinned,account_id}){const ref=parsePR(url);if(typeof title!=='string'||title.length>500||typeof pinned!=='boolean')throw new ReviewError('github_invalid_request');return this.guarded(async user=>{if(account_id!==user.id)throw new ReviewError('github_account_changed');const preferences=this.preferences(user);preferences.pins=preferences.pins.filter(item=>item.url!==ref.url);if(pinned)preferences.pins.unshift({...ref,title});preferences.pins=preferences.pins.slice(0,100);return {pinned,preferences};},(user,result)=>{this.save(user,result.preferences);delete result.preferences;});}
 bind({url,session_id,account_id}){const ref=parsePR(url);if(typeof session_id!=='string'||!/^[-a-zA-Z0-9_]{1,128}$/.test(session_id))throw new ReviewError('github_invalid_request');return this.guarded(async user=>{if(account_id!==user.id)throw new ReviewError('github_account_changed');const preferences=this.preferences(user);preferences.sessions[ref.url]=session_id;preferences.sessions=Object.fromEntries(Object.entries(preferences.sessions).slice(-100));return {session_id,preferences};},(user,result)=>{this.save(user,result.preferences);delete result.preferences;});}
 prepareMerge({url,sha,method,account_id}){const ref=parsePR(url);if(!/^([a-f0-9]{40}|[a-f0-9]{64})$/.test(sha||'')||!['merge','squash','rebase'].includes(method))throw new ReviewError('github_invalid_request');return this.guarded(async(user,generation)=>{if(account_id!==user.id)throw new ReviewError('github_account_changed');const pr=await this.api(ref.endpoint);if(pr.head?.sha!==sha)throw new ReviewError('github_head_changed');if(pr.state!=='open'||pr.draft||pr.mergeable!==true)throw new ReviewError('github_merge_blocked');const ticket=randomUUID();this.mergeTickets.set(ticket,{ref,sha,method,account:user.id,generation,expires:this.now()+90000});return {ticket,title:bounded(pr.title,500),url:ref.url,sha,method};});}
 cancelMerge(ticket){this.mergeTickets.delete(ticket);}
 commitMerge(ticket){const value=this.mergeTickets.get(ticket);this.mergeTickets.delete(ticket);if(!value||value.generation!==this.generation||value.expires<this.now())throw new ReviewError('github_invalid_request');return this.guarded(async user=>{if(user.id!==value.account)throw new ReviewError('github_account_changed');const pr=await this.api(value.ref.endpoint);if(pr.head?.sha!==value.sha)throw new ReviewError('github_head_changed');if(pr.state!=='open'||pr.draft||pr.mergeable!==true)throw new ReviewError('github_merge_blocked');if(value.generation!==this.generation||!(await this.authorized())||value.generation!==this.generation)throw new ReviewError('github_locked');const before=account(await this.api('user'));if(before.id!==value.account)throw new ReviewError('github_account_changed');if(value.generation!==this.generation)throw new ReviewError('github_locked');const result=await this.api(value.ref.endpoint+'/merge',{sha:value.sha,merge_method:value.method});return {merged:result.merged===true,sha:bounded(result.sha,64)};});}
}
module.exports={GitHubReviews,ReviewError,parsePR,classify,cliEnvironment,timelineEvent};
