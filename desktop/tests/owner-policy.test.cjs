const { test } = require('node:test');
const assert = require('node:assert/strict');
const { isOwnerFrame } = require('../owner-policy.cjs');
test('value editor clears sensitive inputs and rejects late results after navigation', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; activePanel='secrets'; window.igorekVault={saveValue:fields=>{globalThis.submitted={...fields}; return new Promise(resolve=>globalThis.finishValue=resolve)}}; openValueEditor()");
  ui.element('value-name').value = 'CUSTOM_TOKEN'; ui.element('value-kind').value = 'secret';
  ui.element('value-input').value = 'fixture-private'; ui.element('value-password').value = 'fixture-password';
  const submit = ui.element('value-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.element('value-input').value, ''); assert.equal(ui.element('value-password').value, '');
  assert.equal(ui.run('submitted.value'), 'fixture-private');
  assert.equal(ui.element('value-submit').disabled, true);
  ui.run('closeValueEditor()');
  ui.run("finishValue({result:{values:[{credential_id:'fixture',name:'CUSTOM_TOKEN',kind:'secret'}]}})");
  await submit;
  assert.equal(ui.element('vault-values').children.length, 0);
  assert.equal(ui.element('value-dialog').open, false);
});

test('values render metadata, use the separate reveal action and clear on blur', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; window.igorekVault={revealValue:async(id,password)=>({result:{secret:'fixture-value',expires_in_seconds:15}})}; renderValues([{credential_id:'fixture',name:'CUSTOM_TOKEN',kind:'secret'}])");
  const row = ui.element('vault-values').children[0];
  assert.equal(row.children[0].textContent, 'CUSTOM_TOKEN');
  assert.equal(row.children[2].children[0].textContent, 'Добавлено тобой');
  row.children[3].children[0].children[0].listeners.click();
  ui.element('secret-password').value = 'fixture-password';
  await ui.element('secret-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.element('secret-value').textContent, 'fixture-value');
  ui.windowEvents.blur();
  assert.equal(ui.element('secret-value').textContent, '');
  row.children[3].children[0].children[1].listeners.click();
  ui.element('value-input').value = 'fixture-value'; ui.element('value-password').value = 'fixture-password';
  ui.windowEvents.blur();
  assert.equal(ui.element('value-input').value, ''); assert.equal(ui.element('value-password').value, '');
  assert.equal(ui.element('value-dialog').open, false);
});

test('value password errors retain metadata while successful save and delete refresh the list', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; window.igorekVault={saveValue:async()=>({error:'authentication_failed'})}; openValueEditor()");
  ui.element('value-name').value = 'EMPTY_VAR'; ui.element('value-kind').value = 'variable';
  ui.element('value-password').value = 'wrong';
  await ui.element('value-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.element('value-name').value, 'EMPTY_VAR'); assert.equal(ui.element('value-password').value, '');
  assert.equal(ui.element('value-dialog').open, true); assert.equal(ui.element('value-submit').disabled, false);
  ui.run("window.igorekVault.saveValue=async()=>({result:{values:[{credential_id:'fixture',name:'EMPTY_VAR',kind:'variable'}]}})");
  ui.element('value-password').value = 'fixture-password';
  await ui.element('value-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.element('value-dialog').open, false);
  const row = ui.element('vault-values').children[0]; row.children[3].children[0].children[2].listeners.click();
  assert.equal(ui.element('value-fields').disabled, true);
  ui.run("window.igorekVault.deleteValue=async(id,password)=>{globalThis.deletedId=id; return {result:{values:[]}}}");
  ui.element('value-password').value = 'fixture-password';
  await ui.element('value-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.run('deletedId'), 'fixture'); assert.equal(ui.element('vault-values').children.length, 0);
});
test('only the exact local owner main frame receives management access', () => {
  const url = 'file:///prototype/owner.html';
  const mainFrame = { url };
  const contents = { mainFrame, isDestroyed: () => false };
  assert.equal(isOwnerFrame({ sender: contents, senderFrame: mainFrame }, contents, url), true);
  assert.equal(isOwnerFrame({ sender: {}, senderFrame: mainFrame }, contents, url), false);
  assert.equal(isOwnerFrame({ sender: contents, senderFrame: { url } }, contents, url), false);
  mainFrame.url = 'https://127.0.0.1/chat';
  assert.equal(isOwnerFrame({ sender: contents, senderFrame: mainFrame }, contents, url), false);
  mainFrame.url = url;
  assert.equal(isOwnerFrame({ sender: contents, senderFrame: mainFrame }, undefined, url), false);
  contents.isDestroyed = () => true;
  assert.equal(isOwnerFrame({ sender: contents, senderFrame: mainFrame }, contents, url), false);
});


test('only successful profile-state mutations trigger a chat refresh', () => {
  const { shouldRefreshProfiles } = require('../owner-policy.cjs');
  for (const action of ['start_agent', 'stop_agent', 'lock', 'unlock', 'update_profile']) {
    assert.equal(shouldRefreshProfiles(action, { result: {} }), true);
    assert.equal(shouldRefreshProfiles(action, { error: 'agent_busy' }), false);
    assert.equal(shouldRefreshProfiles(action, { result: {}, error: 'vault_locked' }), false);
  }
  for (const action of ['status', 'save_profile', 'list_profiles', 'export_recovery', 'check_profile'])
    assert.equal(shouldRefreshProfiles(action, { result: { secret: 'never-forwarded' } }), false);
});

function optionsHarness() {
  const { readFileSync } = require('node:fs');
  const { join } = require('node:path');
  const vm = require('node:vm');
  const source = readFileSync(join(__dirname, '..', '../factory/web_ui/static/app.js'), 'utf8');
  const start = source.indexOf('async function loadOptions() {');
  const end = source.indexOf('//', start);
  const pending = [], handlers = {};
  const profile = { options: [], value: '', replaceChildren() { this.options = []; }, append(option) { this.options.push(option); } };
  const context = { state: {}, ui: { profile, cwd: { value: '/chosen/workspace' }, sessionError: { textContent: '' } },
    button: { disabled: false }, document: { createElement: () => ({}) },
    window: { addEventListener: (name, handler) => { handlers[name] = handler; } },
    sessionCwdCombo: { setValue(value) { context.ui.cwd.value = value; }, render() {} },
    api: () => new Promise((resolve, reject) => pending.push({ resolve, reject })),
    byId: () => context.button };
  vm.createContext(context); vm.runInContext(source.slice(start, end), context);
  return { context, pending, handlers };
}

test('newest options win without replacing the chosen folder or a valid profile', async () => {
  const { context: c, pending } = optionsHarness();
  c.ui.profile.value = 'keep';
  const old = c.loadOptions(), latest = c.loadOptions();
  assert.equal(c.button.disabled, true);
  pending[1].resolve({ profiles: ['keep', 'new'], default_profile: 'new', default_cwd: '/default' });
  await latest;
  assert.equal(c.ui.profile.value, 'keep');
  assert.equal(c.ui.cwd.value, '/chosen/workspace');
  assert.equal(c.button.disabled, false);
  pending[0].resolve({ profiles: ['obsolete'], default_profile: 'obsolete' });
  await old;
  assert.deepEqual(c.ui.profile.options.map(o => o.value), ['keep', 'new']);
  assert.equal(c.ui.profile.value, 'keep');
});

test('stop removes stale profiles and empty or failed refresh disables creation', async () => {
  const { context: c, pending, handlers } = optionsHarness();
  assert.equal(typeof handlers['igorek:profiles-changed'], 'function');
  c.ui.profile.value = 'old';
  let update = c.loadOptions();
  pending[0].resolve({ profiles: ['new'], default_profile: 'new' });
  await update;
  assert.equal(c.ui.profile.value, 'new');
  update = c.loadOptions(); pending[1].resolve({ profiles: [], default_profile: '' }); await update;
  assert.equal(c.ui.profile.value, '');
  assert.equal(c.ui.profile.options.length, 0);
  assert.equal(c.button.disabled, true);
  update = c.loadOptions(); pending[2].reject(new Error('fixture network failure'));
  await assert.rejects(update);
  assert.equal(c.button.disabled, true);
});

const onboardingState = require('../onboarding-state.cjs');
const guideProfiles = [
  { credential_id: 'first', config: { name: 'first' }, status: 'verified' },
  { credential_id: 'second', config: { name: 'second' }, status: 'failed' },
];
test('onboarding requires actual vault, saved connection, verification and matching executor', () => {
  const idle = { running: false };
  assert.equal(onboardingState(undefined, [], idle).action, 'loading');
  for (const state of ['not_initialized', 'locked']) {
    const value = onboardingState(state, guideProfiles, { running: true, profile: 'first' });
    assert.equal(value.action, 'vault'); assert.equal(value.profile, undefined);
    assert.deepEqual(value.complete, [false, false, false, false, false]);
  }
  assert.equal(onboardingState('unlocked', [], idle).action, 'add');
  assert.equal(onboardingState('unlocked', guideProfiles, idle, 'second').action, 'check');
  assert.equal(onboardingState('unlocked', [{ ...guideProfiles[1], status: 'unverified' }], idle).action, 'check');
  assert.equal(onboardingState('unlocked', guideProfiles, idle, 'first').action, 'start');
  assert.equal(onboardingState('unlocked', guideProfiles, { running: true, profile: 'other' }, 'first').action, 'start');
  assert.deepEqual(onboardingState('unlocked', guideProfiles, { running: true, profile: 'first' }).complete, [true, true, true, true, false]);
});
test('onboarding preserves explicit selection and rejects stale verification assumptions', () => {
  const active = { running: true, profile: 'first' };
  assert.equal(onboardingState('unlocked', guideProfiles, active, 'second').action, 'check');
  assert.equal(onboardingState('unlocked', guideProfiles, active, 'missing').action, 'workspace');
  assert.equal(onboardingState('unlocked', [{ ...guideProfiles[0], status: 'failed' }], active).action, 'check');
});
test('first-run guidance reaches workspace and a real persisted result before completion', () => {
  const active = { running: true, profile: 'first' };
  const progress = (...tail) => onboardingState('unlocked', guideProfiles, active, 'first', 'ready', ...tail);
  assert.equal(progress([], false).action, 'workspace');
  assert.equal(progress([], true).action, 'first_task');
  assert.equal(progress(['second'], true).complete[4], false);
  assert.equal(progress(['first'], true).action, 'chat');
  assert.deepEqual(progress(['first'], true).complete, [true, true, true, true, true]);
  assert.equal(progress(['first'], true, 'unavailable').action, 'refresh');
  assert.equal(progress(['first'], true, 'unavailable').complete[4], false);
});

function recoveryUiHarness() {
  const vm = require('node:vm');
  const elements = new Map();
  const timers = new Map(), windowEvents = {}, documentEvents = {};
  let timerId = 0;
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', checked: false, dataset: {}, listeners: {},
      open: false, classList: { toggle() {} }, setAttribute() {}, showModal() { this.open = true; }, close() { this.open = false; this.listeners.close?.(); }, children: [], replaceChildren() { this.children = []; }, append(child) { this.children.push(child); },
      addEventListener(type, fn) { this.listeners[type] = fn; }, click() { return this.listeners.click?.(); }, reset() {}, focus() {}, scrollIntoView() {}, reportValidity() { return true; } });
    return elements.get(id);
  };
  const context = vm.createContext({
    URL,
    setTimeout: (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; }, clearTimeout: id => timers.delete(id),
    document: { hidden: false, addEventListener: (name, fn) => { documentEvents[name] = fn; }, getElementById: element,
      createElement: () => ({ children: [], dataset: {}, listeners: {}, classList: { toggle() {} }, setAttribute() {}, addEventListener(type, fn) { this.listeners[type] = fn; }, append(...children) { this.children.push(...children); } }), querySelectorAll: () => [] },
    window: { setInterval() { return 0; }, igorekOnboardingState: onboardingState, scrollTo() {}, addEventListener: (name, fn) => { const previous = windowEvents[name]; windowEvents[name] = previous ? (...args) => { previous(...args); fn(...args); } : fn; } },
  });
  context.window.IgorekProfileCards = require('../profile-cards.cjs');
  vm.runInContext(require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'owner-ui.js'), 'utf8').replace(/perform\('status'\);\s*$/, ''), context);
  return { element, timers, context, windowEvents, documentEvents, run: code => vm.runInContext(code, context), click: id => element(id).listeners.click() };
}
test('onboarding refresh preserves credential drafts and discards a reply after state changes', async () => {
  const ui = recoveryUiHarness();
  ui.run("state = 'unlocked'; interfaceLocked = false; activePanel = 'start'; profilesState = 'ready'; selectedProfileId = 'first'; el('refresh').disabled = false");
  ui.element('mcp-password').value = 'unsaved-fixture';
  ui.context.window.igorekVault = { status: async () => ({ result: {
    state: 'unlocked', onboarding: { state: 'ready', successful_profiles: ['first'] },
  } }) };
  await ui.run('refreshOnboardingProgress()');
  assert.equal(ui.element('mcp-password').value, 'unsaved-fixture');
  assert.deepEqual(Array.from(ui.run('successfulProfiles')), ['first']);
  let complete;
  ui.context.window.igorekVault.status = () => new Promise(resolve => { complete = resolve; });
  const pending = ui.run("selectedProfileId = 'first'; refreshOnboardingProgress()");
  ui.run("onboardingRevision += 1; successfulProfiles = []");
  complete({ result: { state: 'unlocked', onboarding: { state: 'ready', successful_profiles: ['first'] } } });
  await pending;
  assert.deepEqual(Array.from(ui.run('successfulProfiles')), []);
  assert.equal(ui.element('mcp-password').value, 'unsaved-fixture');
});
test('recovery preparation rejects empty phrase and back clears confirmation secrets', () => {
  const ui = recoveryUiHarness(); ui.run("state = 'unlocked'; setRecoveryStep('prepare')");
  ui.element('recovery-phrase').value = '   '; ui.click('recovery-next');
  assert.equal(ui.element('recovery-confirmation').disabled, true);
  ui.element('recovery-phrase').value = 'saved-phrase'; ui.click('recovery-next');
  assert.equal(ui.element('recovery-confirmation').disabled, false);
  assert.equal(ui.element('recovery-phrase').readOnly, true);
  ui.element('recovery-repeat').value = 'saved-phrase'; ui.element('recovery-password').value = 'secret';
  ui.element('phrase-saved').checked = true; ui.click('recovery-back');
  assert.equal(ui.element('recovery-phrase').value, 'saved-phrase');
  assert.equal(ui.element('recovery-repeat').value, ''); assert.equal(ui.element('recovery-password').value, '');
  assert.equal(ui.element('phrase-saved').checked, false);
  assert.equal(ui.element('recovery-confirmation').disabled, true);
});
test('recovery requires confirmed verification and clears secrets after cancellation or failure', async () => {
  const ui = recoveryUiHarness();
  for (const response of [{ canceled: true }, { error: 'saved_not_verified', path: '/tmp/copy.json' },
    { result: { credential_count: 1 } }, { result: { verified: true, credential_count: 0, path: '/tmp/copy.json' } }]) {
    ui.run("setRecoveryStep('confirm'); el('recovery-phrase').value = 'phrase'; el('recovery-password').value = 'secret'");
    await ui.run(`recoveryOperation(async () => (${JSON.stringify(response)}), 'save')`);
    assert.equal(ui.element('recovery-phrase').value, ''); assert.equal(ui.element('recovery-password').value, '');
    assert.equal(ui.element('recovery-confirmation').disabled, true);
    if (!response.result?.verified) assert.doesNotMatch(ui.element('recovery-status').textContent, /Копия сохранена/);
    else assert.match(ui.element('recovery-status').textContent, /Копия пока пустая/);
  }
});

