const el = id => document.getElementById(id);
let state;
let interfaceLocked = false;
let interfaceLockEnabled = false;
let imageRunning = false;
let imagePolling = false;
let recoveryStep = 'prepare';
let profilesState = 'loading';
let savedProfiles = [];
let selectedProfileId;
let editingProfile = null;
let agent = { running: false, profile: "" };
let successfulProfiles = [];
let onboardingHistoryState = 'ready';
let onboardingRefreshPending = false;
let onboardingRevision = 0;
let chatgptTimer;
let chatgptGeneration = 0;
const chatgptErrors = {
  chatgpt_auth_failed: 'OpenAI отклонил вход. Повторите авторизацию.',
  chatgpt_login_required: 'Войдите в аккаунт OpenAI снова.',
  chatgpt_plan_permission_missing: 'Разрешите использование подписки ChatGPT при входе.',
  chatgpt_identity_invalid: 'Не удалось проверить аккаунт OpenAI. Повторите вход.',
  chatgpt_registration_incomplete: 'OpenAI не завершил регистрацию Игорька. Повторите вход.',
  chatgpt_signin_expired: 'Время входа истекло или окно настройки заблокировано. Повторите вход.',
  chatgpt_signin_busy: 'Вход уже открыт в браузере. Завершите или отмените его.',
  chatgpt_access_denied: 'Вход отменён в OpenAI. Можно попробовать снова.',
  chatgpt_network: 'Не удалось связаться с OpenAI. Проверьте сеть и повторите.',
  chatgpt_invalid_response: 'Неожиданный ответ OpenAI. Повторите попытку.',
  chatgpt_no_models: 'OpenAI не вернул доступных моделей для этого аккаунта.',
  chatgpt_profile_changed: 'Подключение изменилось во время входа. Повторите авторизацию.',
};
function chatgptError(code) { return chatgptErrors[code] || errors[code] || 'Не удалось подключить ChatGPT.'; }
function chatgptConnectionName() {
  const input=el('chatgpt-name'), raw=input.value;
  const valid=/^[A-Za-z][A-Za-z0-9-]{0,39}$/.test(raw);
  input.setCustomValidity?.(valid?'':'Введите имя латинскими буквами: первый символ — буква, дальше можно цифры и дефис. До 40 символов.');
  const name=valid?raw.toLowerCase():null;
  el('chatgpt-name-note').textContent=valid&&raw!==name?'Подключение сохранится как «'+name+'». Заглавные буквы преобразуются в строчные.':'Латинские буквы, цифры и дефис. Заглавные буквы разрешены; первый символ — буква.';
  return name;
}
async function beginChatgpt(profile) {
  showProviderEditor('chatgpt');
  el('chatgpt-form').dataset.credentialId=profile?.credential_id || '';
  el('chatgpt-name').readOnly=Boolean(profile);
  if (profile) el('chatgpt-name').value=profile.config.name;
  const normalized=chatgptConnectionName();
  const name=profile?.config.name || normalized;
  if (!name) {el('chatgpt-name').reportValidity();el('chatgpt-status').textContent='Проверьте имя подключения: используйте латинские буквы, цифры и дефис.';return;}
  const generation = ++chatgptGeneration;
  clearTimeout(chatgptTimer);
  el('chatgpt-login').disabled = true;
  el('chatgpt-status').textContent = 'Открываю вход OpenAI в браузере…';
  try {
    const reply = await window.igorekVault.chatgptBegin(name, profile?.credential_id || '');
    if (reply.error) throw new Error(reply.error);
    el('chatgpt-cancel').hidden = false;
    el('chatgpt-status').textContent = 'Войдите в браузере и разрешите использование подписки. Ожидаю завершения входа…';
    const deadline = Date.now() + 610000;
    async function poll() {
      if (generation !== chatgptGeneration || state !== 'unlocked' || interfaceLocked) return;
      let terminal = false;
      try {
        const status = await window.igorekVault.chatgptStatus();
        if (generation !== chatgptGeneration) return;
        if (status.error) { terminal = true; throw new Error(status.error); }
        const result = status.result.chatgpt;
        if (result.state === 'awaiting') { chatgptTimer = setTimeout(poll, 1500); return; }
        terminal = true;
        el('chatgpt-cancel').hidden = true;
        el('chatgpt-login').disabled = false;
        if (result.state !== 'connected') throw new Error(result.error);
        selectedProfileId = result.credential_id;
        renderProfiles(status.result.profiles);
        const connected = status.result.profiles.find(p => p.credential_id === result.credential_id);
        if (connected) { editProfile(connected); await loadModels(); }
        el('chatgpt-status').textContent = 'Аккаунт подключён. Выберите модель ниже, сохраните изменения и проверьте подключение.';
      } catch (error) {
        if (!terminal && generation === chatgptGeneration && Date.now() < deadline) {
          el('chatgpt-status').textContent = 'Ожидаю вход. Обновление состояния временно недоступно; повторяю…';
          chatgptTimer = setTimeout(poll, 1500); return;
        }
        el('chatgpt-status').textContent = chatgptError(error.message);
        el('chatgpt-login').disabled = false;
        el('chatgpt-cancel').hidden = true;
      }
    }
    chatgptTimer = setTimeout(poll, 1000);
  } catch (error) { el('chatgpt-status').textContent = chatgptError(error.message); el('chatgpt-login').disabled = false; }
}
const VOICE_PROFILE_NAME = 'openai-realtime';
const VOICE_PROFILE_MODEL = 'gpt-realtime-2.1-mini';
const VOICE_PROFILE_URL = 'https://api.openai.com/v1';
function onboardingProfiles() {
  return savedProfiles.filter(profile => profile.config.name !== VOICE_PROFILE_NAME);
}
const PANELS = [
  { id: 'monitor', label: 'Монитор ресурсов', group: 'Интерфейс', sections: ['resource-monitor-settings'], description: 'Компактные показатели компьютера рядом с вашей работой.', guide: ['Ресурсы под рукой', 'Плавающее окно показывает загрузку компьютера и доступную память. Настройка сохраняется между запусками.', 'Включите монитор|Перетащите окно в удобное место|Выключите его здесь или кнопкой ×'] },
  {id:'browser',label:'Браузер',group:'Интерфейс',sections:['browser-control-settings'],description:'Управление вкладками встроенного браузера.',guide:['Браузер для ваших задач','По умолчанию агент пользуется браузером без ручной передачи прав.','При необходимости включите ручной режим|Передавайте управление кнопкой в браузере|Входите в аккаунты самостоятельно']},
  { id: 'access', label: 'Доступ к настройкам', group: 'Защита', sections: ['interface-lock-panel'], description: 'Работа продолжается, настройки под замком.', guide: ['Можно оставить задачу работать', 'Блокировка окна закрывает административные действия, но не останавливает чат и не отзывает уже выданные ключи.', 'Введите пароль для возврата к настройкам|Либо продолжайте разговор в чате|Для полной блокировки хранилища завершите задачу'] },
  { id: 'start', label: 'Обзор и запуск', group: 'Начало работы', sections: ['onboarding'], description: 'Подключение, рабочая папка и первый результат.', guide: ['Всё по порядку', 'Здесь можно подготовить Игорька к работе и продолжить настройку с сохранённого шага.', 'Откройте хранилище|Проверьте подключение|Выберите папку и выполните первую задачу'] },
  { id: 'vault', label: 'Хранилище', group: 'Начало работы', sections: ['vault-panel'], description: 'Откройте доступ к своим подключениям.', guide: ['Ключи под защитой', 'Пароль открывает зашифрованные ключи только в этом приложении.', 'Создайте или введите пароль|Сохраните его в менеджере паролей|При наличии копии используйте восстановление'] },
  { id: 'secrets', label: 'Ключи и переменные', group: 'Подключения', sections: ['secrets-hub'], description: 'Единое место для секретов и параметров подключений.', guide: ['Каждому сервису — свой ключ', 'Основная модель выполняет задачи, голосовая отвечает за разговор. Их подключения настраиваются отдельно.', 'Выберите назначение ключа|Сохраните и проверьте подключение|Обновите копию восстановления'] },
  { id: 'search', label: 'Интернет-поиск', group: 'Подключения', sections: ['search-settings'], description: 'Выбор поисковой службы и сохранение ключа.', guide: ['Подключим интернет-поиск', 'Выберите службу, получите ключ в её кабинете и сохраните в хранилище.', 'Выберите поисковик|Вставьте ключ и проверьте|Начните новую задачу'] },
  { id: 'provider', label: 'Основная модель', group: 'Подключения', sections: ['provider-setup'], description: 'Подписка или API-сервис для задач в чате.', guide: ['Подключим рабочую модель', 'Профиль объединяет адрес сервиса, модель и её ключ. Сохранённый ключ не возвращается в поле ввода.', 'Войдите в ChatGPT или подключите сервис|Выберите модель|Проверьте подключение перед запуском'] },
  { id: 'mcp', label: 'Доступ MCP', group: 'Подключения', sections: ['mcp-settings'], description: 'Разрешения подключений для каждого профиля.', guide: ['Доступ под вашим контролем', 'Новые подключения закрыты до вашего решения.', 'Выберите MCP и профиль|Определите режим доступа|Подтвердите изменение паролем'] },
  { id: 'plugins', label: 'Плагины', group: 'Подключения', sections: ['plugin-settings'], description: 'Google Календарь и разрешения плагинов.', guide: ['Ваш календарь под контролем', 'Чтение разрешается профилю модели. Создание и перенос требуют вашего подтверждения в чате.', 'Нажмите «Подключить Google Календарь»|Подтвердите доступ в Google|Пользуйтесь календарём в чате'] },
  { id: 'voice', label: 'Голосовая модель', group: 'Подключения', sections: ['voice-setup'], description: 'Отдельное подключение для голосовых разговоров.', guide: ['Можно поговорить', 'Голосовое подключение не заменяет выбранную модель задач.', 'Введите ключ OpenAI|Дождитесь результата проверки|Откройте голосовой разговор в чате'] },
  { id:'mobile',label:'Мобильные устройства',group:'Подключения',sections:['mobile-settings'],description:'Телефон использует активный профиль десктопа.',guide:['Игорёк на телефоне','Аккаунт ChatGPT и ключи остаются на компьютере.','Запустите codexpro|Включите HTTPS-доступ|Введите данные сопряжения в Igorek Remote'] },
  { id: 'execution', label: 'Среда выполнения', group: 'Рабочая среда', sections: ['image-setup'], description: 'Выберите, где Игорёк будет выполнять задачи.', guide: ['Определим рабочую среду', 'Режим применяется при следующем запуске исполнителя. Для KVM нужен отдельный проверенный образ.', 'Остановите исполнитель|Выберите режим и подготовьте образ при необходимости|Сохраните режим и запустите профиль'] },
  { id: 'tts', label: 'Озвучивание сообщений', group: 'Интерфейс', sections: ['tts-settings'], description: 'Чтение ответов выбранным TTS-сервером.', guide: ['Голос для текстовых ответов', 'Укажите адрес, модель и голос. Готовые ответы можно прослушать отдельной кнопкой.', 'Подключите TTS-сервер|Проверьте голос|Сохраните настройки'] },
  { id: 'notifications', label: 'Уведомления', group: 'Интерфейс', sections: ['completion-sound-settings'], description: 'Параметры оповещения о событиях и результатах работы приложения.', guide: ['Уведомления под вашим контролем', 'Здесь вы можете настроить уведомления приложения. Выберите удобные параметры оповещения и меняйте их по мере необходимости.', 'Ознакомьтесь с доступными параметрами|Настройте уведомления под себя|Вернитесь к своим задачам'] },
  { id: 'check', label: 'Проверка компьютера', group: 'Рабочая среда', sections: ['computer-check'], description: 'Доступность системных компонентов без изменения настроек.', guide: ['Проверим готовность', 'Отчёт покажет состояние компонентов. Он не запускает модель или виртуальную машину.', 'Запустите проверку|Посмотрите пункты, требующие действия|Вернитесь к настройке среды'] },
  { id: 'workspace', label: 'Рабочая папка', group: 'Рабочая среда', sections: ['workspace-setup'], description: 'Место для файлов первой задачи.', guide: ['Дадим задаче своё место', 'Выберите отдельную папку для знакомства. Внешние папки не удаляются вместе с приложением.', 'Запустите проверенный профиль|Выберите папку|Создайте сессию и отправьте задание'] },
  { id: 'security', label: 'Пароль и блокировка', group: 'Защита и данные', sections: ['password-settings'], description: 'Блокировка окна настройки и смена пароля хранилища.', guide: ['Доступ остаётся у вас', 'Окно настройки можно заблокировать во время задачи. Смена пароля требует остановленного исполнителя.', 'Для временного ухода заблокируйте окно|Для смены пароля остановите исполнитель|Сохраните новый пароль отдельно'] },
  { id: 'backup', label: 'Копии и восстановление', group: 'Защита и данные', sections: ['restoration', 'recovery', 'verification'], description: 'Сохранение и проверка отдельной копии ключей.', guide: ['План на случай потери пароля', 'Файл копии и фраза вместе открывают доступ к ключам. Храните их раздельно.', 'Сохраните фразу отдельно|Создайте файл копии|Проверьте восстановление'] },
  { id: 'data', label: 'Места хранения', group: 'Защита и данные', sections: ['data-dashboard'], description: 'Каталоги настроек, ключей, истории и рабочих файлов.', guide: ['Знаем, где что лежит', 'Копия ключей не содержит историю чатов и рабочие файлы. Для них нужны отдельные копии.', 'Посмотрите каталоги приложения|Отдельно сохраните нужные файлы|Не переносите открытое хранилище вручную'] },
];
let activePanel = 'vault';
let navInitialized = false;
function panelVisible(panel) {
  if (interfaceLocked) return panel.id === 'access';
  if (panel.id === 'access') return false;
  return panel.sections.some(id => { const node = el(id); return node && !node.hidden; });
}
function applyActivePanel() {
  for (const panel of PANELS) {
    for (const id of panel.sections) el(id)?.classList.toggle('active', panel.id === activePanel);
  }
  const panel = PANELS.find(item => item.id === activePanel);
  if (!panel) return;
  el('section-group').textContent = panel.group;
  el('section-title').textContent = panel.label;
  el('section-description').textContent = panel.description;
  el('guide-title').textContent = panel.guide[0];
  el('guide-text').textContent = panel.guide[1];
  el('guide-steps').replaceChildren();
  for (const text of panel.guide[2].split('|')) {
    const item = document.createElement('li'); item.textContent = text; el('guide-steps').append(item);
  }
  el('mascot-stage').dataset.pose = panel.id;
  el('recovery-status').hidden = activePanel !== 'backup';
}
function openPanel(id) {
  const changed=activePanel!==id;
  closeProfileMenus();
  closeSecretView();
  closeValueEditor();
  closeValueAccess();
  closeEnvImport();
  clearTimeout(valueRefreshTimer);
  if (activePanel === 'mcp' && id !== 'mcp') clearPrivateMcp();
  if (activePanel==='provider' && id!=='provider') closeProviderEditor();
  if (activePanel==='search' && id!=='search') clearSearchFields();
  if (activePanel==='voice' && id!=='voice') { el('voice-editor').hidden=true; el('voice-key').value=''; }
  if (activePanel==='mcp' && id!=='mcp') { el('gmail-setup').open=false; el('mcp-access-details').open=false; el('private-mcp-details').open=false; el('gmail-password').value=''; el('mcp-password').value=''; }
  if (activePanel==='plugins' && id!=='plugins') { el('calendar-details').open=false; el('calendar-password').value=''; }
  const panel = PANELS.find(item => item.id === id);
  if (!panel || !panelVisible(panel)) return;
  activePanel = id; renderNav();
  if(id==='mobile')loadMobileNetwork().catch(()=>{});
  if (id === 'search') loadSearchSettings();
  if (id === 'secrets') loadValues();
  if (id === 'mcp' && (changed || !mcpAccessState || requestedMcpPlugin)) loadMcpConnections();
  if (id === 'plugins') loadCalendarStatus();
  if (id === 'mcp' && requestedMcpPlugin) el('mcp-target').value='desktop';
  window.scrollTo(0, 0);
}
function renderNav() {
  const nav = el('settings-nav');
  const current = PANELS.find(panel => panel.id === activePanel);
  if (!current || !panelVisible(current)) activePanel = PANELS.find(panelVisible)?.id || 'vault';
  nav.replaceChildren();
  let group;
  for (const panel of PANELS) {
    if (!panelVisible(panel)) continue;
    if (group !== panel.group) {
      group = panel.group;
      const title = document.createElement('small'); title.className = 'nav-group'; title.textContent = group; nav.append(title);
    }
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'nav-button' + (panel.id === activePanel ? ' active' : '');
    button.textContent = panel.label; button.setAttribute('aria-current', panel.id === activePanel ? 'page' : 'false');
    button.addEventListener('click', () => openPanel(panel.id)); nav.append(button);
  }
  applyActivePanel();
}
for (const button of document.querySelectorAll('[data-open-panel]')) button.addEventListener('click', () => openPanel(button.dataset.openPanel));
const errors = {
  operation_busy: 'Другая операция ещё выполняется. Повторите действие после её завершения.',
  local_requires_host_mode: 'Для сервера на этом компьютере выберите выполнение на этом компьютере в разделе «Среда выполнения». Режим KVM автоматически не меняется.',
  interface_lock_disabled: 'Блокировка настроек выключена.',
  pin_invalid: 'PIN не подошёл.',
  pin_password_required: 'Введите полный пароль Vault. Доступ по PIN недоступен.',
  interface_locked: 'Окно настройки заблокировано. Введите пароль для продолжения.',
  isolation_unavailable: 'KVM недоступен: проверьте образ, доступ к системному libvirt и канал связи. Канал может быть занят другим Игорьком. Исполнитель на компьютере не запускался.',
  desktop_socket_policy_missing: 'Системный libvirt не разрешает канал рабочей папки в AppArmor. Нужна совместимая системная настройка KVM; исполнитель не запускался. Защита сохранена.',
  desktop_socket_policy_unavailable: 'Не удалось проверить системную защиту канала рабочей папки. Исполнитель KVM не запускался. Повторите проверку после настройки libvirt.',
  workspace_required: 'Сначала выберите рабочую папку.',
  agent_not_running: 'Сначала запустите исполнитель выбранного профиля.',
  stop_agent_first: 'Сначала остановите исполнитель, затем измените подключение или пароль.',
  agent_busy: 'Исполнитель занят. Дождитесь завершения задачи или отмените её в чате, затем повторите.',
  profile_not_verified: 'Сначала успешно проверьте подключение этого профиля.',
  authentication_failed: 'Пароль не подошёл. Попробуйте ещё раз.',
  vault_busy: 'Хранилище уже открыто или занято другим процессом.',
  migration_required: 'Для старого хранилища нужен отдельный перенос. Автоматически данные не менялись.',
  invalid_request: 'Проверьте введённые данные.',
  operation_failed: 'Операция не выполнена. Проверьте состояние хранилища.',
  vault_locked: 'Сначала разблокируйте хранилище.',
  recovery_failed: 'Копия повреждена или фраза не подошла. Проверьте файл и фразу.',
  file_exists: 'Этот файл уже существует. Выберите новое имя: старые копии не перезаписываются.',
  file_failed: 'Не удалось прочитать или сохранить файл. Проверьте доступ и размер файла.',
  saved_not_verified: 'Файл сохранён, но проверка не прошла. Не полагайтесь на эту копию до успешной проверки.',
  vault_exists: 'Хранилище уже существует. Восстановление не выполнено: имеющиеся данные сохранены. Проверьте состояние хранилища.',
  profile_exists: 'Профиль с таким именем уже сохранён. Выберите другое имя; прежние настройки и ключ не менялись.',
};
function renderOnboarding() {
  const canContinue = state === 'unlocked' && !interfaceLocked;
  el('onboarding-alternatives').hidden = !canContinue;
  for (const id of ['onboarding-later', 'onboarding-opencode']) {
    el(id).dataset.unavailable = String(!canContinue);
    el(id).disabled = !canContinue || el('refresh').disabled;
  }
  el('execution-save').dataset.unavailable = String(state !== 'unlocked' || agent.running || imageRunning);
  el('execution-save').disabled = el('execution-save').dataset.unavailable === 'true' || el('refresh').disabled;
  el('execution-mode').disabled = state !== 'unlocked' || agent.running || imageRunning;
  el('image-choose').dataset.unavailable = String(state !== 'unlocked' || agent.running || imageRunning);
  el('image-recover').dataset.unavailable = el('image-choose').dataset.unavailable;
  el('image-recover').disabled = el('image-recover').dataset.unavailable === 'true' || el('refresh').disabled;
  el('image-choose').disabled = el('image-choose').dataset.unavailable === 'true' || el('refresh').disabled;
  const progress = window.igorekOnboardingState(state, onboardingProfiles(), agent, selectedProfileId, profilesState,
    successfulProfiles, Boolean(el('workspace-path').dataset.path), onboardingHistoryState);
  if (profilesState === 'ready' && state === 'unlocked') selectedProfileId = progress.profile?.credential_id;
  el('onboarding-title').textContent = state === 'locked' || progress.profile ? 'Продолжить работу' : 'Начнём по порядку';
  el('onboarding-steps').replaceChildren();
  const current = progress.complete.findIndex(done => !done);
  ['Открыть хранилище', 'Сохранить подключение', 'Проверить подключение', 'Запустить Игорька', 'Получить первый результат'].forEach((label, index) => {
    const item = document.createElement('li');
    item.textContent = (progress.complete[index] ? '✓ ' : (index + 1) + '. ') + label;
    if (progress.complete[index]) item.className = 'complete';
    if (index === current) item.setAttribute('aria-current', 'step');
    el('onboarding-steps').append(item);
  });
  el('workspace-open').dataset.unavailable = String(state !== 'unlocked' || !agent.running || !el('workspace-path').dataset.path);
  el('workspace-open').disabled = el('workspace-open').dataset.unavailable === 'true' || el('refresh').disabled;
  el('onboarding-recovery').hidden = state !== 'unlocked';
  el('onboarding-selection').hidden = state !== 'unlocked' || profilesState !== 'ready' || !savedProfiles.length;
  el('onboarding-profile').replaceChildren();
  for (const profile of state === 'unlocked' && profilesState === 'ready' ? onboardingProfiles() : []) {
    const option = document.createElement('option'); option.value = profile.credential_id;
    option.textContent = profile.config.api_format === 'openai-responses' ? profile.config.name : profile.config.name + ' · ' + profile.config.model;
    el('onboarding-profile').append(option);
  }
  el('onboarding-profile').value = selectedProfileId || '';
  const labels = { loading: 'Подождите…', vault: state === 'not_initialized' ? 'Создать хранилище' : 'Открыть хранилище',
    refresh: 'Повторить загрузку', add: 'Добавить подключение', check: 'Проверить подключение', start: 'Запустить Игорька',
    workspace: 'Выбрать рабочую папку', first_task: 'Создать первую сессию', chat: 'Перейти в чат' };
  const details = { loading: 'Читаю состояние приложения и сохранённые подключения…',
    refresh: 'Не удалось прочитать состояние подключений или истории. Повторите загрузку; вводить ключи заново не нужно.',
    vault: state === 'locked' ? 'Хранилище сохранено на этом компьютере. Откройте его паролем, затем запустите нужное подключение. Автоматического запуска нет.' : 'Сначала создайте хранилище паролем или восстановите его из копии.',
    add: 'Сохраните адрес сервиса, модель и API-ключ.', check: 'Профиль сохранён. Следующий шаг — тест подключения и вызова функции.',
    start: 'Сохранённое подключение уже проверено. Ключ повторно вводить не нужно. Запустите Игорька с выбранным профилем.',
    workspace: 'Выберите отдельную папку для первой задачи. Её файлы будут доступны выбранной модели через инструменты Игорька.',
    first_task: 'Создайте сессию и отправьте небольшую задачу. Проверьте ответ в чате. Шаг будет отмечен после завершённой задачи с сохранённым результатом.',
    chat: 'Первый результат получен. Можно продолжить работу в чате с этим подключением.' };
  el('onboarding-status').textContent = details[progress.action];
  el('onboarding-next').textContent = labels[progress.action];
  el('onboarding-next').dataset.unavailable = String(progress.action === 'loading');
  el('onboarding-next').disabled = progress.action === 'loading' || el('refresh').disabled;
}
el('onboarding-profile').addEventListener('change', () => {
  onboardingRevision += 1;
  selectedProfileId = el('onboarding-profile').value; renderOnboarding();
});
el('onboarding-later').addEventListener('click', () => {
  if (state === 'unlocked' && !interfaceLocked && !el('refresh').disabled) el('return-to-chat').click();
});
el('onboarding-opencode').addEventListener('click', async () => {
  if (state !== 'unlocked' || interfaceLocked || el('refresh').disabled) return;
  cancelProfileEdit(); clearPasswordFields();
  el('onboarding-opencode').disabled = true;
  el('error').textContent = '';
  try { await window.igorekVault.openOpenCode(); }
  catch { el('error').textContent = 'Не удалось открыть OpenCode. Повторите переход из основного окна.'; }
  finally { renderOnboarding(); }
});
async function refreshOnboardingProgress() {
  if (state !== 'unlocked' || interfaceLocked || activePanel !== 'start' || document.hidden
    || onboardingRefreshPending || el('refresh').disabled) return;
  const selected = selectedProfileId;
  const revision = onboardingRevision;
  onboardingRefreshPending = true;
  try {
    const reply = await window.igorekVault.status();
    if (revision !== onboardingRevision || state !== 'unlocked' || interfaceLocked || selected !== selectedProfileId
      || reply.error || reply.result?.state !== 'unlocked' || reply.result.interface_locked) return;
    successfulProfiles = Array.isArray(reply.result.onboarding?.successful_profiles) ? reply.result.onboarding.successful_profiles : [];
    onboardingHistoryState = reply.result.onboarding?.state || 'ready';
    if (reply.result.agent) agent = reply.result.agent;
    renderOnboarding();
  } catch {
    if (revision === onboardingRevision && state === 'unlocked' && !interfaceLocked && selected === selectedProfileId) {
      onboardingHistoryState = 'unavailable';
      renderOnboarding();
    }
  } finally { onboardingRefreshPending = false; }
}
window.addEventListener('focus', refreshOnboardingProgress);
window.setInterval(refreshOnboardingProgress, 3000);
el('onboarding-next').addEventListener('click', () => {
  const progress = window.igorekOnboardingState(state, onboardingProfiles(), agent, selectedProfileId, profilesState,
    successfulProfiles, Boolean(el('workspace-path').dataset.path), onboardingHistoryState);
  if (progress.action === 'refresh') return perform('status');
  if (progress.action === 'check') return checkProfile(progress.profile);
  if (progress.action === 'start') return agentOperation('startAgent', progress.profile);
  if (progress.action === 'workspace') return workspaceOperation('chooseWorkspace');
  if (progress.action === 'first_task') return workspaceOperation('openWorkspaceSession');
  if (progress.action === 'chat') return el('return-to-chat').click();
  if (progress.action === 'vault' || progress.action === 'add') {
    const id = progress.action === 'vault' ? 'password' : 'provider-kind-chatgpt';
    openPanel(progress.action === 'vault' ? 'vault' : 'provider');
    if (progress.action==='add') showProviderEditor('choice');
    el(id).scrollIntoView({ block: 'center' }); el(id).focus();
  }
});
function applyState(value, locked = false) {
  onboardingRevision += 1;
  closeProfileMenus();
  chatgptGeneration++;
  clearTimeout(chatgptTimer);
  el('chatgpt-cancel').hidden = true;
  el('chatgpt-status').textContent = '';
  closeSecretView();
  closeValueEditor();
  closeValueAccess();
  closeEnvImport();
  clearTimeout(valueRefreshTimer);
  valuesRequest += 1;
  el('mobile-result').replaceChildren();el('mobile-result').hidden=true;el('mobile-status').textContent='';
  el('vault-values').replaceChildren();
  valuesRecords = []; valuesPage = 0;
  el('values-search').value = ''; el('values-kind').value = 'all'; el('values-origin').value = 'all';
  el('values-page-status').textContent = ''; el('env-import-result').textContent = '';
  el('values-status').textContent = '';
  clearPrivateMcp(value === 'unlocked' && !locked && state === 'unlocked' && !interfaceLocked);
  closeProfileManagement();
  for (const id of ['interface-pin', 'pin-password', 'pin-new', 'pin-repeat', 'lock-option-password']) el(id).value = '';
  const previousState = state;
  const previousInterfaceLocked = interfaceLocked;
  interfaceLocked = locked;
  el('interface-lock-panel').hidden = !interfaceLocked;
  el('interface-password').value = '';
  el('interface-error').textContent = '';
  cancelProfileEdit();
  state = value;
  calendarGeneration++;calendarState={};
  el('calendar-password').value='';el('calendar-status').textContent='';el('calendar-selected').replaceChildren();
  calendarButtons();
  savedProfiles = [];
  el('secret-connections').replaceChildren(); el('profile-list').replaceChildren(); el('voice-summary').replaceChildren();
  el('voice-editor').hidden=true; el('voice-key').value='';
  el('mcp-connections').replaceChildren(); el('calendar-summary').replaceChildren();
  for (const id of ['gmail-setup','mcp-access-details','private-mcp-details','calendar-details','execution-advanced','legacy-profile-details']) el(id).open=false;
  gmailState=null; gmailGeneration++; mcpAccessState=null; mcpLoadGeneration++;
  profilesState = 'loading';
  renderOnboarding();
  el('state').textContent = { not_initialized: 'Создайте пароль для нового хранилища или восстановите ключи из копии.', locked: 'Хранилище заблокировано.', unlocked: 'Хранилище открыто. Выберите сохранённое подключение или добавьте новое.' }[state];
  el('mobile-settings').hidden = state !== 'unlocked';
  el('form').hidden = state === 'unlocked';
  el('confirmation').hidden = state !== 'not_initialized';
  el('repeat').required = state === 'not_initialized';
  el('submit').textContent = state === 'not_initialized' ? 'Создать хранилище' : 'Разблокировать';
  el('lock').hidden = state !== 'unlocked';
  el('lock-interface').hidden = state !== 'unlocked' || !interfaceLockEnabled;
  el('interface-consumers').textContent = agent.running ? 'Исполнитель продолжает работать. Выбранное подключение: ' + agent.profile + '.' : 'Исполнитель сейчас остановлен.';
  el('restoration').hidden = state !== 'not_initialized';
  el('recovery').hidden = state !== 'unlocked';
  el('verification').hidden = false;
  el('recovery-status').textContent = '';
  clearPasswordFields();
  el('password-settings').hidden = state !== 'unlocked';
  el('completion-sound-settings').hidden = state !== 'unlocked';
  el('tts-settings').hidden = state !== 'unlocked';
  el('resource-monitor-settings').hidden = state !== 'unlocked';
  el('password-status').textContent = '';
  el('workspace-setup').hidden = state !== 'unlocked';
  el('provider-setup').hidden = state !== 'unlocked';
  el('mcp-settings').hidden = state !== 'unlocked';
  el('plugin-settings').hidden = state !== 'unlocked';
  el('mcp-password').value = ''; el('gmail-password').value = '';
  el('voice-setup').hidden = state !== 'unlocked';
  el('secrets-hub').hidden = state !== 'unlocked';
  el('search-settings').hidden = state !== 'unlocked';
  clearSearchFields();
  el('secret-connections').replaceChildren();
  el('voice-status').textContent = '';
  el('profile-list').replaceChildren(); el('profile-status').textContent = '';
  if (!navInitialized || previousState !== state || previousInterfaceLocked !== interfaceLocked) { activePanel = interfaceLocked ? 'access' : state === 'unlocked' ? 'start' : 'vault'; navInitialized = true; }
  renderNav();
}
async function perform(action, ...args) {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('error').textContent = '';
  try {
    const reply = await window.igorekVault[action](...args);
    if (reply.error) {
      (interfaceLocked ? el('interface-error') : el('error')).textContent = errors[reply.error] || 'Операция не выполнена.';
      if (reply.error === 'interface_locked' && !interfaceLocked) await perform('status');
      return;
    }
    agent = reply.result.agent || { running: false, profile: "" };
    successfulProfiles = Array.isArray(reply.result.onboarding?.successful_profiles) ? reply.result.onboarding.successful_profiles : [];
    onboardingHistoryState = reply.result.onboarding?.state || 'ready';
    interfaceLockEnabled = reply.result.interface_lock_enabled === true;
    applyState(reply.result.state, reply.result.interface_locked === true);
    el('lock-option-enabled').checked = reply.result.interface_lock_enabled === true;
    el('optional-lock-controls').hidden = !reply.result.interface_lock_enabled;
    el('lock-interface').hidden = state !== 'unlocked' || !reply.result.interface_lock_enabled;
    el('interface-pin-form').hidden = !reply.result.interface_pin_available;
    el('pin-status').textContent = reply.result.interface_pin_enabled ? 'PIN задан для текущего запуска.' : 'PIN не задан.';
    if (!interfaceLocked) {
      if (state === 'unlocked') await loadProfiles();
      await loadExecutionMode();
    }
  } catch { el('error').textContent = 'Нет ответа от приложения. Нажмите «Проверить состояние».'; }
  finally {
    for (const id of ['interface-pin', 'pin-password', 'pin-new', 'pin-repeat', 'lock-option-password']) el(id).value = '';
    el('interface-password').value = '';
    el('password').value = ''; el('repeat').value = '';
    clearRecovery();
    clearRestoration();
    el('profile-key').value = '';
    el('voice-key').value = '';
    for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true';
    if(requestedOwnerPanel&&state==='unlocked'&&!interfaceLocked)openPanel(requestedOwnerPanel);
    else if(activePanel==='plugins'&&state==='unlocked'&&!interfaceLocked)loadCalendarStatus();
  }
}
el('form').addEventListener('submit', event => {
  event.preventDefault();
  const password = el('password').value;
  if (state === 'not_initialized' && password !== el('repeat').value) {
    el('error').textContent = 'Пароли не совпадают.'; return;
  }
  perform(state === 'not_initialized' ? 'create' : 'unlock', password);
});
el('lock-interface').addEventListener('click', () => perform('lockInterface'));
el('lock-option-form').addEventListener('submit', event => { event.preventDefault(); perform('configureInterfaceLock', el('lock-option-password').value, el('lock-option-enabled').checked ? 'on' : 'off'); });
el('interface-pin-form').addEventListener('submit', event => { event.preventDefault(); perform('unlockInterfacePin', el('interface-pin').value); });
el('pin-settings-form').addEventListener('submit', event => {
  event.preventDefault();
  if (el('pin-new').value !== el('pin-repeat').value || !/^[0-9]{6}$/.test(el('pin-new').value)) {
    el('pin-status').textContent = 'Введите и повторите одинаковый PIN из шести цифр.';
    for (const id of ['pin-password', 'pin-new', 'pin-repeat']) el(id).value = '';
    return;
  }
  perform('setInterfacePin', el('pin-password').value, el('pin-new').value);
});
el('pin-remove').addEventListener('click', () => perform('removeInterfacePin', el('pin-password').value));
el('security-lock-interface').addEventListener('click', () => perform('lockInterface'));
el('interface-unlock-form').addEventListener('submit', event => { event.preventDefault(); perform('unlockInterface', el('interface-password').value); });
el('interface-chat').addEventListener('click', () => el('return-to-chat').click());
el('lock').addEventListener('click', () => perform('lock'));
el('refresh').addEventListener('click', () => perform('status'));
el('settings-back').addEventListener('click', () => el('return-to-chat').click());
el('return-to-chat').addEventListener('click', () => {
  cancelProfileEdit(); clearPasswordFields();
  el('password').value = ''; el('repeat').value = ''; clearRecovery(); clearRestoration();
  window.igorekVault.returnToChat().catch(() => {});
});
function setRecoveryStep(next) {
  recoveryStep = next;
  const confirming = next === 'confirm';
  el('recovery-prepare').hidden = confirming;
  el('recovery-confirmation').hidden = !confirming;
  el('recovery-confirmation').disabled = !confirming;
  el('recovery-phrase').readOnly = confirming;
  el('recovery-step').textContent = confirming
    ? 'Шаг 2 из 2. Подтвердите фразу, затем сохраните и проверьте файл.'
    : 'Шаг 1 из 2. Подготовьте фразу и сохраните её отдельно.';
  el('recovery-repeat').value = ''; el('recovery-password').value = '';
  el('phrase-saved').checked = false;
}
el('onboarding-recovery').addEventListener('click', () => {
  openPanel('backup');
  el('recovery').scrollIntoView({ block: 'start' });
  el(recoveryStep === 'confirm' ? 'recovery-repeat' : 'recovery-phrase').focus();
});
el('recovery-next').addEventListener('click', () => {
  if (state !== 'unlocked') return;
  if (!el('recovery-phrase').value.trim()) {
    el('error').textContent = 'Сначала создайте или введите фразу восстановления.';
    el('recovery-phrase').focus(); return;
  }
  if (!el('recovery-phrase').reportValidity()) return;
  el('error').textContent = ''; setRecoveryStep('confirm'); el('recovery-repeat').focus();
});
el('recovery-back').addEventListener('click', () => {
  setRecoveryStep('prepare'); el('recovery-phrase').focus();
});
async function workspaceOperation(action) {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('error').textContent = ''; el('workspace-status').textContent = '';
  try {
    const reply = await window.igorekVault[action]();
    if (reply.canceled) { el('workspace-status').textContent = 'Выбор папки отменён.'; return; }
    if (reply.error) { el('error').textContent = errors[reply.error] || 'Не удалось открыть рабочую папку.'; return; }
    if (reply.result.path) {
      el('workspace-path').textContent = reply.result.path;
      el('workspace-path').dataset.path = reply.result.path;
      renderOnboarding();
      el('workspace-status').textContent = agent.running ? 'Можно создать сессию с этой папкой.' : 'Папка выбрана. Запустите профиль, затем создайте сессию.';
    }
  } catch { el('error').textContent = 'Нет ответа от приложения. Проверьте состояние хранилища.'; }
  finally { for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true'; }
}
el('workspace-choose').addEventListener('click', () => workspaceOperation('chooseWorkspace'));
el('workspace-open').addEventListener('click', () => workspaceOperation('openWorkspaceSession'));
el('readiness-check').addEventListener('click', async () => {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('readiness-status').textContent = 'Проверяю компьютер…';
  el('readiness-results').replaceChildren();el('readiness-summary').textContent='';
  try {
    const reply = await window.igorekVault.readiness();
    const report = reply.result?.readiness;
    if (reply.error || report?.mode !== 'read_only' || !Array.isArray(report.checks)) {
      el('readiness-status').textContent = 'Не удалось получить проверку компьютера. Попробуйте ещё раз.'; return;
    }
    const labels = { ready: 'Готово', needs_action: 'Нужно действие', unknown: 'Не проверено полностью' };
    const facts = { name: 'Система', version: 'Версия', value: 'Архитектура', path: 'Каталог данных',
      libvirt_uri: 'Подключение KVM', bindings_present: 'Компонент управления в комплекте', bindings_loadable: 'Компонент загружается',
      expected_version: 'Нужная версия', size_bytes: 'Размер образа', execution_mode: 'Режим исполнения',
      exists: 'Существует', checked_parent: 'Проверенный каталог', free_bytes: 'Свободно на диске',
      available_bytes: 'Доступная память', total_bytes: 'Всего памяти' };
    const count={ready:0,needs_action:0,unknown:0};
    for (const check of report.checks) count[Object.hasOwn(count,check.status)?check.status:'unknown']++;
    el('readiness-summary').textContent='Готово: '+count.ready+' · Нужно действие: '+count.needs_action+' · Не проверено: '+count.unknown;
    for (const check of report.checks) {
      const card = document.createElement('details'); card.className = 'readiness-row';
      const summary=document.createElement('summary');
      const heading = document.createElement('strong');
      heading.textContent=check.message;
      const badge=document.createElement('span');badge.className='readiness-badge';badge.dataset.status=check.status;badge.textContent=labels[check.status]||labels.unknown;
      summary.append(heading,badge);card.append(summary);
      const details=document.createElement('div');details.className='readiness-facts';
      for (const [key, value] of Object.entries(check.facts || {})) {
        if (!facts[key] || value == null) continue;
        const line = document.createElement('div');
        const formatted = key.endsWith('_bytes') && typeof value === 'number'
          ? (value / 1024 ** 3).toFixed(1) + ' ГиБ' : typeof value === 'boolean' ? (value ? 'Да' : 'Нет') : String(value);
        line.textContent = facts[key] + ': ' + formatted; details.append(line);
      }
      if (check.action) { const line = document.createElement('p'); line.textContent = check.action; details.append(line); }
      card.append(details);el('readiness-results').append(card);
    }
    el('readiness-status').textContent = 'Проверка завершена без изменений. Замечания и непроверенные пункты указаны ниже.';
  } catch { el('readiness-status').textContent = 'Нет ответа от приложения. Повторите проверку.'; }
  finally { for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true'; }
});
function clearPasswordFields() {
  for (const id of ['password-current', 'password-new', 'password-new-repeat']) el(id).value = '';
}
el('password-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (el('password-new').value !== el('password-new-repeat').value) {
    el('error').textContent = 'Новые пароли не совпадают.'; return;
  }
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('error').textContent = ''; el('password-status').textContent = '';
  try {
    const reply = await window.igorekVault.changePassword(el('password-current').value, el('password-new').value);
    if (reply.error) { el('error').textContent = errors[reply.error] || 'Не удалось сменить пароль.'; return; }
    if (reply.result?.state !== 'unlocked') { el('error').textContent = 'Приложение не подтвердило смену пароля.'; return; }
    el('interface-pin-form').hidden = true;
    el('pin-status').textContent = 'PIN сброшен после смены пароля.';
    el('password-status').textContent = 'Пароль изменён. Сохраните новый пароль в менеджере паролей. Прежний пароль больше не открывает это хранилище.';
  } catch { el('error').textContent = 'Нет ответа от приложения. Проверьте состояние хранилища.'; }
  finally {
    clearPasswordFields();
    for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true';
  }
});
el('locations-show').addEventListener('click', async () => {
  if (!el('locations-list').hidden) {
    el('locations-list').hidden = true; el('locations-list').replaceChildren();
    el('locations-status').textContent = '';
    el('locations-show').textContent = 'Показать пути';
    el('locations-show').setAttribute('aria-expanded', 'false');
    return;
  }
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('locations-list').replaceChildren(); el('locations-status').textContent = '';
  try {
    const reply = await window.igorekVault.dataLocations();
    const locations = reply.result?.locations;
    if (!locations || reply.error) { el('locations-status').textContent = 'Не удалось прочитать места хранения.'; return; }
    const labels = { data: 'Данные приложения', vault: 'Зашифрованные ключи', history: 'История и очередь',
      profiles: 'Настройки подключений без ключей', workspace: 'Рабочая папка по умолчанию', ui: 'Данные интерфейса и сопряжения' };
    for (const [key, label] of Object.entries(labels)) {
      if (typeof locations[key] !== 'string') continue;
      const card = document.createElement('details'); card.className = 'storage-path';
      const title = document.createElement('summary');title.textContent = label;card.append(title);
      const path = document.createElement('code');path.textContent = locations[key];card.append(path);
      el('locations-list').append(card);
    }
    el('locations-list').hidden = false;
    el('locations-show').textContent = 'Скрыть пути';
    el('locations-show').setAttribute('aria-expanded', 'true');
    el('locations-status').textContent = 'Показаны пути на этом компьютере. Пароль хранилища и фразу копии приложение не сохраняет для последующего показа.';
  } catch { el('locations-status').textContent = 'Нет ответа от приложения. Повторите попытку.'; }
  finally { for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true'; }
});
function clearRecovery() {
  setRecoveryStep('prepare');
  for (const id of ['recovery-phrase', 'recovery-repeat', 'recovery-password', 'verify-phrase']) el(id).value = '';
  el('phrase-saved').checked = false;
}
function clearRestoration() {
  for (const id of ['restore-phrase', 'restore-password', 'restore-repeat']) el(id).value = '';
}
async function recoveryOperation(action, kind = 'verify') {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('error').textContent = ''; el('recovery-status').textContent = '';
  try {
    const reply = await action();
    if (reply.canceled) { el('recovery-status').textContent = 'Выбор файла отменён.'; return; }
    if (reply.error) { el('error').textContent = (errors[reply.error] || 'Операция не выполнена.') + (reply.path ? ' Файл: ' + reply.path : ''); return; }
    if (reply.result.restored) {
      applyState(reply.result.state, reply.result.interface_locked === true);
      el('password').value = ''; el('repeat').value = '';
      el('recovery-status').textContent = 'Ключи восстановлены: ' + reply.result.credential_count
        + '. Хранилище открыто с новым паролем. Сохраните новый пароль в своём менеджере паролей.';
      await loadProfiles();
    } else if (reply.result.phrase) {
      el('recovery-repeat').value = ''; el('phrase-saved').checked = false;
      el('recovery-phrase').value = reply.result.phrase;
      el('recovery-status').textContent = 'Сохраните фразу отдельно и повторите её ниже.';
    } else if (reply.result.verified === true) {
      el('recovery-status').textContent = (kind === 'save' ? 'Копия сохранена, восстановление из файла проверено. Ключей в копии: ' : 'Восстановление проверено. Ключей в копии: ') + reply.result.credential_count
        + (reply.result.path ? '. Файл: ' + reply.result.path : '')
        + (reply.result.credential_count === 0 ? '. Копия пока пустая: обновите её после добавления ключей.' : '. Храните файл и фразу отдельно. После изменения ключей создайте новую копию.');
    } else { el('error').textContent = 'Приложение не подтвердило проверку копии.'; }
  } catch { el('error').textContent = 'Нет ответа от приложения. Проверьте состояние хранилища.'; }
  finally {
    el('recovery-password').value = ''; el('verify-phrase').value = '';
    if (action !== generatePhrase) clearRecovery();
    clearRestoration();
    for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true';
  }
}
const generatePhrase = () => window.igorekVault.generatePhrase();
el('generate').addEventListener('click', () => recoveryOperation(generatePhrase));
el('recovery-form').addEventListener('submit', event => {
  event.preventDefault();
  if (state !== 'unlocked' || recoveryStep !== 'confirm') return;
  if (el('recovery-phrase').value !== el('recovery-repeat').value) {
    el('error').textContent = 'Фразы не совпадают.'; return;
  }
  recoveryOperation(() => window.igorekVault.saveRecovery(el('recovery-password').value, el('recovery-phrase').value), 'save');
});
el('verify-form').addEventListener('submit', event => {
  event.preventDefault();
  recoveryOperation(() => window.igorekVault.verifyRecovery(el('verify-phrase').value));
});
el('restore-form').addEventListener('submit', event => {
  event.preventDefault();
  if (state !== 'not_initialized') { el('error').textContent = errors.vault_exists; return; }
  if (el('restore-password').value !== el('restore-repeat').value) {
    el('error').textContent = 'Новые пароли не совпадают.'; return;
  }
  recoveryOperation(() => window.igorekVault.restoreRecovery(el('restore-phrase').value, el('restore-password').value));
});
const providerPresets = {
  local: { name: 'local-model', url: 'http://127.0.0.1:1234/v1', model: '' },
  'opencode-go': { name: 'opencode-go', url: 'https://opencode.ai/zen/go/v1', model: 'deepseek-v4-flash' },
  'opencode-zen': { name: 'opencode-zen', url: 'https://opencode.ai/zen/v1', model: 'big-pickle' },
  'zai-coding-plan': { name: 'zai-coding-plan', url: 'https://api.z.ai/api/coding/paas/v4', model: 'glm-5.3-flash' },
  openrouter: { name: 'openrouter', url: 'https://openrouter.ai/api/v1', model: '' },
};
let catalogModels = [], catalogGeneration = 0;
function clearCatalog() {
  catalogGeneration++;
  catalogModels = [];
  el('profile-model-list').replaceChildren();
  el('model-catalog-status').textContent = '';
  el('load-models').disabled = false;
}
function renderCatalog() {
  const select = el('profile-model-list'); select.replaceChildren();
  const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = 'Выберите модель'; select.append(placeholder);
  const freeOnly = el('profile-preset').value === 'opencode-zen' && el('free-models').checked;
  for (const model of catalogModels) {
    if (freeOnly && !model.free) continue;
    const option = document.createElement('option'); option.value = model.id;
    option.textContent = model.name + (model.free ? ' · бесплатно' : '') + ' (' + model.id + ')'; select.append(option);
  }
  select.value = catalogModels.some(m => m.id === el('profile-model').value && (!freeOnly || m.free)) ? el('profile-model').value : '';
}
function updateKeyRequirement() {
  let local = false;
  try { const url = new URL(el('profile-url').value); local = ['http:', 'https:'].includes(url.protocol) && (url.hostname === 'localhost' || url.hostname === '[::1]' || /^127\.\d+\.\d+\.\d+$/.test(url.hostname)); } catch {}
  el('profile-key').required = !editingProfile && !local && el('profile-format').value !== 'openai-responses';
  el('profile-key').placeholder = editingProfile ? 'Оставьте пустым, чтобы сохранить ключ' : local ? 'Необязательно для сервера на этом компьютере' : 'Ключ выбранного сервиса';
}
el('profile-url').addEventListener('input', updateKeyRequirement);
function setPresetView() {
  const preset = el('profile-preset').value;
  const chatgpt = el('profile-format').value === 'openai-responses';
  el('profile-model-caption').textContent = chatgpt ? 'Модель по умолчанию' : 'Идентификатор модели';
  el('profile-catalog').hidden = !chatgpt && (!providerPresets[preset] || preset === 'local');
  el('profile-key-label').hidden = chatgpt;
  el('profile-url-field').hidden = chatgpt;
  el('profile-format-label').hidden = chatgpt;
  el('profile-key').required = !chatgpt && !editingProfile;
  el('profile-url').readOnly = chatgpt;
  el('profile-format').disabled = chatgpt;
  el('profile-preset').disabled = chatgpt;
  el('free-models-label').hidden = preset !== 'opencode-zen';
  el('profile-preset-note').textContent = preset === 'local' ? 'Запустите локальный OpenAI-совместимый сервер. Укажите его адрес и точное имя модели. Для сервера на этом компьютере ключ необязателен. Загрузка модели и расход памяти управляются вашим сервером. Для запуска выберите выполнение на этом компьютере; подключение из KVM пока не поддерживается.' : preset === 'opencode-zen'
    ? 'Zen использует ключ рабочего пространства OpenCode. Бесплатные предложения могут меняться; платные модели скрыты по умолчанию.'
    : providerPresets[preset] ? 'Параметры заполнены. Вставьте ключ: список моделей загрузится автоматически. Параметры можно изменить вручную.' : '';
}
el('profile-preset').addEventListener('change', () => {
  clearCatalog(); setPresetView();
  const preset = providerPresets[el('profile-preset').value];
  if (!preset) return;
  if (!editingProfile) el('profile-name').value = preset.name;
  el('profile-url').value = preset.url; el('profile-format').value = 'openai'; el('profile-model').value = preset.model;
  el('profile-key').value = ''; el('profile-key').required = true;
  updateKeyRequirement();
});
async function loadModels() {
  const preset = el('profile-preset').value;
  const chatgpt = el('profile-format').value === 'openai-responses';
  if (!chatgpt && (!providerPresets[preset] || preset === 'local')) return;
  if (!chatgpt && el('profile-url').value.replace(/\/$/, '') !== providerPresets[preset].url) {
    el('model-catalog-status').textContent = 'Адрес изменён вручную. Укажите модель вручную или выберите пресет заново.'; return;
  }
  const generation = ++catalogGeneration;
  el('load-models').disabled = true; el('model-catalog-status').textContent = 'Загружаю модели…';
  try {
    const reply = chatgpt ? await window.igorekVault.chatgptModels(editingProfile)
      : await window.igorekVault.modelCatalog({ preset, api_key: el('profile-key').value, credential_id: editingProfile || '' });
    if (generation !== catalogGeneration) return;
    if (reply.error) {
      el('model-catalog-status').textContent = reply.error === 'vault_locked' ? 'Сначала разблокируйте хранилище.'
        : chatgpt ? chatgptError(reply.error) : 'Не удалось загрузить каталог. Проверьте ключ и сеть, повторите загрузку или укажите модель вручную.'; return;
    }
    catalogModels = reply.result.models; renderCatalog();
    const selected = el('profile-model').value;
    el('model-catalog-status').textContent = reply.result.note + (chatgpt && selected && !catalogModels.some(model => model.id === selected)
      ? ' Сохранённая модель отсутствует в актуальном списке. Выберите новую модель по умолчанию.' : '');
  } catch {
    if (generation === catalogGeneration) el('model-catalog-status').textContent = 'Нет ответа от приложения. Можно указать модель вручную.';
  } finally { if (generation === catalogGeneration) el('load-models').disabled = false; }
}
el('load-models').addEventListener('click', loadModels);
el('profile-key').addEventListener('change', loadModels);
el('free-models').addEventListener('change', renderCatalog);
el('profile-model-list').addEventListener('change', () => {
  const model = catalogModels.find(m => m.id === el('profile-model-list').value);
  if (model) { el('profile-model').value = model.id; el('profile-format').value = model.api_format; }
});
let secretRequest = 0;
let secretTimer;
let secretProfileId;
let secretIsValue = false;
function clearSecretView() {
  secretRequest += 1;
  clearTimeout(secretTimer);
  secretProfileId = undefined;
  secretIsValue = false;
  el('secret-password').value = '';
  el('secret-password').required = true;
  el('secret-auth').hidden = false;
  el('secret-value').textContent = '';
  el('secret-value').hidden = true;
  el('secret-show').hidden = false;
  el('secret-show').disabled = false;
  el('secret-profile').textContent = '';
  el('secret-status').textContent = '';
}
function closeSecretView() {
  clearSecretView();
  if (el('secret-dialog').open) el('secret-dialog').close();
}
function openSecretView(profile, isValue = false) {
  closeSecretView();
  closeValueAccess();
  if (state !== 'unlocked' || interfaceLocked) return;
  secretProfileId = profile.credential_id;
  secretIsValue = isValue;
  el('secret-profile').textContent = isValue ? profile.name : profile.config.name + ' · ' + profile.config.model;
  el('secret-dialog').showModal();
  el('secret-password').focus();
}
el('secret-close').addEventListener('click', closeSecretView);
el('secret-dialog').addEventListener('close', () => { if (!el('secret-dialog').open) clearSecretView(); });
el('secret-dialog').addEventListener('cancel', closeSecretView);
window.addEventListener('blur', closeSecretView);
window.addEventListener('pagehide', closeSecretView);
document.addEventListener('visibilitychange', () => { if (document.hidden) { closeSecretView(); closeValueEditor(); closeValueAccess(); closeEnvImport(); } });
el('secret-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state !== 'unlocked' || !secretProfileId || !el('secret-dialog').open || el('secret-show').disabled) return;
  const request = ++secretRequest;
  const password = el('secret-password').value;
  el('secret-password').value = '';
  el('secret-show').disabled = true;
  el('secret-status').textContent = 'Проверяю пароль…';
  try {
    const reply = await window.igorekVault[secretIsValue ? 'revealValue' : 'revealProfileKey'](secretProfileId, password);
    if (request !== secretRequest || state !== 'unlocked' || !el('secret-dialog').open || document.hidden) return;
    if (reply.error) { el('secret-status').textContent = errors[reply.error] || 'Просмотр недоступен.'; return; }
    if (typeof reply.result?.secret !== 'string' || reply.result.secret.length > 1024 || reply.result.expires_in_seconds !== 15) {
      el('secret-status').textContent = 'Не удалось подтвердить результат. Повторите просмотр.'; return;
    }
    el('secret-value').textContent = reply.result.secret;
    el('secret-value').hidden = false;
    el('secret-auth').hidden = true;
    el('secret-password').required = false;
    el('secret-show').hidden = true;
    el('secret-status').textContent = 'Значение скроется через 15 секунд. В буфер обмена оно автоматически не копируется.';
    secretTimer = setTimeout(closeSecretView, 15000);
  } catch {
    if (request === secretRequest) el('secret-status').textContent = 'Нет ответа от приложения. Повторите просмотр.';
  } finally {
    if (request === secretRequest) el('secret-show').disabled = false;
  }
});

