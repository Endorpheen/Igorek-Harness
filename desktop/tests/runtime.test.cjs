const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync, chmodSync } = require('node:fs');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { execFileSync } = require('node:child_process');
const { OwnedRuntime, runtimeEnvironment, loadBundle } = require('../runtime.cjs');

test('Windows native account roots use USERPROFILE while backend HOME stays private', () => {
  const env = runtimeEnvironment('C:\\Igorek\\data', {
    SystemRoot: 'C:\\Windows', USERPROFILE: 'C:\\Users\\example',
  }, 'win32');
  assert.equal(env.HOME, 'C:\\Igorek\\data\\home');
  assert.equal(env.FACTORY_EXTERNAL_CODEX_HOME, 'C:\\Users\\example\\.codex');
  assert.equal(env.TEMP, 'C:\\Igorek\\data\\cache');
  assert.equal(env.PYTHONUTF8, '1');
  const bundled = runtimeEnvironment('C:\\Igorek\\data', { SystemRoot: 'C:\\Windows' }, 'win32', 'C:\\Igorek\\bundle');
  assert.ok(bundled.PATH.startsWith('C:\\Igorek\\bundle\\tools;'));
});

function fixture(mode = 'ready', timeoutMs = 2000) {
  const root = mkdtempSync(join(tmpdir(), 'igorek-runtime-test-'));
  const bundle = join(root, 'bundle'), dataDir = join(root, 'data');
  mkdirSync(bundle);
  execFileSync('openssl', ['req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
    '-keyout', join(root, 'key'), '-out', join(bundle, 'cert.pem'), '-subj', '/CN=localhost', '-days', '1'], { stdio: 'ignore' });
  const fake = `
    const fs=require('node:fs'), path=require('node:path');
    const root=process.argv.at(-1), cert=path.join(root,'web-ui','tls','localhost-cert.pem');
    fs.mkdirSync(path.dirname(cert),{recursive:true});
    fs.copyFileSync(path.join(__dirname,'cert.pem'),cert);
    const mode=${JSON.stringify(mode)};
    if(mode==='error') { console.log(JSON.stringify({type:'error'})); process.exit(1); }
    if(mode!=='timeout') console.log(JSON.stringify({type:'ready',web_port:mode==='invalid'?99999:54321,
      cert,pairing_code:'ABCD-1234-ABCD'}));
    if(mode==='owner') {
      require('node:readline').createInterface({input:process.stdin}).on('line',line=>{
        if(line==='stop') { process.exit(0); return; }
        const request=JSON.parse(line);
        console.log(JSON.stringify({type:'owner_reply',id:request.id,result:{state:'locked'}}));
      });
    }
    else if(mode==='hang') setInterval(()=>{},1000);
    else { process.stdin.on('data',()=>process.exit(0)); process.stdin.on('end',()=>process.exit(0)); }
  `;
  writeFileSync(join(bundle, 'fake.cjs'), fake);
  writeFileSync(join(bundle, 'python'), `#!/bin/sh\nexec '${process.execPath}' '${join(bundle, 'fake.cjs')}' "$@"\n`, { mode: 0o755 });
  writeFileSync(join(bundle, 'backend.py'), 'fixture');
  let python = 'python';
  if (process.platform === 'win32') {
    execFileSync('python', ['-m', 'venv', '--without-pip', join(bundle, 'py')]);
    python = 'py/Scripts/python.exe';
    writeFileSync(join(bundle, 'backend.py'), `
import json, pathlib, shutil, sys, time
root = pathlib.Path(sys.argv[-1]).resolve()
cert = root / 'web-ui' / 'tls' / 'localhost-cert.pem'
cert.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(pathlib.Path(__file__).with_name('cert.pem'), cert)
mode = ${JSON.stringify(mode)}
if mode == 'error':
    print(json.dumps({'type': 'error'}), flush=True)
    sys.exit(1)
if mode != 'timeout':
    print(json.dumps({'type': 'ready', 'web_port': 99999 if mode == 'invalid' else 54321,
                     'cert': str(cert), 'pairing_code': 'ABCD-1234-ABCD'}), flush=True)
if mode == 'hang':
    while True: time.sleep(1)
for line in sys.stdin:
    if mode != 'owner' or line.strip() == 'stop': break
    request = json.loads(line)
    print(json.dumps({'type': 'owner_reply', 'id': request['id'], 'result': {'state': 'locked'}}), flush=True)
`);
  }
  writeFileSync(join(bundle, 'manifest.json'), JSON.stringify({ schema_version: 1, python, entry: 'backend.py' }));
  return { root, bundle, dataDir, runtime: new OwnedRuntime({ bundle, dataDir, timeoutMs, stopTimeoutMs: 100 }), cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

test('runtime receives only isolated environment, without inherited secrets', () => {
  const env = runtimeEnvironment('/tmp/owned', { FACTORY_API_KEY: 'secret', HOME: '/production', LD_PRELOAD: 'evil', LANG: 'ru_RU.UTF-8' }, 'linux');
  assert.equal(env.HOME, '/tmp/owned/home'); assert.equal(env.FACTORY_DATA_DIR, '/tmp/owned');
  assert.equal(env.FACTORY_API_KEY, undefined); assert.equal(env.LD_PRELOAD, undefined);
  assert.equal(env.IGOREK_ISOLATION_MODE, 'off');
});
test('Windows runtime retains required OS fields while separating user data and secrets', () => {
  const env = runtimeEnvironment('C:\\Igorek\\data', {
    SystemRoot: 'C:\\Windows', HOME: 'C:\\Users\\example', PATH: 'C:\\Windows\\System32',
    OPENAI_API_KEY: 'fixture-key', FACTORY_API_KEY: 'fixture-api-key',
  }, 'win32');
  assert.equal(env.SystemRoot, 'C:\\Windows');
  assert.equal(env.COMSPEC, 'C:\\Windows\\System32\\cmd.exe');
  assert.equal(env.HOME, 'C:\\Igorek\\data\\home');
  assert.equal(env.USERPROFILE, env.HOME);
  assert.equal(env.APPDATA, 'C:\\Igorek\\data\\home\\AppData\\Roaming');
  assert.equal(env.FACTORY_API_KEY, undefined);
  assert.equal(env.OPENAI_API_KEY, undefined);
  assert.equal(env.FACTORY_WALLY_CREDENTIALS, 'C:\\Users\\example\\.config\\wally\\credentials.json');
  assert.throws(() => runtimeEnvironment('C:\\Igorek\\data', {}, 'win32'));
});
test('desktop host configuration paths accept explicit absolute overrides', () => {
  const env = runtimeEnvironment('/tmp/owned', { HOME: '/home/example',
    FACTORY_WALLY_CREDENTIALS: '/config/wally.json', AGENTFACTORY_LEGACY_SKILLS_PATH: '/config/skills',
    FACTORY_SESSION_EMBEDDINGS_LLAMASERVER_BIN: '/tools/llama-server' }, 'linux');
  assert.equal(env.FACTORY_WALLY_CREDENTIALS, '/config/wally.json');
  assert.equal(env.AGENTFACTORY_LEGACY_SKILLS_PATH, '/config/skills');
  assert.equal(env.FACTORY_SESSION_EMBEDDINGS_LLAMASERVER_BIN, '/tools/llama-server');
  assert.equal(env.IGOREK_ISOLATION_MODE, 'off');
});
test('valid child starts, stops and remains separate from the parent', async () => {
  const f = fixture();
  try {
    const config = await f.runtime.start();
    assert.equal(config.origin, 'https://127.0.0.1:54321');
    assert.equal(config.pairingCode, 'ABCD-1234-ABCD');
    await assert.rejects(f.runtime.start(), /уже запущен/);
    await f.runtime.stop(); await f.runtime.stop();
    assert.equal(f.runtime.child.exitCode, 0);
  } finally { await f.runtime.stop(); f.cleanup(); }
});
for (const mode of ['error', 'invalid', 'timeout']) {
  test(`startup ${mode} cleans up its child`, async () => {
    const f = fixture(mode, mode === 'timeout' ? 150 : 2000);
    try {
      await assert.rejects(f.runtime.start());
      assert.notEqual(f.runtime.child.exitCode, null);
    } finally { await f.runtime.stop(); f.cleanup(); }
  });
}
test('bundle paths cannot escape through traversal or symlinks', () => {
  const f = fixture();
  try {
    const manifest = { schema_version: 1, python: '../key', entry: 'backend.py' };
    writeFileSync(join(f.bundle, 'manifest.json'), JSON.stringify(manifest));
    assert.throws(() => loadBundle(f.bundle));
    symlinkSync(join(f.root, 'key'), join(f.bundle, 'outside'));
    manifest.python = 'outside';
    writeFileSync(join(f.bundle, 'manifest.json'), JSON.stringify(manifest));
    assert.throws(() => loadBundle(f.bundle));
  } finally { f.cleanup(); }
});

test('failed OS spawn does not hang shutdown', async () => {
  const f = fixture();
  if (process.platform === 'win32') {
    writeFileSync(join(f.bundle, 'manifest.json'), JSON.stringify({ schema_version: 1, python: 'python', entry: 'backend.py' }));
  } else chmodSync(join(f.bundle, 'python'), 0o644);
  try { await assert.rejects(f.runtime.start(), /Python/); }
  finally { await f.runtime.stop(); f.cleanup(); }
});
test('forced shutdown affects only its own child', async () => {
  const owned = fixture('hang'), other = fixture();
  try {
    await owned.runtime.start(); await other.runtime.start();
    await owned.runtime.stop();
    assert.equal(owned.runtime.child.signalCode, 'SIGKILL');
    assert.equal(other.runtime.child.exitCode, null);
    assert.equal(other.runtime.child.signalCode, null);
  } finally { await owned.runtime.stop(); await other.runtime.stop(); owned.cleanup(); other.cleanup(); }
});

test('owner requests correlate replies and reject invalid or stopped calls', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    assert.equal((await f.runtime.requestOwner('status')).result.state, 'locked');
    await assert.rejects(f.runtime.requestOwner('reveal_secret'));
    await assert.rejects(f.runtime.requestOwner('unlock', ''));
    assert.equal((await f.runtime.requestOwner('unlock', 'test-password')).result.state, 'locked');
    await f.runtime.stop();
    await assert.rejects(f.runtime.requestOwner('status'));
    assert.equal(f.runtime.requests.size, 0);
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('mobile pairing and quota actions cross the runtime transport with exact schemas', async () => {
  const f = fixture('owner');
  const actions = { mobile_configure: { port: '47823', public_url: 'https://198.51.100.42:47823' }, mobile_start: {}, mobile_status: {}, mobile_stop: {},
    mobile_revoke: { device_id: 'fixture-device' }, quota_begin: { profile: 'codexpro' },
    quota_status: { profile: 'codexpro' }, quota_cancel: { profile: 'codexpro' },quota_confirm:{profile:'codexpro',link_id:'fixture-confirm'} };
  try {
    await f.runtime.start();
    for (const [action, fields] of Object.entries(actions)) {
      assert.equal((await f.runtime.requestOwner(action, fields)).result.state, 'locked', action);
      await assert.rejects(f.runtime.requestOwner(action, { ...fields, url: 'https://untrusted.invalid' }));
      if (Object.keys(fields).length) {
        await assert.rejects(f.runtime.requestOwner(action, {}));
        await assert.rejects(f.runtime.requestOwner(action, Object.fromEntries(Object.keys(fields).map(key => [key, '']))));
      }
    }
    assert.equal((await f.runtime.requestOwner('mobile_configure', { port: '47823', public_url: '' })).result.state, 'locked');
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('mobile and quota commands wait for polling without changing other busy actions', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    const status = f.runtime.requestOwner('status');
    const mobile = f.runtime.requestOwner('mobile_status');
    const stop = f.runtime.requestOwner('mobile_stop');
    const quota = f.runtime.requestOwner('quota_status', { profile: 'codexpro' });
    const polling = f.runtime.requestOwner('status');
    await assert.rejects(f.runtime.requestOwner('unlock', 'test-password'), /завершения/);
    await assert.rejects(f.runtime.requestOwner('mobile_revoke', {}), /данные/);
    const replies = await Promise.all([status, mobile, stop, quota, polling]);
    assert.ok(replies.every(reply => reply.result.state === 'locked'));
    assert.equal(f.runtime.ownerQueue.length, 0);
    const pending = f.runtime.requestOwner('status').catch(() => {});
    const queued = f.runtime.requestOwner('mobile_status');
    const rejected = assert.rejects(queued, /остановился/);
    await f.runtime.stop(); await rejected; await pending;
  } finally { await f.runtime.stop(); f.cleanup(); }
});


test('profile edits allow only an explicitly empty replacement key', async () => {
  const f = fixture('owner');
  const fields = { credential_id: 'id', name: 'profile', model: 'model', base_url: 'https://fixture.invalid/v1', api_format: 'openai', api_key: '' };
  try {
    await f.runtime.start();
    assert.equal((await f.runtime.requestOwner('update_profile', fields)).result.state, 'locked');
    await assert.rejects(f.runtime.requestOwner('save_profile', fields));
    await assert.rejects(f.runtime.requestOwner('update_profile', { ...fields, api_key: ' ' }));
    await assert.rejects(f.runtime.requestOwner('update_profile', { ...fields, model: '' }));
    const missing = { ...fields }; delete missing.api_key;
    await assert.rejects(f.runtime.requestOwner('update_profile', missing));
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('catalog owner protocol accepts public lookup and rejects extra fields', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    const reply = await f.runtime.requestOwner('model_catalog', { preset: 'opencode-zen', api_key: '', credential_id: '' });
    assert.equal(reply.result.state, 'locked');
    await assert.rejects(f.runtime.requestOwner('model_catalog', { preset: '', api_key: '', credential_id: '' }));
    await assert.rejects(f.runtime.requestOwner('model_catalog', { preset: 'openrouter', api_key: 'key', credential_id: '', url: 'https://other.invalid' }));
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('external history roots point to host catalogs while runtime home and secrets stay isolated', () => {
  const env = runtimeEnvironment('/tmp/owned', { HOME: '/host-user', CODEX_HOME: '/host-codex', CLAUDE_CONFIG_DIR: '/host-claude', XDG_DATA_HOME: '/host-data', OPENAI_API_KEY: 'secret' }, 'linux');
  assert.equal(env.FACTORY_EXTERNAL_CODEX_HOME, '/host-codex');
  assert.equal(env.FACTORY_EXTERNAL_CLAUDE_HOME, '/host-claude');
  assert.equal(env.FACTORY_EXTERNAL_OPENCODE_DB, '/host-data/opencode/opencode.db');
  assert.equal(env.HOME, '/tmp/owned/home');
  assert.equal(env.OPENAI_API_KEY, undefined);
  assert.equal(env.CODEX_HOME, undefined);
  const fallback = runtimeEnvironment('/tmp/owned', { HOME: '/host-user' }, 'linux');
  assert.equal(fallback.FACTORY_EXTERNAL_CODEX_HOME, '/host-user/.codex');
  assert.equal(fallback.FACTORY_EXTERNAL_CLAUDE_HOME, '/host-user/.claude');
  assert.equal(fallback.FACTORY_EXTERNAL_OPENCODE_DB, '/host-user/.local/share/opencode/opencode.db');
  assert.equal(runtimeEnvironment('/tmp/owned', { HOME: 'relative' }, 'linux').FACTORY_EXTERNAL_CODEX_HOME, undefined);
});

test('profile key viewing accepts only the exact owner reauthentication fields', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    await assert.rejects(f.runtime.requestOwner('reveal_profile_key', { credential_id: 'fixture' }));
    await assert.rejects(f.runtime.requestOwner('reveal_profile_key', { credential_id: 'fixture', password: '' }));
    await assert.rejects(f.runtime.requestOwner('reveal_profile_key', { credential_id: 'fixture', password: 'password', role: 'owner' }));
    assert.equal((await f.runtime.requestOwner('reveal_profile_key', { credential_id: 'fixture', password: 'password' })).result.state, 'locked');
    await assert.rejects(f.runtime.requestOwner('reveal_secret', { name: 'fixture', password: 'password' }));
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('standalone values use strict fields and preserve empty variable values', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    for (const action of ['reveal_value', 'delete_value']) {
      await assert.rejects(f.runtime.requestOwner(action, {credential_id:'fixture'}));
      await assert.rejects(f.runtime.requestOwner(action, {credential_id:'fixture',password:'password',role:'owner'}));
      assert.equal((await f.runtime.requestOwner(action, {credential_id:'fixture',password:'password'})).result.state, 'locked');
    }
    const fields = {credential_id:'',name:'EMPTY_VAR',kind:'variable',value:'',password:'password'};
    assert.equal((await f.runtime.requestOwner('save_value', fields)).result.state, 'locked');
    await assert.rejects(f.runtime.requestOwner('save_value', {...fields,password:''}));
    await assert.rejects(f.runtime.requestOwner('save_value', {...fields,value:'x'.repeat(1025)}));
    await assert.rejects(f.runtime.requestOwner('list_values', {password:'password'}));
    assert.equal((await f.runtime.requestOwner('list_values')).result.state, 'locked');
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('consumer grants require the exact owner fields', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    const grant = {credential_id:'value',server:'search',profile_id:'profile',env_name:'SEARCH_TOKEN',password:'fixture-password'};
    await assert.rejects(f.runtime.requestOwner('grant_value_access', {...grant,password:''}));
    await assert.rejects(f.runtime.requestOwner('grant_value_access', {...grant,role:'owner'}));
    await assert.rejects(f.runtime.requestOwner('revoke_value_access', {credential_id:'value',password:'fixture-password'}));
    assert.equal((await f.runtime.requestOwner('value_access', {credential_id:'value'})).result.state, 'locked');
    assert.equal((await f.runtime.requestOwner('grant_value_access', grant)).result.state, 'locked');
    assert.equal((await f.runtime.requestOwner('revoke_value_access', {credential_id:'value',grant_id:'grant',password:'fixture-password'})).result.state, 'locked');
  } finally {await f.runtime.stop();f.cleanup();}
});

test('dotenv import accepts only opaque draft, bounded selection and password', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    const fields = {draft_id:'a'.repeat(64),selection:'{"API_TOKEN":"secret"}',password:'fixture-password'};
    await assert.rejects(f.runtime.requestOwner('commit_env_import', {...fields,password:''}));
    await assert.rejects(f.runtime.requestOwner('commit_env_import', {...fields,values:{API_TOKEN:'private'}}));
    await assert.rejects(f.runtime.requestOwner('commit_env_import', {...fields,selection:'x'.repeat(32769)}));
    assert.equal((await f.runtime.requestOwner('commit_env_import', {...fields,selection:' '.repeat(1024)+'{}'})).result.state, 'locked');
    assert.equal((await f.runtime.requestOwner('prepare_env_import', {source:'/tmp/.env'})).result.state, 'locked');
    assert.equal((await f.runtime.requestOwner('cancel_env_import', {draft_id:fields.draft_id})).result.state, 'locked');
  } finally {await f.runtime.stop();f.cleanup();}
});

test('interface lock and unlock use only their exact owner fields', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    await assert.rejects(f.runtime.requestOwner('lock_interface', { role: 'owner' }));
    await assert.rejects(f.runtime.requestOwner('unlock_interface', { password: '' }));
    await assert.rejects(f.runtime.requestOwner('unlock_interface', { password: 'password', pin: '1234' }));
    assert.equal((await f.runtime.requestOwner('lock_interface')).result.state, 'locked');
    assert.equal((await f.runtime.requestOwner('unlock_interface', { password: 'password' })).result.state, 'locked');
  } finally { await f.runtime.stop(); f.cleanup(); }
});


test('optional security and migration require exact fields', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    for (const [action, fields] of [
      ['configure_interface_lock', {password:'fixture',enabled:'off'}],
      ['set_interface_pin', {password:'fixture',pin:'123456'}],
      ['remove_interface_pin', {password:'fixture'}],
      ['unlock_interface_pin', {pin:'123456'}],
      ['read_legacy_profile', {source:'/tmp/fixture.json'}],
    ]) {
      await assert.rejects(f.runtime.requestOwner(action, {...fields,role:'owner'}));
      assert.equal((await f.runtime.requestOwner(action, fields)).result.state, 'locked');
    }
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('profile deletion and rename accept only exact identity and password fields', async () => {
  const f=fixture('owner');
  try {
    await f.runtime.start();
    for (const [action,fields] of [['delete_profile',{credential_id:'fixture',password:'password'}],['rename_profile',{credential_id:'fixture',password:'password',new_name:'second'}]]) {
      await assert.rejects(f.runtime.requestOwner(action,{...fields,role:'owner'}));
      assert.equal((await f.runtime.requestOwner(action,fields)).result.state,'locked');
    }
  } finally {await f.runtime.stop();f.cleanup();}
});

test('MCP access mutations require exact owner fields', async () => {
  const f=fixture('owner');
  try {
    await f.runtime.start();
    const fields={server:'mail',credential_id:'fixture',access:'deny',password:'password'};
    await assert.rejects(f.runtime.requestOwner('set_mcp_access',{...fields,role:'owner'}));
    assert.equal((await f.runtime.requestOwner('set_mcp_access',fields)).result.state,'locked');
    assert.equal((await f.runtime.requestOwner('mcp_access')).result.state,'locked');
  } finally {await f.runtime.stop();f.cleanup();}
});


test('ChatGPT owner protocol accepts exact account actions and rejects token or endpoint injection', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    for (const [action, fields] of [['chatgpt_begin',{name:'chatgpt',credential_id:''}],['chatgpt_status',{}],
      ['chatgpt_cancel',{}],['chatgpt_models',{credential_id:'oauth'}],['chatgpt_disconnect',{credential_id:'oauth',password:'fixture'}]]) {
      assert.equal((await f.runtime.requestOwner(action, fields)).result.state, 'locked');
      await assert.rejects(f.runtime.requestOwner(action,{...fields,access_token:'forged'}));
      await assert.rejects(f.runtime.requestOwner(action,{...fields,base_url:'https://other.invalid'}));
    }
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('enrolled host service commands cross the actual owner protocol without arbitrary fields', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    for (const operation of ['list','status','start','stop']) {
      const fields = {operation, service: operation === 'list' ? '' : 'wangp'};
      assert.equal((await f.runtime.requestOwner('host_service', fields)).result.state, 'locked');
    }
    for (const fields of [
      {operation:'invoke',service:'wangp'}, {operation:'list',service:'wangp'},
      {operation:'start',service:''}, {operation:'start',service:'../other'},
      {operation:'start',service:'wangp',command:'id'}, {operation:'list',service:'',url:'https://other.invalid'},
    ]) await assert.rejects(f.runtime.requestOwner('host_service', fields));
    const plugins = f.runtime.requestOwner('plugins_status');
    const services = f.runtime.requestOwner('host_service', {operation:'list',service:''});
    assert.deepEqual((await Promise.all([plugins, services])).map(reply=>reply.result.state), ['locked','locked']);
  } finally { await f.runtime.stop(); f.cleanup(); }
});
test('OpenCode service delegation crosses the private owner pipe with bounded configuration',async()=>{
  const f=fixture('owner');
  try{
    await f.runtime.start();
    const fields={operation:'grant',config:JSON.stringify({services:['demo'],projects:['/tmp/project']})};
    assert.equal((await f.runtime.requestOwner('opencode_services',fields)).result.state,'locked');
    await assert.rejects(f.runtime.requestOwner('opencode_services',{...fields,url:'http://localhost'}));
    await assert.rejects(f.runtime.requestOwner('opencode_services',{...fields,config:'x'.repeat(65537)}));
    const queued=await Promise.all([f.runtime.requestOwner('plugins_status'),f.runtime.requestOwner('opencode_services',fields)]);
    assert.equal(queued.length,2);
  }finally{await f.runtime.stop();f.cleanup();}
});

test('OpenCode profile grants use the bounded owner pipe and preserve the existing operation queue',async()=>{
  const f=fixture('owner');
  try{
    await f.runtime.start();
    const fields={operation:'grant',config:'{}'};
    assert.equal((await f.runtime.requestOwner('opencode_profiles',fields)).result.state,'locked');
    await assert.rejects(f.runtime.requestOwner('opencode_profiles',{...fields,api_key:'forged'}));
    await assert.rejects(f.runtime.requestOwner('opencode_profiles',{...fields,config:'x'.repeat(65537)}));
    const queued=await Promise.all([f.runtime.requestOwner('plugins_status'),f.runtime.requestOwner('opencode_profiles',fields)]);
    assert.equal(queued.length,2);
  }finally{await f.runtime.stop();f.cleanup();}
});

test('settings reads serialize behind an active request and keep their order', async () => {
  const f = fixture('owner');
  const actions = ['readiness', 'image_status', 'execution_mode', 'list_profiles', 'list_values',
    'private_mcp_status', 'gmail_status', 'plugins_status'];
  const sent = [];
  try {
    await f.runtime.start();
    const write = f.runtime.child.stdin.write.bind(f.runtime.child.stdin);
    f.runtime.child.stdin.write = (data, ...args) => {
      if (data !== 'stop\n') sent.push(JSON.parse(data).action);
      return write(data, ...args);
    };
    const first = f.runtime.requestOwner('status');
    const waiting = actions.map(action => f.runtime.requestOwner(action));
    const results = await Promise.allSettled([first, ...waiting]);
    assert.ok(results.every(value => value.status === 'fulfilled'));
    assert.deepEqual(sent, ['status', ...actions]);
    assert.equal(f.runtime.requests.size, 0);
    assert.equal(f.runtime.ownerQueue.length, 0);
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('duplicate queued settings reads share one promise without bypassing validation', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    const first = f.runtime.requestOwner('status');
    const read = f.runtime.requestOwner('image_status');
    const repeated = Array.from({ length: 30 }, () => f.runtime.requestOwner('image_status'));
    const results = Promise.allSettled([first, read, ...repeated]);
    assert.ok(repeated.every(value => value === read));
    assert.equal(f.runtime.ownerQueue.length, 1);
    await assert.rejects(f.runtime.requestOwner('image_status', { unexpected: 'field' }), /данные/);
    assert.ok((await results).every(value => value.status === 'fulfilled'));
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('busy mutations and a full settings queue have a structural busy code', async () => {
  const f = fixture('owner');
  try {
    await f.runtime.start();
    const first = f.runtime.requestOwner('status');
    const actions = ['readiness', 'image_status', 'execution_mode', 'list_profiles', 'list_values',
      'private_mcp_status', 'gmail_status', 'plugins_status'];
    const waiting = actions.map(action => f.runtime.requestOwner(action));
    const settled = Promise.allSettled([first, ...waiting]);
    await assert.rejects(f.runtime.requestOwner('unlock', 'fixture-password'), error => error.code === 'operation_busy');
    await assert.rejects(f.runtime.requestOwner('host_mcp_access'), error => error.code === 'operation_busy');
    assert.ok((await settled).every(value => value.status === 'fulfilled'));
  } finally { await f.runtime.stop(); f.cleanup(); }
});

test('a read started by a completed request cannot jump ahead of queued settings reads', async () => {
  const f = fixture('owner'), sent = [];
  try {
    await f.runtime.start();
    const write = f.runtime.child.stdin.write.bind(f.runtime.child.stdin);
    f.runtime.child.stdin.write = (data, ...args) => {
      if (data !== 'stop\n') sent.push(JSON.parse(data).action);
      return write(data, ...args);
    };
    const first = f.runtime.requestOwner('status');
    const waiting = f.runtime.requestOwner('readiness');
    const next = first.then(() => f.runtime.requestOwner('image_status'));
    await Promise.all([first, waiting, next]);
    assert.deepEqual(sent, ['status', 'readiness', 'image_status']);
  } finally { await f.runtime.stop(); f.cleanup(); }
});
