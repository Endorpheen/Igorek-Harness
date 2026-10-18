const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');

async function verifyOpenCodeDelegation({appRoot, bundle, root, output, BrowserWindow, WebContentsView, session}) {
  const {OpenCodeService} = require(path.join(appRoot, 'opencode-service.cjs'));
  const {OpenCodeTasks, request, sessionRoute} = require(path.join(appRoot, 'opencode-tasks.cjs'));
  const {OpenCodeWorkspace} = require(path.join(appRoot, 'opencode-workspace.cjs'));
  const {loadBundle} = require(path.join(appRoot, 'runtime.cjs'));
  const python = loadBundle(bundle).python;
  const folder = path.join(root, 'opencode-delegation');
  const source = path.join(folder, 'source'), workspaceRoot = path.join(folder, 'tasks');
  const runID = 'oc_' + 'c'.repeat(32), calls = [], proof = {platform: process.platform};
  fs.mkdirSync(source, {recursive: true}); fs.mkdirSync(workspaceRoot);
  fs.writeFileSync(path.join(source, 'source.txt'), 'original\n');
  execFileSync('git', ['init', '--initial-branch=main'], {cwd: source, stdio: 'ignore'});
  execFileSync('git', ['add', 'source.txt'], {cwd: source});
  execFileSync('git', ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
    '-c', 'core.hooksPath=' + path.join(folder, 'absent-hooks'), '-c', 'commit.gpgsign=false',
    'commit', '-m', 'Create native fixture'], {cwd: source, stdio: 'ignore'});
  function workspaceAction(action) {
    return execFileSync(python, ['-I', '-c',
      "import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from factory.core.opencode_workspace import prepare,apply_result; result=(prepare if sys.argv[4]=='prepare' else apply_result)(sys.argv[2],Path(sys.argv[3]));print(str(result) if sys.argv[4]=='prepare' else json.dumps(result))",
      path.join(bundle, 'packages'), source, path.join(workspaceRoot, runID), action], {encoding: 'utf8', timeout: 60000}).trim();
  }
  const workspace = workspaceAction('prepare');
  let release, next = 0, controller, view, parent; const services = [];
  const originalRequest = http.request;
  http.request = function(options, callback) {
    return originalRequest.call(this, options, response => {
      if (response.statusCode >= 400 && String(options.path).startsWith('/api/')) {
        let body = '';
        response.on('data', part => { if (body.length < 8000) body += part; });
        response.on('end', () => { proof.http_error = {status: response.statusCode, path: options.path,
          body: body.replace(/[A-Za-z0-9+_\/=-]{32,}/g, '[redacted]').slice(0, 3000)}; });
      }
      callback(response);
    });
  };
  const gate = new Promise(resolve => { release = resolve; });
  const provider = http.createServer(async (req, res) => {
    try {
      let raw = ''; for await (const part of req) raw += part;
      const payload = JSON.parse(raw), tools = payload.tools || [];
      calls.push(payload.messages || []);
      if (tools.length && next === 0) await gate;
      const actions = [
        ['shell', {command: 'echo fixture-shell-ok'}],
        ['write', {path: path.join(source, 'forbidden.txt'), content: 'forbidden'}],
        ['write', {path: path.join(workspace, 'result.txt'), content: 'native result\n'}],
        ['question', {questions: [{question: 'Continue the synthetic fixture?', header: 'Fixture',
          options: [{label: 'Proceed', description: 'Continue the fixture'}]}]}],
      ];
      let delta = {role: 'assistant', content: 'Protocol fixture completed.'}, finish = 'stop';
      if (tools.length && next < actions.length) {
        const [name, args] = actions[next++];
        assert(tools.some(tool => tool.function?.name === name), 'Native tool missing: ' + name);
        delta = {role: 'assistant', tool_calls: [{index: 0, id: 'call_fixture_' + next, type: 'function',
          function: {name, arguments: JSON.stringify(args)}}]}; finish = 'tool_calls';
      }
      const base = {id: 'chatcmpl_fixture', object: 'chat.completion.chunk', created: 1, model: 'fixture-model'};
      res.writeHead(200, {'Content-Type': 'text/event-stream'});
      res.end('data: ' + JSON.stringify({...base, choices: [{index: 0, delta, finish_reason: null}]}) + '\n\ndata: ' +
        JSON.stringify({...base, choices: [{index: 0, delta: {}, finish_reason: finish}],
          usage: {prompt_tokens: 10, completion_tokens: 10, total_tokens: 20}}) + '\n\ndata: [DONE]\n\n');
    } catch (error) { res.writeHead(500); res.end(); proof.provider_error = error.message; }
  });
  try {
    const socket = path.join(folder, 'profiles.sock');
    await new Promise(resolve => process.platform === 'win32' ? provider.listen(0, '127.0.0.1', resolve) : provider.listen(socket, resolve));
    const manual = new OpenCodeService(path.join(folder, 'manual'), {python, bundle});
    await manual.install(); manual.config.projects = [source];
    controller = new OpenCodeTasks({manual, root: path.join(folder, 'services'), workspaceRoot, allowed: () => true,
      createService: (serviceRoot, ownedWorkspace, consumer) => {assert.equal(consumer, runID); const service = new OpenCodeService(serviceRoot, {python, bundle, diagnostics: true,
        ownedWorkspace, binaryRoot: manual.binaryRoot, profilesControl: async operation => operation === 'grant' ? {
          socket, ...(process.platform === 'win32' ? {port: provider.address().port} : {}), token: 'd'.repeat(64),
          profiles: [{id: 'fixture-id', name: 'fixture', model: 'fixture-model', api_format: 'openai', context: 32768}],
        } : {revoked: true}});services.push(service);service.on('state',value=>{if(value.error)proof.service_error=value.error;});return service;},
      show: async job => {
        proof.shown_session = job.sessionID;
        if (!BrowserWindow) return;
        parent = new BrowserWindow({show: true, width: 1400, height: 950,
          webPreferences: {sandbox: true, contextIsolation: true, nodeIntegration: false}});
        await parent.loadURL('data:text/html,<html><body><p id="main-chat">Synthetic main chat remains available</p><div id="workspace-opencode-surface" style="position:absolute;left:40%;top:40px;width:60%;height:850px"></div></body></html>');
        view = new OpenCodeWorkspace({root: path.join(folder, 'view'), service: job.service, BrowserWindow, WebContentsView,
          session, dialog: {showMessageBox: async () => ({response: 1})}, shell: {}});
        view.path = sessionRoute(job); view.unlock(); await view.show(parent);
      }});
    const started = await controller.handle({action: 'start', run_id: runID, source, workspace, title: 'Native fixture',
      goal: 'Execute the synthetic filesystem fixture.', profile: 'fixture', session: 'chat-fixture'});
    assert(started.session_id, JSON.stringify({started,diagnostics:proof})); proof.nonblocking_start = true;
    if(parent)assert.equal(await parent.webContents.executeJavaScript("document.getElementById('main-chat').textContent"), 'Synthetic main chat remains available');
    const job = controller.jobs.get(runID);
    const made = await request(job.service, 'POST', job.prefix + '/form', {title: 'Synthetic owner question',
      fields: [{key: 'choice', type: 'boolean', title: 'Continue?', required: true}]});
    const waiting = await controller.handle({action: 'status', run_id: runID});
    assert(waiting.inputs.some(input => input.id === made.data.id));
    assert.equal((await controller.handle({action: 'answer', run_id: runID, request_id: made.data.id,
      response: {answer: {choice: true}}})).resolved, true);
    assert.equal((await request(job.service, 'GET', job.prefix + '/form/' + made.data.id)).data.state.status, 'answered');
    proof.exact_form_reply = true; release();
    for (let attempt = 0; attempt < 200; attempt++) {
      const status = await controller.handle({action: 'status', run_id: runID});
      for (const input of status.inputs || []) {
        const answer = input.kind === 'permission' ? {decision: 'once'} : Object.fromEntries(input.fields.map(field =>
          [field.key, field.type === 'multiselect' ? [field.options[0].value] : field.options?.[0]?.value || 'Proceed']));
        assert.equal((await controller.handle({action: 'answer', run_id: runID, request_id: input.id, response: {answer}})).resolved, true);
        if (input.kind === 'form') proof.native_question_roundtrip = true;
      }
      if (status.outcome) { proof.outcome = status.outcome; break; }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    assert.equal(proof.outcome, 'succeeded'); assert.equal(next, 4); assert(proof.native_question_roundtrip);
    assert.equal(fs.readFileSync(path.join(workspace, 'result.txt'), 'utf8'), 'native result\n');
    assert(!fs.existsSync(path.join(source, 'forbidden.txt'))); assert(!fs.existsSync(path.join(source, 'result.txt')));
    assert(calls.some(messages => JSON.stringify(messages).includes('fixture-shell-ok')));
    assert(calls.some(messages => /EACCES|EPERM|Permission denied|Access is denied|os error (5|13)/i.test(JSON.stringify(messages))), 'Outside write did not report an OS denial');
    proof.source_isolation = true;
    assert(JSON.parse(workspaceAction('apply')).applied);
    assert.equal(fs.readFileSync(path.join(source, 'result.txt'), 'utf8'), 'native result\n'); proof.result_applied = true;
    if(view){for (let attempt = 0; attempt < 100; attempt++) {
      if ((await view.view.webContents.executeJavaScript('document.body.innerText')).includes('Поручение: Native fixture')) { proof.native_right_panel = true; break; }
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    assert(proof.native_right_panel, 'Native right panel did not display the assigned session');
    await new Promise(resolve => setTimeout(resolve, 200));
    fs.writeFileSync(path.join(output, 'opencode-delegation.png'), (await parent.webContents.capturePage()).toPNG());}
    proof.passed = true; return proof;
  } catch (error) { proof.error = error.message; throw error; }
  finally {
    http.request = originalRequest;
    release(); view?.lock(); parent?.destroy(); await controller?.stop();
    await new Promise(resolve => provider.close(resolve));
    if (!proof.passed) {
      proof.process_logs = services.flatMap(service => {
        const files = fs.readdirSync(service.root, {recursive: true, withFileTypes: true}).filter(entry =>
          entry.isFile() && /\.(log|jsonl)$/.test(entry.name)).slice(0, 12);
        return files.map(entry => {
          try { return {file: entry.name, text: fs.readFileSync(path.join(entry.parentPath, entry.name), 'utf8').slice(-16000)
            .replaceAll(service.password, '[redacted]').replace(/[A-Za-z0-9+_\/=-]{32,}/g, '[redacted]')}; }
          catch { return {file: entry.name, text: ''}; }
        });
      });
      console.error(JSON.stringify(proof));
    }
    fs.writeFileSync(path.join(output, 'opencode-delegation.json'), JSON.stringify(proof, null, 2));
  }
}
module.exports = {verifyOpenCodeDelegation};
