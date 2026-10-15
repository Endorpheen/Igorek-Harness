(() => {
  const transport = window.IgorekDesktop?.mcp || (async (action, fields = {}) => {
    if (action === 'settings') {
      byId('composer-connect-note').textContent = 'Права MCP для mobile/web настраиваются в хранилище desktop: выберите среду «Mobile / Web этого ПК».';
      menu.hidden = false; return {opened:true};
    }
    const response = await fetch('/api/mcp', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action,fields})});
    if (!response.ok) return {error:response.status===401 ? 'vault_locked' : 'mcp_unavailable'};
    return response.json();
  });
  let pending = Promise.resolve();
  const bridge = (action, fields) => {
    const next = pending.then(() => transport(action, fields));
    pending = next.catch(() => {});
    return next;
  };
  const byId = id => document.getElementById(id);
  const menu = byId('composer-connect-menu'), rows = byId('composer-connect-rows');
  const chip = byId('composer-mcp-chip'), panel = byId('composer-mcp-task');
  const status = byId('composer-mcp-status'), result = byId('composer-mcp-result');
  let selected = null, generation = 0, running = false;
  let approvalId = null;
  const approval = document.createElement('div'); approval.className='mcp-approval';approval.hidden=true;panel.append(approval);
  function showApproval(value) {
    if(!value){approval.hidden=true;approval.replaceChildren();approvalId=null;return;}
    if(approvalId===value.request_id)return;
    approvalId=value.request_id;approval.hidden=false;approval.replaceChildren();
    const title=document.createElement('strong');title.textContent=value.operation==='move'?'Перенести выбранное событие?':'Создать событие?';
    const details=document.createElement('p');
    const format=entry=>entry?.date || (entry?.dateTime ? new Date(entry.dateTime).toLocaleString(typeof intlLocale==='function'?intlLocale():'ru-RU',{timeZone:value.time_zone,dateStyle:'medium',timeStyle:'short'}) : '—');
    details.textContent=(value.summary||'Без названия')+'\n'+format(value.start)+' — '+format(value.end)+'\nЧасовой пояс: '+value.time_zone;
    if(value.start?.date)details.textContent+='\nНа весь день; конечная дата не входит в событие.';
    if(value.location)details.textContent+='\nМесто: '+value.location;
    if(value.description)details.textContent+='\nОписание: '+value.description;
    if(value.operation==='move')details.textContent+='\nБыло: '+format(value.previous_start)+' — '+format(value.previous_end)+'\nУчастники сохраняются. Новые приглашения не отправляются.';
    if(value.attendees?.length)details.textContent+='\nПриглашения будут отправлены: '+value.attendees.join(', ');
    approval.append(title,details);
    const current=generation;
    for(const [decision,label] of [['approve',value.attendees?.length?'Создать и отправить приглашения':'Подтвердить'],['reject','Отказаться']]){
      const button=document.createElement('button');button.type='button';button.textContent=label;
      button.addEventListener('click',async()=>{
        for(const node of approval.querySelectorAll('button'))node.disabled=true;
        try{const reply=await bridge('approve',{request_id:value.request_id,decision});if(current!==generation)return;if(reply.error){status.textContent=message(reply);showApproval(null);}else showApproval(reply.job?.approval);}
        catch{if(current===generation){status.textContent='Не удалось передать решение. Повторите после обновления состояния.';showApproval(null);}}
      });approval.append(button);
    }
  }
  const errors = { vault_locked:'Откройте хранилище в настройках.', interface_locked:'Разблокируйте настройки.',
    task_other_window:'MCP уже используется в другом окне.', task_replaced:'Задача заменена в настройках.',
    mcp_access_denied:'Доступ отозван. Проверьте разрешения MCP.', private_request_failed:'Запрос не завершён. Проверьте подключение и модель.' };
  function message(reply) {
    const failure = reply.job?.failure;
    if (reply.job?.error === 'private_request_failed' && failure) {
      const model = ['model_auth','model_request','model_response'].includes(failure.stage);
      const source = model ? 'Сервис модели' : 'Подключение MCP';
      const code = Number.isInteger(failure.http_status) && failure.http_status >= 100 && failure.http_status <= 599 ? ' (HTTP ' + failure.http_status + ')' : '';
      const reasons = {
        timeout: source + ' не ответил вовремя. Запрос остановлен; можно повторить.',
        network: 'Не удалось связаться: ' + source.toLowerCase() + '. Проверьте сеть и повторите запрос.',
        authentication: model ? 'Сервис модели отклонил авторизацию' + code + '. Проверьте ключ выбранного профиля.' : 'MCP отклонил авторизацию' + code + '. Повторите вход Google в настройках подключения.',
        rate_limit: source + ' ограничил частоту запросов или квоту' + code + '. Повторите позже.',
        http_error: source + ' отклонил запрос' + code + '.',
        invalid_response: source + ' вернул неподдерживаемый ответ. Запрос остановлен.',
        provider_error: 'Сервис модели вернул ошибку. Повторите запрос позже.',
        output_limit: 'Модель исчерпала лимит ответа и не завершила задачу.',
        turn_limit: 'Модель не завершила задачу за допустимое число обращений к MCP.',
        access_denied: 'Доступ к подключению отозван или изменился. Проверьте разрешения выбранного профиля.',
      };
      return Object.hasOwn(reasons, failure.reason) ? reasons[failure.reason] : 'Внутренняя ошибка обработки MCP. Запрос остановлен.';
    }
    return errors[reply.error || reply.job?.error] || 'Операция недоступна. Проверьте настройки MCP.';
  }
  function reset() {
    showApproval(null);
    generation++; selected = null; running = false;
    chip.hidden = panel.hidden = menu.hidden = true;
    result.textContent = status.textContent = '';
    bridge('clear').catch(() => {});
  }
  byId('composer-mcp-clear').addEventListener('click', reset);
  byId('composer-mcp-remove').addEventListener('click', reset);
  window.addEventListener('pagehide', reset);
  document.addEventListener('visibilitychange', () => { if (document.hidden) reset(); });
  window.addEventListener('igorek-session-changed', reset);
  byId('composer-connect-file').addEventListener('click', () => {
    menu.hidden = true; byId('attach-input').click();
  });
  byId('composer-connect-settings').addEventListener('click', () => {
    menu.hidden = true; bridge('settings').catch(() => {});
  });
  document.addEventListener('click', event => {
    if (!menu.contains(event.target) && event.target !== byId('attach-button')) menu.hidden = true;
  });
  byId('attach-button').addEventListener('click', async event => {
    event.preventDefault(); event.stopImmediatePropagation();
    menu.hidden = !menu.hidden;
    if (menu.hidden) return;
    const current = generation;
    rows.replaceChildren(); byId('composer-connect-note').textContent = 'Загружаю доступные подключения…';
    try {
      const reply = await bridge('list');
      if (current !== generation || menu.hidden) return;
      if (reply.error) { byId('composer-connect-note').textContent = message(reply); return; }
      byId('composer-connect-note').textContent = reply.entries.length ? 'Для этой задачи · разрешения задаются в хранилище' : 'Нет доступных подключений. Подключите аккаунт и разрешите доступ профилю в настройках.';
      for (const entry of reply.entries) {
        const button = document.createElement('button'); button.type = 'button';
        const label = ({gmail:'Gmail',openai_docs:'OpenAI Docs',google_calendar:'Google Календарь'}[entry.server] || entry.server) + (entry.readonly ? ' · только чтение' : '') + ' · ' + entry.profile + (entry.local ? ' · локально' : ' · удалённая модель');
        button.textContent = label;
        button.addEventListener('click', () => {
          reset(); selected = entry; chip.hidden = false;
          byId('composer-mcp-label').textContent = label;
          panel.hidden = false; status.textContent = 'Следующее сообщение отправится только выбранному MCP и профилю. Отдельный контекст, без общей истории.';
        });
        rows.append(button);
      }
    } catch { byId('composer-connect-note').textContent = 'Подключения сейчас недоступны.'; }
  }, true);
  byId('composer-form').addEventListener('submit', async event => {
    if (!selected) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const input = byId('message-input'), text = input.value.trim();
    if (running || !text) return;
    if (text.length > 1024) { status.textContent = 'Для MCP-задачи сократите запрос до 1024 символов.'; return; }
    if (!byId('composer-attachments').hidden) { status.textContent = 'Уберите вложения: в MCP-задаче используются только инструменты выбранного подключения.'; return; }
    const current = ++generation; running = true; panel.hidden = false; result.textContent = '';
    showApproval(null);
    status.textContent = 'Подключение MCP…';
    try {
      let reply = await bridge('start', {server:selected.server, credential_id:selected.credential_id, message:text});
      if (current !== generation) return;
      if (reply.error) { status.textContent = message(reply); return; }
      input.value = ''; input.dispatchEvent(new Event('input', {bubbles:true}));
      while (current === generation) {
        showApproval(reply.job?.approval);
        if (reply.error || reply.job?.error) { status.textContent = message(reply); return; }
        if (!reply.job.running) {
          if (typeof window.IgorekMarkdown?.renderMarkdown === 'function') {
            window.IgorekMarkdown.renderMarkdown(reply.job.result, result);
          } else result.textContent = reply.job.result;
          status.textContent = 'Готово · результат не добавлен в общую историю';
          return;
        }
        status.textContent = reply.job.phase || 'MCP выполняет задачу…';
        await new Promise(resolve => setTimeout(resolve, 700));
        if (current !== generation) return;
        reply = await bridge('status');
      }
    } catch { if (current === generation) status.textContent = 'Нет ответа от приложения. Проверьте подключение в настройках.'; }
    finally { if (current === generation) running = false; }
  }, true);
})();
