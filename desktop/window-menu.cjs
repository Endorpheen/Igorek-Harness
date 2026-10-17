const { SHORTCUTS } = require('./shortcuts.cjs');
function windowMenu(actions, tracing = false, bindings = id => SHORTCUTS.find(item => item.id === id).defaults) {
  const entry = (id, label) => {
    const keys = bindings(id);
    const items = [{ label, click: actions[id], ...(keys[0] ? { accelerator: keys[0] } : {}) }];
    for (const accelerator of keys.slice(1)) items.push({ label, click: actions[id], accelerator, visible: false, acceleratorWorksWhenHidden: true });
    return items;
  };
  return [
    { label: 'Файл', submenu: [
      ...entry('newWindow', 'Новое окно'),
      ...entry('newSession', 'Новая сессия'),
      ...entry('openFolder', 'Открыть папку…'),
      ...entry('browser', 'Браузер'),
      {label:'ChatGPT',click:actions.chatgpt},
      { type: 'separator' }, ...entry('settings', 'Настройки и хранилище'),
      { type: 'separator' }, { role: 'close', label: 'Закрыть окно' }, { role: 'quit', label: 'Выйти' },
    ] },
    { label: 'Правка', submenu: [
      { role: 'undo', label: 'Отменить' }, { role: 'redo', label: 'Повторить' }, { type: 'separator' },
      { role: 'cut', label: 'Вырезать' }, { role: 'copy', label: 'Копировать' }, { role: 'paste', label: 'Вставить' },
      { role: 'delete', label: 'Удалить' }, { type: 'separator' }, { role: 'selectAll', label: 'Выбрать всё' },
    ] },
    { label: 'Вид', submenu: [
      ...entry('sidebar', 'Переключить боковую панель'),
      ...entry('panel', 'Переключить панель проверки'),
      ...entry('terminal', 'Открыть терминал'),
      { type: 'separator' },
      ...entry('focusComposer', 'Фокус на вводе'),
      ...entry('sendMessage', 'Отправить сообщение'),
      ...entry('previousChat', 'Предыдущий чат'), ...entry('nextChat', 'Следующий чат'),
      ...entry('pinChat', 'Закрепить или открепить чат'),
      { label: 'Недавние чаты', submenu: Array.from({ length: 9 }, (_, index) => entry(`recentChat${index + 1}`, `Чат ${index + 1}`)).flat() },
      { type: 'separator' }, ...entry('reload', 'Обновить окно'),
      { role: 'zoomIn', label: 'Увеличить' }, { role: 'zoomOut', label: 'Уменьшить' },
      { role: 'resetZoom', label: 'Фактический размер' }, { type: 'separator' },
      ...entry('fullscreen', 'Полноэкранный режим'),
    ] },
    { label: 'Справка', submenu: [
      { label: 'Сочетания клавиш', click: actions.shortcuts },
      { label: 'Статус системы', click: actions.status },
      { label: 'Диспетчер задач', click: actions.tasks },
      { label: tracing ? 'Остановить и сохранить трассировку…' : 'Начать трассировку производительности', click: actions.trace },
      { type: 'separator' }, ...(actions.whatsNew?[{label:'Что нового',click:actions.whatsNew}]:[]), { label: 'О программе Игорёк', click: actions.about },
    ] },
  ];
}
module.exports = { windowMenu };
