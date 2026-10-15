const { test } = require('node:test');
const assert = require('node:assert/strict');
const { windowMenu } = require('../window-menu.cjs');
const { SHORTCUTS } = require('../shortcuts.cjs');
test('desktop menu exposes working actions and standard edit/window roles', () => {
  const invoked = [];
  const ids = [...SHORTCUTS.map(item => item.id),'chatgpt','shortcuts','status','tasks','trace','about'];
  const actions = Object.fromEntries(ids.map(key => [key, () => invoked.push(key)]));
  const menu = windowMenu(actions);
  assert.deepEqual(menu.map(item => item.label), ['Файл','Правка','Вид','Справка']);
  const walk = items => items.flatMap(item => [item, ...walk(item.submenu || [])]);
  for (const item of walk(menu)) if (item.click) item.click();
  assert.deepEqual(new Set(invoked), new Set(ids));
  assert.deepEqual(menu[1].submenu.filter(item => item.role).map(item => item.role), ['undo','redo','cut','copy','paste','delete','selectAll']);
  assert.equal(menu[0].submenu[0].accelerator, 'CmdOrCtrl+Shift+N');
  assert.equal(menu[3].submenu[3].label, 'Начать трассировку производительности');
  assert.equal(windowMenu(actions, true)[3].submenu[3].label, 'Остановить и сохранить трассировку…');
});
test('configured bindings drive native menu, aliases and removal without changing actions', () => {
  const menu = windowMenu({ sidebar: () => 'sidebar' }, false, id => id === 'sidebar' ? ['Alt+S','Alt+Shift+S'] : []);
  const items = menu[2].submenu.filter(item => item.label === 'Переключить боковую панель');
  assert.deepEqual(items.map(item => item.accelerator), ['Alt+S','Alt+Shift+S']);
  assert.equal(items[1].acceleratorWorksWhenHidden, true);
  assert.equal(items[0].click(), 'sidebar');
  assert.equal(menu[0].submenu[0].accelerator, undefined);
});