let valuesRequest = 0;
let valueRefreshTimer;
let valueRequest = 0;
let valueEditing;
let valuesRecords = [];
let valuesPage = 0;
const VALUE_PAGE_SIZE = 25;
const VALUE_ICONS = {
  view: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>',
  edit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15l-1 6Z"/></svg>',
  delete: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7"/></svg>',
  access: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="9" r="5"/><path d="m12 13 9 9M17 18l3-3M14 15l3-3"/></svg>',
};
function closeValueEditor() {
  valueRequest += 1;
  valueEditing = undefined;
  for (const id of ['value-name', 'value-input', 'value-password']) el(id).value = '';
  el('value-status').textContent = '';
  if (el('value-dialog').open) el('value-dialog').close();
}
function openValueEditor(entry, deleting = false) {
  closeValueEditor(); closeSecretView(); closeValueAccess();
  if (state !== 'unlocked' || interfaceLocked) return;
  valueEditing = { id: entry?.credential_id || '', deleting };
  el('value-name').value = entry?.name || '';
  el('value-kind').value = entry?.kind || 'secret';
  el('value-fields').hidden = deleting; el('value-fields').disabled = deleting;
  el('value-submit').disabled = false;
  el('value-submit').textContent = deleting ? 'Удалить запись' : 'Сохранить';
  el('value-title').textContent = deleting ? 'Удалить запись Vault' : entry ? 'Изменить запись Vault' : 'Добавить запись Vault';
  el('value-note').textContent = deleting ? 'Удалить «' + entry.name + '»? Прежние копии восстановления сохранят эту запись.'
    : 'Введите новое значение целиком и подтвердите паролем. Изменение записи сбросит её разрешения сервисам. После сохранения обновите копию восстановления.';
  el('value-dialog').showModal();
  el(deleting ? 'value-password' : 'value-name').focus();
}
function renderValues(entries) {
  valuesRecords = entries;
  renderValueTable();
}
function renderValueTable() {
  el('vault-values').replaceChildren();
  const query = el('values-search').value.trim().toLocaleLowerCase();
  const kind = el('values-kind').value || 'all', origin = el('values-origin').value || 'all';
  const entries = valuesRecords.filter(entry => entry.name.toLocaleLowerCase().includes(query)
    && (kind === 'all' || entry.kind === kind) && (origin === 'all' || (entry.origin?.kind || 'owner') === origin))
    .sort((a, b) => a.name.localeCompare(b.name, 'ru'));
  const pages = Math.max(1, Math.ceil(entries.length / VALUE_PAGE_SIZE));
  valuesPage = Math.min(valuesPage, pages - 1);
  for (const entry of entries.slice(valuesPage * VALUE_PAGE_SIZE, (valuesPage + 1) * VALUE_PAGE_SIZE)) {
    const row = document.createElement('tr');
    const name = document.createElement('td'); name.className = 'vault-name'; name.textContent = entry.name; name.title = entry.name;
    const type = document.createElement('td'), badge = document.createElement('span');
    badge.className = 'vault-badge ' + (entry.kind === 'secret' ? 'is-secret' : ''); badge.textContent = entry.kind === 'secret' ? 'Секрет' : 'Переменная'; type.append(badge);
    const source = document.createElement('td'); source.className = 'vault-source';
    const sourceTitle = document.createElement('span');
    sourceTitle.textContent = entry.origin?.kind === 'agent' ? 'Агент · ' + entry.origin.profile : entry.origin?.kind === 'import' ? 'Импорт · ' + entry.origin.filename : 'Добавлено тобой';
    sourceTitle.title = sourceTitle.textContent; source.append(sourceTitle);
    if (entry.origin?.kind === 'agent') {
      const task = document.createElement('small'); task.textContent = 'Задача ' + entry.origin.task_id.slice(0, 8);
      source.title = 'Задача: ' + entry.origin.task_id + '\nСессия: ' + entry.origin.session_id; source.append(task);
    }
    const actions = document.createElement('td'), group = document.createElement('div'); group.className = 'vault-row-actions';
    for (const [icon, label, action] of [['view', 'Посмотреть', () => openSecretView(entry, true)], ['edit', 'Изменить', () => openValueEditor(entry)], ['delete', 'Удалить', () => openValueEditor(entry, true)], ['access', 'Доступ', () => openValueAccess(entry)]]) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary vault-icon-action' + (icon === 'delete' ? ' is-delete' : '');
      button.title = label + ': ' + entry.name; button.setAttribute('aria-label', button.title); button.innerHTML = VALUE_ICONS[icon];
      button.addEventListener('click', action); group.append(button);
    }
    actions.append(group); row.append(name, type, source, actions);
    el('vault-values').append(row);
  }
  el('values-status').textContent = 'Всего записей: ' + valuesRecords.length + (entries.length !== valuesRecords.length ? ' · Найдено: ' + entries.length : '');
  el('values-empty').hidden = entries.length > 0;
  el('values-empty').textContent = valuesRecords.length ? 'По выбранным условиям записей нет.' : 'Добавь первую запись или перенеси её из .env-файла.';
  el('values-page-status').textContent = entries.length ? (valuesPage * VALUE_PAGE_SIZE + 1) + '–' + Math.min((valuesPage + 1) * VALUE_PAGE_SIZE, entries.length) + ' из ' + entries.length : '0 записей';
  el('values-prev').disabled = valuesPage === 0; el('values-next').disabled = valuesPage + 1 >= pages;
}
for (const id of ['values-search', 'values-kind', 'values-origin']) el(id).addEventListener(id === 'values-search' ? 'input' : 'change', () => { valuesPage = 0; renderValueTable(); });
el('values-prev').addEventListener('click', () => { if (valuesPage > 0) { valuesPage -= 1; renderValueTable(); } });
el('values-next').addEventListener('click', () => { if (!el('values-next').disabled) { valuesPage += 1; renderValueTable(); } });
async function loadValues() {
  if (state !== 'unlocked' || interfaceLocked) return;
  const request = ++valuesRequest;
  el('values-status').textContent = 'Загружаю записи…';
  try {
    const reply = await window.igorekVault.listValues();
    if (request !== valuesRequest || state !== 'unlocked' || interfaceLocked || activePanel !== 'secrets') return;
    if (reply.error) { el('values-status').textContent = errors[reply.error] || 'Не удалось прочитать записи.'; return; }
    renderValues(reply.result.values);
  } catch { if (request === valuesRequest) el('values-status').textContent = 'Не удалось загрузить записи. Повторите обновление списка.'; }
  finally { scheduleValueRefresh(); }
}
function scheduleValueRefresh() {
  clearTimeout(valueRefreshTimer);
  if (state !== 'unlocked' || interfaceLocked || activePanel !== 'secrets') return;
  valueRefreshTimer = setTimeout(() => {
    if (document.hidden || ['secret-dialog', 'value-dialog', 'value-access-dialog', 'env-import-dialog'].some(id => el(id).open)) scheduleValueRefresh();
    else loadValues();
  }, 5000);
}
el('value-add').addEventListener('click', () => openValueEditor());
el('value-refresh').addEventListener('click', loadValues);
el('value-cancel').addEventListener('click', closeValueEditor);
el('value-dialog').addEventListener('close', () => { if (!el('value-dialog').open && valueEditing) closeValueEditor(); });
el('value-dialog').addEventListener('cancel', closeValueEditor);
window.addEventListener('blur', closeValueEditor);
window.addEventListener('pagehide', closeValueEditor);
el('value-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!valueEditing || state !== 'unlocked' || interfaceLocked || el('value-submit').disabled) return;
  const request = ++valueRequest, editing = valueEditing;
  const fields = {credential_id: editing.id, name: el('value-name').value, kind: el('value-kind').value,
    value: el('value-input').value, password: el('value-password').value};
  el('value-input').value = ''; el('value-password').value = '';
  el('value-submit').disabled = true;
  try {
    const reply = editing.deleting ? await window.igorekVault.deleteValue(editing.id, fields.password) : await window.igorekVault.saveValue(fields);
    if (request !== valueRequest || state !== 'unlocked' || interfaceLocked || !el('value-dialog').open) return;
    if (reply.error) { el('value-status').textContent = reply.error === 'profile_exists' ? 'Запись с этим именем уже существует.' : errors[reply.error] || 'Операция не выполнена.'; return; }
    valuesRequest += 1; closeValueEditor(); renderValues(reply.result.values);
  } catch { if (request === valueRequest) el('value-status').textContent = 'Нет ответа от приложения. Обновите список перед повторной попыткой.'; }
  finally { fields.value = ''; fields.password = ''; if (request === valueRequest) el('value-submit').disabled = false; }
});