test('returning profiles require a successful load before setup or start is offered', () => {
  const idle = { running: false };
  assert.equal(onboardingState('unlocked', guideProfiles, idle, 'first', 'loading').action, 'loading');
  const failed = onboardingState('unlocked', guideProfiles, idle, 'first', 'failed');
  assert.equal(failed.action, 'refresh'); assert.equal(failed.profile, undefined);
  assert.deepEqual(failed.complete, [true, false, false, false, false]);
  assert.equal(onboardingState('unlocked', [], idle, undefined, 'ready').action, 'add');
  const resumed = onboardingState('unlocked', guideProfiles, idle, 'first', 'ready');
  assert.equal(resumed.action, 'start'); assert.deepEqual(resumed.complete, [true, true, true, false, false]);
  assert.equal(onboardingState('locked', guideProfiles, idle, 'first', 'failed').action, 'vault');
});

function workspaceIpcHarness() {
  const vm = require('node:vm');
  const source = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'main.cjs'), 'utf8');
  const start = source.indexOf("ipcMain.handle('igorek:owner-workspace'");
  const end = source.indexOf("ipcMain.handle('igorek:owner-vault'", start);
  const frame = { url: 'file:///owner.html' };
  const contents = { mainFrame: frame, isDestroyed: () => false };
  const event = { sender: contents, senderFrame: frame };
  let handler, selected = { canceled: false, filePaths: ['/tmp/workspace'] }, status = { state: 'unlocked', agent: { running: true } };
  const scripts = []; let closed = 0, dialogs = 0;
  const context = vm.createContext({ managed: true, installingUpdate: false, selectedWorkspace: undefined, ownerUrl: frame.url,
    ownerWindow: { webContents: contents, close: () => closed++ }, isOwnerFrame,
    ipcMain: { handle: (_, fn) => { handler = fn; } },
    runtime: { requestOwner: async () => ({ result: status }) },
    dialog: { showOpenDialog: async () => { dialogs++; return selected; } },
    stat: async () => ({ isDirectory: () => true }), access: async () => {}, constants: { R_OK: 4, W_OK: 2, X_OK: 1 },
    window: { isDestroyed: () => false, webContents: { getURL: () => 'https://127.0.0.1/chat', executeJavaScript: async code => scripts.push(code) }, focus() {} },
    config: { origin: 'https://127.0.0.1' }, sameOrigin: () => true });
  vm.runInContext(source.slice(start, end), context);
  return { call: (action, input = event) => handler(input, action), setStatus: value => { status = value; },
    setSelection: value => { selected = value; }, context, scripts, get closed() { return closed; }, get dialogs() { return dialogs; } };
}
test('workspace picker is owner-only and session navigation requires unlocked running agent', async () => {
  const ui = workspaceIpcHarness();
  await assert.rejects(ui.call('choose', { sender: {}, senderFrame: {} })); assert.equal(ui.dialogs, 0);
  assert.equal((await ui.call('open-session')).error, 'workspace_required');
  ui.setStatus({ state: 'locked' }); assert.equal((await ui.call('choose')).error, 'vault_locked');
  ui.setStatus({ state: 'unlocked', agent: { running: false } });
  assert.equal((await ui.call('choose')).result.path, '/tmp/workspace');
  assert.equal((await ui.call('open-session')).error, 'agent_not_running'); assert.equal(ui.scripts.length, 0);
  ui.setStatus({ state: 'unlocked', agent: { running: true } });
  assert.equal((await ui.call('open-session')).result.opened, true); assert.equal(ui.closed, 1);
});
test('workspace cancellation preserves chosen directory and paths are event data, never executable code', async () => {
  const ui = workspaceIpcHarness();
  const path = '/tmp/quotes " ` ${window.compromised=true}';
  ui.setSelection({ canceled: false, filePaths: [path] }); await ui.call('choose');
  ui.setSelection({ canceled: true }); assert.equal((await ui.call('choose')).canceled, true);
  await ui.call('open-session');
  let detail;
  require('node:vm').runInNewContext(ui.scripts[0], { window: { dispatchEvent: event => { detail = event.detail; } }, CustomEvent: function (_, options) { this.detail = options.detail; } });
  assert.equal(detail.cwd, path);
  assert.equal(ui.context.selectedWorkspace, path);
});


