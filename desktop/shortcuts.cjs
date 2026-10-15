const fs = require('node:fs');
const path = require('node:path');

const SHORTCUTS = [
  ['newSession', 'Новый чат', 'Начать новый чат с выбранной моделью.', ['CmdOrCtrl+N', 'CmdOrCtrl+K']],
  ['newWindow', 'Новое окно', 'Открыть ещё одно окно Игорька.', ['CmdOrCtrl+Shift+N']],
  ['openFolder', 'Открыть папку', 'Выбрать рабочую папку для нового чата.', ['CmdOrCtrl+O']],
  ['settings', 'Настройки', 'Открыть настройки приложения и хранилища.', ['CmdOrCtrl+,']],
  ['sidebar', 'Переключить боковую панель', 'Скрыть или показать проекты и чаты, освобождая место для рабочей области.', ['CmdOrCtrl+Shift+S']],
  ['panel', 'Переключить панель проверки', 'Показать или скрыть изменения, файлы и версии файлов.', ['CmdOrCtrl+J']],
  ['terminal', 'Открыть терминал', 'Открыть вкладку терминала текущего чата.', ['CmdOrCtrl+`']],
  ['browser', 'Браузер', 'Показать или скрыть встроенный браузер.', ['CmdOrCtrl+Shift+B']],
  ['focusComposer', 'Фокус на вводе', 'Переместить курсор в поле сообщения текущего чата.', ['Alt+L']],
  ['sendMessage', 'Отправить сообщение', 'Отправить сообщение, когда курсор находится в поле ввода чата.', ['CmdOrCtrl+Enter']],
  ['previousChat', 'Предыдущий чат', 'Перейти к предыдущему чату в списке недавних.', ['CmdOrCtrl+PageUp']],
  ['nextChat', 'Следующий чат', 'Перейти к следующему чату в списке недавних.', ['CmdOrCtrl+PageDown']],
  ['pinChat', 'Закрепить или открепить чат', 'Переключить закрепление выбранного чата.', ['CmdOrCtrl+Alt+P']],
  ...Array.from({ length: 9 }, (_, index) => [`recentChat${index + 1}`, `Недавний чат ${index + 1}`, `Открыть чат на позиции ${index + 1} по времени последнего обновления.`, [`CmdOrCtrl+Alt+${index + 1}`]]),
  ['reload', 'Обновить окно', 'Перезагрузить содержимое текущего окна.', ['CmdOrCtrl+R']],
  ['fullscreen', 'Полноэкранный режим', 'Развернуть окно на весь экран или вернуться обратно.', ['F11']],
].map(([id, title, description, defaults]) => Object.freeze({ id, title, description, defaults: Object.freeze(defaults) }));

function normalizeAccelerator(value) {
  if (typeof value !== 'string' || value.length > 80) throw Error('Некорректное сочетание.');
  const parts = value.split('+');
  const key = parts.pop();
  const modifiers = parts.map(part => part === 'Ctrl' || part === 'Command' ? 'CmdOrCtrl' : part);
  if (modifiers.some(part => !['CmdOrCtrl', 'Super', 'Alt', 'Shift'].includes(part)) || new Set(modifiers).size !== modifiers.length)
    throw Error('Некорректные клавиши-модификаторы.');
  if (!/^(?:[A-Z0-9]|F(?:[1-9]|1[0-2])|PageUp|PageDown|Home|End|Up|Down|Left|Right|Space|Tab|Enter|Backspace|Delete|,|`|\[|\]|;|\/|\.|-|=)$/.test(key || ''))
    throw Error('Эта клавиша не поддерживается.');
  if (!modifiers.some(part => ['CmdOrCtrl', 'Super', 'Alt'].includes(part)) && !/^F\d+$/.test(key))
    throw Error('Добавь Ctrl, Alt или Super, чтобы сочетание не мешало вводу текста.');
  const normalized = ['CmdOrCtrl', 'Super', 'Alt', 'Shift'].filter(part => modifiers.includes(part)).concat(key).join('+');
  if (['CmdOrCtrl+C', 'CmdOrCtrl+V', 'CmdOrCtrl+X', 'CmdOrCtrl+A', 'CmdOrCtrl+Z', 'CmdOrCtrl+Shift+Z', 'CmdOrCtrl+Y', 'CmdOrCtrl+W', 'CmdOrCtrl+Q', 'Alt+F4', 'Super+Q'].includes(normalized))
    throw Error('Это стандартное сочетание редактирования или закрытия окна.');
  return normalized;
}

class ShortcutStore {
  constructor(file) {
    this.file = file;
    this.overrides = {};
    try {
      const value = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (value && !Array.isArray(value) && typeof value === 'object') {
        const candidate = {};
        for (const item of SHORTCUTS) if (Object.hasOwn(value, item.id)) candidate[item.id] = this.validateKeys(value[item.id]);
        this.assertUnique(candidate);
        this.overrides = candidate;
      }
    } catch { }
  }
  validateKeys(keys) {
    if (!Array.isArray(keys) || keys.length > 4) throw Error('Можно назначить не больше четырёх сочетаний.');
    const values = keys.map(normalizeAccelerator);
    if (new Set(values).size !== values.length) throw Error('Это сочетание уже добавлено.');
    return values;
  }
  keys(id, overrides = this.overrides) {
    const item = SHORTCUTS.find(item => item.id === id);
    if (!item) throw Error('Неизвестное действие.');
    return Object.hasOwn(overrides, id) ? overrides[id] : [...item.defaults];
  }
  assertUnique(overrides) {
    const used = new Map();
    for (const item of SHORTCUTS) for (const key of this.keys(item.id, overrides)) {
      if (used.has(key)) throw Error(`Это сочетание уже назначено: ${used.get(key)}.`);
      used.set(key, item.title);
    }
  }
  snapshot() {
    return { platform: process.platform, items: SHORTCUTS.map(item => ({ ...item, keys: this.keys(item.id) })) };
  }
  write(overrides) {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const temporary = this.file + '.tmp';
    fs.writeFileSync(temporary, JSON.stringify(overrides), { mode: 0o600 });
    fs.renameSync(temporary, this.file);
    this.overrides = overrides;
    return this.snapshot();
  }
  set(id, keys) {
    this.keys(id);
    const candidate = { ...this.overrides, [id]: this.validateKeys(keys) };
    this.assertUnique(candidate);
    return this.write(candidate);
  }
  reset() { return this.write({}); }
}

function matchesShortcut(input, keys, platform=process.platform) {
  if(input.type!=='keyDown'||input.isAutoRepeat)return false;
  const named={Backquote:'`',Comma:',',Period:'.',Semicolon:';',Slash:'/',BracketLeft:'[',BracketRight:']',Minus:'-',Equal:'=',Space:'Space',ArrowUp:'Up',ArrowDown:'Down',ArrowLeft:'Left',ArrowRight:'Right'};
  const key=/^Key[A-Z]$/.test(input.code||'')?input.code.slice(3):/^Digit\d$/.test(input.code||'')?input.code.slice(5):named[input.code]||input.key;
  return keys.some(value=>{const parts=value.split('+'),expected=parts.pop();return expected===key&&Boolean(input.control)===(parts.includes('CmdOrCtrl')&&platform!=='darwin')&&Boolean(input.meta)===(parts.includes('Super')||(parts.includes('CmdOrCtrl')&&platform==='darwin'))&&Boolean(input.alt)===parts.includes('Alt')&&Boolean(input.shift)===parts.includes('Shift')});
}
module.exports = { SHORTCUTS, normalizeAccelerator, ShortcutStore, matchesShortcut };
