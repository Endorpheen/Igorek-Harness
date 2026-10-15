(() => {
  if (!window.IgorekWindow || document.getElementById('igorek-window-bar')) return;
  const bar = document.createElement('header'); bar.id = 'igorek-window-bar';
  bar.setAttribute('aria-label', 'Меню приложения');
  if (document.getElementById('sidebar')) {
    const toggle = document.createElement('button'); toggle.id = 'igorek-toggle-sidebar'; toggle.type = 'button';
    toggle.setAttribute('aria-controls', 'sidebar'); toggle.setAttribute('aria-label', 'Переключить боковую панель');
    toggle.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg>';
    let keys = ['CmdOrCtrl+Shift+S'];
    const update = () => { toggle.setAttribute('aria-expanded', String(!document.body.classList.contains('igorek-sidebar-hidden'))); toggle.title = 'Переключить боковую панель' + (keys.length ? ' · ' + keys.map(key => key.replaceAll('CmdOrCtrl', navigator.platform.includes('Mac') ? '⌘' : 'Ctrl')).join(', ') : ''); };
    toggle.addEventListener('click', () => { document.body.classList.toggle('igorek-sidebar-hidden'); document.body.classList.remove('sidebar-open'); update(); });
    const labels = value => { if (Array.isArray(value)) { keys = value; update(); } };
    window.IgorekWindow.shortcutKeys?.().then(labels).catch(() => {});
    window.IgorekWindow.onShortcutsChanged?.(labels);
    update(); bar.append(toggle);
  }
  for (const label of ['Файл', 'Правка', 'Вид', 'Справка']) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
    button.setAttribute('aria-haspopup', 'menu');
    button.addEventListener('click', () => {
      const rect = button.getBoundingClientRect();
      window.IgorekWindow.menu(label, Math.round(rect.left), Math.round(rect.bottom)).catch(() => {});
    });
    bar.append(button);
  }
  const space = document.createElement('div'); space.className = 'igorek-window-drag'; bar.append(space);
  const rail = document.querySelector('.app-rail');
  if(rail&&window.IgorekDesktop?.chatgpt&&!document.getElementById('igorek-open-chatgpt')){
    const button=document.createElement('button');button.type='button';button.id='igorek-open-chatgpt';button.className='rail-button';button.title='ChatGPT';button.setAttribute('aria-label','ChatGPT');button.setAttribute('aria-pressed','false');
    button.classList.add('chatgpt-rail-button');
    button.innerHTML='<svg viewBox="4 4 32 32" aria-hidden="true" class="openai-logo"><path d="M32.8377 17.282C33.2127 16.25 33.3072 15.218 33.2127 14.1875C33.1197 13.1571 32.7447 12.1251 32.2752 11.1876C31.4322 9.78209 30.2127 8.6571 28.8072 8.0001C27.3072 7.34461 25.7127 7.15711 24.1197 7.53211C23.3698 6.78212 22.5253 6.12512 21.5878 5.65713C20.6503 5.18913 19.5253 5.00013 18.4948 5.00013C16.8851 4.99074 15.3125 5.48246 13.9948 6.40712C12.6824 7.34311 11.7449 8.6571 11.2754 10.1571C10.1504 10.4376 9.21289 10.9071 8.27539 11.4696C7.4324 12.1251 6.77541 12.9696 6.21291 13.8126C5.36992 15.2195 5.08792 16.8125 5.27542 18.407C5.46399 19.9968 6.11605 21.496 7.1504 22.718C6.79608 23.7086 6.66795 24.7659 6.77541 25.8124C6.86991 26.8444 7.2449 27.8749 7.7129 28.8124C8.55739 30.2194 9.77538 31.3444 11.1824 31.9999C12.6824 32.6569 14.2753 32.8444 15.8698 32.4694C16.6198 33.2194 17.4628 33.8749 18.4003 34.3444C19.3378 34.8139 20.4628 34.9999 21.4948 34.9999C23.1043 35.0097 24.6769 34.5185 25.9947 33.5944C27.3072 32.6569 28.2447 31.3444 28.7127 29.8444C29.7719 29.6432 30.7682 29.1934 31.6197 28.5319C32.4627 27.8749 33.2127 27.1249 33.6822 26.1874C34.5251 24.7819 34.8071 23.1875 34.6196 21.5945C34.4322 20 33.8697 18.5015 32.8377 17.282ZM21.5878 33.0304C20.0878 33.0304 18.9628 32.5609 17.9323 31.7179C17.9323 31.7179 18.0253 31.6234 18.1198 31.6234L24.1197 28.1554C24.2862 28.0803 24.4196 27.9469 24.4947 27.7804C24.5698 27.636 24.6021 27.4731 24.5877 27.3109V18.875L27.1197 20.375V27.3124C27.1455 28.0547 27.0215 28.7945 26.755 29.4878C26.4885 30.181 26.085 30.8134 25.5687 31.3473C25.0523 31.8811 24.4337 32.3054 23.7497 32.5949C23.0658 32.8843 22.3305 33.0314 21.5878 33.0304ZM9.49488 27.8749C8.83789 26.7499 8.55739 25.4374 8.83789 24.125C8.83789 24.125 8.93239 24.2195 9.02539 24.2195L15.0253 27.6874C15.1693 27.7638 15.3325 27.7966 15.4948 27.7819C15.6823 27.7819 15.8698 27.7819 15.9628 27.6874L23.2753 23.4695V26.3749L17.1823 29.9374C16.5506 30.3042 15.8527 30.5427 15.1287 30.6393C14.4046 30.7358 13.6686 30.6884 12.9629 30.4999C11.4629 30.1249 10.2449 29.1874 9.49488 27.8749ZM7.9004 14.8445C8.56239 13.7234 9.58826 12.8627 10.8074 12.4056V19.532C10.8074 19.718 10.8074 19.907 10.9004 20C10.9755 20.1665 11.1089 20.2998 11.2754 20.375L18.5878 24.5944L16.0573 26.0944L10.0574 22.625C9.41842 22.2639 8.85742 21.7797 8.40684 21.2004C7.95627 20.6211 7.62506 19.9582 7.4324 19.25C7.05741 17.8445 7.1504 16.157 7.9004 14.8445ZM28.6197 19.625L21.3073 15.407L23.8377 13.9071L29.8377 17.375C30.7752 17.9375 31.5252 18.6875 31.9947 19.625C32.4642 20.5625 32.7447 21.5945 32.6502 22.7195C32.5603 23.7755 32.1699 24.7837 31.5252 25.6249C30.8697 26.4694 30.0252 27.1249 28.9947 27.4999V20.375C28.9947 20.1875 28.9947 20 28.9002 19.907C28.9002 19.907 28.8072 19.718 28.6197 19.625ZM31.1502 15.875C31.1502 15.875 31.0572 15.782 30.9627 15.782L24.9627 12.3126C24.7752 12.2196 24.6822 12.2196 24.4947 12.2196C24.3072 12.2196 24.1197 12.2196 24.0252 12.3126L16.7128 16.532V13.6251L22.8073 10.0626C23.7448 9.50009 24.7752 9.31259 25.9002 9.31259C26.9322 9.31259 27.9627 9.68759 28.9002 10.3446C29.7447 11.0001 30.4947 11.8446 30.8697 12.7821C31.2447 13.7196 31.3377 14.8445 31.1502 15.875ZM15.4003 21.125L12.8699 19.625V12.5946C12.8699 11.5626 13.1503 10.4376 13.7128 9.59459C14.2753 8.6571 15.1198 8.0001 16.0573 7.53211C17.0127 7.05249 18.0956 6.88812 19.1503 7.06261C20.1823 7.15711 21.2128 7.62511 22.0573 8.2821C22.0573 8.2821 21.9628 8.3751 21.8698 8.3751L15.8698 11.8446C15.7033 11.9197 15.57 12.0531 15.4948 12.2196C15.4003 12.4071 15.4003 12.5001 15.4003 12.6876V21.125ZM16.7128 18.125L19.9948 16.25L23.2753 18.125V21.875L19.9948 23.75L16.7128 21.875V18.125Z"/></svg><span>ChatGPT</span>';
    button.addEventListener('click',()=>window.dispatchEvent(new Event('igorek:open-chatgpt-tab')));
    const controls=document.createElement('div');controls.id='igorek-chatgpt-controls';controls.hidden=true;controls.setAttribute('aria-label','Навигация ChatGPT');
    const title=document.createElement('span');title.textContent='ChatGPT';controls.append(title);
    for(const [operation,label,svg] of [['back','Назад в ChatGPT','<path d="m14 6-6 6 6 6"/>'],['forward','Вперёд в ChatGPT','<path d="m10 6 6 6-6 6"/>'],['reload','Обновить ChatGPT','<path d="M19 8a8 8 0 1 0 1 7M19 3v6h-6"/>'],['hide','Вернуться к Игорьку','<path d="m6 6 12 12M18 6 6 18"/>']]){
      const control=document.createElement('button');control.type='button';control.dataset.chatgptAction=operation;control.title=label;control.setAttribute('aria-label',label);control.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true">'+svg+'</svg>';
      control.addEventListener('click',()=>window.IgorekDesktop.chatgptNavigate(operation).catch(()=>{}));controls.append(control);
    }
    bar.append(controls);
    window.IgorekDesktop.onChatGPTState?.(value=>{button.classList.toggle('active',value.visible===true);button.setAttribute('aria-pressed',String(value.visible===true));controls.hidden=value.visible!==true;controls.querySelector('[data-chatgpt-action=back]').disabled=!value.can_back;controls.querySelector('[data-chatgpt-action=forward]').disabled=!value.can_forward;});rail.append(button);
  }
  if(rail&&window.IgorekDesktop?.opencode&&!document.getElementById('igorek-open-opencode')){
    const button=document.createElement('button');button.type='button';button.id='igorek-open-opencode';button.className='rail-button opencode-rail-button';button.title='OpenCode';button.setAttribute('aria-label','OpenCode');button.setAttribute('aria-pressed','false');
    button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h18v16H3zM7 8v8m4-8h6v8h-6z"/></svg><span>OpenCode</span>';
    button.addEventListener('click',()=>window.dispatchEvent(new Event('igorek:open-opencode-tab')));rail.append(button);
  }
  if (rail && window.IgorekDesktop?.browser && !document.getElementById('igorek-open-browser')) {
    const browser = document.createElement('button'); browser.type = 'button'; browser.id = 'igorek-open-browser';
    browser.className = 'rail-button'; browser.title = 'Браузер · Ctrl+Shift+B'; browser.setAttribute('aria-label', 'Браузер');
    browser.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/></svg>';
    browser.addEventListener('click', () => window.IgorekDesktop.browser().catch(() => {})); rail.append(browser);
  }
  if(rail&&window.IgorekDesktop?.browserResize){
    const handle=document.createElement('div');handle.id='igorek-browser-resizer';handle.tabIndex=0;
    handle.setAttribute('role','separator');handle.setAttribute('aria-orientation','vertical');handle.setAttribute('aria-label','Ширина браузера');handle.setAttribute('aria-valuemin','320');handle.title='Потяни, чтобы изменить ширину браузера';
    let dragging=false;
    const resize=(phase,x=0)=>window.IgorekDesktop.browserResize(phase,x).catch(()=>{});
    handle.addEventListener('pointerdown',event=>{if(event.button!==0)return;event.preventDefault();dragging=true;handle.setPointerCapture(event.pointerId);document.body.classList.add('igorek-browser-resizing');resize('start',event.screenX);});
    handle.addEventListener('pointermove',event=>{if(dragging)resize('move',event.screenX);});
    const finish=event=>{if(!dragging)return;dragging=false;document.body.classList.remove('igorek-browser-resizing');if(handle.hasPointerCapture(event.pointerId))handle.releasePointerCapture(event.pointerId);resize('end',event.screenX);};
    handle.addEventListener('pointerup',finish);handle.addEventListener('pointercancel',finish);handle.addEventListener('lostpointercapture',finish);
    handle.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();resize(event.key==='ArrowLeft'?'grow':'shrink');}});
    handle.addEventListener('dblclick',()=>resize('reset'));
    document.body.append(handle);
  }
  for (const [action, label, icon] of [['minimize', 'Свернуть окно', '−'], ['maximize', 'Развернуть или восстановить окно', '□'], ['close', 'Закрыть окно', '×']]) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'igorek-window-control';
    button.textContent = icon; button.title = label; button.setAttribute('aria-label', label);
    button.addEventListener('click', () => window.IgorekWindow.control(action).catch(() => {})); bar.append(button);
  }
  space.addEventListener('dblclick', () => window.IgorekWindow.control('maximize').catch(() => {}));
  document.body.classList.add('igorek-window-framed'); document.body.prepend(bar);
})();