test('readiness UI distinguishes incomplete checks and renders facts as text', async () => {
  const ui = recoveryUiHarness();
  ui.run(`window.igorekVault = { readiness: async () => ({ result: { readiness: { mode: 'read_only', checks: [
    { status: 'unknown', message: '<script>unsafe()</script>', action: 'Измерить требования', facts: { free_bytes: 1073741824, arbitrary_secret: 'hidden' } }
  ] } } }) }`);
  await ui.element('readiness-check').listeners.click();
  const card = ui.element('readiness-results').children[0];
  assert.match(card.children[0].children[1].textContent, /Не проверено полностью/);
  assert.match(card.children[0].children[0].textContent, /<script>/);
  assert.equal(card.children[1].children[0].textContent, 'Свободно на диске: 1.0 ГиБ');
  assert.equal(card.children.length, 2);
  assert.equal(card.children[1].children.length,2);
  assert.match(ui.element('readiness-summary').textContent,/Не проверено: 1/);
  ui.run("window.igorekVault.readiness = async () => ({ result: { readiness: { mode: 'invalid', checks: [] } } })");
  await ui.element('readiness-check').listeners.click();
  assert.match(ui.element('readiness-status').textContent, /Не удалось/);
});


test('desktop data locations derive only from the owned runtime root and UI directory', () => {
  const { ownedDataLocations } = require('../owner-policy.cjs');
  const { join } = require('node:path');
  const locations = ownedDataLocations('/tmp/owned-instance', '/tmp/owned-ui');
  assert.equal(locations.vault, join('/tmp/owned-instance', 'vault', 'credentials'));
  assert.equal(locations.history, join('/tmp/owned-instance', 'database', 'factory.db'));
  assert.equal(locations.workspace, join('/tmp/owned-instance', 'workspace'));
  assert.equal(locations.ui, '/tmp/owned-ui');
  assert.equal(Object.keys(locations).length, 6);
});
test('password form clears inputs on refused or successful changes and rejects mismatches locally', async () => {
  const ui = recoveryUiHarness();
  let calls = 0;
  ui.run("window.igorekVault = {}");
  ui.element('password-new').value = 'new'; ui.element('password-new-repeat').value = 'different';
  await ui.element('password-form').listeners.submit({ preventDefault() {} });
  assert.match(ui.element('error').textContent, /не совпадают/);
  for (const response of [{ error: 'authentication_failed' }, { error: 'stop_agent_first' }, { result: { state: 'unlocked' } }]) {
    ui.run(`window.igorekVault.changePassword = async () => (${JSON.stringify(response)})`);
    ui.element('password-current').value = 'old'; ui.element('password-new').value = 'new'; ui.element('password-new-repeat').value = 'new';
    await ui.element('password-form').listeners.submit({ preventDefault() {} });
    assert.equal(ui.element('password-current').value, ''); assert.equal(ui.element('password-new').value, '');
    assert.equal(ui.element('password-new-repeat').value, '');
    if (!response.error) assert.match(ui.element('password-status').textContent, /Пароль изменён/);
    else assert.equal(ui.element('password-status').textContent, '');
  }
});


test('image preparation displays phases and requires confirmed completion', () => {
  const ui = recoveryUiHarness();
  ui.run("window.igorekOnboardingState = () => ({complete: [], action: 'vault'}); state='unlocked'; renderImage({phase:'copying',running:true})");
  assert.match(ui.element('image-status').textContent, /копию/);
  assert.equal(ui.element('image-choose').disabled, true);
  ui.run("renderImage({phase:'completed',running:false,result:{prepared:true,execution_enabled:false,tool_version:'0.1.819'}})");
  assert.match(ui.element('image-status').textContent, /подготовлен и проверен/);
  assert.match(ui.element('image-status').textContent, /Выберите режим KVM/);
  ui.run("renderImage({phase:'completed',running:false,result:{prepared:false}})");
  assert.doesNotMatch(ui.element('image-status').textContent, /подготовлен и проверен/);
  ui.run("renderImage({phase:'failed',running:false,error:'checksum_mismatch'})");
  assert.match(ui.element('image-status').textContent, /Контрольная сумма/);
});

test('image picker cancellation and backend refusals never announce success', async () => {
  const ui = recoveryUiHarness();
  ui.run("window.igorekVault={prepareImage:async()=>({canceled:true})}");
  await ui.click('image-choose');
  assert.match(ui.element('image-status').textContent, /Выбор отменён/);
  ui.run("window.igorekVault.prepareImage=async()=>({error:'vault_locked'})");
  await ui.click('image-choose');
  assert.match(ui.element('image-status').textContent, /разблокируйте/);
});


test('execution selection requires unlocked stopped owner and confirmed saved mode', async () => {
  const ui = recoveryUiHarness();
  ui.run("window.igorekOnboardingState=()=>({complete:[],action:'vault'}); state='locked'; renderOnboarding()");
  assert.equal(ui.element('execution-save').disabled, true);
  ui.run("state='unlocked'; agent={running:true}; renderOnboarding()");
  assert.equal(ui.element('execution-mode').disabled, true);
  ui.run("agent={running:false}; renderOnboarding(); el('execution-mode').value='kvm'; window.igorekVault={selectExecutionMode:async mode=>({result:{execution:{mode}}}), executionMode:async()=>({result:{execution:{mode:'kvm'}}})}");
  await ui.click('execution-save');
  assert.match(ui.element('execution-status').textContent, /Выбран KVM/);
  ui.run("window.igorekVault.selectExecutionMode=async()=>({error:'stop_agent_first'})");
  await ui.click('execution-save');
  assert.match(ui.element('execution-status').textContent, /остановите/);
});

test('image recovery follows owner lock and executor guards and handles cancellation', async () => {
  const ui = recoveryUiHarness();
  ui.run("window.igorekOnboardingState=()=>({complete:[],action:'vault'}); el('refresh').disabled=false; state='locked'; agent={running:false}; renderOnboarding()");
  assert.equal(ui.element('image-recover').disabled, true);
  ui.run("state='unlocked'; agent={running:true}; renderOnboarding()");
  assert.equal(ui.element('image-recover').disabled, true);
  ui.run("agent={running:false}; renderOnboarding(); window.igorekVault={recoverImage:async()=>({canceled:true})}");
  assert.equal(ui.element('image-recover').disabled, false);
  await ui.click('image-recover');
  assert.match(ui.element('image-status').textContent, /отменён/);
});


test('running profile has a local chat action and stopped profile does not', () => {
  const ui = recoveryUiHarness(); ui.run('renderOnboarding = () => {}');
  let returned = 0; ui.element('return-to-chat').listeners.click = () => returned++;
  ui.run("agent = {running: true, profile: 'first'}; renderProfiles([{config: {name:'first', model:'m', base_url:'https://test/v1',api_format:'openai'}, status:'verified'}])");
  const card = ui.element('profile-list').children[0];
  const button = card.children.find(e => e.className === 'profile-actions').children.find(e => e.textContent === 'Перейти в чат');
  assert.ok(button); button.listeners.click(); assert.equal(returned, 1);
  ui.run("agent = {running: false, profile: ''}; renderProfiles(savedProfiles)");
  assert.equal(ui.element('profile-list').children[0].children.find(e => e.className === 'profile-actions').children.some(e => e.textContent === 'Перейти в чат'), false);
});
test('preset selection fills fields, clears draft key and preserves manual mode', () => {
  const ui = recoveryUiHarness(); ui.element('profile-key').value = 'old-key';
  ui.element('profile-preset').value = 'opencode-go'; ui.element('profile-preset').listeners.change();
  assert.equal(ui.element('profile-url').value, 'https://opencode.ai/zen/go/v1');
  assert.equal(ui.element('profile-model').value, 'deepseek-v4-flash');
  assert.equal(ui.element('profile-key').value, '');
  ui.element('profile-preset').value = 'custom'; ui.element('profile-preset').listeners.change();
  assert.equal(ui.element('profile-catalog').hidden, true);
  assert.equal(ui.element('profile-model').value, 'deepseek-v4-flash');
});
test('catalog selection sets protocol and filters free models without changing model silently', () => {
  const ui = recoveryUiHarness(); ui.element('profile-preset').value = 'opencode-zen'; ui.element('free-models').checked = true;
  ui.run("catalogModels = [{id:'free',name:'Free',free:true,api_format:'anthropic'},{id:'paid',name:'Paid',free:false,api_format:'openai'}]; renderCatalog()");
  assert.deepEqual(ui.element('profile-model-list').children.map(e => e.value), ['', 'free']);
  ui.element('profile-model-list').value = 'free'; ui.element('profile-model-list').listeners.change();
  assert.equal(ui.element('profile-format').value, 'anthropic');
  ui.element('free-models').checked = false; ui.element('free-models').listeners.change();
  assert.equal(ui.element('profile-model-list').children.length, 3);
});
test('late catalog response cannot replace another provider selection', async () => {
  const ui = recoveryUiHarness(); ui.element('profile-preset').value = 'openrouter'; ui.element('profile-url').value = 'https://openrouter.ai/api/v1';
  let resolve;
  ui.run('window.igorekVault = {}');
  ui.run('window').igorekVault.modelCatalog = () => new Promise(r => { resolve = r; });
  const pending = ui.click('load-models'); ui.run('clearCatalog()');
  resolve({result:{models:[{id:'wrong'}],note:'wrong'}}); await pending;
  assert.equal(ui.element('profile-model-list').children.length, 0);
  assert.equal(ui.element('model-catalog-status').textContent, '');
});

