(() => {
  const $ = id => document.getElementById(id);
  let snapshot, editing, candidate, busy = false;
  const icons = { edit:'<path d="m4 16 11-11 4 4L8 20H4Zm10-10 4 4"/>', remove:'<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6m4-6v6"/>' };
  const label = key => key.replaceAll('CmdOrCtrl', snapshot.platform === 'darwin' ? '⌘' : 'Ctrl').replaceAll('Super', snapshot.platform === 'darwin' ? '⌘' : 'Super');
  const icon = (kind, title, click) => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'icon ' + kind;
    button.title = title; button.setAttribute('aria-label', title);
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + icons[kind] + '</svg>';
    button.addEventListener('click', click); return button;
  };
  async function change(id, keys) {
    if (busy) return false;
    busy = true;
    try { snapshot = await window.IgorekShortcuts.set(id, keys); $('error').textContent = ''; render(); return true; }
    catch (error) { $('error').textContent = error.message; $('capture-error').textContent = error.message; return false; }
    finally { busy = false; }
  }
  function capture(item, index) {
    editing = { item, index }; candidate = null; $('captured').textContent = 'Ожидание клавиш…';
    $('save').disabled = true; $('capture-error').textContent = ''; $('capture-title').textContent = item.title;
    $('capture').showModal();
  }
  function render() {
    const query = $('search').value.trim().toLocaleLowerCase('ru');
    const items = snapshot.items.filter(item => [item.title, item.description, ...item.keys.map(label)].join(' ').toLocaleLowerCase('ru').includes(query));
    $('list').replaceChildren(); $('empty').hidden = items.length > 0; $('list').hidden = items.length === 0;
    for (const item of items) {
      const row = document.createElement('div'); row.className = 'shortcut-row'; row.dataset.action = item.id;
      const copy = document.createElement('div'); copy.className = 'shortcut-copy';
      const title = document.createElement('strong'); title.textContent = item.title;
      const description = document.createElement('p'); description.textContent = item.description; copy.append(title, description);
      const bindings = document.createElement('div'); bindings.className = 'bindings';
      for (const [index, key] of (item.keys.length ? item.keys : [null]).entries()) {
        const binding = document.createElement('div'); binding.className = 'binding';
        const chip = document.createElement(key ? 'kbd' : 'span'); chip.textContent = key ? label(key) : 'Не назначено'; if (!key) chip.className = 'unassigned';
        binding.append(chip, icon('edit', 'Изменить: ' + item.title + (key ? ' · ' + label(key) : ''), () => capture(item, index)));
        if (key) binding.append(icon('remove', 'Удалить сочетание: ' + item.title + ' · ' + label(key), () => change(item.id, item.keys.filter((_, position) => position !== index))));
        bindings.append(binding);
      }
      if (item.keys.length > 0 && item.keys.length < 4) {
        const add = document.createElement('button'); add.type = 'button'; add.className = 'add'; add.textContent = '+ Добавить сочетание';
        add.setAttribute('aria-label', 'Добавить сочетание: ' + item.title); add.addEventListener('click', () => capture(item, item.keys.length)); bindings.append(add);
      }
      row.append(copy, bindings); $('list').append(row);
    }
  }
  $('search').addEventListener('input', render);
  $('cancel').addEventListener('click', () => $('capture').close());
  $('capture').addEventListener('click', event => { if (event.target === $('capture')) $('capture').close(); });
  $('capture').addEventListener('keydown', event => {
    if (event.key === 'Escape') return;
    if (!event.ctrlKey && !event.metaKey && !event.altKey && (event.key === 'Tab' || (event.target.tagName === 'BUTTON' && ['Enter', ' '].includes(event.key)))) return;
    event.preventDefault(); event.stopPropagation();
    if (event.repeat || event.isComposing || ['Control','Shift','Alt','Meta'].includes(event.key)) return;
    const named = { Backquote:'`', Comma:',', Period:'.', Semicolon:';', Slash:'/', BracketLeft:'[', BracketRight:']', Minus:'-', Equal:'=', Space:'Space', ArrowUp:'Up', ArrowDown:'Down', ArrowLeft:'Left', ArrowRight:'Right' };
    const key = /^Key[A-Z]$/.test(event.code) ? event.code.slice(3) : /^Digit\d$/.test(event.code) ? event.code.slice(5) : named[event.code] || event.key;
    const modifiers = []; if (event.ctrlKey || (event.metaKey && snapshot.platform === 'darwin')) modifiers.push('CmdOrCtrl');
    if (event.metaKey && snapshot.platform !== 'darwin') modifiers.push('Super'); if (event.altKey) modifiers.push('Alt'); if (event.shiftKey) modifiers.push('Shift');
    candidate = [...modifiers, key].join('+'); $('captured').textContent = label(candidate); $('save').disabled = false; $('capture-error').textContent = '';
  });
  $('save').addEventListener('click', async () => {
    if (!candidate || busy) return;
    const keys = [...editing.item.keys]; keys[editing.index] = candidate; $('save').disabled = true;
    if (await change(editing.item.id, keys)) $('capture').close(); else $('save').disabled = false;
  });
  $('reset').addEventListener('click', () => $('reset-confirm').showModal());
  $('reset-cancel').addEventListener('click', () => $('reset-confirm').close());
  $('reset-save').addEventListener('click', async () => {
    if (busy) return; busy = true; $('reset-save').disabled = true;
    try { snapshot = await window.IgorekShortcuts.reset(); $('error').textContent = ''; $('reset-confirm').close(); render(); }
    catch (error) { $('error').textContent = error.message; }
    finally { busy = false; $('reset-save').disabled = false; }
  });
  window.IgorekShortcuts.list().then(value => { snapshot = value; render(); }).catch(error => { $('error').textContent = error.message; });
})();
