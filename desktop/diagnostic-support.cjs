const {writeFile}=require('node:fs/promises');
const {join,extname}=require('node:path');
const support=require('./diagnostic-support.js');

async function saveAndCompose(value,options){
  const report=support.createReport(value.report,{version:options.version,surface:options.surface});
  if(options.isActive && !await options.isActive())return {state:'cancelled'};
  const result=await options.chooseFile({title:value.language==='en'?'Save diagnostic report':'Сохранить диагностический отчёт',
    defaultPath:join(options.documents,support.fileName(report)),filters:[{name:'JSON',extensions:['json']}],properties:['createDirectory']});
  if(result.canceled || !result.filePath || (options.isActive && !await options.isActive()))return {state:'cancelled'};
  if(extname(result.filePath).toLowerCase()!=='.json')throw new Error('Choose a JSON filename');
  try{await writeFile(result.filePath,JSON.stringify(report,null,2),{encoding:'utf8',flag:'wx',mode:0o600});}
  catch(error){if(error.code==='EEXIST')return {state:'name_conflict'};throw error;}
  if(options.isActive && !await options.isActive())return {state:'saved'};
  try{await options.openComposer(support.mailto(report,value.language));return {state:'composer_opened'};}
  catch{return {state:'saved'};}
}
async function previewIsOpen(contents,reportId){
  if(contents.isDestroyed() || typeof reportId!=='string' || !/^[A-Za-z0-9_.:-]{1,160}$/.test(reportId))return false;
  try{return Boolean(await contents.executeJavaScript("(()=>{const dialog=document.getElementById('igorek-diagnostic-support');return Boolean(dialog?.open)&&dialog.dataset.reportId==="+JSON.stringify(reportId)+";})()"));}
  catch{return false;}
}
module.exports={saveAndCompose,createReport:support.createReport,previewIsOpen};