test('key viewing reauthenticates each time and clears password, displayed key and timer', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; window.igorekVault={revealProfileKey:async(id,password)=>({result:{secret:'fixture-only-key',expires_in_seconds:15}})}; openSecretView({credential_id:'fixture',config:{name:'fixture',model:'fixture-model'}})");
  ui.element('secret-password').value = 'password';
  await ui.element('secret-form').listeners.submit({ preventDefault() {} });
  assert.equal(ui.element('secret-password').value, '');
  assert.equal(ui.element('secret-value').textContent, 'fixture-only-key');
  const timeout = [...ui.timers.values()].find(timer => timer.ms === 15000);
  assert.ok(timeout); timeout.fn();
  assert.equal(ui.element('secret-value').textContent, '');
  assert.equal(ui.element('secret-dialog').open, false);
  ui.run("openSecretView({credential_id:'fixture',config:{name:'fixture',model:'fixture-model'}})");
  assert.equal(ui.element('secret-password').value, '');
  assert.equal(ui.element('secret-value').hidden, true);
  assert.equal(ui.element('secret-auth').hidden, false);
});

for (const action of ['blur', 'pagehide', 'visibilitychange', 'navigation']) {
  test('late key response is discarded after ' + action, async () => {
    const ui = recoveryUiHarness();
    ui.run("state='unlocked'; let deliver; window.igorekVault={revealProfileKey:()=>new Promise(resolve=>deliver=resolve)}; openSecretView({credential_id:'fixture',config:{name:'fixture',model:'fixture-model'}})");
    ui.element('secret-password').value = 'password';
    const pending = ui.element('secret-form').listeners.submit({ preventDefault() {} });
    if (action === 'visibilitychange') { ui.context.document.hidden = true; ui.documentEvents.visibilitychange(); }
    else if (action === 'navigation') ui.run("openPanel('vault')");
    else ui.windowEvents[action]();
    ui.run("deliver({result:{secret:'late-fixture-key',expires_in_seconds:15}})");
    await pending;
    assert.equal(ui.element('secret-value').textContent, '');
    assert.equal(ui.element('secret-dialog').open, false);
    assert.equal(ui.element('secret-password').value, '');
    assert.equal([...ui.timers.values()].some(timer => timer.ms === 15000), false);
  });
}

test('failed reauthentication never displays a key and permits a fresh password', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; window.igorekVault={revealProfileKey:async()=>({error:'authentication_failed'})}; openSecretView({credential_id:'fixture',config:{name:'fixture',model:'fixture-model'}})");
  ui.element('secret-password').value = 'wrong';
  await ui.element('secret-form').listeners.submit({ preventDefault() {} });
  assert.equal(ui.element('secret-value').textContent, '');
  assert.equal(ui.element('secret-password').value, '');
  assert.equal(ui.element('secret-show').disabled, false);
  assert.ok(ui.element('secret-status').textContent.includes('Пароль не подошёл'));
});

function ownerKeyIpcHarness(action = 'reveal_profile_key') {
  const vm = require('node:vm');
  const source = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'main.cjs'), 'utf8');
  const start = source.indexOf("ipcMain.handle('igorek:owner-vault'");
  const end = source.indexOf('\nasync function openWindow()', start);
  const url = 'file:///owner.html', mainFrame = { url };
  const contents = { mainFrame, isDestroyed: () => false };
  const calls = []; let handler, deliver;
  const context = vm.createContext({ managed: true, quitting: false, installingUpdate: false, ownerUrl: url, ownerWindow: { webContents: contents }, isOwnerFrame,
    runtime: { requestOwner: (action, fields) => { calls.push({ action, fields }); return new Promise(resolve => { deliver = resolve; }); } },
    shouldRefreshProfiles: () => false, ipcMain: { handle: (_, callback) => { handler = callback; } } });
  vm.runInContext(source.slice(start, end), context);
  return { context, calls, event: { sender: contents, senderFrame: mainFrame }, call: (event, fields) => handler(event, action, fields), deliver: value => deliver(value) };
}

test('key IPC rejects chat, subframes and disposed owner before any backend request', async () => {
  const h = ownerKeyIpcHarness();
  const fields = { credential_id: 'fixture', password: 'password' };
  await assert.rejects(h.call({ sender: {}, senderFrame: { url: 'https://localhost/chat' } }, fields));
  await assert.rejects(h.call({ sender: h.event.sender, senderFrame: { url: 'file:///owner.html' } }, fields));
  h.context.ownerWindow = null;
  await assert.rejects(h.call(h.event, fields));
  assert.equal(h.calls.length, 0);
});

test('key IPC never returns a pending key to a closed or replaced owner window', async () => {
  const h = ownerKeyIpcHarness();
  const pending = h.call(h.event, { credential_id: 'fixture', password: 'password' });
  assert.equal(h.calls.length, 1);
  h.context.ownerWindow = null;
  h.deliver({ result: { secret: 'discard-this-fixture', expires_in_seconds: 15 } });
  const result = await pending;
  assert.equal(result.error, 'vault_locked');
  assert.equal(JSON.stringify(result).includes('discard-this-fixture'), false);
});

test('locking the vault clears an open key view and blocks reopening it', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; window.igorekVault={revealProfileKey:async()=>({result:{secret:'fixture-key',expires_in_seconds:15}})}; openSecretView({credential_id:'fixture',config:{name:'fixture',model:'fixture-model'}})");
  ui.element('secret-password').value = 'password';
  await ui.element('secret-form').listeners.submit({ preventDefault() {} });
  ui.run("applyState('locked'); openSecretView({credential_id:'fixture',config:{name:'fixture',model:'fixture-model'}})");
  assert.equal(ui.element('secret-value').textContent, '');
  assert.equal(ui.element('secret-dialog').open, false);
  assert.equal([...ui.timers.values()].some(timer => timer.ms === 15000), false);
});

test('interface locking hides settings and clears drafts without loading profiles or stopping the agent', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; let loads=0; window.igorekVault={lockInterface:async()=>({result:{state:'unlocked',interface_locked:true,agent:{running:true,profile:'fixture'}}}),listProfiles:async()=>{loads++;throw Error('must not load')}}; openSecretView({credential_id:'fixture',config:{name:'fixture',model:'model'}})");
  ui.element('secret-password').value = 'pending-password';
  ui.element('profile-key').value = 'draft-private-key';
  await ui.click('lock-interface');
  assert.equal(ui.run('interfaceLocked'), true);
  assert.equal(ui.run('activePanel'), 'access');
  assert.equal(ui.run('loads'), 0);
  assert.equal(ui.run('agent.running'), true);
  assert.equal(ui.element('secret-dialog').open, false);
  assert.equal(ui.element('secret-password').value, '');
  assert.equal(ui.element('profile-key').value, '');
  assert.equal(ui.run("PANELS.filter(panelVisible).map(p=>p.id).join(',')"), 'access');
});

test('interface status survives reopening and only a successful password restores settings', async () => {
  const ui = recoveryUiHarness();
  ui.run("let unlocked=false; window.igorekVault={status:async()=>({result:{state:'unlocked',interface_locked:true,agent:{running:true,profile:'fixture'}}}),unlockInterface:async password=>password==='correct'?{result:{state:'unlocked',interface_locked:false,agent:{running:true,profile:'fixture'}}}:{error:'authentication_failed'},listProfiles:async()=>({result:{profiles:[]}}),executionMode:async()=>({result:{execution:{mode:'off'}}})}");
  await ui.run("perform('status')");
  assert.equal(ui.run('activePanel'), 'access');
  ui.element('interface-password').value = 'wrong';
  await ui.element('interface-unlock-form').listeners.submit({ preventDefault() {} });
  assert.equal(ui.run('interfaceLocked'), true);
  assert.equal(ui.element('interface-password').value, '');
  assert.ok(ui.element('interface-error').textContent.includes('Пароль не подошёл'));
  ui.element('interface-password').value = 'correct';
  await ui.run("perform('unlockInterface', el('interface-password').value)");
  assert.equal(ui.run('interfaceLocked'), false);
  assert.equal(ui.run('activePanel'), 'start');
  assert.equal(ui.run('agent.running'), true);
  assert.equal(ui.element('interface-password').value, '');
});

test('optional lock defaults hidden and PIN form requires matching six digits', async () => {
  const ui = recoveryUiHarness();
  ui.run("window.igorekVault={status:async()=>({result:{state:'unlocked',interface_lock_enabled:false}}),listProfiles:async()=>({result:{profiles:[]}}),executionMode:async()=>({result:{execution:{mode:'off'}}})}");
  await ui.run("perform('status')");
  assert.equal(ui.element('optional-lock-controls').hidden, true);
  assert.equal(ui.element('lock-interface').hidden, true);
  assert.equal(ui.element('lock-option-enabled').checked, false);
  ui.element('pin-new').value = '123456'; ui.element('pin-repeat').value = '654321';
  ui.element('pin-password').value = 'fixture';
  ui.element('pin-settings-form').listeners.submit({ preventDefault() {} });
  assert.equal(ui.element('pin-new').value, '');
  assert.equal(ui.element('pin-password').value, '');
  assert.match(ui.element('pin-status').textContent, /одинаковый PIN/);
});
test('enabled optional lock exposes PIN and clears submitted drafts', async () => {
  const ui = recoveryUiHarness();
  ui.run("window.igorekVault={status:async()=>({result:{state:'unlocked',interface_lock_enabled:true,interface_pin_enabled:true,interface_pin_available:true}}),listProfiles:async()=>({result:{profiles:[]}}),executionMode:async()=>({result:{execution:{mode:'off'}}})}");
  ui.element('pin-password').value = 'fixture'; ui.element('interface-pin').value = '123456';
  await ui.run("perform('status')");
  assert.equal(ui.element('optional-lock-controls').hidden, false);
  assert.equal(ui.element('interface-pin-form').hidden, false);
  assert.equal(ui.element('lock-option-enabled').checked, true);
  assert.equal(ui.element('pin-password').value, '');
  assert.equal(ui.element('interface-pin').value, '');
});

