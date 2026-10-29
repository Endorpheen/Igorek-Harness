(() => {
  'use strict';
  function create({host, tabId, call, onLayout = () => {}, lang = 'ru'}) {
    const en = lang === 'en';
    const node = (tag, text = '', className = '') => {
      const item = document.createElement(tag); item.textContent = text;
      if (className) item.className = className;
      return item;
    };
    const panel = node('section', '', 'workspace-browser-console'); panel.hidden = true;
    panel.setAttribute('aria-label', en ? 'Page console' : 'Консоль страницы');
    const header = node('header'), title = node('strong', en ? 'Console' : 'Консоль');
    const clear = node('button', en ? 'Clear' : 'Очистить'); clear.type = 'button';
    const close = node('button', '×'); close.type = 'button';
    close.setAttribute('aria-label', en ? 'Close console' : 'Закрыть консоль');
    header.append(title, clear, close);
    const output = node('div', '', 'browser-console-output'); output.setAttribute('role', 'log');
    const messages = node('div'), history = node('div'); output.append(messages, history);
    const form = node('form'), input = node('textarea'); input.rows = 2; input.spellcheck = false;
    input.placeholder = 'document.title'; input.setAttribute('aria-label', en ? 'JavaScript expression' : 'Выражение JavaScript');
    const run = node('button', en ? 'Run' : 'Выполнить'); run.type = 'submit';
    const status = node('p', '', 'browser-console-status'); status.setAttribute('role', 'status');
    form.append(input, run); panel.append(header, output, form, status); host.append(panel);
    let disposed = false, busy = false, polling = false, signature = '', epoch = null;
    const errors = {
      console_timeout: en ? 'Execution timed out.' : 'Превышено время выполнения.',
      console_busy: en ? 'An expression is already running.' : 'Выражение уже выполняется.',
      console_debugger_busy: en ? 'Close DevTools and retry.' : 'Закрой DevTools этой вкладки и повтори.',
      browser_changed: en ? 'The tab or page changed.' : 'Вкладка или страница изменилась.',
      browser_loading: en ? 'Wait for the page to load.' : 'Дождись загрузки страницы.',
      desktop_locked: en ? 'Unlock Igorek first.' : 'Разблокируй Игорька.',
      invalid_url: en ? 'Select a web tab.' : 'Выбери веб-вкладку.',
    };
    const failure = reply => reply?.exception?.message || reply?.message || errors[reply?.error]
      || (en ? 'Console operation failed.' : 'Действие консоли не выполнено.');
    const request = (action, fields = {}) => call(action, {tab_id: tabId, ...fields});
    function line(target, text, level = 'info') {
      const row = node('pre', text, 'browser-console-line'); row.dataset.level = level;
      target.append(row); while (target.children.length > 200) target.firstElementChild.remove();
    }
    async function poll() {
      if (disposed || panel.hidden || polling) return;
      polling = true;
      try {
        const reply = await request('console_messages', {limit: 200});
        if (disposed || panel.hidden) return;
        if (reply.error) { status.textContent = failure(reply); return; }
        if (epoch !== null && epoch !== reply.document_epoch) history.replaceChildren();
        epoch = reply.document_epoch;
        const next = JSON.stringify([reply.document_epoch, reply.entries, reply.dropped]);
        if (signature === next) return;
        signature = next; messages.replaceChildren();
        if (reply.dropped) line(messages, (en ? 'Older messages omitted: ' : 'Пропущено старых сообщений: ') + reply.dropped, 'warning');
        for (const entry of reply.entries || []) {
          const location = entry.sourceId ? '\n' + entry.sourceId + ':' + entry.lineNumber : '';
          line(messages, '[' + entry.level + '] ' + entry.message + location, entry.level);
        }
      } catch { if (!disposed) status.textContent = en ? 'No response from the browser.' : 'Нет ответа от браузера.'; }
      finally { polling = false; }
    }
    async function execute(event) {
      event?.preventDefault();
      const expression = input.value.trim();
      if (disposed || busy || !expression) return;
      busy = true; run.disabled = true; status.textContent = en ? 'Running…' : 'Выполняется…';
      line(history, '> ' + expression); input.value = '';
      try {
        const reply = await request('console', {expression});
        if (disposed) return;
        if (reply.error) line(history, failure(reply), 'error');
        else line(history, typeof reply.value === 'string' ? reply.value : JSON.stringify(reply.value, null, 2));
        status.textContent = ''; output.scrollTop = output.scrollHeight;
      } catch { if (!disposed) status.textContent = en ? 'No response from the browser.' : 'Нет ответа от браузера.'; }
      finally { busy = false; if (!disposed) run.disabled = false; }
    }
    form.addEventListener('submit', execute);
    input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) execute(event); });
    const toggle = () => {
      if (disposed) return;
      panel.hidden = !panel.hidden;
      onLayout();
      if (!panel.hidden) { input.focus(); poll(); }
    };
    close.addEventListener('click', () => { if (!panel.hidden) toggle(); });
    clear.addEventListener('click', async () => {
      try {
        const reply = await request('console_clear');
        if (disposed) return;
        if (reply.error) { status.textContent = failure(reply); return; }
        history.replaceChildren(); messages.replaceChildren(); signature = ''; status.textContent = '';
      } catch { if (!disposed) status.textContent = en ? 'Could not clear the console.' : 'Не удалось очистить консоль.'; }
    });
    const timer = setInterval(poll, 1000);
    return {toggle, destroy() { disposed = true; clearInterval(timer); panel.remove(); }};
  }
  window.IgorekBrowserConsole = Object.freeze({create});
})();
