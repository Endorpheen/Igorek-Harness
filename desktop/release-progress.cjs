const {join}=require('node:path');
const {pathToFileURL}=require('node:url');

function createUpdatePresentation(parent,{BrowserWindow,ipcMain}) {
  const url=pathToFileURL(join(__dirname,'release-progress.html')).href;
  const window=new BrowserWindow({width:570,height:350,resizable:false,parent,modal:true,show:false,
    title:'Обновления Игорька',backgroundColor:'#202722',autoHideMenuBar:true,
    webPreferences:{preload:join(__dirname,'release-progress-preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  let pending,closed=false,lastProgress=0;
  const ready=window.loadURL(url).then(()=>{if(!closed)window.show();});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-navigate',(event,target)=>{if(target!==url)event.preventDefault();});
  function choose(event,index) {
    if(closed||event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame
        ||event.senderFrame.url!==url||!pending||!Number.isInteger(index)||index<0||index>=pending.buttons)return;
    const resolve=pending.resolve;pending=null;resolve({response:index});
  }
  ipcMain.on('igorek:update-choice',choose);
  window.on('closed',()=>{closed=true;ipcMain.removeListener('igorek:update-choice',choose);
    if(pending){pending.resolve({response:pending.cancel});pending=null;}});
  const render=async value=>{await ready;if(!closed)await window.webContents.executeJavaScript('window.renderUpdate('+JSON.stringify(value)+')');};
  return {
    async showMessageBox(_parent,options) {
      if(closed)return {response:options.cancelId??options.buttons.length-1};
      let answer;
      const result=new Promise(resolve=>{answer=resolve;});
      pending={resolve:answer,buttons:options.buttons.length,cancel:options.cancelId??options.buttons.length-1};
      await render({message:options.message,detail:options.detail,buttons:options.buttons});
      return result;
    },
    progress(value) {
      const now=Date.now();if(value!==0&&value<1&&now-lastProgress<100)return;
      lastProgress=now;
      render({message:value>=1?'Проверяем скачанный файл…':'Скачиваем обновление…',
        detail:value>=1?'Проверяем размер и контрольную сумму перед установкой.':'Дождитесь окончания загрузки. Приложение пока можно оставить открытым.',
        progress:Math.max(0,Math.min(1,value)),buttons:[]}).catch(()=>{});
    },
    installing(){render({message:'Подготавливаем установку…',detail:'Игорёк закроется и откроется снова. История и подключения сохранятся.',progress:null,buttons:[]}).catch(()=>{});},
    close(){if(!closed)window.destroy();},
  };
}
module.exports={createUpdatePresentation};
