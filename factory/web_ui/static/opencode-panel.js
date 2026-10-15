(function(){
  let current=null;
  const api=operation=>window.IgorekDesktop.opencode(operation);
  const node=(tag,cls,text)=>{const item=document.createElement(tag);item.className=cls;if(text)item.textContent=text;return item;};
  window.IgorekDesktop?.onOpenCodeState?.(value=>current?.update(value));
  function mount(host,active){
    current?.observer.disconnect();
    const toolbar=node('div','opencode-toolbar'),caption=node('span','side-note','OpenCode'),setup=node('div','workspace-home'),surface=node('div','workspace-browser-surface');
    surface.id='workspace-opencode-surface';toolbar.append(caption);host.append(toolbar,setup,surface);
    let status={},busy=false,shown=false;
    const button=(label,operation,parent=toolbar)=>{const item=node('button','mini-button',label);item.type='button';item.addEventListener('click',()=>run(operation));parent.append(item);return item;};
    button('↻','reload').title='Обновить OpenCode';button('−','zoomOut').title='Уменьшить масштаб';button('100%','zoomReset').title='Сбросить масштаб';button('+','zoomIn').title='Увеличить масштаб';
    const configure=button('Настройка','settings');
    const observer=new ResizeObserver(()=>{if(active())api('layout').catch(()=>{});});observer.observe(surface);
    const update=value=>{
      if(!host.isConnected)return;status=value;
      caption.textContent='OpenCode · '+({ready:'Подключён',starting:'Подключение…',installing:'Установка…',stopped:'Остановлен',error:'Нет соединения'}[value.state]||'Настройка');
      surface.hidden=value.state!=='ready';setup.hidden=value.state==='ready'&&!shown;
      if(setup.hidden){api('layout').catch(()=>{});return;}
      setup.replaceChildren(node('h2','','OpenCode 2.0'));
      setup.append(node('p','side-note','Официальный интерфейс OpenCode. Его проекты, модели и сессии работают отдельно от Игорька.'));
      setup.append(node('p','side-note',value.mode==='kvm'?'Изоляция: KVM. Сервисом управляет гостевая система.':'Изоляция: sandbox. Доступны выбранные папки проектов; домашний каталог и подключения Игорька закрыты.'));
      if(value.error)setup.append(node('p','workspace-chat-error',value.error));
      if(value.projects?.length)setup.append(node('p','side-note',value.projects.join('\n')));
      if(value.host_services?.length)setup.append(node('p','side-note','Локальные сервисы: '+value.host_services.join(', ')+' · MCP igorek / host_service'));
      const actions=node('div','workspace-tools');setup.append(actions);
      if(!value.installed&&value.mode==='sandbox')button('Установить OpenCode '+value.version,'install',actions);
      button(value.projects?.length?'Добавить проект':'Выбрать проект','project',actions);
      if(value.projects?.length&&(value.installed||value.mode==='kvm'))button('Подключиться','show',actions);
      const advanced=node('details',''),summary=node('summary','','Подключение и отключение');advanced.append(summary);setup.append(advanced);
      if(value.mode==='sandbox')button('Локальные сервисы…','hostServices',advanced);
      button('Подключить сервис в KVM…','kvm',advanced);
      if(value.mode==='kvm')button('Использовать sandbox','sandbox',advanced);
      button('Отключить OpenCode','disable',advanced);
      for(const item of setup.querySelectorAll('button'))item.disabled=busy||['starting','installing'].includes(value.state);
    };
    const run=async operation=>{
      if(operation==='settings'){shown=!shown;if(shown)await api('hide');else if(status.state==='ready'&&active())await api('show');update(status);return;}
      if(busy)return;busy=true;
      try{if(['show','project','sandbox','kvm','disable','hostServices'].includes(operation))shown=false;const value=await api(operation);update(value);if(['project','hostServices'].includes(operation)&&value.projects?.length&&value.installed&&active())update(await api('show'));}
      catch(error){update({...status,error:error.message});}finally{busy=false;update(status);}
    };
    current={observer,update,resume:()=>{if(status.enabled&&status.projects?.length&&(status.installed||status.mode==='kvm')&&!shown)run('show');else api('layout').catch(()=>{});}};
    api('status').then(value=>{if(!host.isConnected||!active())return;update(value);if(value.enabled&&value.projects?.length&&(value.installed||value.mode==='kvm'))run('show');}).catch(error=>update({...status,error:error.message}));
  }
  window.IgorekOpenCode={mount,resume:()=>current?.resume()};
})();