let valueAccessRequest = 0;
let valueAccessEntry;
let valueAccessState;
let valueAccessRevoking;
let valueAccessBusy = false;
function closeValueAccess() {
  valueAccessRequest += 1;
  valueAccessEntry = valueAccessState = valueAccessRevoking = undefined;
  valueAccessBusy = false;
  el('value-access-password').value = ''; el('value-access-env').value = '';
  el('value-access-status').textContent = ''; el('value-access-name').textContent = '';
  el('value-access-grants').replaceChildren();
  if (el('value-access-dialog').open) el('value-access-dialog').close();
}
function newValueGrant() {
  if (valueAccessBusy) return;
  valueAccessRevoking = undefined;
  el('value-access-password').value = ''; el('value-access-status').textContent = '';
  const available = Boolean(valueAccessState?.servers.length && valueAccessState?.profiles.length);
  el('value-access-fields').hidden = !available; el('value-access-fields').disabled = !available;
  el('value-access-submit').disabled = !available;
  el('value-access-submit').textContent = 'Разрешить доступ';
  el('value-access-operation').textContent = available ? 'Подтвердите передачу значения выбранному сервису.' : 'Для нового разрешения добавьте сервис MCP и профиль модели. Gmail использует своё отдельное подключение.';
}
function renderValueAccess(value) {
  valueAccessState = value;
  el('value-access-name').textContent = value.name;
  for (const [id, entries] of [['value-access-server', value.servers.map(name => ({id: name, name}))], ['value-access-profile', value.profiles.map(p => ({id: p.credential_id, name: p.name}))]]) {
    el(id).replaceChildren();
    for (const entry of entries) { const option = document.createElement('option'); option.value = entry.id; option.textContent = entry.name; el(id).append(option); }
  }
  el('value-access-grants').replaceChildren();
  for (const grant of value.grants) {
    const row = document.createElement('div'); row.className = 'secret-row';
    const name = document.createElement('span'); name.className = 'secret-summary';
    const profile = value.profiles.find(p => p.credential_id === grant.profile_id);
    name.textContent = grant.server + ' · ' + (profile?.name || 'Удалённый профиль') + ' · ' + grant.env_name + (grant.active ? '' : ' · требуется новое подтверждение');
    const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary'; button.textContent = 'Отозвать';
    button.addEventListener('click', () => {
      if (valueAccessBusy) return;
      valueAccessRevoking = grant.id;
      el('value-access-fields').hidden = true; el('value-access-fields').disabled = true;
      el('value-access-password').value = ''; el('value-access-status').textContent = '';
      el('value-access-submit').disabled = false; el('value-access-submit').textContent = 'Отозвать доступ';
      el('value-access-operation').textContent = 'Отозвать: ' + name.textContent + '?'; el('value-access-password').focus();
    });
    row.append(name, button); el('value-access-grants').append(row);
  }
  el('value-access-list-status').textContent = value.grants.length ? 'Сохранённых разрешений: ' + value.grants.length : 'Разрешений пока нет.';
  newValueGrant();
}
async function openValueAccess(entry) {
  closeValueAccess(); closeSecretView(); closeValueEditor();
  if (state !== 'unlocked' || interfaceLocked) return;
  valueAccessEntry = entry;
  const request = ++valueAccessRequest;
  el('value-access-dialog').showModal(); el('value-access-name').textContent = entry.name;
  el('value-access-list-status').textContent = 'Загружаю разрешения…';
  newValueGrant();
  try {
    const reply = await window.igorekVault.valueAccess(entry.credential_id);
    if (request !== valueAccessRequest || state !== 'unlocked' || interfaceLocked || !el('value-access-dialog').open) return;
    if (reply.error) { el('value-access-status').textContent = errors[reply.error] || 'Не удалось прочитать разрешения.'; return; }
    renderValueAccess(reply.result.value_access);
  } catch { if (request === valueAccessRequest) el('value-access-status').textContent = 'Нет ответа от приложения.'; }
}
el('value-access-close').addEventListener('click', closeValueAccess);
el('value-access-new').addEventListener('click', newValueGrant);
el('value-access-dialog').addEventListener('cancel', closeValueAccess);
el('value-access-dialog').addEventListener('close', () => { if (!el('value-access-dialog').open && valueAccessEntry) closeValueAccess(); });
window.addEventListener('blur', closeValueAccess);
window.addEventListener('pagehide', closeValueAccess);
el('value-access-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!valueAccessEntry || state !== 'unlocked' || interfaceLocked || el('value-access-submit').disabled) return;
  valueAccessBusy = true;
  const request = ++valueAccessRequest;
  const fields = {credential_id: valueAccessEntry.credential_id, password: el('value-access-password').value};
  const revoking = valueAccessRevoking;
  if (revoking) fields.grant_id = revoking;
  else { fields.server = el('value-access-server').value; fields.profile_id = el('value-access-profile').value; fields.env_name = el('value-access-env').value; }
  el('value-access-password').value = ''; el('value-access-submit').disabled = true;
  try {
    const reply = await window.igorekVault[revoking ? 'revokeValueAccess' : 'grantValueAccess'](fields);
    if (request !== valueAccessRequest || state !== 'unlocked' || interfaceLocked || !el('value-access-dialog').open) return;
    if (reply.error) { el('value-access-status').textContent = errors[reply.error] || 'Разрешение не изменено. Проверьте пароль и имя переменной; одна переменная не может получать две разные записи.'; return; }
    valueAccessBusy = false;
    renderValueAccess(reply.result.value_access);
    el('value-access-status').textContent = revoking ? 'Доступ отозван.' : 'Разрешение сохранено. Обновите копию восстановления.';
  } catch { if (request === valueAccessRequest) el('value-access-status').textContent = 'Нет ответа от приложения. Откройте доступ заново перед повторной попыткой.'; }
  finally { fields.password = ''; if (request === valueAccessRequest) { valueAccessBusy = false; el('value-access-submit').disabled = false; } }
});