test('legacy import creates a reviewable draft with an empty key and preserves cancel', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked';window.igorekVault={chooseLegacyProfile:async()=>({result:{legacy_profile:{name:'old-profile',model:'fixture-model',base_url:'https://example.test/v1',api_format:'openai'}}})}");
  ui.element('profile-key').value = 'old-draft-key';
  await ui.click('legacy-profile-choose');
  assert.equal(ui.element('profile-name').value, 'old-profile');
  assert.equal(ui.element('profile-model').value, 'fixture-model');
  assert.equal(ui.element('profile-key').value, '');
  ui.run("window.igorekVault.chooseLegacyProfile=async()=>({canceled:true})");
  await ui.click('legacy-profile-choose');
  assert.equal(ui.element('profile-model').value, 'fixture-model');
  ui.run("interfaceLocked=true;window.igorekVault.chooseLegacyProfile=async()=>{throw Error('must not call')}");
  await ui.click('legacy-profile-choose');
  assert.equal(ui.element('profile-model').value, 'fixture-model');
});

test('profile management requires confirmation and clears canceled or rejected passwords', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked';const profile={credential_id:'fixture',config:{name:'first'}};openProfileManagement(profile,'deleteProfile')");
  assert.equal(ui.element('profile-manage-dialog').open, true);
  assert.match(ui.element('profile-manage-note').textContent, /История/);
  ui.element('profile-manage-password').value = 'fixture-password';
  ui.click('profile-manage-cancel');
  assert.equal(ui.element('profile-manage-password').value, '');
  ui.run("openProfileManagement(profile,'renameProfile');window.igorekVault={renameProfile:async()=>({error:'authentication_failed'})}");
  ui.element('profile-manage-password').value = 'wrong';
  await ui.element('profile-manage-form').listeners.submit({preventDefault(){}});
  assert.equal(ui.element('profile-manage-password').value, '');
  assert.match(ui.element('profile-manage-status').textContent, /Пароль не подошёл/);
  ui.run("closeProfileManagement();agent={running:true,profile:'first'};openProfileManagement(profile,'deleteProfile')");
  assert.equal(ui.element('profile-manage-dialog').open, false);
});

test('local preset permits empty key and remote URLs restore key requirement', () => {
  const ui=recoveryUiHarness();
  ui.element('profile-preset').value='local';
  ui.element('profile-preset').listeners.change();
  assert.equal(ui.element('profile-url').value,'http://127.0.0.1:1234/v1');
  assert.equal(ui.element('profile-key').required,false);
  assert.equal(ui.element('profile-catalog').hidden,true);
  ui.element('profile-url').value='https://example.test/v1';
  ui.element('profile-url').listeners.input();
  assert.equal(ui.element('profile-key').required,true);
  ui.element('profile-preset').value='openrouter';
  ui.element('profile-preset').listeners.change();
  assert.equal(ui.element('profile-key').required,true);
  assert.equal(ui.element('profile-catalog').hidden,false);
});

test('MCP permission menu uses dynamic server and profile ids and clears password', async () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked';window.igorekVault={mcpAccess:async()=>({result:{mcp_access:{servers:[{id:'custom-service'}],profiles:[{credential_id:'stable-id',config:{name:'my-model'}}],grants:{}}}}),setMcpAccess:async fields=>({result:{mcp_access:{servers:[{id:fields.server}],profiles:[{credential_id:fields.credential_id,config:{name:'my-model'}}],grants:{}}}})}");
  await ui.click('mcp-load');
  assert.equal(ui.element('mcp-server').children[0].value,'custom-service');
  assert.equal(ui.element('mcp-profile').children[0].value,'stable-id');
  assert.equal(ui.element('mcp-mode').value,'deny');
  ui.element('mcp-password').value='fixture-password';
  await ui.element('mcp-access-form').listeners.submit({preventDefault(){}});
  assert.equal(ui.element('mcp-password').value,'');
});

for (const action of ['reveal_value', 'save_value', 'delete_value', 'list_values']) {
  test('standalone value IPC rejects foreign frames: ' + action, async () => {
    const h = ownerKeyIpcHarness(action);
    await assert.rejects(h.call({sender:{},senderFrame:{url:'https://localhost/chat'}}, {}));
    await assert.rejects(h.call({sender:h.event.sender,senderFrame:{url:'file:///owner.html'}}, {}));
    assert.equal(h.calls.length, 0);
  });
}
test('standalone value IPC discards a revealed value after the owner closes', async () => {
  const h = ownerKeyIpcHarness('reveal_value');
  const pending = h.call(h.event, {credential_id:'fixture',password:'fixture-password'});
  h.context.ownerWindow = null;
  h.deliver({result:{secret:'private-fixture',expires_in_seconds:15}});
  assert.equal((await pending).error, 'vault_locked');
});

test('value grants show dynamic consumers, require password and support exact revocation', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; globalThis.accessFixture={credential_id:'value',name:'Search token',servers:['search'],profiles:[{credential_id:'profile',name:'model'}],grants:[]}; window.igorekVault={valueAccess:async()=>({result:{value_access:accessFixture}}),grantValueAccess:async fields=>{globalThis.grantFields={...fields};return {result:{value_access:{...accessFixture,grants:[{id:'grant',server:'search',profile_id:'profile',env_name:'SEARCH_TOKEN',active:true}]}}}},revokeValueAccess:async fields=>{globalThis.revokeFields={...fields};return {result:{value_access:accessFixture}}}};");
  await ui.run("openValueAccess({credential_id:'value',name:'Search token'})");
  assert.equal(ui.element('value-access-server').children[0].value, 'search');
  assert.equal(ui.element('value-access-profile').children[0].value, 'profile');
  ui.element('value-access-server').value = 'search'; ui.element('value-access-profile').value = 'profile';
  ui.element('value-access-env').value = 'SEARCH_TOKEN'; ui.element('value-access-password').value = 'fixture-password';
  await ui.element('value-access-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.run('grantFields.credential_id'), 'value'); assert.equal(ui.run('grantFields.profile_id'), 'profile');
  assert.equal(ui.element('value-access-password').value, '');
  assert.equal(ui.element('value-access-grants').children.length, 1);
  ui.element('value-access-grants').children[0].children[1].listeners.click();
  assert.equal(ui.element('value-access-fields').disabled, true);
  ui.element('value-access-password').value = 'fixture-password';
  await ui.element('value-access-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.run('revokeFields.grant_id'), 'grant');
  assert.equal(ui.element('value-access-grants').children.length, 0);
});

test('value grants discard late replies and clear submitted password on blur', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;window.igorekVault={valueAccess:async()=>({result:{value_access:{name:'Demo',servers:['search'],profiles:[{credential_id:'profile',name:'model'}],grants:[]}}}),grantValueAccess:()=>new Promise(resolve=>globalThis.deliverGrant=resolve)}");
  await ui.run("openValueAccess({credential_id:'value',name:'Demo'})");
  ui.element('value-access-server').value = 'search';ui.element('value-access-profile').value = 'profile';
  ui.element('value-access-env').value = 'SEARCH_TOKEN'; ui.element('value-access-password').value = 'fixture-password';
  const pending = ui.element('value-access-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.element('value-access-password').value, '');
  ui.click('value-access-new');
  assert.equal(ui.element('value-access-submit').disabled, true);
  ui.windowEvents.blur();
  ui.run("deliverGrant({result:{value_access:{name:'Late',servers:[],profiles:[],grants:[{id:'late'}]}}})");
  await pending;
  assert.equal(ui.element('value-access-dialog').open, false);
  assert.equal(ui.element('value-access-grants').children.length, 0);
});

for (const action of ['value_access', 'grant_value_access', 'revoke_value_access']) {
  test('consumer grant IPC rejects chat and subframes: ' + action, async () => {
    const h = ownerKeyIpcHarness(action);
    await assert.rejects(h.call({sender:{},senderFrame:{url:'https://localhost/chat'}}, {}));
    await assert.rejects(h.call({sender:h.event.sender,senderFrame:{url:'file:///owner.html'}}, {}));
    assert.equal(h.calls.length, 0);
  });
}

test('agent records show trusted task origin and auto-refresh without exposing the value', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;activePanel='secrets';window.igorekVault={listValues:async()=>({result:{values:[{credential_id:'agent-value',name:'Service token',kind:'secret',origin:{kind:'agent',profile:'my-model',task_id:'task-12345678',session_id:'session-demo'}}]}})}");
  await ui.run('loadValues()');
  const summary = ui.element('vault-values').children[0].children[2];
  assert.match(summary.children[0].textContent, /Агент · my-model/);
  assert.match(summary.title, /task-12345678/);
  assert.match(summary.title, /session-demo/);
  const refresh = [...ui.timers.values()].find(timer => timer.ms === 5000);
  assert.ok(refresh);
  ui.run("el('value-dialog').open=true"); refresh.fn();
  assert.equal(ui.element('vault-values').children.length, 1);
  ui.run("applyState('locked')");
  assert.equal(ui.element('vault-values').children.length, 0);
  assert.equal([...ui.timers.values()].some(timer => timer.ms === 5000), false);
});

