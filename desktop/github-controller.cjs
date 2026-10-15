const {ReviewError}=require('./github-reviews.cjs');
const FIELDS={identity:[],status:[],list:['filter','query','state','page','team'],detail:['url'],files:['url','page','sha'],timeline:['url','page'],pin:['url','title','pinned','account_id'],bind:['url','session_id','account_id'],merge:['url','sha','method','account_id']};
async function reviewRequest(service,{trusted,confirm},operation,fields={}){
 const valid=()=>{if(!trusted())throw new ReviewError('github_locked');};valid();
 if(!FIELDS[operation]||!fields||typeof fields!=='object'||Array.isArray(fields)||Object.keys(fields).some(k=>!FIELDS[operation].includes(k)))throw new ReviewError('github_invalid_request');
 if(!service)throw new ReviewError('github_locked');
 if(operation==='merge'){
  const prepared=await service.prepareMerge(fields);valid();
  try{if(!(await confirm(prepared)))return {cancelled:true};valid();return await service.commitMerge(prepared.ticket);}
  finally{service.cancelMerge(prepared.ticket);}
 }
 const value=await service[operation](operation==='detail'?fields.url:fields);valid();return value;
}
module.exports={reviewRequest};
