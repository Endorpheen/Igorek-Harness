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
module.exports = { isOwnerFrame, shouldRefreshProfiles, ownedDataLocations };
