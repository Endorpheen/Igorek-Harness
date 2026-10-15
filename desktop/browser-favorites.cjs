const fs=require('node:fs');
const path=require('node:path');
const {createHash,randomUUID}=require('node:crypto');
const {pageUrl}=require('./browser.cjs');
const LIMIT=50;
function favorite(fields){
  if(!fields||typeof fields!=='object'||Array.isArray(fields)||Object.keys(fields).some(key=>!['url','title'].includes(key))||typeof fields.url!=='string'||fields.url.length>2048||typeof fields.title!=='string'||fields.title.length>64)throw Error('Укажи адрес сайта и название до 64 символов.');
  const raw=fields.url.trim();let url;try{url=new URL(/^[a-z][a-z0-9+.-]*:/i.test(raw)?raw:'https://'+raw);}catch{throw Error('Укажи корректный HTTPS-адрес сайта.');}
  if(!pageUrl(url.href))throw Error('Нужен публичный HTTPS-адрес сайта.');
  return {id:createHash('sha256').update(url.href).digest('hex'),url:url.href,title:(fields.title.trim()||url.hostname).slice(0,64)};
}
class BrowserFavorites{
  constructor(root){this.root=root;this.file=path.join(root,'favorites.json');this.rows=[];this.error=false;
    try{const info=fs.lstatSync(this.file);if(!info.isFile()||info.isSymbolicLink()||info.size>256*1024)throw Error();const data=JSON.parse(fs.readFileSync(this.file,'utf8'));if(data.version!==1||!Array.isArray(data.sites)||data.sites.length>LIMIT)throw Error();this.rows=data.sites.map(value=>favorite({url:value.url,title:value.title}));if(new Set(this.rows.map(row=>row.id)).size!==this.rows.length)throw Error();}
    catch(error){if(error.code!=='ENOENT')this.error=true;}
  }
  save(rows){
    if(this.error)throw Error('Не удалось прочитать избранное. Существующий файл сохранён.');
    fs.mkdirSync(this.root,{recursive:true,mode:0o700});
    try{if(fs.lstatSync(this.file).isSymbolicLink())throw Error('Некорректный файл избранного.');}catch(error){if(error.code!=='ENOENT')throw error;}
    const temporary=path.join(this.root,'.favorites-'+randomUUID());
    try{fs.writeFileSync(temporary,JSON.stringify({version:1,sites:rows.map(({url,title})=>({url,title}))}),{flag:'wx',mode:0o600});fs.renameSync(temporary,this.file);this.rows=rows;}
    finally{try{fs.unlinkSync(temporary);}catch{}}
  }
  add(fields){const row=favorite(fields),rows=this.rows.slice(),index=rows.findIndex(item=>item.id===row.id);if(index<0){if(rows.length>=LIMIT)throw Error('В избранном можно сохранить до 50 сайтов.');rows.push(row);}else rows[index]=row;this.save(rows);}
  remove(fields){if(!fields||Object.keys(fields).join()!=='id'||typeof fields.id!=='string'||!this.rows.some(row=>row.id===fields.id))throw Error('Сайт не найден в избранном.');this.save(this.rows.filter(row=>row.id!==fields.id));}
}
module.exports={BrowserFavorites,favorite,LIMIT};
