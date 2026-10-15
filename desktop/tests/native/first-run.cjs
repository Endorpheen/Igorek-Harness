const { app, BrowserWindow, ipcMain } = require('electron');
const assert = require('node:assert/strict');
const { X509Certificate } = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

if (!['linux', 'win32'].includes(process.platform) || !path.isAbsolute(process.env.IGOREK_TEST_RUNTIME || '')) {
  console.error('Provide an absolute IGOREK_TEST_RUNTIME for native desktop acceptance.');
  app.exit(2);
} else {
  run().catch(error => { console.error(error.message); app.exit(1); });
}

async function run() {
  const bundle = fs.realpathSync(process.env.IGOREK_TEST_RUNTIME);
  const desktop = path.resolve(__dirname, '../..');
  const appRoot = fs.existsSync(path.join(path.dirname(bundle), 'runtime.cjs')) ? path.dirname(bundle) : desktop;
  const { OwnedRuntime, runtimeEnvironment, loadBundle } = require(path.join(appRoot, 'runtime.cjs'));
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'igorek-native-first-run-'));
  const output = process.env.IGOREK_TEST_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'igorek-native-report-'));
  if (!path.isAbsolute(output)) throw Error('IGOREK_TEST_OUTPUT must be absolute.');
  fs.mkdirSync(output, { recursive: true, mode: 0o700 });
  const report = { status: 'running', platform: process.platform, stages: [], provider: 'synthetic_loopback' };
  const stage = name => { report.stages.push(name); console.log('PASS ' + name); };
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  let runtime, server, chat, settings, config, closing;
  const calls = [];
  const navigationHints = [];
  const navigationPreload = path.join(root, 'navigation-preload.cjs');
  fs.writeFileSync(navigationPreload, "const {contextBridge,ipcRenderer}=require('electron'); contextBridge.exposeInMainWorld('IgorekDesktop',{navigation:(action,id)=>ipcRenderer.invoke('igorek:ui-navigation',action,id)});");

  // OwnedRuntime reads these host paths explicitly; give every one a synthetic root.
  process.env.HOME = path.join(root, 'host');
  process.env.XDG_CONFIG_HOME = path.join(root, 'host/config');
  process.env.XDG_DATA_HOME = path.join(root, 'host/data');
  for (const name of ['FACTORY_EXTERNAL_CODEX_HOME', 'FACTORY_EXTERNAL_CLAUDE_HOME',
    'FACTORY_EXTERNAL_OPENCODE_DB', 'FACTORY_WALLY_CREDENTIALS',
    'AGENTFACTORY_LEGACY_SKILLS_PATH', 'FACTORY_SESSION_EMBEDDINGS_LLAMASERVER_BIN']) {
    process.env[name] = path.join(root, 'absent', name);
  }
  app.setPath('userData', path.join(root, 'electron'));
  app.on('window-all-closed', () => {});

  async function until(predicate, label, seconds = 30) {
    const deadline = Date.now() + seconds * 1000;
    while (Date.now() < deadline) {
      if (await predicate()) return;
      await pause(100);
    }
    throw Error(label);
  }
  async function owner(action, fields = {}) {
    const reply = await runtime.requestOwner(action, fields);
    if (reply.error) throw Error('Owner action ' + action + ': ' + reply.error);
    return reply.result;
  }
  async function api(route, body) {
    const options = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    };
    return chat.webContents.executeJavaScript(`(async()=>{
      const response=await fetch(${JSON.stringify(route)},${JSON.stringify(options)});
      const value=await response.json();
      if(!response.ok) throw Error('Test API failed: '+response.status+' '+${JSON.stringify(route)}+' '+String(value?.code||value?.error?.code||value?.error?.type||'unknown'));
      return value;
    })()`);
  }
  function cleanup() {
    if (!closing) closing = (async () => {
      chat?.destroy(); settings?.destroy();
      await runtime?.stop();
      if (server) await new Promise(resolve => { server.close(resolve); server.closeAllConnections(); });
      // Windows keeps Chromium profile handles until Electron has exited.
      // The ephemeral runner removes that fixture after this process finishes.
      if (process.platform !== 'win32') fs.rmSync(root, { recursive: true, force: true });
    })();
    return closing;
  }
  const timeout = setTimeout(() => {
    report.status = 'failed'; report.error = 'native_acceptance_timeout';
    fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(report, null, 2));
    cleanup().finally(() => app.exit(1));
  }, 240000);
  timeout.unref();
  try {
    await app.whenReady();
    await require('./update-flow.cjs').verifyUpdateFlow({appRoot,root,BrowserWindow,ipcMain,python:loadBundle(bundle).python});
    server = http.createServer((request, response) => {
      let raw = '';
      request.on('data', chunk => { raw += chunk; if (raw.length > 1_000_000) request.destroy(); });
      request.on('end', () => {
        if (request.method === 'GET') {
          response.setHeader('Content-Type', 'application/json');
          response.end(JSON.stringify({ data: [{ id: 'fixture-model' }] })); return;
        }
        const body = JSON.parse(raw);
        navigationHints.push((body.messages || []).some(item => item.role === 'system' && String(item.content).includes('igorek://ui/notifications')));
        calls.push(request.headers.authorization === 'Bearer fixture-only-key');
        const message = [...(body.messages || [])].reverse().find(item => item.role === 'user');
        const text = typeof message?.content === 'string' ? message.content : JSON.stringify(message?.content);
        let answer = text?.includes('fixture-second-ready') ? 'fixture-second-ready' : 'fixture-onboard-ready';
        if (text?.includes('fixture-navigation-help')) answer = 'Настройки основной модели здесь: [Открыть настройки модели](igorek://ui/models).';
        let toolCall;
        const demoStep = text?.match(/This is step (\d+)\/8 of the built-in introductory demo/);
        if (demoStep) {
          answer = 'Здравствуйте! Я Игорёк. Покажу следующий шаг и объясню результат.';
          if (!(body.messages || []).some(item => item.role === 'tool')) {
            const step = Number(demoStep[1]);
            const plan = ['Create a note', 'Read and edit it', 'Find the note and its text'].map((step, index) => ({
              step, status: Number(demoStep[1]) === 8 ? 'completed' : index === 0 ? 'in_progress' : 'pending',
            }));
            const actions = {
              2: ['update_plan', { plan }],
              3: ['Write', { file_path: 'demo-note.txt', content: text.split('with exactly this content:\n')[1] }],
              4: ['Read', { file_path: 'demo-note.txt' }],
              5: ['Edit', { file_path: 'demo-note.txt', old_string: 'Давайте попробуем вместе.', new_string: 'Теперь можно дать мне свою задачу.' }],
              6: ['Glob', { pattern: '*.txt', path: '.' }],
              7: ['Grep', { pattern: 'файлами', path: 'demo-note.txt' }],
              8: ['update_plan', { plan }],
            };
            if (actions[step]) toolCall = { id: 'demo-call-' + step, type: 'function',
              function: { name: actions[step][0], arguments: JSON.stringify(actions[step][1]) } };
          }
        }
        const finish = toolCall ? 'tool_calls' : 'stop';
        const content = toolCall ? { tool_calls: [{ index: 0, ...toolCall }] } : { content: answer };
        if (body.stream) {
          response.writeHead(200, { 'Content-Type': 'text/event-stream' });
          response.end('data: ' + JSON.stringify({ id: 'fixture', choices: [{ index: 0, delta: content, finish_reason: null }] })
            + '\n\ndata: ' + JSON.stringify({ id: 'fixture', choices: [{ index: 0, delta: {}, finish_reason: finish }], usage: { prompt_tokens: 10, completion_tokens: 3 } })
            + '\n\ndata: [DONE]\n\n');
        } else {
          response.setHeader('Content-Type', 'application/json');
          response.end(JSON.stringify({ id: 'fixture', model: 'fixture-model',
            choices: [{ index: 0, message: { role: 'assistant', ...(toolCall ? { tool_calls: [toolCall] } : { content: answer }) }, finish_reason: finish }],
            usage: { prompt_tokens: 10, completion_tokens: 3 } }));
        }
      });
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const dataDir = path.join(root, 'data');
    runtime = new OwnedRuntime({ bundle, dataDir, timeoutMs: 40000 });
    config = await runtime.start(); report.version = config.factoryVersion;
    const fileContract = `import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from factory.core.mcp_bridge import set_desktop_access_check
set_desktop_access_check(lambda server: False)
from factory.core.cc_backend_tools import _tool_write, _tool_read, _tool_glob, _tool_grep
root=Path(sys.argv[1]); path=root/'fixture.txt'
_tool_write({'file_path':str(path),'content':'Привет, Игорь!\\n--pre=fixture\\n'},str(root),'workspace',None)
assert 'Привет' in _tool_read({'file_path':str(path)},str(root),'workspace')
assert 'fixture.txt' in _tool_glob({'pattern':'*.txt'},str(root),'workspace')
assert '--pre=fixture' in _tool_grep({'pattern':'--pre=fixture','path':'fixture.txt'},str(root),'workspace')
assert 'Привет' in _tool_grep({'pattern':'Привет','path':'fixture.txt'},str(root),'workspace')
try:
 _tool_read({'file_path':str(root.parent/'outside.txt')},str(root),'workspace')
 raise AssertionError('Workspace escape was accepted')
except ValueError: pass
path.unlink()
print(json.dumps({'files':True}))`;
    const files = execFileSync(loadBundle(bundle).python, ['-I', '-c', fileContract, path.join(dataDir, 'workspace'), path.join(bundle, 'packages')], {
      env: runtimeEnvironment(dataDir, process.env, process.platform, bundle), encoding: 'utf8', timeout: 30000,
    });
    assert.equal(JSON.parse(files.trim().split('\n').at(-1)).files, true);
    stage('packaged_file_tools_and_workspace_scope');
    assert.equal((await owner('status')).state, 'not_initialized');
    await owner('create', { password: 'fixture-password' });
    const saved = await owner('save_profile', { name: 'qa-first-task', model: 'fixture-model',
      base_url: 'http://127.0.0.1:' + server.address().port + '/v1', api_format: 'openai', api_key: 'fixture-only-key' });
    const identity = saved.saved_profile.credential_id;
    assert.equal((await owner('check_profile', { credential_id: identity })).check.status, 'verified');
    stage('clean_vault_and_verified_model_profile');
    await owner('start_agent', { credential_id: identity });
    assert.equal((await owner('status')).agent.running, true);
    assert.deepEqual((await owner('status')).onboarding.successful_profiles, []);
    stage('running_worker_does_not_complete_first_result');
    ipcMain.handle('igorek:owner-vault', (_, action, fields) => runtime.requestOwner(action, fields));
    ipcMain.handle('igorek:completion-sound', () => ({ enabled: false, background: false }));
    ipcMain.handle('igorek:notifications', () => []);
    ipcMain.handle('igorek:release-info', () => ({ version: config.factoryVersion, changes: [] }));
    ipcMain.handle('igorek:browser-control-settings', () => ({ manual: false }));
    const navigation = require(path.join(appRoot, 'ui-navigation.cjs'));
    ipcMain.handle('igorek:ui-navigation', async (_, action, id) => {
      if (action === 'catalog') return { routes: navigation.catalog };
      const route = navigation.findRoute(id);
      if (!route || route.unavailable) return { error: 'invalid_request' };
      if (route.kind === 'settings') {
        if (!settings || settings.isDestroyed()) {
          settings = new BrowserWindow({ show: true, width: 1280, height: 900,
            webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(appRoot, 'owner-preload.cjs') } });
          await settings.loadFile(path.join(appRoot, 'owner.html'));
        }
        settings.webContents.send('igorek:owner-panel', route.target);
      }
      return { opened: true, route: route.id, kind: route.kind, target: route.target };
    });
    settings = new BrowserWindow({ show: true, width: 1280, height: 900,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(appRoot, 'owner-preload.cjs') } });
    await settings.loadFile(path.join(appRoot, 'owner.html'));
    await until(() => settings.webContents.executeJavaScript("document.getElementById('onboarding-steps').children.length===5"), 'Five-step guide missing');
    chat = new BrowserWindow({ show: true, width: 1360, height: 900,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: navigationPreload } });
    const fingerprint = new X509Certificate(fs.readFileSync(path.join(dataDir, 'web-ui/tls/localhost-cert.pem'))).fingerprint256;
    chat.webContents.session.setCertificateVerifyProc((request, callback) => {
      let valid = false;
      try { valid = request.hostname === '127.0.0.1' && new X509Certificate(request.certificate.data).fingerprint256 === fingerprint; } catch {}
      callback(valid ? 0 : -3);
    });
    await chat.loadURL(config.origin);
    await api('/api/pair', { code: config.pairingCode });
    await chat.loadURL(config.origin);
    const session = await api('/api/sessions', { profile: 'qa-first-task', cwd: path.join(dataDir, 'workspace'), idempotency_key: 'qa-session' });
    const route = '/api/sessions/' + session.session_id;
    await api(route + '/messages', { text: 'Reply exactly fixture-onboard-ready. Do not call tools.', idempotency_key: 'qa-first-message' });
    await until(async () => {
      const value = await api(route);
      return !value.running_task_id && value.messages.some(item => item.role === 'assistant' && item.text?.includes('fixture-onboard-ready'));
    }, 'First task did not finish', 60);
    assert.deepEqual((await owner('status')).onboarding.successful_profiles, ['qa-first-task']);
    settings.show(); settings.focus();
    await until(() => settings.webContents.executeJavaScript("document.getElementById('onboarding-steps').children[4].classList.contains('complete')"), 'Native guide did not show first result');
    stage('https_pairing_first_task_and_native_progress');
    await owner('lock'); assert.equal((await owner('status')).state, 'locked');
    await owner('unlock', { password: 'fixture-password' });
    assert.deepEqual((await owner('status')).onboarding.successful_profiles, ['qa-first-task']);
    stage('lock_unlock_preserves_first_result');
    await chat.webContents.session.cookies.flushStore();
    chat.destroy(); chat = null;
    settings.destroy(); settings = null;
    await runtime.stop(); assert.equal(runtime.child.exitCode, 0);
    runtime = new OwnedRuntime({ bundle, dataDir, timeoutMs: 40000 });
    config = await runtime.start();
    assert.equal(new X509Certificate(fs.readFileSync(path.join(dataDir, 'web-ui/tls/localhost-cert.pem'))).fingerprint256, fingerprint);
    assert.equal((await owner('status')).state, 'locked');
    await owner('unlock', { password: 'fixture-password' });
    assert.deepEqual((await owner('status')).onboarding.successful_profiles, ['qa-first-task']);
    assert.equal((await owner('list_profiles')).profiles[0].credential_id, identity);
    chat = new BrowserWindow({ show: true, width: 1360, height: 900,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: navigationPreload } });
    chat.show(); chat.focus();
    await chat.loadURL(config.origin);
    assert.equal((await api('/api/status')).paired, true);
    assert.ok((await api(route)).messages.some(item => item.text?.includes('fixture-onboard-ready')));
    const selector = '[data-session-id="' + session.session_id + '"] .session-main';
    await until(() => chat.webContents.executeJavaScript('Boolean(document.querySelector(' + JSON.stringify(selector) + '))'), 'Restored session missing from native sidebar');
    await chat.webContents.executeJavaScript('document.querySelector(' + JSON.stringify(selector) + ').click()');
    await until(() => chat.webContents.executeJavaScript("document.getElementById('timeline').textContent.includes('fixture-onboard-ready')"), 'Restored history missing from native timeline');
    stage('full_restart_keeps_profile_cookie_history_and_first_result');
    await owner('start_agent', { credential_id: identity });
    await api(route + '/messages', { text: 'Reply exactly fixture-second-ready. Do not call tools.', idempotency_key: 'qa-second-message' });
    await until(async () => {
      const value = await api(route);
      return !value.running_task_id && value.messages.some(item => item.role === 'assistant' && item.text?.includes('fixture-second-ready'));
    }, 'Task after restart did not finish', 60);
    assert.ok(calls.length >= 3 && calls.every(Boolean));
    stage('continued_session_after_restart');
    await chat.webContents.executeJavaScript("document.getElementById('message-input').value='/demo'; document.getElementById('composer-form').requestSubmit()");
    let demoId;
    await until(async () => {
      demoId = await chat.webContents.executeJavaScript("state.session?.meta?.demo ? state.selected : null");
      return Boolean(demoId);
    }, 'Demo command did not open its own session');
    await until(async () => {
      const value = await api('/api/sessions/' + demoId);
      if (value.meta.demo.status === 'stopped') throw Error('Demo stopped: ' + JSON.stringify(value.meta.demo));
      return value.meta.demo.status === 'completed' && value.meta.demo.results.length === 8;
    }, 'Guided demo did not finish', 90);
    await until(() => chat.webContents.executeJavaScript("document.getElementById('timeline').textContent.includes('Никаких отчётов')"), 'Demo summary missing from chat');
    await until(() => chat.webContents.executeJavaScript("document.getElementById('stop-button').hidden"), 'Demo left the chat busy');
    const demoValue = await api('/api/sessions/' + demoId);
    assert.equal(fs.readFileSync(path.join(demoValue.meta.cwd, 'demo-note.txt'), 'utf8').includes('Теперь можно дать мне свою задачу.'), true);
    assert.ok(demoValue.meta.demo.results.every(item => item.passed));
    assert.equal((await api(route)).meta.demo, undefined);
    await chat.webContents.executeJavaScript('selectSession(' + JSON.stringify(demoId) + ')');
    await until(() => chat.webContents.executeJavaScript("document.getElementById('timeline').textContent.includes('Никаких отчётов')"), 'Demo history did not survive reopening');
    await pause(500);
    await chat.webContents.executeJavaScript("state.pinned=false; ui.scroll.style.scrollBehavior='auto'; ui.scroll.scrollTo({top:ui.scroll.scrollHeight,behavior:'instant'})");
    await until(() => chat.webContents.executeJavaScript("ui.scroll.scrollHeight-ui.scroll.scrollTop-ui.scroll.clientHeight<2"), 'Demo summary scroll did not settle');
    await pause(200);
    fs.writeFileSync(path.join(output, 'demo-chat.png'), (await chat.webContents.capturePage()).toPNG());
    await chat.webContents.executeJavaScript("state.pinned=false; ui.scroll.scrollTo({top:0,behavior:'instant'})");
    await until(() => chat.webContents.executeJavaScript("ui.scroll.scrollTop<2"), 'Demo intro scroll did not settle');
    await pause(200);
    fs.writeFileSync(path.join(output, 'demo-intro.png'), (await chat.webContents.capturePage()).toPNG());
    stage('guided_demo_real_tools_summary_and_history');
    assert.equal(await chat.webContents.executeJavaScript("state.uiNavigation.length"), navigation.catalog.length);
    await chat.webContents.executeJavaScript("document.querySelector('a[data-ui-route=plugins]').click()");
    await until(() => chat.webContents.executeJavaScript("!document.getElementById('plugins-view').hidden"), 'Plugin link did not open the actual section');
    await chat.webContents.executeJavaScript('selectSession(' + JSON.stringify(demoId) + ')');
    await until(() => chat.webContents.executeJavaScript("Boolean(document.querySelector('a[data-ui-route=notifications]'))"), 'Reopened demo link missing');
    await chat.webContents.executeJavaScript("document.querySelector('a[data-ui-route=notifications]').click()");
    await until(() => settings?.webContents.executeJavaScript("document.querySelector('.nav-button.active')?.textContent==='Уведомления'"), 'Notification link did not select the actual settings panel');
    chat.show(); chat.focus();
    await chat.webContents.executeJavaScript('selectSession(' + JSON.stringify(session.session_id) + ')');
    await chat.webContents.executeJavaScript("document.getElementById('message-input').value='fixture-navigation-help'; document.getElementById('composer-form').requestSubmit()");
    await until(() => chat.webContents.executeJavaScript("Boolean(document.querySelector('a[data-ui-route=models]'))"), 'Normal model answer did not produce a navigation link');
    assert.ok(navigationHints.at(-1));
    await chat.webContents.executeJavaScript("document.querySelector('a[data-ui-route=models]').click()");
    await until(() => settings.webContents.executeJavaScript("document.querySelector('.nav-button.active')?.textContent==='Основная модель'"), 'Model link did not select primary model settings');
    stage('desktop_navigation_links_and_settings');
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed'; report.error = error.message;
    if (chat && !chat.isDestroyed()) {
      report.ui = await chat.webContents.executeJavaScript(`({
        selected: typeof state==='object' ? state.selected : null,
        itemTypes: typeof state==='object' ? state.items.map(item=>item.type) : [],
        timeline: document.getElementById('timeline')?.textContent,
        alerts: Array.from(document.querySelectorAll('.toast')).map(item=>item.textContent)
      })`).catch(() => ({ error: 'renderer_unavailable' }));
      if (process.platform === 'win32' && report.ui.selected) {
        try {
          const session = await api('/api/sessions/' + report.ui.selected);
          if (session.meta?.demo) {
            const file = path.join(session.meta.cwd, 'demo-note.txt');
            const bytes = fs.readFileSync(file);
            const options = { env: runtimeEnvironment(runtime.dataDir, process.env, process.platform, bundle),
              encoding: 'utf8', timeout: 5000, maxBuffer: 8192 };
            report.demo_file_probe = { bytes: bytes.length, utf8ContainsPattern: bytes.toString('utf8').includes('файлами') };
            try {
              report.demo_file_probe.rg = execFileSync(path.join(bundle, 'tools/rg.exe'),
                ['--no-heading', '-n', '-e', 'файлами', '--', file], options);
            } catch (probe) {
              report.demo_file_probe.rg = { status: probe.status, stdout: String(probe.stdout || ''), stderr: String(probe.stderr || '') };
            }
          }
        } catch (probe) { report.demo_file_probe = { unavailable: true }; }
      }
    }
    console.error(error.stack);
  } finally {
    clearTimeout(timeout);
    fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(report, null, 2) + '\n');
    await cleanup();
  }
  console.log('Native acceptance report: ' + path.join(output, 'summary.json'));
  app.exit(report.status === 'passed' ? 0 : 1);
}
