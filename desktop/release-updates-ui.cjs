const { join } = require('node:path');
const { ReleaseUpdates } = require('./release-updates.cjs');

function updateDialog({app,dialog,shell,session,current,portable,install,channel}) {
  let busy = false, pendingFile;
  return async parent => {
    if (busy || !parent || parent.isDestroyed()) return {state:'busy'};
    busy = true;
    const updater = new ReleaseUpdates({current:current(),portable,preview:channel?.()==='preview',
      cache:join(app.getPath('userData'),'release-downloads'),fetch:(url,options)=>session.fetch(url,options)});
    try {
      const result = await updater.check();
      if (parent.isDestroyed()) return {state:'cancelled'};
      if (result.state !== 'available') {
        pendingFile = null;
        await dialog.showMessageBox(parent,{type:'info',title:'Обновления Игорька',
          message:result.state==='current'?'Установлена актуальная версия.':'Публичных релизов пока нет.',
          detail:`Текущая версия: ${result.current}`,buttons:['Закрыть']});
        return result;
      }
      const reusable = pendingFile?.version === result.version;
      const choice = await dialog.showMessageBox(parent,{type:'info',title:'Обновления Игорька',
        message:`Доступен Игорёк ${result.version}`,detail:`Установлена версия ${result.current}. Сборка: ${result.name}.\nСкачивание начнётся после подтверждения.`,
        buttons:[reusable?'Продолжить установку':'Скачать обновление','Посмотреть релиз','Отмена'],defaultId:0,cancelId:2});
      if (parent.isDestroyed() || choice.response===2) return {state:'cancelled'};
      if (choice.response===1) {await shell.openExternal(result.url);return {state:'release_opened'};}
      parent.setProgressBar(0);
      const file = reusable?pendingFile:await updater.download(value=>{if(!parent.isDestroyed())parent.setProgressBar(value);});
      pendingFile = file;
      if (parent.isDestroyed()) return {state:'downloaded'};
      parent.setProgressBar(-1);
      const choiceInstall = await dialog.showMessageBox(parent,{type:'info',title:'Обновление скачано',
        message:`Игорёк ${result.version} готов к установке.`,
        detail:'Контрольная сумма проверена. Установка закроет и заново откроет Игорька. История и подключения сохранятся. При активной задаче установка будет отложена.',
        buttons:install?['Установить и перезапустить','Показать файл','Позже']:['Показать файл','Позже'],defaultId:0,cancelId:install?2:1});
      if (!parent.isDestroyed()) {
        if (install && choiceInstall.response===0) return await install(file);
        if (choiceInstall.response===(install?1:0)) shell.showItemInFolder(file.path);
      }
      return {state:'downloaded',version:result.version};
    } catch (error) {
      if (!parent.isDestroyed()) {
        const detail = error.message==='update_tasks_active'?'Дождитесь завершения задачи и повторите установку. Скачанный файл сохранён.'
          :error.message==='update_opencode_active'?'Завершите работу и остановите OpenCode в настройках перед установкой. Скачанный файл сохранён.'
          :error.message==='update_locked'?'Разблокируйте хранилище перед установкой. Подключения и история не изменены.'
          :error.message==='unknown_update_installation'?'Для этой установки не найден штатный каталог. Скачанный файл можно установить вручную.'
          :error.message.startsWith('update_helper')?'Не удалось подготовить установку. Приложение не закрыто; скачанный файл сохранён. Можно повторить установку.'
          :error.message==='release_check_rate_limited'
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
