/* Bundled reactions: stable text tokens persist through ordinary chat history. */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.IgorekEmoji = api;
})(typeof globalThis === 'object' ? globalThis : this, function (root) {
  let entries = new Map();
  const idPattern = /^pepe_[a-z_]{1,32}$/;
  const text = (ru, en) => root.IgorekI18n?.lang === 'en' ? en : ru;
  function install(value) {
    if (!value || value.schema_version !== 1 || !Array.isArray(value.emojis) || !value.emojis.length || value.emojis.length > 64) throw Error('Invalid emoji catalog');
    const next = new Map();
    for (const row of value.emojis) {
      if (!row || typeof row.id !== 'string' || !idPattern.test(row.id) || next.has(row.id) || row.file !== 'pepe/' + row.id + '.gif' || typeof row.label !== 'string' || row.label.length > 80) throw Error('Invalid emoji entry');
      next.set(row.id, {...row});
    }
    entries = next;
    return list();
  }
  function list() { return Array.from(entries.values(), row => ({...row})); }
  function match(value, offset = 0) {
    const found = /^:(pepe_[a-z_]{1,32}):/.exec(value.slice(offset));
    return found && entries.has(found[1]) ? {id:found[1],token:found[0]} : null;
  }
  function label(row) { return root.IgorekI18n?.lang === 'en' ? row.label : row.label_ru || row.label; }
  function image(id, doc = root.document) {
    const row = entries.get(id); if (!row) return null;
    const img = doc.createElement('img'); img.className = 'pepe-emoji'; img.src = '/emoji/' + row.file;
    img.alt = ':' + id + ':'; img.title = label(row); img.loading = 'lazy'; img.decoding = 'async';
    img.width = 28; img.height = 28; return img;
  }
  function fragment(value, doc = root.document) {
    const out = doc.createDocumentFragment(); let plain = '';
    for (let i = 0; i < value.length;) {
      const found = value[i] === ':' && match(value, i);
      if (!found) { plain += value[i++]; continue; }
      if (plain) { out.append(doc.createTextNode(plain)); plain = ''; }
      out.append(image(found.id, doc)); i += found.token.length;
    }
    if (plain) out.append(doc.createTextNode(plain)); return out;
  }
  const isTool = name => /(?:^|__|\.)render_emoji$/.test(String(name || ''));
  function decode(item) {
    if (!isTool(item.name)) return null;
    try {
      const result = typeof item.result === 'string' ? JSON.parse(item.result) : item.result;
      return result?.ok === true && result.kind === 'igorek.emoji' && result.schema_version === 1 && entries.has(result.emoji_id) ? result.emoji_id : null;
    } catch (_) { return null; }
  }
  function renderTool(item) {
    const card = root.document.createElement('div'); card.className = 'emoji-reaction';
    card.updateEmoji = next => {
      const id = decode(next); card.replaceChildren();
      if (id) card.append(image(id)); else card.textContent = next.status === 'working' ? text('Подбирает реакцию…','Choosing a reaction…') : text('Эмодзи недоступен','Emoji unavailable');
    };
    card.updateEmoji(item); return card;
  }
  function insertToken(message, id) {
    if (!entries.has(id)) return false;
    const start = message.selectionStart ?? message.value.length, end = message.selectionEnd ?? start;
    const token = ':' + id + ':';
    message.setRangeText(token, start, end, 'end');
    message.dispatchEvent(new root.Event('input', {bubbles:true})); message.focus(); return true;
  }
  function mountComposer() {
    const anchor = root.document.getElementById('attach-button'), message = root.document.getElementById('message-input');
    if (!anchor || !message || root.document.getElementById('emoji-button')) return;
    const button = root.document.createElement('button'); button.type = 'button'; button.id = 'emoji-button'; button.className = 'attach-button emoji-button';
    button.textContent = '☺'; button.title = text('Эмодзи Пепе','Pepe emoji'); button.setAttribute('aria-label', button.title);
    const picker = root.document.createElement('div'); picker.className = 'emoji-picker'; picker.setAttribute('popover','auto'); picker.id = 'emoji-picker';
    picker.setAttribute('role','dialog'); picker.setAttribute('aria-label', button.title);
    button.setAttribute('popovertarget',picker.id); button.setAttribute('aria-controls',picker.id);
    const title = root.document.createElement('strong'); title.textContent = 'Pepe'; picker.append(title);
    const grid = root.document.createElement('div'); grid.className = 'emoji-picker-grid';
    for (const row of entries.values()) {
      const choice = root.document.createElement('button'); choice.type = 'button'; choice.title = label(row); choice.setAttribute('aria-label',label(row));
      choice.append(image(row.id)); choice.onclick = () => { insertToken(message,row.id); picker.hidePopover?.(); }; grid.append(choice);
    }
    picker.append(grid); anchor.after(button); root.document.body.append(picker);
    button.onclick = () => {
      const rect = button.getBoundingClientRect(); picker.style.left = Math.min(rect.left,Math.max(8,root.innerWidth-280))+'px';
      picker.style.bottom = Math.max(8,root.innerHeight-rect.top+8)+'px';
    };
  }
  async function load() {
    const response = await root.fetch('/emoji/catalog.json',{credentials:'same-origin'});
    if (!response.ok) throw Error('Emoji catalog unavailable');
    install(await response.json()); mountComposer(); root.dispatchEvent(new root.Event('igorek-emoji-ready')); return list();
  }
  if (root.document) {
    const start = () => { load().catch(() => {}); };
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded',start,{once:true}); else start();
  }
  return {install,list,match,image,fragment,isTool,decode,renderTool,insertToken,load};
});