test('table paginates a hundred records and combines name, type and source filters', () => {
  const ui = recoveryUiHarness();
  ui.run("renderValues(Array.from({length:100},(_,i)=>({credential_id:String(i),name:'KEY_'+String(i).padStart(3,'0'),kind:i%2?'secret':'variable',origin:i%3===1?{kind:'agent',profile:'model',task_id:'task-'+i,session_id:'session'}:{kind:'owner'}})))");
  assert.equal(ui.element('vault-values').children.length, 25);
  assert.equal(ui.element('values-page-status').textContent, '1–25 из 100');
  ui.click('values-next'); assert.equal(ui.element('vault-values').children[0].children[0].textContent, 'KEY_025');
  ui.element('values-search').value = 'key_099'; ui.element('values-search').listeners.input();
  assert.equal(ui.element('vault-values').children.length, 1);
  assert.equal(ui.element('values-page-status').textContent, '1–1 из 1');
  ui.element('values-search').value = ''; ui.element('values-search').listeners.input();
  ui.element('values-kind').value = 'secret'; ui.element('values-kind').listeners.change();
  assert.equal(ui.element('values-page-status').textContent, '1–25 из 50');
  ui.element('values-origin').value = 'agent'; ui.element('values-origin').listeners.change();
  assert.equal(ui.element('vault-values').children.length, 17);
  ui.element('values-search').value = 'missing'; ui.element('values-search').listeners.input();
  assert.equal(ui.element('values-empty').hidden, false);
  assert.equal(ui.element('values-next').disabled, true);
});

test('dotenv preview selects metadata only, skips conflicts and submits explicit types', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;window.igorekVault={chooseEnvImport:async()=>({result:{env_import:{draft_id:'a'.repeat(64),filename:'.env',entries:[{name:'API_TOKEN',empty:false,exists:false},{name:'EXISTING',empty:false,exists:true},{name:'EMPTY',empty:true,exists:false},{name:'__proto__',empty:false,exists:false}]}}}),commitEnvImport:async fields=>{globalThis.submittedImport={...fields};return {result:{imported:3,values:[]}}},cancelEnvImport:async()=>({result:{canceled:true}})}");
  await ui.click('env-import-choose');
  assert.equal(ui.element('env-import-dialog').open, true);
  assert.equal(ui.element('env-import-rows').children.length, 4);
  assert.equal(ui.run("envImportRows[1].checkbox.disabled"), true);
  ui.run("envImportRows[0].select.value='secret'");
  ui.element('env-import-password').value = 'fixture-password';
  await ui.element('env-import-form').listeners.submit({preventDefault() {}});
  const selection = JSON.parse(ui.run('submittedImport.selection'));
  assert.equal(selection.API_TOKEN, 'secret'); assert.equal(selection.EMPTY, 'variable');
  assert.equal(Object.hasOwn(selection, 'EXISTING'), false); assert.equal(Object.hasOwn(selection, '__proto__'), true);
  assert.equal(ui.element('env-import-password').value, ''); assert.equal(ui.element('env-import-dialog').open, false);
  assert.match(ui.element('env-import-result').textContent, /Перенесено записей: 3/);
});

test('dotenv password refusal and late commit response never expose or retain password', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;window.igorekVault={chooseEnvImport:async()=>({result:{env_import:{draft_id:'a'.repeat(64),filename:'.env',entries:[{name:'API_TOKEN',empty:false,exists:false}]}}}),commitEnvImport:async()=>({error:'authentication_failed'}),cancelEnvImport:async()=>({result:{canceled:true}})}");
  await ui.click('env-import-choose'); ui.element('env-import-password').value = 'wrong';
  await ui.element('env-import-form').listeners.submit({preventDefault() {}});
  assert.equal(ui.element('env-import-password').value, ''); assert.equal(ui.element('env-import-dialog').open, true);
  ui.run("window.igorekVault.commitEnvImport=()=>new Promise(resolve=>globalThis.deliverImport=resolve)");
  ui.element('env-import-password').value = 'fixture-password';
  const pending = ui.element('env-import-form').listeners.submit({preventDefault() {}});
  ui.windowEvents.blur();
  ui.run("deliverImport({result:{imported:1,values:[{credential_id:'late',name:'LATE',kind:'variable'}]}})");
  await pending;
  assert.equal(ui.element('env-import-dialog').open, false); assert.equal(ui.element('env-import-password').value, '');
  assert.equal(ui.element('vault-values').children.length, 0);
});

for (const action of ['choose_env_import', 'commit_env_import', 'cancel_env_import', 'prepare_env_import']) {
  test('dotenv IPC refuses chat and foreign subframes: ' + action, async () => {
    const h = ownerKeyIpcHarness(action);
    await assert.rejects(h.call({sender:{},senderFrame:{url:'https://localhost/chat'}}, {}));
    await assert.rejects(h.call({sender:h.event.sender,senderFrame:{url:'file:///owner.html'}}, {}));
    assert.equal(h.calls.length, 0);
  });
}

test('owner renderer cannot bypass dotenv file selection with an arbitrary source path', async () => {
  const h = ownerKeyIpcHarness('prepare_env_import');
  await assert.rejects(h.call(h.event, {source:'/tmp/unselected.env'}));
  assert.equal(h.calls.length, 0);
});

test('closing the owner during dotenv file selection prevents reading the selected file', async () => {
  const h = ownerKeyIpcHarness('choose_env_import');
  let resolveSelection, selectionOpened;
  const opened = new Promise(resolve => {selectionOpened=resolve;});
  h.context.dialog = {showOpenDialog:()=>new Promise(resolve=>{resolveSelection=resolve;selectionOpened();})};
  const pending = h.call(h.event, undefined);
  h.deliver({result:{state:'unlocked'}});
  await opened;
  assert.equal(typeof resolveSelection, 'function');
  h.context.ownerWindow = null;
  resolveSelection({canceled:false,filePaths:['/tmp/unselected.env']});
  assert.equal((await pending).error, 'vault_locked');
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].action, 'status');
});

test('Gmail actions explain missing password before IPC; status remains passwordless', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; globalThis.gmailCalls=0; window.igorekVault={gmailImportHost:async()=>{gmailCalls++;throw Error('must not invoke')},gmailStatus:async()=>{gmailCalls++;return {result:{gmail:{connected:true}}}}}");
  for (const password of ['', '   ']) {
    ui.element('gmail-password').value = password;
    await ui.run("gmailOperation('gmailImportHost')");
    assert.equal(ui.run('gmailCalls'), 0);
    assert.match(ui.element('gmail-status').textContent, /Введите пароль Vault этого desktop/);
    assert.equal(ui.element('gmail-password').value, '');
  }
  await ui.run("gmailOperation('gmailStatus')");
  assert.equal(ui.run('gmailCalls'), 1);
  assert.match(ui.element('gmail-status').textContent, /Авторизация Gmail сохранена/);
});

test('Gmail import preserves exact password, reports authentication error and clears input', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; window.igorekVault={gmailImportHost:async password=>{globalThis.gmailPassword=password;return {error:'authentication_failed'}}}");
  ui.element('gmail-password').value = ' fixture-password ';
  await ui.run("gmailOperation('gmailImportHost')");
  assert.equal(ui.run('gmailPassword'), ' fixture-password ');
  assert.equal(ui.element('gmail-password').value, '');
  assert.notEqual(ui.element('gmail-status').textContent, 'Нет ответа от приложения.');
  ui.run("window.igorekVault.gmailImportHost=async()=>({result:{gmail:{connected:true}}})");
  ui.element('gmail-password').value = 'fixture-password';
  await ui.run("gmailOperation('gmailImportHost')");
  assert.match(ui.element('gmail-status').textContent, /Авторизация Gmail сохранена/);
});


test('ChatGPT profiles expose account controls without a token reveal action', () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; renderProfiles([{credential_id:'oauth',config:{name:'chatgpt',model:'fixture-model',base_url:'https://api.openai.com/v1',api_format:'openai-responses'},status:'unverified',chatgpt:{email:'fixture@example.invalid',connected:true}}])");
  const row = ui.element('secret-connections').children[0];
  assert.equal(row.children.length, 4);
  assert.equal(row.children.find(e=>e.className==='profile-actions').children.some(e=>e.textContent==='Посмотреть ключ'),false);
  const card = ui.element('profile-list').children[0];
  assert.ok(card.children.find(e => e.className === 'profile-actions').children.some(x => x.textContent === 'Войти снова'));
  assert.ok(card.children.find(e => e.className === 'profile-actions').children.some(x => x.textContent === 'Выйти из ChatGPT'));
  ui.run("editProfile(savedProfiles[0])");
  assert.equal(ui.element('profile-key-label').hidden, true);
  assert.equal(ui.element('profile-key').required, false);
  assert.equal(ui.element('profile-url').readOnly, true);
  assert.equal(ui.element('profile-catalog').hidden, false);
  ui.run('cancelProfileEdit()');
  assert.equal(ui.element('profile-key-label').hidden, false);
  assert.equal(ui.element('profile-format').disabled, false);
});

test('ChatGPT model picker uses the account catalog and never an API key field', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; window.igorekVault={chatgptModels:async id=>{globalThis.accountId=id;return {result:{models:[{id:'fixture-model',name:'Fixture',api_format:'openai-responses',free:false}],note:'Подписка'}}}}; editProfile({credential_id:'oauth',config:{name:'chatgpt',model:'fixture-model',base_url:'https://api.openai.com/v1',api_format:'openai-responses'}})");
  await ui.run('loadModels()');
  assert.equal(ui.run('accountId'), 'oauth');
  assert.equal(ui.element('profile-model-list').children.length, 2);
  assert.equal(ui.element('model-catalog-status').textContent, 'Подписка');
});

