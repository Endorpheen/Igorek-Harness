(() => {
  let opened = null;
  const pending = new Set();
  const labels = {
    ru: {title:'Профиль и модель', hint:'Выбор запускает исполнителя для этого чата.', loading:'Загружаю профили…',
      empty:'Нет проверенных подключений. Добавьте и проверьте модель в настройках.',
      unavailable:'Переключение профиля в этом подключении недоступно.', current:'В этом чате', running:'Исполнитель запущен',
      starting:'Запускаю профиль…', switchingModel:'Переключаю модель…', modelsUnavailable:'Не удалось загрузить модели аккаунта. Повторите позже.', busy:'Дождитесь завершения текущей задачи.', failed:'Не удалось переключить профиль.', choose:'Выбрать профиль и запустить исполнителя'},
    en: {title:'Profile and model', hint:'Selecting a profile starts its worker for this chat.', loading:'Loading profiles…',
      empty:'No verified connections. Add and check a model in settings.', unavailable:'Profile switching is unavailable for this connection.',
      current:'This chat', running:'Worker running', starting:'Starting profile…', busy:'Wait for the current task to finish.',
      failed:'Could not switch the profile.', switchingModel:'Switching model…', modelsUnavailable:'Could not load account models. Try again later.', choose:'Choose a profile and start its worker'},
  };
  const text = key => labels[document.documentElement.lang.startsWith('en') ? 'en' : 'ru'][key];
  function node(tag, className, value='') {
    const item=document.createElement(tag);item.className=className;item.textContent=value;return item;
  }
  function close(focus=false) {
    if(!opened)return;
    const {menu,button}=opened;opened=null;menu.remove();button.setAttribute('aria-expanded','false');
    if(focus&&button.isConnected)button.focus();
  }
  function attach(button, options) {
    button.type='button';button.setAttribute('aria-haspopup','menu');button.setAttribute('aria-expanded','false');
    button.setAttribute('aria-label',text('choose'));
    button.addEventListener('click',async()=>{
      if(opened?.button===button){close();return;}
      close();
      const id=options.sessionId();if(!id)return;
      const menu=node('div','composer-profile-menu'),title=node('strong','',text('title'));
      const note=node('p','composer-profile-note',text('hint')),status=node('p','composer-profile-status',text('loading'));
      const list=node('div','composer-profile-list');menu.setAttribute('role','menu');menu.setAttribute('aria-label',text('title'));
      status.setAttribute('role','status');menu.append(title,note,list,status);document.body.append(menu);
      const item={menu,button,id};opened=item;button.setAttribute('aria-expanded','true');
      const box=button.getBoundingClientRect(),width=Math.min(360,window.innerWidth-24);
      menu.style.width=width+'px';menu.style.left=Math.max(12,Math.min(box.right-width,window.innerWidth-width-12))+'px';
      menu.style.bottom=Math.max(12,window.innerHeight-box.top+8)+'px';
      menu.style.maxHeight=Math.max(120,box.top-20)+'px';
      const valid=()=>opened===item&&button.isConnected&&options.sessionId()===id;
      const blocked=()=>pending.has(id)||Boolean(options.busy?.());
      menu.addEventListener('keydown',event=>{
        if(event.key==='Escape'){event.preventDefault();close(true);return;}
        if(event.key==='Tab'){close();return;}
        if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
        const rows=Array.from(list.querySelectorAll('button:not(:disabled)'));if(!rows.length)return;
        event.preventDefault();let index=rows.indexOf(document.activeElement);
        index=event.key==='Home'?0:event.key==='End'?rows.length-1:(index+(event.key==='ArrowUp'?-1:1)+rows.length)%rows.length;
        rows[index].focus();
      });
      try {
        const currentProfile=String(options.profile()||'');
        const catalog=await options.api('/api/session-options'+(currentProfile?'?model_profile='+encodeURIComponent(currentProfile):''));if(!valid())return;
        if(currentProfile!==String(options.profile()||'')){close();return;}
        if(!catalog.profile_switch_supported){status.textContent=text('unavailable');return;}
        const profiles=Array.isArray(catalog.profile_catalog)?catalog.profile_catalog:[];
        if(!profiles.length){status.textContent=text('empty');return;}
        status.textContent=blocked()?text('busy'):'';
        for(const profile of profiles){
          if(typeof profile.name!=='string'||!profile.name)continue;
          const row=node('button','composer-profile-option');row.type='button';row.disabled=blocked();
          row.dataset.profile=profile.name;
          row.setAttribute('role','menuitemradio');row.setAttribute('aria-checked',String(profile.name===options.profile()));
          row.append(node('span','composer-profile-label',profile.name),node('span','composer-profile-model',String(profile.model||'')));
          if(profile.name===options.profile())row.append(node('small','',text('current')));
          if(profile.name===catalog.default_profile)row.append(node('small','',text('running')));
          row.addEventListener('click',async()=>{
            if(!valid()||blocked())return;
            pending.add(id);options.changing?.(true);status.textContent=text('starting');
            for(const entry of list.querySelectorAll('button'))entry.disabled=true;
            try {
              const result=await options.api('/api/sessions/'+encodeURIComponent(id)+'/profile',{
                method:'POST',body:JSON.stringify({profile:profile.name}),
              });
              if(result?.session_id!==id||result?.meta?.profile!==profile.name)throw Error(text('failed'));
              window.dispatchEvent(new CustomEvent('igorek-profile-changed',{detail:result}));
              if(!valid())return;
              await options.changed(result);
              if(valid())close(true);
            } catch(error) {
              if(valid()){status.textContent=error.message||text('failed');status.setAttribute('role','alert');}
            } finally {
              pending.delete(id);options.changing?.(false);
              if(valid())for(const entry of list.querySelectorAll('button'))entry.disabled=blocked();
            }
          });
          list.append(row);
        }
        const active=profiles.find(profile=>profile.name===options.profile());
        if(active?.models_status==='unavailable'&&!blocked())status.textContent=text('modelsUnavailable');
        if(Array.isArray(active?.models)&&active.models.length){
          list.append(node('strong','composer-profile-label',document.documentElement.lang.startsWith('en')?'Model for this chat':'Модель для этого чата'));
          for(const model of active.models){
            const row=node('button','composer-profile-option');row.type='button';row.disabled=blocked();row.dataset.model=model;
            row.setAttribute('role','menuitemradio');row.setAttribute('aria-checked',String(model===options.model?.()));
            row.append(node('span','composer-profile-label',active.model_labels?.[model]||model));row.title=model;
            row.addEventListener('click',async()=>{
              if(!valid()||blocked())return;
              if(model===options.model?.()){close(true);return;}
              pending.add(id);options.changing?.(true);status.textContent=text('switchingModel');
              for(const entry of list.querySelectorAll('button'))entry.disabled=true;
              try{
                const result=await options.api('/api/sessions/'+encodeURIComponent(id)+'/model',{
                  method:'POST',body:JSON.stringify({profile:active.name,model})});
                if(result?.session_id!==id||result?.meta?.profile!==active.name||result?.meta?.model!==model)throw Error(text('failed'));
                window.dispatchEvent(new CustomEvent('igorek-profile-changed',{detail:result}));
                if(!valid())return;await options.changed(result);if(valid())close(true);
              }catch(error){if(valid()){status.textContent=error.message||text('failed');status.setAttribute('role','alert');}}
              finally{pending.delete(id);options.changing?.(false);if(valid())for(const entry of list.querySelectorAll('button'))entry.disabled=blocked();}
            });list.append(row);
          }
        }
        list.querySelector('button:not(:disabled)')?.focus();
      } catch(error) {
        if(valid()){status.textContent=error.message||text('failed');status.setAttribute('role','alert');}
      }
    });
  }
  document.addEventListener('pointerdown',event=>{if(opened&&!opened.menu.contains(event.target)&&!opened.button.contains(event.target))close();});
  document.addEventListener('keydown',event=>{if(opened&&event.key==='Escape'){event.preventDefault();close(true);}});
  window.addEventListener('igorek-session-changed',()=>close());
  window.addEventListener('pagehide',()=>close());
  window.addEventListener('resize',()=>close());
  window.IgorekComposerProfiles={attach,busy:id=>pending.has(id)};
})();
