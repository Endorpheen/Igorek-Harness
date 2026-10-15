(() => {
  const bridge=window.IgorekDesktop?.agent;
  if(!bridge)return;
  const dialog=document.createElement('dialog');dialog.id='agent-start-dialog';dialog.className='session-dialog';
  const title=document.createElement('h2'),detail=document.createElement('p'),error=document.createElement('p'),actions=document.createElement('div');actions.className='dialog-actions';error.setAttribute('role','status');
  const cancel=document.createElement('button'),confirm=document.createElement('button');cancel.type=confirm.type='button';cancel.textContent='Отмена';confirm.className='primary-button';confirm.id='agent-start-confirm';cancel.id='agent-start-cancel';
  actions.append(cancel,confirm);dialog.append(title,detail,error,actions);document.body.append(dialog);
  let pending=null,epoch=0;
  const messages={vault_locked:'Откройте хранилище в настройках Игорька.',interface_locked:'Разблокируйте настройки Игорька.',profile_not_verified:'Проверьте подключение выбранного профиля в настройках.',agent_busy:'Дождитесь завершения текущей задачи, затем повторите запуск.',operation_busy:'Дождитесь завершения текущей операции и повторите запуск.',local_requires_host_mode:'Локальной модели нужен режим выполнения на этом компьютере.',isolation_unavailable:'Проверьте среду выполнения в настройках.'};
  function finish(result){const item=pending;pending=null;epoch++;if(dialog.open)dialog.close();item?.resolve(result);}
  cancel.addEventListener('click',()=>finish(false));dialog.addEventListener('cancel',event=>{event.preventDefault();finish(false)});
  window.addEventListener('igorek-session-changed',()=>finish(false));window.addEventListener('pagehide',()=>finish(false));
  confirm.addEventListener('click',async()=>{
    const item=pending;if(!item)return;
    if(item.settings){finish(false);window.IgorekDesktop.mcp('settings').catch(()=>{});return;}
    const current=epoch;confirm.disabled=cancel.disabled=true;error.textContent='Запускаю профиль…';
    try{
      const reply=await bridge('start',item.profile);
      if(current!==epoch)return;
      if(reply.error){error.textContent=messages[reply.error]||'Не удалось запустить профиль. Проверьте настройки.';if(['vault_locked','interface_locked','profile_not_verified','local_requires_host_mode','isolation_unavailable'].includes(reply.error)){item.settings=true;confirm.textContent='Открыть настройки';}return;}
      if(reply.agent?.running&&reply.agent.profile===item.profile)finish(true);
      else error.textContent='Исполнитель ещё не готов. Повторите запуск.';
    }catch{if(current===epoch)error.textContent='Нет ответа от приложения. Повторите запуск.';}
    finally{confirm.disabled=cancel.disabled=false;}
  });
  window.IgorekAgent={async ensureProfile(profile){
    if(typeof profile!=='string'||!profile)return false;
    if(pending)return false;
    const current=++epoch;
    let reply;try{reply=await bridge('status',profile)}catch{reply={error:'operation_busy'}}
    if(current!==epoch)return false;
    if(reply.agent?.running&&reply.agent.profile===profile)return true;
    return new Promise(resolve=>{
      const settings=['vault_locked','interface_locked','profile_not_verified','local_requires_host_mode','isolation_unavailable'].includes(reply.error);
      pending={resolve,profile,settings};confirm.disabled=cancel.disabled=false;
      title.textContent=settings?'Откройте настройки Игорька':'Запустить профиль «'+profile+'»?';
      detail.textContent=settings?messages[reply.error]:reply.agent?.running?'Сейчас работает профиль «'+reply.agent.profile+'». Запустить выбранный профиль вместо него?':'Исполнитель остановлен. Запустить выбранный профиль и продолжить работу в чате?';
      error.textContent=reply.error&&!settings?(messages[reply.error]||'Состояние сейчас недоступно. Можно повторить запуск.') : '';
      confirm.textContent=settings?'Открыть настройки':'Запустить';dialog.showModal();confirm.focus();
    });
  }};
})();
