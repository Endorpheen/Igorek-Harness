(() => {
  const byId=id=>document.getElementById(id);
  let workspaceMounted=false;
  const originalParent=byId('plugins-view').parentNode;
  let generation=0,timer=0,busy=false,loading=false,pollInterval=10000;const expanded=new Set();
  let serviceTimer=0,serviceGeneration=0,serviceBusy=false;
  let extensionsGeneration=0;
  const extensionsModel={profile:'',busy:false},extensionsExpanded=new Set();
  const messages={operation_busy:'Дождитесь завершения текущей операции и повторите действие.',vault_locked:'Откройте Vault в настройках Игорька.',interface_locked:'Разблокируйте настройки Игорька.',invalid_request:'Действие недоступно.',operation_failed:'Операция не выполнена. Проверьте подключение.'};
  const bridge=(action,fields)=>window.IgorekDesktop?.plugins ? window.IgorekDesktop.plugins(action,fields) : Promise.resolve({error:'desktop_required'});
  const specs=[{id:'openai_docs',name:'OpenAI Docs',description:'Поиск и чтение официальной документации OpenAI. Публичный MCP, API-ключ не нужен.'},
    {id:'google_calendar',name:'Google Календарь',description:'События и свободное время в ваших календарях. Создание и перенос — после вашего подтверждения. Аккаунт подключается через Google.'}];
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text)node.textContent=text;return node;};
  function pollLater() {
    timer=window.setTimeout(()=>{if(!document.hidden&&document.hasFocus()&&!document.querySelector('.plugin-menu:popover-open'))load();else if(state.workspaceView==='plugins'||workspaceMounted)pollLater();},pollInterval);
  }
  function hide() {
    generation++;window.clearTimeout(timer);window.clearTimeout(serviceTimer);serviceGeneration++;byId('plugins-view').hidden=true;
    extensionsGeneration++;
    byId('plugins-cards').replaceChildren();
    byId('nav-plugins').classList.remove('active');byId('nav-plugins').removeAttribute('aria-current');
  }
  async function loadServices(){
    if(!window.IgorekDesktop?.hostService||serviceBusy||byId('plugins-view').hidden)return;
    const current=++serviceGeneration;
    try{
      const reply=await window.IgorekDesktop.hostService('list');if(current!==serviceGeneration)return;
      const host=byId('plugins-host-services');host.replaceChildren();
      if(reply.error){host.append(el('span','muted',reply.error==='host_service_access_denied'?'Локальные сервисы: разблокируйте Игорька.':'Локальные сервисы недоступны.'));return;}
      for(const service of reply.services||[]){
        const card=el('article','host-service-card');card.title='Автостоп через '+Math.round((service.idle_seconds||300)/60)+' мин без работы; генерация не прерывается.';const logo=el('span','host-service-logo');logo.append(workspaceGlyph('terminal'));
        const status=service.ready?'Готов':service.state==='active'?'Запускается…':service.state==='failed'?'Ошибка':'Остановлен';
        const label=el('span','host-service-label',service.label),indicator=el('span','host-service-state',status);indicator.classList.toggle('ready',service.ready);
        const button=el('button','host-service-control');button.type='button';const running=service.state==='active'||service.state==='activating';button.title=running?'Остановить':'Запустить';button.setAttribute('aria-label',button.title+' '+service.label);button.append(workspaceGlyph(running?'stop':'play'));
        button.addEventListener('click',async()=>{if(serviceBusy)return;serviceBusy=true;button.disabled=true;window.clearTimeout(serviceTimer);try{const result=await window.IgorekDesktop.hostService(running?'stop':'start',service.name);if(result.error)indicator.textContent='Не удалось';}catch{indicator.textContent='Не удалось';}finally{serviceBusy=false;loadServices();}});
        card.append(logo,label,indicator,button);host.append(card);
      }
    }catch{if(current===serviceGeneration)byId('plugins-host-services').textContent='Локальные сервисы недоступны.';}
    finally{if(current===serviceGeneration&&!byId('plugins-view').hidden){window.clearTimeout(serviceTimer);serviceTimer=window.setTimeout(loadServices,3000);}}
  }
  function render(data) {
    pollInterval=data.plugins?.google_calendar?.pending?1500:10000;
    byId('plugins-cards').replaceChildren();
    for(const spec of specs) {
      const value=data.plugins?.[spec.id]||{},card=el('article','plugin-card');card.dataset.plugin=spec.id;
      card.append(el('h2','',spec.name),el('p','',spec.description));
      let status=!data.vault_unlocked?'Vault закрыт':value.enabled?'Включён': 'Отключён';
      if(spec.id==='google_calendar'&&data.vault_unlocked) status=value.pending?'Завершаю подключение Google…':value.enabled?'Календарь подключён':value.error?'Подключение не завершено':'Не подключён';
      card.append(el('strong','plugin-state',status));
      if(value.time_zone)card.append(el('p','', 'Календарь: '+(value.calendar_name||'основной')+' · '+value.time_zone));
      if(value.check)card.append(el('p','', (value.check.ok?'Последняя проверка прошла':'Последняя проверка не прошла')+' · '+new Date(value.check.checked_at*1000).toLocaleString('ru-RU')));
      const actions=el('div','plugin-actions');
      const button=(label,handler,unavailable=false)=>{const node=el('button','',label);node.type='button';node.dataset.unavailable=String(unavailable);node.disabled=busy||loading||unavailable;node.addEventListener('click',handler);actions.append(node);};
      if(spec.id==='google_calendar') {
        const profile=(value.pending&&value.pending_profile) || data.profiles?.find(p=>p.name===state.session?.meta?.profile)?.name || (data.profiles?.length===1?data.profiles[0].name:'');
        if(!value.enabled)button(value.pending?'Подключение…':'Подключить Google Календарь',()=>action(spec.id,'connect',profile),value.pending||!data.vault_unlocked);
        if(value.enabled||value.pending||value.connected)button(value.pending?'Отменить':'Отключить',()=>action(spec.id,'disconnect'));
        if(!value.enabled)card.append(el('p','',profile?'После подтверждения Google календарь будет доступен модели «'+profile+'». Изменения событий требуют вашего подтверждения.':'Откройте чат с проверенной моделью, затем подключите календарь.'));
        if(value.error)card.append(el('p','',calendarError(value.error)));
        if(value.error==='calendar_api_disabled'){
          let target;try{target=new URL(value.activation_url||'https://console.cloud.google.com/apis/library/calendar-json.googleapis.com')}catch{}
          if(target?.origin==='https://console.cloud.google.com'&&target.pathname==='/apis/library/calendar-json.googleapis.com'&&!target.username&&!target.password&&[...target.searchParams.keys()].every(k=>k==='project')){
            const link=el('a','plugin-check-links','Открыть Google Calendar API и включить');link.href=target.href;link.target='_blank';link.rel='noopener noreferrer';card.append(link);
          }
        }
        if(value.enabled)button('Настройки',()=>settings('plugins'));
      } else {
        button('Подключить',()=>action(spec.id,'connect'));
        button('Проверить',()=>action(spec.id,'check'));
        button('Отключить',()=>action(spec.id,'disconnect'));
        button('Разрешения модели',()=>settings('mcp',spec.id));
      }
      card.append(actions);
      const profiles=(value.model_access||[]).map(item=>item.profile+(item.access==='local_only'?' (только локальная обработка)':''));
      card.append(el('p','plugin-model-access',!value.enabled?'Сначала подключите плагин.':profiles.length?'Доступ разрешён: '+profiles.join(', '):'Доступ модели не выдан. Нажмите «Разрешения модели», выберите профиль, разрешите доступ и сохраните с паролем Vault.'));
      if(value.enabled&&profiles.length){
        const selected=value.model_access?.find(item=>item.profile===state.session?.meta?.profile);
        const separate=selected?.access==='local_only'||value.model_access.every(item=>item.access==='local_only');
        card.append(el('p','',separate?'Для режима «Только локальная обработка» выберите подключение через «+»: его данные обрабатываются в отдельном контексте.':'С разрешённой моделью попросите Игорька использовать '+spec.name+' прямо в обычном чате.'));
      }
      const links=el('div','plugin-check-links');
      for(const item of value.check?.links||[]) {
        let url;try{url=new URL(item.url)}catch{continue}
        if(url.protocol!=='https:'||!['developers.openai.com','platform.openai.com','learn.chatgpt.com'].includes(url.hostname))continue;
        const link=el('a','',item.title||item.url);link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';links.append(link);
      }
      card.append(links);compact(card,spec,status,value);byId('plugins-cards').append(card);
    }
  }
  function compact(card,spec,status,value) {
    const provider=window.IgorekProfileCards?.serviceProvider(spec.id);
    const heading=card.querySelector('h2'),stateLabel=card.querySelector('.plugin-state'),actions=card.querySelector('.plugin-actions');
    const top=el('div','plugin-compact-head'),logo=el('span','plugin-logo');logo.setAttribute('aria-hidden','true');
    if(provider?.logo)logo.innerHTML=provider.logo;
    else logo.textContent=spec.id==='openai_docs'?'◎':'▦';
    const copy=el('div','plugin-compact-copy');copy.append(heading,stateLabel);stateLabel.classList.toggle('is-off',!value.enabled);
    const more=el('button','plugin-more','⋮');more.type='button';more.title='Действия — '+spec.name;more.setAttribute('aria-label',more.title);more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-expanded','false');
    const menu=el('div','plugin-menu');menu.id='plugin-menu-'+spec.id;menu.setAttribute('popover','auto');menu.setAttribute('role','menu');more.setAttribute('aria-controls',menu.id);
    for(const button of [...actions.children]){button.setAttribute('role','menuitem');button.addEventListener('click',()=>menu.hidePopover?.());menu.append(button);}
    menu.addEventListener('toggle',()=>more.setAttribute('aria-expanded',String(menu.matches(':popover-open'))));
    more.addEventListener('click',()=>{
      if(menu.matches(':popover-open')){menu.hidePopover();return;}
      const r=more.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(innerWidth-248,r.right-240))+'px';menu.style.top=Math.max(8,Math.min(innerHeight-280,r.bottom+6))+'px';menu.showPopover();menu.querySelector('button:not(:disabled)')?.focus();
    });
    menu.addEventListener('keydown',event=>{const buttons=[...menu.querySelectorAll('button:not(:disabled)')];const i=buttons.indexOf(document.activeElement);if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(i+(event.key==='ArrowUp'?-1:1)+buttons.length)%buttons.length]?.focus();}});
    const details=el('details','plugin-details'),summary=el('summary','','Подробнее');details.append(summary);details.open=expanded.has(spec.id);details.addEventListener('toggle',()=>{if(details.open)expanded.add(spec.id);else expanded.delete(spec.id);});
    for(const child of [...card.children])if(child!==actions)details.append(child);
    actions.remove();top.append(logo,copy,more);card.append(top,menu,details);card.classList.add('is-compact');
  }
  function calendarError(code) {
    return ({google_not_configured:'В Игорьке ещё не настроено подключение Google.',model_profile_required:'Откройте чат с проверенной моделью, затем нажмите «Подключить Google Календарь».',calendar_permissions_missing:'Google не предоставил все разрешения календаря. Повторите подключение и подтвердите доступ.',calendar_api_disabled:'В Google-проекте подключения выключен Calendar API. Включите Google Calendar API в Google Cloud Console и повторите подключение.',calendar_authorization_failed:'Подключение Google не завершено. Повторите и подтвердите доступ в браузере.',mcp_access_denied:'Модель или доступ изменились во время подключения. Повторите подключение.'})[code]||messages[code]||'Не удалось подключить Google Календарь. Повторите вход.';
  }
  async function settings(panel,plugin) {
    const reply=await bridge('settings',plugin?{panel,plugin}:{panel});
    if(reply.error)byId('plugins-status').textContent=messages[reply.error]||'Настройки доступны в самодостаточном desktop-приложении.';
  }
  async function load() {
    if(busy||loading||!state.paired||byId('plugins-view').hidden)return;
    loading=true;controls();
    const current=generation;
    try {
      const reply=await bridge('status');if(current!==generation)return;
      if(reply.error){if(reply.error!=='operation_busy')byId('plugins-status').textContent=messages[reply.error]||'Раздел подключается к собственному runtime desktop.';return;}
      byId('plugins-status').textContent='';render(reply);
    } catch {if(current===generation)byId('plugins-status').textContent='Не удалось получить состояние. Повторите обновление.';}
    finally {loading=false;controls();if(state.paired&&(state.workspaceView==='plugins'||workspaceMounted)&&!byId('plugins-view').hidden){if(current===generation)pollLater();else load();}}
  }
  function controls() {
    byId('plugins-refresh').disabled=busy||loading||extensionsModel.busy||Boolean(extensionsModel.skillBusy);
    for(const node of byId('plugins-cards').querySelectorAll('button'))node.disabled=busy||loading||node.dataset.unavailable==='true';
  }
  async function action(plugin,operation,profile) {
    if(busy||loading)return;busy=true;window.clearTimeout(timer);const current=++generation;let refreshAfter=false;
    byId('plugins-refresh').disabled=true;
    for(const node of byId('plugins-cards').querySelectorAll('button'))node.disabled=true;
    byId('plugins-status').textContent=operation==='check'?'Проверяю настоящее подключение…':'Выполняю действие…';
    try {
      if(plugin==='google_calendar'&&operation==='connect'&&!profile){byId('plugins-status').textContent=calendarError('model_profile_required');return;}
      const reply=await bridge(plugin==='google_calendar'&&operation==='connect'?'calendar_connect':'action',plugin==='google_calendar'&&operation==='connect'?{profile}:{plugin,operation});if(current!==generation)return;
      if(reply.error){byId('plugins-status').textContent=plugin==='google_calendar'?calendarError(reply.error):messages[reply.error]||'Операция не выполнена.';refreshAfter=plugin==='google_calendar'&&reply.error==='calendar_api_disabled';}
      else if(reply.opened){byId('plugins-status').textContent='Подтвердите доступ в открытом окне Google. Подключение завершится автоматически.';pollInterval=1500;}
      else {render(reply);byId('plugins-status').textContent=reply.plugins?.[plugin]?.check?.ok===false?'Проверка не прошла. Подключение не объявляется работающим.':'Состояние сохранено.';}
    } catch {if(current===generation)byId('plugins-status').textContent='Нет ответа. Повторите действие после завершения текущей операции.';}
    finally {busy=false;controls();if(state.paired&&(state.workspaceView==='plugins'||workspaceMounted)&&!byId('plugins-view').hidden){if(current===generation&&!refreshAfter)pollLater();else load();}}
  }
  function unmount(){if(!workspaceMounted)return;hide();originalParent.append(byId('plugins-view'));workspaceMounted=false;}
  function mount(container){unmount();workspaceMounted=true;container.append(byId('plugins-view'));byId('plugins-view').hidden=false;load();loadExtensions();loadServices();}
  async function show() {
    unmount();
    if(!state.paired)return;
    showMainView();state.mainTitle=ui.title.textContent;state.workspaceView='plugins';
    byId('plugins-view').hidden=false;document.querySelector('.workspace-row').hidden=true;
    for(const id of ['nav-home','nav-scheduled']){byId(id).classList.remove('active');byId(id).removeAttribute('aria-current');}
    byId('nav-plugins').classList.add('active');byId('nav-plugins').setAttribute('aria-current','page');
    ui.title.textContent='Плагины';ui.panelButton.hidden=true;document.body.classList.remove('sidebar-open');await Promise.all([load(),loadExtensions(),loadServices()]);
  }
  function renderExtensions(){
    const host=byId('plugins-extensions');if(!host||byId('plugins-view').hidden)return;
    const model=extensionsModel,toolbar=el('div','plugins-extension-toolbar'),label=el('label','','Инструменты профиля'),select=el('select');select.setAttribute('aria-label','Профиль скиллов и MCP');
    const profiles=[...(model.options?.profiles||[])];if(model.profile&&!profiles.includes(model.profile))profiles.unshift(model.profile);
    for(const profile of profiles){const option=el('option','',profile);option.value=profile;select.append(option);}select.value=model.profile;select.disabled=model.busy||Boolean(model.skillBusy);select.addEventListener('change',()=>loadExtensions(select.value));label.append(select);toolbar.append(label);
    const grid=el('div','plugins-extension-grid');
    const changed=()=>{renderExtensions();controls();if(!model.skillBusy)loadExtensions();};
    for(const [key,title,icon,card,rows] of [
      ['skills',t('skills.card'),'plan',renderExtensionsSettings(model,{profileSelector:false,compact:true,changed}),'skills'],
      ['mcp',t('mcp.card'),'plugins',renderMcpSettings(model),'mcp_servers']
    ]){
      const group=el('details','plugins-extension-group'),summary=el('summary'),logo=el('span','plugins-extension-icon'),count=el('span','plugins-extension-count',model.busy?'…':model.extensionsError?'!':model.extensions?String(model.extensions[rows]?.length||0):'—'),chevron=el('span','plugins-extension-chevron');count.classList.toggle('is-error',Boolean(model.extensionsError));if(model.extensionsError){count.title=model.extensionsError;count.setAttribute('aria-label',model.extensionsError);}group.dataset.extension=key;group.open=extensionsExpanded.has(key);logo.append(workspaceGlyph(icon));chevron.append(workspaceGlyph('chevron'));summary.append(logo,el('span','',title),count,chevron);group.append(summary);card.querySelector('.settings-card-head')?.remove();card.classList.add('plugins-extension-body');group.append(card);group.addEventListener('toggle',()=>{if(group.open)extensionsExpanded.add(key);else extensionsExpanded.delete(key)});grid.append(group);
    }
    host.replaceChildren(toolbar,grid);controls();
  }
  async function loadExtensions(profile){
    if(!window.IgorekDesktop?.plugins||!state.paired||byId('plugins-view').hidden)return;
    const model=extensionsModel;if(model.skillBusy){renderExtensions();return;}
    if(profile!==undefined&&profile!==model.profile){model.profile=profile;model.extensions=null;model.extensionsError='';}
    const current=++extensionsGeneration;model.busy=true;renderExtensions();
    try{
      if(!model.options){const options=await api('/api/session-options');if(current!==extensionsGeneration)return;model.options=options;}
      if(!model.profile)model.profile=(model.options.profiles||[]).includes(state.session?.meta?.profile)?state.session.meta.profile:model.options.default_profile||model.options.profiles?.[0]||'';
      const data=await api('/api/extensions'+(model.profile?'?profile='+encodeURIComponent(model.profile):''));if(current!==extensionsGeneration)return;model.extensions=data;model.extensionsError='';
    }catch(error){if(current===extensionsGeneration){model.extensions=null;model.extensionsError=error.message;}}
    finally{if(current===extensionsGeneration){model.busy=false;renderExtensions();}}
  }
  window.IgorekPlugins={hide,show,mount,unmount};byId('nav-plugins').addEventListener('click',show);byId('plugins-refresh').addEventListener('click',()=>{window.clearTimeout(timer);load();loadExtensions();loadServices()});
  byId('nav-plugins').disabled=true;
})();
