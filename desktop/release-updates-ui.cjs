const { join } = require('node:path');
const { ReleaseUpdates } = require('./release-updates.cjs');

function updateDialog({app,dialog,shell,session,current,portable}) {
  let busy = false;
  return async parent => {
    if (busy || !parent || parent.isDestroyed()) return {state:'busy'};
    busy = true;
    const updater = new ReleaseUpdates({current:current(),portable,
      cache:join(app.getPath('userData'),'release-downloads'),fetch:(url,options)=>session.fetch(url,options)});
    try {
      const result = await updater.check();
      if (parent.isDestroyed()) return {state:'cancelled'};
      if (result.state !== 'available') {
        await dialog.showMessageBox(parent,{type:'info',title:'Обновления Игорька',
          message:result.state==='current'?'Установлена актуальная версия.':'Публичных релизов пока нет.',
          detail:`Текущая версия: ${result.current}`,buttons:['Закрыть']});
        return result;
      }
      const choice = await dialog.showMessageBox(parent,{type:'info',title:'Обновления Игорька',
        message:`Доступен Игорёк ${result.version}`,detail:`Установлена версия ${result.current}. Сборка: ${result.name}.\nСкачивание начнётся после подтверждения.`,
        buttons:['Скачать обновление','Посмотреть релиз','Отмена'],defaultId:0,cancelId:2});
      if (parent.isDestroyed() || choice.response===2) return {state:'cancelled'};
      if (choice.response===1) {await shell.openExternal(result.url);return {state:'release_opened'};}
      parent.setProgressBar(0);
      const file = await updater.download(value=>{if(!parent.isDestroyed())parent.setProgressBar(value);});
      if (parent.isDestroyed()) return {state:'downloaded'};
      parent.setProgressBar(-1);
      const install = await dialog.showMessageBox(parent,{type:'info',title:'Обновление скачано',
        message:`Игорёк ${result.version} готов к установке.`,
        detail:file.portable?'Контрольная сумма проверена. Закройте Игорька и замените прежний AppImage скачанным файлом. История и подключения хранятся отдельно.'
          :'Контрольная сумма проверена. Завершите задачи и закройте Игорька перед установкой новой версии. История и подключения хранятся отдельно.',
        buttons:file.portable?['Показать файл','Позже']:['Показать установщик','Позже'],defaultId:0,cancelId:1});
      // The native file manager leaves installation and closing the app explicit.
      if (install.response===0 && !parent.isDestroyed()) shell.showItemInFolder(file.path);
      return {state:'downloaded',version:result.version};
    } catch (error) {
      if (!parent.isDestroyed()) {
        const detail = error.message==='release_check_rate_limited'
          ?'GitHub временно ограничил частоту запросов. Попробуйте позже.'
          :error.message==='platform_asset_missing'?'В релизе пока нет сборки для этой системы.'
          :error.message==='unverified_release_asset'?'У сборки нет подтверждённой контрольной суммы. Установка не предлагается.'
          :'Не удалось получить или проверить обновление. Проверьте подключение и повторите позже.';
        await dialog.showMessageBox(parent,{type:'warning',title:'Обновления Игорька',message:'Проверка обновления не завершена.',detail,buttons:['Закрыть']});
      }
      return {state:'failed'};
    } finally {busy=false;if(!parent.isDestroyed())parent.setProgressBar(-1);}
  };
}
module.exports = {updateDialog};
