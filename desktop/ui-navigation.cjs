const { existsSync, readFileSync } = require('node:fs');
const { join } = require('node:path');

const bundled = join(__dirname, 'runtime/packages/factory/web_ui/static/navigation.json');
const source = join(__dirname, '../factory/web_ui/static/navigation.json');
const catalog = Object.freeze(JSON.parse(readFileSync(existsSync(bundled) ? bundled : source, 'utf8')).map(Object.freeze));
const routes = new Map(catalog.map(route => [route.id, route]));
const settingsPanels = new Set(['plugins', ...catalog.filter(route => route.kind === 'settings').map(route => route.target)]);
function findRoute(id) {
  return typeof id === 'string' && /^[a-z][a-z0-9-]*$/.test(id) ? routes.get(id) : undefined;
}

module.exports = { catalog, findRoute, settingsPanels };
