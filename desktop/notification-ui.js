(() => {
  const bridge = window.IgorekNotifications, rail = document.querySelector('.app-rail');
  if (!bridge || !rail || document.getElementById('igorek-notifications-button')) return;
  const make = (tag, cls, text) => { const node = document.createElement(tag); if (cls) node.className = cls; if (text) node.textContent = text; return node; };
  const button = make('button', 'rail-button'); button.id = 'igorek-notifications-button'; button.type = 'button';
  button.title = 'Центр уведомлений'; button.setAttribute('aria-label', 'Центр уведомлений');
  button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', 'igorek-notifications-panel');
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>';
  const badge = make('span', 'igorek-notification-badge'); badge.hidden = true; button.append(badge); rail.append(button);
  const panel = make('section'); panel.id = 'igorek-notifications-panel'; panel.hidden = true;
  panel.setAttribute('aria-label', 'Центр уведомлений');
  const header = make('header'), title = make('h2', '', 'Уведомления'), close = make('button', '', '×');
  close.type = 'button'; close.setAttribute('aria-label', 'Закрыть уведомления'); header.append(title, close);
  const actions = make('div', 'igorek-notification-actions'), read = make('button', '', 'Прочитать все'), clear = make('button', '', 'Очистить');
  read.type = clear.type = 'button'; actions.append(read, clear);
  const support = make('button', '', 'Отправить диагностику'); support.type='button'; support.id='igorek-notification-support';
  support.addEventListener('click',()=>window.IgorekDiagnosticSupport?.open()); actions.append(support);
  const list = make('div', 'igorek-notification-list'); panel.append(header, actions, list); document.body.append(panel);
  let items = [], expanded = new Set();
  const update = values => {
    items = Array.isArray(values) ? values : [];
    const unread = items.filter(item => !item.read).length;
    badge.hidden = !unread; badge.textContent = unread > 99 ? '99+' : String(unread);
    button.setAttribute('aria-label', 'Центр уведомлений' + (unread ? ' · непрочитанных: ' + unread : ''));
    list.replaceChildren(); read.disabled = !unread; clear.disabled = !items.length;
    if (!items.length) list.append(make('p', 'igorek-notification-empty', 'Пока нет уведомлений. Ошибки приложения и агента появятся здесь.'));
    for (const item of items) {
      const row = make('details', 'igorek-notification-row' + (item.read ? '' : ' unread'));
      row.dataset.id = item.id;
      row.open = expanded.has(item.id);
      const summary = make('summary'), dot = make('span', 'igorek-notification-dot ' + item.level);
      const text = make('div', 'igorek-notification-heading');
      text.append(make('strong', '', item.source || 'Приложение'), make('span', 'igorek-notification-preview', item.detail));
      const time = make('time', '', new Date(item.time).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }));
      time.dateTime = new Date(item.time).toISOString();
      summary.append(dot, text, time);
      if (item.count > 1) summary.append(make('span', 'igorek-notification-count', '×' + item.count));
      const detail = make('pre', 'igorek-notification-detail', item.detail);
      if (item.diagnostic) {
        detail.textContent += '\n\n' + (item.recoveryProof === 'operation_confirmed' ? 'Принятие подтверждено' : ({observed:'Ошибка зарегистрирована',recovering:'Восстановление соединения',needs_owner:'Требует внимания',recovered:'Восстановлено'}[item.state] || 'Ошибка зарегистрирована')) +
          '\nКод: '+item.diagnostic.code+'\nНаблюдений: '+item.count+'\nИнцидент: '+item.id+
          (item.diagnostic.http_request_id ? '\nЗапрос: '+item.diagnostic.http_request_id : '')+
          (item.diagnostic.attempt ? '\nПопытка: '+item.diagnostic.attempt : '')+
          (item.firstSeen ? '\nПервое наблюдение: '+new Date(item.firstSeen).toLocaleString('ru-RU') : '')+
          (item.recoveredAt ? '\nПодтверждение: '+new Date(item.recoveredAt).toLocaleString('ru-RU') : '')+
          (item.diagnostic.next_retry_at_ms ? '\nСледующая попытка: '+new Date(item.diagnostic.next_retry_at_ms).toLocaleString('ru-RU') : '');
        dot.className='igorek-notification-dot '+(item.state==='recovered'?'info':item.level);
      }
      row.append(summary, detail); row.addEventListener('toggle', () => row.open ? expanded.add(item.id) : expanded.delete(item.id)); list.append(row);
    }
  };
  const hide = () => { panel.hidden = true; button.setAttribute('aria-expanded', 'false'); button.classList.remove('active'); };
  button.addEventListener('click', () => {
    if (!panel.hidden) { hide(); return; }
    panel.hidden = false; button.setAttribute('aria-expanded', 'true'); button.classList.add('active');
    bridge.list().then(update).catch(() => {});
  });
  close.addEventListener('click', () => { hide(); button.focus(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { hide(); button.focus(); } });
  read.addEventListener('click', () => bridge.read().then(update).catch(() => {}));
  clear.addEventListener('click', () => { expanded.clear(); bridge.clear().then(update).catch(() => {}); });
  bridge.onChange(update); bridge.list().then(update).catch(() => {});
})();