function closeProfileMenus() {
  for (const menu of document.querySelectorAll('.profile-actions:popover-open')) menu.hidePopover();
}
window.addEventListener('blur', closeProfileMenus);
window.addEventListener('resize', closeProfileMenus);
window.addEventListener('scroll', closeProfileMenus);

let settingsMenuId = 0;
function compactSettingsCard({name, description, provider, indicator, hint='', permissions='', menuTitle='', actions=[]}) {
  const card = document.createElement('div'); card.className = 'profile-card compact-settings-card';
  const logo = document.createElement('span'); logo.className = 'profile-logo';
  logo.setAttribute('role','img'); logo.setAttribute('aria-label',provider.name); logo.innerHTML = provider.logo;
  const identity = document.createElement('div'); identity.className = 'profile-identity';
  const title = document.createElement('strong'); title.className = 'profile-name'; title.textContent = name; title.title = name;
  const model = document.createElement('span'); model.className = 'profile-model'; model.textContent = description; model.title = description;
  const status = document.createElement('span'); status.className = 'profile-indicator'; status.dataset.tone = indicator.tone;
  status.textContent = indicator.label; status.title = hint || indicator.label;
  identity.append(title,model);
  if (permissions) { const line=document.createElement('span'); line.className='settings-permissions'; line.textContent=permissions; identity.append(line); }
  identity.append(status);
    const menu = document.createElement('div'); menu.className = 'profile-actions';
    menu.id = 'settings-actions-' + (++settingsMenuId); menu.setAttribute('popover', 'auto');
    menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', 'Действия: ' + name);
    const heading = document.createElement('p'); heading.className = 'profile-actions-heading';
    heading.textContent = menuTitle || name;
    menu.append(heading);
    const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'profile-menu-toggle';
    toggle.textContent = '⋮'; toggle.title = 'Действия';
    toggle.setAttribute('aria-label', 'Действия: ' + name);
    toggle.setAttribute('aria-haspopup', 'menu'); toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('popovertarget', menu.id); toggle.setAttribute('aria-controls', menu.id);
    menu.addEventListener('toggle', event => {
      const open = event.newState === 'open';
      toggle.setAttribute('aria-expanded', String(open));
      if (!open) return;
      const anchor = toggle.getBoundingClientRect(), bounds = menu.getBoundingClientRect();
      const below = window.innerHeight - anchor.bottom - 8;
      const top = below < bounds.height && anchor.top > below ? anchor.top - bounds.height - 4 : anchor.bottom + 4;
      menu.style.left = Math.max(8, Math.min(anchor.right - bounds.width, window.innerWidth - bounds.width - 8)) + 'px';
      menu.style.top = Math.max(8, Math.min(top, window.innerHeight - bounds.height - 8)) + 'px';
      menu.querySelector('button:not(:disabled)')?.focus();
    });
    menu.addEventListener('keydown', event => {
      const buttons = [...menu.querySelectorAll('button:not(:disabled)')];
      const current = buttons.indexOf(document.activeElement);
      let index;
      if (event.key === 'ArrowDown') index = (current + 1) % buttons.length;
      if (event.key === 'ArrowUp') index = (current - 1 + buttons.length) % buttons.length;
      if (event.key === 'Home') index = 0;
      if (event.key === 'End') index = buttons.length - 1;
      if (index !== undefined && buttons.length) { event.preventDefault(); buttons[index].focus(); }
    });
    function action(label, callback, disabled = false, destructive = false) {
      const button = document.createElement('button'); button.type = 'button';
      button.className = destructive ? 'profile-action-delete' : '';
      button.setAttribute('role', 'menuitem'); button.textContent = label;
      button.disabled = disabled; button.dataset.unavailable = String(disabled);
      button.addEventListener('click', () => { closeProfileMenus(); callback(); });
      menu.append(button);
    }
  for (const item of actions) action(item.label,item.run,item.disabled,item.destructive);
  card.append(logo,identity,toggle,menu);
  return card;
}
function modelProfileCard(profile, reveal=false) {
  const provider=window.IgorekProfileCards.profileProvider(profile.config);
  const indicator=window.IgorekProfileCards.profileIndicator(profile,agent);
  const running=agent.running && agent.profile===profile.config.name;
  const voiceOnly=profile.config.name===VOICE_PROFILE_NAME;
  const chatgpt=profile.config.api_format==='openai-responses';
  const actions=[{label:'Проверить подключение',run:()=>checkProfile(profile),disabled:running}];
  if (!voiceOnly) actions.push({label:running?'Остановить исполнитель':'Запустить исполнитель',run:()=>agentOperation(running?'stopAgent':'startAgent',profile),
    disabled:!running&&(profile.status!=='verified'||chatgpt&&!profile.chatgpt?.connected)});
  if (running) actions.push({label:'Перейти в чат',run:()=>el('return-to-chat').click()});
  actions.push({label:voiceOnly?'Настроить голос':'Редактировать',run:()=>voiceOnly?showVoiceEditor():editProfile(profile),disabled:running});
  if (reveal && !chatgpt) actions.push({label:'Посмотреть ключ',run:()=>openSecretView(profile)});
  if (chatgpt) actions.push({label:'Войти снова',run:()=>beginChatgpt(profile),disabled:running},
    {label:'Выйти из ChatGPT',run:()=>openProfileManagement(profile,'chatgptDisconnect'),disabled:running});
  if (!voiceOnly) actions.push({label:'Переименовать',run:()=>openProfileManagement(profile,'renameProfile'),disabled:running},
    {label:'Удалить профиль',run:()=>openProfileManagement(profile,'deleteProfile'),disabled:running,destructive:true});
  let hint=profile.status==='failed'?checkReason(profile.check):voiceOnly?'Голосовое подключение':indicator.label;
  if (profile.check?.checked_at) hint+=' · '+new Date(profile.check.checked_at).toLocaleString();
  return compactSettingsCard({name:profile.config.name,description:chatgpt?'По умолчанию · '+profile.config.model:profile.config.model,provider,indicator,hint,
    menuTitle:provider.name+(profile.chatgpt?.email?' · '+profile.chatgpt.email:''),actions});
}
function renderProfiles(profiles) {
  closeProfileMenus();
  savedProfiles=profiles; profilesState='ready'; renderOnboarding();
  el('secret-connections').replaceChildren(); el('profile-list').replaceChildren();
  if (!profiles.length) {
    el('secret-connections').textContent='Подключений пока нет. Добавьте основную модель.';
    el('profile-list').textContent='Пока нет подключений. Нажмите «Добавить подключение».';
  }
  for (const profile of profiles) {
    el('secret-connections').append(modelProfileCard(profile,true));
    el('profile-list').append(modelProfileCard(profile));
  }
  renderVoiceSettings();
}
function renderVoiceSettings() {
  el('voice-summary').replaceChildren();
  const profile=savedProfiles.find(p=>p.config.name===VOICE_PROFILE_NAME);
  el('voice-add').hidden=Boolean(profile);
  if (!profile) { el('voice-summary').textContent='Голосовое подключение ещё не настроено.'; return; }
  el('voice-summary').append(compactSettingsCard({name:'OpenAI Realtime',description:profile.config.model,
    provider:window.IgorekProfileCards.profileProvider(profile.config),indicator:window.IgorekProfileCards.profileIndicator(profile,agent),
    actions:[{label:'Проверить подключение',run:async()=>{await checkProfile(profile);el('voice-status').textContent=el('profile-status').textContent||el('error').textContent;}},
      {label:'Заменить API-ключ',run:showVoiceEditor}]}));
}
function showVoiceEditor() {
  openPanel('voice'); el('voice-editor').hidden=false; el('voice-key').value='';
  el('voice-editor').scrollIntoView({block:'nearest'}); el('voice-key').focus();
}

