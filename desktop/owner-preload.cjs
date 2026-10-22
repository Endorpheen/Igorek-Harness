const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('IgorekCompletionSoundSettings', Object.freeze({
  read: () => ipcRenderer.invoke('igorek:completion-sound', 'read'),
  set: enabled => ipcRenderer.invoke('igorek:completion-sound', 'set', enabled),
  onChange: callback => ipcRenderer.on('igorek:completion-sound-state', (_, value) => callback(value)),
}));
contextBridge.exposeInMainWorld('IgorekResourceMonitorSettings', Object.freeze({
  read: () => ipcRenderer.invoke('igorek:resource-monitor', 'read'),
  set: enabled => ipcRenderer.invoke('igorek:resource-monitor', 'set', enabled),
  onChange: callback => ipcRenderer.on('igorek:resource-monitor-state', (_, value) => callback(value)),
}));
const notificationSource = (channel, action) => ({search_status:'Интернет-поиск',save_search:'Интернет-поиск',mobile_configure:'Адрес подключения телефона',mobile_start:'Сопряжение телефона',mobile_status:'Мобильные устройства',mobile_stop:'Мобильные устройства',mobile_revoke:'Отключение телефона',quota_begin:'Квота Codex',quota_status:'Квота Codex',quota_cancel:'Квота Codex'}[action] || ({'igorek:owner-vault':'Настройки','igorek:session-quota':'Квота Codex','igorek:session-agent':'Исполнитель','igorek:session-plugins':'Плагины','igorek:session-mcp':'Подключения MCP','igorek:session-browser':'Браузер'}[channel]) || 'Приложение');
const invoke = async (channel, ...args) => {
  try {
    const reply = await ipcRenderer.invoke(channel, ...args);
    if (reply?.error && !(channel === 'igorek:owner-vault' && reply.error === 'operation_busy'))
      ipcRenderer.invoke('igorek:notifications', 'add', { source: notificationSource(channel, args[0]), level: 'error', detail: ({search_auth_failed:'Поисковая служба отклонила ключ. Проверьте ключ в настройках интернет-поиска.',search_limit_reached:'Исчерпан лимит поиска или превышена частота запросов. Проверьте кабинет поисковой службы.',search_unavailable:'Поисковая служба недоступна. Повторите проверку позже.',search_invalid_response:'Поисковая служба вернула некорректный ответ. Настройки не изменены.'}[reply.error] || String(reply.error)) }).catch(() => {});
    return reply;
  } catch (error) {
    ipcRenderer.invoke('igorek:notifications', 'add', { source: notificationSource(channel, args[0]), level: 'error', detail: String(error.message || error) }).catch(() => {});
    throw error;
  }
};
contextBridge.exposeInMainWorld('IgorekNotifications', Object.freeze({
  list: () => ipcRenderer.invoke('igorek:notifications', 'list'),
  add: value => ipcRenderer.invoke('igorek:notifications', 'add', value),
  read: () => ipcRenderer.invoke('igorek:notifications', 'read'),
  clear: () => ipcRenderer.invoke('igorek:notifications', 'clear'),
  recover: value => ipcRenderer.invoke('igorek:notifications', 'recover', value),
  confirm: value => ipcRenderer.invoke('igorek:notifications', 'confirm', value),
  snapshot: () => ipcRenderer.invoke('igorek:notifications', 'snapshot'),
  support: value => ipcRenderer.invoke('igorek:diagnostic-support', value),
  onChange: callback => { ipcRenderer.on('igorek:notifications-changed', (_, items) => callback(items)); },
}));
ipcRenderer.on('igorek:owner-panel', (_, panel) => {
  if ((typeof panel === 'string' && /^[a-z]+$/.test(panel)) || (panel?.panel === 'mcp' && ['openai_docs','google_calendar'].includes(panel.plugin))) window.dispatchEvent(new CustomEvent('igorek-owner-panel', {detail:panel}));
});
contextBridge.exposeInMainWorld('igorekVault', {
  browserControl: value => ipcRenderer.invoke('igorek:browser-control-settings',value),
  releaseInfo: () => ipcRenderer.invoke('igorek:release-info'),
  chatgptBegin: (name, credential_id = '') => invoke('igorek:owner-vault', 'chatgpt_begin', { name, credential_id }),
  chatgptStatus: () => invoke('igorek:owner-vault', 'chatgpt_status'),
  chatgptCancel: () => invoke('igorek:owner-vault', 'chatgpt_cancel'),
  chatgptModels: credential_id => invoke('igorek:owner-vault', 'chatgpt_models', { credential_id }),
  chatgptDisconnect: (credential_id, password) => invoke('igorek:owner-vault', 'chatgpt_disconnect', { credential_id, password }),
  pluginsStatus: () => invoke('igorek:owner-vault', 'plugins_status'),
  pluginAction: (plugin, operation) => invoke('igorek:owner-vault', 'plugin_action', {plugin,operation}),
  calendarConnect: profile => invoke('igorek:owner-vault', 'calendar_connect', {profile}),
  calendarConfigure: password => invoke('igorek:owner-vault', 'calendar_configure_file', {password}),
  calendarBegin: password => invoke('igorek:owner-vault', 'calendar_begin', {password}),
  calendarDisconnect: () => invoke('igorek:owner-vault', 'calendar_disable'),
  calendarList: () => invoke('igorek:owner-vault', 'calendar_list'),
  calendarSelect: (calendar_id,password) => invoke('igorek:owner-vault', 'calendar_select', {calendar_id,password}),
  chooseEnvImport: () => invoke('igorek:owner-vault', 'choose_env_import'),
  commitEnvImport: fields => invoke('igorek:owner-vault', 'commit_env_import', fields),
  cancelEnvImport: draft_id => invoke('igorek:owner-vault', 'cancel_env_import', {draft_id}),
  valueAccess: credential_id => invoke('igorek:owner-vault', 'value_access', {credential_id}),
  grantValueAccess: fields => invoke('igorek:owner-vault', 'grant_value_access', fields),
  revokeValueAccess: fields => invoke('igorek:owner-vault', 'revoke_value_access', fields),
  searchStatus: () => invoke('igorek:owner-vault', 'search_status'),
  saveSearch: fields => invoke('igorek:owner-vault', 'save_search', fields),
  listValues: () => invoke('igorek:owner-vault', 'list_values'),
  saveValue: fields => invoke('igorek:owner-vault', 'save_value', fields),
  revealValue: (credential_id, password) => invoke('igorek:owner-vault', 'reveal_value', { credential_id, password }),
  deleteValue: (credential_id, password) => invoke('igorek:owner-vault', 'delete_value', { credential_id, password }),
  chooseLegacyProfile: () => invoke('igorek:owner-vault', 'choose_legacy_profile'),
  deleteProfile: (credential_id, password) => invoke('igorek:owner-vault', 'delete_profile', { credential_id, password }),
  renameProfile: (credential_id, password, new_name) => invoke('igorek:owner-vault', 'rename_profile', { credential_id, password, new_name }),
  gmailImportHost: password => invoke('igorek:owner-vault', 'gmail_import_host', {password}),
  gmailStatus: () => invoke('igorek:owner-vault', 'gmail_status'),
  gmailConfigure: password => invoke('igorek:owner-vault', 'gmail_configure_file', {password}),
  gmailBegin: password => invoke('igorek:owner-vault', 'gmail_begin', {password}),
  gmailDisconnect: password => invoke('igorek:owner-vault', 'gmail_disconnect', {password}),
  privateMcpStart: fields => invoke('igorek:owner-vault', 'private_mcp_start', fields),
  privateMcpStatus: () => invoke('igorek:owner-vault', 'private_mcp_status'),
  privateMcpClear: () => invoke('igorek:owner-vault', 'private_mcp_clear'),
  hostMcpAccess: () => invoke('igorek:owner-vault', 'host_mcp_access'),
  setHostMcpAccess: fields => invoke('igorek:owner-vault', 'set_host_mcp_access', fields),
  mcpAccess: () => invoke('igorek:owner-vault', 'mcp_access'),
  setMcpAccess: fields => invoke('igorek:owner-vault', 'set_mcp_access', fields),
  chooseWorkspace: () => invoke('igorek:owner-workspace', 'choose'),
  openWorkspaceSession: () => invoke('igorek:owner-workspace', 'open-session'),
  returnToChat: () => invoke('igorek:owner-return-to-chat'),
  openOpenCode: () => invoke('igorek:owner-open-opencode'),
  mobile: (action,fields={}) => invoke('igorek:owner-vault',action,fields),
  dataLocations: () => invoke('igorek:owner-locations'),
  changePassword: (current, new_password) => invoke('igorek:owner-vault', 'change_password', { current, new_password }),
  recoverImage: () => invoke('igorek:owner-vault', 'recover_image_file'),
  prepareImage: () => invoke('igorek:owner-vault', 'prepare_image_file'),
  executionMode: () => invoke('igorek:owner-vault', 'execution_mode'),
  selectExecutionMode: mode => invoke('igorek:owner-vault', 'select_execution_mode', { mode }),
  imageStatus: () => invoke('igorek:owner-vault', 'image_status'),
  readiness: () => invoke('igorek:owner-vault', 'readiness'),
  status: () => invoke('igorek:owner-vault', 'status'),
  create: password => invoke('igorek:owner-vault', 'create', password),
  unlock: password => invoke('igorek:owner-vault', 'unlock', password),
  configureInterfaceLock: (password, enabled) => invoke('igorek:owner-vault', 'configure_interface_lock', { password, enabled }),
  setInterfacePin: (password, pin) => invoke('igorek:owner-vault', 'set_interface_pin', { password, pin }),
  removeInterfacePin: password => invoke('igorek:owner-vault', 'remove_interface_pin', { password }),
  unlockInterfacePin: pin => invoke('igorek:owner-vault', 'unlock_interface_pin', { pin }),
  lockInterface: () => invoke('igorek:owner-vault', 'lock_interface'),
  unlockInterface: password => invoke('igorek:owner-vault', 'unlock_interface', { password }),
  lock: () => invoke('igorek:owner-vault', 'lock'),
  generatePhrase: () => invoke('igorek:owner-vault', 'generate_phrase'),
  saveRecovery: (password, recovery_phrase) => invoke('igorek:owner-vault', 'save_recovery', { password, recovery_phrase }),
  verifyRecovery: recovery_phrase => invoke('igorek:owner-vault', 'verify_recovery_file', { recovery_phrase }),
  restoreRecovery: (recovery_phrase, new_password) => invoke('igorek:owner-vault', 'restore_recovery_file', { recovery_phrase, new_password }),
  modelCatalog: fields => invoke('igorek:owner-vault', 'model_catalog', fields),
  listProfiles: () => invoke('igorek:owner-vault', 'list_profiles'),
  revealProfileKey: (credential_id, password) => invoke('igorek:owner-vault', 'reveal_profile_key', { credential_id, password }),
  updateProfile: fields => invoke('igorek:owner-vault', 'update_profile', fields),
  saveProfile: fields => invoke('igorek:owner-vault', 'save_profile', fields),
  startAgent: credential_id => invoke('igorek:owner-vault', 'start_agent', { credential_id }),
  stopAgent: () => invoke('igorek:owner-vault', 'stop_agent'),
  checkProfile: credential_id => invoke('igorek:owner-vault', 'check_profile', { credential_id }),
});

contextBridge.exposeInMainWorld('IgorekWindow', Object.freeze({
  menu: (label, x, y) => invoke('igorek:window-menu', label, x, y),
  control: action => invoke('igorek:window-control', action),
}));
