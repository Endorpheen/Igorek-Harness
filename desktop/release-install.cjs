const fs = require('node:fs/promises');
const {realpathSync} = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {randomUUID} = require('node:crypto');

function installerEnvironment(source = process.env) {
  const names = ['PATH','SystemRoot','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP',
    'HOME','USERPROFILE','APPDATA','LOCALAPPDATA','LANG','LC_ALL','DISPLAY','WAYLAND_DISPLAY',
    'XAUTHORITY','XDG_RUNTIME_DIR','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','DBUS_SESSION_BUS_ADDRESS'];
  return Object.fromEntries(names.filter(name => typeof source[name] === 'string').map(name => [name,source[name]]));
}

async function installationTarget({app, executable = process.execPath, appimage = app.isPackaged ? process.env.APPIMAGE : undefined,
  platform = process.platform}) {
  if (platform === 'win32') {
    if (path.basename(executable).toLowerCase() !== 'igorek.exe') throw Error('unknown_update_installation');
    return {kind:'windows',target:realpathSync.native(path.dirname(executable))};
  }
  if (platform !== 'linux') throw Error('unsupported_update_platform');
  if (appimage) {
    if (!path.isAbsolute(appimage)) throw Error('unknown_update_installation');
    const info = await fs.lstat(appimage);
    if (!info.isFile() || info.isSymbolicLink() || info.uid !== process.getuid()) throw Error('unknown_update_installation');
    return {kind:'appimage',target:realpathSync.native(appimage)};
  }
  const directory = realpathSync.native(app.getAppPath());
  const prefix = path.dirname(path.dirname(path.dirname(directory)));
  const marker = JSON.parse(await fs.readFile(path.join(prefix,'installation.json'),'utf8'));
  if (path.basename(directory) !== 'app' || path.basename(path.dirname(path.dirname(directory))) !== 'releases'
      || marker.schema_version !== 1 || marker.kind !== 'igorek-linux-user-installation') throw Error('unknown_update_installation');
  return {kind:'snapshot',target:realpathSync.native(prefix)};
}

async function launchInstallation(file, {app, python, dataDir, prepare, spawnChild = spawn}) {
  const target = await installationTarget({app});
  const directory = realpathSync.native(path.dirname(file.path));
  const cache = realpathSync.native(path.join(app.getPath('userData'),'release-downloads'));
  if (path.dirname(directory) !== cache || !path.basename(directory).startsWith('release-')) throw Error('invalid_update_cache');
  await prepare();
  const attempt = randomUUID();
  const request = path.join(directory,`install-request-${attempt}.json`);
  const value = {schema:1,...target,file:realpathSync.native(file.path),size:file.size,
    sha256:file.sha256,version:file.version,parent_pid:process.pid,restart:true,
    data_dir:realpathSync.native(dataDir)};
  const handle = await fs.open(request,'wx',0o600);
  try {await handle.writeFile(JSON.stringify(value));await handle.sync();} finally {await handle.close();}
  const helper = path.join(directory,`apply-update-${attempt}.py`);
  await fs.copyFile(path.join(__dirname,'release-install.py'),helper);
  if (process.platform === 'win32') {
    const copied = path.join(directory,`helper-python-${attempt}`);
    await fs.cp(path.dirname(python),copied,{recursive:true,errorOnExist:true,force:false});
    python = path.join(copied,path.basename(python));
  }
  const child = spawnChild(python,['-I','-B',helper,request],{
    env:installerEnvironment(),cwd:directory,detached:true,windowsHide:true,stdio:'ignore'});
  let failure;
  child.once('error',()=>{failure=Error('update_helper_failed');});
  const deadline = Date.now()+15000;
  while (Date.now()<deadline) {
    if (failure) throw failure;
    try {
      if (await fs.readFile(request.replace(/\.json$/,'.ready'),'utf8') === 'ready') {
        child.unref();app.quit();return {state:'installing',version:file.version};
      }
    } catch (error) {if(error.code!=='ENOENT')throw error;}
    if (child.exitCode !== null) throw Error('update_helper_failed');
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  child.kill();throw Error('update_helper_timeout');
}
async function showInstallationResult({app,dialog,parent}) {
  const file = path.join(app.getPath('userData'),'release-downloads','last-install-result.json');
  try {
    const info = await fs.lstat(file);
    if (!info.isFile() || info.isSymbolicLink() || info.size > 4096) return;
    const result = JSON.parse(await fs.readFile(file,'utf8'));
    if (!['installed','failed'].includes(result.state)) return;
    await fs.unlink(file);
    await dialog.showMessageBox(parent,{type:result.state==='installed'?'info':'warning',title:'Обновление Игорька',
      message:result.state==='installed'?'Обновление установлено.':'Обновление не установлено.',
      detail:result.state==='installed'?'История и подключения сохранены.':'Не удалось завершить установку. История и подключения хранятся отдельно. Повторите проверку обновлений или используйте скачанный установщик.',
      buttons:['Закрыть']});
  } catch { /* An absent or malformed local receipt cannot initiate installation. */ }
}
module.exports = {launchInstallation,installationTarget,installerEnvironment,showInstallationResult};
