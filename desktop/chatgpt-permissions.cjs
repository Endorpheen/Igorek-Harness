const fs=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
class ChatGPTPermissions{
 constructor(root){this.file=path.join(root,'chatgpt-permissions.json');}
 microphone(){try{const info=fs.lstatSync(this.file);if(info.isSymbolicLink()||info.size>4096)return false;const value=JSON.parse(fs.readFileSync(this.file,'utf8'));return value.version===1&&value.origin==='https://chatgpt.com'&&value.microphone===true;}catch{return false;}}
 setMicrophone(allowed){
  if(typeof allowed!=='boolean')throw Error('Invalid microphone setting');
  if(fs.existsSync(this.file)&&fs.lstatSync(this.file).isSymbolicLink())throw Error('Invalid permissions file');
  fs.mkdirSync(path.dirname(this.file),{recursive:true,mode:0o700});
  const temporary=this.file+'.'+randomUUID();
  try{fs.writeFileSync(temporary,JSON.stringify({version:1,origin:'https://chatgpt.com',microphone:allowed})+'\n',{mode:0o600,flag:'wx'});fs.renameSync(temporary,this.file);}finally{try{fs.unlinkSync(temporary);}catch{}}
 }
}
module.exports={ChatGPTPermissions};
