const fs=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
const LIMIT=1000;
function record(value){
 if(!value||typeof value!=='object'||typeof value.id!=='string'||!/^[-a-zA-Z0-9]{1,80}$/.test(value.id)||typeof value.name!=='string'||!value.name||value.name.length>1024||!['progressing','completed','cancelled','interrupted'].includes(value.state)||!Number.isFinite(value.at)||value.at<=0||!Number.isFinite(value.received)||value.received<0||!Number.isFinite(value.total)||value.total<0||typeof value.path!=='string'||value.path.length>4096||value.path&&!path.isAbsolute(value.path))return null;
 return {id:value.id,name:value.name,state:value.state,at:value.at,received:value.received,total:value.total,path:value.path};
}
class DownloadHistory{
 constructor(root){this.root=root;this.file=path.join(root,'downloads.json');this.rows=[];this.error=false;this.readFailed=false;
  try{const stat=fs.lstatSync(this.file);if(!stat.isFile()||stat.isSymbolicLink()||stat.size>2*1024*1024)throw Error();const data=JSON.parse(fs.readFileSync(this.file,'utf8'));if(data.version!==1||!Array.isArray(data.downloads))throw Error();this.rows=data.downloads.slice(0,LIMIT).map(record).filter(Boolean).map(row=>({...row,state:row.state==='progressing'?'interrupted':row.state}));}
  catch(error){if(error.code!=='ENOENT')this.error=this.readFailed=true;}
 }
 save(){if(this.readFailed)return;let temporary;
  try{fs.mkdirSync(this.root,{recursive:true,mode:0o700});try{if(fs.lstatSync(this.file).isSymbolicLink())throw Error();}catch(error){if(error.code!=='ENOENT')throw error;}
   temporary=path.join(this.root,'.downloads-'+randomUUID());fs.writeFileSync(temporary,JSON.stringify({version:1,downloads:this.rows.map(record).filter(Boolean).slice(0,LIMIT)}),{mode:0o600,flag:'wx'});fs.renameSync(temporary,this.file);this.error=false;
  }catch{this.error=true;}finally{if(temporary)try{fs.unlinkSync(temporary);}catch{}}
 }
 add(row){this.rows.unshift(row);this.rows=this.rows.slice(0,LIMIT);this.save();}
 finish(row){if(this.rows.some(item=>item.id===row.id))this.save();}
 remove(id){this.rows=this.rows.filter(row=>row.id!==id);this.save();}
 clear(){this.rows=[];this.readFailed=false;this.save();}
}
module.exports={DownloadHistory,LIMIT};
