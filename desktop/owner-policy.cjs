function isOwnerFrame(event, contents, url) {
  return Boolean(contents && !contents.isDestroyed() && event.sender === contents
    && event.senderFrame === contents.mainFrame && event.senderFrame.url === url);
}
function shouldRefreshProfiles(action, reply) {
  return ['start_agent', 'stop_agent', 'lock', 'unlock', 'update_profile', 'delete_profile', 'rename_profile', 'chatgpt_disconnect'].includes(action)
    && Boolean(reply?.result) && !reply.error;
}
function ownedDataLocations(root, uiRoot) {
  const { join } = require('node:path');
  return { data: root, vault: join(root, 'vault', 'credentials'), history: join(root, 'database', 'factory.db'),
    profiles: join(root, 'profiles'), workspace: join(root, 'workspace'), ui: uiRoot };
}
function ownerOpenCodeHandler({ owner, main, ownerUrl, origin, sameOrigin, available, requestStatus }) {
  return async event => {
    const settings = owner(), target = main();
    const trusted = () => settings && target && owner() === settings && main() === target
      && !settings.isDestroyed() && !target.isDestroyed()
      && isOwnerFrame(event, settings.webContents, ownerUrl)
      && sameOrigin(target.webContents.getURL(), origin());
    if (!available() || !trusted()) throw new Error('Доступ запрещён.');
    const reply = await requestStatus();
    if (!trusted() || !available() || reply.result?.state !== 'unlocked' || reply.result?.interface_locked)
      throw new Error('Разблокируйте хранилище Игорька.');
    await target.webContents.executeJavaScript("window.dispatchEvent(new Event('igorek:open-opencode-tab'));");
    if (!trusted()) throw new Error('Окно настройки изменилось. Повторите переход.');
    settings.close(); target.focus();
    return true;
  };
}
module.exports = { isOwnerFrame, shouldRefreshProfiles, ownedDataLocations, ownerOpenCodeHandler };
