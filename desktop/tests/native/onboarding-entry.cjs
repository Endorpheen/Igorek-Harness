const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { X509Certificate } = require('node:crypto');

async function verifyOnboardingEntry({ runtime, config, dataDir, appRoot, root, output, BrowserWindow, ipcMain, clickNative, until }) {
  const { isOwnerFrame, ownerOpenCodeHandler } = require(path.join(appRoot, 'owner-policy.cjs'));
  let settings, main;
  const channels = [];
  const handle = (name, callback) => { ipcMain.handle(name, callback); channels.push(name); };
  const ownerUrl = pathToFileURL(path.join(appRoot, 'owner.html')).href;
  const preload = path.join(root, 'onboarding-entry-preload.cjs');
  fs.writeFileSync(preload, "const {contextBridge,ipcRenderer}=require('electron');contextBridge.exposeInMainWorld('IgorekDesktop',{opencode:operation=>ipcRenderer.invoke('igorek:opencode',operation)});contextBridge.exposeInMainWorld('IgorekWindow',{menu:async()=>{},control:async()=>{}});");
  const status = async () => {
    const reply = await runtime.requestOwner('status');
    assert.ok(!reply.error); return reply.result;
  };
  const unchanged = async () => {
    const reply = await runtime.requestOwner('list_profiles');
    assert.deepEqual(reply.result.profiles, []);
    assert.equal((await status()).agent.running, false);
  };
  const openSettings = async () => {
    settings = new BrowserWindow({ show: true, width: 1280, height: 900,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload: path.join(appRoot, 'owner-preload.cjs') } });
    await settings.loadURL(ownerUrl);
    await until(() => settings.webContents.executeJavaScript("document.getElementById('onboarding-steps').children.length===5&&!document.getElementById('refresh').disabled"), 'Onboarding guide did not finish loading');
  };
  try {
    handle('igorek:owner-vault', (event, action, fields) => {
      assert.ok(isOwnerFrame(event, settings?.webContents, ownerUrl));
      return runtime.requestOwner(action, fields);
    });
    handle('igorek:completion-sound', () => ({ enabled: false, background: false }));
    handle('igorek:release-info', () => ({ version: config.factoryVersion, changes: [] }));
    handle('igorek:browser-control-settings', () => ({ manual: false }));
    handle('igorek:notifications', () => []);
    handle('igorek:owner-return-to-chat', event => {
      assert.ok(isOwnerFrame(event, settings?.webContents, ownerUrl));
      settings.close(); main.focus(); return true;
    });
    handle('igorek:owner-open-opencode', ownerOpenCodeHandler({
      owner: () => settings, main: () => main, ownerUrl, origin: () => config.origin,
      sameOrigin: (a, b) => new URL(a).origin === new URL(b).origin, available: () => true,
      requestStatus: () => runtime.requestOwner('status'),
    }));
    const operations = [];
    handle('igorek:opencode', async (event, operation) => {
      assert.equal(event.sender, main.webContents);
      assert.equal(event.senderFrame, event.sender.mainFrame);
      const value = await status();
      assert.equal(value.state, 'unlocked'); assert.ok(!value.interface_locked);
      assert.ok(['manual', 'status', 'hide', 'layout'].includes(operation));
      operations.push(operation);
      return { state: 'stopped', mode: 'sandbox', installed: false, enabled: false, projects: [], version: '2.0.27' };
    });
    main = new BrowserWindow({ show: true, width: 1360, height: 900,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, preload } });
    const fingerprint = new X509Certificate(fs.readFileSync(path.join(dataDir, 'web-ui/tls/localhost-cert.pem'))).fingerprint256;
    main.webContents.session.setCertificateVerifyProc((request, callback) => {
      let valid = false;
      try { valid = request.hostname === '127.0.0.1' && new X509Certificate(request.certificate.data).fingerprint256 === fingerprint; } catch {}
      callback(valid ? 0 : -3);
    });
    await main.loadURL(config.origin);
    await main.webContents.executeJavaScript(`(async()=>{const response=await fetch('/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:${JSON.stringify(config.pairingCode)}})});if(!response.ok)throw Error('Native onboarding pairing failed');})()`);
    await main.loadURL(config.origin);
    await runtime.requestOwner('lock');
    await openSettings();
    assert.equal(await settings.webContents.executeJavaScript("document.getElementById('onboarding-alternatives').hidden"), true);
    await settings.webContents.executeJavaScript("document.getElementById('password').value='fixture-password'");
    await clickNative(settings, '#submit');
    await until(() => settings.webContents.executeJavaScript("!document.getElementById('onboarding-alternatives').hidden"), 'Independent onboarding actions remained hidden after unlock');
    await unchanged();
    await clickNative(settings, '#onboarding-later');
    await until(() => settings.isDestroyed(), 'Defer model setup did not return to the main window');
    await unchanged();
    await openSettings();
    await until(() => settings.webContents.executeJavaScript("!document.getElementById('onboarding-opencode').disabled"), 'OpenCode entry was not enabled without a model');
    await clickNative(settings, '#onboarding-opencode');
    await until(() => settings.isDestroyed(), 'OpenCode entry did not close settings');
    await until(() => main.webContents.executeJavaScript("Boolean(document.getElementById('workspace-opencode-surface')?.isConnected)"), 'OpenCode tab was not mounted without a model');
    await until(() => operations.includes('manual') && operations.includes('status'), 'OpenCode entry requests did not complete');
    assert.ok(operations.includes('manual') && operations.includes('status'));
    await unchanged();
    await main.webContents.session.cookies.flushStore();
    fs.writeFileSync(path.join(output, 'onboarding-opencode-without-model.png'), (await main.webContents.capturePage()).toPNG());
  } finally {
    settings?.destroy(); main?.destroy();
    for (const name of channels) ipcMain.removeHandler(name);
  }
}

module.exports = { verifyOnboardingEntry };
