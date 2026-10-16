const { spawn } = require('node:child_process');
const { readFileSync, mkdirSync, realpathSync } = require('node:fs');
const { join, resolve, relative, isAbsolute, sep } = require('node:path');
const { EventEmitter } = require('node:events');
const { randomUUID } = require('node:crypto');
const { settings } = require('./policy.cjs');
const OWNER_READ_ACTIONS = new Set(['status', 'readiness', 'image_status', 'execution_mode',
  'list_profiles', 'list_values', 'value_access', 'private_mcp_status', 'host_mcp_access',
  'mcp_access', 'gmail_status', 'plugins_status', 'calendar_list']);
const OWNER_QUEUE_ACTIONS = new Set([...OWNER_READ_ACTIONS, 'host_service', 'opencode_services',
  'opencode_profiles', 'mobile_configure', 'mobile_start', 'mobile_status', 'mobile_stop',
  'mobile_revoke', 'quota_begin', 'quota_status', 'quota_cancel', 'quota_confirm']);
const canonicalPath = value => process.platform === 'win32' ? realpathSync.native(value) : realpathSync(value);

function runtimeEnvironment(root, source = process.env, platform = process.platform, bundle = null) {
  const paths = platform === 'win32' ? require('node:path').win32 : require('node:path').posix;
  const { join, isAbsolute } = paths;
  const env = { PATH: platform === 'win32' ? source.PATH || '' : '/usr/bin:/bin', LANG: source.LANG || 'C.UTF-8',
    HOME: join(root, 'home'), XDG_CONFIG_HOME: join(root, 'home', '.config'),
    XDG_DATA_HOME: join(root, 'home', '.local', 'share'), XDG_CACHE_HOME: join(root, 'cache'),
    FACTORY_DATA_DIR: root, IGOREK_FACTORY_HOME: root,
    FACTORY_ENV_FILES_DIR: join(root, 'env'), PYTHONUNBUFFERED: '1', PYTHONNOUSERSITE: '1', PYTHONUTF8: '1',
    IGOREK_ISOLATION_MODE: 'off' };
  if (platform === 'win32') {
    const systemRoot = source.SystemRoot || source.SYSTEMROOT || source.WINDIR;
    if (typeof systemRoot !== 'string' || !isAbsolute(systemRoot)) throw new Error('Не найден системный каталог Windows.');
    env.SystemRoot = systemRoot;
    env.WINDIR = systemRoot;
    env.COMSPEC = join(systemRoot, 'System32', 'cmd.exe');
    env.PATHEXT = source.PATHEXT || '.COM;.EXE;.BAT;.CMD';
    env.USERPROFILE = join(root, 'home');
    env.APPDATA = join(root, 'home', 'AppData', 'Roaming');
    env.LOCALAPPDATA = join(root, 'home', 'AppData', 'Local');
    env.TEMP = join(root, 'cache');
    env.TMP = env.TEMP;
    if (!env.PATH) env.PATH = join(systemRoot, 'System32') + ';' + systemRoot;
    if (bundle) env.PATH = join(bundle, 'tools') + ';' + env.PATH;
  }
  const hostHome = source.HOME || (platform === 'win32' ? source.USERPROFILE : undefined);
  if (typeof hostHome === 'string' && isAbsolute(hostHome)) {
    const roots = {
      FACTORY_GMAIL_HOST_VAULT: join(hostHome, '.igorek-agent-factory', 'vault'),
      FACTORY_EXTERNAL_CLAUDE_HOME: source.FACTORY_EXTERNAL_CLAUDE_HOME || source.CLAUDE_CONFIG_DIR || join(hostHome, '.claude'),
      FACTORY_EXTERNAL_CODEX_HOME: source.FACTORY_EXTERNAL_CODEX_HOME || source.CODEX_HOME || join(hostHome, '.codex'),
      FACTORY_EXTERNAL_OPENCODE_DB: source.FACTORY_EXTERNAL_OPENCODE_DB || join(source.XDG_DATA_HOME && isAbsolute(source.XDG_DATA_HOME) ? source.XDG_DATA_HOME : join(hostHome, '.local', 'share'), 'opencode', 'opencode.db'),
      FACTORY_WALLY_CREDENTIALS: source.FACTORY_WALLY_CREDENTIALS || join(source.XDG_CONFIG_HOME && isAbsolute(source.XDG_CONFIG_HOME) ? source.XDG_CONFIG_HOME : join(hostHome, '.config'), 'wally', 'credentials.json'),
      AGENTFACTORY_LEGACY_SKILLS_PATH: source.AGENTFACTORY_LEGACY_SKILLS_PATH || join(source.XDG_CONFIG_HOME && isAbsolute(source.XDG_CONFIG_HOME) ? source.XDG_CONFIG_HOME : join(hostHome, '.config'), 'igorek', 'legacy-skills'),
      FACTORY_SESSION_EMBEDDINGS_LLAMASERVER_BIN: source.FACTORY_SESSION_EMBEDDINGS_LLAMASERVER_BIN || join(hostHome, '.local', 'bin', 'igorek-llama-server'),
    };
    for (const [name, path] of Object.entries(roots)) {
      if (typeof path === 'string' && isAbsolute(path)) env[name] = path;
    }
  }
  return env;
}
function bundleFile(root, name) {
  if (typeof name !== 'string' || isAbsolute(name)) throw new Error('Некорректный путь в комплекте Python.');
  const path = realpathSync(join(root, name));
  const rel = relative(realpathSync(root), path);
  if (rel === '..' || rel.startsWith('..' + sep) || isAbsolute(rel)) throw new Error('Путь выходит за пределы комплекта Python.');
  return path;
}
function loadBundle(root) {
  const manifest = JSON.parse(readFileSync(join(root, 'manifest.json'), 'utf8'));
  if (manifest.schema_version !== 1) throw new Error('Неизвестный формат комплекта Python.');
  return { python: bundleFile(root, manifest.python), entry: bundleFile(root, manifest.entry) };
}
class OwnedRuntime extends EventEmitter {
  constructor({ bundle, dataDir, timeoutMs = 20000, stopTimeoutMs = 7000 }) {
    super(); this.bundle = bundle; this.dataDir = resolve(dataDir); this.timeoutMs = timeoutMs; this.stopTimeoutMs = stopTimeoutMs;
    this.child = null; this.stopping = false;
    this.requests = new Map();
    this.ownerQueue = [];
  }
  async start() {
    if (this.child) throw new Error('Backend уже запущен.');
    const { python, entry } = loadBundle(this.bundle);
    mkdirSync(this.dataDir, { recursive: true, mode: 0o700 });
    this.dataDir = canonicalPath(this.dataDir);
    mkdirSync(join(this.dataDir, 'home'), { recursive: true, mode: 0o700 });
    mkdirSync(join(this.dataDir, 'cache'), { recursive: true, mode: 0o700 });
    this.child = spawn(python, ['-I', entry, this.dataDir], {
      cwd: this.dataDir, env: runtimeEnvironment(this.dataDir, process.env, process.platform, this.bundle), shell: false, windowsHide: true,
      stdio: ['pipe', 'pipe', 'ignore'],
    });
    this.child.stdin.on('error', () => {});
    const child = this.child;
    child.once('exit', (code, signal) => {
      for (const request of this.requests.values()) request.reject(new Error('Backend остановился.'));
      for (const request of this.ownerQueue.splice(0)) request.reject(new Error('Backend остановился.'));
      if (!this.stopping) this.emit('failed', { code, signal });
    });
    try {
      return await new Promise((resolveReady, reject) => {
        let pending = '';
        const timer = setTimeout(() => reject(new Error('Backend не запустился вовремя.')), this.timeoutMs);
        const finish = (error, value) => { clearTimeout(timer); error ? reject(error) : resolveReady(value); };
        child.once('error', () => finish(new Error('Не удалось запустить комплектный Python.')));
        child.once('exit', () => finish(new Error('Backend завершился при запуске.')));
        child.stdout.on('data', data => {
          pending += data.toString();
          if (pending.length > 24 * 1024 * 1024) { finish(new Error('Некорректный ответ backend.')); return; }
          while (pending.includes('\n')) {
            const end = pending.indexOf('\n'), line = pending.slice(0, end); pending = pending.slice(end + 1);
            try {
              const event = JSON.parse(line);
              if (event.type === 'browser_request') {
                if (typeof event.id !== 'string' || !/^[a-f0-9]{32}$/.test(event.id)) continue;
                const respond = result => {
                  if (this.child === child && !this.stopping && child.exitCode === null)
                    child.stdin.write(JSON.stringify({type:'browser_reply',id:event.id,result}) + '\n');
                };
                Promise.resolve().then(() => this.browserHandler ? this.browserHandler(event.fields) : {error:'desktop_browser_unavailable'})
                  .then(respond, () => respond({error:'desktop_browser_failed'}));
                continue;
              }
              if (event.type === 'owner_reply') {
                const request = this.requests.get(event.id);
                if (request) { if (event.result && Object.hasOwn(event.result, 'state')) this.emit('owner-state', event.result); request.resolve(event); }
                continue;
              }
              if (event.type === 'error') { finish(new Error('Локальный backend не запустился.')); return; }
              if (event.type === 'ready') {
                if (!Number.isInteger(event.web_port) || event.web_port < 1 || event.web_port > 65535) {
                  finish(new Error('Backend передал некорректный порт.')); return;
                }
                if (typeof event.pairing_code !== 'string' || event.pairing_code.length > 32) {
                  finish(new Error('Backend передал некорректный код сопряжения.')); return;
                }
                if (typeof event.cert !== 'string'
                    || canonicalPath(event.cert) !== canonicalPath(join(this.dataDir, 'web-ui', 'tls', 'localhost-cert.pem'))
                    || relative(this.dataDir, canonicalPath(event.cert)) !== join('web-ui', 'tls', 'localhost-cert.pem')) {
                  finish(new Error('Сертификат backend находится вне ожидаемого каталога.')); return;
                }
                const config = settings(['--url', `https://127.0.0.1:${event.web_port}`, '--cert', event.cert], {});
                finish(null, { ...config, pairingCode: event.pairing_code,
                  factoryVersion: typeof event.factory_version === 'string' && event.factory_version.length <= 64 ? event.factory_version : 'неизвестна' });
              }
            } catch { finish(new Error('Некорректный ответ backend.')); }
          }
        });
      });
    } catch (error) { await this.stop(); throw error; }
  }
  requestOwner(action, fields, fromQueue = false) {
    const expected = { status: [], gmail_status: [], gmail_import_host: ['password'], gmail_configure: ['source', 'password'], gmail_begin: ['password'], gmail_disconnect: ['password'], private_mcp_start: ['server', 'credential_id', 'message'], private_mcp_status: [], private_mcp_clear: [], host_mcp_access: [], set_host_mcp_access: ['server', 'credential_id', 'access', 'password'], mcp_access: [], set_mcp_access: ['server', 'credential_id', 'access', 'password'], read_legacy_profile: ['source'], configure_interface_lock: ['password', 'enabled'], lock_interface: [], unlock_interface: ['password'], set_interface_pin: ['password', 'pin'], remove_interface_pin: ['password'], unlock_interface_pin: ['pin'], readiness: [], image_status: [], execution_mode: [], select_execution_mode: ['mode'], prepare_image: ['source'], recover_image: ['source'], change_password: ['current', 'new_password'], create: ['password'], unlock: ['password'], lock: [],
      generate_phrase: [], export_recovery: ['password', 'recovery_phrase'], verify_recovery: ['backup', 'recovery_phrase'],
      restore_recovery: ['backup', 'recovery_phrase', 'new_password'], model_catalog: ['preset', 'api_key', 'credential_id'], delete_profile: ['credential_id', 'password'], rename_profile: ['credential_id', 'password', 'new_name'], list_profiles: [],
      reveal_profile_key: ['credential_id', 'password'], save_profile: ['name', 'model', 'base_url', 'api_format', 'api_key'], update_profile: ['credential_id', 'name', 'model', 'base_url', 'api_format', 'api_key'], check_profile: ['credential_id'], start_agent: ['credential_id'], stop_agent: [] };
    Object.assign(expected, { list_values: [], save_value: ['credential_id', 'name', 'kind', 'value', 'password'],
      reveal_value: ['credential_id', 'password'], delete_value: ['credential_id', 'password'],
      value_access: ['credential_id'], grant_value_access: ['credential_id', 'server', 'profile_id', 'env_name', 'password'],
      revoke_value_access: ['credential_id', 'grant_id', 'password'] });
    Object.assign(expected, {prepare_env_import: ['source'], commit_env_import: ['draft_id', 'selection', 'password'], cancel_env_import: ['draft_id']});
    Object.assign(expected, {chatgpt_begin: ['name', 'credential_id'], chatgpt_status: [], chatgpt_cancel: [],
      chatgpt_models: ['credential_id'], chatgpt_disconnect: ['credential_id', 'password']});
    Object.assign(expected, {mobile_configure: ['port', 'public_url'], mobile_start: [], mobile_status: [], mobile_stop: [], mobile_revoke: ['device_id'],
      quota_begin: ['profile'], quota_status: ['profile'], quota_cancel: ['profile'], quota_confirm: ['profile','link_id']});
    Object.assign(expected, {plugins_status: [], plugin_action: ['plugin','operation'], calendar_connect: ['profile'], calendar_disable: [], session_agent: ['operation','profile'],
      browser_read_session: [], host_service: ['operation','service'], opencode_services: ['operation','config'], opencode_profiles: ['operation','config'],
      calendar_configure: ['source','password'], calendar_begin: ['password'], calendar_disconnect: ['password'],
      calendar_list: [], calendar_select: ['calendar_id','password'], private_mcp_approve: ['request_id','decision']});
    if (!Object.hasOwn(expected, action)) return Promise.reject(new Error('Недопустимое действие.'));
    if (!this.child || this.stopping || this.child.exitCode !== null || this.child.signalCode !== null)
      return Promise.reject(new Error('Backend недоступен.'));
    const id = randomUUID(), message = { id, action };
    if (typeof fields === 'string') fields = { password: fields };
    fields ??= {};
    if (typeof fields !== 'object' || Array.isArray(fields) || Object.keys(fields).some(key => !expected[action].includes(key)))
      return Promise.reject(new Error('Некорректные данные.'));
    if (action === 'host_service' && (!['list','status','start','stop'].includes(fields.operation)
      || (fields.operation === 'list' ? fields.service !== '' : !/^[a-z][a-z0-9_-]{0,39}$/.test(fields.service))))
      return Promise.reject(new Error('Некорректные данные.'));
    for (const key of expected[action]) {
      const value = fields[key], maximum = key === 'backup' ? 22369624 : ['opencode_services','opencode_profiles'].includes(action) && key === 'config' ? 65536 : action === 'commit_env_import' && key === 'selection' ? 32768 : 1024;
      const allowEmpty = (action === 'host_service' && key === 'service' && fields.operation === 'list') || (action === 'mobile_configure' && key === 'public_url') || (action === 'chatgpt_begin' && key === 'credential_id') || (action==='session_agent'&&key==='profile'&&fields.operation==='catalog') || (['save_profile', 'update_profile'].includes(action) && key === 'api_key') || (action === 'model_catalog' && ['api_key', 'credential_id'].includes(key)) || (action === 'save_value' && ['credential_id', 'value'].includes(key));
      if (action === 'save_value' && key === 'value' && typeof value === 'string' && value.length <= maximum) { message[key] = value; continue; }
      if (typeof value !== 'string' || (!value.trim() && !(allowEmpty && value === '')) || value.length > maximum)
        return Promise.reject(new Error('Некорректные данные.'));
      message[key] = value;
    }
    if (this.requests.size || (!fromQueue && this.ownerQueue.length)) {
      const validated = Object.fromEntries(expected[action].map(key => [key, message[key]]));
      if (OWNER_READ_ACTIONS.has(action)) {
        const pending = this.ownerQueue.find(item => item.action === action
          && JSON.stringify(item.fields) === JSON.stringify(validated));
        if (pending) return pending.promise;
      }
      if (!OWNER_QUEUE_ACTIONS.has(action) || this.ownerQueue.length >= 8)
        return Promise.reject(Object.assign(new Error('Дождитесь завершения операции.'), { code: 'operation_busy' }));
      let resolve, reject;
      const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
      this.ownerQueue.push({ action, fields: validated, resolve, reject, promise });
      queueMicrotask(() => this.drainOwnerQueue());
      return promise;
    }
    return new Promise((resolveReply, reject) => {
      const finish = (callback, value) => { clearTimeout(timer); this.requests.delete(id); callback(value); queueMicrotask(() => this.drainOwnerQueue()); };
      const timer = setTimeout(() => finish(reject, new Error('Ответ не получен. Проверьте состояние хранилища.')), ['host_service','plugin_action','calendar_connect','mobile_revoke'].includes(action) ? 90000 : action === 'check_profile' ? 130000 : ['model_catalog', 'chatgpt_models', 'chatgpt_disconnect', 'chatgpt_status','quota_begin','quota_status','quota_cancel','quota_confirm'].includes(action) ? 50000 : action === 'mobile_start' ? 30000 : 15000);
      this.requests.set(id, { resolve: value => finish(resolveReply, value), reject: error => finish(reject, error) });
      this.child.stdin.write(JSON.stringify(message) + '\n', error => {
        if (error) finish(reject, new Error('Backend недоступен.'));
      });
    });
  }
  drainOwnerQueue() {
    if (this.requests.size || !this.ownerQueue.length) return;
    const request = this.ownerQueue.shift();
    this.requestOwner(request.action, request.fields, true).then(request.resolve, request.reject).finally(() => this.drainOwnerQueue());
  }
  async stop() {
    this.stopping = true;
    for (const request of this.ownerQueue.splice(0)) request.reject(new Error('Backend остановился.'));
    const child = this.child;
    if (!child || !child.pid || child.exitCode !== null || child.signalCode !== null) return;
    await new Promise(resolveStopped => {
      const force = setTimeout(() => child.kill('SIGKILL'), this.stopTimeoutMs);
      child.once('exit', () => { clearTimeout(force); resolveStopped(); });
      child.stdin.end('stop\n');
    });
  }
}
module.exports = { OwnedRuntime, runtimeEnvironment, bundleFile, loadBundle };