test('ChatGPT sign-in is polled to a saved account and canceled results stay inactive', async () => {
  const ui = recoveryUiHarness();
  ui.run("state='unlocked'; interfaceLocked=false; window.igorekVault={chatgptBegin:async(name,id)=>({result:{opened:true}}),chatgptStatus:async()=>({result:{chatgpt:{state:'connected',credential_id:'oauth'},profiles:[{credential_id:'oauth',config:{name:'chatgpt',model:'fixture-model',base_url:'https://api.openai.com/v1',api_format:'openai-responses'},status:'unverified',chatgpt:{connected:true}}]}}),chatgptModels:async()=>({result:{models:[],note:'Подписка'}}),chatgptCancel:async()=>({result:{}})}");
  ui.element('chatgpt-name').value='chatgpt';
  await ui.run('beginChatgpt()');
  assert.equal(ui.element('chatgpt-cancel').hidden, false);
  const poll = [...ui.timers.values()].find(t => t.ms === 1000).fn;
  await poll();
  assert.equal(ui.run('editingProfile'), 'oauth');
  assert.match(ui.element('chatgpt-status').textContent, /Аккаунт подключён/);
  await ui.run('beginChatgpt()');
  const late = [...ui.timers.values()].filter(t => t.ms === 1000).at(-1).fn;
  await ui.click('chatgpt-cancel'); await late();
  assert.equal(ui.element('chatgpt-status').textContent, 'Вход отменён.');
});


test('provider identity follows the actual endpoint and status never invents a connection', () => {
  const {profileProvider,profileIndicator}=require('../profile-cards.cjs');
  for (const [url,id] of [['https://api.openai.com/v1','openai'],['https://opencode.ai/zen/go/v1','opencode-go'],['https://opencode.ai/zen/v1','zen'],
    ['https://api.z.ai/api/coding/paas/v4','zai'],['https://openrouter.ai/api/v1','openrouter'],
    ['http://127.0.0.1:1234/v1','local'],['https://api.openai.com.evil.invalid/v1','custom']]) {
    const provider=profileProvider({base_url:url,api_format:'openai'});
    assert.equal(provider.id,id); assert.ok(provider.logo.startsWith('<svg'));
  }
  const p={config:{name:'demo'},status:'unverified'};
  assert.equal(profileIndicator(p,{running:false}).label,'Не проверен');
  assert.equal(profileIndicator({...p,status:'verified'},{running:false}).label,'Проверен');
  assert.equal(profileIndicator({...p,status:'failed'},{running:false}).label,'Ошибка проверки');
  assert.equal(profileIndicator({...p,status:'verified',chatgpt:{connected:false}},{running:false}).label,'Нужен вход');
  assert.equal(profileIndicator(p,{running:true,profile:'demo'}).label,'Работает');
});

test('compact cards keep actions in a menu and retain running and voice restrictions', () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked'; agent={running:true,profile:'demo'}; renderProfiles([{credential_id:'demo',config:{name:'demo',model:'long-model',base_url:'https://opencode.ai/zen/go/v1',api_format:'openai'},status:'verified'},{credential_id:'voice',config:{name:'openai-realtime',model:'gpt-realtime',base_url:'https://api.openai.com/v1',api_format:'openai'},status:'verified'}])");
  const [card,voice]=ui.element('profile-list').children;
  const menu=card.children.find(x=>x.className==='profile-actions');
  assert.ok(menu);
  assert.equal(card.children.filter(x=>x.className==='profile-menu-toggle').length,1);
  assert.equal(card.children.some(x=>x.textContent==='Проверить подключение'),false);
  assert.equal(menu.children.find(x=>x.textContent==='Проверить подключение').disabled,true);
  assert.equal(menu.children.find(x=>x.textContent==='Редактировать').disabled,true);
  assert.equal(menu.children.find(x=>x.textContent==='Остановить исполнитель').disabled,false);
  const voiceMenu=voice.children.find(x=>x.className==='profile-actions');
  assert.equal(voiceMenu.children.some(x=>x.textContent==='Запустить исполнитель'),false);
  assert.ok(voiceMenu.children.some(x=>x.textContent==='Настроить голос'));
});


test('model setup opens on demand and editing reveals the exact saved profile', async () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;cancelProfileEdit()");
  assert.equal(ui.element('provider-editor').hidden,true);
  await ui.click('provider-add');
  assert.equal(ui.element('provider-choice').hidden,false);
  assert.equal(ui.element('api-editor').hidden,true);
  await ui.click('provider-kind-api');
  assert.equal(ui.element('api-editor').hidden,false);
  assert.equal(ui.element('chatgpt-editor').hidden,true);
  ui.run("editProfile({credential_id:'existing',config:{name:'existing',model:'fixture-model',base_url:'https://openrouter.ai/api/v1',api_format:'openai'}})");
  assert.equal(ui.run('editingProfile'),'existing');
  assert.equal(ui.element('profile-model').value,'fixture-model');
  assert.equal(ui.element('provider-editor').hidden,false);
  await ui.click('provider-editor-close');
  assert.equal(ui.element('provider-editor').hidden,true);
});

test('connection cards in the secret hub keep key reveal behind reauthentication', () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;renderProfiles([{credential_id:'api',config:{name:'router',model:'fixture',base_url:'https://openrouter.ai/api/v1',api_format:'openai'},status:'verified'}])");
  const card=ui.element('secret-connections').children[0];
  const menu=card.children.find(x=>x.className==='profile-actions');
  menu.children.find(x=>x.textContent==='Посмотреть ключ').listeners.click();
  assert.equal(ui.element('secret-dialog').open,true);
  assert.equal(ui.element('secret-value').hidden,true);
  assert.equal(ui.element('secret-password').value,'');
});

test('MCP cards show only grants bound to the current profile config and reveal the chosen editor', () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;activePanel='mcp';el('mcp-target').value='desktop';globalThis.mcpFixture={servers:[{id:'gmail'}],profiles:[{credential_id:'go',config:{name:'go',model:'model',base_url:'https://opencode.ai/zen/go/v1',api_format:'openai'}}],grants:{gmail:{go:{access:'allow',config:{api_format:'openai',base_url:'https://opencode.ai/zen/go/v1',model:'model',name:'go'}}}}};renderMcpAccess(mcpFixture)");
  let card=ui.element('mcp-connections').children[0];
  assert.equal(card.children[1].children.find(x=>x.className==='settings-permissions').textContent,'Доступ: go');
  card.children.find(x=>x.className==='profile-actions').children.find(x=>x.textContent==='Права доступа моделей').listeners.click();
  assert.equal(ui.element('mcp-access-details').open,true);
  assert.equal(ui.element('mcp-server').value,'gmail');
  ui.run("mcpFixture.profiles[0].config.model='changed';renderMcpAccess(mcpFixture)");
  card=ui.element('mcp-connections').children[0];
  assert.equal(card.children[1].children.find(x=>x.className==='settings-permissions').textContent,'Доступ моделям не выдан');
});

test('late MCP replies cannot replace the selected environment', async () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;el('mcp-target').value='desktop';window.igorekVault={mcpAccess:()=>new Promise(r=>globalThis.finishDesktop=r),hostMcpAccess:()=>new Promise(r=>globalThis.finishHost=r)}");
  const old=ui.run('loadMcpConnections()');
  ui.element('mcp-target').value='host';
  const latest=ui.run('loadMcpConnections()');
  ui.run("finishHost({result:{mcp_access:{servers:[{id:'host-only'}],profiles:[],grants:{}}}})");await latest;
  ui.run("finishDesktop({result:{mcp_access:{servers:[{id:'desktop-only'}],profiles:[],grants:{}}}})");await old;
  assert.equal(ui.run('mcpAccessState.servers[0].id'),'host-only');
  assert.equal(ui.element('gmail-setup').hidden,true);
  assert.equal(ui.element('private-mcp-details').hidden,true);
});

test('voice summary exposes a blank replacement form without revealing a stored key', () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;renderProfiles([{credential_id:'voice',config:{name:'openai-realtime',model:'gpt-realtime-2.1-mini',base_url:'https://api.openai.com/v1',api_format:'openai'},status:'verified'}])");
  assert.equal(ui.element('voice-add').hidden,true);
  const card=ui.element('voice-summary').children[0];
  card.children.find(x=>x.className==='profile-actions').children.find(x=>x.textContent==='Заменить API-ключ').listeners.click();
  assert.equal(ui.element('voice-editor').hidden,false);
  assert.equal(ui.element('voice-key').value,'');
  ui.element('voice-key').value='draft-key';ui.click('voice-editor-close');
  assert.equal(ui.element('voice-editor').hidden,true);
  assert.equal(ui.element('voice-key').value,'');
});

test('calendar summary keeps model permissions visible and setup collapsed until chosen', () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;calendarState={connected:true,enabled:true,calendar_name:'Рабочий',model_access:[{profile:'go',access:'allow'},{profile:'local',access:'local_only'}]};calendarButtons()");
  const card=ui.element('calendar-summary').children[0];
  assert.equal(ui.element('calendar-connect').hidden,true);
  assert.equal(card.children[1].children.find(x=>x.className==='settings-permissions').textContent,'Доступ: go, local (локально)');
  card.children.find(x=>x.className==='profile-actions').children.find(x=>x.textContent==='Дополнительные настройки').listeners.click();
  assert.equal(ui.element('calendar-details').open,true);
  ui.run("calendarState.check={ok:false};calendarButtons()");
  const failed=ui.element('calendar-summary').children[0].children[1].children.find(x=>x.className==='profile-indicator');
  assert.equal(failed.textContent,'Ошибка проверки');
});


test('CodexPro reaches ChatGPT authorization with a visible canonical name', async () => {
  const ui=recoveryUiHarness();
  ui.run("state='unlocked';interfaceLocked=false;window.igorekVault={chatgptBegin:async(name,id)=>{globalThis.authorizationArgs={name,id};return {result:{opened:true}}}};");
  ui.element('chatgpt-name').value='CodexPro';
  await ui.run('beginChatgpt()');
  assert.equal(ui.run('authorizationArgs.name'),'codexpro');
  assert.equal(ui.run('authorizationArgs.id'),'');
  assert.equal(ui.element('chatgpt-name').value,'CodexPro');
  assert.match(ui.element('chatgpt-name-note').textContent,/codexpro/);
  assert.equal(ui.element('chatgpt-cancel').hidden,false);
});