function checkReason(check) {
  if (chatgptErrors[check?.reason]) return chatgptErrors[check.reason];
  if (check?.reason === 'subscription_sharing_usage_limit_exceeded') return 'лимит подписки ChatGPT исчерпан';
  if (check?.reason === 'subscription_sharing_usage_unavailable') return 'использование подписки ChatGPT сейчас недоступно';
  const label=({ authentication: 'сервис отклонил ключ', rate_limit: 'ограничение квоты или частоты запросов',
    http_error: 'сервис вернул HTTP ' + check?.http_status, network: 'ошибка сети или TLS', timeout: 'сервис не ответил вовремя',
    invalid_response: 'неожиданный формат ответа', native_tool_call_missing: 'модель не вернула структурированный вызов функции',
    invalid_tool_call: 'вызов функции не соответствует тесту', tool_result_not_confirmed: 'модель не подтвердила результат функции',
    provider_error: 'сервис сообщил об ошибке',
    responses_failed: 'OpenAI отклонил запрос или не завершил ответ',
    responses_transport: 'не удалось завершить обмен с OpenAI',
    responses_tls: 'ошибка защищённого соединения с OpenAI',
    responses_timeout: 'OpenAI не ответил вовремя',
    responses_network: 'ошибка соединения с OpenAI',
    responses_invalid_response: 'ответ OpenAI не прошёл проверку формата',
    responses_incomplete_stream: 'соединение оборвалось до завершения ответа OpenAI' })[check?.reason] || 'не удалось завершить проверку';
  const details=[];
  if (check?.http_status && check.reason!=='http_error') details.push('HTTP '+check.http_status);
  if (check?.provider_error_code) details.push(check.provider_error_code);
  if (check?.error_param) details.push('поле '+check.error_param);
  if (check?.validation_code) details.push(check.validation_code);
  if (check?.response_content_type) details.push('формат '+check.response_content_type);
  if (check?.request_phase==='text_response') details.push('ответ модели');
  if (check?.request_id) details.push('запрос '+check.request_id);
  return label+(details.length?' ('+details.join(' · ')+')':'');
}
async function checkProfile(profile) {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('error').textContent = ''; el('profile-status').textContent = 'Проверяю ' + profile.config.name + '… Один короткий запрос; ожидайте ответа.';
  try {
    const reply = await window.igorekVault.checkProfile(profile.credential_id);
    if (reply.error) { el('error').textContent = chatgptErrors[reply.error] || errors[reply.error] || 'Не удалось проверить профиль.'; return; }
    agent = reply.result.agent || agent;
    renderProfiles(reply.result.profiles);
    el('profile-status').textContent = reply.result.check.status === 'verified'
      ? 'Модель ответила. Можно запустить исполнитель и создать сессию в основном окне.'
      : 'Проверка не прошла: ' + checkReason(reply.result.check) + '.';
    if (reply.result.check.status === 'failed') window.IgorekNotifications?.add({source:'Проверка · '+profile.config.name,level:'error',detail:el('profile-status').textContent}).catch(()=>{});
  } catch { el('error').textContent = 'Нет ответа от приложения. Проверьте состояние хранилища.'; }
  finally { for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true'; }
}
async function agentOperation(action, profile) {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('error').textContent = ''; el('profile-status').textContent = 'Обновляю состояние исполнителя…';
  try {
    const reply = await window.igorekVault[action](profile.credential_id);
    if (reply.error) { el('error').textContent = errors[reply.error] || 'Операция не выполнена.'; return; }
    agent = reply.result.agent;
    renderProfiles(reply.result.profiles);
    el('profile-status').textContent = agent.running
      ? 'Исполнитель запущен. При переходе в чат будет выбран этот профиль; история сообщений сохранится.'
      : 'Исполнитель остановлен. История и очередь сохранены; для продолжения запустите профиль снова.';
  } catch { el('error').textContent = 'Нет ответа от приложения. Проверьте состояние хранилища.'; }
  finally { for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true'; }
}
async function loadProfiles() {
  profilesState = 'loading'; renderOnboarding();
  try {
    const reply = await window.igorekVault.listProfiles();
    if (reply.error) {
      el('error').textContent = errors[reply.error] || 'Не удалось прочитать профили.';
      profilesState = 'failed'; renderOnboarding(); return;
    }
    agent = reply.result.agent || agent;
    renderProfiles(reply.result.profiles);
  } catch {
    profilesState = 'failed'; renderOnboarding();
    el('error').textContent = 'Нет ответа от приложения. Повторите загрузку подключений.';
  }
}
function cancelProfileEdit() {
  el('provider-editor').hidden=true; el('api-editor').hidden=true; el('chatgpt-editor').hidden=true;
  el('chatgpt-form').dataset.credentialId=''; el('chatgpt-name').readOnly=false;
  editingProfile = null;
  el('profile-form').reset();
  el('profile-format').value = 'openai';
  el('profile-format').disabled = false; el('profile-preset').disabled = false; el('profile-url').readOnly = false;
  el('profile-preset').value = 'custom'; clearCatalog(); setPresetView();
  el('profile-name').readOnly = false;
  el('profile-key').required = true;
  el('profile-key').placeholder = '';
  el('profile-submit').textContent = 'Сохранить профиль и ключ';
  el('profile-cancel').hidden = true; el('profile-edit-note').hidden = true;
}
function editProfile(profile) {
  cancelProfileEdit();
  editingProfile = profile.credential_id;
  for (const [id, value] of [['profile-name', profile.config.name], ['profile-url', profile.config.base_url],
    ['profile-model', profile.config.model], ['profile-format', profile.config.api_format]]) el(id).value = value;
  el('profile-preset').value = Object.keys(providerPresets).find(k => providerPresets[k].url === profile.config.base_url) || 'custom';
  if (profile.config.api_format === 'openai-responses') el('profile-preset').value = 'chatgpt';
  setPresetView();
  el('profile-name').readOnly = true;
  el('profile-key').required = false; el('profile-key').placeholder = 'Оставьте пустым, чтобы сохранить ключ';
  el('profile-submit').textContent = 'Сохранить изменения';
  el('profile-cancel').hidden = false; el('profile-edit-note').hidden = false;
  if (profile.config.api_format === 'openai-responses') {
    el('profile-edit-note').hidden = true;
    el('profile-preset-note').textContent = 'Подписка ChatGPT: выберите модель аккаунта. Вход хранится в Vault; пароль OpenAI здесь не нужен.';
  }
  el('error').textContent = ''; el('profile-status').textContent = '';
  openPanel('provider');
  showProviderEditor('api');
  el('provider-editor-title').textContent='Изменить подключение';
  el('profile-form').scrollIntoView({ block: 'start' }); el('profile-model').focus();
}
function showProviderEditor(kind='choice') {
  el('provider-editor').hidden=false;
  el('provider-choice').hidden=kind!=='choice'; el('api-editor').hidden=kind!=='api'; el('chatgpt-editor').hidden=kind!=='chatgpt';
  el('provider-editor-title').textContent=editingProfile?'Изменить подключение':'Новое подключение';
  if(kind==='chatgpt') chatgptConnectionName();
  el('provider-editor').scrollIntoView({block:'nearest'});
}
function closeProviderEditor() {
  const pending=el('chatgpt-cancel').hidden===false;
  cancelProfileEdit();
  if (!pending) return Promise.resolve();
  chatgptGeneration++;clearTimeout(chatgptTimer);el('chatgpt-cancel').hidden=true;el('chatgpt-login').disabled=false;
  return window.igorekVault?.chatgptCancel?.()?.catch?.(()=>{});
}
el('provider-add').addEventListener('click', async()=>{await closeProviderEditor();openPanel('provider');showProviderEditor();el('provider-kind-chatgpt').focus();});
el('provider-editor-close').addEventListener('click', closeProviderEditor);
for (const [id,kind] of [['provider-kind-api','api'],['provider-kind-chatgpt','chatgpt']]) el(id).addEventListener('click',async()=>{await closeProviderEditor();showProviderEditor(kind);el(kind==='api'?'profile-name':'chatgpt-name').focus();});
el('voice-add').addEventListener('click',showVoiceEditor);
el('voice-editor-close').addEventListener('click',()=>{el('voice-editor').hidden=true;el('voice-key').value='';});
el('provider-choice-openai').innerHTML=window.IgorekProfileCards.profileProvider({base_url:'https://api.openai.com/v1'}).logo;
el('provider-choice-api').innerHTML=window.IgorekProfileCards.profileProvider({base_url:'http://127.0.0.1'}).logo;
el('chatgpt-name').addEventListener('input',chatgptConnectionName);
el('chatgpt-name').addEventListener('invalid',chatgptConnectionName);
el('profile-cancel').addEventListener('click', cancelProfileEdit);
el('chatgpt-form').addEventListener('submit', event => { event.preventDefault(); beginChatgpt(savedProfiles.find(p=>p.credential_id===el('chatgpt-form').dataset.credentialId)); });
el('chatgpt-cancel').addEventListener('click', async () => {
  chatgptGeneration++; clearTimeout(chatgptTimer);
  await window.igorekVault.chatgptCancel();
  el('chatgpt-login').disabled = false; el('chatgpt-cancel').hidden = true;
  el('chatgpt-status').textContent = 'Вход отменён.';
});
el('profile-form').addEventListener('submit', async event => {
  event.preventDefault();
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('error').textContent = ''; el('profile-status').textContent = '';
  try {
    const fields = { name: el('profile-name').value,
      model: el('profile-model').value, base_url: el('profile-url').value,
      api_format: el('profile-format').value, api_key: el('profile-key').value };
    const reply = editingProfile
      ? await window.igorekVault.updateProfile({ ...fields, credential_id: editingProfile })
      : await window.igorekVault.saveProfile(fields);
    if (reply.error) { el('error').textContent = errors[reply.error] || 'Не удалось сохранить профиль.'; return; }
    agent = reply.result.agent || agent;
    selectedProfileId = reply.result.saved_profile?.credential_id || editingProfile || selectedProfileId;
    renderProfiles(reply.result.profiles);
    const changed = reply.result.updated_profile?.changed;
    cancelProfileEdit();
    el('profile-status').textContent = changed === false ? 'Параметры не изменились. Прежний результат проверки сохранён.' : 'Профиль сохранён в Vault. Обновите копию восстановления. Теперь проверьте подключение кнопкой в карточке.';
  } catch { el('error').textContent = 'Нет ответа от приложения. Проверьте состояние хранилища.'; }
  finally {
    el('profile-key').value = '';
    for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true';
  }
});


el('voice-form').addEventListener('submit', async event => {
  event.preventDefault();
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  el('error').textContent = ''; el('voice-status').textContent = '';
  const key = el('voice-key').value;
  if (!key.trim()) {
    el('voice-status').textContent = 'Введите API-ключ OpenAI.';
    for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true';
    return;
  }
  try {
    const existing = savedProfiles.find(profile => profile.config.name === VOICE_PROFILE_NAME);
    const fields = { name: VOICE_PROFILE_NAME, model: VOICE_PROFILE_MODEL, base_url: VOICE_PROFILE_URL,
      api_format: 'openai', api_key: key };
    const reply = existing
      ? await window.igorekVault.updateProfile({ ...fields, credential_id: existing.credential_id })
      : await window.igorekVault.saveProfile(fields);
    if (reply.error) { el('error').textContent = errors[reply.error] || 'Не удалось сохранить ключ.'; return; }
    agent = reply.result.agent || agent;
    renderProfiles(reply.result.profiles);
    const targetId = existing?.credential_id || reply.result.saved_profile?.credential_id;
    if (targetId) {
      el('voice-status').textContent = 'Ключ сохранён в Vault. Проверяю подключение…';
      await checkProfile({ credential_id: targetId, config: { name: VOICE_PROFILE_NAME } });
      el('voice-status').textContent = 'Ключ сохранён. Если проверка прошла, голосовой звонок доступен в чате.';
      el('voice-editor').hidden=true;
    } else {
      el('voice-status').textContent = 'Ключ сохранён. Нажмите «Проверить подключение» на карточке профиля.';
    }
  } catch { el('error').textContent = 'Нет ответа от приложения. Проверьте состояние хранилища.'; }
  finally {
    el('voice-key').value = '';
    for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true';
  }
});
const imageErrors = {
  unsafe_destination: 'Восстановление остановлено: каталог образа содержит неожиданные файлы или ссылки.',
  image_in_use: 'Остались диски сессий. Образ не заменён; сначала завершите изолированные сессии.',
  recovery_source_is_destination: 'Выберите исходный образ из поставки, а не текущую копию.',
  recovery_destination_missing: 'Сначала подготовьте образ: текущей копии для восстановления нет.',
  destination_exists: 'Отдельный образ уже существует. Он не был заменён.',
  version_mismatch: 'Версия образа не совпадает с версией приложения. Выберите подходящий образ.',
  checksum_mismatch: 'Контрольная сумма не совпала. Образ повреждён или изменён.',
  missing_manifest: 'Рядом с образом не найден файл .manifest.json.',
  missing_image: 'Файл образа не найден.',
  invalid_manifest: 'Файл описания образа повреждён.',
  dependent_image: 'Нужен самостоятельный образ qcow2 без внешних дисков и шифрования.',
  invalid_qcow2: 'Не удалось проверить формат образа. Проверьте файл и наличие qemu-img.',
  symlink_source: 'Выберите сам файл образа, а не символическую ссылку.',
  symlink_path: 'Путь содержит символическую ссылку. Выберите прямой путь к файлу.',
  image_busy: 'Подготовка образа уже выполняется.',
};
function renderImage(image) {
  imageRunning = image.running === true;
  const phases = { checking_source: 'Проверяю исходный образ: версию, контрольную сумму и формат…',
    copying: 'Сохраняю отдельную копию образа…', checking_copy: 'Проверяю целостность сохранённой копии…',
    idle: 'Для подготовки откройте хранилище и остановите исполнитель.' };
  if (image.phase === 'completed' && image.result?.prepared === true && image.result.execution_enabled === false)
    el('image-status').textContent = 'Отдельный образ подготовлен и проверен. Версия: ' + image.result.tool_version + '. Выберите режим KVM для следующего запуска исполнителя.';
  else if (image.phase === 'failed')
    el('image-status').textContent = imageErrors[image.error] || 'Подготовка не выполнена. Проверьте доступ к файлам и свободное место.';
  else el('image-status').textContent = phases[image.phase] || 'Состояние подготовки не подтверждено.';
  renderOnboarding();
}
async function pollImage() {
  if (interfaceLocked || imagePolling || el('refresh').disabled || (activePanel==='plugins'&&!imageRunning)) { setTimeout(pollImage, 1500); return; }
  imagePolling = true;
  try {
    const reply = await window.igorekVault.imageStatus();
    if (reply.result?.image && (reply.result.image.phase !== 'idle' || imageRunning)) renderImage(reply.result.image);
  } catch {
    if (imageRunning) el('image-status').textContent = 'Не удалось получить состояние. Подготовка могла продолжиться; проверю снова…';
  } finally { imagePolling = false; setTimeout(pollImage, 1500); }
}
async function chooseImage(recover = false) {
  if (imageRunning || imagePolling) return;
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  try {
    el('image-status').textContent = recover ? 'Выберите файл для восстановления…' : 'Ищу установленный пакет образа…';
    const reply = await (recover ? window.igorekVault.recoverImage() : window.igorekVault.prepareImage());
    if (reply.canceled) el('image-status').textContent = 'Выбор отменён. Подготовка не запускалась.';
    else if (reply.result?.image) renderImage(reply.result.image);
    else el('image-status').textContent = errors[reply.error] || imageErrors[reply.error] || 'Подготовка не запущена.';
  } catch { el('image-status').textContent = 'Нет ответа от приложения. Проверьте состояние перед повтором.'; }
  finally { for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true'; }
}
el('image-choose').addEventListener('click', () => chooseImage());
el('image-recover').addEventListener('click', () => chooseImage(true));
setTimeout(pollImage, 1500);

async function loadExecutionMode() {
  try {
    const reply = await window.igorekVault.executionMode();
    if (!['off', 'kvm'].includes(reply.result?.execution?.mode)) throw new Error();
    el('execution-mode').value = reply.result.execution.mode;
    const capabilities = reply.result.execution.capabilities;
    if (capabilities?.platform === 'win32') {
      el('execution-mode').querySelector('option[value="kvm"]').disabled = true;
      el('execution-status').textContent = 'Windows preview: чат и файловые инструменты. Защищённый запуск команд и KVM пока недоступны.';
      return;
    }
    el('execution-status').textContent = reply.result.execution.mode === 'kvm'
      ? 'Выбран KVM. Задачи будут направлены в виртуальную машину; гостевой запуск ещё нужно проверить.'
      : 'Выбран запуск на этом компьютере без KVM.';
  } catch { el('execution-status').textContent = 'Не удалось прочитать выбранный режим. Проверьте состояние приложения.'; }
}
el('execution-save').addEventListener('click', async () => {
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  try {
    const reply = await window.igorekVault.selectExecutionMode(el('execution-mode').value);
    if (reply.error) el('execution-status').textContent = errors[reply.error] || 'Режим не сохранён.';
    else await loadExecutionMode();
  } catch { el('execution-status').textContent = 'Нет ответа от приложения. Проверьте сохранённый режим.'; }
  finally { for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true'; }
});



el('legacy-profile-choose').addEventListener('click', async () => {
  if (interfaceLocked || state !== 'unlocked') return;
  const button = el('legacy-profile-choose'); button.disabled = true;
  try {
    const reply = await window.igorekVault.chooseLegacyProfile();
    if (reply.canceled || interfaceLocked || state !== 'unlocked') return;
    if (reply.error || !reply.result?.legacy_profile) {
      el('legacy-profile-status').textContent = 'Не удалось прочитать профиль cc-backend. Проверьте выбранный JSON-файл.';
      return;
    }
    cancelProfileEdit();
    el('profile-preset').value = 'custom'; clearCatalog(); setPresetView();
    const profile = reply.result.legacy_profile;
    for (const [id, key] of [['profile-name', 'name'], ['profile-model', 'model'], ['profile-url', 'base_url'], ['profile-format', 'api_format']]) el(id).value = profile[key];
    el('profile-key').value = ''; updateKeyRequirement();
    showProviderEditor('api');
    el('legacy-profile-status').textContent = 'Параметры перенесены в форму. Проверьте их, введите ключ и сохраните новое подключение.';
    el('profile-form').scrollIntoView({ block: 'start' });
  } catch { el('legacy-profile-status').textContent = 'Не удалось прочитать выбранный профиль.'; }
  finally { button.disabled = false; }
});

let profileManagement = null;
function closeProfileManagement() {
  profileManagement = null;
  el('profile-manage-password').value = ''; el('profile-rename').value = '';
  if (el('profile-manage-dialog').open) el('profile-manage-dialog').close();
}
function openProfileManagement(profile, action) {
  if (interfaceLocked || state !== 'unlocked' || (agent.running && agent.profile === profile.config.name)) return;
  closeProfileManagement();
  profileManagement = { id: profile.credential_id, action };
  const deleting = action === 'deleteProfile';
  const disconnecting = action === 'chatgptDisconnect';
  el('profile-manage-title').textContent = disconnecting ? 'Выйти из ChatGPT?' : deleting ? 'Удалить профиль «' + profile.config.name + '»?' : 'Переименовать профиль';
  el('profile-manage-note').textContent = deleting ? 'Подключение и его ключ будут удалены из Vault. История разговоров и ранее созданные копии восстановления сохранятся. Ключ у провайдера не отзывается.' : 'Ключ и проверка подключения сохранятся. История прежних разговоров останется под старым именем.';
  if (disconnecting) el('profile-manage-note').textContent = 'Доступ к подписке будет отключён, токены удалены из Vault. Профиль и история сохранятся; для возвращения потребуется вход.';
  if (deleting && profile.chatgpt) el('profile-manage-note').textContent = 'Подключение будет удалено из Vault; приложение попытается отозвать вход в OpenAI. История и прежние копии восстановления сохранятся.';
  el('profile-rename-label').hidden = deleting || disconnecting; el('profile-rename').required = !deleting && !disconnecting;
  el('profile-rename').value = profile.config.name;
  el('profile-manage-status').textContent = '';
  el('profile-manage-dialog').showModal();
}
el('profile-manage-cancel').addEventListener('click', closeProfileManagement);
el('profile-manage-dialog').addEventListener('close', closeProfileManagement);
el('profile-manage-dialog').addEventListener('cancel', closeProfileManagement);
el('profile-manage-form').addEventListener('submit', async event => {
  event.preventDefault();
  const operation = profileManagement;
  if (!operation || interfaceLocked || state !== 'unlocked') return;
  for (const button of document.querySelectorAll('button')) button.disabled = true;
  try {
    const reply = await window.igorekVault[operation.action](operation.id, el('profile-manage-password').value, el('profile-rename').value);
    if (profileManagement !== operation) return;
    if (reply.error) { el('profile-manage-status').textContent = errors[reply.error] || 'Операция не выполнена. Проверьте имя профиля.'; return; }
    if (operation.action === 'chatgptDisconnect') {
      el('chatgpt-status').textContent = reply.result.revoked ? 'Вы вышли из ChatGPT.' : 'Вход удалён на этом компьютере. Отзыв на стороне OpenAI не подтверждён: отключите Игорька в ChatGPT → Settings → Security and login → Sign in with ChatGPT.';
    }
    if (reply.chatgpt_revoked === false) el('chatgpt-status').textContent = 'Профиль удалён. Отзыв входа на стороне OpenAI не подтверждён: отключите приложение в настройках ChatGPT.';
    agent = reply.result.agent || agent;
    cancelProfileEdit(); closeProfileManagement(); renderProfiles(reply.result.profiles);
    el('profile-status').textContent = 'Профили обновлены. Обновите копию восстановления.';
  } catch { if (profileManagement === operation) el('profile-manage-status').textContent = 'Нет ответа от приложения.'; }
  finally {
    el('profile-manage-password').value = '';
    for (const button of document.querySelectorAll('button')) button.disabled = button.dataset.unavailable === 'true';
  }
});

let mcpAccessState = null;
let mcpLoadGeneration=0;
let gmailState=null;
let requestedMcpPlugin = null;
function showMcpMode() {
  const profile=mcpAccessState?.profiles?.find(p=>p.credential_id===el('mcp-profile').value);
  const grant=mcpAccessState?.grants?.[el('mcp-server').value]?.[el('mcp-profile').value];
  el('mcp-mode').value=profile && grant && window.IgorekProfileCards.sameConfig(grant.config,profile.config)?grant.access:'deny';
}
function renderMcpCards(value) {
  el('mcp-connections').replaceChildren();
  if (!value?.servers?.length) {el('mcp-connections').textContent='В этой среде нет подключений MCP.';return;}
  const desktop=el('mcp-target').value!=='host';
  for (const server of value.servers) {
    const provider=window.IgorekProfileCards.serviceProvider(server.id);
    const granted=window.IgorekProfileCards.mcpGrants(value,server.id);
    const indicator=server.disabled?{tone:'offline',label:'Отключён'}:{tone:'verified',label:'Включён'};
    if (server.id==='gmail' && desktop) {
      Object.assign(indicator,!gmailState?{tone:'unverified',label:'Вход не проверен'}:gmailState.error?{tone:'failed',label:'Ошибка входа'}:
        gmailState.pending?{tone:'unverified',label:'Ожидает входа'}:gmailState.connected?{tone:'verified',label:'Вход сохранён'}:{tone:'offline',label:'Не подключён'});
    }
    const actions=[{label:'Права доступа моделей',run:()=>showMcpEditor(server.id)}];
    if (desktop) actions.push({label:'Отдельная задача MCP',run:()=>showMcpEditor(server.id,true),disabled:server.disabled===true});
    if (server.id==='gmail' && desktop) actions.push({label:'Настроить Gmail',run:showGmailEditor},
      {label:'Проверить состояние входа',run:()=>gmailOperation('gmailStatus')});
    el('mcp-connections').append(compactSettingsCard({name:provider.name,description:server.id==='gmail'?'Только чтение почты':server.id==='google_calendar'?'События календаря':server.id==='openai_docs'?'Официальная документация':'Инструменты MCP',
      provider,indicator,permissions:granted.length?'Доступ: '+granted.join(', '):'Доступ моделям не выдан',actions}));
  }
}
function showGmailEditor() {
  openPanel('mcp'); el('gmail-setup').open=true;
  el('gmail-setup').scrollIntoView({block:'nearest'}); el('gmail-password').focus();
}
async function showMcpEditor(server,processing=false) {
  if (activePanel!=='mcp') {requestedMcpPlugin=server;openPanel('mcp');if(processing)el('private-mcp-details').open=true;return;}
  if (!mcpAccessState) await loadMcpConnections();
  if (interfaceLocked || state!=='unlocked' || !mcpAccessState?.servers.some(s=>s.id===server)) return;
  requestedMcpPlugin=null;el('mcp-server').value=server;showMcpMode();
  el('mcp-access-details').open=true;
  if (processing && el('mcp-target').value==='desktop') el('private-mcp-details').open=true;
  el(processing?'private-mcp-details':'mcp-access-details').scrollIntoView({block:'nearest'});
}
function renderMcpAccess(value) {
  const server = requestedMcpPlugin || el('mcp-server').value, profile = el('mcp-profile').value;
  mcpAccessState = value;
  for (const id of ['mcp-server', 'mcp-profile']) el(id).replaceChildren();
  for (const server of value.servers) {
    const option = document.createElement('option'); option.value = server.id; option.textContent = ({openai_docs:'OpenAI Docs',google_calendar:'Google Календарь',gmail:'Gmail'}[server.id] || server.id) + (server.disabled ? ' · отключён' : ''); el('mcp-server').append(option);
  }
  for (const profile of value.profiles) {
    const option = document.createElement('option'); option.value = profile.credential_id; option.textContent = profile.config.name; el('mcp-profile').append(option);
  }
  if (value.servers.some(item=>item.id===server)) el('mcp-server').value=server;
  if (value.profiles.some(item=>item.credential_id===profile)) el('mcp-profile').value=profile;
  el('mcp-access-form').hidden = !value.servers.length || !value.profiles.length;
  el('mcp-status').textContent = !value.servers.length ? 'В этом экземпляре ещё нет настроенных MCP-подключений. Gmail пока не подключён.' : !value.profiles.length ? 'Сначала добавьте профиль модели.' : '';
  showMcpMode();
  renderMcpCards(value);
}

el('mcp-target').addEventListener('change',()=>{
  clearPrivateMcp();mcpLoadGeneration++;mcpAccessState=null;gmailState=null;requestedMcpPlugin=null;
  el('mcp-access-form').hidden=true;el('private-mcp-form').hidden=el('mcp-target').value==='host';
  el('mcp-access-details').open=false;el('private-mcp-details').open=false;el('gmail-setup').hidden=el('mcp-target').value==='host';
  el('mcp-connections').replaceChildren();loadMcpConnections();
});
el('mcp-server').addEventListener('change', () => { requestedMcpPlugin=null; clearPrivateMcp(); showMcpMode(); });
el('mcp-profile').addEventListener('change', () => { clearPrivateMcp(); showMcpMode(); });
async function loadMcpConnections() {
  if (interfaceLocked || state!=='unlocked') return;
  if (requestedMcpPlugin) el('mcp-target').value='desktop';
  const generation=++mcpLoadGeneration, target=el('mcp-target').value;
  el('mcp-load').disabled=true;
  el('gmail-setup').hidden=target==='host'; el('private-mcp-details').hidden=target==='host'; el('private-mcp-form').hidden=target==='host';
  try {
    const reply=await window.igorekVault[target==='host'?'hostMcpAccess':'mcpAccess']();
    if (generation!==mcpLoadGeneration || interfaceLocked || state!=='unlocked' || target!==el('mcp-target').value) return;
    if (reply.error) {el('mcp-status').textContent=errors[reply.error]||'Не удалось загрузить права.';return;}
    renderMcpAccess(reply.result.mcp_access);
    if (requestedMcpPlugin) {el('mcp-access-details').open=true;requestedMcpPlugin=null;}
    if (target!=='host' && typeof window.igorekVault.gmailStatus==='function') {
      const status=await window.igorekVault.gmailStatus();
      if (generation===mcpLoadGeneration && !interfaceLocked && state==='unlocked' && !status.error) showGmailStatus(status.result.gmail);
    }
  } catch {if(generation===mcpLoadGeneration)el('mcp-status').textContent='Нет ответа от приложения. Повторите обновление.';}
  finally{if(generation===mcpLoadGeneration)el('mcp-load').disabled=false;}
}
el('mcp-load').addEventListener('click',loadMcpConnections);
el('mcp-access-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (interfaceLocked || state !== 'unlocked') return;
  try {
    const reply = await window.igorekVault[el('mcp-target').value === 'host' ? 'setHostMcpAccess' : 'setMcpAccess']({server: el('mcp-server').value, credential_id: el('mcp-profile').value, access: el('mcp-mode').value, password: el('mcp-password').value});
    if (interfaceLocked || state !== 'unlocked') return;
    if (reply.error) { el('mcp-status').textContent = errors[reply.error] || 'Не удалось изменить доступ.'; return; }
    renderMcpAccess(reply.result.mcp_access);
    el('mcp-access-details').open=false;
    el('mcp-status').textContent = 'Доступ сохранён. Новые права действуют со следующего вызова.';
  } catch { el('mcp-status').textContent = 'Нет ответа от приложения.'; }
  finally { el('mcp-password').value = ''; }
});

let privateGeneration = 0;
function clearPrivateMcp(remote = true) {
  privateGeneration++;
  el('private-mcp-message').value = ''; el('private-mcp-result').textContent = ''; el('private-mcp-status').textContent = '';
  if (remote !== false && state === 'unlocked' && !interfaceLocked) window.igorekVault?.privateMcpClear?.()?.catch?.(() => {});
}
el('private-mcp-clear').addEventListener('click', clearPrivateMcp);
window.addEventListener('pagehide', () => clearPrivateMcp(false));
el('private-mcp-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (interfaceLocked || state !== 'unlocked') return;
  const generation = ++privateGeneration;
  el('private-mcp-result').textContent = ''; el('private-mcp-status').textContent = 'Подключение MCP…';
  const fields = {server:el('mcp-server').value, credential_id:el('mcp-profile').value, message:el('private-mcp-message').value};
  el('private-mcp-message').value = '';
  try {
    let reply = await window.igorekVault.privateMcpStart(fields);
    while (generation === privateGeneration && !interfaceLocked && state === 'unlocked') {
      if (reply.error || reply.result?.private_mcp?.error) { el('private-mcp-status').textContent = 'Обработка недоступна или прервана. Проверьте разрешение MCP и выбранный профиль.'; return; }
      const value = reply.result.private_mcp;
      if (!value.running) { el('private-mcp-result').textContent = value.result; el('private-mcp-status').textContent = 'Готово. Результат не сохранён в общий чат.'; return; }
      await new Promise(resolve => setTimeout(resolve, 700));
      if (generation !== privateGeneration || interfaceLocked || state !== 'unlocked') return;
      reply = await window.igorekVault.privateMcpStatus();
    }
  } catch { if (generation === privateGeneration) el('private-mcp-status').textContent = 'Нет ответа от приложения.'; }
});

let gmailGeneration = 0;
function showGmailStatus(value) {
  gmailState=value;
  if(mcpAccessState && el('mcp-target').value!=='host') renderMcpCards(mcpAccessState);
  el('gmail-status').textContent = value.error ? 'Авторизация не завершена. Повторите вход; проверьте настройки OAuth-клиента.' : value.pending ? 'Ожидаю входа и разрешения в браузере на этом компьютере…' : value.connected ? 'Авторизация Gmail сохранена в Vault. Выберите профиль, разрешите Gmail и выберите подключение через «+» в чате.' : value.configured ? 'OAuth-клиент сохранён. Введите пароль Vault и нажмите «Войти в Google».' : 'Выберите JSON-файл Google OAuth client типа Desktop app.';
}
async function gmailOperation(action) {
  const generation = ++gmailGeneration;
  if (interfaceLocked || state !== 'unlocked') return;
  const password = el('gmail-password').value;
  if (action !== 'gmailStatus' && !password.trim()) {
    el('gmail-password').value = '';
    el('gmail-status').textContent = 'Введите пароль Vault этого desktop в поле выше. Это пароль хранилища, а не пароль Google.';
    el('gmail-password').focus();
    return;
  }
  try {
    const reply = await window.igorekVault[action](password);
    if (interfaceLocked || state !== 'unlocked' || generation !== gmailGeneration || reply.canceled) return;
    if (reply.error) { el('gmail-status').textContent = errors[reply.error] || 'Операция Gmail не выполнена. Проверьте пароль Vault и файл OAuth-клиента.'; return; }
    if (reply.result.gmail) showGmailStatus(reply.result.gmail);
    if (reply.result.opened) {
      el('gmail-status').textContent = 'Вход открыт в системном браузере. Завершите его на этом компьютере.';
      for (let attempt=0; attempt<90 && generation===gmailGeneration; attempt++) {
        await new Promise(resolve => setTimeout(resolve,2000));
        if (interfaceLocked || state !== 'unlocked' || generation!==gmailGeneration) return;
        const status=await window.igorekVault.gmailStatus();
        if (status.error) { el('gmail-status').textContent = 'Откройте Vault и повторите проверку подключения.'; return; }
        showGmailStatus(status.result.gmail);
        if (!status.result.gmail.pending) return;
      }
    }
  } catch { if (generation===gmailGeneration) el('gmail-status').textContent='Нет ответа от приложения.'; }
  finally { el('gmail-password').value=''; }
}
for (const [id,action] of [['gmail-import-host','gmailImportHost'],['gmail-configure','gmailConfigure'],['gmail-connect','gmailBegin'],['gmail-disconnect','gmailDisconnect'],['gmail-refresh','gmailStatus']]) el(id).addEventListener('click',()=>gmailOperation(action));

let envImportRequest = 0;
let envImportDraft;
let envImportRows = [];
let envImportBusy = false;
function closeEnvImport() {
  envImportRequest += 1;
  const draft = envImportDraft;
  envImportDraft = undefined; envImportRows = []; envImportBusy = false;
  el('env-import-password').value = ''; el('env-import-status').textContent = '';
  el('env-import-source').textContent = ''; el('env-import-count').textContent = '';
  el('env-import-rows').replaceChildren();
  if (el('env-import-dialog').open) el('env-import-dialog').close();
  if (draft && typeof window.igorekVault?.cancelEnvImport === 'function') {
    try { return window.igorekVault.cancelEnvImport(draft.draft_id).catch(() => {}); } catch {}
  }
  return Promise.resolve();
}
function importError(reply) {
  const messages = {env_import_invalid:'Файл или выбранные записи не соответствуют формату .env.', env_import_empty:'Нет записей для переноса.',
    env_import_expired:'Список устарел. Выбери файл заново.', env_import_changed:'Файл изменился после просмотра. Выбери его заново.', env_import_conflict:'В Vault появились записи с такими именами. Выбери файл заново.'};
  return (messages[reply.error] || errors[reply.error] || 'Перенос не выполнен.') + (Number.isInteger(reply.error_line) ? ' Строка ' + reply.error_line + '.' : '');
}
function updateImportSelection() {
  const available = envImportRows.filter(row => !row.checkbox.disabled);
  const count = available.filter(row => row.checkbox.checked).length;
  el('env-import-all').checked = count > 0 && count === available.length;
  el('env-import-all').indeterminate = count > 0 && count < available.length;
  el('env-import-count').textContent = 'Выбрано: ' + count + ' из ' + available.length + ' доступных';
  el('env-import-submit').disabled = envImportBusy || count === 0;
}
function renderEnvImport(draft) {
  envImportDraft = draft; envImportRows = [];
  el('env-import-source').textContent = 'Файл: ' + draft.filename;
  el('env-import-rows').replaceChildren();
  for (const entry of draft.entries) {
    const row = document.createElement('tr'), checkCell = document.createElement('td'), checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = !entry.exists; checkbox.disabled = entry.exists; checkbox.setAttribute('aria-label', 'Перенести ' + entry.name);
    checkbox.addEventListener('change', updateImportSelection); checkCell.append(checkbox);
    const name = document.createElement('td'); name.className = 'vault-name'; name.textContent = entry.name; name.title = entry.name;
    const type = document.createElement('td'), select = document.createElement('select'); select.setAttribute('aria-label', 'Тип записи ' + entry.name); select.disabled = entry.exists;
    for (const [value, label] of [['variable', 'Переменная'], ['secret', 'Секрет']]) {
      const option = document.createElement('option'); option.value = value; option.textContent = label; option.disabled = value === 'secret' && entry.empty; select.append(option);
    }
    select.value = 'variable'; type.append(select);
    const status = document.createElement('td'); status.className = 'vault-import-state'; status.textContent = entry.exists ? 'Уже есть в Vault' : entry.empty ? 'Пустое значение' : 'Готово к переносу';
    row.className = entry.exists ? 'vault-existing' : ''; row.append(checkCell, name, type, status); el('env-import-rows').append(row);
    envImportRows.push({entry, checkbox, select});
  }
  updateImportSelection(); el('env-import-dialog').showModal();
}
el('env-import-choose').addEventListener('click', async () => {
  if (state !== 'unlocked' || interfaceLocked || el('env-import-choose').disabled) return;
  el('env-import-choose').disabled = true; el('env-import-result').textContent = '';
  await closeEnvImport(); closeSecretView(); closeValueEditor(); closeValueAccess();
  const request = ++envImportRequest;
  try {
    const reply = await window.igorekVault.chooseEnvImport();
    if (request !== envImportRequest || state !== 'unlocked' || interfaceLocked || reply.canceled) {
      if (reply.result?.env_import?.draft_id) await window.igorekVault.cancelEnvImport(reply.result.env_import.draft_id).catch(() => {});
      return;
    }
    if (reply.error) { el('env-import-result').textContent = importError(reply); return; }
    renderEnvImport(reply.result.env_import);
  } catch { if (request === envImportRequest) el('env-import-result').textContent = 'Не удалось выбрать файл. Повтори попытку.'; }
  finally { el('env-import-choose').disabled = false; }
});
el('env-import-all').addEventListener('change', () => {
  if (envImportBusy) return;
  for (const row of envImportRows) if (!row.checkbox.disabled) row.checkbox.checked = el('env-import-all').checked;
  updateImportSelection();
});
el('env-import-close').addEventListener('click', closeEnvImport);
el('env-import-dialog').addEventListener('cancel', closeEnvImport);
el('env-import-dialog').addEventListener('close', () => { if (!el('env-import-dialog').open && envImportDraft) closeEnvImport(); });
window.addEventListener('blur', () => { if (el('env-import-dialog').open) closeEnvImport(); });
window.addEventListener('pagehide', closeEnvImport);
el('env-import-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!envImportDraft || state !== 'unlocked' || interfaceLocked || envImportBusy) return;
  const selected = Object.create(null);
  for (const row of envImportRows) if (!row.checkbox.disabled && row.checkbox.checked) selected[row.entry.name] = row.select.value;
  if (!Object.keys(selected).length) return;
  const request = ++envImportRequest;
  const fields = {draft_id: envImportDraft.draft_id, selection: JSON.stringify(selected), password: el('env-import-password').value};
  el('env-import-password').value = ''; envImportBusy = true; updateImportSelection();
  try {
    const reply = await window.igorekVault.commitEnvImport(fields);
    if (request !== envImportRequest || state !== 'unlocked' || interfaceLocked || !el('env-import-dialog').open) return;
    if (reply.error) { el('env-import-status').textContent = importError(reply); return; }
    envImportDraft = undefined; closeEnvImport(); renderValues(reply.result.values);
    el('env-import-result').textContent = 'Перенесено записей: ' + reply.result.imported + '. Исходный файл сохранён. Обнови копию восстановления.';
  } catch { if (request === envImportRequest) el('env-import-status').textContent = 'Нет ответа от приложения. Проверь список перед повторным переносом.'; }
  finally { fields.password = ''; if (request === envImportRequest) { envImportBusy = false; updateImportSelection(); } }
});

let calendarGeneration=0, requestedOwnerPanel=null, calendarBusy=false, calendarStatusBusy=false, calendarState={};
function renderCalendarSummary() {
  el('calendar-summary').replaceChildren();
  if (state!=='unlocked'||interfaceLocked) return;
  const value=calendarState,busy=calendarBusy||calendarStatusBusy;
  const provider=window.IgorekProfileCards.serviceProvider('google_calendar');
  const indicator=value.error?{tone:'failed',label:'Ошибка подключения'}:value.check?.ok===false?{tone:'failed',label:'Ошибка проверки'}:value.pending?{tone:'unverified',label:'Ожидает входа'}:
    value.enabled?{tone:'verified',label:'Включён'}:value.connected?{tone:'unverified',label:'Не включён'}:
      typeof value.connected==='boolean'?{tone:'offline',label:'Не подключён'}:{tone:'unverified',label:'Состояние не проверено'};
  const names=(value.model_access||[]).map(p=>p.profile+(p.access==='local_only'?' (локально)':''));
  el('calendar-summary').append(compactSettingsCard({name:provider.name,provider,indicator,
    description:(value.calendar_name||value.calendar_id||'Календарь не выбран')+(value.time_zone?' · '+value.time_zone:''),permissions:names.length?'Доступ: '+names.join(', '):'Доступ моделям не выдан',
    actions:[{label:'Обновить состояние',run:()=>loadCalendarStatus(),disabled:busy},
      {label:'Проверить и включить',run:()=>calendarOperation('check'),disabled:busy||!value.connected},
      {label:'Выбрать календарь',run:()=>{showCalendarDetails();calendarOperation('calendarList');},disabled:busy||!value.connected},
      {label:'Права доступа моделей',run:()=>{requestedMcpPlugin='google_calendar';openPanel('mcp');},disabled:busy},
      {label:'Дополнительные настройки',run:showCalendarDetails,disabled:busy},
      {label:'Отключить',run:()=>calendarOperation('calendarDisconnect'),disabled:busy||!value.connected,destructive:true}]}));
}
function showCalendarDetails() {el('calendar-details').open=true;el('calendar-details').scrollIntoView({block:'nearest'});}
function calendarButtons() {
  const busy=calendarBusy||calendarStatusBusy||state!=='unlocked'||interfaceLocked;
  el('calendar-configure').disabled=busy;
  el('calendar-connect').disabled=busy||calendarState.pending;
  for(const id of ['calendar-check','calendar-disconnect','calendar-load'])el(id).disabled=busy||!calendarState.connected;
  el('calendar-select').disabled=busy||!calendarState.connected||!el('calendar-selected').options?.length;
  el('calendar-connect').hidden=calendarState.connected===true&&!calendarState.error&&!calendarState.pending;
  renderCalendarSummary();
}
window.addEventListener('igorek-owner-panel',event=>{
  const panel=typeof event.detail==='string'?event.detail:event.detail?.panel;
  if (!PANELS.some(item => item.id === panel)) return;
  requestedMcpPlugin=panel==='mcp'&&['openai_docs','google_calendar'].includes(event.detail?.plugin)?event.detail.plugin:null;
  requestedOwnerPanel=panel;
  if(state==='unlocked'&&!interfaceLocked)openPanel(requestedOwnerPanel);
});
window.addEventListener('blur',()=>{el('calendar-password').value='';});
async function loadCalendarStatus(afterOperation=false) {
  if(state!=='unlocked'||interfaceLocked||calendarStatusBusy||(calendarBusy&&!afterOperation))return;
  calendarStatusBusy=true;calendarButtons();
  const generation=++calendarGeneration;
  try {
    const reply=await window.igorekVault.pluginsStatus();
    if(generation!==calendarGeneration||state!=='unlocked'||interfaceLocked)return;
    if(reply.error){if(reply.error==='operation_busy'){setTimeout(()=>{if(generation===calendarGeneration&&activePanel==='plugins')loadCalendarStatus();},500);return;}el('calendar-status').hidden=false;el('calendar-status').textContent=errors[reply.error]||'Состояние недоступно.';return;}
    const value=reply.result.plugins.google_calendar;
    calendarState=value;
    el('calendar-profile-note').hidden=value.enabled===true&&!value.pending;
    el('calendar-status').hidden=value.enabled===true&&!value.error&&!value.pending&&value.check?.ok!==false;
    const profile=agent.profile || savedProfiles.find(p=>p.credential_id===selectedProfileId)?.config.name || '';
    el('calendar-profile-note').textContent=profile?'Календарь будет доступен модели «'+profile+'». Создание и перенос событий требуют вашего подтверждения.':'Сначала выберите проверенную модель в разделе «Обзор и запуск».';
    el('calendar-status').textContent=value.pending?'Подтвердите доступ в браузере Google. Подключение завершится автоматически.':value.error?'Подключение не завершено. Повторите вход Google.':value.enabled?'Календарь подключён'+(value.time_zone?' · '+value.time_zone:''): 'Нажмите «Подключить Google Календарь» и подтвердите доступ в браузере.';
    el('calendar-connect').disabled=value.pending;
    el('calendar-check').disabled=!value.connected;
    el('calendar-disconnect').disabled=!value.connected;
    if(value.pending)setTimeout(()=>{if(generation===calendarGeneration&&activePanel==='plugins')loadCalendarStatus();},1500);
  }catch{if(generation===calendarGeneration){el('calendar-status').hidden=false;el('calendar-status').textContent='Нет ответа. Повторите проверку состояния.';}}
  finally{calendarStatusBusy=false;calendarButtons();}
}
async function calendarOperation(action) {
  if(state!=='unlocked'||interfaceLocked||calendarBusy||calendarStatusBusy)return;
  const password=el('calendar-password').value;
  if(['calendarConfigure','calendarSelect'].includes(action)&&!password.trim()){
    el('calendar-status').textContent='Введите пароль Vault этого desktop.';el('calendar-password').focus();return;
  }
  calendarBusy=true;
  el('calendar-status').hidden=false;
  calendarButtons();
  el('calendar-password').value='';const generation=++calendarGeneration;
  el('calendar-status').textContent='Выполняю действие…';
  try {
    const profile=agent.profile || savedProfiles.find(p=>p.credential_id===selectedProfileId)?.config.name || '';
    const reply=action==='calendarConnect'?await window.igorekVault.calendarConnect(profile):action==='check'?await window.igorekVault.pluginAction('google_calendar','connect'):action==='calendarSelect'?await window.igorekVault.calendarSelect(el('calendar-selected').value,password):await window.igorekVault[action](password);
    if(generation!==calendarGeneration||state!=='unlocked'||interfaceLocked)return;
    if(reply.canceled){el('calendar-status').textContent='Выбор отменён.';return;}
    if(reply.error){el('calendar-status').textContent=errors[reply.error]||'Не удалось выполнить действие. Проверьте пароль Vault и настройку OAuth.';return;}
    if(reply.result?.plugins?.google_calendar?.check?.ok===false){calendarState=reply.result.plugins.google_calendar;el('calendar-status').textContent='Проверка не прошла. Проверьте Calendar API, разрешения Google и выбранный календарь.';return;}
    if(action==='calendarList') {
      el('calendar-selected').replaceChildren();
      for(const value of reply.result.calendars){const option=document.createElement('option');option.value=value.id;option.textContent=value.name+' · '+value.time_zone;el('calendar-selected').append(option);}
    }
    await loadCalendarStatus(true);
  } catch{if(generation===calendarGeneration)el('calendar-status').textContent='Операция не выполнена. Дождитесь завершения текущего действия и повторите.';}
  finally{calendarBusy=false;el('calendar-password').value='';calendarButtons();}
}
for(const [id,action] of [['calendar-configure','calendarConfigure'],['calendar-connect','calendarConnect'],['calendar-check','check'],['calendar-disconnect','calendarDisconnect'],['calendar-load','calendarList'],['calendar-select','calendarSelect']])el(id).addEventListener('click',()=>calendarOperation(action));
el('calendar-permissions').addEventListener('click',()=>{requestedMcpPlugin='google_calendar';openPanel('mcp');});
perform('status');

for(const menu of document.querySelectorAll('.storage-actions')){
  menu.addEventListener('toggle',event=>{
    const toggle=document.querySelector('[popovertarget="'+menu.id+'"]');toggle.setAttribute('aria-expanded',String(event.newState==='open'));
    if(event.newState!=='open')return;
    const anchor=toggle.getBoundingClientRect(),bounds=menu.getBoundingClientRect();
    menu.style.left=Math.max(8,Math.min(anchor.right-bounds.width,innerWidth-bounds.width-8))+'px';
    menu.style.top=Math.max(8,Math.min(anchor.bottom+4,innerHeight-bounds.height-8))+'px';
  });
  menu.addEventListener('click',event=>{if(event.target.closest('button'))menu.hidePopover();});
}

let mobileNetworkLoaded=false,mobileNetworkDirty=false,mobileBusy=false,mobileAccessEnabled=false;
for(const id of ['mobile-port','mobile-public-url'])el(id).addEventListener('input',()=>{mobileNetworkDirty=true;});
const mobileErrors={mobile_address_invalid:'Введите корректный HTTPS-адрес без логина, пароля, пути и параметров.',mobile_port_invalid:'Порт должен быть целым числом от 1024 до 65535.',mobile_port_unavailable:'Порт занят другим приложением или недоступен. Выберите свободный порт и согласуйте его с пробросом роутера.'};
async function loadMobileNetwork(){
  if(mobileNetworkLoaded)return;
  const reply=await window.igorekVault.mobile('mobile_status');
  if(reply.error||!reply.result)return;
  if(!mobileNetworkDirty){el('mobile-port').value=String(reply.result.port||47823);el('mobile-public-url').value=reply.result.public_url||'';}
  mobileAccessEnabled=reply.result.enabled===true;
  if(reply.result.restore_error){el('mobile-status').textContent=mobileErrors[reply.result.restore_error]||'Не удалось восстановить мобильный доступ. Попробуйте включить его снова.';window.IgorekNotifications?.add({source:'Мобильный доступ',level:'error',detail:el('mobile-status').textContent}).catch(()=>{});}
  else if(mobileAccessEnabled)el('mobile-status').textContent='Мобильный доступ включён. После перезапуска и открытия хранилища он восстановится автоматически.';
  else if((reply.result.devices||[]).some(device=>device.revoked_at==null))el('mobile-status').textContent='Сопряжение сохранено. Включите доступ — повторно сканировать QR не нужно.';
  renderMobileDevices(reply.result.devices||[]);mobileNetworkLoaded=true;
}
for(const [id,action]of [['mobile-start','mobile_start'],['mobile-stop','mobile_stop']])el(id).addEventListener('click',async()=>{
  if(mobileBusy)return;mobileBusy=true;
  for(const id of ['mobile-start','mobile-stop','mobile-port','mobile-public-url'])el(id).disabled=true;
  const target=el('mobile-result');target.replaceChildren();target.hidden=true;
  try{
    if(action==='mobile_start'){
      await loadMobileNetwork();
      const configured=await window.igorekVault.mobile('mobile_configure',{port:el('mobile-port').value,public_url:el('mobile-public-url').value});
      if(configured.error){el('mobile-status').textContent=mobileErrors[configured.error]||errors[configured.error]||'Не удалось сохранить адрес подключения.';return;}
      mobileNetworkLoaded=true;mobileNetworkDirty=false;
      if(configured.result?.public_url!==undefined)el('mobile-public-url').value=configured.result.public_url;
    }
    const reply=await window.igorekVault.mobile(action),result=reply.result;
    if(reply.error||!result){el('mobile-status').textContent=mobileErrors[reply.error]||errors[reply.error]||'Не удалось изменить доступ. Сначала откройте хранилище.';return;}
    el('mobile-status').textContent=result.enabled?'Доступ включён. Адрес: '+(result.qr?.url||(result.urls||[])[0]||'не определён')+'. Профиль: '+(result.profile||'не запущен'):'Доступ с телефона отключён.';
    mobileAccessEnabled=result.enabled===true;
    if(result.enabled)el('mobile-network').open=false;
    renderMobileDevices(result.devices||[]);
    if(result.qr){const image=document.createElement('img');image.className='mobile-pairing-qr';image.alt='Одноразовый QR-код сопряжения';image.src=result.qr.data_url;target.append(image);const note=document.createElement('p');note.className='note';note.textContent='Сканируйте в Igorek Remote. Код одноразовый, действует 5 минут.';target.append(note);const expiry=result.qr.expires_at;setTimeout(()=>{if(image.isConnected&&Date.now()>=expiry*1000){image.remove();note.textContent='Код истёк. Нажмите кнопку выше для нового QR-кода.';}},Math.max(0,expiry*1000-Date.now()));}
    if(result.enabled){const manual=document.createElement('details');manual.className='mobile-pairing-manual';const summary=document.createElement('summary');summary.textContent='Ввести данные вручную';manual.append(summary);for(const [name,value]of [['Адрес',result.qr?.url||(result.urls||[])[0]||'https://IP-этого-компьютера:'+result.port],['Код сопряжения',result.pairing?.code||''],['Отпечаток SHA-256',result.fingerprint||'']]){const label=document.createElement('label');label.textContent=name;const input=document.createElement('textarea');input.readOnly=true;input.rows=name==='Отпечаток SHA-256'?2:1;input.value=value;label.append(input);manual.append(label);}target.append(manual);target.hidden=false;}
  }catch{el('mobile-status').textContent='Приложение не ответило. Повторите попытку.';}
  finally{mobileBusy=false;for(const id of ['mobile-start','mobile-stop','mobile-port','mobile-public-url'])el(id).disabled=false;}
});

function renderMobileDevices(devices){
  el('mobile-start').textContent=devices.some(device=>device.revoked_at==null)?mobileAccessEnabled?'Новый QR-код':'Включить доступ':'Установить сопряжение';
  const list=el('mobile-devices');const signature=JSON.stringify(devices.map(d=>[d.device_id,d.name,d.platform,d.revoked_at]));if(list.dataset.signature===signature||list.querySelector('.profile-actions:popover-open'))return;list.dataset.signature=signature;list.replaceChildren();
  if(!devices.length){const note=document.createElement('p');note.className='note';note.textContent='Сопряжённых телефонов пока нет.';list.append(note);return;}
  for(const device of devices){
    const revoked=device.revoked_at!=null;
    const card=compactSettingsCard({name:device.name||'Телефон',description:device.platform||'Мобильное устройство',provider:{name:'Телефон',logo:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 18h4"/></svg>'},indicator:{tone:revoked?'offline':'verified',label:revoked?'Доступ отключён':'Сопряжено'},actions:revoked?[]:[{label:'Отключить',destructive:true,run:async()=>{const reply=await window.igorekVault.mobile('mobile_revoke',{device_id:device.device_id});if(reply.result)renderMobileDevices(reply.result.devices||[]);}}]});if(revoked)card.querySelector('.profile-menu-toggle').hidden=true;list.append(card);
  }
}
window.setInterval(async()=>{if(activePanel!=='mobile'||state!=='unlocked'||interfaceLocked||document.hidden)return;try{const reply=await window.igorekVault.mobile('mobile_status');if(reply.result){mobileAccessEnabled=reply.result.enabled===true;renderMobileDevices(reply.result.devices||[]);}}catch{}},3000);

if(window.igorekVault?.releaseInfo)window.igorekVault.releaseInfo().then(info=>{el('desktop-release-version').textContent='Версия '+info.version+' · Что нового';for(const text of info.changes||[]){const item=document.createElement('li');item.textContent=text;el('desktop-release-changes').append(item);}el('desktop-release').hidden=false;}).catch(()=>{});

const browserHandoff=el('browser-handoff');
if(window.igorekVault?.browserControl&&browserHandoff){window.igorekVault.browserControl().then(value=>{browserHandoff.checked=value.require_handoff===true;}).catch(()=>{});
browserHandoff.addEventListener('change',async()=>{const previous=!browserHandoff.checked;browserHandoff.disabled=true;try{const value=await window.igorekVault.browserControl({require_handoff:browserHandoff.checked});browserHandoff.checked=value.require_handoff;el('browser-handoff-status').textContent=value.require_handoff?'В браузере доступна ручная передача управления.':'Агент пользуется браузером без ручной передачи управления.';}catch(error){browserHandoff.checked=previous;el('browser-handoff-status').textContent=error.message;}finally{browserHandoff.disabled=false;}});

}


let searchProviders = [];
let searchRequest = 0;
function clearSearchFields() {
  el('search-key').value = '';
  el('search-password').value = '';
}
function renderSearchPlan() {
  const provider = searchProviders.find(item => item.id === el('search-provider').value);
  el('search-plan').textContent = provider ? provider.note + ' Кабинет: ' + provider.url + (provider.configured ? ' Ключ уже сохранён.' : ' Ключ ещё не сохранён.') : 'Поисковые запросы не отправляются.';
}
function renderSearchSettings(search) {
  searchProviders = search.providers || [];
  const select = el('search-provider'); select.replaceChildren();
  for (const item of [{id:'off',name:'Выключен'}, ...searchProviders]) {
    const option = document.createElement('option'); option.value=item.id; option.textContent=item.name; select.append(option);
  }
  select.value=search.provider || 'off'; renderSearchPlan();
}
async function loadSearchSettings() {
  const request=++searchRequest;
  if (!window.igorekVault?.searchStatus) return;
  try {
    const reply=await window.igorekVault.searchStatus();
    if(request!==searchRequest || state!=='unlocked' || interfaceLocked || activePanel!=='search')return;
    if(reply.result?.search)renderSearchSettings(reply.result.search);
  } catch {}
}
el('search-provider').addEventListener('change',()=>{el('search-key').value='';renderSearchPlan();});
window.addEventListener('blur',clearSearchFields);
el('search-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(state!=='unlocked'||interfaceLocked||el('search-save').disabled)return;
  const fields={provider:el('search-provider').value,api_key:el('search-key').value,password:el('search-password').value};
  clearSearchFields(); const request=++searchRequest;
  el('search-save').disabled=true;el('search-provider').disabled=true;
  el('search-status').textContent='Проверяю подключение…';
  try {
    const reply=await window.igorekVault.saveSearch(fields);
    if(request!==searchRequest||state!=='unlocked'||interfaceLocked||activePanel!=='search')return;
    el('search-status').textContent='';
    if(reply.result?.search){renderSearchSettings(reply.result.search);el('search-status').textContent=fields.provider==='off'?'Поиск выключен.':'Подключение проверено и сохранено.';}
  } catch {el('search-status').textContent='';}
  finally {el('search-save').disabled=false;el('search-provider').disabled=false;}
});