test('invalid connection names stay local and corrected names clear Russian validation', async () => {
  const ui=recoveryUiHarness();
  let message='';ui.element('chatgpt-name').setCustomValidity=value=>message=value;
  ui.run("state='unlocked';interfaceLocked=false;globalThis.authCalls=0;window.igorekVault={chatgptBegin:async()=>{authCalls++;return {result:{opened:true}}}};");
  ui.element('chatgpt-name').value='../bad';
  await ui.run('beginChatgpt()');
  assert.equal(ui.run('authCalls'),0);assert.match(message,/латинскими/);
  ui.element('chatgpt-name').value='Codex-Pro2';ui.element('chatgpt-name').listeners.input();
  assert.equal(message,'');
  assert.match(ui.element('chatgpt-name-note').textContent,/codex-pro2/);
});

test('Responses probe failures show HTTP code, field and request phase', () => {
  const ui=recoveryUiHarness();
  const text=ui.run("checkReason({reason:'responses_failed',http_status:400,provider_error_code:'unknown_parameter',error_param:'tool_choice.namespace',request_phase:'text_response',request_id:'req_fixture12345678'})");
  assert.match(text,/HTTP 400/);assert.match(text,/tool_choice.namespace/);
  assert.match(text,/ответ модели/);assert.match(text,/req_fixture/);
});

test('Responses unexpected body diagnostics include safe media type', () => {
  const ui=recoveryUiHarness();
  const text=ui.run("checkReason({reason:'responses_invalid_response',http_status:200,response_content_type:'text/html',validation_code:'responses_invalid_sse_envelope',request_phase:'text_response'})");
  assert.match(text,/HTTP 200/);assert.match(text,/формат text\/html/);
  assert.match(text,/responses_invalid_sse_envelope/);assert.match(text,/ответ модели/);
});

test('settings state changes and pagehide do not send locked or closing MCP cleanup requests', async () => {
  const ui=recoveryUiHarness(); let calls=0;
  ui.context.window.igorekVault={privateMcpClear:async()=>{calls++;return {result:{}};}};
  ui.run("state='locked';clearPrivateMcp()");
  ui.run("applyState('unlocked');clearPrivateMcp()");
  assert.equal(calls,1);
  ui.run("applyState('locked');state='unlocked'");
  ui.windowEvents.pagehide();assert.equal(calls,1);
  ui.run("clearPrivateMcp()");assert.equal(calls,2);
});

function quotaUiHarness() {
  const vm=require('node:vm'),source=require('node:fs').readFileSync(require('node:path').join(__dirname, '..','../factory/web_ui/static/app.js'),'utf8');
  const pending=[],notices=[],timers=new Map(),handlers={};let sequence=0;
  const c={state:{paired:true,quota:{},panel:{open:false,tab:'chat'}},profile:'first',OPENCODE_USAGE_TTL_MS:60000,Date,encode:encodeURIComponent,
    currentSessionProfile:()=>c.profile,renderPlanSection(){},normalizeOpenCodeUsage:v=>v,codexQuotaError:e=>e.code,
    desktopNotification:(...args)=>notices.push(args),document:{hidden:false},loadOptions:async()=>{},ui:{sessionError:{}},
    api:(url,options)=>new Promise((resolve,reject)=>pending.push({url,options,resolve,reject})),
    window:{IgorekDesktop:{},setTimeout:(fn,ms)=>{timers.set(++sequence,{fn,ms});return sequence;},clearTimeout:id=>timers.delete(id),addEventListener:(name,fn)=>handlers[name]=fn}};
  vm.createContext(c);
  for(const [start,end] of [['function currentQuotaModel()', 'function buildPlanOpenCodeUsage()'],['async function loadOpenCodeUsage(', 'function buildPlanView()'],['window.addEventListener("igorek:profiles-changed"', 'function createWorkdirCombo(']])vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start))),c);
  return {c,pending,notices,timers,handlers,success:(request,value=37)=>request.resolve({provider:'codex',usage:{weekly:{percent:value}},fetched_at:'fixture'})};
}

test('locked startup quota waits without notices and refreshes after owner unlock with panel closed', async () => {
  const h=quotaUiHarness(),p=h.c.loadOpenCodeUsage();
  assert.equal(h.pending[0].options.notify,false);
  h.pending.shift().reject(Object.assign(Error('locked'),{code:'vault_locked',status:423}));await p;
  assert.equal(h.notices.length,0);assert.equal(h.timers.size,0);assert.equal(h.c.state.quota.data,null);
  h.handlers['igorek:profiles-changed']();h.success(h.pending.shift());await new Promise(r=>setImmediate(r));
  assert.equal(h.c.state.quota.data.weekly.percent,37);assert.equal(h.notices.length,0);
});

test('transient quota automatically recovers without opening Plan and backoff is bounded', async () => {
  const h=quotaUiHarness();let p=h.c.loadOpenCodeUsage();h.pending.shift().reject(Object.assign(Error('temporary'),{code:'unavailable',status:503}));await p;
  assert.equal(h.notices.length,0);assert.equal([...h.timers.values()][0].ms,5000);
  const timer=[...h.timers.values()][0];h.timers.clear();timer.fn();assert.match(h.pending[0].url,/refresh=1/);h.success(h.pending.shift());await new Promise(r=>setImmediate(r));
  assert.equal(h.c.state.quota.status,'ready');assert.equal(h.notices.length,0);assert.equal(h.timers.size,0);
  h.c.state.quota.fetchedAt=0;h.c.refreshOpenCodeUsageIfVisible();assert.equal(h.pending.length,1);h.success(h.pending.shift());
});

test('quota reports persistent errors once and manual refresh failures are not hidden', async () => {
  const h=quotaUiHarness();
  for(let i=0;i<7;i++){
    const p=h.c.loadOpenCodeUsage(true,true);h.pending.shift().reject(Object.assign(Error('temporary'),{code:'unavailable',status:503}));await p;
    assert.ok([...h.timers.values()][0].ms<=60000);
  }
  assert.equal(h.notices.length,1);
  const p=h.c.loadOpenCodeUsage(true);h.pending.shift().reject(Object.assign(Error('bad response'),{code:'invalid_response',status:502}));await p;
  assert.equal(h.notices.length,2);
  const recovered=h.c.loadOpenCodeUsage(true);h.success(h.pending.shift());await recovered;
  assert.equal(h.c.state.quota.retryTimer,0);assert.equal(h.timers.size,0);
});

test('late quota response cannot restore data after lock or profile switch', async () => {
  const h=quotaUiHarness(),old=h.c.loadOpenCodeUsage();h.handlers['igorek:profiles-changed']();
  h.success(h.pending.shift(),99);await old;assert.equal(h.c.state.quota.data,null);
  h.c.profile='second';const current=h.c.loadOpenCodeUsage();
  h.success(h.pending.shift(),88);await new Promise(r=>setImmediate(r));assert.equal(h.c.state.quota.data,null);
  h.success(h.pending.shift(),12);await current;assert.equal(h.c.state.quota.profile,'second');assert.equal(h.c.state.quota.data.weekly.percent,12);
});

test('quiet API errors remain typed and ordinary failures still create notifications', async () => {
  const vm=require('node:vm'),source=require('node:fs').readFileSync(require('node:path').join(__dirname, '..','../factory/web_ui/static/app.js'),'utf8'),notices=[],requests=[];
  const c={fetch:async(path,options)=>{requests.push(options);return {ok:false,status:423,json:async()=>({code:'vault_locked',error:'locked'})};},desktopNotification:(...args)=>notices.push(args),t:v=>v,showConnection(){}};
  vm.createContext(c);vm.runInContext(source.slice(source.indexOf('async function api('),source.indexOf('function toast(')),c);
  await assert.rejects(c.api('/quota',{notify:false}),e=>e.code==='vault_locked'&&e.status===423);assert.equal(notices.length,0);assert.equal('notify' in requests[0],false);
  await assert.rejects(c.api('/explicit'));assert.equal(notices.length,1);
});

test('quota connection confirms the existing account without browser login or polling', async () => {
  const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
  const source=fs.readFileSync(path.join(__dirname,'../../factory/web_ui/static/app.js'),'utf8');
  const model={},calls=[],notices=[];let refreshed=0;
  const c={window:{IgorekDesktop:{quota:async(action,profile)=>{calls.push([action,profile]);return {state:'connected'};}}},
    currentQuotaModel:()=>model,currentSessionProfile:()=> 'fixture',renderPlanSection(){},
    loadOpenCodeUsage:async force=>{assert.equal(force,true);refreshed++;},toast:(...args)=>notices.push(args),
    codexQuotaError:()=> 'quota-cancelled',setTimeout:()=>{throw Error('Quota must not poll for login');}};
  vm.createContext(c);
  vm.runInContext(source.slice(source.indexOf('async function connectCodexQuota('),source.indexOf('async function loadOpenCodeUsage(')),c);
  await c.connectCodexQuota('fixture');
  assert.deepEqual(calls,[['quota_begin','fixture']]);assert.equal(refreshed,1);assert.equal(model.quotaConnecting,false);
  assert.equal(notices[0][0],'Счётчик подключён');
  c.window.IgorekDesktop.quota=async()=>({error:'quota_cancelled'});
  await c.connectCodexQuota('fixture');assert.equal(refreshed,1);assert.equal(notices.at(-1)[1],'error');
});

for(const flag of ['quitting','installingUpdate']) {
  test('owner IPC refuses backend work during '+flag,async()=>{
    const h=ownerKeyIpcHarness('status');h.context[flag]=true;
    const result=await h.call(h.event,{});
    assert.equal(result.error,'desktop_updating');assert.equal(h.calls.length,0);
  });
}
