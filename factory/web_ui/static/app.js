const state = {
  sessions: [], sessionTotal: null, selected: "", session: null, items: [], stream: null, lastSeq: 0,
  seenSeq: new Set(), knownAssistantText: new Set(), textStreams: new Map(),
  reasoningStreams: new Map(), toolItems: new Map(), toastTimer: 0, sending: false,
  pinned: true, programmaticScroll: false, settings: null, settingsTimer: 0, dashboard: null,
  workdirs: [],
  attachments: [], attachmentSeq: 0, closedStreams: new Map(), textStreamRounds: new Map(),
  activeTasks: new Set(), finishedTasks: new Set(), startedTasks: new Set(),
  indicatorKey: "indicator.working", runtimeKey: "status.waiting", runtimeActive: false,
  paired: false, device: null,
  taskStarts: new Map(), finishNotes: new Map(),
  profile: null, logsSize: null,
  // Element identifiers created from session.messages when a session opens:
  // These identifiers allow reconstruction from the log to distinguish live elements
  // from the authoritative transcript.
  historyBaseIds: new Set(),
  selectGeneration: 0,
  // Real profile model and context window from /api/model-info (cached by
  // profile+model) to avoid querying them on every redraw.
  modelInfo: new Map(),
  // OpenCode Go quota (rolling/weekly/monthly) from /api/opencode-usage. The key
  // remains on the host: the browser receives only percentages, status, and reset time.
  quota: { status: "idle", error: "", code: "", data: null, fetchedAt: 0 },
  mentions: {
    menu: "closed", mode: "", items: [], active: -1, query: "", dir: "",
    start: 0, caret: 0,
    skillCache: new Map(), skillError: new Map(), skillsTracked: new Map(), tree: new Map(),
    skillPending: new Set(), treePending: new Set(),
  },
  logs: {
    status: "idle", error: "", statusError: "", units: [], active: "", rows: [],
    unit: "", truncated: false, auto: false, timer: 0, tick: 0, updated: 0,
  },
  agents: {
    status: "idle", error: "", sessions: [], counts: {}, total: 0, scannedAt: 0,
    source: "", query: "", visible: 0,
    options: null, optionsLoaded: false, optionsError: "",
    preview: null, importBusy: false, importError: "",
  },
  panel: {
    open: false, tab: "workspace", fullscreen: false,
    workspace: new window.IgorekWorkspaceTabs.Model(),
    changes: { status: "idle", error: "", data: null },
    changesDiff: null,
    review: { mode: "session", base: "", includeUntracked: false, query: "", filter: "", collapsed: new Set(), width: 260, seq: 0, diffSeq: 0 },
    files: { status: "idle", error: "", data: null },
    fileView: null,
    checkpoints: { status: "idle", error: "", data: null, offset: 0, loadingMore: false },
    cp: { hash: "", short: "", status: "idle", changes: null, error: "", diff: null },
    subagents: { status: "idle", error: "", data: null, busy: "" },
  },
  // Live delegate_task subagents (via SSE) before the tool card.
  // Key: subagent_id; Value: summary of the last event.
  subagents: new Map(),
  rail: { items: [], active: -1, signature: "" },
  // Agent plan (update_plan) and usage from the durable session log. Live events
  // update the model immediately; history is loaded in pages via /events?limit=
  plan: {
    session: "", status: "idle", error: "", loading: false, present: false,
    explanation: "", steps: [], seq: 0,
    usage: { input: 0, output: 0, rounds: 0, lastInput: null, lastLatency: 0, truncated: false },
    usageSeqs: new Set(),
    history: null,
  },
};
const byId = (id) => document.getElementById(id);
const ui = {
  connect: byId("connect-view"), workspace: byId("workspace-view"), welcome: byId("welcome-view"),
  conversation: byId("conversation-view"), timeline: byId("timeline"), sessions: byId("session-list"),
  sessionCount: byId("session-count"), title: byId("active-title"), context: byId("session-context"),
  pairForm: byId("pair-form"), pairCode: byId("pair-code"), pairError: byId("pair-error"),
  dialog: byId("session-dialog"), sessionForm: byId("session-form"), profile: byId("profile-select"),
  cwd: byId("cwd-input"), cwdValue: byId("cwd-value"), cwdCombo: byId("cwd-combo"),
  cwdToggle: byId("cwd-toggle"), cwdList: byId("cwd-options"), sessionError: byId("session-error"),
  composer: byId("composer-form"), message: byId("message-input"), send: byId("send-button"),
  stop: byId("stop-button"), attach: byId("attach-button"), attachInput: byId("attach-input"),
  attachments: byId("composer-attachments"),
  composerMeta: byId("composer-meta"), composerModel: byId("composer-model"), mic: byId("mic-button"),
  modePlan: byId("mode-plan"), modeExecute: byId("mode-execute"), modeNote: byId("composer-mode-note"),
  mentionMenu: byId("mention-menu"), mentionHead: byId("mention-menu-head"),
  mentionRows: byId("mention-rows"), mentionNote: byId("mention-note"),
  runtime: byId("runtime-badge"), runtimeLabel: byId("runtime-label"), indicator: byId("streaming-indicator"),
  indicatorLabel: byId("streaming-label"),
  scroll: byId("conversation-scroll"), deviceName: byId("device-name"), logout: byId("logout"),
  toast: byId("toast"),
  brand: byId("open-dashboard"), settingsButton: byId("open-settings"),
  settingsDialog: byId("settings-dialog"), closeSettings: byId("close-settings"), settingsBody: byId("settings-body"),
  dashboardDialog: byId("dashboard-dialog"), closeDashboard: byId("close-dashboard"), dashboardBody: byId("dashboard-body"),
  openLogs: byId("open-logs"),
  logsDialog: byId("logs-dialog"), closeLogs: byId("close-logs"), logsTabs: byId("logs-tabs"),
  logsCaption: byId("logs-caption"), logsOutput: byId("logs-output"), logsError: byId("logs-error"),
  logsRefresh: byId("logs-refresh"), logsAutoSlot: byId("logs-auto-slot"),
  openAgents: byId("open-agents"),
  agentsDialog: byId("agents-dialog"), closeAgents: byId("close-agents"),
  agentsToolbar: byId("agents-toolbar"), agentsSources: byId("agents-sources"),
  agentsSearch: byId("agents-search"), agentsRefresh: byId("agents-refresh"),
  agentsError: byId("agents-error"), agentsCaption: byId("agents-caption"),
  agentsList: byId("agents-list"), agentsMore: byId("agents-more"),
  agentsListView: byId("agents-list-view"), agentsPreview: byId("agents-preview"),
  agentsBack: byId("agents-back"), agentsPreviewTitle: byId("agents-preview-title"),
  agentsPreviewMeta: byId("agents-preview-meta"), agentsPreviewRefresh: byId("agents-preview-refresh"),
  agentsPreviewScroll: byId("agents-preview-scroll"), agentsTimeline: byId("agents-timeline"),
  agentsLoadMore: byId("agents-load-more"), agentsProfile: byId("agents-profile"),
  agentsCwd: byId("agents-cwd"), agentsCwdList: byId("agents-cwd-options"),
  agentsCwdCombo: byId("agents-cwd-combo"), agentsCwdToggle: byId("agents-cwd-toggle"),
  agentsCwdValue: byId("agents-cwd-value"), agentsCwdHint: byId("agents-cwd-hint"),
  agentsImport: byId("agents-import"), agentsImportError: byId("agents-import-error"),
  panelButton: byId("toggle-panel"), sidePanel: byId("side-panel"), closePanel: byId("close-panel"),
  panelResize: byId("panel-resize"), rail: byId("message-rail"),
  sidePanelCaption: byId("side-panel-caption"), panelFullscreen: byId("panel-fullscreen"),
  sideTabs: { plan: byId("side-tab-plan"), changes: byId("side-tab-changes"), files: byId("side-tab-files"), checkpoints: byId("side-tab-checkpoints"), subagents: byId("side-tab-subagents"), terminal: byId("side-tab-terminal") },
  sideViews: { plan: byId("side-view-plan"), changes: byId("side-view-changes"), files: byId("side-view-files"), checkpoints: byId("side-view-checkpoints"), subagents: byId("side-view-subagents"), terminal: byId("side-view-terminal") },
};
const encode = (value) => encodeURIComponent(String(value));
const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
};

// ----------------------------------------------------------------
// Interface localization (ru/en). Russian is the original dictionary language.
// Static markup is translated via data-i18n attributes; dynamic strings are built
// via t() during rendering. Language switching redraws visible representations,
// so all keys must remain in the dictionary, not in code.
// ----------------------------------------------------------------
const LANG_STORAGE_KEY = "igorek-web:lang";
const SUPPORTED_LANGS = ["ru", "en"];
const DEFAULT_LANG = "ru";
let lang = DEFAULT_LANG;

const I18N = {
  ru: {
    "compaction.started": "Сжимаем контекст",
    "compaction.progress": "Готовим память для продолжения",
    "compaction.completed": "Контекст сжат",
    "compaction.failed": "Не удалось сжать контекст — история сохранена",
    "compaction.carried": "Эта память передана агенту вместе с последними ходами. Полная история сохранена.",
    "compaction.partial": "Промежуточное резюме. Контекст изменится после успешного сохранения.",

    "app.title": "Игорёк — агентная рабочая среда",
    "brand.name": "Игорёк",
    "brand.caption": "АГЕНТНАЯ СРЕДА",
    "sidebar.open_dashboard": "Открыть панель управления",
    "sidebar.dashboard_title": "Панель управления",
    "sidebar.close_menu": "Закрыть меню",
    "sidebar.new_session": "Новая сессия",
    "sidebar.sessions_label": "ВАШИ СЕССИИ",
    "sidebar.sessions_aria": "Сессии",
    "sidebar.local_runtime": "Локальный runtime",
    "sidebar.logs": "Логи",
    "sidebar.agents": "История агентов",
    "sidebar.logout": "Завершить подключение",
    "sidebar.footer": "Работает на этом компьютере",
    "sidebar.device_disconnected": "Браузер не подключён",
    "sidebar.local_browser": "Локальный браузер",
    "topbar.open_menu": "Открыть меню",
    "topbar.workspace": "Рабочее пространство",
    "topbar.select_session": "Выберите сессию",
    "topbar.panel_aria": "Панель проверки",
    "topbar.panel_title": "Изменения, файлы, чекпоинты",
    "topbar.settings_aria": "Настройки",
    "topbar.settings_title": "Настройки",
    "status.waiting": "ожидание",
    "status.ready": "готов",
    "status.working": "в работе",
    "status.loading": "загрузка",
    "status.offline": "нет связи",
    "status.stopping": "останавливаю",
    "status.queued": "в очереди",
    "status.typing": "печатает",
    "status.needs_attention": "нужно внимание",
    "status.reconnecting": "переподключение",
    "status.check": "проверить статус",
    "status.tool": "инструмент",
    "status.stopped": "остановлено",
    "indicator.working": "Игорёк работает",
    "indicator.stopping": "Останавливаю…",
    "indicator.approval": "Ждёт подтверждения",
    "indicator.isolation": "Запускается изолированная среда…",
    "task.accepted": "Задача принята",
    "task.isolation_extra": "— запускается изолированная среда…",
    "task.started": "Игорёк работает",
    "task.paused": "Работа приостановлена до подтверждения",
    "task.stopped": "Задача остановлена",
    "task.finished": "Задача завершена",
    "task.finished_status": "Задача завершилась: {status}",
    "task.finished_error": "Задача завершилась с ошибкой: {error}",
    "task.no_description": "без описания",
    "task.unknown_status": "неизвестный статус",
    "task.requeued": "Задача снова в очереди",
    "task.requeued_steer": "Продолжаю — доставлены новые сообщения",
    "task.requeued_orphan": "Задача перезапущена с сообщениями из очереди",
    "task.requeued_approval": "Продолжаю после подтверждения…",
    "duration.seconds": "{n} с",
    "duration.minutes": "{m} мин",
    "duration.minutes_seconds": "{m} мин {s} с",
    "duration.hours": "{h} ч",
    "duration.hours_minutes": "{h} ч {r} мин",
    "bytes.b": "Б",
    "bytes.kb": "КБ",
    "bytes.mb": "МБ",
    "session.no_messages": "пока без сообщений",
    "session.empty_list": "Здесь появятся ваши рабочие сессии.",
    "session.default_title": "Новая сессия",
    "session.profile_fallback": "профиль",
    "session.rename": "Переименовать",
    "session.rename_aria": "Переименовать сессию «{title}»",
    "session.rename_input_aria": "Новое название сессии",
    "session.renamed": "Название обновлено",
    "session.cwd_missing": "рабочая папка не указана",
    "connect.eyebrow": "ВАШ ЛОКАЛЬНЫЙ АГЕНТ",
    "connect.title_lead": "Рабочее место",
    "connect.title_accent": "Игорька",
    "connect.lead": "Подключите этот браузер к вашему runtime. Переписка и ключи остаются на вашем компьютере.",
    "connect.step1_title": "Откройте терминал",
    "connect.step1_hint": "На компьютере, где работает Игорёк",
    "connect.step2_title": "Создайте код подключения",
    "connect.code_label": "Одноразовый код",
    "connect.button": "Подключить",
    "connect.note": "Код действует ограниченное время и используется один раз. Доступ можно отозвать из самого интерфейса.",
    "connect.orbit_local": "ЛОКАЛЬНО",
    "connect.orbit_tags": "АГЕНТ · ИНСТРУМЕНТЫ · СОБЫТИЯ",
    "connect.paired": "Браузер подключён к локальному runtime",
    "welcome.eyebrow": "СЕССИИ АГЕНТА",
    "welcome.title_lead": "Продолжим",
    "welcome.title_accent": "с этого места?",
    "welcome.lead": "Выберите сессию слева или начните новую. Вызовы инструментов, результаты и ход работы появятся здесь по мере выполнения.",
    "welcome.button": "Создать сессию",
    "welcome.meta1": "ОДИН ИНТЕРФЕЙС",
    "welcome.meta2": "ВАШ ЛОКАЛЬНЫЙ RUNTIME",
    "composer.rail_aria": "Навигация по вашим сообщениям",
    "composer.attach_aria": "Прикрепить файл",
    "demo.start": "Познакомиться с Игорьком · /demo",
    "demo.separate": "Отправьте /demo отдельно, без вложений.",
    "navigation.unavailable": "Этот раздел сейчас недоступен. Откройте его через меню приложения.",
    "composer.placeholder": "Напишите задачу Игорьку…",
    "composer.input_aria": "Сообщение Игорьку",
    "composer.hint_text": " — отправить",
    "composer.stop": "Стоп",
    "composer.send": "Отправить",
    "composer.send_aria": "Отправить сообщение",
    "composer.caption": "Локальная сессия · сообщения обрабатываются выбранным профилем",
    "composer.queued": "в очереди",
    "composer.force_send": "Отправить сейчас",
    "composer.force_send_title": "Доставить сообщение в текущую задачу немедленно, не дожидаясь её завершения",
    "composer.cancel_queued": "Отменить",
    "composer.cancel_queued_title": "Убрать сообщение из очереди задачи",
    "composer.prioritized": "отправляется…",
    "composer.model_title": "Модель сессии: {model}",
    "composer.profile_title": "Профиль сессии: {profile}",
    "composer.mic_start": "Голосовой ввод",
    "composer.mic_stop": "Остановить голосовой ввод",
    "composer.mic_error": "Голосовой ввод: {error}",
    "composer.mic_denied": "Доступ к микрофону запрещён",
    "composer.mic_unavailable": "Распознавание речи недоступно",
    "voice.button_aria": "Голосовой звонок",
    "voice.button_title": "Голосовой звонок",
    "voice.panel_aria": "Панель голосового звонка",
    "voice.resize_aria": "Изменить размер панели",
    "voice.error.key_setup": "Для звонка нужен отдельный API-ключ OpenAI. Откройте «Настройка Игорька» → «Голосовая модель» и сохраните ключ: адрес https://api.openai.com/v1, формат OpenAI, модель реального времени gpt-realtime-2.1-mini. Там же нажмите «Проверить подключение». Запускать исполнитель этого профиля не нужно: задачи останутся на текущем профиле.",
    "voice.error.unavailable": "Голосовой режим сейчас недоступен. Проверьте подключение и настройки сервера.",
    "voice.title": "Голосовой звонок",
    "voice.minimize_aria": "Свернуть панель звонка",
    "voice.restore_aria": "Развернуть панель звонка",
    "voice.hangup_aria": "Завершить звонок",
    "voice.user_label": "Вы",
    "voice.assistant_label": "Игорёк",
    "voice.system_label": "Система",
    "voice.mic_aria": "Микрофон",
    "voice.mic_label": "Микрофон",
    "voice.speaker_aria": "Звук",
    "voice.speaker_label": "Звук",
    "voice.status.idle": "Готов к звонку",
    "voice.status.connecting": "Подключаю…",
    "voice.status.listening": "Слушаю",
    "voice.status.thinking": "Думаю",
    "voice.status.reconnecting": "Переподключаюсь…",
    "voice.status.failed": "Сбой ответа",
    "voice.status.error": "Ошибка звонка",
    "voice.status.mic_off": "Микрофон выключен",
    "voice.status.mic_on": "Микрофон включён",
    "voice.status.speaker_off": "Звук выключен",
    "voice.status.speaker_on": "Звук включён",
    "voice.error.no_session": "Сначала выберите сессию",
    "voice.error.mic_denied": "Нет доступа к микрофону",
    "voice.error.mic_missing": "Микрофон не найден",
    "voice.error.expired": "Сессия звонка истекла — попробуйте ещё раз",
    "voice.error.start": "Не удалось начать звонок: {error}",
    "voice.error.audio": "Не удалось включить звук — нажмите «Звук»",
    "voice.error.task": "Не удалось отправить задачу голосом",
    "voice.error.approval": "Не удалось отправить решение",
    "voice.error.realtime": "Ошибка голосового сервера",
    "voice.error.reconnect": "Не удалось переподключиться",
    "voice.task_running": "Выполняется задача {task}…",
    "voice.task_done": "Задача выполнена",
    "voice.task_failed": "Задача завершилась с ошибкой",
    "voice.task_failed_reason": "Задача завершилась с ошибкой: {error}",
    "voice.result_prefix": "Результат выполнения задачи: ",
    "voice.approval_title": "Нужно подтверждение",
    "composer.mode_aria": "Режим работы",
    "composer.mode_plan": "План",
    "composer.mode_execute": "Выполнить",
    "composer.mode_plan_note": "Режим планировки — агент только планирует, ничего не выполняет",
    "composer.mode_hint": " — режим",
    "message.you": "Вы",
    "message.user_letter": "И",
    "message.copy": "копировать",
    "message.copied": "скопировано",
    "message.copy_unavailable": "недоступно",
    "message.copy_aria": "Скопировать текст сообщения",
    "rail.tick": "Сообщение {n}{suffix}",
    "rail.preview_fallback": "Сообщение",
    "tool.default_name": "Инструмент",
    "tool.status.running": "выполняется",
    "tool.status.done": "готово",
    "tool.status.error": "ошибка",
    "tool.status.exit": "код выхода",
    "tool.status.stopped": "остановлено",
    "tool.input": "ВХОДНЫЕ ДАННЫЕ",
    "tool.result": "РЕЗУЛЬТАТ",
    "tool.result_ms": "РЕЗУЛЬТАТ · {ms} МС",
    "tool.open_details": "Открыть детали",
    "tool.params": "{n} параметр(а)",
    "reasoning.title": "Ход рассуждения",
    "reasoning.title_live": "Ход рассуждения · сейчас",
    "approval.waiting": "Ожидает подтверждения: {tool}",
    "approval.decided": "Решение принято: {tool}",
    "approval.default_tool": "действие агента",
    "approval.default_reason": "Проверьте запрос в центре согласований.",
    "approval.resolved_allow_once": "разрешено один раз",
    "approval.resolved_allow_session": "разрешено до конца сессии",
    "approval.resolved_reject": "отклонено",
    "approval.resolved_cancel": "отменено",
    "approval.btn_allow": "Разрешить",
    "approval.btn_allow_session": "Разрешить всегда",
    "approval.btn_reject": "Отклонить",
    "approval.sent": "Решение отправлено",
    "error.connection_closed": "Подключение браузера завершено",
    "error.request_failed": "Ошибка запроса ({status})",
    "attachments.preview_aria": "Посмотреть {name}",
    "attachments.loading": "загрузка…",
    "attachments.error": "ошибка",
    "attachments.remove_aria": "Убрать вложение «{name}»",
    "attachments.empty": "Файл «{name}» пустой",
    "attachments.limit": "Не больше {n} вложений в одном сообщении",
    "attachments.upload_failed": "Не удалось загрузить «{name}»: {error}",
    "attachments.send_failed": "Не отправлено — проверьте вложения: {list}",
    "panel.aria": "Панель проверки",
    "panel.kicker": "ПРОВЕРКА",
    "panel.data": "Данные сессии",
    "panel.tabs_aria": "Разделы панели проверки",
    "panel.tab.changes": "Изменения",
    "panel.tab.files": "Файлы",
    "panel.tab.checkpoints": "Чекпоинты",
    "panel.tab.subagents": "Агенты",
    "panel.tab.terminal": "Терминал",
    "panel.caption.terminal": "Терминал сессии",
    "terminal.title": "Терминал",
    "terminal.hint": "Интерактивная оболочка внутри рабочей папки сессии.",
    "terminal.connecting": "Подключаю терминал…",
    "terminal.connected": "Терминал подключён — хост, без изоляции",
    "terminal.ready": "Оболочка запускается только по кнопке",
    "terminal.connect": "Подключиться",
    "terminal.stop": "Завершить терминал",
    "terminal.connected_kvm": "Терминал подключён — KVM текущей сессии",
    "terminal.reason.kvm_terminal_unavailable": "Терминал KVM пока недоступен: нужен гостевой сервис и мост к владельцу ВМ. Оболочка хоста не запускается.",
    "terminal.reason.host_terminal_disabled": "Терминал хоста отключён настройкой сервера",
    "terminal.exited": "Оболочка завершилась (код {code})",
    "terminal.restart": "Перезапустить оболочку",
    "terminal.open": "Открыть терминал",
    "terminal.error.ticket": "Не удалось получить доступ к терминалу: {reason}",
    "terminal.error.connect": "Не удалось подключиться к терминалу: {reason}",
    "terminal.error.unavailable": "Терминал доступен только при открытом веб-интерфейсе на этом компьютере.",
    "terminal.reason.ticket_invalid": "билет терминала недействителен или уже использован",
    "terminal.reason.ticket_expired": "срок действия билета терминала истёк",
    "terminal.reason.ticket_session_mismatch": "билет принадлежит другой сессии",
    "terminal.reason.terminal_busy": "к этому терминалу уже подключена другая вкладка",
    "terminal.reason.terminal_limit": "достигнут предел одновременно открытых терминалов",
    "terminal.reason.terminal_exists": "терминал этой сессии уже открыт",
    "terminal.reason.ghostty_missing": "оболочка недоступна",
    "terminal.aria": "Терминал",
    "terminal.shell_aria": "Область терминала",
    "panel.caption.changes": "Изменения файлов",
    "panel.caption.files": "Файлы сессии",
    "panel.caption.checkpoints": "История версий",
    "panel.caption.subagents": "Субагенты сессии",
    "panel.subagents_title": "Субагенты",
    "panel.subagents_empty": "Субагентов нет",
    "panel.subagents_empty_hint": "Здесь появятся дочерние запуски, которые агент создаёт через delegate_task.",
    "subagents.label": "Субагент",
    "subagents.untitled": "Поручение без описания",
    "subagents.status.running": "выполняется",
    "subagents.status.ok": "завершён",
    "subagents.status.error": "ошибка",
    "subagents.status.cancelled": "отменён",
    "subagents.status.unknown": "неизвестно",
    "subagents.stop": "Остановить",
    "subagents.stopping": "Отмена отправлена",
    "subagents.tools": "{n} инстр.",
    "subagents.seconds": "с",
    "subagents.ms": "мс",
    "panel.width_aria": "Ширина панели проверки",
    "panel.width_title": "Потяните, чтобы изменить ширину. Двойной клик — сбросить",
    "panel.width_reset": "Ширина панели сброшена",
    "panel.fullscreen_aria": "Развернуть панель на весь экран",
    "panel.collapse": "Свернуть панель",
    "panel.expand": "Развернуть на весь экран",
    "panel.close_aria": "Закрыть панель",
    "panel.close_title": "Закрыть панель",
    "panel.back": "К списку",
    "panel.back_to_files": "К файлам",
    "panel.back_to_diff": "К диффу",
    "panel.refresh": "Обновить",
    "panel.view": "Просмотр",
    "panel.view_title": "Показать содержимое файла",
    "panel.loading": "Загрузка…",
    "panel.session_required": "Сессия не выбрана",
    "panel.session_required_hint": "Откройте сессию слева — панель покажет её изменения, файлы и версии.",
    "panel.diff_title": "Дифф файла",
    "panel.changed_files": "Изменённые файлы",
    "panel.files_totals": "{n} файл(ов) · +{a} −{d}",
    "panel.no_changes": "Изменений нет",
    "panel.no_changes_hint": "Агент ещё не правил файлы в этой сессии — диффы появятся после его правок.",
    "panel.show_diff": "Показать дифф",
    "panel.change.added": "добавлен",
    "panel.change.modified": "изменён",
    "panel.change.deleted": "удалён",
    "panel.diff_source_added": "файл добавлен целиком",
    "panel.diff_source_unavailable": "дифф недоступен",
    "panel.diff_source": "источник: {source}",
    "panel.diff_partial": "показано частично",
    "panel.diff_too_large": "Файл слишком большой для построчного диффа.",
    "panel.diff_no_baseline": "Нет сохранённой базовой версии — построчный дифф недоступен.",
    "panel.diff_no_changes": "Отличий в файле нет.",
    "panel.files_none": "Файлов нет",
    "panel.files_none_hint": "Здесь появятся файлы, которые агент создал или изменил в этой сессии.",
    "panel.files_count": "{n} файл(ов)",
    "panel.file_open": "Показать содержимое",
    "panel.file_view": "смотреть",
    "panel.file_download": "скачать",
    "panel.download_file": "Скачать файл",
    "file.default_name": "файл",
    "viewer.download": "Скачать",
    "viewer.preview_mode": "Превью",
    "viewer.code_mode": "Код",
    "viewer.preview_title": "Показать отрендеренный документ",
    "viewer.code_title": "Показать исходный код",
    "viewer.frame_title": "Предпросмотр {name}",
    "viewer.image_alt": "изображение",
    "viewer.unavailable_title": "Предпросмотр недоступен",
    "viewer.binary_hint": "Файл помечен как двоичный — скачайте его целиком.",
    "viewer.image_too_big": "Изображение слишком большое",
    "viewer.image_limit": "Предпросмотр ограничен {size} — скачайте файл целиком.",
    "viewer.binary_title": "Похоже на двоичный файл",
    "viewer.binary_preview_off": "Предпросмотр отключён — скачайте файл целиком.",
    "viewer.read_failed": "Не удалось прочитать файл ({status})",
    "viewer.error_fallback": "Не удалось прочитать файл",
    "viewer.fallback_hint": "Скачайте файл, чтобы посмотреть его целиком.",
    "viewer.truncated": "Показаны первые {size} — скачайте файл, чтобы увидеть его целиком.",
    "viewer.not_in_attachments": "Файл не появился в списке вложений — доступен только дифф",
    "viewer.language_file": "файл",
    "viewer.language_code": "код",
    "viewer.copy": "копировать",
    "viewer.copied": "скопировано",
    "viewer.copy_unavailable": "недоступно",
    "viewer.reveal": "Открыть в папке",
    "viewer.reveal_title": "Открыть расположение файла в файловом менеджере",
    "viewer.highlight_limit": "Подсветка отключена для файлов больше {size} — исходный код показан как есть.",
    "viewer.sheet_tabs_aria": "Листы книги",
    "viewer.sheet_untitled": "Лист",
    "viewer.sheet_empty": "Лист пуст",
    "viewer.sheet_empty_hint": "На этом листе нет данных для отображения.",
    "viewer.chart_title": "График",
    "viewer.chart_series": "Ряд",
    "viewer.chart_unsupported": "Этот тип диаграммы пока не поддерживается. Откройте скачанный файл в Excel.",
    "viewer.chart_empty": "У графика нет сохранённых числовых данных.",
    "viewer.chart_limit": "Достигнут лимит просмотра: 32 графика, 16 рядов, 1000 точек на ряд; картинки — до 2 МиБ каждая, 8 МиБ суммарно.",
    "viewer.sheet_note": "Оформление ячеек Excel не отображается.",
    "viewer.sheet_truncated": "Показаны первые {rows} строк × {cols} столбцов; остальное скачайте целиком.",
    "viewer.sheet_too_big": "Файл больше {size} — просмотр в браузере отключён, скачайте его целиком.",
    "viewer.sheet_timeout": "Разбор таблицы не уложился в лимит времени, воркер остановлен. Скачайте файл и откройте его в Excel.",
    "viewer.sheet_encrypted": "Файл защищён паролем — разбор невозможен. Скачайте файл и откройте его в Excel.",
    "viewer.sheet_corrupt": "Не удалось разобрать таблицу: файл повреждён или это не Excel. Скачайте его целиком.",
    "viewer.sheet_error": "Ошибка разбора таблицы: {error}",
    "viewer.sheet_unavailable": "Просмотр таблиц недоступен в этом браузере",
    "viewer.sheet_prev": "← Назад",
    "viewer.sheet_next": "Вперёд →",
    "viewer.sheet_page": "Строки {from}–{to} из {total}",
    "reveal.opened": "Открываю в файловом менеджере",
    "reveal.no_display": "Нет графической сессии — файловый менеджер недоступен",
    "reveal.failed": "Не удалось открыть файловый менеджер: {error}",
    "checkpoints.snapshot": "снимок",
    "checkpoints.version": "Версия {short}",
    "checkpoints.files_of_version": "Файлы версии {short} относительно текущих",
    "checkpoints.no_changes": "Отличий нет",
    "checkpoints.no_changes_hint": "В этой версии не было изменений файлов относительно текущего состояния.",
    "checkpoints.count": "{n} верс(ий)",
    "checkpoints.history": "История версий",
    "checkpoints.unavailable": "Чекпоинты недоступны",
    "checkpoints.unavailable_hint": "Для рабочей папки этой сессии история версий не ведётся.",
    "checkpoints.none": "Версий нет",
    "checkpoints.none_hint": "Снимки появятся, когда агент начнёт изменять файлы.",
    "checkpoints.open": "Показать файлы этой версии",
    "checkpoints.more": "Показать ещё",
    "panel.tab.plan": "План",
    "panel.caption.plan": "План агента",
    "plan.progress": "выполнено {done} из {total}",
    "plan.empty": "Плана пока нет",
    "plan.empty_hint": "План появится, когда агент опубликует его через update_plan.",
    "plan.status.completed": "выполнено",
    "plan.status.in_progress": "выполняется",
    "plan.status.pending": "ожидает",
    "plan.context_title": "Контекст",
    "plan.context_input": "Вход",
    "plan.context_output": "Выход",
    "plan.context_rounds": "Раундов",
    "plan.context_last": "Последний запрос",
    "plan.context_latency": "Задержка",
    "plan.context_latency_ms": "{ms} мс",
    "plan.context_window": "Окно контекста",
    "plan.context_used_pct": "{pct}% использовано",
    "plan.context_free_pct": "свободно {pct}%",
    "plan.context_window_note": "Окно контекста модели; проценты — от последнего запроса.",
    "plan.context_bar_aria": "Использование окна контекста: {pct}%",
    "plan.context_note": "Размер окна контекста недоступен из веб-интерфейса — показаны абсолютные значения из событий.",
    "plan.context_empty": "Данных о токенах пока нет.",
    "plan.quota_title": "Квота OpenCode",
    "plan.quota_window_5h": "5 часов",
    "plan.quota_window_week": "Неделя",
    "plan.quota_window_month": "Месяц",
    "plan.quota_bar_aria": "Квота {window}: {pct}%",
    "plan.quota_reset_in": "сброс через {when}",
    "plan.quota_resets_at": "сброс {when}",
    "plan.quota_reset_soon": "сброс наступит с минуты на минуту",
    "plan.quota_rate_limited": "лимит исчерпан",
    "plan.quota_note": "Квота подписки OpenCode Go, с которой работает этот компьютер.",
    "plan.quota_loading": "Запрашиваю квоту…",
    "plan.quota_refresh_aria": "Обновить квоту OpenCode",
    "plan.quota_error_no_key": "Ключ OpenCode не найден на этом компьютере.",
    "plan.quota_error_key_rejected": "Сервис отклонил ключ OpenCode.",
    "plan.quota_error_subscription": "Для квоты нужна подписка OpenCode Go.",
    "plan.quota_error_timeout": "Сервис квоты не ответил вовремя.",
    "plan.quota_error_network": "Не удалось связаться с сервисом квоты.",
    "plan.quota_error_invalid": "Сервис квоты вернул неожиданный ответ.",
    "plan.quota_error_generic": "Квота OpenCode недоступна.",
    "plan.history_truncated": "Просмотрены не все события — суммы могут быть неполными.",
    "dialog.new_session_kicker": "НОВАЯ СЕССИЯ",
    "onboarding.first_task": "Создай в рабочей папке новый файл igorek-welcome.md с коротким приветствием, затем прочитай его и покажи содержимое. Если такой файл уже есть, выбери новое имя; существующие файлы не перезаписывай.",
    "dialog.new_session_title": "Настроим рабочее место",
    "dialog.description": "Выберите профиль агента и папку, с которой он будет работать.",
    "dialog.profile_label": "Профиль агента",
    "dialog.cwd_label": "Рабочая папка",
    "dialog.cwd_placeholder": "/путь/к/проекту",
    "dialog.cwd_toggle_aria": "Показать список рабочих папок",
    "dialog.cwd_options_aria": "Рабочие папки",
    "dialog.cwd_no_matches": "Совпадений нет — можно ввести путь вручную.",
    "dialog.cancel": "Отмена",
    "dialog.create": "Создать",
    "dialog.close": "Закрыть",
    "settings.kicker": "НАСТРОЙКИ",
    "settings.heading": "Управление runtime",
    "settings.card.profile": "Профиль",
    "settings.profile.local": "локально",
    "settings.profile.note": "Имя и аватар хранятся только в этом браузере и подставляются в ваши сообщения.",
    "settings.profile.name_placeholder": "Имя рядом с вашими сообщениями",
    "settings.profile.name_aria": "Отображаемое имя",
    "settings.profile.avatar_set": "Аватар установлен — он заменит букву «{letter}» в ваших сообщениях.",
    "settings.profile.avatar_none": "Пока без фото: в сообщениях остаётся буква «{letter}».",
    "settings.profile.save_name": "Сохранить имя",
    "settings.profile.upload": "Загрузить фото",
    "settings.profile.remove_avatar": "Убрать фото",
    "settings.profile.name_saved": "Имя сохранено",
    "settings.profile.avatar_updated": "Аватар обновлён",
    "settings.profile.avatar_removed": "Аватар убран",
    "settings.profile.storage_full": "В хранилище браузера не хватило места",
    "settings.profile.bad_type": "Подойдёт png, jpg, webp или svg",
    "settings.profile.read_failed": "Не удалось прочитать изображение",
    "settings.profile.too_big": "Файл больше {size}",
    "settings.language.card": "Язык интерфейса",
    "settings.language.state_ru": "выбран русский",
    "settings.language.state_en": "выбран английский",
    "settings.language.note": "Все подписи интерфейса переключаются сразу и сохраняются в этом браузере.",
    "settings.state.on": "включено",
    "settings.state.off": "выключено",
    "settings.approval.card": "Подтверждение команд",
    "settings.approval.note_on": "Опасные действия в агентных сессиях требуют подтверждения.",
    "settings.approval.note_off": "Внимание: опасные действия выполняются без запроса подтверждения.",
    "settings.approval.turn_on": "включить",
    "settings.approval.turn_off": "выключить",
    "settings.approval.enabled": "Подтверждение команд включено",
    "settings.approval.disabled": "Подтверждение команд отключено",
    "settings.security.card": "Уровень доступа",
    "settings.security.state_weakened": "ослаблен",
    "settings.security.state_full": "полный доступ",
    "settings.security.state_baseline": "базовый",
    "settings.security.current": "Текущий уровень: {level}",
    "settings.security.current_weakened": "Текущий уровень: {level} · ослабление действует ограниченное время",
    "settings.security.note": "Усиление применяется сразу; ослабление — только после подтверждения и на ограниченное время.",
    "settings.security.expired": "Срок ослабления истёк — действует базовый уровень.",
    "settings.security.expiry_hours": "осталось ~{h} ч (до {clock})",
    "settings.security.expiry_minutes": "осталось ~{m} мин (до {clock})",
    "settings.security.tag_current": "текущий",
    "settings.security.tag_weaken": "ослабить",
    "settings.security.tag_strengthen": "усилить",
    "settings.security.confirm_full": "Полный доступ отключает защиту: guard выключен, security disabled, агенту доступен полный файловый доступ.",
    "settings.security.confirm_partial": "Часть ограничений будет снята на время действия уровня.",
    "settings.security.confirm_line": "Уровень: {from} → {to}. Ослабление ограничено по времени (по умолчанию 1 час), затем вернётся базовый уровень.",
    "settings.security.sending": "Отправка…",
    "settings.security.confirm": "Подтвердить ослабление",
    "settings.security.updated": "Уровень доступа обновлён",
    "level.strict": "Строгий",
    "level.standard": "Стандартный",
    "level.trusted": "Доверенный",
    "level.full": "Полный доступ",
    "sandbox.card": "Песочница Bash",
    "sandbox.state_active": "активна",
    "sandbox.state_waiting": "ожидает bwrap",
    "sandbox.state_off": "выключена",
    "sandbox.reason_env": "отключена настройкой сервера",
    "sandbox.reason_user": "отключена в настройках",
    "sandbox.reason_bwrap": "bwrap не установлен",
    "sandbox.reason_namespace": "namespaces запрещены",
    "sandbox.reason_observe": "режим наблюдения",
    "sandbox.unknown": "Состояние неизвестно.",
    "sandbox.observe": "Режим наблюдения.",
    "sandbox.unavailable": "Недоступна: {reason}.",
    "sandbox.reason_unknown": "причина неизвестна",
    "sandbox.off": "Отключена: запись не ограничивается песочницей.",
    "sandbox.active": "Активна — запись только в разрешённые папки.",
    "sandbox.warning": "Агент сможет писать за пределы разрешённых папок.",
    "sandbox.disable": "Отключить",
    "sandbox.enabled_toast": "Песочница включена",
    "sandbox.disabled_toast": "Песочница отключена",
    "devices.card": "Устройства",
    "devices.note": "Браузеры и телефоны с доступом к этому runtime. Отозванное устройство теряет доступ, удалённое — исчезает из списка.",
    "devices.purge": "Удалить отозванные ({n})",
    "devices.purge_confirm_one": "Удалить {n} отозванную запись из списка машин? Текущее устройство останется.",
    "devices.purge_confirm_many": "Удалить {n} отозванных записей из списка машин? Текущее устройство останется.",
    "devices.delete_confirm": "Удалить запись «{name}» из списка машин?",
    "devices.delete": "Удалить",
    "devices.name_missing": "Без имени",
    "devices.this_device": "это устройство",
    "devices.revoked": "отозвано",
    "devices.active": "активно",
    "devices.current_chip": "текущее",
    "devices.remove": "удалить",
    "devices.revoke": "отозвать",
    "devices.none": "Устройства не найдены.",
    "devices.revoked_toast": "Устройство отозвано",
    "devices.deleted_toast": "Устройство удалено",
    "devices.purged_toast": "Удалено записей: {n}",
    "skills.card": "Скиллы",
    "skills.note": "Включайте и выключайте локальные skills выбранного профиля.",
    "skills.select_aria": "Профиль для списка скиллов",
    "skills.no_profile": "Профиль не выбран",
    "skills.not_supported": "не для профиля",
    "skills.disable": "Выключить {name}",
    "skills.enable": "Включить {name}",
    "skills.none": "Для выбранного профиля skills не найдены.",
    "mcp.card": "MCP-серверы",
    "mcp.note": "MCP-серверы выбранного профиля. Состояние доступно только для чтения.",
    "mcp.default_name": "MCP-сервер",
    "mcp.capabilities": "возможности: {list}",
    "mcp.tools": "инструменты: {list}",
    "mcp.no_description": "без описания",
    "mcp.enabled": "включён",
    "mcp.disabled": "выключен",
    "mcp.none": "Для выбранного профиля MCP-серверы не найдены.",
    "dashboard.kicker": "ПАНЕЛЬ УПРАВЛЕНИЯ",
    "dashboard.heading": "Состояние интерфейса",
    "dashboard.connection": "Подключение",
    "dashboard.connected": "Подключено",
    "dashboard.disconnected": "Нет подключения",
    "dashboard.device": "Устройство: {name}",
    "dashboard.local_browser": "Локальный браузер",
    "dashboard.connect_hint": "Подключите браузер одноразовым кодом из терминала.",
    "dashboard.runtime": "Runtime",
    "dashboard.bridge_unknown": "адрес bridge неизвестен",
    "dashboard.profile_folder": "Профиль и папка",
    "dashboard.not_selected": "не выбран",
    "dashboard.counters": "Счётчики",
    "dashboard.sessions": "Сессий: {n}",
    "dashboard.devices": "Устройств: {n} · отозвано: {k}",
    "dashboard.devices_none": "устройства: нет данных",
    "dashboard.logs_title": "Журналы systemd-сервисов",
    "dashboard.agents_title": "Импорт сессий OpenCode, Codex и Claude Code",
    "dashboard.copy": "Скопировать сведения",
    "dashboard.copied": "Сведения скопированы",
    "dashboard.clipboard_unavailable": "Буфер обмена недоступен",
    "dashboard.diag_version": "Игорёк web · версия {v}",
    "dashboard.diag_bridge": "Bridge: {url}",
    "dashboard.diag_device": "Устройство: {name}",
    "dashboard.diag_sessions": "Сессии: {n}",
    "dashboard.diag_profile": "Профиль: {p}",
    "dashboard.unknown": "неизвестно",
    "dashboard.local_browser_lower": "локальный браузер",
    "logs.kicker": "ЛОГИ",
    "logs.heading": "Журналы сервисов",
    "logs.tabs_aria": "Сервисы",
    "logs.auto_label": "автообновление",
    "logs.auto_aria": "Автообновление журнала",
    "logs.refresh": "Обновить",
    "logs.output_aria": "Журнал выбранного сервиса",
    "logs.status.active": "включён",
    "logs.status.inactive": "остановлен",
    "logs.status.failed": "ошибка",
    "logs.status.activating": "запускается",
    "logs.status.deactivating": "останавливается",
    "logs.status.reloading": "перезагрузка",
    "logs.status.maintenance": "обслуживание",
    "logs.status.not_found": "не настроен",
    "logs.status.unknown": "неизвестно",
    "logs.tabs_empty": "Список сервисов пока пуст",
    "logs.no_service": "Сервис не выбран",
    "logs.last_lines": "последние {n} строк",
    "logs.older": "есть более старые записи",
    "logs.updating": "обновление…",
    "logs.updated": "обновлено {time}",
    "logs.expand": "Развернуть",
    "logs.collapse": "Свернуть",
    "logs.expand_title": "Развернуть журнал на весь экран",
    "logs.collapse_title": "Вернуть прежний размер журнала",
    "logs.read_failed": "Не удалось прочитать журнал",
    "logs.loading": "Загрузка…",
    "logs.no_records": "Записей нет — сервис ещё ничего не выводил.",
    "logs.started": " · запущен: {value}",
    "agents.kicker": "ИСТОРИЯ АГЕНТОВ",
    "agents.heading": "Внешние сессии",
    "agents.sources_aria": "Источник истории",
    "agents.search_placeholder": "Поиск по истории",
    "agents.refresh": "Сканировать заново",
    "agents.all": "Все",
    "agents.default_source": "Агент",
    "agents.no_date": "без даты",
    "agents.untitled": "Без названия",
    "agents.messages": "{n} сообщ.",
    "agents.open_preview": "Открыть предпросмотр истории",
    "agents.scanning": "Сканируем сохранённые сессии…",
    "agents.no_matches": "Подходящих сессий не найдено",
    "agents.shown": "Показано {shown} из {total} · всего в каталоге {catalog}{scanned}",
    "agents.scanned": " · скан {time}",
    "agents.empty_title": "Сессий не найдено",
    "agents.empty_hint": "Смените источник или запрос, либо нажмите «Сканировать заново».",
    "agents.more": "Показать больше",
    "agents.more_count": "Показать больше ({shown} из {total})",
    "agents.back": "← К списку",
    "agents.importing": "Создаём…",
    "agents.import": "Импортировать",
    "agents.import_note": "Будет создан новый чат Игорька. Исходная сессия останется без изменений.",
    "agents.profile_label": "Профиль",
    "agents.cwd_label": "Рабочая папка",
    "agents.cwd_note": "Сервер проверит исходную папку по правилам изоляции. Если она недоступна, будет использована папка сервера.",
    "agents.cwd_original": "исходная папка: {path}",
    "cwd.denied_home": "Домашнюю папку нельзя использовать как рабочую: KVM-изоляция её не пробрасывает. Выберите папку проекта.",
    "cwd.denied_root": "Корень файловой системы нельзя использовать как рабочую папку. Выберите папку проекта.",
    "cwd.denied_system": "Системный каталог нельзя использовать как рабочую папку. Выберите папку проекта.",
    "agents.import_error_loading": "Дождитесь загрузки истории",
    "agents.import_error_profile": "Выберите профиль для продолжения",
    "agents.import_error_cwd": "Укажите рабочую папку",
    "agents.import_error_session": "Мост не вернул идентификатор новой сессии",
    "agents.imported_new": "Продолжение создано — открываю",
    "agents.imported_fallback": "Исходная папка отклонена ({reason}). Сессия создана в {path}.",
    "agents.imported_duplicate": "Продолжение уже существует — открываю",
    "agents.preview_error": "Не удалось загрузить историю",
    "agents.preview_empty": "История пуста",
    "agents.preview_empty_hint": "В этой внешней сессии нет сообщений для показа.",
    "agents.loaded": "Загружено из истории {source} · {n} сообщ.",
    "agents.meta_messages": "{n} из {total} сообщений",
    "agents.chip.tool_result": "Результат инструмента",
    "agents.chip.web_search": "Поиск в интернете",
    "agents.chip.attachment": "Файл",
    "mention.aria": "Подсказки $ для скиллов и @ для файлов",
    "mention.head_skills": "Скиллы · {profile}",
    "mention.head_files": "Файлы · {path}",
    "mention.empty": "Ничего не найдено",
    "mention.loading": "Загрузка…",
    "mention.error_skills": "Не удалось загрузить скиллы профиля",
    "mention.error_tree": "Не удалось прочитать рабочую папку",
    "mention.no_profile": "Профиль сессии не определён",
    "mention.no_session": "Сначала выберите сессию",
    "mention.skills_limit": "Не больше 5 скиллов в одном сообщении",
    "logout.toast": "Доступ этого браузера отозван"
  },
  en: {
    "compaction.started": "Compacting context",
    "compaction.progress": "Preparing session memory",
    "compaction.completed": "Context compacted",
    "compaction.failed": "Compaction failed — history preserved",
    "compaction.carried": "This memory is sent to the agent with recent turns. Full history is preserved.",
    "compaction.partial": "Intermediate summary. Context changes after a successful save.",

    "app.title": "Igorek — agent workspace",
    "brand.name": "Igorek",
    "brand.caption": "AGENT WORKSPACE",
    "sidebar.open_dashboard": "Open dashboard",
    "sidebar.dashboard_title": "Dashboard",
    "sidebar.close_menu": "Close menu",
    "sidebar.new_session": "New session",
    "sidebar.sessions_label": "YOUR SESSIONS",
    "sidebar.sessions_aria": "Sessions",
    "sidebar.local_runtime": "Local runtime",
    "sidebar.logs": "Logs",
    "sidebar.agents": "Agent history",
    "sidebar.logout": "Disconnect",
    "sidebar.footer": "Running on this computer",
    "sidebar.device_disconnected": "Browser not connected",
    "sidebar.local_browser": "Local browser",
    "topbar.open_menu": "Open menu",
    "topbar.workspace": "Workspace",
    "topbar.select_session": "Select a session",
    "topbar.panel_aria": "Inspector panel",
    "topbar.panel_title": "Changes, files, checkpoints",
    "topbar.settings_aria": "Settings",
    "topbar.settings_title": "Settings",
    "status.waiting": "idle",
    "status.ready": "ready",
    "status.working": "working",
    "status.loading": "loading",
    "status.offline": "offline",
    "status.stopping": "stopping",
    "status.queued": "queued",
    "status.typing": "typing",
    "status.needs_attention": "needs attention",
    "status.reconnecting": "reconnecting",
    "status.check": "check status",
    "status.tool": "tool",
    "status.stopped": "stopped",
    "indicator.working": "Igorek is working",
    "indicator.stopping": "Stopping…",
    "indicator.approval": "Waiting for approval",
    "indicator.isolation": "Starting isolated environment…",
    "task.accepted": "Task accepted",
    "task.isolation_extra": "— starting isolated environment…",
    "task.started": "Igorek is working",
    "task.paused": "Work paused until approval",
    "task.stopped": "Task stopped",
    "task.finished": "Task finished",
    "task.finished_status": "Task finished: {status}",
    "task.finished_error": "Task failed: {error}",
    "task.no_description": "no description",
    "task.unknown_status": "unknown status",
    "task.requeued": "Task requeued",
    "task.requeued_steer": "Continuing — new messages delivered",
    "task.requeued_orphan": "Task restarted with queued messages",
    "task.requeued_approval": "Continuing after approval…",
    "duration.seconds": "{n}s",
    "duration.minutes": "{m}m",
    "duration.minutes_seconds": "{m}m {s}s",
    "duration.hours": "{h}h",
    "duration.hours_minutes": "{h}h {r}m",
    "bytes.b": "B",
    "bytes.kb": "KB",
    "bytes.mb": "MB",
    "session.no_messages": "no messages yet",
    "session.empty_list": "Your work sessions will appear here.",
    "session.default_title": "New session",
    "session.profile_fallback": "profile",
    "session.rename": "Rename",
    "session.rename_aria": "Rename session “{title}”",
    "session.rename_input_aria": "New session title",
    "session.renamed": "Title updated",
    "session.cwd_missing": "no working folder set",
    "connect.eyebrow": "YOUR LOCAL AGENT",
    "connect.title_lead": "A workspace",
    "connect.title_accent": "for Igorek",
    "connect.lead": "Connect this browser to your runtime. Chats and keys stay on your computer.",
    "connect.step1_title": "Open a terminal",
    "connect.step1_hint": "On the computer where Igorek runs",
    "connect.step2_title": "Create a pairing code",
    "connect.code_label": "One-time code",
    "connect.button": "Connect",
    "connect.note": "The code is time-limited and single-use. You can revoke access from the interface itself.",
    "connect.orbit_local": "LOCAL",
    "connect.orbit_tags": "AGENT · TOOLS · EVENTS",
    "connect.paired": "Browser connected to the local runtime",
    "welcome.eyebrow": "AGENT SESSIONS",
    "welcome.title_lead": "Pick up",
    "welcome.title_accent": "where we left off?",
    "welcome.lead": "Choose a session on the left or start a new one. Tool calls, results and progress will appear here as they happen.",
    "welcome.button": "Create session",
    "welcome.meta1": "ONE INTERFACE",
    "welcome.meta2": "YOUR LOCAL RUNTIME",
    "composer.rail_aria": "Navigate your messages",
    "composer.attach_aria": "Attach file",
    "demo.start": "Meet Igorek · /demo",
    "demo.separate": "Send /demo on its own, without attachments.",
    "navigation.unavailable": "This section is currently unavailable. Open it from the application menu.",
    "composer.placeholder": "Write a task for Igorek…",
    "composer.input_aria": "Message for Igorek",
    "composer.hint_text": " — send",
    "composer.stop": "Stop",
    "composer.send": "Send",
    "composer.send_aria": "Send message",
    "composer.caption": "Local session · messages are processed by the selected profile",
    "composer.queued": "queued",
    "composer.force_send": "Send now",
    "composer.force_send_title": "Deliver this message into the running task immediately, without waiting for it to finish",
    "composer.cancel_queued": "Cancel",
    "composer.cancel_queued_title": "Remove this message from the task queue",
    "composer.prioritized": "sending…",
    "composer.model_title": "Session model: {model}",
    "composer.profile_title": "Session profile: {profile}",
    "composer.mic_start": "Voice input",
    "composer.mic_stop": "Stop voice input",
    "composer.mic_error": "Voice input: {error}",
    "composer.mic_denied": "Microphone access is denied",
    "composer.mic_unavailable": "Speech recognition is unavailable",
    "voice.button_aria": "Voice call",
    "voice.button_title": "Voice call",
    "voice.panel_aria": "Voice call panel",
    "voice.resize_aria": "Resize the call panel",
    "voice.error.key_setup": "Calls require a separate OpenAI API key. Open Settings → Voice model and save the key: https://api.openai.com/v1, OpenAI format, realtime model gpt-realtime-2.1-mini. Then press Check connection there. Do not start its worker; tasks stay on the current profile.",
    "voice.error.unavailable": "Voice mode is unavailable. Check the connection and server settings.",
    "voice.title": "Voice call",
    "voice.minimize_aria": "Collapse the call panel",
    "voice.restore_aria": "Expand the call panel",
    "voice.hangup_aria": "End the call",
    "voice.user_label": "You",
    "voice.assistant_label": "Igorek",
    "voice.system_label": "System",
    "voice.mic_aria": "Microphone",
    "voice.mic_label": "Microphone",
    "voice.speaker_aria": "Sound",
    "voice.speaker_label": "Sound",
    "voice.status.idle": "Ready to call",
    "voice.status.connecting": "Connecting…",
    "voice.status.listening": "Listening",
    "voice.status.thinking": "Thinking",
    "voice.status.reconnecting": "Reconnecting…",
    "voice.status.failed": "Response failed",
    "voice.status.error": "Call error",
    "voice.status.mic_off": "Microphone is off",
    "voice.status.mic_on": "Microphone is on",
    "voice.status.speaker_off": "Sound is off",
    "voice.status.speaker_on": "Sound is on",
    "voice.error.no_session": "Select a session first",
    "voice.error.mic_denied": "Microphone access is denied",
    "voice.error.mic_missing": "No microphone found",
    "voice.error.expired": "The call session expired — please try again",
    "voice.error.start": "Could not start the call: {error}",
    "voice.error.audio": "Could not play audio — press “Sound”",
    "voice.error.task": "Could not submit the voice task",
    "voice.error.approval": "Could not send the decision",
    "voice.error.realtime": "Voice server error",
    "voice.error.reconnect": "Could not reconnect",
    "voice.task_running": "Task {task} is running…",
    "voice.task_done": "Task completed",
    "voice.task_failed": "Task failed",
    "voice.task_failed_reason": "Task failed: {error}",
    "voice.result_prefix": "Task result: ",
    "voice.approval_title": "Approval required",
    "composer.mode_aria": "Run mode",
    "composer.mode_plan": "Plan",
    "composer.mode_execute": "Execute",
    "composer.mode_plan_note": "Planning mode — the agent only plans and performs no actions",
    "composer.mode_hint": " — mode",
    "message.you": "You",
    "message.user_letter": "Y",
    "message.copy": "copy",
    "message.copied": "copied",
    "message.copy_unavailable": "unavailable",
    "message.copy_aria": "Copy message text",
    "rail.tick": "Message {n}{suffix}",
    "rail.preview_fallback": "Message",
    "tool.default_name": "Tool",
    "tool.status.running": "running",
    "tool.status.done": "done",
    "tool.status.error": "error",
    "tool.status.exit": "exit code",
    "tool.status.stopped": "stopped",
    "tool.input": "INPUT",
    "tool.result": "RESULT",
    "tool.result_ms": "RESULT · {ms} MS",
    "tool.open_details": "Open details",
    "tool.params": "{n} parameter(s)",
    "reasoning.title": "Reasoning",
    "reasoning.title_live": "Reasoning · now",
    "approval.waiting": "Awaiting approval: {tool}",
    "approval.decided": "Decision recorded: {tool}",
    "approval.default_tool": "agent action",
    "approval.default_reason": "Review the request in the approvals center.",
    "approval.resolved_allow_once": "allowed once",
    "approval.resolved_allow_session": "allowed for the session",
    "approval.resolved_reject": "rejected",
    "approval.resolved_cancel": "cancelled",
    "approval.btn_allow": "Allow",
    "approval.btn_allow_session": "Always allow",
    "approval.btn_reject": "Reject",
    "approval.sent": "Decision sent",
    "error.connection_closed": "Browser connection closed",
    "error.request_failed": "Request failed ({status})",
    "attachments.preview_aria": "View {name}",
    "attachments.loading": "uploading…",
    "attachments.error": "error",
    "attachments.remove_aria": "Remove attachment “{name}”",
    "attachments.empty": "File “{name}” is empty",
    "attachments.limit": "No more than {n} attachments per message",
    "attachments.upload_failed": "Failed to upload “{name}”: {error}",
    "attachments.send_failed": "Not sent — check the attachments: {list}",
    "panel.aria": "Inspector panel",
    "panel.kicker": "INSPECT",
    "panel.data": "Session data",
    "panel.tabs_aria": "Inspector panel sections",
    "panel.tab.changes": "Changes",
    "panel.tab.files": "Files",
    "panel.tab.checkpoints": "Checkpoints",
    "panel.tab.subagents": "Agents",
    "panel.tab.terminal": "Terminal",
    "panel.caption.terminal": "Session terminal",
    "terminal.title": "Terminal",
    "terminal.hint": "An interactive shell inside the session working folder.",
    "terminal.connecting": "Connecting the terminal…",
    "terminal.connected": "Terminal connected — host, without isolation",
    "terminal.ready": "Start the shell using the Connect button",
    "terminal.connect": "Connect",
    "terminal.stop": "Close terminal",
    "terminal.connected_kvm": "Terminal connected — current session KVM",
    "terminal.reason.kvm_terminal_unavailable": "KVM terminal unavailable: guest service and VM owner bridge required. Host shell is disabled for this session.",
    "terminal.reason.host_terminal_disabled": "Host terminal disabled by server configuration",
    "terminal.exited": "The shell exited (code {code})",
    "terminal.restart": "Restart the shell",
    "terminal.open": "Open terminal",
    "terminal.error.ticket": "Could not get terminal access: {reason}",
    "terminal.error.connect": "Could not connect to the terminal: {reason}",
    "terminal.error.unavailable": "The terminal is available only while the local web interface is open on this computer.",
    "terminal.reason.ticket_invalid": "the terminal ticket is invalid or already used",
    "terminal.reason.ticket_expired": "the terminal ticket expired",
    "terminal.reason.ticket_session_mismatch": "the ticket belongs to another session",
    "terminal.reason.terminal_busy": "another tab is already attached to this terminal",
    "terminal.reason.terminal_limit": "the limit of open terminals is reached",
    "terminal.reason.terminal_exists": "this session's terminal is already open",
    "terminal.reason.ghostty_missing": "the shell is unavailable",
    "terminal.aria": "Terminal",
    "terminal.shell_aria": "Terminal area",
    "panel.caption.changes": "File changes",
    "panel.caption.files": "Session files",
    "panel.caption.checkpoints": "Version history",
    "panel.caption.subagents": "Session subagents",
    "panel.subagents_title": "Subagents",
    "panel.subagents_empty": "No subagents",
    "panel.subagents_empty_hint": "Child runs created by the agent via delegate_task will appear here.",
    "subagents.label": "Subagent",
    "subagents.untitled": "Untitled task",
    "subagents.status.running": "running",
    "subagents.status.ok": "completed",
    "subagents.status.error": "error",
    "subagents.status.cancelled": "cancelled",
    "subagents.status.unknown": "unknown",
    "subagents.stop": "Stop",
    "subagents.stopping": "Cancellation sent",
    "subagents.tools": "{n} tools",
    "subagents.seconds": "s",
    "subagents.ms": "ms",
    "panel.width_aria": "Inspector panel width",
    "panel.width_title": "Drag to resize. Double-click to reset",
    "panel.width_reset": "Panel width reset",
    "panel.fullscreen_aria": "Expand the panel to full screen",
    "panel.collapse": "Collapse the panel",
    "panel.expand": "Expand to full screen",
    "panel.close_aria": "Close the panel",
    "panel.close_title": "Close the panel",
    "panel.back": "Back to list",
    "panel.back_to_files": "Back to files",
    "panel.back_to_diff": "Back to diff",
    "panel.refresh": "Refresh",
    "panel.view": "View",
    "panel.view_title": "Show the file contents",
    "panel.loading": "Loading…",
    "panel.session_required": "No session selected",
    "panel.session_required_hint": "Open a session on the left — the panel will show its changes, files and versions.",
    "panel.diff_title": "File diff",
    "panel.changed_files": "Changed files",
    "panel.files_totals": "{n} files · +{a} −{d}",
    "panel.no_changes": "No changes",
    "panel.no_changes_hint": "The agent has not edited any files in this session yet — diffs will appear after its edits.",
    "panel.show_diff": "Show diff",
    "panel.change.added": "added",
    "panel.change.modified": "modified",
    "panel.change.deleted": "deleted",
    "panel.diff_source_added": "file added entirely",
    "panel.diff_source_unavailable": "diff unavailable",
    "panel.diff_source": "source: {source}",
    "panel.diff_partial": "shown partially",
    "panel.diff_too_large": "The file is too large for a line-by-line diff.",
    "panel.diff_no_baseline": "No saved baseline — a line-by-line diff is unavailable.",
    "panel.diff_no_changes": "No differences in the file.",
    "panel.files_none": "No files",
    "panel.files_none_hint": "Files the agent created or modified in this session will appear here.",
    "panel.files_count": "{n} files",
    "panel.file_open": "Show the contents",
    "panel.file_view": "view",
    "panel.file_download": "download",
    "panel.download_file": "Download file",
    "file.default_name": "file",
    "viewer.download": "Download",
    "viewer.preview_mode": "Preview",
    "viewer.code_mode": "Code",
    "viewer.preview_title": "Show the rendered document",
    "viewer.code_title": "Show the source code",
    "viewer.frame_title": "Preview {name}",
    "viewer.image_alt": "image",
    "viewer.unavailable_title": "Preview unavailable",
    "viewer.binary_hint": "The file is marked as binary — download it to view it.",
    "viewer.image_too_big": "The image is too large",
    "viewer.image_limit": "The preview is limited to {size} — download the file to see it in full.",
    "viewer.binary_title": "Looks like a binary file",
    "viewer.binary_preview_off": "The preview is off — download the file to view it.",
    "viewer.read_failed": "Could not read the file ({status})",
    "viewer.error_fallback": "Could not read the file",
    "viewer.fallback_hint": "Download the file to view it in full.",
    "viewer.truncated": "Showing the first {size} — download the file to see it in full.",
    "viewer.not_in_attachments": "The file did not appear in the attachments list — only the diff is available",
    "viewer.language_file": "file",
    "viewer.language_code": "code",
    "viewer.copy": "copy",
    "viewer.copied": "copied",
    "viewer.copy_unavailable": "unavailable",
    "viewer.reveal": "Open in folder",
    "viewer.reveal_title": "Reveal the file in the desktop file manager",
    "viewer.highlight_limit": "Highlighting is off for files larger than {size} — the source is shown as-is.",
    "viewer.sheet_tabs_aria": "Workbook sheets",
    "viewer.sheet_untitled": "Sheet",
    "viewer.sheet_empty": "Sheet is empty",
    "viewer.sheet_empty_hint": "This sheet has no data to show.",
    "viewer.chart_title": "Chart",
    "viewer.chart_series": "Series",
    "viewer.chart_unsupported": "This chart type is not supported yet. Download and open the file in Excel.",
    "viewer.chart_empty": "The chart has no saved numeric data.",
    "viewer.chart_limit": "Preview limit reached: 32 charts, 16 series, 1000 points per series; images up to 2 MiB each, 8 MiB total.",
    "viewer.sheet_note": "Excel cell styling is not shown.",
    "viewer.sheet_truncated": "Showing the first {rows} rows × {cols} columns; download the file for the rest.",
    "viewer.sheet_too_big": "The file is larger than {size} — in-browser preview is off, download it instead.",
    "viewer.sheet_timeout": "Parsing hit the time limit and the worker was stopped. Download the file and open it in Excel.",
    "viewer.sheet_encrypted": "The file is password-protected — it cannot be parsed. Download it and open it in Excel.",
    "viewer.sheet_corrupt": "Could not parse the spreadsheet: the file is damaged or is not Excel. Download it instead.",
    "viewer.sheet_error": "Spreadsheet parsing failed: {error}",
    "viewer.sheet_unavailable": "Spreadsheet preview is unavailable in this browser",
    "viewer.sheet_prev": "← Previous",
    "viewer.sheet_next": "Next →",
    "viewer.sheet_page": "Rows {from}–{to} of {total}",
    "reveal.opened": "Opening in the file manager",
    "reveal.no_display": "No graphical session — the file manager is unavailable",
    "reveal.failed": "Could not open the file manager: {error}",
    "checkpoints.snapshot": "snapshot",
    "checkpoints.version": "Version {short}",
    "checkpoints.files_of_version": "Files in version {short} compared to the current state",
    "checkpoints.no_changes": "No differences",
    "checkpoints.no_changes_hint": "This version had no file changes compared to the current state.",
    "checkpoints.count": "{n} versions",
    "checkpoints.history": "Version history",
    "checkpoints.unavailable": "Checkpoints unavailable",
    "checkpoints.unavailable_hint": "Version history is not kept for this session working folder.",
    "checkpoints.none": "No versions",
    "checkpoints.none_hint": "Snapshots will appear once the agent starts editing files.",
    "checkpoints.open": "Show the files of this version",
    "checkpoints.more": "Show more",
    "panel.tab.plan": "Plan",
    "panel.caption.plan": "Agent plan",
    "plan.progress": "{done} of {total} done",
    "plan.empty": "No plan yet",
    "plan.empty_hint": "The plan will appear once the agent publishes it via update_plan.",
    "plan.status.completed": "completed",
    "plan.status.in_progress": "in progress",
    "plan.status.pending": "pending",
    "plan.context_title": "Context",
    "plan.context_input": "Input",
    "plan.context_output": "Output",
    "plan.context_rounds": "Rounds",
    "plan.context_last": "Last request",
    "plan.context_latency": "Latency",
    "plan.context_latency_ms": "{ms} ms",
    "plan.context_window": "Context window",
    "plan.context_used_pct": "{pct}% used",
    "plan.context_free_pct": "{pct}% free",
    "plan.context_window_note": "Model context window; percentages use the last request.",
    "plan.context_bar_aria": "Context window usage: {pct}%",
    "plan.context_note": "The context window is not exposed to the web interface — absolute numbers from events are shown.",
    "plan.context_empty": "No token data yet.",
    "plan.quota_title": "OpenCode quota",
    "plan.quota_window_5h": "5 hours",
    "plan.quota_window_week": "Week",
    "plan.quota_window_month": "Month",
    "plan.quota_bar_aria": "Quota {window}: {pct}%",
    "plan.quota_reset_in": "resets in {when}",
    "plan.quota_resets_at": "resets {when}",
    "plan.quota_reset_soon": "resets any minute now",
    "plan.quota_rate_limited": "rate limited",
    "plan.quota_note": "OpenCode Go subscription quota used by this computer.",
    "plan.quota_loading": "Loading quota…",
    "plan.quota_refresh_aria": "Refresh the OpenCode quota",
    "plan.quota_error_no_key": "No OpenCode key was found on this computer.",
    "plan.quota_error_key_rejected": "The service rejected the OpenCode key.",
    "plan.quota_error_subscription": "An OpenCode Go subscription is required for the quota.",
    "plan.quota_error_timeout": "The quota service did not answer in time.",
    "plan.quota_error_network": "Could not reach the quota service.",
    "plan.quota_error_invalid": "The quota service returned an unexpected response.",
    "plan.quota_error_generic": "OpenCode quota is unavailable.",
    "plan.history_truncated": "Not all events were scanned — totals may be incomplete.",
    "dialog.new_session_kicker": "NEW SESSION",
    "onboarding.first_task": "Create a new igorek-welcome.md file in the working folder with a short greeting, then read it and show its contents. If the file already exists, choose another name; do not overwrite existing files.",
    "dialog.new_session_title": "Set up the workspace",
    "dialog.description": "Choose the agent profile and the folder it will work in.",
    "dialog.profile_label": "Agent profile",
    "dialog.cwd_label": "Working folder",
    "dialog.cwd_placeholder": "/path/to/project",
    "dialog.cwd_toggle_aria": "Show the working folders",
    "dialog.cwd_options_aria": "Working folders",
    "dialog.cwd_no_matches": "No matches — you can type the path manually.",
    "dialog.cancel": "Cancel",
    "dialog.create": "Create",
    "dialog.close": "Close",
    "settings.kicker": "SETTINGS",
    "settings.heading": "Runtime management",
    "settings.card.profile": "Profile",
    "settings.profile.local": "local",
    "settings.profile.note": "The name and avatar are stored only in this browser and shown next to your messages.",
    "settings.profile.name_placeholder": "Name shown next to your messages",
    "settings.profile.name_aria": "Display name",
    "settings.profile.avatar_set": "Avatar set — it replaces the letter “{letter}” next to your messages.",
    "settings.profile.avatar_none": "No photo yet: messages keep the letter “{letter}”.",
    "settings.profile.save_name": "Save name",
    "settings.profile.upload": "Upload photo",
    "settings.profile.remove_avatar": "Remove photo",
    "settings.profile.name_saved": "Name saved",
    "settings.profile.avatar_updated": "Avatar updated",
    "settings.profile.avatar_removed": "Avatar removed",
    "settings.profile.storage_full": "Browser storage is full",
    "settings.profile.bad_type": "Use png, jpg, webp or svg",
    "settings.profile.read_failed": "Could not read the image",
    "settings.profile.too_big": "The file is larger than {size}",
    "settings.language.card": "Interface language",
    "settings.language.state_ru": "Russian selected",
    "settings.language.state_en": "English selected",
    "settings.language.note": "All interface labels switch immediately and are stored in this browser.",
    "settings.state.on": "enabled",
    "settings.state.off": "disabled",
    "settings.approval.card": "Command approval",
    "settings.approval.note_on": "Dangerous actions in agent sessions require approval.",
    "settings.approval.note_off": "Warning: dangerous actions run without asking for approval.",
    "settings.approval.turn_on": "turn on",
    "settings.approval.turn_off": "turn off",
    "settings.approval.enabled": "Command approval enabled",
    "settings.approval.disabled": "Command approval disabled",
    "settings.security.card": "Access level",
    "settings.security.state_weakened": "weakened",
    "settings.security.state_full": "full access",
    "settings.security.state_baseline": "baseline",
    "settings.security.current": "Current level: {level}",
    "settings.security.current_weakened": "Current level: {level} · the weakening is time-limited",
    "settings.security.note": "Strengthening applies immediately; weakening requires confirmation and is time-limited.",
    "settings.security.expired": "The weakening expired — the baseline level is active.",
    "settings.security.expiry_hours": "~{h} h left (until {clock})",
    "settings.security.expiry_minutes": "~{m} min left (until {clock})",
    "settings.security.tag_current": "current",
    "settings.security.tag_weaken": "weaken",
    "settings.security.tag_strengthen": "strengthen",
    "settings.security.confirm_full": "Full access disables protection: guard off, security disabled, full filesystem access for the agent.",
    "settings.security.confirm_partial": "Some restrictions will be lifted for the duration of the level.",
    "settings.security.confirm_line": "Level: {from} → {to}. The weakening is time-limited (1 hour by default), then the baseline level returns.",
    "settings.security.sending": "Sending…",
    "settings.security.confirm": "Confirm weakening",
    "settings.security.updated": "Access level updated",
    "level.strict": "Strict",
    "level.standard": "Standard",
    "level.trusted": "Trusted",
    "level.full": "Full access",
    "sandbox.card": "Bash sandbox",
    "sandbox.state_active": "active",
    "sandbox.state_waiting": "waiting for bwrap",
    "sandbox.state_off": "off",
    "sandbox.reason_env": "disabled by the server setting",
    "sandbox.reason_user": "disabled in settings",
    "sandbox.reason_bwrap": "bwrap is not installed",
    "sandbox.reason_namespace": "namespaces are blocked",
    "sandbox.reason_observe": "observe mode",
    "sandbox.unknown": "Status unknown.",
    "sandbox.observe": "Observe mode.",
    "sandbox.unavailable": "Unavailable: {reason}.",
    "sandbox.reason_unknown": "unknown reason",
    "sandbox.off": "Off: writes are not sandboxed.",
    "sandbox.active": "Active — writes go only to allowed folders.",
    "sandbox.warning": "The agent will be able to write outside allowed folders.",
    "sandbox.disable": "Disable",
    "sandbox.enabled_toast": "Sandbox enabled",
    "sandbox.disabled_toast": "Sandbox disabled",
    "devices.card": "Devices",
    "devices.note": "Browsers and phones with access to this runtime. A revoked device loses access; a deleted one disappears from the list.",
    "devices.purge": "Delete revoked ({n})",
    "devices.purge_confirm_one": "Delete {n} revoked record from the device list? The current device will stay.",
    "devices.purge_confirm_many": "Delete {n} revoked records from the device list? The current device will stay.",
    "devices.delete_confirm": "Delete “{name}” from the device list?",
    "devices.delete": "Delete",
    "devices.name_missing": "Unnamed",
    "devices.this_device": "this device",
    "devices.revoked": "revoked",
    "devices.active": "active",
    "devices.current_chip": "current",
    "devices.remove": "delete",
    "devices.revoke": "revoke",
    "devices.none": "No devices found.",
    "devices.revoked_toast": "Device revoked",
    "devices.deleted_toast": "Device deleted",
    "devices.purged_toast": "Records deleted: {n}",
    "skills.card": "Skills",
    "skills.note": "Enable or disable local skills of the selected profile.",
    "skills.select_aria": "Profile for the skills list",
    "skills.no_profile": "No profile selected",
    "skills.not_supported": "not for this profile",
    "skills.disable": "Disable {name}",
    "skills.enable": "Enable {name}",
    "skills.none": "No skills found for the selected profile.",
    "mcp.card": "MCP servers",
    "mcp.note": "MCP servers of the selected profile. The status is read-only.",
    "mcp.default_name": "MCP server",
    "mcp.capabilities": "capabilities: {list}",
    "mcp.tools": "tools: {list}",
    "mcp.no_description": "no description",
    "mcp.enabled": "enabled",
    "mcp.disabled": "disabled",
    "mcp.none": "No MCP servers found for the selected profile.",
    "dashboard.kicker": "DASHBOARD",
    "dashboard.heading": "Interface status",
    "dashboard.connection": "Connection",
    "dashboard.connected": "Connected",
    "dashboard.disconnected": "Not connected",
    "dashboard.device": "Device: {name}",
    "dashboard.local_browser": "Local browser",
    "dashboard.connect_hint": "Connect the browser with a one-time code from the terminal.",
    "dashboard.runtime": "Runtime",
    "dashboard.bridge_unknown": "bridge address unknown",
    "dashboard.profile_folder": "Profile and folder",
    "dashboard.not_selected": "not selected",
    "dashboard.counters": "Counters",
    "dashboard.sessions": "Sessions: {n}",
    "dashboard.devices": "Devices: {n} · revoked: {k}",
    "dashboard.devices_none": "devices: no data",
    "dashboard.logs_title": "Systemd service logs",
    "dashboard.agents_title": "Import sessions from OpenCode, Codex and Claude Code",
    "dashboard.copy": "Copy diagnostics",
    "dashboard.copied": "Diagnostics copied",
    "dashboard.clipboard_unavailable": "Clipboard unavailable",
    "dashboard.diag_version": "Igorek web · version {v}",
    "dashboard.diag_bridge": "Bridge: {url}",
    "dashboard.diag_device": "Device: {name}",
    "dashboard.diag_sessions": "Sessions: {n}",
    "dashboard.diag_profile": "Profile: {p}",
    "dashboard.unknown": "unknown",
    "dashboard.local_browser_lower": "local browser",
    "logs.kicker": "LOGS",
    "logs.heading": "Service logs",
    "logs.tabs_aria": "Services",
    "logs.auto_label": "auto-refresh",
    "logs.auto_aria": "Auto-refresh the log",
    "logs.refresh": "Refresh",
    "logs.output_aria": "Log of the selected service",
    "logs.status.active": "active",
    "logs.status.inactive": "inactive",
    "logs.status.failed": "failed",
    "logs.status.activating": "activating",
    "logs.status.deactivating": "deactivating",
    "logs.status.reloading": "reloading",
    "logs.status.maintenance": "maintenance",
    "logs.status.not_found": "not configured",
    "logs.status.unknown": "unknown",
    "logs.tabs_empty": "The service list is empty so far",
    "logs.no_service": "No service selected",
    "logs.last_lines": "last {n} lines",
    "logs.older": "older entries exist",
    "logs.updating": "updating…",
    "logs.updated": "updated {time}",
    "logs.expand": "Expand",
    "logs.collapse": "Collapse",
    "logs.expand_title": "Expand the log to full screen",
    "logs.collapse_title": "Restore the previous log size",
    "logs.read_failed": "Could not read the log",
    "logs.loading": "Loading…",
    "logs.no_records": "No entries — the service has not logged anything yet.",
    "logs.started": " · started: {value}",
    "agents.kicker": "AGENT HISTORY",
    "agents.heading": "External sessions",
    "agents.sources_aria": "History source",
    "agents.search_placeholder": "Search history",
    "agents.refresh": "Scan again",
    "agents.all": "All",
    "agents.default_source": "Agent",
    "agents.no_date": "no date",
    "agents.untitled": "Untitled",
    "agents.messages": "{n} messages",
    "agents.open_preview": "Open the history preview",
    "agents.scanning": "Scanning saved sessions…",
    "agents.no_matches": "No matching sessions",
    "agents.shown": "Showing {shown} of {total} · {catalog} in the catalog{scanned}",
    "agents.scanned": " · scanned {time}",
    "agents.empty_title": "No sessions found",
    "agents.empty_hint": "Change the source or query, or press “Scan again”.",
    "agents.more": "Show more",
    "agents.more_count": "Show more ({shown} of {total})",
    "agents.back": "← Back to list",
    "agents.importing": "Creating…",
    "agents.import": "Import",
    "agents.import_note": "A new Igorek chat will be created. The original session stays untouched.",
    "agents.profile_label": "Profile",
    "agents.cwd_label": "Working folder",
    "agents.cwd_note": "The server checks the source folder against isolation rules. If unavailable, it uses the server folder.",
    "agents.cwd_original": "original folder: {path}",
    "cwd.denied_home": "The home folder cannot be used as the working folder: KVM isolation does not share it. Choose a project folder.",
    "cwd.denied_root": "The filesystem root cannot be used as the working folder. Choose a project folder.",
    "cwd.denied_system": "A system directory cannot be used as the working folder. Choose a project folder.",
    "agents.import_error_loading": "Wait for the history to load",
    "agents.import_error_profile": "Select a profile to continue",
    "agents.import_error_cwd": "Specify a working folder",
    "agents.import_error_session": "The bridge did not return the new session id",
    "agents.imported_new": "Continuation created — opening",
    "agents.imported_fallback": "The source folder was rejected ({reason}). The session was created in {path}.",
    "agents.imported_duplicate": "Continuation already exists — opening",
    "agents.preview_error": "Could not load the history",
    "agents.preview_empty": "History is empty",
    "agents.preview_empty_hint": "This external session has no messages to show.",
    "agents.loaded": "Loaded from {source} history · {n} messages",
    "agents.meta_messages": "{n} of {total} messages",
    "agents.chip.tool_result": "Tool result",
    "agents.chip.web_search": "Web search",
    "agents.chip.attachment": "File",
    "mention.aria": "Type $ for skills and @ for files",
    "mention.head_skills": "Skills · {profile}",
    "mention.head_files": "Files · {path}",
    "mention.empty": "Nothing found",
    "mention.loading": "Loading…",
    "mention.error_skills": "Could not load the profile skills",
    "mention.error_tree": "Could not read the working folder",
    "mention.no_profile": "The session profile is unknown",
    "mention.no_session": "Select a session first",
    "mention.skills_limit": "No more than 5 skills per message",
    "logout.toast": "This browser's access has been revoked"
  }
};

function readStoredLang() {
  const stored = storageRead(LANG_STORAGE_KEY);
  return SUPPORTED_LANGS.includes(stored) ? stored : DEFAULT_LANG;
}

function t(key, params) {
  const table = I18N[lang] || I18N[DEFAULT_LANG];
  let text = table[key];
  if (text === undefined) text = I18N[DEFAULT_LANG][key];
  if (text === undefined) return key;
  if (params) {
    for (const name of Object.keys(params)) text = text.split("{" + name + "}").join(String(params[name]));
  }
  return text;
}

// Reference to a key inside a string parameter: its value is translated during rendering (e.g., «Task completed: {status}»).
const keyRef = (key) => ({ i18nKey: key });
function localizeParam(value) {
  return value && typeof value === "object" && value.i18nKey ? t(value.i18nKey) : value;
}
function statusLineText(item) {
  if (!item || !item.key) return (item && item.text) || "";
  const params = {};
  for (const [name, value] of Object.entries(item.params || {})) params[name] = localizeParam(value);
  let text = t(item.key, params);
  if (item.extraKey) text += " " + t(item.extraKey);
  if (item.suffix) text += item.suffix;
  return text;
}
function intlLocale() {
  return lang === "en" ? "en" : "ru";
}

function applyStaticI18n() {
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll("[data-i18n-title]")) node.title = t(node.dataset.i18nTitle);
  for (const node of document.querySelectorAll("[data-i18n-aria-label]")) {
    node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
  }
  for (const node of document.querySelectorAll("[data-i18n-placeholder]")) node.placeholder = t(node.dataset.i18nPlaceholder);
  document.documentElement.lang = lang;
}

function renderRuntime() {
  ui.runtime.classList.toggle("active", Boolean(state.runtimeActive));
  ui.runtimeLabel.textContent = t(state.runtimeKey || "status.waiting");
  if (ui.stop) ui.stop.hidden = !state.runtimeActive;
}
function setRuntime(active, key) {
  state.runtimeActive = Boolean(active);
  if (key) state.runtimeKey = key;
  renderRuntime();
}
function renderIndicator() {
  if (ui.indicatorLabel) ui.indicatorLabel.textContent = t(state.indicatorKey || "indicator.working");
}
function setIndicator(key) {
  if (key) state.indicatorKey = key;
  renderIndicator();
}
function renderDeviceName() {
  const custom = state.device && state.device.name ? String(state.device.name) : "";
  ui.deviceName.textContent = custom || t(state.paired ? "sidebar.local_browser" : "sidebar.device_disconnected");
}

// Redraws everything currently visible in the selected language. Called once during loading (after initialize) and on every language change.
function syncLanguage() {
  document.documentElement.lang = lang;
  applyStaticI18n();
  renderRuntime();
  renderIndicator();
  renderDeviceName();
  if (!state.session) ui.title.textContent = t("topbar.select_session");
  else if (state.session.meta) {
    ui.title.textContent = state.session.meta.title || state.session.meta.first_user_message || t("session.default_title");
  }
  renderSessions();
  renderContext(state.session);
  if(state.workspaceView==="scheduled"){state.mainTitle=ui.title.textContent;ui.title.textContent="Запланировано";}
  else if(state.workspaceView==='plugins'){state.mainTitle=ui.title.textContent;ui.title.textContent='Плагины';}
  renderAttachments();
  renderMicButton();
  state.rail.signature = "";
  renderTimeline();
  syncComposerAccess();
  if (ui.settingsDialog.open) renderSettings();
  if (ui.dashboardDialog.open) renderDashboard();
  if (ui.logsDialog.open) renderLogs();
  if (ui.agentsDialog.open) renderAgents();
  renderPanel();
  if (logsAutoSwitch) logsAutoSwitch.setAttribute("aria-label", t("logs.auto_aria"));
  if (logsExpandButton) {
    const size = logsSizeModel();
    logsExpandButton.textContent = size.expanded ? t("logs.collapse") : t("logs.expand");
    logsExpandButton.title = size.expanded ? t("logs.collapse_title") : t("logs.expand_title");
  }
}

function setLanguage(next) {
  const value = SUPPORTED_LANGS.includes(next) ? next : DEFAULT_LANG;
  lang = value;
  storageWrite(LANG_STORAGE_KEY, lang);
  syncLanguage();
}

function renderLanguageSettings() {
  const card = drawerCard(
    t("settings.language.card"),
    lang === "en" ? t("settings.language.state_en") : t("settings.language.state_ru"),
    "on",
  );
  card.append(element("p", "settings-note", t("settings.language.note")));
  const choices = element("div", "settings-actions");
  for (const code of SUPPORTED_LANGS) {
    const active = code === lang;
    const button = element("button", "mini-button" + (active ? " active" : ""), code === "en" ? "English" : "Русский");
    button.type = "button";
    button.lang = code;
    button.setAttribute("aria-pressed", String(active));
    button.disabled = active;
    button.addEventListener("click", () => setLanguage(code));
    choices.append(button);
  }
  card.append(choices);
  return card;
}

// Browser-local appearance state (profile, logs window size). Stored only in
// localStorage: there is no bridge endpoint for it, and every read is guarded
// so corrupted or foreign values fall back to defaults.
const PROFILE_STORAGE_KEY = "igorek-web:profile";
const LOGS_SIZE_STORAGE_KEY = "igorek-web:logs-size";
const MODE_STORAGE_KEY = "igorek-web:mode";
const MODE_PLAN = "plan";
const MODE_EXECUTE = "execute";
let composerMode = MODE_EXECUTE;
const PROFILE_AVATAR_LIMIT = 2 * 1024 * 1024;
const PROFILE_AVATAR_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/svg+xml"]);
const LOGS_SIZE_MIN = { width: 340, height: 260 };

function storageRead(key) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    return null;
  }
}
function storageWrite(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (error) {
    return false;
  }
}
function storageRemove(key) {
  try {
    window.localStorage.removeItem(key);
    return true;
  } catch (error) {
    return false;
  }
}
function readStoredMode() {
  return storageRead(MODE_STORAGE_KEY) === MODE_PLAN ? MODE_PLAN : MODE_EXECUTE;
}
function planModeActive() {
  return composerMode === MODE_PLAN;
}
function renderComposerMode() {
  const plan = planModeActive();
  ui.composer.classList.toggle("plan-mode", plan);
  ui.modePlan.classList.toggle("active", plan);
  ui.modePlan.setAttribute("aria-pressed", String(plan));
  ui.modeExecute.classList.toggle("active", !plan);
  ui.modeExecute.setAttribute("aria-pressed", String(!plan));
  ui.modeNote.hidden = !plan;
}
function setComposerMode(next) {
  composerMode = next === MODE_PLAN ? MODE_PLAN : MODE_EXECUTE;
  storageWrite(MODE_STORAGE_KEY, composerMode);
  renderComposerMode();
}
function toggleComposerMode() {
  setComposerMode(planModeActive() ? MODE_EXECUTE : MODE_PLAN);
  ui.message.focus();
}
function profileModel() {
  if (!state.profile) {
    const stored = storageRead(PROFILE_STORAGE_KEY);
    const source = stored && typeof stored === "object" ? stored : {};
    state.profile = {
      name: String(source.name || "").trim().slice(0, 60),
      avatar: typeof source.avatar === "string" && source.avatar.startsWith("data:image/") ? source.avatar : "",
    };
  }
  return state.profile;
}
function saveProfile(next) {
  const stored = storageWrite(PROFILE_STORAGE_KEY, next);
  if (!stored) return false;
  state.profile = next;
  if (ui.settingsDialog.open) renderSettings();
  renderTimeline();
  return true;
}
function handleProfileAvatar(file) {
  if (!file) return;
  if (file.size > PROFILE_AVATAR_LIMIT) {
    toast(t("settings.profile.too_big", { size: formatBytes(PROFILE_AVATAR_LIMIT) }));
    return;
  }
  const type = String(file.type || "").toLowerCase();
  if (!PROFILE_AVATAR_TYPES.has(type)) {
    toast(t("settings.profile.bad_type"));
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const result = typeof reader.result === "string" ? reader.result : "";
    if (!result.startsWith("data:image/")) {
      toast(t("settings.profile.read_failed"));
      return;
    }
    if (!saveProfile({ ...profileModel(), avatar: result })) {
      toast(t("settings.profile.storage_full"));
      return;
    }
    toast(t("settings.profile.avatar_updated"));
  };
  reader.onerror = () => toast(t("settings.profile.read_failed"));
  reader.readAsDataURL(file);
}
function logsSizeModel() {
  if (!state.logsSize) {
    const stored = storageRead(LOGS_SIZE_STORAGE_KEY);
    const source = stored && typeof stored === "object" ? stored : {};
    const width = Number(source.width);
    const height = Number(source.height);
    state.logsSize = {
      width: Number.isFinite(width) ? width : 0,
      height: Number.isFinite(height) ? height : 0,
      expanded: Boolean(source.expanded),
    };
  }
  return state.logsSize;
}
function clampLogsSize(width, height) {
  const maxWidth = Math.max(LOGS_SIZE_MIN.width, window.innerWidth - 16);
  const maxHeight = Math.max(LOGS_SIZE_MIN.height, window.innerHeight - 16);
  return {
    width: Math.min(Math.max(Math.round(width), LOGS_SIZE_MIN.width), maxWidth),
    height: Math.min(Math.max(Math.round(height), LOGS_SIZE_MIN.height), maxHeight),
  };
}
function applyLogsSize() {
  const model = logsSizeModel();
  ui.logsDialog.classList.toggle("logs-expanded", model.expanded);
  if (model.expanded) {
    ui.logsDialog.style.width = "calc(100vw - 16px)";
    ui.logsDialog.style.height = "calc(100vh - 16px)";
    return;
  }
  const size = model.width && model.height ? clampLogsSize(model.width, model.height) : null;
  ui.logsDialog.style.width = size ? size.width + "px" : "";
  ui.logsDialog.style.height = size ? size.height + "px" : "";
}
function persistLogsSize() {
  const model = logsSizeModel();
  if (!ui.logsDialog.open || model.expanded) return;
  const rect = ui.logsDialog.getBoundingClientRect();
  if (rect.width < 120 || rect.height < 120) return;
  Object.assign(model, clampLogsSize(rect.width, rect.height));
  storageWrite(LOGS_SIZE_STORAGE_KEY, model);
}
let logsResizeTimer = 0;
function toggleLogsExpanded() {
  const model = logsSizeModel();
  if (!model.expanded) persistLogsSize();
  model.expanded = !model.expanded;
  storageWrite(LOGS_SIZE_STORAGE_KEY, model);
  applyLogsSize();
  renderLogs();
}

const PANEL_WIDTH_STORAGE_KEY = "igorek-web:panel-width";
const PANEL_WIDTH_MIN = 300;
const PANEL_WIDTH_DEFAULT = 372;
const PANEL_WIDTH_DEFAULT_MEDIUM = 330;
const PANEL_CHAT_MIN = 340;

function panelWidthDefault() {
  return window.innerWidth <= 900 ? PANEL_WIDTH_DEFAULT_MEDIUM : PANEL_WIDTH_DEFAULT;
}
function panelWidthMax() {
  const viewportLimit = Math.round(window.innerWidth * 0.7);
  const chatLimit = Math.round(window.innerWidth - PANEL_CHAT_MIN);
  return Math.max(PANEL_WIDTH_MIN, Math.min(viewportLimit, chatLimit));
}
function panelWidthStored() {
  const value = Number(storageRead(PANEL_WIDTH_STORAGE_KEY));
  return Number.isFinite(value) && value > 0 ? value : 0;
}
function panelClampWidth(width) {
  return Math.min(Math.max(Math.round(width), PANEL_WIDTH_MIN), panelWidthMax());
}
function panelWidthEffective() {
  const stored = panelWidthStored();
  return stored ? panelClampWidth(stored) : panelWidthDefault();
}
function panelWidthCurrent() {
  const current = Number.parseFloat(ui.sidePanel.style.getPropertyValue("--panel-width"));
  return Number.isFinite(current) && current > 0 ? current : panelWidthEffective();
}
function applyPanelWidth(width) {
  const value = panelClampWidth(width);
  ui.sidePanel.style.setProperty("--panel-width", value + "px");
  if (ui.panelResize) {
    ui.panelResize.setAttribute("aria-valuenow", String(value));
    ui.panelResize.setAttribute("aria-valuemax", String(panelWidthMax()));
  }
  return value;
}
function resetPanelWidth() {
  storageRemove(PANEL_WIDTH_STORAGE_KEY);
  applyPanelWidth(panelWidthDefault());
  scheduleRailLayout();
  toast(t("panel.width_reset"));
}
function persistPanelWidth() {
  storageWrite(PANEL_WIDTH_STORAGE_KEY, applyPanelWidth(panelWidthCurrent()));
}
function nudgePanelWidth(delta) {
  storageWrite(PANEL_WIDTH_STORAGE_KEY, applyPanelWidth(panelWidthCurrent() + delta));
  scheduleRailLayout();
}
function startPanelResize(event) {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  if (!state.panel.open || state.panel.fullscreen) return;
  event.preventDefault();
  const handle = ui.panelResize;
  const startX = event.clientX;
  const startWidth = panelWidthCurrent();
  try {
    handle.setPointerCapture(event.pointerId);
  } catch (error) {
    // Some environments do not allow pointer capture; in such cases, the gesture is handled by the same grip listeners.
  }
  document.body.classList.add("panel-resizing");
  const move = (moveEvent) => {
    applyPanelWidth(startWidth + (startX - moveEvent.clientX));
    scheduleRailLayout();
  };
  const finish = (endEvent) => {
    handle.removeEventListener("pointermove", move);
    handle.removeEventListener("pointerup", finish);
    handle.removeEventListener("pointercancel", finish);
    document.body.classList.remove("panel-resizing");
    try {
      handle.releasePointerCapture(endEvent.pointerId);
    } catch (error) {
      // Capture has already been released by the environment; this is not an error.
    }
    persistPanelWidth();
    scheduleRailLayout();
  };
  handle.addEventListener("pointermove", move);
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
}
function handlePanelResizeKeydown(event) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  event.preventDefault();
  nudgePanelWidth(event.key === "ArrowLeft" ? 24 : -24);
}
function handleViewportResize() {
  const stored = panelWidthStored();
  if (stored) {
    const clamped = applyPanelWidth(stored);
    if (clamped !== Math.round(stored)) storageWrite(PANEL_WIDTH_STORAGE_KEY, clamped);
  } else {
    applyPanelWidth(panelWidthDefault());
  }
  scheduleRailLayout();
}

function desktopNotification(source, detail, level = 'error') {
  window.IgorekNotifications?.add({ source, detail: String(detail || ''), level }).catch(() => {});
}
async function api(path, options = {}) {
  const { notify = true, ...fetchOptions } = options;
  const response = await fetch(path, {
    credentials: "same-origin",
    ...fetchOptions,
    headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    showConnection();
    desktopNotification('Подключение', payload.error || t("error.connection_closed"));
    throw Object.assign(new Error(payload.error || t("error.connection_closed")),{code:"connection_closed",status:401});
  }
  if (!response.ok) {
    const error = new Error(payload.error || t("error.request_failed", { status: response.status }));
    error.status = response.status;
    if (payload.code) error.code = String(payload.code);
    if (notify) desktopNotification('Запрос приложения', error.message + (error.code ? ' · ' + error.code : ''));
    throw error;
  }
  return payload;
}

function toast(message, level = 'info') {
  desktopNotification(level === 'error' ? 'Ошибка приложения' : 'Приложение', message, level);
  ui.toast.textContent = message;
  ui.toast.classList.add("visible");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => ui.toast.classList.remove("visible"), 3300);
}
function showConnection() {
  window.IgorekAppHistory?.reset();
  window.IgorekReviews?.hide({forget:true});
  pendingReviewSession=null;
  document.body.classList.remove("gh-review-mode");
  byId("nav-reviews").disabled=true;
  window.IgorekPlugins?.hide();
  byId("nav-plugins").disabled = true;
  window.clearTimeout(state.schedulesTimer);
  state.scheduleGeneration=(state.scheduleGeneration||0)+1; state.schedulesData=null;
  byId("schedules-list").replaceChildren(); closeScheduleDialog();
  byId("nav-scheduled").disabled = true;
  byId("scheduled-view").hidden = true;
  document.querySelector(".workspace-row").hidden = false;
  state.stream?.close();
  state.stream = null;
  stopMic();
  closePanel();
  ui.connect.hidden = false;
  ui.workspace.hidden = true;
  ui.logout.hidden = true;
  ui.openLogs.hidden = true;
  ui.openAgents.hidden = true;
  ui.settingsButton.hidden = true;
  ui.panelButton.hidden = true;
  state.paired = false;
  state.device = null;
  renderDeviceName();
  if (ui.settingsDialog.open) ui.settingsDialog.close();
  if (ui.dashboardDialog.open) ui.dashboardDialog.close();
  if (ui.logsDialog.open) ui.logsDialog.close();
  if (ui.agentsDialog.open) ui.agentsDialog.close();
  stopLogsAuto();
  setRuntime(false, "status.waiting");
}
function showWorkspace(device) {
  byId("nav-reviews").disabled=!window.IgorekDesktop?.github;
  ui.connect.hidden = true;
  ui.workspace.hidden = false;
  ui.logout.hidden = false;
  ui.openLogs.hidden = false;
  ui.openAgents.hidden = false;
  ui.settingsButton.hidden = false;
  ui.panelButton.hidden = false;
  state.paired = true;
  byId("nav-scheduled").disabled = false;
  byId("nav-plugins").disabled = false;
  state.device = device || null;
  renderDeviceName();
}
function shortDate(timestamp) {
  const value = Number(timestamp || 0);
  if (!value) return t("session.no_messages");
  const date = new Date(value < 10000000000 ? value * 1000 : value);
  return new Intl.DateTimeFormat(intlLocale(), { day: "numeric", month: "short" }).format(date);
}
function projectKey(session) {
  const cwd = String(session.cwd || "");
  return cwd ? cwd.replace(/\/+$/, "") || "/" : "";
}
function projectName(path) { return path ? path.split("/").filter(Boolean).pop() || "/" : "Без проекта"; }
function sidebarSessionRow(session, compact = false) {
  const row = element("div", "session-item" + (state.selected === session.session_id ? " active" : ""));
  row.dataset.sessionId = session.session_id;
  const main = element("button", "session-main"); main.type = "button";
  main.setAttribute("aria-current", state.selected === session.session_id ? "page" : "false");
  const icon = element("span", "session-icon", compact ? "⌖" : "◈");
  const copy = element("span", "session-copy");
  copy.append(element("span", "session-title", session.title || t("session.default_title")));
  copy.append(element("span", "session-meta", (compact ? projectName(projectKey(session)) : session.profile || t("session.profile_fallback")) + " · " + shortDate(session.updated_at)));
  main.append(icon, copy); main.addEventListener("click", () => selectSession(session.session_id)); row.append(main);
  if (session.active) row.append(element("span", "session-live"));
  const pin = element("button", "session-edit session-pin" + (session.pinned ? " is-pinned" : ""), session.pinned ? "★" : "☆");
  pin.type = "button"; pin.title = session.pinned ? "Открепить чат" : "Закрепить чат";
  pin.setAttribute("aria-label", pin.title + ": " + (session.title || t("session.default_title"))); pin.setAttribute("aria-pressed", String(Boolean(session.pinned)));
  pin.addEventListener("click", event => { event.stopPropagation(); toggleSessionPin(session.session_id, !session.pinned, pin); }); row.append(pin);
  const edit = element("button", "session-edit", "✎"); edit.type = "button"; edit.title = t("session.rename");
  edit.setAttribute("aria-label", t("session.rename_aria", {title:session.title || t("session.default_title")})); edit.addEventListener("click",()=>startRename(copy,session)); row.append(edit);
  return row;
}
async function toggleSessionPin(sessionId, pinned, button) {
  if (button) button.disabled = true;
  try {
    const result = await api("/api/sessions/" + encode(sessionId), {method:"PATCH",body:JSON.stringify({pinned})});
    if (result.pinned !== pinned) throw Error("Сервер не подтвердил закрепление чата");
    const session = state.sessions.find(item=>item.session_id===sessionId); if (session) session.pinned = pinned;
    renderSessions();
  } catch (error) { toast(error.message, "error"); if (button) button.disabled = false; }
}
function renderSessions() {
  ui.sessions.replaceChildren();
  ui.sessionCount.textContent = String(state.sessionTotal == null ? state.sessions.length : state.sessionTotal);
  if (!state.sessions.length) { ui.sessions.append(element("p","session-list-empty",t("session.empty_list"))); return; }
  const pinned = state.sessions.filter(session=>session.pinned === true);
  if (pinned.length) {
    ui.sessions.append(element("div","project-section-label","Закреплённые"));
    for (const session of pinned) ui.sessions.append(sidebarSessionRow(session,true));
  }
  ui.sessions.append(element("div","project-section-label","Проекты"));
  const groups = new Map();
  for (const session of state.sessions) { const key=projectKey(session); if (!groups.has(key)) groups.set(key,[]); groups.get(key).push(session); }
  const sorted = [...groups].sort((a,b)=>Math.max(...b[1].map(s=>Number(s.updated_at||0)))-Math.max(...a[1].map(s=>Number(s.updated_at||0))));
  for (const [key,sessions] of sorted) {
    const group=element("section","session-project"); group.dataset.project=key;
    const header=element("div","project-heading"), toggle=element("button","project-toggle");toggle.type="button";toggle.title=key||"Рабочая папка не сохранена";
    const collapsed=state.projectCollapsed?.has(key)===true;
    toggle.setAttribute("aria-expanded",String(!collapsed)); toggle.append(element("span","project-chevron",collapsed?"›":"⌄"),element("span","project-folder-icon","▱"),element("span","project-name",projectName(key)),element("span","project-count",String(sessions.length)));
    toggle.addEventListener("click",()=>{state.projectCollapsed??=new Set();if(state.projectCollapsed.has(key))state.projectCollapsed.delete(key);else state.projectCollapsed.add(key);renderSessions();});header.append(toggle);
    if(key){const add=element("button","project-add","+");add.type="button";add.title="Новый чат в "+key;add.setAttribute("aria-label",add.title);add.addEventListener("click",()=>{openNewSession();sessionCwdCombo.setValue(key);});header.append(add);}
    group.append(header);
    if(!collapsed){
      const shown=state.projectShown?.get(key)||5;
      for(const session of sessions.slice(0,shown))group.append(sidebarSessionRow(session));
      if(sessions.length>shown){const more=element("button","project-more","Показать ещё "+Math.min(10,sessions.length-shown));more.type="button";more.addEventListener("click",()=>{state.projectShown??=new Map();state.projectShown.set(key,shown+10);renderSessions();});group.append(more);}
    }
    ui.sessions.append(group);
  }
  if(Number(state.sessionTotal)>state.sessions.length&&(state.sessionLimit||100)<5000){const more=element("button","project-more sessions-load-more","Загрузить более старые чаты");more.type="button";more.addEventListener("click",()=>{more.disabled=true;state.sessionLimit=Math.min(5000,(state.sessionLimit||100)+100);loadSessions().catch(error=>{toast(error.message, "error");more.disabled=false;});});ui.sessions.append(more);}
}
function startRename(copy, session) {
  const title = copy.querySelector(".session-title");
  if (!title) return;
  const input = element("input", "session-rename-input");
  input.type = "text";
  input.value = session.title || "";
  input.maxLength = 160;
  input.setAttribute("aria-label", t("session.rename_input_aria"));
  const main = copy.closest(".session-main");
  if (main?.tagName === "BUTTON") {
    const editor = element("div", "session-main");
    editor.append(...main.childNodes);
    main.replaceWith(editor);
  }
  title.replaceWith(input);
  input.focus();
  input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    if (save) renameSession(session.session_id, input.value);
    else renderSessions();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finish(true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      finish(false);
    }
  });
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("click", (event) => event.stopPropagation());
}
async function renameSession(sessionId, value) {
  const title = String(value || "").trim().slice(0, 160);
  const session = state.sessions.find((item) => item.session_id === sessionId);
  if (!title || (session && session.title === title)) {
    renderSessions();
    return;
  }
  try {
    const result = await api("/api/sessions/" + encode(sessionId), {
      method: "PATCH", body: JSON.stringify({ title }),
    });
    const updated = String((result && result.title) || title);
    if (session) session.title = updated;
    if (state.selected === sessionId) { if(["scheduled","plugins"].includes(state.workspaceView))state.mainTitle=updated;else ui.title.textContent=updated; }
    toast(t("session.renamed"));
  } catch (error) {
    toast(error.message, "error");
  }
  renderSessions();
}
async function loadSessions() {
  const result = await api("/api/sessions" + (state.sessionLimit ? "?limit=" + state.sessionLimit : ""));
  state.sessions = Array.isArray(result.sessions) ? result.sessions : [];
  const total = Number(result.total);
  // The bridge returns `total` as the number of sessions up to the `limit`; the list remains capped.
  // If the field did not arrive (old bridge), the counter drops to the length of the list.
  state.sessionTotal = Number.isFinite(total) && total >= 0 ? total : null;
  renderSessions();
}
let appNavigationTimer=null;
function scheduleAppNavigation(){
  clearTimeout(appNavigationTimer);
  appNavigationTimer=setTimeout(()=>window.IgorekAppHistory?.record(appNavigationRoute()),0);
}
function appNavigationRoute(){
  if(!state.paired)return null;
  const view=['scheduled','reviews','plugins'].includes(state.workspaceView)?state.workspaceView:'main';
  if(view==='main'&&!state.selected)return null;
  if(state.selected&&state.session?.session_id!==state.selected)return null;
  return {view,session:state.selected||'',panel:state.panel.open,tab:state.panel.open?state.panel.workspace.active?.id||'':'',pull:view==='reviews'?window.IgorekReviews?.location?.()||'':''};
}
function showMainView() {
  window.IgorekReviews?.hide();
  document.body.classList.remove("gh-review-mode");
  byId("nav-reviews").classList.remove("active");
  byId("nav-reviews").removeAttribute("aria-current");
  ui.workspace.hidden = !state.paired;
  window.IgorekPlugins?.hide();
  state.workspaceView = "main";
  window.clearTimeout(state.schedulesTimer);
  byId("scheduled-view").hidden = true;
  document.querySelector(".workspace-row").hidden = false;
  byId("nav-home").classList.add("active"); byId("nav-home").setAttribute("aria-current","page");
  byId("nav-scheduled").classList.remove("active"); byId("nav-scheduled").removeAttribute("aria-current");
  if (state.mainTitle) ui.title.textContent = state.mainTitle;
  ui.panelButton.hidden = !state.paired;
  window.IgorekAppHistory?.schedule();
}
async function showScheduledView() {
  if (!state.paired) return;
  if (document.body.classList.contains("gh-review-mode")) showMainView();
  if (state.workspaceView === 'plugins') showMainView();
  window.IgorekPlugins?.hide();
  if (state.workspaceView !== "scheduled") state.mainTitle = ui.title.textContent;
  state.workspaceView = "scheduled";
  window.IgorekAppHistory?.schedule();
  byId("scheduled-view").hidden = false; document.querySelector(".workspace-row").hidden = true;
  byId("nav-home").classList.remove("active"); byId("nav-home").removeAttribute("aria-current");
  byId("nav-scheduled").classList.add("active"); byId("nav-scheduled").setAttribute("aria-current","page");
  ui.title.textContent = "Запланировано"; ui.panelButton.hidden = true;
  document.body.classList.remove("sidebar-open");
  await loadSchedules();
}
function scheduleDate(value, timezone) {
  if (!value) return "—";
  try { return new Intl.DateTimeFormat(intlLocale(),{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit",timeZone:timezone||"Europe/Moscow"}).format(new Date(Number(value)*1000)); }
  catch { return "—"; }
}
function renderSchedules() {
  const list=byId("schedules-list"); list.replaceChildren();
  const model=state.schedulesData||{schedules:[],available:true};
  byId("schedule-new").disabled = model.available===false || Boolean(state.schedulesBusy);
  byId("schedules-status").textContent = model.available===false ? "Расписание доступно в запущенном desktop." : "Заданий: "+model.schedules.length+" · "+(model.running?"Планировщик работает":"Планировщик остановлен");
  if (!model.schedules.length) { list.append(element("div","schedule-empty",model.available===false?"Открой приложение Игорёк для управления заданиями.":"Пока нет запланированных заданий. Создай первое — оно появится здесь.")); return; }
  for (const row of model.schedules) {
    const card=element("article","schedule-card");card.dataset.scheduleId=row.schedule_id;
    const top=element("div","schedule-card-head"),title=element("h2","",row.name);
    const active=["queued","processing","waiting_approval","cancelling","retry"].includes(row.last_status);
    const done=row.schedule_type==="once"&&Number(row.run_count)>0&&!row.next_run_at;
    const status=active?(row.last_status==="queued"?"В очереди":row.last_status==="waiting_approval"?"Нужно подтверждение":"Выполняется"):!row.enabled?"Пауза":done?"Выполнено":!row.ready?"Ждёт исполнителя":!row.next_run_at?"Нет следующего запуска":"Запланировано";
    top.append(title,element("span","schedule-state"+(active?" is-running":""),status));card.append(top);
    card.append(element("p","schedule-payload",row.payload||""));
    const meta=element("div","schedule-meta");
    const hours=Number(row.interval_hours||1);
    meta.append(element("span","",(row.schedule_type==="hourly"?(hours===1?"Каждый час":"Каждые "+hours+" ч"):row.schedule_type==="daily"?"Каждый день":row.schedule_type==="once"?"Один раз":"Cron")+" · "+(row.schedule_type==="hourly"&&hours===1?":"+String(row.time_value||"00:00").slice(-2):(row.time_value||row.cron_expression||"—"))+" · "+(row.timezone||"UTC")),element("span","",row.profile||""));
    const directory=element("span","schedule-directory",row.cwd||"Без проекта");directory.title=row.cwd||"";meta.append(directory);card.append(meta);
    const lastStatus={ok:"выполнено",fail:"ошибка",cancelled:"отменено",timeout:"истекло время",queued:"в очереди",processing:"в работе",waiting_approval:"нужно подтверждение",dead_letter:"не выполнено"}[row.last_status]||row.last_status||"нет запусков";
    card.append(element("p","schedule-next","Следующий: "+scheduleDate(row.next_run_at,row.timezone)+" · Последний: "+lastStatus+" · Запусков: "+Number(row.run_count||0)));
    const deliveryLabel={delivered:"Доставлено в исходный чат",skipped:"Новых материалов нет · без сообщения",missing_result:"Запуск не выбрал результат доставки",delivery_failed:"Ошибка доставки · смотри рабочий запуск",failed:"Фоновая проверка завершилась ошибкой"}[row.delivery_status];
    if(deliveryLabel)card.append(element("p","schedule-next",deliveryLabel));
    const actions=element("div","schedule-actions");
    for(const [label,action] of [["Изменить","edit"],[row.enabled?"Пауза":"Возобновить","enabled"],["Выполнить сейчас","run"],["Удалить","delete"]]){
      const button=element("button","secondary-button"+(action==="delete"?" schedule-delete":""),label);button.type="button";
      button.disabled=Boolean(state.schedulesBusy)||(action==="run"&&!row.ready)||(action==="edit"&&!['daily','once','hourly'].includes(row.schedule_type));
      button.addEventListener("click",()=>action==="edit"?openScheduleDialog(row):scheduleAction(row,action));actions.append(button);
    }
    if(row.last_session_id){const open=element("button","secondary-button schedule-open-result","Открыть чат последнего запуска");open.type="button";open.addEventListener("click",()=>selectSession(row.last_session_id));actions.append(open);}
    card.append(actions);list.append(card);
  }
}
async function loadSchedules() {
  if(state.workspaceView!=="scheduled"||state.schedulesBusy)return;
  if(byId("schedule-dialog").open){window.clearTimeout(state.schedulesTimer);state.schedulesTimer=window.setTimeout(()=>loadSchedules(),10000);return;}
  const generation=state.scheduleGeneration=(state.scheduleGeneration||0)+1;
  byId("schedules-error").textContent="";
  try { const result=await api("/api/schedules");if(generation!==state.scheduleGeneration)return;state.schedulesData=result;renderSchedules(); }
  catch(error){if(generation===state.scheduleGeneration)byId("schedules-error").textContent=error.message;}
  finally {window.clearTimeout(state.schedulesTimer);if(state.workspaceView==="scheduled"&&state.paired)state.schedulesTimer=window.setTimeout(()=>loadSchedules(),10000);}
}
async function scheduleAction(row,action) {
  if(state.schedulesBusy)return;
  if(action==="delete"&&!window.confirm("Удалить расписание «"+row.name+"»? Уже запущенное задание продолжит работу."))return;
  state.schedulesBusy=true;const generation=state.scheduleGeneration=(state.scheduleGeneration||0)+1;renderSchedules();
  try {
    const target="/api/schedules/"+encode(row.schedule_id)+(action==="run"?"/run":"");
    const result=await api(target,{method:action==="delete"?"DELETE":action==="enabled"?"PATCH":"POST",...(action==="delete"?{}:{body:JSON.stringify(action==="enabled"?{enabled:!row.enabled}:{})})});
    if(generation!==state.scheduleGeneration||!state.paired)return;
    state.schedulesData=result;byId("schedules-error").textContent="";await loadSessions();
  }catch(error){byId("schedules-error").textContent=error.message;}
  finally{state.schedulesBusy=false;renderSchedules();if(state.workspaceView==="scheduled")loadSchedules();}
}
function closeScheduleDialog(){state.scheduleEditorGeneration=(state.scheduleEditorGeneration||0)+1;if(byId("schedule-dialog").open)byId("schedule-dialog").close();byId("schedule-payload").value="";state.scheduleOptions=null;}
function updateScheduleInterval() {
  const hourly=byId("schedule-type").value==="hourly",input=byId("schedule-interval-hours"),hours=Number(input.value);
  byId("schedule-interval-field").hidden=!hourly;input.disabled=!hourly;input.required=hourly;
  byId("schedule-time-label").textContent=hourly?(hours===1?"Минута запуска":"Первый запуск"):"Время";
  byId("schedule-interval-note").hidden=!hourly;
  byId("schedule-interval-note").textContent=hours===1?"Каждый час в выбранную минуту; значение часа не учитывается.":"Первый запуск — в выбранное время сегодня или завтра. Далее — через указанное число часов. Пропущенные запуски не накапливаются.";
}
async function openScheduleDialog(row=null) {
  const dialog=byId("schedule-dialog");if(dialog.open||state.schedulesBusy)return;
  const generation=state.scheduleEditorGeneration=(state.scheduleEditorGeneration||0)+1;
  state.scheduleEditing=row?.schedule_id||"";byId("schedule-error").textContent="";
  byId("schedule-dialog-title").textContent=row?"Изменить задание":"Новое задание";
  byId("schedule-name").value=row?.name||"";byId("schedule-payload").value=row?.payload||"";
  byId("schedule-type").value=row?.schedule_type||"daily";byId("schedule-time").value=row?.time_value||"09:00";
  byId("schedule-interval-hours").value=String(row?.interval_hours||1);updateScheduleInterval();
  byId("schedule-timezone").value=row?.timezone||"Europe/Moscow";
  byId("schedule-cwd").value=row?.cwd||state.session?.meta?.cwd||ui.cwd.value||"";
  byId("schedule-profile").replaceChildren();byId("schedule-save").disabled=true;dialog.showModal();
  try{
    const options=await api("/api/session-options");if(!dialog.open||generation!==state.scheduleEditorGeneration||!state.paired)return;state.scheduleOptions=options;
    for(const profile of options.profiles||[]){const option=document.createElement("option");option.value=profile;option.textContent=profile;byId("schedule-profile").append(option);}
    if(row?.profile&&!(options.profiles||[]).includes(row.profile)){const option=document.createElement("option");option.value=row.profile;option.textContent=row.profile+" (исполнитель остановлен)";byId("schedule-profile").append(option);}
    byId("schedule-profile").value=row?.profile||options.default_profile||(options.profiles||[])[0]||"";
    if(!byId("schedule-cwd").value)byId("schedule-cwd").value=options.default_cwd||"";
    byId("schedule-workdirs").replaceChildren();for(const path of options.workdirs||[]){const option=document.createElement("option");option.value=path;byId("schedule-workdirs").append(option);}
    byId("schedule-save").disabled=!(options.profiles||[]).length;
    if(!(options.profiles||[]).length)byId("schedule-error").textContent="Сначала запусти нужный исполнитель в настройках приложения.";
  }catch(error){if(dialog.open)byId("schedule-error").textContent=error.message;}
}
byId("nav-home").addEventListener("click",showMainView);
byId("nav-scheduled").addEventListener("click",()=>showScheduledView());
byId("schedules-refresh").addEventListener("click",()=>loadSchedules());
byId("schedule-new").addEventListener("click",()=>openScheduleDialog());
byId("schedule-type").addEventListener("change",updateScheduleInterval);
byId("schedule-interval-hours").addEventListener("input",updateScheduleInterval);
for(const id of ["schedule-close","schedule-cancel"])byId(id).addEventListener("click",closeScheduleDialog);
byId("schedule-dialog").addEventListener("close",()=>{state.scheduleEditorGeneration=(state.scheduleEditorGeneration||0)+1;byId("schedule-payload").value="";state.scheduleOptions=null;});
byId("schedule-form").addEventListener("submit",async event=>{
  event.preventDefault();const button=byId("schedule-save");if(button.disabled)return;
  const fields={name:byId("schedule-name").value,payload:byId("schedule-payload").value,profile:byId("schedule-profile").value,cwd:byId("schedule-cwd").value,schedule_type:byId("schedule-type").value,time_value:byId("schedule-time").value,timezone:byId("schedule-timezone").value};
  if(fields.schedule_type==="hourly")fields.interval_hours=Number(byId("schedule-interval-hours").value);
  if(!(state.scheduleOptions?.profiles||[]).includes(fields.profile)){byId("schedule-error").textContent="Запусти исполнитель выбранного профиля.";return;}
  button.disabled=true;state.schedulesBusy=true;const editorGeneration=state.scheduleEditorGeneration;const generation=state.scheduleGeneration=(state.scheduleGeneration||0)+1;
  try{const result=await api("/api/schedules"+(state.scheduleEditing?"/"+encode(state.scheduleEditing):""),{method:state.scheduleEditing?"PATCH":"POST",body:JSON.stringify(fields)});if(generation!==state.scheduleGeneration||editorGeneration!==state.scheduleEditorGeneration||!state.paired||!byId("schedule-dialog").open)return;state.schedulesData=result;closeScheduleDialog();renderSchedules();}
  catch(error){byId("schedule-error").textContent=error.message;}
  finally{button.disabled=false;state.schedulesBusy=false;if(state.workspaceView==="scheduled")loadSchedules();}
});

async function loadOptions() {
  const generation = state.optionsGeneration = (state.optionsGeneration || 0) + 1;
  const button = byId("create-session-button");
  button.disabled = true;
  let loaded = false;
  try {
    const result = await api("/api/session-options");
    if(window.IgorekDesktop?.agent){
      const catalog=await window.IgorekDesktop.agent('catalog');
      if(!catalog.error)result.profiles=[...new Set([...(result.profiles||[]),...(catalog.profiles||[])])];
    }
    if (generation !== state.optionsGeneration) return;
    const selected = ui.profile.value;
    const profiles = result.profiles || [];
    ui.profile.replaceChildren();
    for (const name of profiles) {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      ui.profile.append(option);
    }
    ui.profile.value = profiles.includes(selected) ? selected : profiles.includes(state.session?.meta?.profile) ? state.session.meta.profile : profiles.includes(result.default_profile) ? result.default_profile : profiles[0] || "";
    state.workdirs = (result.workdirs || []).map((path) => String(path));
    if (!ui.cwd.value.trim()) {
      sessionCwdCombo.setValue(result.default_cwd || state.workdirs[0] || "");
    }
    sessionCwdCombo.render();
    loaded = true;
  } finally {
    if (generation === state.optionsGeneration) button.disabled = !loaded || !ui.profile.options.length;
  }
}
window.addEventListener("igorek:profiles-changed", () => {
  loadOptions().catch((error) => { ui.sessionError.textContent = error.message; });
  if (window.IgorekDesktop && state.paired) {
    window.clearTimeout(state.quota.retryTimer);
    state.quota = {profile:currentSessionProfile(),provider:'',status:'idle',data:null,fetchedAt:0};
    loadOpenCodeUsage().catch(() => {});
  }
});

// The directory selection component: field + button + hint list. The same component is used in the new session dialog and external history import, so
// the list of working directories (`state.workdirs`) is shared.
function createWorkdirCombo(input, toggle, list, valueInput, onPick = null) {
  const combo = {
    input, toggle, list, valueInput, active: -1,
    setValue(path) {
      const value = String(path || "");
      input.value = value;
      valueInput.value = value;
    },
    matches(filtered) {
      const query = filtered ? input.value.trim().toLowerCase() : "";
      if (!query) return state.workdirs;
      return state.workdirs.filter((path) => path.toLowerCase().includes(query));
    },
    render(filtered) {
      const matches = combo.matches(filtered);
      list.replaceChildren();
      combo.active = matches.length ? 0 : -1;
      matches.forEach((path, index) => {
        const option = element("button", "cwd-option" + (index === combo.active ? " active" : ""), path);
        option.type = "button";
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(index === combo.active));
        option.addEventListener("mousedown", (event) => event.preventDefault());
        option.addEventListener("click", () => {
          combo.setValue(path);
          if (onPick) onPick();
          combo.close();
        });
        list.append(option);
      });
      if (!matches.length) list.append(element("p", "cwd-empty", t("dialog.cwd_no_matches")));
    },
    open(filtered) {
      combo.render(Boolean(filtered));
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-expanded", "true");
    },
    close() {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-expanded", "false");
    },
    move(delta) {
      const options = list.querySelectorAll(".cwd-option");
      if (!options.length) return;
      combo.active = (combo.active + delta + options.length) % options.length;
      options.forEach((option, index) => {
        option.classList.toggle("active", index === combo.active);
        option.setAttribute("aria-selected", String(index === combo.active));
      });
      options[combo.active].scrollIntoView({ block: "nearest" });
    },
    pick() {
      const options = list.querySelectorAll(".cwd-option");
      const active = options[combo.active];
      if (!active) return false;
      combo.setValue(active.textContent);
      if (onPick) onPick();
      combo.close();
      return true;
    },
    bind() {
      input.addEventListener("focus", () => combo.open());
      input.addEventListener("click", () => { if (list.hidden) combo.open(); });
      input.addEventListener("input", () => {
        valueInput.value = input.value;
        if (list.hidden) combo.open(true);
        else combo.render(true);
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          if (list.hidden) combo.open();
          else combo.move(1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          if (list.hidden) combo.open();
          else combo.move(-1);
        } else if (event.key === "Enter") {
          if (!list.hidden && combo.pick()) event.preventDefault();
        } else if (event.key === "Escape" && !list.hidden) {
          event.preventDefault();
          event.stopPropagation();
          combo.close();
        }
      });
      toggle.addEventListener("mousedown", (event) => event.preventDefault());
      toggle.addEventListener("click", () => {
        if (list.hidden) {
          input.focus();
          combo.open();
        } else {
          combo.close();
        }
      });
    },
  };
  combo.bind();
  return combo;
}
const sessionCwdCombo = createWorkdirCombo(ui.cwd, ui.cwdToggle, ui.cwdList, ui.cwdValue);
const agentsCwdCombo = createWorkdirCombo(ui.agentsCwd, ui.agentsCwdToggle, ui.agentsCwdList, ui.agentsCwdValue, () => {
  const preview = agentsModel().preview;
  if (preview) preview.cwdTouched = true;
  renderAgentsCwdHint();
});

// Client-side boundary for KVM policy (best effort): home directory, filesystem root, and
// obvious system directories. The server only mirrors home and root directories (`/`)
// (server.py::_cwd_denied_reason), so this list is intentionally limited.
const CWD_DENIED_EXACT = new Set([
  "/", "/root", "/home", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
  "/boot", "/dev", "/proc", "/sys", "/run",
]);
function cwdDeniedReason(raw) {
  const value = String(raw || "").trim();
  if (!value) return "";
  if (value === "~") return "home";
  const normalized = value.replace(/\/+$/, "") || "/";
  if (normalized === "/") return "root";
  if (normalized === "/root" || /^\/home\/[^/]+$/.test(normalized)) return "home";
  if (CWD_DENIED_EXACT.has(normalized)) return "system";
  return "";
}
function cwdDeniedMessage(reason) {
  if (reason === "home") return t("cwd.denied_home");
  if (reason === "root") return t("cwd.denied_root");
  return t("cwd.denied_system");
}
const desktopWorkspaceButton = byId("desktop-choose-workspace");
if (desktopWorkspaceButton && window.IgorekDesktop?.chooseWorkspace) {
  desktopWorkspaceButton.hidden = false;
  desktopWorkspaceButton.addEventListener("click", async () => {
    try {
      const selected = await window.IgorekDesktop.chooseWorkspace();
      if (!selected.canceled && selected.path) {
        sessionCwdCombo.setValue(selected.path);
        sessionCwdCombo.close();
      }
    } catch (error) { ui.sessionError.textContent = error.message; }
  });
}
function openNewSession() {
  showMainView();
  ui.sessionError.textContent = "";
  if (!ui.dialog.open) ui.dialog.showModal();
  loadOptions().catch((error) => { ui.sessionError.textContent = error.message; });
}
window.addEventListener("igorek:workspace-selected", (event) => {
  const cwd = event.detail?.cwd;
  if (typeof cwd !== "string" || !cwd || cwd.length > 4096) return;
  sessionCwdCombo.setValue(cwd);
  state.firstRunExample = true;
  openNewSession();
});
function stopStream() {
  state.stream?.close();
  state.stream = null;
}
async function selectSession(sessionId) {
  showMainView();
  if (state.selected !== sessionId) window.dispatchEvent(new Event('igorek-session-changed'));
  stopStream();
  stopMic();
  window.IgorekVoice?.onSessionChanged(sessionId);
  const generation = Number(state.selectGeneration || 0) + 1;
  state.selectGeneration = generation;
  state.selected = sessionId;
  window.IgorekSummary?.sync();
  state.pinned = true;
  state.items = [];
  state.seenSeq.clear();
  state.textStreams.clear();
  state.reasoningStreams.clear();
  state.toolItems.clear();
  state.knownAssistantText.clear();
  releaseAttachmentPreviews();
  state.attachments = [];
  resetMentionState();
  state.closedStreams.clear();
  state.textStreamRounds.clear();
  state.activeTasks.clear();
  state.finishedTasks.clear();
  state.startedTasks.clear();
  state.taskStarts.clear();
  state.finishNotes.clear();
  resetPanelData();
  setIndicator("indicator.working");
  renderAttachments();
  ui.welcome.hidden = true;
  ui.conversation.hidden = false;
  ui.timeline.replaceChildren();
  clearMessageRail();
  setRuntime(false, "status.loading");
  renderSessions();
  document.body.classList.remove("sidebar-open");
  try {
    const cursor = await api("/api/sessions/" + encode(sessionId) + "/cursor");
    const session = await api("/api/sessions/" + encode(sessionId));
    if (state.selected !== sessionId || state.selectGeneration !== generation) return;
    state.session = session;
    renderAgentNavigation();
    ui.composer.hidden = Boolean(session.meta?.agent_session);
    window.IgorekCompletionSound?.watch(session.meta?.agent_session ? null : session.running_task_id);
    if (session.running_task_id) {
      const runningTask = String(session.running_task_id);
      state.activeTasks.add(runningTask);
      state.startedTasks.add(runningTask);
      if (!state.taskStarts.has(runningTask)) state.taskStarts.set(runningTask, Date.now());
    }
    state.lastSeq = Number(cursor.seq || 0);
    state.items = sessionMessageItems(session.messages || []);
    state.items.filter((item) => item.type === "assistant").forEach((item) => state.knownAssistantText.add(item.text || ""));
    state.historyBaseIds = new Set(state.items.map((item) => item.id));
    renderContext(session);
    renderTimeline();
    if (state.panel.open) refreshPanel(false);
    loadApprovals(sessionId).catch(() => {});
    const selectedTitle=session.meta?.title || session.meta?.first_user_message || t("session.default_title");
    if(["scheduled","plugins"].includes(state.workspaceView))state.mainTitle=selectedTitle;else ui.title.textContent=selectedTitle;
    setRuntime(Boolean(session.running_task_id), session.running_task_id ? "status.working" : "status.ready");
    if (session.meta?.agent_session) {
      await loadAgentJournal(sessionId, generation);
      if (state.selected !== sessionId || state.selectGeneration !== generation) return;
      startStream(sessionId, state.lastSeq);
    } else {
      startStream(sessionId, state.lastSeq);
      loadSessionJournal(sessionId).catch(() => {});
    }
    loadSubagents(sessionId).catch(() => {});
    loadOpenCodeUsage().catch(() => {});
    await loadSessions();
    window.IgorekAppHistory?.schedule();
  } catch (error) {
    setRuntime(false, "status.offline");
    toast(error.message, "error");
  }
}
function renderContext(session) {
  const meta = (session && session.meta) || {};
  ui.context.replaceChildren(
    element("span", "context-dot"),
    element("span", "", meta.profile || t("session.profile_fallback")),
    element("span", "context-separator", "·"),
    element("span", "", meta.cwd || t("session.cwd_missing")),
  );
  renderComposerModel(session);
}
// Resolve local server models through /api/model-info; session metadata remains
// the fallback. Refresh at task boundaries and expire idle cache entries.
function modelInfoKey(profile, model) {
  return String(profile || "").trim() + "|" + String(model || "").trim();
}
function cachedModelInfo(profile, model) {
  return state.modelInfo.get(modelInfoKey(profile, model)) || null;
}
function modelInfoExpired(info) {
  return info?.status === "ready" && Date.now() - (info.loadedAt || 0) >= 30000;
}
async function loadModelInfo(profile, model, force = false) {
  const key = modelInfoKey(profile, model);
  const existing = state.modelInfo.get(key);
  if (existing?.pending) return existing.pending;
  if (existing?.status === "ready" && !force && !modelInfoExpired(existing)) return existing;
  const info = existing || { profile, model: model || "", context_window: null, status: "loading" };
  info.status = "loading";
  state.modelInfo.set(key, info);
  info.pending = (async () => {
    try {
      const suffix = model ? "&model=" + encode(model) : "";
      const payload = await api("/api/model-info?profile=" + encode(profile || "") + suffix);
      info.profile = String(payload.profile || profile || "");
      info.model = String(payload.model || model || "");
      const window = Number(payload.context_window);
      info.context_window = Number.isFinite(window) && window > 0 ? window : null;
      info.status = "ready";
      info.loadedAt = Date.now();
      return info;
    } catch (error) {
      info.status = "error";
      throw error;
    } finally {
      info.pending = null;
    }
  })();
  return info.pending;
}
function refreshComposerModel() {
  const meta = state.session?.meta;
  if (!meta?.profile) return;
  loadModelInfo(meta.profile, meta.model, true)
    .then(() => { renderComposerModel(state.session); renderPlanSection(); })
    .catch(() => {});
}
function renderComposerModel(session) {
  const meta = (session && session.meta) || {};
  const profile = String(meta.profile || "").trim();
  const metaModel = String(meta.model || "").trim();
  if (!profile && !metaModel) {
    ui.composerMeta.hidden = true;
    ui.composerModel.replaceChildren();
    return;
  }
  const cached = cachedModelInfo(profile, metaModel);
  const model = String((cached && cached.model) || metaModel).trim();
  const chip = ui.composerModel;
  chip.replaceChildren();
  chip.append(element("span", "composer-model-dot"));
  chip.append(element("span", "composer-model-profile", profile || t("session.profile_fallback")));
  let title = t("composer.profile_title", { profile: profile || t("session.profile_fallback") });
  if (model) {
    chip.append(element("span", "composer-model-separator", "·"));
    chip.append(element("span", "composer-model-name", model));
    title = t("composer.model_title", { model });
  }
  chip.title = title;
  chip.setAttribute("aria-label", title);
  ui.composerMeta.hidden = false;
  if (!cached || cached.status === "error" || modelInfoExpired(cached)) {
    loadModelInfo(profile, metaModel)
      .then(() => { renderComposerModel(state.session); renderPlanSection(); })
      .catch(() => {});
  }
}
function upsert(item) {
  const index = state.items.findIndex((current) => current.id === item.id);
  if (index >= 0) state.items[index] = { ...state.items[index], ...item };
  else state.items.push(item);
  renderTimeline();
}
function scrollIsPinned() {
  const el = ui.scroll;
  return el.scrollTop + el.clientHeight >= el.scrollHeight - 48;
}
function stickToBottom() {
  if (!state.pinned) return;
  const el = ui.scroll;
  state.programmaticScroll = true;
  const behavior = el.style.scrollBehavior;
  el.style.scrollBehavior = "auto";
  el.scrollTop = el.scrollHeight;
  window.requestAnimationFrame(() => {
    el.style.scrollBehavior = behavior;
    state.programmaticScroll = false;
  });
}
function sessionMessageItems(messages) {
  const items = [];
  const tools = new Map();
  messages.forEach((message, index) => {
    const id = message.history_id || (message.message_id ? "msg-" + message.message_id : "history-" + message.role + "-" + index);
    if (message.role === "tool") {
      const key = message.tool_use_id ? "import-tool-" + (message.task_id ? message.task_id + '-' : '') + message.tool_use_id : id;
      let item = tools.get(key);
      if (!item) {
        item = { id: key, type: "tool", task_id: message.task_id || '', tool_use_id: message.tool_use_id || '', name: message.tool_name || t("tool.default_name"), input: message.tool_input || {}, result: "", status: "done", ts: message.ts, history: true };
        tools.set(key, item);
        items.push(item);
      }
      if (message.tool_name) item.name = message.tool_name;
      if (message.tool_input && Object.keys(message.tool_input).length) item.input = message.tool_input;
      if (message.kind === "tool_result") item.result = message.tool_output || message.text || "";
      else if (message.kind === "tool_call" && !message.tool_name) {
        const parts = externalToolParts(message.text);
        item.name = parts.name;
        item.input = externalToolInput(parts.body);
      }
      item.status = message.tool_status || "done";
      if (message.kind === "web_search") item.input = { query: message.text };
    } else {
      items.push({ id, type: message.role, text: message.text || "", queued: Boolean(message.queued), ts: message.ts, history: true, task_id: message.task_id || "", message_id: message.message_id || "", attachments: message.attachments || [], files: message.files || [] });
    }
  });
  return items;
}
function compactionItem(event) {
  const data = event.payload || {};
  const phase = event.kind.split(".")[1];
  const labels = { started: "compaction.started", progress: "compaction.progress", completed: "compaction.completed", failed: "compaction.failed" };
  return {
    id: "compaction-" + String(event.task_id || "") + "-" + String(data.compaction_id || ""),
    type: "compaction", phase, title: t(labels[phase] || "compaction.started"),
    text: String(data.summary_text || ""), reason: String(data.reason || ""),
    chunk: data.chunk, totalChunks: data.total_chunks, seq: event.seq,
  };
}
function renderCompaction(item) {
  // Card slots are built once: updates to phase/text/reason apply to existing nodes,
  // so an expanded card does not flicker or collapse.
  const card = element("details", "compaction-card " + item.phase);
  const summary = element("summary", "");
  const note = element("p", "compaction-note");
  const body = element("div", "compaction-summary");
  const reason = element("p", "");
  card.append(summary, note, body, reason);
  let lastText = null;
  const update = (next) => {
    card.className = "compaction-card " + (next.phase || "");
    card.open = next.phase === "progress";
    summary.textContent = (next.title || "") + (next.chunk ? " · " + next.chunk + "/" + next.totalChunks : "");
    const text = String(next.text || "");
    note.hidden = !text;
    if (text) note.textContent = t(next.phase === "completed" ? "compaction.carried" : "compaction.partial");
    body.hidden = !text;
    if (text !== lastText) {
      lastText = text;
      body.replaceChildren();
      if (text) {
        if (window.IgorekMarkdown) window.IgorekMarkdown.renderMarkdown(text, body);
        else body.textContent = text;
      }
    }
    reason.hidden = !next.reason;
    reason.textContent = next.reason || "";
  };
  update(item);
  return registerTimelineUpdater(card, update);
}
const timelineNodes = new Map();
// Node → incremental update function. Redrawing an already displayed DOM element
// (re-parsing markdown, highlight.js, layout recalculation) causes visible flickering
// and scroll jumps, so content changes are applied piecewise, and CSS appearance
// animations are played only for truly new IDs.
const timelineUpdaters = new WeakMap();
function registerTimelineUpdater(node, update) {
  timelineUpdaters.set(node, update);
  return node;
}
function timelineVariant(item) {
  if (item.type === "approval") {
    if (item.resolved && item.kind !== 'user_input') return 'approval:resolved';
    return item.kind === "user_input" && !item.resolved ? "approval:form" : "approval:card";
  }
  if (item.type === "user" || item.type === "assistant") return "message:" + item.type;
  return item.type;
}
const activityOpenStates = new Map();
function activityItems(items) { return window.IgorekToolGroups ? window.IgorekToolGroups.project(items) : items; }
function renderTimelineNow() {
  window.IgorekSummary?.sync();
  const fragment = document.createDocumentFragment();
  const retained = new Set();
  const nodes = [];
  for (const item of activityItems(state.items)) {
    const signature = JSON.stringify(item);
    const variant = timelineVariant(item);
    const cached = timelineNodes.get(item.id);
    retained.add(item.id);
    if (cached && cached.node.parentNode === ui.timeline && cached.variant === variant) {
      // Same ID and variant: update content in place while keeping the same node.
      if (cached.signature !== signature) {
        if (cached.update) cached.update(item);
        cached.signature = signature;
      }
      nodes.push(cached.node);
      continue;
    }
    // Streaming response is pinned under a new ID: reuse the already visible DOM node,
    // otherwise, the CSS `rise-in` animation restarts on every round.
    if (item.adoptFrom) {
      const donor = timelineNodes.get(item.adoptFrom);
      if (donor && donor.node.parentNode === ui.timeline && donor.variant === variant) {
        if (donor.update) donor.update(item);
        donor.node.style.animation = "none";
        timelineNodes.delete(item.adoptFrom);
        delete item.adoptFrom;
        timelineNodes.set(item.id, { signature: JSON.stringify(item), node: donor.node, variant, update: donor.update });
        nodes.push(donor.node);
        continue;
      }
      delete item.adoptFrom;
    }
    const before = fragment.childNodes.length;
    if (item.type === "user" || item.type === "assistant") fragment.append(renderMessage(item));
    else if (item.type === "tool_group") fragment.append(renderActivityGroup(item, renderTimeline, 'main:' + state.selected));
    else if (item.type === "tool") fragment.append(renderTool(item));
    else if (item.type === "approval") fragment.append(renderApproval(item));
    else if (item.type === "reasoning") fragment.append(renderReasoning(item));
    else if (item.type === "compaction") fragment.append(renderCompaction(item));
    else if (item.type === "status") {
      const line = element("div", "status-line " + (item.level || ""), statusLineText(item));
      fragment.append(registerTimelineUpdater(line, (next) => {
        line.className = "status-line " + (next.level || "");
        line.textContent = statusLineText(next);
      }));
    }
    if (fragment.childNodes.length > before) {
      const node = fragment.lastChild;
      // Replacement of an existing ID (e.g., a confirmation form turned into a
      // solution card) — this is not a new occurrence: we do not restart the animation.
      if (cached) node.style.animation = "none";
      timelineNodes.set(item.id, { signature: JSON.stringify(item), node, variant, update: timelineUpdaters.get(node) });
      nodes.push(node);
    }
  }
  for (const id of timelineNodes.keys()) if (!retained.has(id)) timelineNodes.delete(id);
  let cursor = ui.timeline.firstChild;
  for (const node of nodes) {
    if (node === cursor) cursor = cursor.nextSibling;
    else ui.timeline.insertBefore(node, cursor);
  }
  while (cursor) {
    const next = cursor.nextSibling;
    cursor.remove();
    cursor = next;
  }
  const streaming = state.items.some((item) => item.streaming || (item.type === "tool" && item.status === "running"));
  ui.indicator.hidden = !(streaming || state.activeTasks.size > 0);
  stickToBottom();
  buildMessageRail();
}
// Redraw at most once per frame: a series of SSE events in a single tick
// should not trigger multiple layout passes in a row.
let timelineFrame = 0;
function renderTimeline() {
  if (timelineFrame) return;
  const raf = (typeof window !== "undefined" && window.requestAnimationFrame) ? window.requestAnimationFrame : null;
  if (!raf) { renderTimelineNow(); return; }
  timelineFrame = raf(() => { timelineFrame = 0; renderTimelineNow(); });
}
function copyMessage(button, text) {
  const value = String(text || "");
  const clipboard = window.navigator && window.navigator.clipboard;
  const settle = (key) => {
    button.textContent = t(key);
    button.classList.toggle("copied", key === "message.copied");
    window.clearTimeout(button.copyTimer);
    button.copyTimer = window.setTimeout(() => {
      if (!button.isConnected) return;
      button.textContent = t("message.copy");
      button.classList.remove("copied");
    }, 1600);
  };
  if (!value) {
    settle("message.copy_unavailable");
    return;
  }
  if (clipboard && clipboard.writeText) {
    clipboard.writeText(value).then(
      () => settle("message.copied"),
      () => settle("message.copy_unavailable"),
    );
  } else {
    settle("message.copy_unavailable");
  }
}
function renderMessage(item, context = {}) {
  const role = item.type === "user" ? "user" : "assistant";
  const profile = profileModel();
  const row = element("article", "message-row " + role);
  row.dataset.itemId = item.id;
  const avatar = element("span", "message-avatar");
  if (role === "assistant") {
    const mark = document.createElement("img");
    mark.src = "/mark.svg";
    mark.alt = "";
    avatar.append(mark);
  } else if (profile.avatar) {
    const photo = document.createElement("img");
    photo.src = profile.avatar;
    photo.alt = "";
    avatar.append(photo);
  } else avatar.textContent = t("message.user_letter");
  const content = element("div", "message-content");
  const heading = element("div", "message-heading");
  const name = element("strong", "", role === "user" ? (profile.name || t("message.you")) : t("brand.name"));
  const time = element("time", "");
  const queuedChip = element("span", "queued-chip", t("composer.queued"));
  const prioritizedChip = element("span", "queued-chip prioritized", t("composer.prioritized"));
  // A queued message can be forced («Send now») or removed from the queue.
  // Buttons live within the line and update in place, so they are not recreated
  // for every event.
  const queuedActions = element("span", "queued-actions");
  const forceButton = element("button", "queued-action", t("composer.force_send"));
  forceButton.type = "button";
  forceButton.title = t("composer.force_send_title");
  const cancelButton = element("button", "queued-action danger", t("composer.cancel_queued"));
  cancelButton.type = "button";
  cancelButton.title = t("composer.cancel_queued_title");
  queuedActions.append(forceButton, cancelButton);
  const copy = element("button", "message-copy", t("message.copy"));
  copy.type = "button";
  copy.title = t("message.copy_aria");
  copy.setAttribute("aria-label", t("message.copy_aria"));
  let currentText = String(item.text || "");
  let bound = item;
  copy.addEventListener("click", () => copyMessage(copy, currentText));
  forceButton.addEventListener("click", () => forceQueuedMessage(bound, forceButton, context.sessionId || state.selected, context.changed));
  cancelButton.addEventListener("click", () => cancelQueuedMessage(bound, cancelButton, context.sessionId || state.selected, context.changed));
  heading.append(name, time, queuedChip, prioritizedChip, queuedActions, copy);
  const body = element("div", "message-text");
  const attached = element("div", "message-attachments");
  const sessionId = context.sessionId || state.selected;
  content.append(heading, body, attached);
  row.append(avatar, content);
  let lastText = null;
  let lastStreaming = null;
  let lastAttachments = null;
  // In-place update: streaming deltas only modify text/streaming content,
  // so rebuilding the line and re-parsing markdown is unnecessary—otherwise,
  // every response frame would flicker and reset the scroll position.
  const update = (next) => {
    bound = next;
    const rawText = String(next.text || "");
    const attachments = role === "user" && Array.isArray(next.attachments) ? next.attachments : [];
    const files = Array.isArray(next.files) ? next.files : [];
    const signature = JSON.stringify([attachments, files]);
    if (signature !== lastAttachments) {
      lastAttachments = signature;
      attached.replaceChildren();
      for (const item of files) {
        if (!/^[0-9a-f]{64}$/.test(String(item?.id || ""))) continue;
        const name = String(item.name || "file");
        const link = element("a", "attachment-chip message-file", "↓ " + name);
        link.href = downloadFileUrl(sessionId, item.id); link.download = name;
        link.title = t("panel.download_file");
        const mime = String(item.mime_type || "");
        const kind = mime.startsWith('audio/') ? 'audio' : mime.startsWith('video/') ? 'video' : '';
        if (kind) {
          const card = element('section', 'message-media-card ' + kind);
          const header = element('div', 'message-media-header');
          const logo = element('span', 'message-media-logo', kind === 'audio' ? '♫' : '▷');
          const download = element('a', 'message-media-download');download.href=link.href;download.download=name;download.title=t('panel.download_file');download.setAttribute('aria-label',t('panel.download_file')+' '+name);download.append(workspaceGlyph('download'));
          header.append(logo,element('span','message-media-name',name),element('span','message-media-size',formatBytes(item.size||0)),download);
          const player = element(kind, 'message-media-player');player.controls=true;player.preload='none';player.autoplay=false;player.src=previewFileUrl(sessionId,item.id);player.setAttribute('aria-label',name);
          if (kind === 'video') player.playsInline=true;
          const error = element('div','side-note','Не удалось воспроизвести этот формат. Файл можно скачать.');error.hidden=true;player.addEventListener('error',()=>{error.hidden=false;});
          card.append(header,player,error);attached.append(card);
        } else attached.append(link);
      }
      for (const item of attachments) {
        if (!/^[0-9a-f]{32}$/.test(String(item?.upload_id || ""))) continue;
        const url = "/api/sessions/" + encode(sessionId) + "/uploads/" + encode(item.upload_id);
        const name = String(item.name || "file");
        if (["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/avif"].includes(item.mime_type)) {
          const preview = element("button", "message-image attachment-thumbnail");
          preview.type = "button"; preview.title = name;
          preview.setAttribute("aria-label", t("attachments.preview_aria", { name }));
          const image = element("img"); image.src = url; image.alt = name; image.loading = "lazy";
          image.addEventListener("load", () => { if (!context.sessionId) stickToBottom(); });
          image.addEventListener("error", () => { image.hidden = true; preview.classList.add("unavailable"); preview.append(element("span", "", name)); }, { once: true });
          preview.append(image);
          preview.addEventListener("click", () => showAttachmentPreview({ name, previewUrl: url }));
          attached.append(preview);
        } else {
          const file = element("a", "attachment-chip message-file", name);
          file.href = url; file.download = name;
          attached.append(file);
        }
      }
      attached.hidden = !attached.childElementCount;
    }
    currentText = rawText;
    name.textContent = role === "user" ? (profile.name || t("message.you")) : t("brand.name");
    const queued = Boolean(next.queued);
    const prioritized = Boolean(next.prioritized);
    queuedChip.hidden = !(queued && !prioritized);
    prioritizedChip.hidden = !(queued && prioritized);
    const messageId = queuedMessageId(next);
    const taskId = String(next.task_id || "");
    queuedActions.hidden = !(queued && messageId && taskId);
    forceButton.disabled = prioritized;
    const ts = Number(next.ts || 0);
    time.hidden = !ts;
    if (ts) {
      const date = new Date(ts < 10000000000 ? ts * 1000 : ts);
      time.textContent = new Intl.DateTimeFormat(intlLocale(), { hour: "2-digit", minute: "2-digit" }).format(date);
    }
    const streaming = Boolean(next.streaming);
    if (streaming !== lastStreaming) {
      lastStreaming = streaming;
      body.classList.toggle("live-caret", streaming);
    }
    if (rawText !== lastText) {
      lastText = rawText;
      if (role === "assistant" && rawText && window.IgorekMarkdown) {
        window.IgorekMarkdown.renderMarkdown(rawText, body);
        if (state.uiNavigation?.length && window.IgorekDesktop?.navigation) {
          window.IgorekMarkdown.activateNavigation(body, state.uiNavigation, id => navigateDesktop(id));
        }
      } else body.textContent = rawText;
    }
  };
  update(item);
  return registerTimelineUpdater(row, update);
}
async function navigateDesktop(id) {
  const route = state.uiNavigation?.find(route => route.id === id);
  if (!state.paired || !route) return;
  try {
    const result = await window.IgorekDesktop.navigation('open', id);
    if (result.error || !result.opened || result.route !== id || result.kind !== route.kind || result.target !== route.target) throw Error(t('navigation.unavailable'));
    if (route.kind === 'workspace') {
      const button = byId(route.target);
      if (!button || button.disabled || button.hidden) throw Error(t('navigation.unavailable'));
      if (button.getAttribute('aria-pressed') !== 'true' && button.getAttribute('aria-expanded') !== 'true' && button.getAttribute('aria-current') !== 'page') button.click();
    } else if (route.kind === 'panel') {
      showMainView();
      if (route.target === 'downloads') activateWorkspaceTab(state.panel.workspace.open('downloads'));
      else switchPanelTab(route.target);
      openPanel();
    }
  } catch (error) { toast(error.message, 'error'); }
}
function queuedMessageId(item) {
  if (!item) return "";
  if (item.message_id) return String(item.message_id);
  const id = String(item.id || "");
  return id.startsWith("msg-") ? id.slice(4) : "";
}
// Forced delivery: the bridge marks a message as priority, and the executor
// retrieves it from the queue at the nearest task boundary without waiting for the task to end.
async function forceQueuedMessage(item, button, sessionId = state.selected, changed = null) {
  const messageId = queuedMessageId(item);
  const taskId = String((item && item.task_id) || "");
  if (!sessionId || !messageId || !taskId) return;
  button.disabled = true;
  try {
    await api(
      "/api/sessions/" + encode(sessionId) + "/messages/" + encode(messageId) + "/send-now",
      { method: "POST", body: JSON.stringify({ task_id: taskId }) },
    );
    item.prioritized = true;
    if (changed) changed(); else renderTimeline();
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
  }
}
async function cancelQueuedMessage(item, button, sessionId = state.selected, changed = null) {
  const messageId = queuedMessageId(item);
  const taskId = String((item && item.task_id) || "");
  if (!sessionId || !messageId || !taskId) return;
  button.disabled = true;
  try {
    await api(
      "/api/sessions/" + encode(sessionId) + "/messages/" + encode(messageId) + "/cancel",
      { method: "POST", body: JSON.stringify({ task_id: taskId }) },
    );
    if (changed) changed(); else { state.items = state.items.filter((row) => row.id !== item.id); renderTimeline(); }
  } catch (error) {
    toast(error.message, "error");
    button.disabled = false;
  }
}
const RAIL_MIN_MESSAGES = 2;
const RAIL_PREVIEW_LIMIT = 120;
let railLayoutFrame = 0;
function scheduleRailLayout() {
  if (railLayoutFrame) return;
  railLayoutFrame = window.requestAnimationFrame(() => {
    railLayoutFrame = 0;
    layoutMessageRail();
    updateRailActive();
  });
}
function clearMessageRail() {
  state.rail.items = [];
  state.rail.active = -1;
  state.rail.signature = "";
  if (ui.rail) {
    ui.rail.hidden = true;
    ui.rail.replaceChildren();
  }
}
function railPreviewText(item) {
  const text = String((item && item.text) || "").replace(/\s+/g, " ").trim();
  return text.length > RAIL_PREVIEW_LIMIT ? text.slice(0, RAIL_PREVIEW_LIMIT - 1).trimEnd() + "…" : text;
}
function buildMessageRailTicks(rows) {
  const fragment = document.createDocumentFragment();
  rows.forEach((row, index) => {
    const item = state.items.find((current) => current.id === row.dataset.itemId) || {};
    const preview = railPreviewText(item);
    const time = item.ts ? localClock(toMillis(item.ts)) : "";
    const tick = element("button", "message-rail-tick");
    tick.type = "button";
    const suffix = (time ? " · " + time : "") + (preview ? ": " + preview : "");
    tick.setAttribute("aria-label", t("rail.tick", { n: index + 1, suffix }));
    const bubble = element("span", "message-rail-preview");
    bubble.append(element("span", "message-rail-preview-text", preview || t("rail.preview_fallback")));
    if (time) bubble.append(element("span", "message-rail-preview-time", time));
    tick.append(bubble);
    tick.addEventListener("click", () => scrollToRailMessage(index));
    fragment.append(tick);
  });
  return fragment;
}
function buildMessageRail() {
  const rail = ui.rail;
  if (!rail) return;
  const rows = Array.from(ui.timeline.querySelectorAll(".message-row.user"));
  const signature = rows.map((row) => row.dataset.itemId || "").join(",");
  if (signature !== state.rail.signature) {
    state.rail.signature = signature;
    state.rail.active = -1;
    if (rows.length < RAIL_MIN_MESSAGES || ui.conversation.hidden) {
      rail.hidden = true;
      rail.replaceChildren();
      state.rail.items = [];
    } else {
      rail.hidden = false;
      rail.replaceChildren(buildMessageRailTicks(rows));
      state.rail.items = rows.map((row, index) => ({ row, tick: rail.children[index] }));
    }
  }
  layoutMessageRail();
  updateRailActive();
}
function layoutMessageRail() {
  const rail = ui.rail;
  if (!rail || rail.hidden || ui.conversation.hidden) return;
  const scroll = ui.scroll;
  rail.style.top = Math.max(0, scroll.offsetTop + 14) + "px";
  const height = Math.max(12, scroll.clientHeight - 28);
  rail.style.height = height + "px";
  const step = Math.min(10, height / Math.max(1, state.rail.items.length));
  state.rail.items.forEach((entry, index) => {
    entry.tick.style.top = ((index + 0.5) * step) + "px";
    entry.tick.style.height = step + "px";
  });
}
function setRailActive(index) {
  state.rail.active = index;
  state.rail.items.forEach((entry, position) => entry.tick.classList.toggle("active", position === index));
}
function updateRailActive() {
  const items = state.rail.items;
  if (!items.length || ui.rail.hidden) return;
  const limit = ui.scroll.scrollTop + 64;
  let active = 0;
  for (let index = 0; index < items.length; index += 1) {
    if (items[index].row.offsetTop <= limit) active = index;
  }
  if (active !== state.rail.active) setRailActive(active);
}
function scrollToRailMessage(index) {
  const entry = state.rail.items[index];
  if (!entry) return;
  setRailActive(index);
  ui.scroll.scrollTo({ top: Math.max(0, entry.row.offsetTop - 14), behavior: "smooth" });
}
function stringify(value) {
  if (typeof value === "string") return value;
  try { return JSON.stringify(value ?? {}, null, 2); } catch { return String(value ?? ""); }
}
function summarize(input) {
  if (typeof input === "string") return input.slice(0, 100) || t("tool.open_details");
  if (!input || typeof input !== "object") return t("tool.open_details");
  for (const key of ["command", "file_path", "path", "query", "url"]) {
    if (typeof input[key] === "string" && input[key]) return input[key].slice(0, 100);
  }
  return Object.keys(input).length ? t("tool.params", { n: Object.keys(input).length }) : t("tool.open_details");
}
const TOOL_STATUS_KEYS = {
  running: "tool.status.running", done: "tool.status.done", error: "tool.status.error",
  exit: "tool.status.exit", stopped: "tool.status.stopped",
};
function approvalVerdict(item, short = false) {
  const keys = { allow_once: 'allow_once', allow_session: 'allow_session', reject: 'reject', cancel: 'cancel' };
  const decision = keys[item.resolved];
  if (!decision) return String(item.resolved || '');
  if (short) return lang === 'en'
    ? ({allow_once:'once',allow_session:'session',reject:'rejected',cancel:'cancelled'})[decision]
    : ({allow_once:'один раз',allow_session:'на сессию',reject:'отклонено',cancel:'отменено'})[decision];
  return t('approval.resolved_' + decision);
}
function renderResolvedApproval(item) {
  const card = element('details', 'resolved-approval');
  const summary = element('summary', 'resolved-approval-head');
  const label = element('span', 'resolved-approval-name');
  const verdict = element('span', 'resolved-approval-verdict');
  summary.append(element('span', 'resolved-approval-mark', '◇'), label, verdict);
  const body = element('div', 'resolved-approval-body');
  const reason = element('p'), target = element('pre');
  body.append(reason, target); appendApprovalOrigin(body, item); card.append(summary, body);
  const update = next => {
    label.textContent = next.tool || t('approval.default_tool');
    verdict.textContent = approvalVerdict(next);
    card.classList.toggle('danger', ['reject', 'cancel'].includes(next.resolved));
    if (reason.textContent !== (next.reason || '')) reason.textContent = next.reason || ''; reason.hidden = !next.reason;
    if (target.textContent !== (next.target || '')) target.textContent = next.target || ''; target.hidden = !next.target;
  };
  update(item); return registerTimelineUpdater(card, update);
}
function renderActivityGroup(item, rerender, scope) {
  const card = element('section', 'activity-group');
  const head = element('button', 'activity-group-head'); head.type = 'button';
  const chevron = element('span', 'activity-group-chevron', '›');
  const label = element('span', 'activity-group-label');
  const badge = element('span', 'activity-group-state');
  head.append(chevron, label, badge);
  const body = element('div', 'activity-group-body');
  card.append(head, body);
  const key = scope + '\0' + item.id;
  if (!activityOpenStates.has(key)) activityOpenStates.set(key, item.rows.some(row => row.item.open));
  const nodes = new Map(); let bound = item;
  head.addEventListener('click', () => {
    activityOpenStates.set(key, !activityOpenStates.get(key)); update(bound);
  });
  const update = next => {
    bound = next;
    const stats = window.IgorekToolGroups.stats(next), open = activityOpenStates.get(key);
    card.classList.toggle('open', open); card.classList.toggle('running', stats.running > 0); card.classList.toggle('error', stats.errors > 0);
    head.setAttribute('aria-expanded', String(Boolean(open)));
    label.textContent = (stats.tools ? (lang === 'en' ? 'Actions: ' : 'Действия: ') : (lang === 'en' ? 'Decisions: ' : 'Решения: ')) + stats.count;
    const names = [...new Set(next.rows.filter(row => row.item.type === 'tool').map(row => row.item.name || t('tool.default_name')))];
    if (names.length) label.textContent += ' · ' + names.slice(0, 3).join(', ') + (names.length > 3 ? '…' : '');
    const counts = [];
    if (stats.successful) counts.push(["success", "✓ " + stats.successful, lang === "en" ? "Successful" : "Успешно"]);
    if (stats.errors) counts.push(["error", "✕ " + stats.errors, lang === "en" ? "Unsuccessful" : "Неуспешно"]);
    if (stats.running) counts.push(["running", "⋯ " + stats.running, lang === "en" ? "Running" : "Выполняется"]);
    if (stats.stopped) counts.push(["stopped", "■ " + stats.stopped, lang === "en" ? "Stopped" : "Остановлено"]);
    if (stats.unknown) counts.push(["unknown", "? " + stats.unknown, lang === "en" ? "Unknown result" : "Результат неизвестен"]);
    badge.replaceChildren();
    if (stats.done) badge.append(element("span", "activity-count success", "✓ " + t("tool.status.done")));
    else for (const [kind, text] of counts) badge.append(element("span", "activity-count " + kind, text));
    const summary = counts.map(([, text, label]) => label + ": " + text.slice(2)).join(" · ");
    badge.title = summary; badge.setAttribute("aria-label", summary);
    head.title = summary + (stats.permissions ? " · " + (lang === 'en' ? 'Recorded decisions: ' : 'Принятых решений: ') + stats.permissions : '');
    body.hidden = !open;
    const retained = new Set(), ordered = [];
    for (const row of next.rows) {
      let cached = nodes.get(row.id), node = cached?.node;
      if (cached && row.item.type === 'tool') row.item.open = Boolean(cached.item.open);
      if (!node) {
        node = row.item.type === 'tool' ? renderTool(row.item, rerender)
          : row.item.type === 'approval' ? renderResolvedApproval(row.item)
          : element('div', 'activity-status', statusLineText(row.item));
        nodes.set(row.id, {node, item:row.item});
      }
      else cached.item = row.item;
      if (row.item.type === 'tool') timelineUpdaters.get(node)?.(row.item, row.approvals);
      else if (row.item.type === 'approval') timelineUpdaters.get(node)?.(row.item);
      else node.textContent = statusLineText(row.item);
      retained.add(row.id); ordered.push(node);
    }
    let cursor = body.firstChild;
    for (const node of ordered) { if (node === cursor) cursor = cursor.nextSibling; else body.insertBefore(node, cursor); }
    while (cursor) { const following = cursor.nextSibling; cursor.remove(); cursor = following; }
    for (const id of nodes.keys()) if (!retained.has(id)) nodes.delete(id);
  };
  update(item); return registerTimelineUpdater(card, update);
}
// rerender — where to redraw the card after collapsing/expanding:
// a standard session timeline or agent history preview.
function renderTool(item, rerender = renderTimeline) {
  const card = element("article", "tool-card" + (item.open ? " open" : ""));
  const head = element("button", "tool-head");
  head.type = "button";
  head.setAttribute("aria-expanded", String(Boolean(item.open)));
  head.append(element("span", "tool-glyph", "⌘"));
  const title = element("span", "tool-title-wrap");
  const name = element("span", "tool-name", item.name || t("tool.default_name"));
  const summary = element("span", "tool-summary", summarize(item.input));
  title.append(name, summary);
  const stateBadge = element("span", "tool-state " + (item.status || "done"));
  const permissions = element('span', 'tool-permissions');
  head.append(title, permissions, stateBadge, element("span", "tool-chevron", "⌄"));
  // Click toggles the **current** state of the element: upsert replaces the object in
  // state.items, so the handler maintains an up-to-date reference rather than the original.
  let bound = item;
  head.addEventListener("click", () => { bound.open = !bound.open; rerender(); });
  const details = element("div", "tool-details");
  details.append(element("div", "tool-detail-label", t("tool.input")));
  const inputPre = element("pre", "tool-detail-content", stringify(item.input));
  details.append(inputPre);
  const resultLabel = element("div", "tool-detail-label");
  const resultPre = element("pre", "tool-detail-content");
  details.append(resultLabel, resultPre);
  const subagentLabel = element("div", "tool-detail-label", t("subagents.label"));
  // The subagent slot is the **only** part regenerated: its entry is updated via
  // separate SSE events; the rest of the card changes incrementally.
  const subagentSlot = element("div", "tool-subagent-slot");
  details.append(subagentLabel, subagentSlot);
  const approvalSlot = element('div', 'tool-approval-details'); details.append(approvalSlot);
  card.append(head, details);
  let lastSubagentSignature = "";
  // The tool card updates in-place: a status change from `running` → `done` should **not**
  // regenerate the DOM, otherwise open details and scroll position would reset.
  let lastApprovals = '';
  const update = (next, approvals = []) => {
    bound = next;
    const open = Boolean(next.open);
    card.classList.toggle("open", open);
    head.setAttribute("aria-expanded", String(open));
    name.textContent = next.name || t("tool.default_name");
    summary.textContent = summarize(next.input);
    const status = next.is_error ? 'error' : next.status || "done";
    stateBadge.className = "tool-state " + status;
    stateBadge.textContent = TOOL_STATUS_KEYS[status] ? t(TOOL_STATUS_KEYS[status]) : status;
    const approvalSignature = JSON.stringify(approvals);
    if (approvalSignature !== lastApprovals) {
      lastApprovals = approvalSignature; permissions.replaceChildren(); approvalSlot.replaceChildren();
      for (const approval of approvals) {
        const chip = element('span', 'tool-permission' + (['reject', 'cancel'].includes(approval.resolved) ? ' danger' : ''), approvalVerdict(approval, true));
        chip.title = approvalVerdict(approval); permissions.append(chip); approvalSlot.append(renderResolvedApproval(approval));
      }
    }
    const renderedInput = stringify(next.input);
    if (inputPre.textContent !== renderedInput) inputPre.textContent = renderedInput;
    const result = String(next.result || "");
    resultLabel.hidden = !result;
    resultPre.hidden = !result;
    if (result) {
      resultLabel.textContent = next.duration ? t("tool.result_ms", { ms: next.duration }) : t("tool.result");
      if (resultPre.textContent !== result) resultPre.textContent = result;
    } else {
      resultLabel.textContent = "";
      resultPre.textContent = "";
    }
    const subagent = next.subagent || null;
    subagentLabel.hidden = !subagent;
    subagentSlot.hidden = !subagent;
    const signature = subagent ? JSON.stringify(subagent) : "";
    if (signature !== lastSubagentSignature) {
      lastSubagentSignature = signature;
      subagentSlot.replaceChildren();
      if (subagent) subagentSlot.append(buildSubagentBody(subagent));
    }
  };
  update(item);
  return registerTimelineUpdater(card, update);
}
function renderApproval(item) {
  if (item.kind === "user_input" && !item.resolved) return renderUserQuestion(item);
  if (item.resolved && item.kind !== 'user_input') return renderResolvedApproval(item);
  const card = element("article", "event-card approval-card" + (item.resolved ? " resolved" : ""));
  card.append(element("span", "event-mark", "◇"));
  const text = element("div", "event-copy");
  const title = element("strong", "");
  const detail = element("span", "");
  text.append(title, detail);
  appendApprovalOrigin(text, item);
  // The action slot remains the same: buttons before decision, verdict after.
  // The card updates in-place, so the decision does not regenerate the DOM.
  const actions = element("div", "approval-actions");
  text.append(actions);
  card.append(text);
  let bound = item;
  let lastResolved = null;
  const update = (next) => {
    bound = next;
    const resolved = next.resolved || "";
    card.classList.toggle("resolved", Boolean(resolved));
    title.textContent = t(resolved ? "approval.decided" : "approval.waiting", { tool: next.tool || t("approval.default_tool") });
    const calendarWrite = ['calendar_create_event', 'calendar_move_event'].includes(next.tool);
    const action = next.target || (next.input ? stringify(next.input) : '');
    detail.textContent = [action, next.reason].filter(Boolean).join('\n\n') || t("approval.default_reason");
    detail.style.whiteSpace = 'pre-wrap';
    if (resolved === lastResolved) return;
    lastResolved = resolved;
    actions.replaceChildren();
    if (resolved) {
      const labels = {
        allow_once: "approval.resolved_allow_once", allow_session: "approval.resolved_allow_session",
        reject: "approval.resolved_reject", cancel: "approval.resolved_cancel",
      };
      const rejected = resolved === "reject" || resolved === "cancel";
      const verdict = labels[resolved] ? t(labels[resolved]) : resolved;
      actions.append(element("span", "approval-verdict" + (rejected ? " danger" : ""), verdict));
    } else {
      const choices = calendarWrite ? [["allow_once", "approval.btn_allow"], ["reject", "approval.btn_reject"]] : [["allow_once", "approval.btn_allow"], ["allow_session", "approval.btn_allow_session"], ["reject", "approval.btn_reject"]];
      for (const [decision, labelKey] of choices) {
        const button = element("button", "approval-button " + decision, t(labelKey));
        button.type = "button";
        button.addEventListener("click", () => decideApproval(bound, decision));
        actions.append(button);
      }
    }
  };
  update(item);
  return registerTimelineUpdater(card, update);
}
async function decideApproval(item, decision) {
  if (!item.request_id) return;
  try {
    await api(
      "/api/tasks/" + encode(item.task_id || "task") + "/approvals/" + encode(item.request_id),
      { method: "POST", body: JSON.stringify({ decision }) },
    );
    const current = state.items.find(row => row.id === item.id && row.request_id === item.request_id);
    if (current) upsert({ ...current, resolved: decision });
    toast(t("approval.sent"));
  } catch (error) {
    toast(error.message, "error");
  }
}
async function loadApprovals(sessionId) {
  const generation = state.selectGeneration;
  const payload = await api("/api/approvals?session_id=" + encode(sessionId));
  if (state.selected !== sessionId || state.selectGeneration !== generation) return;
  for (const approval of payload.approvals || []) {
    if (!approval.request_id) continue;
    const approvalId="approval-"+String(approval.task_id||'task')+'-'+approval.request_id;
    if (state.items.some((item) => item.id === approvalId)) continue;
    upsert({
      id: approvalId, type: "approval",
      task_id: approval.task_id, request_id: approval.request_id,
      kind: approval.kind, input: approval.input, session_id: approval.session_id,
      tool: approval.tool, reason: approval.reason, target: approval.target,
    });
  }
}
async function stopSession() {
  if (!state.selected) return;
  try {
    await api("/api/sessions/" + encode(state.selected) + "/cancel", {
      method: "POST", body: JSON.stringify({ mode: "cancel" }),
    });
    toast(t("indicator.stopping"));
    setIndicator("indicator.stopping");
    setRuntime(true, "status.stopping");
  } catch (error) {
    toast(error.message, "error");
  }
}
function reasoningItem(taskId) {
  return state.items.find((current) => current.id === "reasoning-" + taskId);
}
function reasoningDurationMs(item) {
  const start = Number(item.startedTs || 0);
  const end = Number(item.endedTs || 0);
  if (!start || !end || end <= start) return 0;
  return end - start;
}
function highlightReasoningCode(body) {
  const viewer = window.IgorekFileViewer;
  if (!viewer) return;
  for (const code of body.querySelectorAll(".md-pre > code")) {
    code.closest(".md-code")?.classList.add("file-viewer-code");
    const text = code.textContent || "";
    const language = Array.from(code.classList).find(name => name.startsWith("language-"));
    if (language) {
      viewer.applyHighlight(code, text, language.slice(9)).catch(() => {});
    } else if (viewer.byteLength(text) <= viewer.limits.highlightBytes) {
      viewer.ensureHighlightJs().then(highlighter => {
        if (!highlighter) return;
        try { highlighter.highlightElement(code); }
        catch (_) { code.textContent = text; }
      }).catch(() => {});
    }
  }
}
function renderReasoning(item) {
  const details = document.createElement("details");
  details.className = "reasoning-card";
  const summary = element("summary");
  const summaryText = document.createTextNode("");
  const duration = element("span", "reasoning-duration", "");
  summary.append(summaryText, duration);
  // Thoughts are rendered via the **same** markdown module as assistant replies:
  // headings/lists/tables/code appear identically. The module constructs DOM **without** innerHTML, so
  // CSP for the page remains unaffected.
  const body = element("div", "reasoning-text");
  let bound = item;
  let lastText = null;
  // In-place updates preserve the `<details>` expanded state and scroll position:
  // rebuilding the node on every delta would discard both.
  const update = (next) => {
    bound = next;
    details.open = Boolean(next.open);
    summaryText.nodeValue = next.streaming ? t("reasoning.title_live") : t("reasoning.title");
    const durationMs = reasoningDurationMs(next);
    duration.hidden = durationMs < 1000;
    if (durationMs >= 1000) duration.textContent = " · " + formatDuration(durationMs);
    const raw = String(next.text || "");
    if (raw !== lastText) {
      lastText = raw;
      if (raw && window.IgorekMarkdown) window.IgorekMarkdown.renderMarkdown(raw, body);
      else body.textContent = raw;
      highlightReasoningCode(body);
    }
  };
  details.addEventListener("toggle", () => { bound.open = details.open; });
  details.append(summary, body);
  update(item);
  return registerTimelineUpdater(details, update);
}
function startStream(sessionId, after) {
  stopStream();
  const stream = new EventSource("/api/sessions/" + encode(sessionId) + "/events?after=" + encode(after));
  state.stream = stream;
  stream.onopen = () => {
    if (state.activeTasks.size) setRuntime(true, "status.working");
    else if (state.runtimeKey === "status.reconnecting") setRuntime(false, "status.ready");
  };
  stream.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data);
      if (event.kind === "stream.ready") return;
      if (Number.isInteger(event.seq)) {
        if (state.seenSeq.has(event.seq)) return;
        state.seenSeq.add(event.seq);
        state.lastSeq = Math.max(state.lastSeq, event.seq);
      }
      window.IgorekCompletionSound?.observe(event);
      handleEvent(event);
      window.IgorekVoice?.onSessionEvent(event);
    } catch (error) {
      console.error("Не удалось разобрать событие агента", error);
    }
  };
  stream.onerror = () => {
    if (state.activeTasks.size) setRuntime(true, "status.reconnecting");
    else setRuntime(false, "status.offline");
  };
}
function isolationStarting() {
  return String((state.session && state.session.isolation) || "") === "kvm";
}
function requeueText(reason) {
  if (reason === "pending_steer_messages") return "task.requeued_steer";
  if (reason === "orphan_steer_messages") return "task.requeued_orphan";
  if (reason === "approval_resolved") return "task.requeued_approval";
  return "task.requeued";
}
function toMillis(value) {
  const raw = Number(value || 0);
  if (!raw) return 0;
  return Math.round(raw < 10000000000 ? raw * 1000 : raw);
}
function formatDuration(ms) {
  const total = Math.max(0, Math.round(Number(ms || 0) / 1000));
  if (total < 60) return t("duration.seconds", { n: total });
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes < 60) {
    return seconds
      ? t("duration.minutes_seconds", { m: minutes, s: seconds })
      : t("duration.minutes", { m: minutes });
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest
    ? t("duration.hours_minutes", { h: hours, r: String(rest).padStart(2, "0") })
    : t("duration.hours", { h: hours });
}
function localClock(ms) {
  return new Intl.DateTimeFormat(intlLocale(), { hour: "2-digit", minute: "2-digit" }).format(new Date(ms));
}
function finishSuffix(taskId, finishedAt) {
  const started = state.taskStarts.get(taskId);
  if (!started) return "";
  state.taskStarts.delete(taskId);
  const end = Number(finishedAt) || Date.now();
  return " · " + formatDuration(Math.max(0, end - started)) + " · " + localClock(end);
}
function startTask(taskId, runtimeKey, statusKey, statusParams, level, extraKey) {
  state.startedTasks.add(taskId);
  state.finishedTasks.delete(taskId);
  state.finishNotes.delete(taskId);
  if (!state.taskStarts.has(taskId)) state.taskStarts.set(taskId, Date.now());
  state.activeTasks.add(taskId);
  if (statusKey) {
    upsert({
      id: "status-" + taskId + "-accepted", type: "status",
      key: statusKey, params: statusParams || null, extraKey: extraKey || "", level: level || "",
    });
  } else renderTimeline();
  setRuntime(true, runtimeKey);
}
// **Waiting approval** is a pause, not a final state: it arrives in `task.finish`
// (reason=approval_required) and `task.status`; it is **not** considered a completion.
function pauseTask(taskId) {
  state.finishedTasks.delete(taskId);
  state.activeTasks.add(taskId);
  if (!state.taskStarts.has(taskId)) state.taskStarts.set(taskId, Date.now());
  upsert({
    id: "status-" + taskId + "-approval", type: "status",
    task_id: taskId,
    key: "task.paused", level: "warn",
  });
  setIndicator("indicator.approval");
  setRuntime(true, "status.needs_attention");
}
function finishTask(taskId, ok, noteKey, noteParams, runtimeKey, finishedAt) {
  refreshComposerModel();
  state.panel.filesGeneration = (state.panel.filesGeneration || 0) + 1;
  state.panel.files.status = "idle";
  const level = ok ? "ok" : "warn";
  let note = state.finishNotes.get(taskId);
  // `task.status(fail)` occurs before `task.error`: the specific reason from `error_message`
  // must replace the generic string «Task completed: fail», otherwise the isolation error text remains hidden.
  const upgrade = note && noteKey === "task.finished_error" && note.key !== "task.finished_error";
  if (!note || upgrade) {
    note = {
      key: noteKey, params: noteParams || null,
      suffix: note ? note.suffix : finishSuffix(taskId, finishedAt),
    };
    state.finishNotes.set(taskId, note);
  }
  const statusItem = {
    id: "status-" + taskId + "-finish", type: "status",
    key: note.key, params: note.params, suffix: note.suffix, level,
  };
  if (state.finishedTasks.has(taskId)) {
    upsert(statusItem);
    if (state.panel.open) refreshPanel(true);
    return;
  }
  state.finishedTasks.add(taskId);
  state.activeTasks.delete(taskId);
  state.textStreams.delete(taskId);
  state.textStreamRounds.delete(taskId);
  state.reasoningStreams.delete(taskId);
  for (const item of state.items) {
    if (item.id === "stream-" + taskId && item.type === "assistant") item.streaming = false;
    if (item.id === "reasoning-" + taskId && item.type === "reasoning") item.streaming = false;
    if (item.type === "tool" && item.status === "running" && item.id.startsWith("tool-" + taskId + "-")) item.status = "stopped";
  }
  setIndicator("indicator.working");
  upsert(statusItem);
  setRuntime(false, runtimeKey || (ok ? "status.ready" : "status.check"));
  loadSessions().catch(() => {});
  if (state.panel.open) refreshPanel(true);
}
function resetTextStream(taskId) {
  state.textStreams.delete(taskId);
  state.textStreamRounds.delete(taskId);
  state.closedStreams.delete(taskId);
  state.items = state.items.filter(item => item.id !== 'stream-' + taskId);
}
function handleEvent(event) {
  const data = event.payload || {};
  const taskId = event.task_id || data.task_id || "task";
  switch (event.kind) {
    case "compaction.started":
    case "compaction.progress":
    case "compaction.completed":
    case "compaction.failed":
      upsert(compactionItem(event));
      break;

    case "schedule.delivered": {
      upsert({id:"msg-"+data.message_id,type:"assistant",text:data.text||"",files:data.files||[],ts:event.ts,task_id:taskId});
      loadSessions().catch(()=>{});
      break;
    }
    case "task.submitted": {
      if (data.text || data.attachments?.length) {
        upsert({
          id: data.message_id ? "msg-" + data.message_id : "user-" + taskId + "-" + (event.seq || Date.now()),
          type: "user", text: data.text || "", attachments: data.attachments || [], queued: false, ts: event.ts,
          task_id: taskId, message_id: data.message_id || "",
        });
      }
      startTask(taskId, "status.queued", "task.accepted", null, null, isolationStarting() ? "task.isolation_extra" : "");
      break;
    }
    case "task.message_added": {
      if (data.text || data.attachments?.length) {
        upsert({
          id: data.message_id ? "msg-" + data.message_id : "user-" + taskId + "-" + (event.seq || Date.now()),
          type: "user", text: data.text || "", attachments: data.attachments || [], queued: true, ts: event.ts,
          task_id: taskId, message_id: data.message_id || "",
        });
      }
      state.activeTasks.add(taskId);
      setRuntime(true, "status.queued");
      renderTimeline();
      break;
    }
    case "task.message_prioritized": {
      const item = state.items.find((current) => current.id === "msg-" + (data.message_id || ""));
      if (item) { item.prioritized = true; renderTimeline(); }
      break;
    }
    case "task.message_cancelled": {
      const id = "msg-" + (data.message_id || "");
      const index = state.items.findIndex((current) => current.id === id);
      if (index >= 0) { state.items.splice(index, 1); renderTimeline(); }
      break;
    }
    case "steer.delivered": {
      const item = state.items.find((current) => current.id === "msg-" + (data.message_id || ""));
      if (item) { item.queued = false; renderTimeline(); }
      break;
    }
    case "task.start": {
      refreshComposerModel();
      resetTextStream(taskId);
      startTask(taskId, "status.working", "task.started", null, "ok");
      setIndicator("indicator.working");
      break;
    }
    case "task.requeued": {
      resetTextStream(taskId);
      const reason = String(data.reason || "");
      const textKey = requeueText(reason);
      state.finishedTasks.delete(taskId);
      state.finishNotes.delete(taskId);
      if (state.startedTasks.has(taskId)) {
        // **Continuation of an already running task**: a separate line, not a new start.
        upsert({
          id: "status-" + taskId + "-resumed-" + String(event.seq || Date.now()),
          type: "status", task_id: taskId, key: textKey, level: "",
        });
        state.activeTasks.add(taskId);
        if (!state.taskStarts.has(taskId)) state.taskStarts.set(taskId, Date.now());
        setRuntime(true, "status.queued");
      } else {
        startTask(taskId, "status.queued", textKey);
      }
      setIndicator(isolationStarting() ? "indicator.isolation" : "indicator.working");
      break;
    }
    case "message.files": {
      const item = [...state.items].reverse().find(row => row.type === 'assistant' && row.task_id === taskId && !row.streaming);
      if (item) upsert({...item, files: Array.isArray(data.files) ? data.files : []});
      break;
    }
    case "model.text_delta": {
      const delta = data.text || "";
      if (!delta || state.finishedTasks.has(taskId)) break;
      const round = Number.isInteger(data.round) && data.round > 0 ? data.round : null;
      const closed = state.closedStreams.get(taskId);
      // Durable responses can precede their queued live deltas. Round identity,
      // rather than text overlap, decides whether a live frame is obsolete.
      if (closed && (round === null || (closed.round !== null && round <= closed.round))) break;
      const previousRound = state.textStreamRounds.get(taskId);
      if (previousRound != null && round !== null && round < previousRound) break;
      const previous = previousRound === round ? state.textStreams.get(taskId) || "" : "";
      const text = previous + delta;
      state.textStreams.set(taskId, text);
      state.textStreamRounds.set(taskId, round);
      upsert({ id: "stream-" + taskId, type: "assistant", text, streaming: true });
      setRuntime(true, "status.typing");
      break;
    }
    case "model.round_response": {
      if(globalThis.window?.IgorekDesktop)refreshOpenCodeUsageIfVisible();
      const text = data.text || "";
      const round = Number.isInteger(data.round) && data.round > 0 ? data.round : null;
      const streamRound = state.textStreamRounds.get(taskId);
      const closesCurrent = streamRound === undefined || streamRound === round;
      const streamVisible = closesCurrent && state.items.some(item => item.id === "stream-" + taskId && item.type === "assistant");
      if (closesCurrent) {
        state.items = state.items.filter(item => item.id !== "stream-" + taskId);
        state.textStreams.delete(taskId);
        state.textStreamRounds.delete(taskId);
      }
      const closed = state.closedStreams.get(taskId);
      if (!closed || closed.round === null || (round !== null && round >= closed.round)) state.closedStreams.set(taskId, { round });
      if (text && !state.knownAssistantText.has(text)) {
        state.knownAssistantText.add(text);
        // Round numbers restart after approvals; durable sequence identifies the message.
        const committed = { id: "assistant-" + taskId + "-" + (event.seq || data.round || Date.now()), type: "assistant", text, ts: event.ts, task_id: taskId };
        if (streamVisible) committed.adoptFrom = "stream-" + taskId;
        upsert(committed);
      } else renderTimeline();
      if (!data.demo_notice) noteUsage(data, event.seq);
      if (data.demo_notice) {
        const active = state.activeTasks.size > 0;
        setRuntime(active, active ? "status.working" : "status.ready");
      } else setRuntime(true, "status.working");
      break;
    }
    case "model.reasoning_delta": {
      if (state.finishedTasks.has(taskId)) break;
      const previous = reasoningItem(taskId);
      const text = String(previous?.text || "") + (data.text || "");
      state.reasoningStreams.set(taskId, (state.reasoningStreams.get(taskId) || "") + (data.text || ""));
      // Duration is calculated based on **actual event timestamps (ts)**, not wall-clock time:
      // the first delta sets the start, each subsequent one sets the end.
      const stamp = toMillis(event.ts);
      upsert({
        id: "reasoning-" + taskId, type: "reasoning", text, streaming: true,
        startedTs: (previous && previous.startedTs) || stamp || 0,
        endedTs: stamp || (previous && previous.endedTs) || 0,
      });
      break;
    }
    case "model.reasoning": {
      if (state.finishedTasks.has(taskId)) break;
      const previous = reasoningItem(taskId);
      const pending = state.reasoningStreams.get(taskId) || "";
      const accumulated = String(previous?.text || "");
      const committed = pending && accumulated.endsWith(pending)
        ? accumulated.slice(0, accumulated.length - pending.length) : accumulated;
      const text = committed + (data.text || pending);
      state.reasoningStreams.delete(taskId);
      // A **flash frame** (synthetic live frame) may arrive without `ts`:
      // the end of deliberation is determined by the `ts` of the last delta; do not invent anything.
      const stamp = toMillis(event.ts);
      if (text) {
        upsert({
          id: "reasoning-" + taskId, type: "reasoning", text, streaming: false,
          startedTs: (previous && previous.startedTs) || stamp || 0,
          endedTs: stamp || (previous && previous.endedTs) || 0,
        });
      }
      break;
    }
    case "tool.request_batch":
      for (const tool of data.tools || []) {
        const id = "tool-" + taskId + "-" + (tool.id || event.seq || Date.now());
        const item = { id, type: "tool", task_id: taskId, tool_use_id: tool.id || '', name: tool.name, input: tool.input || {}, status: "running", open: false };
        const known = knownSubagentFor(tool.id);
        if (known) item.subagent = known;
        state.toolItems.set(id, item);
        upsert(item);
      }
      window.IgorekSummary?.refresh(true);
      notePlanFromTools(data.tools, event.seq);
      setRuntime(true, "status.tool");
      break;
    case "tool.result": {
      if (['error', 'fail', 'failed'].includes(data.status) || data.is_error === true)
        desktopNotification('Инструмент · ' + (data.tool_name || 'Выполнение'), data.result_preview || data.error_message || 'Ошибка выполнения инструмента');
      const id = "tool-" + taskId + "-" + (data.tool_use_id || "");
      const previous = state.toolItems.get(id);
      const item = {
        id, type: "tool", task_id: taskId, tool_use_id: data.tool_use_id || '', is_error: data.is_error === true, name: data.tool_name || previous?.name || t("tool.default_name"),
        input: data.tool_input || previous?.input || {}, result: data.result_preview || "",
        duration: Number(data.duration_ms || 0), status: data.status || "done", open: Boolean(previous?.open),
      };
      state.toolItems.set(id, item);
      refreshSessionArtifacts();
      const knownSub = knownSubagentFor(data.tool_use_id);
      if (knownSub) item.subagent = knownSub;
      upsert(item);
      if (String(data.tool_name || "") === "update_plan") notePlan(data.tool_input, event.seq);
      break;
    }
    case "approval.required":
      upsert({
        id: "approval-" + String(data.task_id||taskId) + '-' + (data.request_id || event.seq || Date.now()), type: "approval",
        task_id: data.task_id || taskId, request_id: data.request_id,
        tool_use_id: data.tool_use_id || '', kind: data.kind, input: data.input,
        tool: data.tool, reason: data.reason, target: data.target,
      });
      pauseTask(taskId);
      break;
    case "approval.resolved": {
      const requestId = String(data.request_id || "");
      const current = state.items.find(item => item.type === "approval" && item.request_id === requestId && (!item.task_id || String(item.task_id)===String(data.task_id||taskId)));
      if (current && ["allow_once", "allow_session", "reject", "cancel"].includes(data.decision)) {
        upsert({ ...current, resolved: data.decision });
      }
      break;
    }
    case "task.status":
      if (data.status === "waiting_approval") pauseTask(taskId);
      else if (data.status === "cancelling") { setIndicator("indicator.stopping"); setRuntime(true, "status.stopping"); }
      else if (data.status === "ok") {
        finishTask(taskId, true, "task.finished", null, "", toMillis(data.ts || event.ts) || Date.now());
      }
      else if (["cancelled", "fail", "dead_letter", "timeout"].includes(data.status)) {
        const stopped = data.status === "cancelled";
        finishTask(
          taskId, false,
          stopped ? "task.stopped" : "task.finished_status",
          stopped ? null : { status: data.status },
          stopped ? "status.stopped" : "",
          toMillis(data.ts) || Date.now(),
        );
      }
      break;
    case "task.cancel_requested":
      state.activeTasks.add(taskId);
      setIndicator("indicator.stopping");
      setRuntime(true, "status.stopping");
      break;
    case "task.cancelled":
      finishTask(taskId, false, "task.stopped", null, "status.stopped", toMillis(data.ts) || Date.now());
      break;
    case "task.error":
      desktopNotification('Ошибка агента · ' + taskId, data.error_message || t("task.no_description"));
      finishTask(
        taskId, false,
        "task.finished_error",
        { error: data.error_message || keyRef("task.no_description") },
        "", toMillis(data.ts) || Date.now(),
      );
      break;
    case "task.finish": {
      const status = String(data.status || "").toLowerCase();
      if (status === "waiting_approval") {
        // `task.finish` with status `waiting_approval` is a **pause in the trace for confirmation**, not a terminal task outcome.
        pauseTask(taskId);
        break;
      }
      const ok = ["ok", "completed", "success"].includes(status);
      finishTask(
        taskId, ok,
        ok ? "task.finished" : "task.finished_status",
        ok ? null : { status: data.status || keyRef("task.unknown_status") },
        "", toMillis(data.ts || data.finished_at || data.completed_at) || Date.now(),
      );
      break;
    }
    case "subagent.start":
    case "subagent.progress":
    case "subagent.finish":
    case "subagent.cancel_requested":
      applySubagentEvent(event, data);
      break;
    default:
      break;
  }
}

// --- Subagents delegate_task -----------------------------
// The child task card is linked to the delegate_task call via
// tool_use_id: both history and SSE refer to the same card (id
// "tool-<task>-<tool_use_id>"), so duplicates do not appear.
function knownSubagentFor(toolUseId) {
  if (!toolUseId) return null;
  for (const record of state.subagents.values()) {
    if (record.tool_use_id === toolUseId) return record;
  }
  return null;
}
function eventTaskId(event, data) {
  return event.task_id || data.task_id || "task";
}
function applySubagentEvent(event, data) {
  const subagentId = String(data.subagent_id || "");
  if (!subagentId) return;
  const record = { ...(state.subagents.get(subagentId) || { subagent_id: subagentId }) };
  for (const key of ["tool_use_id", "goal_title", "model", "profile", "depth", "status",
    "stage", "tool_name", "tool_calls_count", "summary", "error", "parent_task_id"]) {
    if (data[key] !== undefined) record[key] = data[key];
  }
  if (data.stage) record.last_stage = data.stage;
  if (data.tool_name) record.last_tool = data.tool_name;
  if (event.kind === "subagent.cancel_requested") record.cancelRequested = true;
  if (!record.status) record.status = "running";
  state.subagents.set(subagentId, record);
  const taskId = eventTaskId(event, data);
  if (record.tool_use_id) {
    const cardId = "tool-" + taskId + "-" + record.tool_use_id;
    const item = state.toolItems.get(cardId) || state.items.find((current) => current.id === cardId);
    if (item) {
      item.subagent = record;
      renderTimeline();
    }
  }
  // The «Agents» panel is reloaded at startup and completion: progress updates
  // only the card to avoid network calls for each child tool.
  if (state.panel.open && state.panel.tab === "subagents" && event.kind !== "subagent.progress") {
    loadSubagents(state.selected);
  }
}
async function createSession(event) {
  const reviewContext=pendingReviewSession;
  event.preventDefault();
  ui.sessionError.textContent = "";
  const cwd = ui.cwd.value.trim();
  const denied = window.IgorekDesktop?.chooseWorkspace ? "" : cwdDeniedReason(cwd);
  if (denied) {
    ui.sessionError.textContent = cwdDeniedMessage(denied);
    return;
  }
  const button = byId("create-session-button");
  button.disabled = true;
  try {
    if(window.IgorekAgent&&!(await window.IgorekAgent.ensureProfile(ui.profile.value)))return;
    const session = await api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({
        profile: ui.profile.value, cwd, idempotency_key: crypto.randomUUID(),
      }),
    });
    const firstRunExample = state.firstRunExample;
    ui.dialog.close();
    await loadSessions();
    await selectSession(session.session_id);
    if (firstRunExample && !ui.message.value.trim()) {
      ui.message.value = t("onboarding.first_task");
      ui.message.dispatchEvent(new Event("input", { bubbles: true }));
    }
    if(reviewContext){pendingReviewSession=null;await window.IgorekReviews.sessionCreated(reviewContext,session.session_id);}
    else ui.message.focus();
  } catch (error) {
    ui.sessionError.textContent = error.message;
    if(reviewContext&&!ui.dialog.open)toast(error.message,"error");
  } finally {
    button.disabled = false;
  }
}
const ATTACHMENT_LIMIT = 20;
function formatBytes(size) {
  const value = Number(size || 0);
  if (value >= 1024 * 1024) return (value / (1024 * 1024)).toFixed(1) + " " + t("bytes.mb");
  if (value >= 1024) return Math.round(value / 1024) + " " + t("bytes.kb");
  return value + " " + t("bytes.b");
}
// Clipboard paste follows the same path as the «+» button. Screenshots arrive
// from clipboard without a name (or with a template image.png): assign a clear name
// screenshot-<YYYYMMDD-HHMMSS>.<MIME-type-extension>.
const CLIPBOARD_GENERIC_NAMES = new Set(["image", "image.png", "image.jpg", "blob", "blob.png", "file", "file.png"]);
const PASTED_FILE_EXTENSIONS = {
  "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/gif": "gif",
  "image/webp": "webp", "image/bmp": "bmp", "image/tiff": "tiff", "image/svg+xml": "svg",
};
function pasteTimestamp(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return String(date.getFullYear()) + pad(date.getMonth() + 1) + pad(date.getDate())
    + "-" + pad(date.getHours()) + pad(date.getMinutes()) + pad(date.getSeconds());
}
function pastedFileExtension(type) {
  if (PASTED_FILE_EXTENSIONS[type]) return PASTED_FILE_EXTENSIONS[type];
  const subtype = String(type || "").split("/")[1] || "";
  const clean = subtype.split("+")[0].replace(/[^a-z0-9]/g, "");
  return clean || "png";
}
function pastedFileName(type, stamp, index) {
  const prefix = String(type || "").startsWith("image/") ? "screenshot" : "pasted";
  return prefix + "-" + stamp + (index ? "-" + (index + 1) : "") + "." + pastedFileExtension(type);
}
// Returns only file elements from clipboard. An empty list indicates
// a text paste—it cannot be intercepted.
function clipboardPasteFiles(event) {
  const data = event.clipboardData;
  if (!data || !data.items) return [];
  const stamp = pasteTimestamp(new Date());
  const result = [];
  let unnamed = 0;
  for (const item of Array.from(data.items)) {
    if (item.kind !== "file") continue;
    const file = item.getAsFile();
    if (!file) continue;
    const type = String(file.type || item.type || "").toLowerCase();
    const name = String(file.name || "").trim();
    if (name && !CLIPBOARD_GENERIC_NAMES.has(name.toLowerCase())) {
      result.push(file);
      continue;
    }
    result.push(new File([file], pastedFileName(type, stamp, unnamed), {
      type: type || "application/octet-stream",
      lastModified: Number(file.lastModified) || Date.now(),
    }));
    unnamed += 1;
  }
  return result;
}
function handleMessagePaste(event) {
  const files = clipboardPasteFiles(event);
  if (!files.length) return;
  event.preventDefault();
  addAttachments(files);
}
function releaseAttachmentPreviews(){for(const item of state.attachments){if(item.previewUrl)URL.revokeObjectURL(item.previewUrl);item.previewUrl='';}}
function showAttachmentPreview(item){
  if(!item.previewUrl)return;
  const dialog=element('dialog','attachment-preview-dialog'),head=element('div','attachment-preview-heading'),close=element('button','workspace-icon-button','×'),image=element('img');
  close.type='button';close.setAttribute('aria-label','Закрыть просмотр');head.append(element('span','',item.name),close);image.src=item.previewUrl;image.alt=item.name;dialog.append(head,image);document.body.append(dialog);close.addEventListener('click',()=>dialog.close());dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
}
window.addEventListener('pagehide',releaseAttachmentPreviews);
function addAttachments(files) {
  for (const file of files) {
    if (!file || !file.size) { toast(t("attachments.empty", { name: (file && file.name) || "" })); continue; }
    if (state.attachments.length >= ATTACHMENT_LIMIT) { toast(t("attachments.limit", { n: ATTACHMENT_LIMIT })); break; }
    if (state.attachments.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) continue;
    state.attachmentSeq += 1;
    state.attachments.push({
      key: "attachment-" + state.attachmentSeq, file, name: file.name, size: file.size,
      status: "pending", uploadId: "", sessionId: "",
      previewUrl: ["image/png","image/jpeg","image/webp","image/gif","image/bmp","image/avif"].includes(file.type) ? URL.createObjectURL(file) : "",
    });
  }
  renderAttachments();
}
function renderAttachments() {
  ui.attachments.replaceChildren();
  ui.attachments.hidden = !state.attachments.length;
  for (const item of state.attachments) {
    const chip = element("span", "attachment-chip " + item.status + (item.previewUrl ? " attachment-image-card" : ""));
    chip.title=item.name;
    if(item.previewUrl){const preview=element("button","attachment-thumbnail"),image=element("img");preview.type="button";preview.setAttribute("aria-label","Посмотреть "+item.name);image.src=item.previewUrl;image.alt=item.name;preview.append(image);preview.addEventListener("click",()=>showAttachmentPreview(item));chip.append(preview);}
    else chip.append(element("span", "attachment-name", item.name));
    const detail = item.status === "uploading" ? t("attachments.loading")
      : item.status === "error" ? t("attachments.error") : formatBytes(item.size);
    chip.append(element("span", "attachment-size", detail));
    const remove = element("button", "attachment-remove", "×");
    remove.type = "button";
    remove.setAttribute("aria-label", t("attachments.remove_aria", { name: item.name }));
    remove.addEventListener("click", () => {
      if(item.previewUrl)URL.revokeObjectURL(item.previewUrl);
      state.attachments = state.attachments.filter((current) => current !== item);
      renderAttachments();
    });
    chip.append(remove);
    ui.attachments.append(chip);
  }
}
async function uploadAttachment(item, sessionId) {
  item.status = "uploading";
  renderAttachments();
  try {
    const result = await api("/api/sessions/" + encode(sessionId) + "/uploads", {
      method: "POST",
      body: item.file,
      headers: {
        "Content-Type": item.file.type || "application/octet-stream",
        "X-File-Name": encodeURIComponent(item.name),
      },
    });
    item.uploadId = String(result.upload_id || "");
    item.sessionId = sessionId;
    item.status = item.uploadId ? "uploaded" : "error";
  } catch (error) {
    item.status = "error";
    toast(t("attachments.upload_failed", { name: item.name, error: error.message }));
  }
  renderAttachments();
  return item.status === "uploaded";
}
async function sendMessage(event) {
  event.preventDefault();
  const text = ui.message.value.trim();
  if (text === '/demo' && state.attachments.length) { toast(t('demo.separate')); return; }
  if (state.session?.meta?.agent_session) return;
  if ((!text && !state.attachments.length) || !state.selected || state.sending) return;
  state.sending = true;
  ui.send.disabled = true;
  const sessionId = state.selected;
  try {
    if(window.IgorekAgent&&!(await window.IgorekAgent.ensureProfile(state.session?.meta?.profile)))return;
    if(state.selected!==sessionId)return;
    const failed = [];
    for (const item of state.attachments.slice()) {
      if (item.uploadId && item.sessionId === sessionId) continue;
      if (!(await uploadAttachment(item, sessionId))) failed.push(item.name);
    }
    if (failed.length) {
      toast(t("attachments.send_failed", { list: failed.join(", ") }));
      return;
    }
    const uploadIds = state.attachments
      .filter((item) => item.uploadId && item.sessionId === sessionId)
      .map((item) => item.uploadId);
    const payload = { text, idempotency_key: crypto.randomUUID(), upload_ids: uploadIds, plan_mode: planModeActive() };
    if (text === '/demo') payload.demo_language = lang;
    if (state.uiNavigation?.length) payload.desktop_ui = true;
    const skillIds = trackedSkillIds(text);
    if (skillIds.length) payload.skill_ids = skillIds;
    const sent = await api("/api/sessions/" + encode(sessionId) + "/messages", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    releaseAttachmentPreviews();
    state.attachments = [];
    renderAttachments();
    mentionModel().skillsTracked.clear();
    closeMentionMenu();
    ui.message.value = "";
    ui.message.style.height = "auto";
    if (sent?.demo && sent.session_id) {
      await selectSession(sent.session_id);
      return;
    }
    state.pinned = true;
    setIndicator(isolationStarting() ? "indicator.isolation" : "indicator.working");
    setRuntime(true, "status.queued");
    ui.message.focus();
    await loadSessions();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.sending = false;
    ui.send.disabled = false;
  }
}

// Voice input: Web Speech API (Chrome/Edge). The button appears
// only where the API is available; transcription is written in textarea. Repeating the click
// stops recording. The Ctrl+Shift+B hotkey for the host script is unavailable on the page—this
// is a separate host-side mechanism; the webpage cannot trigger it.
const MIC_ERROR_KEYS = { "not-allowed": "composer.mic_denied", "service-not-allowed": "composer.mic_denied" };
let micRecognition = null;
let micRecording = false;
let micBase = "";
let micCaret = 0;

function micSupported() {
  return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
}
function renderMicButton() {
  if (!ui.mic) return;
  const supported = micSupported();
  ui.mic.hidden = !supported;
  if (!supported) return;
  const label = micRecording ? t("composer.mic_stop") : t("composer.mic_start");
  ui.mic.classList.toggle("recording", micRecording);
  ui.mic.setAttribute("aria-pressed", String(micRecording));
  ui.mic.setAttribute("aria-label", label);
  ui.mic.title = label;
}
function micApply(transcript) {
  const before = micBase.slice(0, micCaret);
  const after = micBase.slice(micCaret);
  ui.message.value = before + transcript + after;
  const caret = before.length + transcript.length;
  ui.message.setSelectionRange(caret, caret);
  autoSizeMessage();
}
function stopMic() {
  if (micRecognition) {
    try {
      micRecognition.stop();
    } catch (error) {
      // Recognition completed automatically.
    }
  }
  micRecording = false;
  renderMicButton();
}
function startMic() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) return;
  micBase = ui.message.value;
  micCaret = Number(ui.message.selectionStart ?? micBase.length);
  const recognition = new Recognition();
  recognition.lang = lang === "en" ? "en-US" : "ru-RU";
  recognition.interimResults = true;
  recognition.continuous = false;
  recognition.onresult = (event) => {
    let finalText = "";
    let interimText = "";
    for (let index = 0; index < event.results.length; index += 1) {
      const result = event.results[index];
      const text = result[0] ? String(result[0].transcript || "") : "";
      if (result.isFinal) finalText += text;
      else interimText += text;
    }
    micApply(finalText + interimText);
  };
  recognition.onerror = (event) => {
    const code = String((event && event.error) || "");
    micRecording = false;
    renderMicButton();
    if (code === "aborted" || code === "no-speech") return;
    toast(t(MIC_ERROR_KEYS[code] || "composer.mic_error", { error: code || t("composer.mic_unavailable") }));
  };
  recognition.onend = () => {
    micRecording = false;
    renderMicButton();
  };
  micRecognition = recognition;
  micRecording = true;
  renderMicButton();
  try {
    recognition.start();
  } catch (error) {
    micRecording = false;
    renderMicButton();
    toast(t("composer.mic_error", { error: error.message }));
  }
}
function toggleMic() {
  if (micRecording) stopMic();
  else startMic();
}

// In Composer references: «$» denotes included skills from the session profile, «@» denotes files
// from the session's working directory. Visible text is not replaced: the skill is passed as «$id» and
// added to skill_ids, while the file is inserted with its relative path unchanged.
const MENTION_MAX_ROWS = 60;
const MENTION_MAX_SKILLS = 5;
const MENTION_TREE_TTL_MS = 15000;
const MENTION_TREE_DEBOUNCE_MS = 140;
const MENTION_SKILL_TOKEN = /(^|\s)\$([A-Za-z0-9._-]{1,160})/g;
let mentionTimer = 0;

function mentionModel() { return state.mentions; }
function currentSessionProfile() {
  const meta = (state.session && state.session.meta) || {};
  if (meta.profile) return String(meta.profile);
  const row = state.sessions.find((item) => item.session_id === state.selected);
  return String((row && row.profile) || "");
}
function mentionContext() {
  const value = ui.message.value;
  const caret = Number(ui.message.selectionStart ?? value.length);
  let start = caret;
  while (start > 0 && !/\s/.test(value[start - 1])) start -= 1;
  const token = value.slice(start, caret);
  if (!token) return null;
  const trigger = token[0];
  if (trigger !== "$" && trigger !== "@") return null;
  return { trigger, query: token.slice(1), start, caret };
}
function mentionPathParts(query) {
  const slash = query.lastIndexOf("/");
  if (slash < 0) return { dir: "", filter: query };
  return { dir: query.slice(0, slash + 1), filter: query.slice(slash + 1) };
}
function autoSizeMessage() {
  ui.message.style.height = "auto";
  ui.message.style.height = Math.min(ui.message.scrollHeight, 170) + "px";
}
function resetMentionState() {
  mentionModel().skillCache.clear();
  mentionModel().skillError.clear();
  mentionModel().tree.clear();
  mentionModel().skillsTracked.clear();
  mentionModel().skillPending.clear();
  mentionModel().treePending.clear();
  closeMentionMenu();
}
function closeMentionMenu() {
  const model = mentionModel();
  model.menu = "closed";
  model.mode = "";
  model.items = [];
  model.active = -1;
  model.query = "";
  model.dir = "";
  model.start = 0;
  model.caret = 0;
  model.skillError.clear();
  window.clearTimeout(mentionTimer);
  ui.mentionMenu.hidden = true;
  ui.mentionRows.replaceChildren();
  ui.mentionHead.hidden = true;
  ui.mentionNote.hidden = true;
}
function renderMentionFrame(mode, headText) {
  const model = mentionModel();
  model.menu = mode;
  ui.mentionHead.hidden = !headText;
  ui.mentionHead.textContent = headText || "";
  ui.mentionMenu.hidden = false;
}
function showMentionNote(text) {
  const model = mentionModel();
  model.items = [];
  model.active = -1;
  ui.mentionRows.replaceChildren();
  ui.mentionRows.hidden = true;
  ui.mentionNote.textContent = text || "";
  ui.mentionNote.hidden = !text;
}
function renderMentionRows(rows) {
  const model = mentionModel();
  model.items = rows;
  model.active = rows.length ? 0 : -1;
  ui.mentionNote.hidden = true;
  if (!rows.length) {
    showMentionNote(t("mention.empty"));
    return;
  }
  ui.mentionRows.hidden = false;
  ui.mentionRows.replaceChildren();
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const button = element("button", "mention-row" + (index === 0 ? " active" : ""));
    button.type = "button";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", index === 0 ? "true" : "false");
    button.append(element("span", "mention-row-name", row.label));
    if (row.meta) button.append(element("span", "mention-row-meta", row.meta));
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => acceptMention(index));
    button.addEventListener("mouseenter", () => setMentionActive(index));
    ui.mentionRows.append(button);
  }
}
function setMentionActive(index) {
  const model = mentionModel();
  if (index < 0 || index >= model.items.length) return;
  model.active = index;
  const buttons = ui.mentionRows.children;
  for (let i = 0; i < buttons.length; i += 1) {
    const active = i === index;
    buttons[i].classList.toggle("active", active);
    buttons[i].setAttribute("aria-selected", active ? "true" : "false");
  }
  if (buttons[index] && typeof buttons[index].scrollIntoView === "function") {
    buttons[index].scrollIntoView({ block: "nearest" });
  }
}
function replaceMentionToken(replacement) {
  const ctx = mentionContext();
  if (!ctx) return;
  const value = ui.message.value;
  ui.message.value = value.slice(0, ctx.start) + replacement + value.slice(ctx.caret);
  const caret = ctx.start + replacement.length;
  ui.message.setSelectionRange(caret, caret);
}
function acceptMention(index) {
  const row = mentionModel().items[index];
  if (!row) return;
  if (row.kind === "skill") {
    if (!mentionModel().skillsTracked.has(row.id) && mentionModel().skillsTracked.size >= MENTION_MAX_SKILLS) {
      toast(t("mention.skills_limit"));
      return;
    }
    replaceMentionToken("$" + row.id);
    mentionModel().skillsTracked.set(row.id, true);
    closeMentionMenu();
  } else if (row.kind === "dir") {
    replaceMentionToken("@" + row.path + "/");
    refreshMention();
  } else {
    replaceMentionToken(row.path);
    closeMentionMenu();
  }
  autoSizeMessage();
  ui.message.focus();
}
function refreshMention() {
  const ctx = mentionContext();
  if (!ctx) {
    closeMentionMenu();
    return;
  }
  if (ctx.trigger === "$") openSkillMention(ctx);
  else openFileMention(ctx);
}
function mentionKeydown(event) {
  const model = mentionModel();
  if (model.menu === "closed") return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (model.items.length) setMentionActive((model.active + 1) % model.items.length);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    if (model.items.length) setMentionActive((model.active - 1 + model.items.length) % model.items.length);
  } else if (event.key === "Enter" || event.key === "Tab") {
    if (!model.items.length) return;
    event.preventDefault();
    acceptMention(model.active >= 0 ? model.active : 0);
  } else if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeMentionMenu();
  }
}
function normalizeSkills(payload) {
  const skills = payload && Array.isArray(payload.skills) ? payload.skills : [];
  return skills.filter((skill) => skill && skill.skill_id && skill.enabled && skill.supported !== false);
}
function skillCacheFromSettings(profile) {
  const settings = state.settings;
  if (!settings || settings.profile !== profile || !settings.extensions) return null;
  return normalizeSkills(settings.extensions);
}
function openSkillMention(ctx) {
  const model = mentionModel();
  model.mode = "skills";
  model.query = ctx.query;
  model.dir = "";
  model.start = ctx.start;
  model.caret = ctx.caret;
  const profile = currentSessionProfile();
  const head = t("mention.head_skills", { profile: profile || t("session.profile_fallback") });
  if (!profile) {
    renderMentionFrame("skills", head);
    showMentionNote(t("mention.no_profile"));
    return;
  }
  const cached = model.skillCache.get(profile) || skillCacheFromSettings(profile);
  if (cached) {
    model.skillCache.set(profile, cached);
    renderSkillMentionRows(cached, ctx.query);
    return;
  }
  renderMentionFrame("skills", head);
  if (model.skillError.has(profile)) {
    showMentionNote(t("mention.error_skills"));
    return;
  }
  showMentionNote(t("mention.loading"));
  if (!model.skillPending.has(profile)) {
    model.skillPending.add(profile);
    fetchProfileSkills(profile);
  }
}
async function fetchProfileSkills(profile) {
  const model = mentionModel();
  let failed = false;
  try {
    const payload = await api("/api/extensions" + (profile ? "?profile=" + encode(profile) : ""));
    model.skillCache.set(profile, normalizeSkills(payload));
  } catch (error) {
    failed = true;
    model.skillError.set(profile, error.message);
  } finally {
    model.skillPending.delete(profile);
  }
  if (model.mode !== "skills" || currentSessionProfile() !== profile) return;
  if (failed) showMentionNote(t("mention.error_skills"));
  else renderSkillMentionRows(model.skillCache.get(profile) || [], model.query);
}
function renderSkillMentionRows(skills, query) {
  const needle = String(query || "").trim().toLowerCase();
  const rows = skills.filter((skill) => {
    if (!needle) return true;
    const id = String(skill.skill_id || "").toLowerCase();
    const name = String(skill.name || "").toLowerCase();
    return id.startsWith(needle) || name.includes(needle) || id.includes(needle);
  }).slice(0, MENTION_MAX_ROWS).map((skill) => ({
    kind: "skill",
    id: String(skill.skill_id),
    label: String(skill.name || skill.skill_id),
    meta: "$" + skill.skill_id + (skill.description ? " · " + String(skill.description) : ""),
  }));
  renderMentionFrame("skills", t("mention.head_skills", {
    profile: currentSessionProfile() || t("session.profile_fallback"),
  }));
  renderMentionRows(rows);
}
function mentionHeadPath(dir) {
  return t("mention.head_files", { path: "@" + dir });
}
function openFileMention(ctx) {
  const model = mentionModel();
  const { dir, filter } = mentionPathParts(ctx.query);
  model.mode = "files";
  model.query = filter;
  model.dir = dir;
  model.start = ctx.start;
  model.caret = ctx.caret;
  const sessionId = state.selected;
  if (!sessionId) {
    renderMentionFrame("files", mentionHeadPath(dir));
    showMentionNote(t("mention.no_session"));
    return;
  }
  const key = sessionId + "\u0000" + dir;
  const cached = model.tree.get(key);
  if (cached && Date.now() - cached.ts < MENTION_TREE_TTL_MS) {
    renderFileMentionRows(cached, dir, filter);
    return;
  }
  renderMentionFrame("files", mentionHeadPath(dir));
  showMentionNote(t("mention.loading"));
  window.clearTimeout(mentionTimer);
  mentionTimer = window.setTimeout(() => {
    if (!mentionModel().treePending.has(key)) fetchMentionTree(key, dir);
  }, MENTION_TREE_DEBOUNCE_MS);
}
async function fetchMentionTree(key, dir) {
  const model = mentionModel();
  if (model.treePending.has(key)) return;
  model.treePending.add(key);
  const sessionId = key.slice(0, key.indexOf("\u0000"));
  try {
    const data = await api("/api/sessions/" + encode(sessionId) + "/tree?path=" + encode(dir) + "&depth=1");
    model.tree.set(key, {
      ts: Date.now(),
      entries: Array.isArray(data.entries) ? data.entries : [],
      truncated: Boolean(data.truncated),
    });
  } catch (error) {
    model.tree.set(key, { ts: Date.now(), entries: [], error: error.message });
  } finally {
    model.treePending.delete(key);
  }
  if (model.mode !== "files" || model.dir !== dir || state.selected !== sessionId) return;
  renderFileMentionRows(model.tree.get(key), dir, currentFileFilter());
}
function currentFileFilter() {
  const ctx = mentionContext();
  if (!ctx || ctx.trigger !== "@") return mentionModel().query;
  return mentionPathParts(ctx.query).filter;
}
function renderFileMentionRows(cached, dir, filter) {
  renderMentionFrame("files", mentionHeadPath(dir));
  if (!cached) {
    showMentionNote(t("mention.loading"));
    return;
  }
  if (cached.error) {
    showMentionNote(t("mention.error_tree"));
    return;
  }
  const needle = String(filter || "").trim().toLowerCase();
  const entries = (cached.entries || []).filter((entry) => {
    if (!needle) return true;
    const name = String(entry.name || "").toLowerCase();
    const path = String(entry.path || "").toLowerCase();
    return name.startsWith(needle) || name.includes(needle) || path.includes(needle);
  }).slice(0, MENTION_MAX_ROWS);
  const rows = entries.map((entry) => ({
    kind: entry.type === "dir" ? "dir" : "file",
    path: String(entry.path || entry.name || ""),
    label: entry.type === "dir" ? String(entry.name) + "/" : String(entry.name),
    meta: entry.type === "dir" ? "" : formatBytes(Number(entry.size) || 0),
  }));
  renderMentionRows(rows);
}
function extractSkillTokens(text) {
  const ids = new Set();
  MENTION_SKILL_TOKEN.lastIndex = 0;
  let match = MENTION_SKILL_TOKEN.exec(text);
  while (match) {
    ids.add(match[2]);
    match = MENTION_SKILL_TOKEN.exec(text);
  }
  return ids;
}
function pruneTrackedSkills() {
  const tracked = mentionModel().skillsTracked;
  if (!tracked.size) return;
  const tokens = extractSkillTokens(ui.message.value);
  for (const id of Array.from(tracked.keys())) {
    if (!tokens.has(id)) tracked.delete(id);
  }
}
function trackedSkillIds(text) {
  const tokens = extractSkillTokens(text);
  return Array.from(mentionModel().skillsTracked.keys()).filter((id) => tokens.has(id));
}

const SECURITY_LEVELS = ["strict", "standard", "trusted", "full"];
const SECURITY_LABEL_KEYS = { strict: "level.strict", standard: "level.standard", trusted: "level.trusted", full: "level.full" };
let composerAccessPanel = null, composerAccessTrigger = null, composerAccessOpenTimer = 0, composerAccessCloseTimer = 0;
function closeComposerAccessLater(){clearTimeout(composerAccessCloseTimer);composerAccessCloseTimer=setTimeout(()=>{if(!composerAccessPanel?.contains(document.activeElement))composerAccessPanel?.hidePopover();},220);}
function createComposerAccessButton() {
  const button = element("button", "composer-access-button");
  button.type = "button";
  button.setAttribute("aria-haspopup", "dialog");
  button.setAttribute("aria-expanded", "false");
  button.addEventListener("click", () => openComposerAccess(button));
  button.addEventListener("pointerenter", event => {clearTimeout(composerAccessCloseTimer);if(event.pointerType!=="touch"&&matchMedia("(hover:hover)").matches)composerAccessOpenTimer=setTimeout(()=>openComposerAccess(button,true),180);});
  button.addEventListener("pointerleave", () => {clearTimeout(composerAccessOpenTimer);closeComposerAccessLater();});
  updateComposerAccessButton(button);
  return button;
}
function updateComposerAccessButton(button) {
  const model = settingsModel(), known = Boolean(model.security && model.approval);
  const level = known ? effectiveSecurityLevel(model.security) : "";
  const automatic = model.approval?.enabled === false;
  const icon = workspaceGlyph('access');
  icon.classList.add('composer-access-icon');
  button.replaceChildren(icon, element("span", "composer-access-label", known ? securityLabel(level) : (lang === "en" ? "Access" : "Доступ")), element("span", "composer-access-arrow", "⌄"));
  button.classList.toggle("automatic", automatic);
  button.classList.toggle("full", level === "full");
  button.title = (lang === "en" ? "Access and approvals" : "Доступ и подтверждения") + (known ? ": " + securityLabel(level) + " · " + (automatic ? (lang === "en" ? "automatic" : "автоматически") : (lang === "en" ? "ask permission" : "спрашивать")) : "");
  button.setAttribute("aria-label", button.title);
}
function syncComposerAccess() {
  document.querySelectorAll(".composer-access-button").forEach(updateComposerAccessButton);
  if (composerAccessPanel?.matches(":popover-open")) renderComposerAccess();
}
async function refreshComposerAccess() {
  const model = settingsModel();
  const generation = model.accessGeneration = (model.accessGeneration || 0) + 1;
  const result = await Promise.allSettled([api("/api/security-settings"), api("/api/approval-settings")]);
  if (generation !== model.accessGeneration || model.securityBusy || model.approvalBusy) return;
  if (result[0].status === "fulfilled") { model.security = result[0].value; model.securityError = ""; }
  else model.securityError = result[0].reason.message;
  if (result[1].status === "fulfilled") { model.approval = result[1].value; model.approvalError = ""; }
  else model.approvalError = result[1].reason.message;
  syncComposerAccess();
}
function positionComposerAccess() {
  if (!composerAccessTrigger?.isConnected || !composerAccessPanel?.matches(":popover-open")) return;
  const anchor = composerAccessTrigger.getBoundingClientRect(), panel = composerAccessPanel;
  const width = Math.min(240, window.innerWidth - 24);
  panel.style.width = width + "px";
  panel.style.left = Math.max(12, Math.min(anchor.left, window.innerWidth - width - 12)) + "px";
  panel.style.top = Math.max(12, anchor.top - panel.getBoundingClientRect().height - 8) + "px";
}
async function openComposerAccess(button, hover = false) {
  clearTimeout(composerAccessOpenTimer);clearTimeout(composerAccessCloseTimer);
  if (!composerAccessPanel) {
    composerAccessPanel = element("section", "composer-access-panel");
    composerAccessPanel.popover = "auto";
    composerAccessPanel.setAttribute("role", "dialog");
    composerAccessPanel.setAttribute("aria-label", lang === "en" ? "Access and approvals" : "Доступ и подтверждения");
    composerAccessPanel.addEventListener("toggle", event => {
      document.querySelectorAll(".composer-access-button").forEach(node => node.setAttribute("aria-expanded", String(event.newState === "open" && node === composerAccessTrigger)));
    });
    composerAccessPanel.addEventListener("pointerenter",()=>clearTimeout(composerAccessCloseTimer));
    composerAccessPanel.addEventListener("pointerleave",closeComposerAccessLater);
    document.body.append(composerAccessPanel);
  }
  if (composerAccessPanel.matches(":popover-open") && composerAccessTrigger === button) { if(!hover)composerAccessPanel.hidePopover(); return; }
  composerAccessTrigger = button;
  renderComposerAccess(); composerAccessPanel.showPopover(); positionComposerAccess();
  await refreshComposerAccess(); positionComposerAccess();
}
function renderComposerAccess() {
  const panel = composerAccessPanel, model = settingsModel();
  if (!panel) return;
  const english = lang === "en", current = effectiveSecurityLevel(model.security);
  panel.replaceChildren(element("strong", "access-heading", english ? "Access and approvals" : "Доступ и подтверждения"));
  panel.querySelector(".access-heading").title = english ? "Shared settings for all chats. Access applies to new tasks." : "Общие настройки для всех чатов. Уровень доступа — для новых задач.";
  const descriptions = english ? {
    strict:"Workspace · strict checks", standard:"Workspace · command checks", trusted:"Workspace · softer command checks", full:"Files outside workspace · your OS user permissions"
  } : { strict:"Рабочая папка · строгие проверки", standard:"Рабочая папка · проверки команд", trusted:"Рабочая папка · мягкие проверки команд", full:"Файлы вне рабочей папки · права пользователя ОС" };
  const list = element("div", "access-levels");
  for (const level of securityLevelOptions(model.security)) {
    const button = element("button", "access-level" + (model.security && current === level ? " selected" : ""));
    button.type = "button"; button.dataset.level = level;
    button.disabled = !model.security || model.securityBusy || model.approvalBusy;
    button.setAttribute("aria-pressed", String(Boolean(model.security && current === level)));
    button.title = descriptions[level];
    const copy = element("span"); copy.append(element("strong", "", securityLabel(level)));
    button.append(copy, element("span", "access-check", model.security && current === level ? "✓" : ""));
    button.addEventListener("click", () => {
      if (current === level) return;
      if (levelRank(level) > levelRank(model.security.baseline)) { model.accessPending = level; renderComposerAccess(); }
      else setSecurityLevel(level, false, model);
    }); list.append(button);
  }
  panel.append(list);
  const expiry = model.security && securityExpiryText(model.security);
  if (expiry) panel.append(element("p", "access-note", expiry));
  const approval = element("label", "access-approval"), check = element("input");
  check.type = "checkbox"; check.checked = model.approval?.enabled !== false;
  check.disabled = !model.approval || model.approvalBusy || model.securityBusy;
  check.addEventListener("change", () => setApprovals(check.checked));
  const description = element("span");
  description.append(element("strong", "", english ? "Ask permission" : "Спрашивать разрешение"), element("small", "", english ? "When off, Write/Edit and other gated actions run automatically. File access limits remain." : "Выключено: Write/Edit и другие подтверждаемые действия выполняются автоматически. Границы доступа к файлам сохраняются."));
  approval.title = description.querySelector("small").textContent;description.querySelector("small").remove();
  approval.append(check, description); panel.append(approval);
  if (model.accessPending) {
    const target = model.accessPending, strip = element("div", "access-confirm");
    strip.append(element("p", "access-note", securityLabel(target) + (english ? " for 1 hour. Confirm access change." : " на 1 час. Подтверди изменение доступа.")));
    const apply = element("button", "mini-button danger", english ? "Enable for 1 hour" : "Включить на 1 час"), cancel = element("button", "mini-button", t("dialog.cancel"));
    apply.type = cancel.type = "button"; apply.disabled = cancel.disabled = Boolean(model.securityBusy);
    apply.addEventListener("click", () => setSecurityLevel(target, true, model));
    cancel.addEventListener("click", () => { model.accessPending = ""; renderComposerAccess(); });
    strip.append(apply, cancel); panel.append(strip);
  }
  for (const error of [model.securityError, model.approvalError]) if (error) panel.append(element("p", "settings-error", error));
  positionComposerAccess();
}
const SANDBOX_REASON_KEYS = {
  env_disabled: "sandbox.reason_env", user_disabled: "sandbox.reason_user",
  bwrap_missing: "sandbox.reason_bwrap", namespace_blocked: "sandbox.reason_namespace",
  observe: "sandbox.reason_observe",
};
const securityLabel = (level) => t(SECURITY_LABEL_KEYS[normalizeLevel(level)]);
function normalizeLevel(value) {
  const level = String(value || "").trim().toLowerCase();
  return SECURITY_LEVELS.includes(level) ? level : "standard";
}
function levelRank(value) {
  return SECURITY_LEVELS.indexOf(normalizeLevel(value));
}
function nowSeconds() {
  return Math.floor(Date.now() / 1000);
}
function effectiveSecurityLevel(settings) {
  if (!settings) return "standard";
  const level = normalizeLevel(settings.level);
  const baseline = normalizeLevel(settings.baseline);
  if (levelRank(level) <= levelRank(baseline)) return level;
  return Number(settings.expires_at || 0) > nowSeconds() ? level : baseline;
}
function securityWeakened(settings) {
  if (!settings) return false;
  const weakened = Boolean(settings.is_weakened) || levelRank(settings.level) > levelRank(settings.baseline);
  return weakened && Number(settings.expires_at || 0) > nowSeconds();
}
function securityExpiryText(settings) {
  const expires = Number((settings || {}).expires_at || 0);
  if (!expires) return "";
  if (expires <= nowSeconds()) return t("settings.security.expired");
  const minutes = Math.max(1, Math.round((expires - nowSeconds()) / 60));
  const clock = new Intl.DateTimeFormat(intlLocale(), { hour: "2-digit", minute: "2-digit" }).format(new Date(expires * 1000));
  return minutes >= 60
    ? t("settings.security.expiry_hours", { h: Math.round(minutes / 60), clock })
    : t("settings.security.expiry_minutes", { m: minutes, clock });
}
function sandboxStatusText(settings) {
  if (!settings) return t("sandbox.unknown");
  const reason = String(settings.reason || "").trim().toLowerCase();
  if (reason === "observe") return t("sandbox.observe");
  if (!settings.available || reason === "bwrap_missing" || reason === "namespace_blocked") {
    const reasonText = SANDBOX_REASON_KEYS[reason] ? t(SANDBOX_REASON_KEYS[reason]) : (reason || t("sandbox.reason_unknown"));
    return t("sandbox.unavailable", { reason: reasonText });
  }
  if (!settings.enabled || reason === "user_disabled" || reason === "env_disabled") {
    return t("sandbox.off");
  }
  return t("sandbox.active");
}
function switchButton(on, disabled, onToggle) {
  const button = element("button", "switch" + (on ? " on" : ""));
  button.type = "button";
  button.setAttribute("role", "switch");
  button.setAttribute("aria-checked", String(Boolean(on)));
  button.disabled = Boolean(disabled);
  button.addEventListener("click", onToggle);
  return button;
}
function settingsModel() {
  if (!state.settings) state.settings = { profile: "", busy: false };
  return state.settings;
}
function drawerCard(title, stateText, stateClass) {
  const card = element("section", "settings-card");
  const head = element("div", "settings-card-head");
  head.append(element("h3", "", title));
  if (stateText) head.append(element("span", "settings-state" + (stateClass ? " " + stateClass : ""), stateText));
  card.append(head);
  return card;
}
function openSettings() {
  if (!ui.settingsDialog.open) ui.settingsDialog.showModal();
  startSettingsRefresh();
  loadSettings().catch((error) => toast(error.message, "error"));
}
function startSettingsRefresh() {
  window.clearInterval(state.settingsTimer);
  state.settingsTimer = window.setInterval(refreshSecuritySettings, 60000);
}
async function refreshSecuritySettings() {
  if (!ui.settingsDialog.open) return;
  const model = settingsModel();
  const before = JSON.stringify([model.security, model.sandbox]);
  await Promise.all([
    api("/api/security-settings").then((value) => { model.security = value; }).catch(() => {}),
    api("/api/sandbox-settings").then((value) => { model.sandbox = value; }).catch(() => {}),
  ]);
  if (JSON.stringify([model.security, model.sandbox]) !== before) renderSettings();
}
async function loadSettings(profile) {
  const model = settingsModel();
  if (profile !== undefined) model.profile = profile;
  model.busy = true;
  renderSettings();
  await Promise.all([
    (window.IgorekDesktop?.releaseInfo ? window.IgorekDesktop.releaseInfo() : api("/api/about")).then(value=>{model.release=value;}).catch(()=>{}),
    api("/api/session-options").then((value) => { model.options = value; model.optionsError = ""; }).catch((error) => { model.optionsError = error.message; }),
    api("/api/approval-settings").then((value) => { model.approval = value; model.approvalError = ""; }).catch((error) => { model.approvalError = error.message; }),
    api("/api/security-settings").then((value) => { model.security = value; model.securityError = ""; }).catch((error) => { model.securityError = error.message; }),
    api("/api/sandbox-settings").then((value) => { model.sandbox = value; model.sandboxError = ""; }).catch((error) => { model.sandboxError = error.message; }),
    api("/api/devices").then((value) => { model.devices = value; model.devicesError = ""; }).catch((error) => { model.devicesError = error.message; }),
  ]);
  if (!model.profile) {
    const options = model.options || {};
    model.profile = options.default_profile || (options.profiles || [])[0] || "";
  }
  if (!window.IgorekDesktop?.plugins) {
    try {
      model.extensions = await api("/api/extensions" + (model.profile ? "?profile=" + encode(model.profile) : ""));
      model.extensionsError = "";
    } catch (error) {
      model.extensionsError = error.message;
    }
  }
  model.busy = false;
  renderSettings();
}
function renderSettings() {
  const model = settingsModel();
  syncComposerAccess();
  const fragment = document.createDocumentFragment();
  if(model.release){const release=element('details','settings-release'),summary=element('summary','','Игорёк '+model.release.version+' · Что нового'),list=element('ul');for(const text of model.release.changes||[])list.append(element('li','',text));release.append(summary,list);fragment.append(release);}
  fragment.append(renderLanguageSettings());
  fragment.append(renderProfileSettings());
  fragment.append(renderApprovalSettings(model));
  fragment.append(renderSecuritySettings(model));
  fragment.append(renderSandboxSettings(model));
  fragment.append(renderDevicesSettings(model));
  if (!window.IgorekDesktop?.plugins) {
    fragment.append(renderExtensionsSettings(model));
    fragment.append(renderMcpSettings(model));
  }
  ui.settingsBody.replaceChildren(fragment);
}
function renderProfileSettings() {
  const model = profileModel();
  const card = drawerCard(t("settings.card.profile"), model.name || t("settings.profile.local"));
  card.append(element("p", "settings-note", t("settings.profile.note")));
  const row = element("div", "profile-row");
  const preview = element("span", "profile-avatar");
  if (model.avatar) {
    const photo = document.createElement("img");
    photo.src = model.avatar;
    photo.alt = "";
    preview.append(photo);
  } else preview.textContent = t("message.user_letter");
  const fields = element("div", "profile-fields");
  const name = element("input", "profile-name-input");
  name.type = "text";
  name.value = model.name;
  name.maxLength = 60;
  name.placeholder = t("settings.profile.name_placeholder");
  name.setAttribute("aria-label", t("settings.profile.name_aria"));
  const letter = t("message.user_letter");
  fields.append(name, element("span", "profile-hint", model.avatar
    ? t("settings.profile.avatar_set", { letter })
    : t("settings.profile.avatar_none", { letter })));
  row.append(preview, fields);
  card.append(row);
  const actions = element("div", "settings-actions");
  const nameSave = element("button", "mini-button", t("settings.profile.save_name"));
  nameSave.type = "button";
  nameSave.addEventListener("click", () => {
    const value = name.value.trim().slice(0, 60);
    if (!saveProfile({ ...profileModel(), name: value })) {
      toast(t("settings.profile.storage_full"));
      return;
    }
    toast(t("settings.profile.name_saved"));
  });
  const pick = element("button", "mini-button", t("settings.profile.upload"));
  pick.type = "button";
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/png,image/jpeg,image/webp,image/svg+xml";
  input.hidden = true;
  input.addEventListener("change", () => {
    handleProfileAvatar((input.files || [])[0]);
    input.value = "";
  });
  pick.addEventListener("click", () => input.click());
  actions.append(nameSave, pick, input);
  if (model.avatar) {
    const remove = element("button", "mini-button danger", t("settings.profile.remove_avatar"));
    remove.type = "button";
    remove.addEventListener("click", () => {
      if (!saveProfile({ ...profileModel(), avatar: "" })) {
        toast(t("settings.profile.storage_full"));
        return;
      }
      toast(t("settings.profile.avatar_removed"));
    });
    actions.append(remove);
  }
  card.append(actions);
  return card;
}
function renderApprovalSettings(model) {
  const enabled = model.approval ? Boolean(model.approval.enabled) : false;
  const card = drawerCard(
    t("settings.approval.card"),
    model.approval ? (enabled ? t("settings.state.on") : t("settings.state.off")) : "…",
    model.approval ? (enabled ? "on" : "danger") : "",
  );
  card.append(element("p", "settings-note", enabled
    ? t("settings.approval.note_on")
    : t("settings.approval.note_off")));
  if (model.approvalError) card.append(element("p", "settings-error", model.approvalError));
  const actions = element("div", "settings-actions");
  actions.append(switchButton(enabled, !model.approval || model.approvalBusy, () => setApprovals(!enabled)));
  actions.append(element("span", "settings-state", enabled ? t("settings.approval.turn_off") : t("settings.approval.turn_on")));
  card.append(actions);
  return card;
}
async function setApprovals(enabled) {
  const model = settingsModel();
  model.accessGeneration = (model.accessGeneration || 0) + 1;
  model.approvalBusy = true;
  renderSettings();
  try {
    model.approval = await api("/api/approval-settings", { method: "PATCH", body: JSON.stringify({ enabled }) });
    model.approvalError = "";
    toast(enabled ? t("settings.approval.enabled") : t("settings.approval.disabled"));
  } catch (error) {
    model.approvalError = error.message;
    toast(error.message, "error");
  }
  model.approvalBusy = false;
  renderSettings();
}
function renderSecuritySettings(model) {
  const settings = model.security;
  const current = effectiveSecurityLevel(settings);
  const weakened = securityWeakened(settings);
  const stateKey = weakened ? "settings.security.state_weakened"
    : current === "full" ? "settings.security.state_full" : "settings.security.state_baseline";
  const card = drawerCard(
    t("settings.security.card"),
    settings ? t(stateKey) : "…",
    settings ? (weakened || current === "full" ? "danger" : "on") : "",
  );
  const line = element("p", "settings-note");
  line.textContent = weakened
    ? t("settings.security.current_weakened", { level: securityLabel(current) })
    : t("settings.security.current", { level: securityLabel(current) });
  card.append(line);
  const expiry = settings ? securityExpiryText(settings) : "";
  if (expiry) card.append(element("p", "settings-note", expiry));
  card.append(element("p", "settings-note", t("settings.security.note")));
  if (model.securityError) card.append(element("p", "settings-error", model.securityError));
  const baseline = settings ? normalizeLevel(settings.baseline) : "standard";
  const levels = securityLevelOptions(settings);
  const list = element("div", "level-list");
  for (const level of levels) {
    const isCurrent = level === current;
    const isWeakening = levelRank(level) > levelRank(baseline);
    const button = element("button", "level-button" + (isCurrent ? " current" : "") + (isWeakening ? " danger" : ""));
    button.type = "button";
    button.disabled = isCurrent || model.securityBusy;
    button.append(element("span", "", securityLabel(level)));
    const tagKey = isCurrent ? "settings.security.tag_current"
      : isWeakening ? "settings.security.tag_weaken" : "settings.security.tag_strengthen";
    button.append(element("span", "level-tag", t(tagKey)));
    if (!isCurrent) button.addEventListener("click", () => requestSecurityLevel(level, baseline, model));
    list.append(button);
  }
  card.append(list);
  if (model.securityPending) card.append(securityConfirmStrip(model.securityPending, current, model));
  return card;
}
function securityLevelOptions(settings) {
  const available = ((settings || {}).available_levels || [])
    .map((level) => normalizeLevel(level))
    .filter((level, index, list) => SECURITY_LEVELS.includes(level) && list.indexOf(level) === index);
  return available.length ? available : SECURITY_LEVELS;
}
function securityConfirmStrip(target, current, model) {
  const strip = element("div", "confirm-strip");
  strip.append(element("p", "", target === "full"
    ? t("settings.security.confirm_full")
    : t("settings.security.confirm_partial")));
  strip.append(element("p", "", t("settings.security.confirm_line", {
    from: securityLabel(current), to: securityLabel(target),
  })));
  const actions = element("div", "confirm-actions");
  const confirm = element("button", "danger-button", model.securityBusy ? t("settings.security.sending") : t("settings.security.confirm"));
  confirm.type = "button";
  confirm.disabled = Boolean(model.securityBusy);
  confirm.addEventListener("click", () => setSecurityLevel(target, true, model));
  const cancel = element("button", "secondary-button", t("dialog.cancel"));
  cancel.type = "button";
  cancel.disabled = Boolean(model.securityBusy);
  cancel.addEventListener("click", () => { model.securityPending = ""; renderSettings(); });
  actions.append(confirm, cancel);
  strip.append(actions);
  return strip;
}
function requestSecurityLevel(level, baseline, model) {
  if (levelRank(level) > levelRank(baseline)) {
    model.securityPending = level;
    renderSettings();
    return;
  }
  setSecurityLevel(level, false, model);
}
async function setSecurityLevel(level, confirm, model) {
  model.accessGeneration = (model.accessGeneration || 0) + 1;
  model.securityBusy = true;
  renderSettings();
  try {
    model.security = await api("/api/security-settings", {
      method: "PATCH", body: JSON.stringify({ level, confirm }),
    });
    model.securityError = "";
    model.securityPending = "";
    model.accessPending = "";
    toast(t("settings.security.updated"));
  } catch (error) {
    model.securityError = error.message;
    toast(error.message, "error");
  }
  model.securityBusy = false;
  renderSettings();
}
function renderSandboxSettings(model) {
  const settings = model.sandbox;
  const enabled = settings ? Boolean(settings.enabled) : false;
  const active = settings ? Boolean(settings.active) : false;
  const stateKey = active ? "sandbox.state_active" : enabled ? "sandbox.state_waiting" : "sandbox.state_off";
  const card = drawerCard(
    t("sandbox.card"),
    settings ? t(stateKey) : "…",
    settings ? (active ? "on" : "warn") : "",
  );
  card.append(element("p", "settings-note", sandboxStatusText(settings)));
  if (model.sandboxError) card.append(element("p", "settings-error", model.sandboxError));
  const actions = element("div", "settings-actions");
  const canToggle = Boolean(settings) && (enabled || Boolean(settings.available)) && !model.sandboxBusy;
  actions.append(switchButton(enabled, !canToggle, () => requestSandbox(!enabled, model)));
  actions.append(element("span", "settings-state", enabled ? t("settings.approval.turn_off") : t("settings.approval.turn_on")));
  card.append(actions);
  if (model.sandboxPending) {
    const strip = element("div", "confirm-strip");
    strip.append(element("p", "", t("sandbox.warning")));
    const controls = element("div", "confirm-actions");
    const confirm = element("button", "danger-button", t("sandbox.disable"));
    confirm.type = "button";
    confirm.disabled = Boolean(model.sandboxBusy);
    confirm.addEventListener("click", () => setSandbox(false, model));
    const cancel = element("button", "secondary-button", t("dialog.cancel"));
    cancel.type = "button";
    cancel.disabled = Boolean(model.sandboxBusy);
    cancel.addEventListener("click", () => { model.sandboxPending = false; renderSettings(); });
    controls.append(confirm, cancel);
    strip.append(controls);
    card.append(strip);
  }
  return card;
}
function requestSandbox(enabled, model) {
  const settings = model.sandbox;
  if (!enabled && settings && settings.enabled) {
    model.sandboxPending = true;
    renderSettings();
    return;
  }
  setSandbox(enabled, model);
}
async function setSandbox(enabled, model) {
  model.sandboxBusy = true;
  renderSettings();
  try {
    model.sandbox = await api("/api/sandbox-settings", { method: "PATCH", body: JSON.stringify({ enabled }) });
    model.sandboxError = "";
    model.sandboxPending = false;
    toast(enabled ? t("sandbox.enabled_toast") : t("sandbox.disabled_toast"));
  } catch (error) {
    model.sandboxError = error.message;
    toast(error.message, "error");
  }
  model.sandboxBusy = false;
  renderSettings();
}
function renderDevicesSettings(model) {
  const payload = model.devices || {};
  const devices = Array.isArray(payload.devices) ? payload.devices : [];
  const currentId = String(payload.current_device_id || "");
  const revoked = devices.filter((item) => item.revoked_at);
  const card = drawerCard(t("devices.card"), model.devices ? String(devices.length) : "…");
  card.append(element("p", "settings-note", t("devices.note")));
  if (model.devicesError) card.append(element("p", "settings-error", model.devicesError));
  if (revoked.length) {
    const actions = element("div", "settings-actions");
    const purge = element("button", "mini-button danger", t("devices.purge", { n: revoked.length }));
    purge.type = "button";
    purge.addEventListener("click", () => { model.purgePending = true; renderSettings(); });
    actions.append(purge);
    card.append(actions);
  }
  if (model.purgePending) card.append(purgeConfirmStrip(model, revoked.length));
  if (model.devicePending) card.append(deviceDeleteConfirmStrip(model, devices));
  const rows = element("div", "device-rows");
  for (const device of devices) {
    const row = element("div", "device-row");
    const copy = element("div", "item-copy");
    copy.append(element("span", "item-name", device.name || t("devices.name_missing")));
    const meta = [
      device.platform ? String(device.platform) : "web",
      device.device_id === currentId ? t("devices.this_device") : "",
      device.revoked_at ? t("devices.revoked") : t("devices.active"),
    ].filter(Boolean).join(" · ");
    copy.append(element("span", "item-meta", meta));
    row.append(copy);
    if (device.device_id === currentId) {
      row.append(element("span", "item-chip", t("devices.current_chip")));
    } else if (device.revoked_at) {
      const remove = element("button", "mini-button danger", t("devices.remove"));
      remove.type = "button";
      remove.addEventListener("click", () => { model.devicePending = device.device_id; renderSettings(); });
      row.append(remove);
    } else {
      const revoke = element("button", "mini-button", t("devices.revoke"));
      revoke.type = "button";
      revoke.addEventListener("click", () => revokeDevice(device));
      row.append(revoke);
    }
    rows.append(row);
  }
  if (!devices.length && !model.devicesError) rows.append(element("p", "settings-note", t("devices.none")));
  card.append(rows);
  return card;
}
function purgeConfirmStrip(model, count) {
  const strip = element("div", "confirm-strip");
  strip.append(element("p", "", t(count === 1 ? "devices.purge_confirm_one" : "devices.purge_confirm_many", { n: count })));
  const controls = element("div", "confirm-actions");
  const confirm = element("button", "danger-button", t("devices.delete"));
  confirm.type = "button";
  confirm.addEventListener("click", () => { model.purgePending = false; purgeDevices(); });
  const cancel = element("button", "secondary-button", t("dialog.cancel"));
  cancel.type = "button";
  cancel.addEventListener("click", () => { model.purgePending = false; renderSettings(); });
  controls.append(confirm, cancel);
  strip.append(controls);
  return strip;
}
function deviceDeleteConfirmStrip(model, devices) {
  const target = devices.find((item) => item.device_id === model.devicePending) || {};
  const strip = element("div", "confirm-strip");
  strip.append(element("p", "", t("devices.delete_confirm", { name: target.name || model.devicePending })));
  const controls = element("div", "confirm-actions");
  const confirm = element("button", "danger-button", t("devices.delete"));
  confirm.type = "button";
  confirm.addEventListener("click", () => { const id = model.devicePending; model.devicePending = ""; deleteDevice(id); });
  const cancel = element("button", "secondary-button", t("dialog.cancel"));
  cancel.type = "button";
  cancel.addEventListener("click", () => { model.devicePending = ""; renderSettings(); });
  controls.append(confirm, cancel);
  strip.append(controls);
  return strip;
}
async function refreshDevices() {
  const model = settingsModel();
  try {
    model.devices = await api("/api/devices");
    model.devicesError = "";
  } catch (error) {
    model.devicesError = error.message;
  }
  renderSettings();
}
async function revokeDevice(device) {
  try {
    await api("/api/devices/" + encode(device.device_id), { method: "DELETE" });
    toast(t("devices.revoked_toast"));
  } catch (error) {
    toast(error.message, "error");
  }
  await refreshDevices();
}
async function deleteDevice(deviceId) {
  try {
    await api("/api/devices/" + encode(deviceId) + "?purge=1", { method: "DELETE" });
    toast(t("devices.deleted_toast"));
  } catch (error) {
    toast(error.message, "error");
  }
  await refreshDevices();
}
async function purgeDevices() {
  try {
    const result = await api("/api/devices/purge-revoked", { method: "POST" });
    toast(t("devices.purged_toast", { n: Number((result && result.deleted) || 0) }));
  } catch (error) {
    toast(error.message, "error");
  }
  await refreshDevices();
}
function renderExtensionsSettings(model, handlers = {}) {
  const payload = model.extensions || {};
  const skills = Array.isArray(payload.skills) ? payload.skills : [];
  const profiles = ((model.options || {}).profiles || []).slice();
  if (model.profile && !profiles.includes(model.profile)) profiles.unshift(model.profile);
  const card = drawerCard(t("skills.card"), model.extensions ? String(skills.length) : "…");
  card.append(element("p", "settings-note", t("skills.note")));
  const select = element("select", "profile-select");
  select.setAttribute("aria-label", t("skills.select_aria"));
  if (!profiles.length) select.append(element("option", "", t("skills.no_profile")));
  for (const name of profiles) {
    const option = element("option", "", name);
    option.value = name;
    select.append(option);
  }
  select.value = model.profile;
  select.addEventListener("change", () => (handlers.reload || loadSettings)(select.value));
  if (handlers.profileSelector !== false) card.append(select);
  if (model.extensionsError) card.append(element("p", "settings-error", model.extensionsError));
  const rows = element("div", "skill-rows");
  for (const skill of skills) {
    const row = element("div", "skill-row");
    const copy = element("div", "item-copy");
    copy.append(element("span", "item-name", skill.name || skill.skill_id));
    copy.append(element("span", "item-meta", skill.description || skill.category || skill.skill_id));
    row.append(copy);
    if (!skill.supported) {
      row.append(element("span", "item-chip quiet", t("skills.not_supported")));
    } else {
      const toggle = switchButton(Boolean(skill.enabled), handlers.compact ? model.busy || Boolean(model.skillBusy) : model.skillBusy === skill.skill_id, () => toggleSkill(skill, model, handlers.changed));
      const name = skill.name || skill.skill_id;
      toggle.setAttribute("aria-label", t(skill.enabled ? "skills.disable" : "skills.enable", { name }));
      row.append(toggle);
    }
    rows.append(row);
  }
  if (!skills.length && !model.extensionsError) {
    rows.append(element("p", "settings-note", t("skills.none")));
  }
  card.append(rows);
  return card;
}
function renderMcpSettings(model) {
  const payload = model.extensions || {};
  const servers = Array.isArray(payload.mcp_servers) ? payload.mcp_servers : [];
  const card = drawerCard(t("mcp.card"), model.extensions ? String(servers.length) : "…");
  card.append(element("p", "settings-note", t("mcp.note")));
  if (model.extensionsError) card.append(element("p", "settings-error", model.extensionsError));
  const rows = element("div", "skill-rows");
  for (const server of servers) {
    const row = element("div", "skill-row");
    const copy = element("div", "item-copy");
    copy.append(element("span", "item-name", server.name || t("mcp.default_name")));
    const meta = [];
    if (server.description) meta.push(String(server.description));
    const capabilities = Array.isArray(server.capabilities) ? server.capabilities : [];
    if (capabilities.length) meta.push(t("mcp.capabilities", { list: capabilities.join(", ") }));
    const tools = Array.isArray(server.tools_hint) ? server.tools_hint : [];
    if (tools.length) meta.push(t("mcp.tools", { list: tools.join(", ") }));
    copy.append(element("span", "item-meta", meta.join(" · ") || t("mcp.no_description")));
    row.append(copy);
    const enabled = Boolean(server.enabled);
    row.append(element("span", "item-chip" + (enabled ? "" : " quiet"), t(enabled ? "mcp.enabled" : "mcp.disabled")));
    rows.append(row);
  }
  if (!servers.length && !model.extensionsError) {
    rows.append(element("p", "settings-note", t("mcp.none")));
  }
  card.append(rows);
  return card;
}
async function toggleSkill(skill, model, changed = renderSettings) {
  const payload = model.extensions, profile = model.profile;
  model.skillBusy = skill.skill_id;
  changed();
  try {
    const result = await api("/api/extensions/skills/" + encode(skill.skill_id), {
      method: "PATCH", body: JSON.stringify({ enabled: !skill.enabled }),
    });
    const list = (payload && payload.skills) || [];
    const index = list.findIndex((item) => item.skill_id === skill.skill_id);
    if (index >= 0 && result && result.skill) list[index] = result.skill;
    if (model.extensions === payload && model.profile === profile) model.extensionsError = "";
  } catch (error) {
    if (model.extensions === payload && model.profile === profile) model.extensionsError = error.message;
    toast(error.message, "error");
  }
  model.skillBusy = "";
  changed();
}

function dashboardModel() {
  if (!state.dashboard) state.dashboard = {};
  return state.dashboard;
}
function openDashboard() {
  if (!ui.dashboardDialog.open) ui.dashboardDialog.showModal();
  loadDashboard().catch((error) => toast(error.message, "error"));
}
async function loadDashboard() {
  const model = dashboardModel();
  model.busy = true;
  renderDashboard();
  await Promise.all([
    api("/api/status").then((value) => { model.status = value; model.statusError = ""; }).catch((error) => { model.statusError = error.message; }),
    api("/api/about").then((value) => { model.about = value; model.aboutError = ""; }).catch((error) => { model.aboutError = error.message; }),
    api("/api/session-options").then((value) => { model.options = value; model.optionsError = ""; }).catch((error) => { model.optionsError = error.message; }),
  ]);
  if (model.status ? model.status.paired : false) {
    try {
      model.devices = await api("/api/devices");
      model.devicesError = "";
    } catch (error) {
      model.devicesError = error.message;
    }
  }
  await loadSessions().catch(() => {});
  model.busy = false;
  renderDashboard();
}
function renderDashboard() {
  const model = dashboardModel();
  const paired = model.status ? Boolean(model.status.paired) : false;
  const fragment = document.createDocumentFragment();
  const grid = element("div", "dash-grid");

  const connection = element("section", "dash-card");
  connection.append(element("span", "dash-label", t("dashboard.connection")));
  connection.append(element("strong", "", paired ? t("dashboard.connected") : t("dashboard.disconnected")));
  connection.append(element("span", "dash-value", paired
    ? t("dashboard.device", { name: (model.status.device && model.status.device.name) || t("dashboard.local_browser") })
    : t("dashboard.connect_hint")));
  connection.append(element("span", "dash-value mono", "HTTPS · LOCAL"));
  if (model.statusError) connection.append(element("span", "settings-error", model.statusError));
  grid.append(connection);

  const runtime = element("section", "dash-card");
  runtime.append(element("span", "dash-label", t("dashboard.runtime")));
  runtime.append(element("strong", "", t("brand.name") + " " + ((model.about && model.about.version) || "…")));
  const about = model.about || {};
  const bridge = about.bridge_host
    ? about.bridge_scheme + "://" + about.bridge_host + (about.bridge_port ? ":" + about.bridge_port : "")
    : (model.aboutError || t("dashboard.bridge_unknown"));
  runtime.append(element("span", "dash-value mono", bridge));
  grid.append(runtime);

  const options = model.options || {};
  const sessionMeta = (state.session && state.session.meta) || {};
  const profile = sessionMeta.profile || options.default_profile || t("dashboard.not_selected");
  const cwd = sessionMeta.cwd || options.default_cwd || (options.workdirs || [])[0] || t("session.cwd_missing");
  const profileCard = element("section", "dash-card");
  profileCard.append(element("span", "dash-label", t("dashboard.profile_folder")));
  profileCard.append(element("strong", "", profile));
  profileCard.append(element("span", "dash-value mono", cwd));
  if (model.optionsError) profileCard.append(element("span", "settings-error", model.optionsError));
  grid.append(profileCard);

  const devices = (model.devices && Array.isArray(model.devices.devices)) ? model.devices.devices : null;
  const counts = element("section", "dash-card");
  counts.append(element("span", "dash-label", t("dashboard.counters")));
  counts.append(element("strong", "", t("dashboard.sessions", { n: state.sessions.length })));
  counts.append(element("span", "dash-value", devices
    ? t("dashboard.devices", { n: devices.length, k: devices.filter((item) => item.revoked_at).length })
    : (model.devicesError || t("dashboard.devices_none"))));
  grid.append(counts);
  fragment.append(grid);

  const actions = element("div", "dash-actions");
  const refresh = element("button", "secondary-button", t("logs.refresh"));
  refresh.type = "button";
  refresh.disabled = Boolean(model.busy);
  refresh.addEventListener("click", () => loadDashboard().catch((error) => toast(error.message, "error")));
  actions.append(refresh);
  if (paired) {
    const logsButton = element("button", "secondary-button", t("sidebar.logs"));
    logsButton.type = "button";
    logsButton.title = t("dashboard.logs_title");
    logsButton.addEventListener("click", () => { ui.dashboardDialog.close(); openLogs(); });
    const agentsButton = element("button", "secondary-button", t("sidebar.agents"));
    agentsButton.type = "button";
    agentsButton.title = t("dashboard.agents_title");
    agentsButton.addEventListener("click", () => { ui.dashboardDialog.close(); openAgents(); });
    const settingsButton = element("button", "secondary-button", t("topbar.settings_title"));
    settingsButton.type = "button";
    settingsButton.addEventListener("click", () => { ui.dashboardDialog.close(); openSettings(); });
    const copy = element("button", "secondary-button", t("dashboard.copy"));
    copy.type = "button";
    copy.addEventListener("click", () => copyDiagnostics(model).catch((error) => toast(error.message, "error")));
    const disconnect = element("button", "danger-button", t("sidebar.logout"));
    disconnect.type = "button";
    disconnect.addEventListener("click", async () => {
      ui.dashboardDialog.close();
      await logout();
    });
    actions.append(logsButton, agentsButton, settingsButton, copy, disconnect);
  }
  fragment.append(actions);
  ui.dashboardBody.replaceChildren(fragment);
}
async function copyDiagnostics(model) {
  const about = model.about || {};
  const bridge = about.bridge_host
    ? about.bridge_scheme + "://" + about.bridge_host + (about.bridge_port ? ":" + about.bridge_port : "")
    : t("dashboard.unknown");
  const lines = [
    t("dashboard.diag_version", { v: about.version || t("dashboard.unknown") }),
    t("dashboard.diag_bridge", { url: bridge }),
    t("dashboard.diag_device", {
      name: (model.status && model.status.device && model.status.device.name) || t("dashboard.local_browser_lower"),
    }),
    t("dashboard.diag_sessions", { n: state.sessions.length }),
    t("dashboard.diag_profile", {
      p: (state.session && state.session.meta && state.session.meta.profile)
        || (model.options && model.options.default_profile) || t("dashboard.not_selected"),
    }),
  ];
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(lines.join("\n"));
    toast(t("dashboard.copied"));
  } else {
    toast(t("dashboard.clipboard_unavailable"));
  }
}

const LOG_STATUS_KEYS = {
  active: "logs.status.active", inactive: "logs.status.inactive", failed: "logs.status.failed",
  activating: "logs.status.activating", deactivating: "logs.status.deactivating",
  reloading: "logs.status.reloading", maintenance: "logs.status.maintenance",
  "not-found": "logs.status.not_found", unknown: "logs.status.unknown",
};
const LOG_STATUS_CLASSES = { active: "on", failed: "danger", "not-found": "quiet", unknown: "warn" };
const LOGS_AUTO_MS = 3000;
const LOGS_PAGE_LINES = 200;
const LOGS_STATUS_EVERY = 5;
let logsAutoSwitch = null;
let logsExpandButton = null;

function logsModel() {
  return state.logs;
}
function logsUnit(key) {
  return state.logs.units.find((item) => item.key === key) || null;
}
function openLogs() {
  applyLogsSize();
  if (!ui.logsDialog.open) ui.logsDialog.showModal();
  const model = logsModel();
  model.tick = 0;
  model.status = "loading";
  renderLogs();
  logsRefresh().catch((error) => toast(error.message, "error"));
}
function closeLogs() {
  persistLogsSize();
  if (ui.logsDialog.open) ui.logsDialog.close();
  stopLogsAuto();
}
function startLogsAuto() {
  stopLogsAuto();
  state.logs.timer = window.setInterval(() => {
    const model = logsModel();
    model.tick += 1;
    if (model.tick % LOGS_STATUS_EVERY === 1) loadLogsStatus().catch(() => {});
    loadLogTail(true).catch(() => {});
  }, LOGS_AUTO_MS);
}
function stopLogsAuto() {
  window.clearInterval(state.logs.timer);
  state.logs.timer = 0;
}
function toggleLogsAuto() {
  const model = logsModel();
  model.auto = !model.auto;
  if (model.auto) startLogsAuto();
  else stopLogsAuto();
  renderLogs();
}
async function logsRefresh() {
  await loadLogsStatus();
  await loadLogTail();
}
async function loadLogsStatus() {
  const model = logsModel();
  try {
    const payload = await api("/api/logs/status");
    model.units = Array.isArray(payload.units) ? payload.units : [];
    model.statusError = String(payload.error || "");
    if (!logsUnit(model.active)) model.active = model.units.length ? model.units[0].key : "";
  } catch (error) {
    model.units = [];
    model.statusError = error.message;
  }
  renderLogs();
}
async function loadLogTail(autoTick) {
  const model = logsModel();
  if (!model.active) {
    renderLogs();
    return;
  }
  const key = model.active;
  if (!autoTick) {
    model.status = "loading";
    model.error = "";
    renderLogs();
  }
  try {
    const payload = await api("/api/logs?unit=" + encode(key) + "&lines=" + LOGS_PAGE_LINES);
    if (model.active !== key) return;
    model.rows = Array.isArray(payload.lines) ? payload.lines : [];
    model.truncated = Boolean(payload.truncated);
    model.unit = String(payload.unit || "");
    model.status = "ready";
    model.error = "";
    model.updated = Date.now();
  } catch (error) {
    if (model.active !== key) return;
    const firstError = model.status !== "error";
    model.status = "error";
    model.error = error.message;
    if (firstError) toast(error.message, "error");
  }
  renderLogs();
}
function selectLogUnit(key) {
  const model = logsModel();
  if (model.active === key) return;
  model.active = key;
  model.rows = [];
  model.unit = "";
  model.truncated = false;
  model.status = "loading";
  model.tick = 0;
  renderLogs();
  loadLogTail().catch(() => {});
}
function renderLogs() {
  const model = logsModel();
  ui.logsTabs.replaceChildren();
  for (const unit of model.units) {
    const active = unit.key === model.active;
    const badgeClass = LOG_STATUS_CLASSES[unit.active] || "quiet";
    const tab = element("button", "logs-tab" + (active ? " active" : ""));
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(active));
    tab.title = unit.unit + (unit.since ? t("logs.started", { value: unit.since }) : "");
    tab.append(
      element("span", "logs-dot " + badgeClass),
      element("span", "logs-tab-name", unit.key),
      element("span", "logs-badge " + badgeClass, LOG_STATUS_KEYS[unit.active] ? t(LOG_STATUS_KEYS[unit.active]) : unit.active),
    );
    tab.addEventListener("click", () => selectLogUnit(unit.key));
    ui.logsTabs.append(tab);
  }
  if (!model.units.length) ui.logsTabs.append(element("span", "logs-tabs-empty", t("logs.tabs_empty")));
  const current = logsUnit(model.active);
  const caption = [current ? current.unit : (model.unit || t("logs.no_service"))];
  if (model.status === "ready") caption.push(t("logs.last_lines", { n: model.rows.length }));
  if (model.truncated) caption.push(t("logs.older"));
  if (model.status === "loading" || model.status === "idle") caption.push(t("logs.updating"));
  if (model.status === "ready" && model.updated) caption.push(t("logs.updated", { time: localClock(model.updated) }));
  ui.logsCaption.textContent = caption.join(" · ");
  ui.logsRefresh.disabled = model.status === "loading";
  if (logsAutoSwitch) {
    logsAutoSwitch.classList.toggle("on", model.auto);
    logsAutoSwitch.setAttribute("aria-checked", String(model.auto));
  }
  if (logsExpandButton) {
    const size = logsSizeModel();
    logsExpandButton.textContent = size.expanded ? t("logs.collapse") : t("logs.expand");
    logsExpandButton.setAttribute("aria-pressed", String(size.expanded));
    logsExpandButton.title = size.expanded ? t("logs.collapse_title") : t("logs.expand_title");
  }
  const errorText = model.statusError || (model.status === "error" ? model.error : "");
  ui.logsError.hidden = !errorText;
  ui.logsError.textContent = errorText;
  const atBottom = ui.logsOutput.scrollTop + ui.logsOutput.clientHeight >= ui.logsOutput.scrollHeight - 24;
  let text = "";
  let className = "logs-output";
  if (model.status === "error") {
    text = model.error || t("logs.read_failed");
    className += " muted";
  } else if (!model.rows.length) {
    text = model.status === "loading" || model.status === "idle" ? t("logs.loading") : t("logs.no_records");
    className += " muted";
  } else {
    text = model.rows.join("\n");
  }
  ui.logsOutput.className = className;
  ui.logsOutput.textContent = text;
  if (model.status === "ready" && atBottom) ui.logsOutput.scrollTop = ui.logsOutput.scrollHeight;
}

const EXTERNAL_SOURCE_TITLES = { claude: "Claude Code", opencode: "OpenCode", codex: "Codex" };
const EXTERNAL_SOURCE_ORDER = ["claude", "opencode", "codex"];
const AGENTS_PAGE_SIZE = 50;
const AGENTS_PREVIEW_LIMIT = 150;
const EXTERNAL_TOOL_TITLE_KEYS = { tool_result: "agents.chip.tool_result", web_search: "agents.chip.web_search", attachment: "agents.chip.attachment" };

function agentsModel() {
  return state.agents;
}
function externalSourceTitle(source) {
  return EXTERNAL_SOURCE_TITLES[String(source || "")] || String(source || "") || t("agents.default_source");
}
function externalDate(timestamp) {
  const ms = toMillis(timestamp);
  if (!ms) return t("agents.no_date");
  return new Intl.DateTimeFormat(intlLocale(), {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  }).format(new Date(ms));
}
function openAgents() {
  if (!ui.agentsDialog.open) ui.agentsDialog.showModal();
  const model = agentsModel();
  if (!model.optionsLoaded) loadAgentsOptions().catch((error) => toast(error.message, "error"));
  if (model.status === "idle" || model.status === "error") loadExternalSessions(false);
  else renderAgents();
}
function closeAgents() {
  if (ui.agentsDialog.open) ui.agentsDialog.close();
}
async function loadExternalSessions(refresh) {
  const model = agentsModel();
  model.status = "loading";
  model.error = "";
  model.preview = null;
  model.visible = 0;
  renderAgents();
  try {
    const payload = await api("/api/external-sessions?limit=1000" + (refresh ? "&refresh=1" : ""));
    model.sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
    model.counts = payload.counts && typeof payload.counts === "object" ? payload.counts : {};
    model.total = Number(payload.total || model.sessions.length);
    model.scannedAt = Number(payload.scanned_at || 0);
    model.status = "ready";
  } catch (error) {
    model.status = "error";
    model.error = error.message;
    toast(error.message, "error");
  }
  renderAgents();
}
function externalSessionMatches(session, source, needle) {
  if (source && String(session.source || "") !== source) return false;
  if (!needle) return true;
  return [session.title, session.cwd, session.model, session.agent, session.session_id]
    .some((value) => String(value || "").toLowerCase().includes(needle));
}
function visibleExternalSessions() {
  const model = agentsModel();
  const needle = model.query.trim().toLowerCase();
  return model.sessions.filter((session) => externalSessionMatches(session, model.source, needle));
}
function selectAgentsSource(source) {
  const model = agentsModel();
  if (model.source === source) return;
  model.source = source;
  model.visible = 0;
  renderAgents();
}
function buildAgentsSourceTabs() {
  const model = agentsModel();
  ui.agentsSources.replaceChildren();
  const choices = [["", t("agents.all"), Number(model.total || model.sessions.length || 0)]];
  for (const source of EXTERNAL_SOURCE_ORDER) {
    choices.push([source, externalSourceTitle(source), Number(model.counts[source] || 0)]);
  }
  for (const [id, label, count] of choices) {
    const active = model.source === id;
    const tab = element("button", "logs-tab" + (active ? " active" : ""));
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(active));
    tab.append(element("span", "logs-tab-name", label), element("span", "logs-badge", String(count)));
    tab.addEventListener("click", () => selectAgentsSource(id));
    ui.agentsSources.append(tab);
  }
}
function buildAgentsSessionRow(session) {
  const row = element("button", "agents-session");
  row.type = "button";
  row.title = t("agents.open_preview");
  const copy = element("span", "agents-session-copy");
  copy.append(element("span", "agents-session-title", session.title || session.session_id || t("agents.untitled")));
  const meta = element("span", "agents-session-meta");
  meta.append(element("span", "item-chip", externalSourceTitle(session.source)));
  meta.append(element("span", "", externalDate(session.updated_at)));
  meta.append(element("span", "", t("agents.messages", { n: Number(session.message_count || 0) })));
  if (session.model) meta.append(element("span", "", String(session.model)));
  copy.append(meta);
  copy.append(element("span", "agents-session-cwd", String(session.cwd || "") || t("session.cwd_missing")));
  row.append(copy, element("span", "agents-session-open", "→"));
  row.addEventListener("click", () => openExternalSession(session));
  return row;
}
function renderAgentsList() {
  const model = agentsModel();
  const sessions = visibleExternalSessions();
  const limit = model.visible || AGENTS_PAGE_SIZE;
  const shown = sessions.slice(0, limit);
  if (model.status === "loading" && !model.sessions.length) {
    ui.agentsCaption.textContent = t("agents.scanning");
  } else if (model.status === "error") {
    ui.agentsCaption.textContent = "";
  } else if (!sessions.length) {
    ui.agentsCaption.textContent = t("agents.no_matches");
  } else {
    const scanned = model.scannedAt ? t("agents.scanned", { time: localClock(model.scannedAt * 1000) }) : "";
    ui.agentsCaption.textContent = t("agents.shown", {
      shown: shown.length, total: sessions.length, catalog: model.total, scanned,
    });
  }
  const fragment = document.createDocumentFragment();
  if (model.status === "loading" && !model.sessions.length) {
    fragment.append(panelLoading());
  } else if (!sessions.length && model.status !== "error") {
    fragment.append(panelEmpty(t("agents.empty_title"), t("agents.empty_hint")));
  } else {
    for (const session of shown) fragment.append(buildAgentsSessionRow(session));
  }
  ui.agentsList.replaceChildren(fragment);
  ui.agentsMore.hidden = sessions.length <= shown.length;
  if (!ui.agentsMore.hidden) {
    ui.agentsMore.textContent = t("agents.more_count", { shown: shown.length, total: sessions.length });
  }
}
function renderAgents() {
  const model = agentsModel();
  const previewOpen = Boolean(model.preview);
  ui.agentsToolbar.hidden = previewOpen;
  ui.agentsListView.hidden = previewOpen;
  ui.agentsPreview.hidden = !previewOpen;
  ui.agentsError.hidden = previewOpen || !model.error;
  ui.agentsError.textContent = previewOpen ? "" : model.error;
  ui.agentsRefresh.disabled = model.status === "loading";
  ui.agentsSearch.disabled = previewOpen;
  if (previewOpen) {
    renderAgentsPreview();
    return;
  }
  buildAgentsSourceTabs();
  renderAgentsList();
}
function externalToolInput(raw) {
  const text = String(raw || "").trim();
  if (!text) return {};
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" ? parsed : { input: text };
  } catch (error) {
    return { input: text };
  }
}
function externalToolParts(text) {
  const raw = String(text || "");
  const lineEnd = raw.indexOf("\n");
  if (lineEnd < 0) return { name: raw.trim() || t("tool.default_name"), body: "" };
  return { name: raw.slice(0, lineEnd).trim() || t("tool.default_name"), body: raw.slice(lineEnd + 1) };
}
// External transcripts arrive as a stream of individual messages: a tool call and its result
// are combined into a single card, as in Igorek’s own tasks.
function externalTimelineItems(messages, startIndex, source) {
  const items = [];
  let pendingTool = null;
  messages.forEach((message, index) => {
    const role = String((message && message.role) || "");
    const kind = String((message && message.kind) || "text");
    const text = String((message && message.text) || "");
    const ts = Number((message && message.ts) || 0);
    const id = "ext-" + source + "-" + (startIndex + index);
    if (role === "user" || role === "assistant") {
      items.push({ id, type: role, text, ts });
      pendingTool = null;
      return;
    }
    if (role === "reasoning" || kind === "reasoning") {
      items.push({ id, type: "reasoning", text, streaming: false });
      pendingTool = null;
      return;
    }
    if (kind === "tool_result" && pendingTool && !pendingTool.result) {
      pendingTool.result = text;
      pendingTool = null;
      return;
    }
    if (kind === "tool_call") {
      const parts = externalToolParts(text);
      const item = {
        id, type: "tool", name: parts.name, input: externalToolInput(parts.body),
        result: "", status: "done", open: false,
      };
      items.push(item);
      pendingTool = item;
      return;
    }
    // opencode/codex places the tool name as the first line of text (bridge format),
    // so the name is taken from there, and the body is passed into the result.
    if (kind === "tool_result" && (source === "opencode" || source === "codex")) {
      const parts = externalToolParts(text);
      items.push({
        id, type: "tool", name: parts.name, input: {},
        result: parts.body, status: "done", open: false,
      });
      pendingTool = null;
      return;
    }
    items.push({
      id, type: "tool", name: EXTERNAL_TOOL_TITLE_KEYS[kind] ? t(EXTERNAL_TOOL_TITLE_KEYS[kind]) : t("tool.default_name"),
      input: kind === "web_search" ? { query: text } : text,
      result: "", status: "done", open: false,
    });
    pendingTool = null;
  });
  return items;
}
async function openExternalSession(session) {
  const model = agentsModel();
  const originalCwd = String(session.cwd || "").trim();
  model.preview = {
    source: String(session.source || ""), id: String(session.session_id || ""),
    title: String(session.title || ""), status: "loading", error: "",
    detail: null, items: [], nextOffset: null, loadingMore: false, originalCwd, cwdTouched: false,
  };
  model.importBusy = false;
  model.importError = "";
  await loadAgentsOptions();
  const options = model.options || {};
  agentsCwdCombo.setValue(originalCwd || options.default_cwd || state.workdirs[0] || "");
  renderAgentsCwdHint();
  renderAgents();
  loadExternalSessionDetail(false);
}
async function loadExternalSessionDetail(more) {
  const model = agentsModel();
  const preview = model.preview;
  if (!preview) return;
  const offset = more ? Number(preview.nextOffset || 0) : 0;
  preview.loadingMore = Boolean(more);
  preview.error = "";
  if (!more) {
    preview.status = "loading";
    preview.detail = null;
    preview.items = [];
    preview.nextOffset = null;
  }
  renderAgents();
  try {
    const target = "/api/external-sessions/" + encode(preview.source) + "/" + encode(preview.id)
      + "?offset=" + encode(String(offset)) + "&limit=" + AGENTS_PREVIEW_LIMIT;
    const detail = await api(target);
    if (agentsModel().preview !== preview) return;
    const messages = Array.isArray(detail.messages) ? detail.messages : [];
    const mapped = externalTimelineItems(messages, more ? preview.items.length : 0, preview.source);
    preview.detail = detail;
    preview.items = more ? preview.items.concat(mapped) : mapped;
    preview.nextOffset = detail.next_offset === null || detail.next_offset === undefined
      ? null : Number(detail.next_offset);
    preview.status = "ready";
  } catch (error) {
    if (agentsModel().preview !== preview) return;
    preview.error = error.message;
    if (more) toast(error.message, "error");
    else preview.status = "error";
  }
  preview.loadingMore = false;
  renderAgents();
  const scroll = ui.agentsPreviewScroll;
  window.requestAnimationFrame(() => {
    scroll.scrollTop = more ? scroll.scrollHeight : 0;
  });
}
function renderAgentsPreview() {
  const model = agentsModel();
  const preview = model.preview;
  if (!preview) return;
  const detail = preview.detail || {};
  ui.agentsPreviewTitle.textContent = preview.title || externalSourceTitle(preview.source);
  const meta = [externalSourceTitle(preview.source)];
  if (detail.model) meta.push(String(detail.model));
  if (detail.agent) meta.push(String(detail.agent));
  if (detail.cwd) meta.push(String(detail.cwd));
  meta.push(externalDate(detail.updated_at));
  meta.push(t("agents.meta_messages", {
    n: preview.items.length, total: Number(detail.total_messages || preview.items.length),
  }));
  ui.agentsPreviewMeta.textContent = meta.join(" · ");
  const busy = preview.status === "loading" || preview.loadingMore;
  ui.agentsPreviewRefresh.disabled = busy;
  ui.agentsLoadMore.hidden = busy || !preview.nextOffset;
  if (!ui.agentsLoadMore.hidden) ui.agentsLoadMore.textContent = t("agents.more");
  renderAgentsTimeline();
  renderAgentsImportState();
}
function renderAgentsTimeline() {
  const preview = agentsModel().preview;
  if (!preview) return;
  const fragment = document.createDocumentFragment();
  if (preview.status === "loading") {
    fragment.append(panelLoading());
  } else if (preview.status === "error") {
    fragment.append(element("p", "side-error", preview.error || t("agents.preview_error")));
  } else if (!preview.items.length) {
    fragment.append(panelEmpty(t("agents.preview_empty"), t("agents.preview_empty_hint")));
  } else {
    fragment.append(element(
      "div", "status-line",
      t("agents.loaded", { source: externalSourceTitle(preview.source), n: preview.items.length }),
    ));
    for (const item of preview.items) {
      if (item.type === "user" || item.type === "assistant") fragment.append(renderMessage(item));
      else if (item.type === "tool") fragment.append(renderTool(item, renderAgentsTimeline));
      else if (item.type === "reasoning") fragment.append(renderReasoning(item));
    else if (item.type === "compaction") fragment.append(renderCompaction(item));
      else if (item.type === "status") fragment.append(element("div", "status-line " + (item.level || ""), statusLineText(item)));
    }
    if (preview.error) fragment.append(element("p", "side-error", preview.error));
  }
  ui.agentsTimeline.replaceChildren(fragment);
}
// Show the source workspace as a hint after the user selects another path.
function renderAgentsCwdHint() {
  const preview = agentsModel().preview;
  const original = preview ? String(preview.originalCwd || "").trim() : "";
  const current = ui.agentsCwd.value.trim();
  const show = Boolean(original) && Boolean(preview.cwdTouched) && original !== current;
  ui.agentsCwdHint.hidden = !show;
  ui.agentsCwdHint.textContent = show ? t("agents.cwd_original", { path: original }) : "";
}
function renderAgentsImportState() {
  const model = agentsModel();
  if (!model.preview) return;
  const busy = Boolean(model.importBusy);
  ui.agentsImport.disabled = busy;
  const label = ui.agentsImport.querySelector("span");
  if (label) label.textContent = busy ? t("agents.importing") : t("agents.import");
  const error = model.importError || model.optionsError || "";
  ui.agentsImportError.hidden = !error;
  ui.agentsImportError.textContent = error;
  renderAgentsCwdHint();
}
let agentsOptionsPromise = null;
function loadAgentsOptions() {
  const model = agentsModel();
  if (agentsOptionsPromise) return agentsOptionsPromise;
  model.optionsLoaded = true;
  agentsOptionsPromise = (async () => {
    try {
      model.options = await api("/api/session-options");
      model.optionsError = "";
    } catch (error) {
      model.optionsError = error.message;
    }
    const options = model.options || {};
    ui.agentsProfile.replaceChildren();
    for (const name of options.profiles || []) {
      const option = element("option", "", name);
      option.value = name;
      ui.agentsProfile.append(option);
    }
    if (options.default_profile) ui.agentsProfile.value = options.default_profile;
    state.workdirs = (options.workdirs || []).map((path) => String(path));
    if (!ui.agentsCwd.value.trim()) {
      agentsCwdCombo.setValue(options.default_cwd || state.workdirs[0] || "");
    }
    renderAgentsImportState();
  })();
  return agentsOptionsPromise;
}
async function importExternalSession() {
  const model = agentsModel();
  const preview = model.preview;
  if (!preview || model.importBusy) return;
  if (preview.status !== "ready" || !preview.detail) {
    toast(t("agents.import_error_loading"));
    return;
  }
  const profile = String(ui.agentsProfile.value || "").trim();
  const cwd = preview.cwdTouched ? String(ui.agentsCwd.value || "").trim() : "";
  if (!profile) {
    model.importError = t("agents.import_error_profile");
    renderAgentsImportState();
    return;
  }
  if (preview.cwdTouched && !cwd) {
    model.importError = t("agents.import_error_cwd");
    renderAgentsImportState();
    return;
  }
  const denied = preview.cwdTouched ? cwdDeniedReason(cwd) : "";
  if (denied) {
    model.importError = cwdDeniedMessage(denied);
    renderAgentsImportState();
    return;
  }
  model.importBusy = true;
  model.importError = "";
  renderAgentsImportState();
  try {
    const result = await api(
      "/api/external-sessions/" + encode(preview.source) + "/" + encode(preview.id) + "/import",
      {
        method: "POST",
        body: JSON.stringify({ profile, cwd, idempotency_key: crypto.randomUUID() }),
      },
    );
    const sessionId = String((result.session || {}).session_id || "");
    if (!sessionId) throw new Error(t("agents.import_error_session"));
    const duplicate = Boolean(result.duplicate);
    model.preview = null;
    closeAgents();
    toast(result.cwd_fallback_reason
      ? t("agents.imported_fallback", { reason: result.cwd_fallback_reason, path: result.cwd || "" })
      : (duplicate ? t("agents.imported_duplicate") : t("agents.imported_new")));
    await loadSessions();
    await selectSession(sessionId);
  } catch (error) {
    model.importError = error.message;
    toast(error.message, "error");
  }
  model.importBusy = false;
  renderAgentsImportState();
}

// ---------------------------------------------------------------------------
// «Plan»: the latest update_plan and session usage.
// Live events update the model directly; when opening an old or imported session,
// the same data is reconstructed from the durable log (/api/sessions/{id}/events?after=&limit=).
// Sequence numbers prevent the log page from overwriting a fresher live event or
// counting a round twice.
// -----------------------------------------------------------------------------
const JOURNAL_PAGE_LIMIT = 500;
const JOURNAL_MAX_PAGES = 8;
const PLAN_STATUS_KEYS = {
  completed: "plan.status.completed",
  in_progress: "plan.status.in_progress",
  pending: "plan.status.pending",
};
const PLAN_MARKERS = { completed: "✓", in_progress: "●", pending: "○" };

function resetPlanState() {
  state.plan = {
    session: "", status: "idle", error: "", loading: false, present: false,
    explanation: "", steps: [], seq: 0,
    usage: { input: 0, output: 0, rounds: 0, lastInput: null, lastLatency: 0, truncated: false },
    usageSeqs: new Set(),
    history: newJournalHistory(),
  };
}
function normalizePlanSteps(input) {
  if (!input || typeof input !== "object" || !Array.isArray(input.plan)) return null;
  const steps = [];
  for (const item of input.plan) {
    if (!item || typeof item !== "object") continue;
    const text = String(item.step || "").trim();
    if (!text) continue;
    const status = PLAN_MARKERS[item.status] ? item.status : "pending";
    steps.push({ text, status });
  }
  return steps;
}
function notePlan(input, seq) {
  const steps = normalizePlanSteps(input);
  if (steps === null) return;
  const model = state.plan;
  const order = Number(seq || 0);
  if (order && order < model.seq) return;
  model.steps = steps;
  model.explanation = typeof input.explanation === "string" ? input.explanation.trim() : "";
  model.seq = order || model.seq;
  model.present = true;
  model.error = "";
  if (model.status === "idle" || model.status === "loading") model.status = "ready";
  if (!model.loading) renderPlanSection();
}
function notePlanFromTools(tools, seq) {
  for (const tool of Array.isArray(tools) ? tools : []) {
    if (tool && tool.name === "update_plan") notePlan(tool.input, seq);
  }
}
function usageTokens(raw) {
  if (!raw || typeof raw !== "object") return null;
  const pick = (...names) => {
    for (const name of names) {
      const value = Number(raw[name]);
      if (Number.isFinite(value)) return value;
    }
    return null;
  };
  const input = pick("input_tokens", "prompt_tokens", "prompt");
  const output = pick("output_tokens", "completion_tokens", "completion");
  if (input === null && output === null) return null;
  return { input: input || 0, output: output || 0 };
}
function noteUsage(payload, seq) {
  const tokens = usageTokens(payload && payload.usage);
  if (!tokens) return;
  const model = state.plan;
  const order = Number(seq || 0);
  if (order && model.usageSeqs.has(order)) return;
  if (order) model.usageSeqs.add(order);
  model.usage.input += tokens.input;
  model.usage.output += tokens.output;
  model.usage.rounds += 1;
  model.usage.lastInput = tokens.input;
  const latency = Number(payload.latency_ms);
  if (Number.isFinite(latency) && latency > 0) model.usage.lastLatency = latency;
  if (!model.loading) renderPlanSection();
}
function scanJournalEvent(event) {
  if (!event || !event.kind) return;
  const payload = event.payload || {};
  if (event.kind === "tool.request_batch") notePlanFromTools(payload.tools, event.seq);
  else if (event.kind === "tool.result" && String(payload.tool_name || "") === "update_plan") {
    notePlan(payload.tool_input, event.seq);
  } else if (event.kind === "model.round_response") noteUsage(payload, event.seq);
}

// ---------------------------------------------------------------------------
// Session history from the durable log.
// The live channel displays tools, reasoning, and task boundaries, but
// session.messages contains only user/assistant text. When opening a session,
// the same events are retrieved from the log by seq, and the authoritative message text
// is inserted into its event by message_id and round text. Thus, after a reload,
// the timeline matches what was visible live, with no duplicates.
// ---------------------------------------------------------------------------
const HISTORY_SUB = { user: 1, assistant: 2, approval: 3, status: 4, reasoning: 5, tool: 6 };

function newJournalHistory() {
  return {
    events: 0,
    userMessages: new Map(),
    delivered: new Set(),
    rounds: [],
    tools: new Map(),
    reasoning: new Map(),
    compactions: new Map(),
    statuses: new Map(),
    approvals: new Map(),
    taskStarts: new Map(),
    errorTexts: new Set(),
    timeIndex: [],
  };
}
function historyEventTs(event) {
  const payload = event.payload || {};
  const value = Number(event.ts || payload.ts || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}
function historyStatus(history, id, seq, fields, mode) {
  const existing = history.statuses.get(id);
  const position = existing ? (mode === "last" ? Math.max(existing.seq, seq) : Math.min(existing.seq, seq)) : seq;
  history.statuses.set(id, { ...(existing || {}), ...fields, id, type: "status", seq: position });
}
function historyToolId(taskId, toolUseId) {
  return "tool-" + taskId + "-" + toolUseId;
}
function historyFinishStatus(history, taskId, seq, ts, fields) {
  const start = history.taskStarts.get(taskId);
  const end = Number(ts || 0);
  let suffix = "";
  if (end) {
    suffix = " · " + localClock(end * 1000);
    if (start && end >= start) suffix = " · " + formatDuration((end - start) * 1000) + " · " + localClock(end * 1000);
  }
  historyStatus(history, "status-" + taskId + "-finish", seq, { ...fields, suffix }, "last");
}
function collectJournalHistory(history, event) {
  if (!history || !event || !event.kind) return;
  const payload = event.payload || {};
  const taskId = String(event.task_id || payload.task_id || "task");
  const seq = Number(event.seq || 0);
  const ts = historyEventTs(event);
  history.events += 1;
  if (ts) history.timeIndex.push({ seq, ts });
  switch (event.kind) {
    case "compaction.started":
    case "compaction.progress":
    case "compaction.completed":
    case "compaction.failed": {
      const item = compactionItem(event);
      history.compactions.set(item.id, { ...item, seq });
      break;
    }

    case "task.submitted":
    case "task.message_added": {
      const messageId = String(payload.message_id || "");
      if (!messageId) break;
      const previous = history.userMessages.get(messageId);
      history.userMessages.set(messageId, {
        messageId,
        taskId: String(payload.task_id || event.task_id || ""),
        seq: previous ? previous.seq : seq,
        text: String(payload.text || ""),
        attachments: payload.attachments || previous?.attachments || [],
        ts: (previous && previous.ts) || ts,
        queued: event.kind === "task.message_added" && !history.delivered.has(messageId),
      });
      if (event.kind === "task.submitted") {
        historyStatus(history, "status-" + taskId + "-accepted", seq, { key: "task.accepted", level: "" }, "first");
        if (ts && !history.taskStarts.has(taskId)) history.taskStarts.set(taskId, ts);
      }
      break;
    }
    case "steer.delivered": {
      const messageId = String(payload.message_id || "");
      if (!messageId) break;
      history.delivered.add(messageId);
      const entry = history.userMessages.get(messageId);
      if (entry) entry.queued = false;
      break;
    }
    case "task.start": {
      if (ts && !history.taskStarts.has(taskId)) history.taskStarts.set(taskId, ts);
      historyStatus(history, "status-" + taskId + "-accepted", seq, {
        key: "task.started", level: "ok",
        extraKey: isolationStarting() ? "task.isolation_extra" : "",
      }, "first");
      break;
    }
    case "model.round_response": {
      history.rounds.push({
        seq, taskId, round: Number(payload.round || 0), text: String(payload.text || ""), ts,
        id: "assistant-" + taskId + "-" + (seq || Number(payload.round || 0)),
      });
      break;
    }
    case "model.reasoning": {
      const text = String(payload.text || "");
      if (!text) break;
      const entry = history.reasoning.get(taskId) || { seq, parts: [], ts: 0, firstTs: 0, lastTs: 0 };
      entry.parts.push(text);
      if (ts) {
        if (!entry.firstTs) entry.firstTs = ts;
        entry.lastTs = ts;
        entry.ts = ts;
      }
      history.reasoning.set(taskId, entry);
      break;
    }
    case "tool.request_batch": {
      for (const tool of Array.isArray(payload.tools) ? payload.tools : []) {
        const toolUseId = String((tool && tool.id) || "");
        if (!toolUseId) continue;
        const id = historyToolId(taskId, toolUseId);
        const previous = history.tools.get(id);
        history.tools.set(id, {
          id, type: "tool", taskId, tool_use_id: toolUseId, seq: previous ? previous.seq : seq,
          name: String((tool && tool.name) || (previous && previous.name) || t("tool.default_name")),
          input: (tool && tool.input) || (previous && previous.input) || {},
          result: previous ? previous.result : "",
          duration: previous ? previous.duration : 0,
          status: previous ? previous.status : "running",
          open: Boolean(previous && previous.open),
        });
      }
      break;
    }
    case "tool.result": {
      const id = historyToolId(taskId, String(payload.tool_use_id || ""));
      const previous = history.tools.get(id);
      history.tools.set(id, {
        id, type: "tool", taskId, tool_use_id: String(payload.tool_use_id || ''), is_error: payload.is_error === true, seq: previous ? previous.seq : seq,
        name: String(payload.tool_name || (previous && previous.name) || t("tool.default_name")),
        input: payload.tool_input || (previous && previous.input) || {},
        result: String(payload.result_preview || ""),
        duration: Number(payload.duration_ms || 0),
        status: String(payload.status || "done"),
        open: Boolean(previous && previous.open),
      });
      break;
    }
    case "approval.required": {
      const requestId = String(payload.request_id || "");
      if (!requestId) break;
      const approvalTask=String(payload.task_id||taskId);
      history.approvals.set(approvalTask+'\0'+requestId, {
        id: "approval-" + approvalTask + '-' + requestId, type: "approval", seq,
        task_id: approvalTask, request_id: requestId,
        tool_use_id: String(payload.tool_use_id || ''), kind: payload.kind, input: payload.input,
        tool: String(payload.tool || ""), reason: String(payload.reason || ""),
        target: String(payload.target || ""),
      });
      historyStatus(history, "status-" + taskId + "-approval", seq, { task_id: taskId, key: "task.paused", level: "warn" }, "first");
      break;
    }
    case "approval.resolved": {
      const item = history.approvals.get(String(payload.task_id||taskId)+'\0'+String(payload.request_id || ""));
      if (item) item.resolved = String(payload.decision || "");
      break;
    }
    case "task.status": {
      const status = String(payload.status || "");
      if (status === "waiting_approval") {
        historyStatus(history, "status-" + taskId + "-approval", seq, { task_id: taskId, key: "task.paused", level: "warn" }, "first");
      } else if (["cancelled", "fail", "dead_letter", "timeout"].includes(status)) {
        historyFinishStatus(history, taskId, seq, ts, status === "cancelled"
          ? { key: "task.stopped", level: "warn" }
          : { key: "task.finished_status", params: { status }, level: "warn" });
      }
      break;
    }
    case "task.cancelled": {
      historyFinishStatus(history, taskId, seq, ts, { key: "task.stopped", level: "warn" });
      break;
    }
    case "task.error": {
      const message = String(payload.error_message || "");
      if (message) history.errorTexts.add(message.trim());
      historyFinishStatus(history, taskId, seq, ts, {
        key: "task.finished_error",
        params: { error: message || keyRef("task.no_description") },
        level: "warn",
      });
      break;
    }
    case "task.finish": {
      const status = String(payload.status || "").toLowerCase();
      if (status === "waiting_approval") {
        historyStatus(history, "status-" + taskId + "-approval", seq, { task_id: taskId, key: "task.paused", level: "warn" }, "first");
        break;
      }
      const ok = ["ok", "completed", "success"].includes(status);
      historyFinishStatus(history, taskId, seq, ts, ok
        ? { key: "task.finished", level: "ok" }
        : { key: "task.finished_status", params: { status: status || keyRef("task.unknown_status") }, level: "warn" });
      break;
    }
    case "task.requeued": {
      historyStatus(history, "status-" + taskId + "-resumed-" + seq, seq, {
        task_id: taskId, key: requeueText(String(payload.reason || "")), level: "",
      }, "first");
      break;
    }
    default:
      break;
  }
}
function historyPosition(history, seq, ts) {
  if (Number.isFinite(seq) && seq > 0) return seq;
  if (ts > 0 && history.timeIndex.length) {
    let low = 0, high = history.timeIndex.length - 1, found = -1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (history.timeIndex[mid].ts <= ts) { found = mid; low = mid + 1; } else high = mid - 1;
    }
    if (found >= 0) return history.timeIndex[found].seq + 0.5;
    return Math.max(0, history.timeIndex[0].seq - 0.5);
  }
  return 0.5;
}
function historyEntry(item, history, seq, ts, sub) {
  return { item, sub: sub || 0, sortKey: historyPosition(history, Number(seq || 0), Number(ts || 0)) };
}
function historyTextEdgesMatch(left, right) {
  if (left.length < 160 || right.length < 160) return false;
  return left.slice(0, 60) === right.slice(0, 60) && left.slice(-60) === right.slice(-60);
}
function historyAssistantMatch(history, text, usedRounds, taskId) {
  const target = String(text || "").trim();
  const matches = (round) => {
    const roundText = String(round.text || "").trim();
    if (!roundText) return false;
    if (roundText === target || roundText.startsWith(target) || target.startsWith(roundText)) return true;
    return historyTextEdgesMatch(roundText, target);
  };
  let fallback = -1;
  let taskMatch = -1;
  let taskLast = -1;
  for (let index = 0; index < history.rounds.length; index += 1) {
    if (usedRounds.has(index)) continue;
    const round = history.rounds[index];
    if (taskId && round.taskId === taskId) {
      if (String(round.text || "").trim()) taskLast = index;
      if (target && matches(round) && taskMatch < 0) taskMatch = index;
      continue;
    }
    if (target && matches(round) && fallback < 0) fallback = index;
  }
  if (taskMatch >= 0) return taskMatch;
  if (taskLast >= 0) return taskLast;
  return fallback;
}
function buildHistoryEntries(session, history) {
  if (!history || !history.events) return null;
  const messages = Array.isArray(session && session.messages) ? session.messages : [];
  const authoritativeUserIds = new Set(
    messages.filter((message) => message.role === "user" && message.message_id).map((message) => String(message.message_id)),
  );
  const entries = [];
  const usedRounds = new Set();
  const imported = messages.filter((message) => message.history_id);
  sessionMessageItems(imported).forEach((item, index) => entries.push({ item, sub: 0, sortKey: -imported.length + index }));
  messages.forEach((message, index) => {
    if (message.history_id || !["user", "assistant"].includes(message.role)) return;
    const role = message.role === "user" ? "user" : "assistant";
    const text = String(message.text || "");
    if (role === "assistant" && text.trim() && history.errorTexts.has(text.trim())) return;
    let seq = null;
    if (role === "user") {
      const anchor = message.message_id ? history.userMessages.get(String(message.message_id)) : null;
      if (anchor) seq = anchor.seq;
    } else {
      const match = historyAssistantMatch(history, text, usedRounds, message.task_id ? String(message.task_id) : "");
      if (match >= 0) {
        usedRounds.add(match);
        seq = history.rounds[match].seq;
      }
    }
    const item = {
      id: message.message_id ? "msg-" + message.message_id : "history-" + role + "-" + index,
      type: role, text, ts: message.ts, task_id: message.task_id || "", attachments: message.attachments || [], files: message.files || [],
    };
    if (role === "user") {
      item.queued = Boolean(message.queued);
      item.task_id = message.task_id || "";
      item.message_id = message.message_id || "";
      if (message.message_id && history.delivered.has(String(message.message_id))) item.queued = false;
    }
    entries.push(historyEntry(item, history, seq, message.ts, HISTORY_SUB[role]));
  });
  for (const anchor of history.userMessages.values()) {
    if (authoritativeUserIds.has(anchor.messageId)) continue;
    entries.push(historyEntry({
      id: "msg-" + anchor.messageId, type: "user", text: anchor.text, ts: anchor.ts || null,
      queued: anchor.queued, task_id: anchor.taskId || "", message_id: anchor.messageId, attachments: anchor.attachments || [],
    }, history, anchor.seq, anchor.ts, HISTORY_SUB.user));
  }
  const finishedTasks = new Set();
  for (const status of history.statuses.values()) {
    const match = /^status-(.+)-finish$/.exec(status.id);
    if (match) finishedTasks.add(match[1]);
  }
  history.rounds.forEach((round, index) => {
    if (usedRounds.has(index) || !String(round.text || "").trim()) return;
    entries.push(historyEntry({
      id: round.id, type: "assistant", text: round.text, ts: round.ts || null,
    }, history, round.seq, round.ts, HISTORY_SUB.assistant));
  });
  for (const tool of history.tools.values()) {
    if (tool.status === "running" && finishedTasks.has(tool.taskId)) tool.status = "stopped";
    entries.push(historyEntry(tool, history, tool.seq, 0, HISTORY_SUB.tool));
  }
  for (const [taskId, reasoning] of history.reasoning) {
    const text = reasoning.parts.join("");
    if (!text.trim()) continue;
    entries.push(historyEntry({
      id: "reasoning-" + taskId, type: "reasoning", text, ts: reasoning.ts || null,
      startedTs: toMillis(reasoning.firstTs), endedTs: toMillis(reasoning.lastTs),
    }, history, reasoning.seq, reasoning.ts, HISTORY_SUB.reasoning));
  }
  for (const item of history.compactions.values()) entries.push(historyEntry(item, history, item.seq, 0, HISTORY_SUB.status));
  for (const status of history.statuses.values()) {
    entries.push(historyEntry(status, history, status.seq, 0, HISTORY_SUB.status));
  }
  for (const approval of history.approvals.values()) {
    entries.push(historyEntry(approval, history, approval.seq, 0, HISTORY_SUB.approval));
  }
  entries.sort((left, right) => (left.sortKey - right.sortKey) || (left.sub - right.sub));
  return entries.map((entry) => entry.item);
}
function applyHistoryEntries(entries) {
  const live = state.items.filter((item) => !state.historyBaseIds.has(item.id));
  const rebuilt = new Map(entries.map((item) => [item.id, item]));
  for (const item of live) {
    const target = rebuilt.get(item.id);
    if (!target) continue;
    const keepText = String(target.text || "");
    const longer = (target.type === "reasoning" || target.type === "assistant")
      && String(item.text || "").length > keepText.length;
    Object.assign(target, item);
    if (!longer && (target.type === "reasoning" || target.type === "assistant")) target.text = keepText;
  }
  const extra = live.filter((item) => !rebuilt.has(item.id));
  state.items = entries.concat(extra);
  renderTimeline();
}

async function loadSessionJournal(sessionId, force = false) {
  const model = state.plan;
  if (!sessionId || model.loading) return;
  if (!force && model.session === sessionId && model.status !== "idle") return;
  model.session = sessionId;
  model.status = "loading";
  model.error = "";
  model.loading = true;
  model.history = newJournalHistory();
  renderPlanSection();
  let after = 0;
  let pages = 0;
  let full = false;
  try {
    while (state.selected === sessionId && state.plan === model) {
      const page = await api(
        "/api/sessions/" + encode(sessionId) + "/events?after=" + encode(after)
        + "&limit=" + encode(JOURNAL_PAGE_LIMIT),
      );
      if (state.selected !== sessionId || state.plan !== model) return;
      const events = (page && page.events) || [];
      for (const event of events) {
        scanJournalEvent(event);
        collectJournalHistory(model.history, event);
      }
      pages += 1;
      full = events.length >= JOURNAL_PAGE_LIMIT;
      const last = events.length ? Number(events[events.length - 1].seq) : 0;
      if (!full || !last || last <= after) break;
      after = last;
    }
    model.usage.truncated = false;
    model.status = model.present ? "ready" : "empty";
  } catch (error) {
    if (state.selected !== sessionId || state.plan !== model) return;
    model.status = "error";
    model.error = error.message;
  } finally {
    if (state.selected === sessionId && state.plan === model) {
      model.loading = false;
      const historyItems = state.session?.meta?.agent_session ? null : buildHistoryEntries(state.session, model.history);
      if (historyItems) applyHistoryEntries(historyItems);
      renderPlanSection();
    }
  }
}
function renderPlanSection() {
  if (state.panel.open && state.panel.tab === "plan") {
    renderPanelSection(ui.sideViews.plan, buildPlanView);
  }
}
function formatCount(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return value === null || value === undefined ? "" : String(value);
  return new Intl.NumberFormat(intlLocale()).format(Math.round(number));
}
function buildPlanRow(step) {
  const row = element("div", "plan-row plan-row-" + step.status);
  const marker = element("span", "plan-marker " + step.status, PLAN_MARKERS[step.status] || "○");
  marker.title = t(PLAN_STATUS_KEYS[step.status] || "plan.status.pending");
  marker.setAttribute("aria-hidden", "true");
  row.append(marker, element("span", "plan-step", step.text));
  return row;
}
function planMetric(label, value) {
  const cell = element("div", "plan-metric");
  cell.append(element("span", "plan-metric-label", label), element("span", "plan-metric-value", value));
  return cell;
}
function modelContextWindow() {
  const meta = (state.session && state.session.meta) || {};
  const info = cachedModelInfo(String(meta.profile || ""), String(meta.model || ""));
  const value = info && info.status === "ready" ? info.context_window : null;
  return Number.isFinite(value) && value > 0 ? value : null;
}
function buildPlanContextUsage(usage, contextWindow) {
  const box = element("div", "plan-context-usage");
  const percent = Math.round((usage.lastInput / contextWindow) * 100);
  const head = element("div", "plan-context-usage-head");
  head.append(element("span", "plan-context-usage-used", t("plan.context_used_pct", { pct: formatCount(percent) })));
  head.append(element("span", "plan-context-usage-free", t("plan.context_free_pct", { pct: formatCount(Math.max(0, 100 - percent)) })));
  const track = element("div", "plan-context-bar");
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", t("plan.context_bar_aria", { pct: formatCount(percent) }));
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  track.setAttribute("aria-valuenow", String(Math.min(100, Math.max(0, percent))));
  const fill = element("span", "plan-context-bar-fill");
  fill.style.width = Math.min(100, Math.max(0, percent)) + "%";
  track.append(fill);
  box.append(head, track);
  return box;
}
function buildPlanContext(usage) {
  const box = element("section", "plan-context");
  box.append(element("h4", "plan-context-title", t("plan.context_title")));
  if (!usage.rounds) {
    box.append(element("p", "plan-context-note", t("plan.context_empty")));
    return box;
  }
  const contextWindow = modelContextWindow();
  const grid = element("div", "plan-context-grid");
  grid.append(planMetric(t("plan.context_input"), formatCount(usage.input)));
  grid.append(planMetric(t("plan.context_output"), formatCount(usage.output)));
  grid.append(planMetric(t("plan.context_rounds"), formatCount(usage.rounds)));
  if (usage.lastInput !== null) {
    grid.append(planMetric(t("plan.context_last"), formatCount(usage.lastInput)));
  }
  if (contextWindow) {
    grid.append(planMetric(t("plan.context_window"), formatCount(contextWindow)));
  }
  if (usage.lastLatency) {
    grid.append(planMetric(
      t("plan.context_latency"),
      t("plan.context_latency_ms", { ms: formatCount(usage.lastLatency) }),
    ));
  }
  box.append(grid);
  if (contextWindow && usage.lastInput !== null) {
    box.append(buildPlanContextUsage(usage, contextWindow));
    box.append(element("p", "plan-context-note", t("plan.context_window_note")));
  }
  if (usage.truncated) box.append(element("p", "plan-context-note", t("plan.history_truncated")));
  if (!contextWindow) box.append(element("p", "plan-context-note", t("plan.context_note")));
  return box;
}
// OpenCode Go quota: numbers come from /api/opencode-usage (the key does not leave
// the host). We display used/free, bandwidth usage, and reset time; when
// rate-limited, a clear status is shown. Client-side cache duration is 60 seconds, same as the server.
const OPENCODE_USAGE_TTL_MS = 60000;
const OPENCODE_WINDOW_ORDER = ["rolling", "weekly", "monthly"];
const OPENCODE_WINDOW_KEYS = {
  rolling: "plan.quota_window_5h",
  weekly: "plan.quota_window_week",
  monthly: "plan.quota_window_month",
};
const OPENCODE_ERROR_KEYS = {
  no_key: "plan.quota_error_no_key",
  key_rejected: "plan.quota_error_key_rejected",
  subscription_required: "plan.quota_error_subscription",
  timeout: "plan.quota_error_timeout",
  network: "plan.quota_error_network",
  invalid_response: "plan.quota_error_invalid",
};
function normalizeOpenCodeUsage(usage) {
  if (!usage || typeof usage !== "object") return null;
  const result = {};
  for (const name of OPENCODE_WINDOW_ORDER) {
    const entry = usage[name];
    if (!entry || typeof entry !== "object") return null;
    const percent = Number(entry.percent);
    result[name] = {
      percent: Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : null,
      status: typeof entry.status === "string" ? entry.status : "",
      resetsAt: typeof entry.resetsAt === "string" ? entry.resetsAt : "",
    };
  }
  return result;
}
function quotaTone(entry) {
  if (entry.status === "rate-limited") return "danger";
  if (entry.percent !== null && entry.percent >= 85) return "danger";
  if (entry.percent !== null && entry.percent >= 60) return "warn";
  return "ok";
}
function quotaResetText(resetsAt) {
  const time = Date.parse(String(resetsAt || ""));
  if (!Number.isFinite(time)) return "";
  const delta = time - Date.now();
  if (delta <= 60000) return t("plan.quota_reset_soon");
  const minutes = Math.round(delta / 60000);
  if (minutes < 60) {
    return t("plan.quota_reset_in", { when: t("duration.minutes", { m: formatCount(minutes) }) });
  }
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours < 48) {
    const when = rest
      ? t("duration.hours_minutes", { h: formatCount(hours), r: formatCount(rest) })
      : t("duration.hours", { h: formatCount(hours) });
    return t("plan.quota_reset_in", { when });
  }
  const local = new Intl.DateTimeFormat(intlLocale(), {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(time));
  return t("plan.quota_resets_at", { when: local });
}
function buildQuotaRow(name, entry) {
  const label = t(OPENCODE_WINDOW_KEYS[name]);
  const tone = quotaTone(entry);
  const row = element("div", "plan-quota-row");
  const head = element("div", "plan-quota-row-head");
  head.append(element("span", "plan-quota-window", label));
  const used = entry.percent === null ? "—" : t("plan.context_used_pct", { pct: formatCount(entry.percent) });
  head.append(element("span", "plan-quota-percent " + tone, used));
  row.append(head);
  if (entry.percent !== null) {
    const track = element("div", "plan-context-bar");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", t("plan.quota_bar_aria", { window: label, pct: formatCount(entry.percent) }));
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    track.setAttribute("aria-valuenow", String(Math.round(entry.percent)));
    const fill = element("span", "plan-context-bar-fill plan-quota-fill " + tone);
    fill.style.width = entry.percent + "%";
    track.append(fill);
    row.append(track);
  }
  const meta = element("div", "plan-quota-meta");
  meta.append(element(
    "span", "plan-quota-free",
    entry.percent === null ? "" : t("plan.context_free_pct", { pct: formatCount(Math.max(0, 100 - entry.percent)) }),
  ));
  const reset = quotaResetText(entry.resetsAt);
  if (reset) meta.append(element("span", "plan-quota-reset", reset));
  row.append(meta);
  if (entry.status && entry.status !== "ok") {
    row.append(element(
      "span", "plan-quota-status",
      entry.status === "rate-limited" ? t("plan.quota_rate_limited") : entry.status,
    ));
  }
  return row;
}
function codexQuotaError(error) {
  if(!error)return 'Недельная квота пока недоступна.';
  const text=({quota_login_required:'Нажми «Подключить счётчик», чтобы подтвердить аккаунт. Рабочий вход сохранится',quota_local_login_required:'Текущий токен квоты недоступен. Рабочее подключение модели сохранено',quota_profile_changed:'Подключение профиля изменилось. Подключи счётчик заново',quota_confirmation_required:'Подтверди аккаунт счётчика',quota_cancelled:'Подключение счётчика отменено',quota_identity_invalid:'Не удалось проверить аккаунт счётчика',quota_account_mismatch:'Аккаунт Codex изменился или не совпадает с подключённым. Подключи счётчик заново',quota_login_failed:'Не удалось подтвердить привязку счётчика',quota_access_denied:'Доступ к квоте отклонён. Рабочее подключение модели сохранено',quota_weekly_missing:'Ответ OpenAI не содержит поддерживаемых лимитов',quota_invalid_response:'OpenAI вернул неожиданный формат квоты',quota_network:'Не удалось связаться с сервисом квоты',quota_rate_limited:'Сервис квоты ограничил частоту запросов',quota_http_error:'Не удалось прочитать квоту OpenAI'})[error.code]||'Квота Codex недоступна';
  return text+(Number.isInteger(error.http_status)?' (HTTP '+error.http_status+')':'')+'.';
}
function buildCodexQuotaRow(name,entry) {
  const row=buildQuotaRow(name,entry),remaining=Math.max(0,100-entry.percent);
  row.querySelector('.plan-quota-percent').textContent='Осталось '+formatCount(remaining)+'%';
  row.querySelector('.plan-quota-free').textContent='Использовано '+formatCount(entry.percent)+'%';
  const bar=row.querySelector('[role="progressbar"]');
  bar.setAttribute('aria-label',(name==='weekly'?'Неделя':'5 часов')+': осталось '+formatCount(remaining)+'%');
  bar.setAttribute('aria-valuenow',String(Math.round(remaining)));
  bar.querySelector('.plan-context-bar-fill').style.width=remaining+'%';
  return row;
}
function currentQuotaModel() {
  const profile = window.IgorekDesktop ? currentSessionProfile() : '';
  if (state.quota.profile !== profile) { window.clearTimeout(state.quota.retryTimer); state.quota = {profile,provider:window.IgorekDesktop?'':'opencode',status:'idle',error:'',code:'',data:null,fetchedAt:0}; }
  return state.quota;
}
function buildPlanOpenCodeUsage() {
  const model = currentQuotaModel();
  if(model.provider==='unsupported')return document.createDocumentFragment();
  const codex=model.provider==='codex';
  const box = element("section", "plan-context plan-quota");
  const head = element("div", "plan-quota-head");
  const title=codex?'Квота профиля · Codex':model.provider==='opencode'?t('plan.quota_title'):'Квота профиля';
  head.append(element("h4", "plan-context-title", title));
  const refresh = element("button", "mini-button plan-quota-refresh", "↻");
  refresh.type = "button";refresh.title='Обновить квоту';refresh.setAttribute('aria-label',refresh.title);
  refresh.addEventListener("click", () => { loadOpenCodeUsage(true).catch(() => {}); });
  head.append(refresh);box.append(head);
  if (model.status === "idle" || model.status === "loading" && !model.data) {
    box.append(element("p", "plan-context-note", t("plan.quota_loading")));return box;
  }
  if(codex&&window.IgorekDesktop?.quota){const connect=element('button','mini-button',model.accountEmail?'Подключить заново':'Подключить счётчик');connect.type='button';connect.disabled=!!model.quotaConnecting;if(model.quotaConnecting)connect.textContent='Подключаю…';connect.addEventListener('click',()=>connectCodexQuota(model.profile));box.append(connect);}
  if (!model.data) {
    if (['vault_locked','interface_locked'].includes(model.code)) {box.append(element('p','plan-context-note',model.error));return box;}
    const empty=codex&&model.status==='ready'&&!model.quotaError;
    box.append(element('p',empty?'plan-context-note':'plan-quota-error',codex
      ?codexQuotaError(model.quotaError)
      :model.provider==='opencode'?t(OPENCODE_ERROR_KEYS[model.code]||'plan.quota_error_generic'):'Квота выбранного подключения сейчас недоступна.'));
    return box;
  }
  for (const name of codex?['rolling','weekly']:OPENCODE_WINDOW_ORDER) if(model.data[name])box.append(codex?buildCodexQuotaRow(name,model.data[name]):buildQuotaRow(name, model.data[name]));
  if(codex&&model.quotaError)box.append(element('p','plan-quota-error',codexQuotaError(model.quotaError)));
  box.append(element('p','plan-context-note',codex
    ?'Лимиты подписки Codex'+(model.accountEmail?' · '+model.accountEmail:'')+(model.source==='codex_local'?' · Codex на этом ПК':'')+(model.observedAt?' · '+new Date(model.observedAt).toLocaleString('ru-RU'):'')+'.'
    :t('plan.quota_note')));
  return box;
}
async function connectCodexQuota(profile) {
  const desktop=window.IgorekDesktop,model=currentQuotaModel();
  if(model.quotaConnecting)return;model.quotaConnecting=true;renderPlanSection();
  try {
    const result=await desktop.quota('quota_begin',profile);
    if(currentSessionProfile()!==profile)return;
    if(result.error)throw new Error(codexQuotaError({code:result.error}));
    if(result.state!=='connected')throw new Error(codexQuotaError({code:'quota_confirmation_required'}));
    await loadOpenCodeUsage(true);toast('Счётчик подключён');
  }catch(error){toast(error.message, "error");}
  finally{model.quotaConnecting=false;if(currentSessionProfile()===profile)renderPlanSection();}
}
async function loadOpenCodeUsage(force = false, background = false) {
  const model = currentQuotaModel();
  if (model.status === "loading" || window.IgorekDesktop&&(!state.paired||!model.profile)) return;
  const fresh = model.status==='ready' && Date.now() - model.fetchedAt < OPENCODE_USAGE_TTL_MS;
  if (fresh && !force) {renderPlanSection();return;}
  window.clearTimeout(model.retryTimer);model.retryTimer=0;
  model.status = "loading";model.error = "";model.code = "";model.quotaError = null;renderPlanSection();
  try {
    const path=window.IgorekDesktop?'/api/provider-usage?profile='+encode(model.profile)+(force?'&refresh=1':''):'/api/opencode-usage'+(force?'?refresh=1':'');
    const payload=await api(path,{notify:false});
    if(state.quota!==model||window.IgorekDesktop&&currentSessionProfile()!==model.profile)return;
    model.provider=window.IgorekDesktop?payload.provider:'opencode';
    model.quotaError=payload.quota_error||null;model.source=payload.source||'';
    model.accountEmail=payload.account_email||'';

    if(model.provider==='codex') {
      const entries={};
      for(const name of ['rolling','weekly']){
        const entry=payload.usage?.[name];
        if(entry&&typeof entry.percent==='number'&&Number.isFinite(entry.percent)&&entry.percent>=0&&entry.percent<=100)
          entries[name]={percent:entry.percent,status:entry.status||'',resetsAt:entry.resetsAt||''};
      }
      model.data=Object.keys(entries).length?entries:null;
    } else if(model.provider==='unsupported')model.data=null;
    else {model.data=normalizeOpenCodeUsage(payload.usage);if(!model.data)throw Object.assign(new Error('Некорректная квота'),{code:'invalid_response'});}
    model.observedAt=payload.fetched_at||'';model.fetchedAt=Date.now();model.status='ready';
  } catch(error) {
    if(state.quota!==model||window.IgorekDesktop&&currentSessionProfile()!==model.profile)return;
    model.data=null;model.status='error';model.error=error.message;model.code=error.code||'';
    model.httpStatus=error.status||0;
  }
  const failure=model.quotaError|| (model.status==='error'?{code:model.code,http_status:model.httpStatus}:null);
  if(failure){
    const locked=['vault_locked','interface_locked'].includes(failure.code);
    const transient=['unavailable','quota_network','quota_codex_timeout','quota_codex_request_failed','quota_rate_limited'].includes(failure.code)||failure.http_status>=500||!failure.code;
    model.failures=(model.failures||0)+1;
    if(locked){model.data=null;model.accountEmail='';model.error='Разблокируйте хранилище — счётчик обновится автоматически.';}
    if(transient && window.IgorekDesktop) model.retryTimer=window.setTimeout(()=>{
      model.retryTimer=0;
      if(state.paired&&state.quota===model&&currentSessionProfile()===model.profile)loadOpenCodeUsage(true,true).catch(()=>{});
    },Math.min(60000,5000*2**Math.min(model.failures-1,4)));
    if(!locked&&failure.http_status!==401&&((force&&!background)||!transient||model.failures===3)&&model.notifiedCode!==failure.code){
      desktopNotification('Квота · '+model.profile,model.quotaError?codexQuotaError(failure):model.error+(failure.code?' · '+failure.code:''));model.notifiedCode=failure.code;
    }
  }else{model.failures=0;model.notifiedCode=null;}
  renderPlanSection();
}
function refreshOpenCodeUsageIfVisible() {
  if (document.hidden) return;
  if (!state.paired || state.quota.retryTimer || (!window.IgorekDesktop && (!state.panel.open || state.panel.tab !== "plan"))) return;
  loadOpenCodeUsage(false).catch(() => {});
}
function buildPlanView() {
  const guard = sessionRequired();
  if (guard) return guard;
  const model = state.plan;
  const fragment = document.createDocumentFragment();
  const done = model.steps.filter((step) => step.status === "completed").length;
  fragment.append(panelToolbar(
    model.present ? t("plan.progress", { done, total: model.steps.length }) : t("panel.caption.plan"),
    [panelRefreshButton(() => loadSessionJournal(state.selected, true))],
  ));
  if (!model.present && model.status === "loading") {
    fragment.append(panelLoading());
  } else if (!model.present && model.status === "error") {
    fragment.append(element("p", "side-error", model.error));
  } else if (!model.present || !model.steps.length) {
    fragment.append(panelEmpty(t("plan.empty"), t("plan.empty_hint")));
  } else {
    if (model.explanation) fragment.append(element("p", "plan-explanation", model.explanation));
    const list = element("div", "plan-list");
    for (const step of model.steps) list.append(buildPlanRow(step));
    fragment.append(list);
    if (model.status === "error") fragment.append(element("p", "side-error", model.error));
  }
  fragment.append(buildPlanContext(model.usage));
  fragment.append(buildPlanOpenCodeUsage());
  if (currentQuotaModel().status === "idle") loadOpenCodeUsage().catch(() => {});
  return fragment;
}

const CHANGE_STATUS_KEYS = { added: "panel.change.added", modified: "panel.change.modified", deleted: "panel.change.deleted" };
const PANEL_TAB_CAPTION_KEYS = {
  plan: "panel.caption.plan",
  changes: "panel.caption.changes",
  files: "panel.caption.files",
  checkpoints: "panel.caption.checkpoints",
  subagents: "panel.caption.subagents",
  terminal: "panel.caption.terminal",
};

// The terminal operates outside the DOM panel model as a standalone imperative module:
// xterm maintains its own buffer, and section redraw would destroy its state.
const TERMINAL_TAB = window.IgorekTerminal || null;
let terminalMountedFor = "";

function unmountTerminal() {
  if (!TERMINAL_TAB || !terminalMountedFor) return;
  TERMINAL_TAB.unmount();
  terminalMountedFor = "";
}

function renderTerminalTab() {
  if (!TERMINAL_TAB) return;
  const sessionId = state.selected || "";
  if (!sessionId) {
    unmountTerminal();
    const view = ui.sideViews.terminal;
    if (view) {
      view.textContent = "";
      view.append(panelEmpty(t("panel.session_required"), t("panel.session_required_hint")));
    }
    return;
  }
  if (terminalMountedFor === sessionId) return;
  unmountTerminal();
  const view = ui.sideViews.terminal;
  if (!view) return;
  TERMINAL_TAB.mount(view, sessionId, { t: t });
  terminalMountedFor = sessionId;
}

function resetPanelData() {
  const model = state.panel;
  model.workspace.resetSession();
  model.filesGeneration = (model.filesGeneration || 0) + 1;
  model.changes = { status: "idle", error: "", data: null };
  model.changesDiff = null;
  model.review = { mode: "session", base: "", includeUntracked: false, query: "", filter: "", collapsed: new Set(), width: 260, seq: 0, diffSeq: 0 };
  model.files = { status: "idle", error: "", data: null };
  closeFileViewerSession();
  model.fileView = null;
  model.checkpoints = { status: "idle", error: "", data: null, offset: 0, loadingMore: false };
  model.cp = { hash: "", short: "", status: "idle", changes: null, error: "", diff: null };
  model.subagents = { status: "idle", error: "", data: null, busy: "" };
  state.subagents.clear();
  // Session change: disconnect from the old terminal but do NOT turn it off on the server —
  // the shell remains active until session termination. On return, the screen will restore based on
  // the last confirmed seq from sessionStorage.
  unmountTerminal();
  resetPlanState();
  workspaceDisplayedTab = null;
  const selectedTab = model.workspace.active;
  model.tab = selectedTab?.kind === "tool" ? selectedTab.key : "workspace";
  if (model.open) activateWorkspaceTab(selectedTab);
}
function openPanel() {
  state.panel.open = true;
  const active = state.panel.workspace.active;
  if (active?.kind === "browser") workspaceBrowserCall("select", { id: active.id }).catch(() => {});
  renderPanel();
  refreshPanel(false);
  const view = state.panel.fileView;
  if (view && view.status === "idle") loadFileViewerContent().catch(() => {});
}
function closePanel() {
  state.panel.open = false;
  state.panel.fullscreen = false;
  suspendFileViewer();
  renderPanel();
}
function togglePanel() {
  if (state.panel.open) closePanel();
  else openPanel();
}
function togglePanelFullscreen() {
  state.panel.fullscreen = !state.panel.fullscreen;
  renderPanel();
}
function switchPanelTab(tab) {
  if (!PANEL_TAB_CAPTION_KEYS[tab]) return;
  activateWorkspaceTab(state.panel.workspace.open("tool", tab));
  refreshPanel(false);
}
function refreshPanel(force) {
  if (!state.panel.open) {
    renderPanel();
    return;
  }
  const sessionId = state.selected;
  if (!sessionId) {
    renderPanel();
    return;
  }
  const model = state.panel;
  if (model.tab === "plan") {
    if (force || state.plan.status === "idle") loadSessionJournal(sessionId, force);
    else renderPlanSection();
  } else if (model.tab === "changes") {
    if (force || model.changes.status === "idle") loadChanges(sessionId);
    else renderPanel();
  } else if (model.tab === "files") {
    if (force || model.files.status === "idle") loadFiles(sessionId);
    else renderPanel();
  } else if (model.tab === "subagents") {
    if (force || model.subagents.status === "idle") loadSubagents(sessionId);
    else renderPanel();
  } else if (model.tab === "terminal") {
    // The terminal loads independently (xterm lazily, WS by ticket); panel refresh
    // does not affect it, otherwise every update would recreate the shell.
    renderTerminalTab();
  } else if (model.tab === "checkpoints" && (force || model.checkpoints.status === "idle")) {
    loadCheckpoints(sessionId, false);
  } else {
    renderPanel();
  }
}
function reviewQuery(model, path) {
  return "?mode=" + encode(model.review.mode) + "&base=" + encode(model.review.base) + "&include_untracked=" + (model.review.includeUntracked ? "1" : "0") + (path ? "&path=" + encode(path) + "&revision=" + encode(model.changes.data?.revision || "") : "");
}
async function loadChanges(sessionId) {
  const model = state.panel, review = model.review, sequence = ++review.seq;
  ++review.diffSeq;
  model.changes = { status: "loading", error: "", data: model.changes.data };
  renderPanel();
  try {
    const data = await api("/api/sessions/" + encode(sessionId) + "/changes/review" + reviewQuery(model));
    if (state.selected !== sessionId || model.review !== review || sequence !== review.seq) return;
    model.changes = { status: "ready", error: "", data };
    if (review.mode === "branch") review.base = data.base || review.base;
    const files = data.files || [];
    const path = files.some(file => file.path === model.changesDiff?.path) ? model.changesDiff.path : files[0]?.path;
    model.changesDiff = null;
    if (path) { openChangesDiff(sessionId, path); return; }
  } catch (error) {
    if (state.selected !== sessionId || model.review !== review || sequence !== review.seq) return;
    model.changes = { status: "error", error: error.message, data: null };
    model.changesDiff = null;
  }
  renderPanel();
}
async function openChangesDiff(sessionId, path) {
  const model = state.panel, review = model.review, sequence = ++review.diffSeq;
  model.changesDiff = { path, status: "loading", error: "", data: null };
  renderPanel();
  try {
    const data = await api("/api/sessions/" + encode(sessionId) + "/changes/review" + reviewQuery(model, path));
    if (state.selected !== sessionId || model.review !== review || sequence !== review.diffSeq) return;
    model.changesDiff = { path, status: "ready", error: "", data };
    const files = model.changes.data?.files;
    const entry = files?.find(file => file.path === path);
    if (entry) {
      Object.assign(entry, { additions: data.additions, deletions: data.deletions, status: data.status, source: data.source, unavailable_reason: data.unavailable_reason });
      const unavailable = {};
      for (const file of files) if (file.source === "unavailable") { const reason = file.unavailable_reason || "read_error"; unavailable[reason] = (unavailable[reason] || 0) + 1; }
      const unknown = Object.values(unavailable).reduce((n,count) => n + count, 0);
      model.changes.data.totals = { files: files.length, additions: files.reduce((n,f) => n + (f.additions || 0), 0), deletions: files.reduce((n,f) => n + (f.deletions || 0), 0), unknown, counted_files: files.length - unknown, unavailable, partial: Boolean(unknown || model.changes.data.omitted) };
    }
  } catch (error) {
    if (state.selected !== sessionId || model.review !== review || sequence !== review.diffSeq) return;
    model.changesDiff = { path, status: "error", error: error.message, data: null };
  }
  renderPanel();
}
let sessionArtifactsTimer=null;
function refreshSessionArtifacts(){
  clearTimeout(sessionArtifactsTimer);
  const sessionId=state.selected;
  if(!sessionId)return;
  sessionArtifactsTimer=setTimeout(()=>{
    if(!state.paired||state.selected!==sessionId)return;
    state.panel.filesGeneration=(state.panel.filesGeneration||0)+1;
    state.panel.files.status='idle';
    window.IgorekSummary?.refresh(true);
    if(state.panel.open&&state.panel.tab==='files')loadFiles(sessionId);
  },350);
}
async function loadFiles(sessionId) {
  const model = state.panel;
  const generation = (model.filesGeneration || 0) + 1;
  model.filesGeneration = generation;
  model.files = { status: "loading", error: "", data: model.files.data };
  renderPanel();
  try {
    const data = await api("/api/sessions/" + encode(sessionId) + "/files");
    if (!state.paired || state.selected !== sessionId || model.filesGeneration !== generation) return;
    model.files = { status: "ready", error: "", data };
  } catch (error) {
    if (!state.paired || state.selected !== sessionId || model.filesGeneration !== generation) return;
    model.files = { status: "error", error: error.message, data: null };
    toast(error.message, "error");
  }
  renderPanel();
}
async function loadCheckpoints(sessionId, more) {
  const model = state.panel;
  const offset = more ? model.checkpoints.offset : 0;
  if (more) model.checkpoints.loadingMore = true;
  else model.checkpoints = { status: "loading", error: "", data: model.checkpoints.data, offset: 0, loadingMore: false };
  renderPanel();
  try {
    const data = await api(
      "/api/sessions/" + encode(sessionId) + "/checkpoints?limit=50&offset=" + encode(offset),
    );
    if (state.selected !== sessionId) return;
    const page = Array.isArray(data.checkpoints) ? data.checkpoints : [];
    const checkpoints = more ? (model.checkpoints.data?.checkpoints || []).concat(page) : page;
    model.checkpoints = {
      status: "ready", error: "", data: { ...data, checkpoints },
      offset: offset + page.length, loadingMore: false,
    };
  } catch (error) {
    if (state.selected !== sessionId) return;
    model.checkpoints = { status: "error", error: error.message, data: null, offset: 0, loadingMore: false };
    toast(error.message, "error");
  }
  renderPanel();
}
async function openCheckpoint(sessionId, checkpoint) {
  const model = state.panel;
  const hash = String(checkpoint.hash || "");
  model.cp = {
    hash, short: checkpoint.short_hash || hash.slice(0, 8),
    status: "loading", changes: null, error: "", diff: null,
  };
  renderPanel();
  try {
    const data = await api(
      "/api/sessions/" + encode(sessionId) + "/checkpoints/" + encode(hash) + "/changes",
    );
    if (state.selected !== sessionId || model.cp.hash !== hash) return;
    model.cp.status = "ready";
    model.cp.changes = data;
  } catch (error) {
    if (state.selected !== sessionId || model.cp.hash !== hash) return;
    model.cp.status = "error";
    model.cp.error = error.message;
    toast(error.message, "error");
  }
  renderPanel();
}
async function openCheckpointDiff(sessionId, hash, path) {
  const model = state.panel;
  model.cp.diff = { path, status: "loading", error: "", data: null };
  renderPanel();
  try {
    const data = await api(
      "/api/sessions/" + encode(sessionId) + "/checkpoints/" + encode(hash) + "/diff?path=" + encode(path),
    );
    if (state.selected !== sessionId || model.cp.hash !== hash) return;
    model.cp.diff = { path, status: "ready", error: "", data };
  } catch (error) {
    if (state.selected !== sessionId || model.cp.hash !== hash) return;
    model.cp.diff = { path, status: "error", error: error.message, data: null };
    toast(error.message, "error");
  }
  renderPanel();
}
function panelEmpty(title, hint) {
  const box = element("div", "side-empty");
  box.append(element("strong", "", title), element("span", "", hint));
  return box;
}
function panelLoading() {
  return element("p", "side-note", t("panel.loading"));
}
function panelToolbar(note, actions) {
  const toolbar = element("div", "side-toolbar");
  toolbar.append(element("span", "side-toolbar-note", note));
  const box = element("div", "side-toolbar-actions");
  for (const action of actions || []) box.append(action);
  toolbar.append(box);
  return toolbar;
}
function panelRefreshButton(onClick) {
  const button = element("button", "mini-button", t("panel.refresh"));
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}
function panelViewButton(onClick) {
  const button = element("button", "mini-button", t("panel.view"));
  button.type = "button";
  button.title = t("panel.view_title");
  button.addEventListener("click", onClick);
  return button;
}
function panelBackButton(label, onClick) {
  const button = element("button", "mini-button", "← " + label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}
const SUBAGENT_STATUS_KEYS = {
  running: "subagents.status.running", ok: "subagents.status.ok",
  error: "subagents.status.error", cancelled: "subagents.status.cancelled",
};
function subagentStatusKey(status) {
  return SUBAGENT_STATUS_KEYS[status] || "subagents.status.unknown";
}
function subagentStatusClass(status) {
  if (status === "running") return "running";
  if (status === "ok") return "done";
  if (status === "cancelled") return "stopped";
  return "error";
}
function subagentDuration(record) {
  const ms = Number(record.duration_ms || 0);
  if (!ms) return "";
  if (ms >= 1000) return (ms / 1000).toFixed(1) + " " + t("subagents.seconds");
  return ms + " " + t("subagents.ms");
}
function subagentMetaText(record) {
  const parts = [];
  const duration = subagentDuration(record);
  if (duration) parts.push(duration);
  if (record.model) parts.push(record.model);
  if (record.profile) parts.push(record.profile);
  if (Number(record.tool_calls_count || 0)) parts.push(t("subagents.tools", { n: record.tool_calls_count }));
  if (record.last_tool) parts.push(record.last_tool);
  return parts.join(" · ");
}
// Common layout for the sub-agent card: used in the «Agents» panel and
// in the expanded card for a child launch in the chat feed.
function buildSubagentBody(record) {
  const body = element("div", "subagent-body");
  const meta = subagentMetaText(record);
  if (meta) body.append(element("div", "subagent-meta", meta));
  if (record.last_stage) body.append(element("div", "subagent-stage", record.last_stage));
  if (record.error) body.append(element("div", "subagent-error", record.error));
  else if (record.summary) body.append(element("pre", "subagent-summary", record.summary));
  return body;
}
function buildSubagentRow(record) {
  const card = element("article", "subagent-card " + subagentStatusClass(record.status));
  const head = element("div", "subagent-head");
  head.append(element("span", "subagent-glyph", "⚙"));
  const text = element("div", "subagent-headtext");
  text.append(element("strong", "subagent-title", record.goal_title || t("subagents.untitled")));
  text.append(element("span", "subagent-state", t(subagentStatusKey(record.status))));
  head.append(text);
  const actions = element("div", "subagent-actions");
  if (record.status === "running") {
    const stop = element("button", "mini-button danger", t("subagents.stop"));
    stop.type = "button";
    stop.disabled = Boolean(state.panel.subagents.busy);
    stop.addEventListener("click", () => cancelSubagent(record.subagent_id));
    actions.append(stop);
  }
  head.append(actions);
  card.append(head, buildSubagentBody(record));
  return card;
}
function agentTreeOrder(sessions) {
  const byId = new Map(sessions.map((node) => [node.session_id, node]));
  const children = new Map();
  for (const node of sessions) {
    const parent = byId.has(node.parent_session_id) ? node.parent_session_id : null;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(node);
  }
  const seen = new Set();
  const ordered = [];
  function visit(node, depth) {
    if (seen.has(node.session_id)) return;
    seen.add(node.session_id);
    ordered.push({ ...node, treeDepth: depth });
    for (const child of children.get(node.session_id) || []) visit(child, depth + 1);
  }
  for (const node of children.get(null) || []) visit(node, 0);
  for (const node of sessions) visit(node, 0);
  return ordered;
}
function agentStatusText(status) {
  const labels = {
    running: ["Работает", "Running"], waiting_approval: ["Ждёт подтверждения", "Waiting for approval"],
    waiting_input: ["Ждёт ответа", "Waiting for answer"], completed: ["Завершён", "Completed"],
    failed: ["Ошибка", "Failed"], cancelled: ["Остановлен", "Cancelled"],
  };
  const pair = labels[status];
  return pair ? pair[lang === "en" ? 1 : 0] : status;
}
function renderAgentNavigation() {
  const nav = byId("agent-navigation");
  if (!nav) return;
  nav.replaceChildren();
  const meta = state.session?.meta || {};
  nav.hidden = !meta.agent_session;
  if (!meta.agent_session) return;
  for (const [id, label] of [[meta.parent_session_id, lang === "en" ? "← Parent" : "← Родитель"], [meta.root_session_id, "Main"]]) {
    if (!id || id === state.selected) continue;
    const button = element("button", "mini-button", label);
    button.type = "button";
    button.addEventListener("click", () => selectSession(id));
    nav.append(button);
  }
  const agents = element("button", "mini-button", lang === "en" ? "Agents" : "Агенты");
  agents.type = "button";
  agents.addEventListener("click", () => { switchPanelTab("subagents"); openPanel(); });
  nav.append(agents, element("span", "agent-view-note", lang === "en" ? "Child session · live view" : "Дочерняя сессия · живой просмотр"));
}
function buildSubagentsView() {
  const model = state.panel;
  const fragment = document.createDocumentFragment();
  if (!state.selected) return sessionRequired();
  fragment.append(panelToolbar(t("panel.subagents_title"), [panelRefreshButton(() => loadSubagents(state.selected))]));
  if (!model.subagents.data && (model.subagents.status === "idle" || model.subagents.status === "loading")) {
    fragment.append(panelLoading()); return fragment;
  }
  if (model.subagents.error) fragment.append(element("p", "side-error", model.subagents.error));
  const list = element("div", "side-list agent-session-tree");
  for (const node of agentTreeOrder(model.subagents.data?.sessions || [])) {
    const button = element("button", "agent-session-row" + (node.session_id === state.selected ? " active" : ""));
    button.type = "button";
    button.style.paddingInlineStart = (12 + Math.min(node.treeDepth, 8) * 16) + "px";
    button.setAttribute("aria-current", node.session_id === state.selected ? "page" : "false");
    button.append(element("strong", "agent-session-title", node.parent_session_id ? node.title || node.role : "Main"));
    button.append(element("span", "agent-session-status", agentStatusText(node.status)));
    if (node.activity) button.append(element("span", "agent-session-activity", node.activity));
    button.addEventListener("click", () => { if (state.selected !== node.session_id) selectSession(node.session_id); });
    list.append(button);
  }
  fragment.append(list);
  return fragment;
}

async function loadSubagents(sessionId) {
  const model = state.panel;
  model.subagents = { ...model.subagents, status: "loading", error: "" };
  renderPanel();
  try {
    const data = await api("/api/sessions/" + encode(sessionId) + "/agents");
    if (state.selected !== sessionId) return;
    model.subagents = { status: "ready", error: "", data, busy: "" };
  } catch (error) {
    if (state.selected !== sessionId) return;
    model.subagents = { status: "error", error: error.message, data: null, busy: "" };
    toast(error.message, "error");
  }
  renderPanel();
}
async function cancelSubagent(subagentId) {
  const sessionId = state.selected;
  if (!sessionId || !subagentId) return;
  state.panel.subagents.busy = subagentId;
  renderPanel();
  try {
    await api(
      "/api/sessions/" + encode(sessionId) + "/subagents/" + encode(subagentId) + "/cancel",
      { method: "POST", body: JSON.stringify({}) },
    );
    toast(t("subagents.stopping"));
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.panel.subagents.busy = "";
    loadSubagents(sessionId);
  }
}
function renderPanelSection(container, build) {
  const scrollTop = container.scrollTop;
  container.replaceChildren(build());
  container.scrollTop = scrollTop;
}
function renderPanel() {
  window.IgorekAppHistory?.schedule();
  window.IgorekSummary?.sync();
  if(!state.panel.open||state.panel.workspace.active?.kind!=='opencode')window.IgorekDesktop?.opencode?.('hide').catch(()=>{});
  ui.sidePanel.hidden = !state.panel.open;
  const fullscreen = Boolean(state.panel.open && state.panel.fullscreen);
  state.panel.fullscreen = fullscreen;
  const panelHost = fullscreen ? document.querySelector(".main-panel") : document.querySelector(".workspace-row");
  if (ui.sidePanel.parentNode !== panelHost) panelHost.append(ui.sidePanel);
  ui.sidePanel.classList.toggle("panel-fullscreen", fullscreen);
  ui.panelFullscreen.hidden = false;
  ui.panelFullscreen.replaceChildren(workspaceGlyph(fullscreen ? "collapse" : "expand"));
  ui.panelFullscreen.title = fullscreen ? t("panel.collapse") : t("panel.expand");
  ui.panelFullscreen.setAttribute("aria-label", ui.panelFullscreen.title);
  ui.panelFullscreen.setAttribute("aria-pressed", String(fullscreen));
  ui.panelButton.classList.toggle("active", state.panel.open);
  ui.panelButton.setAttribute("aria-expanded", String(state.panel.open));
  renderWorkspaceTabs();
  if (!state.panel.open) {
    unmountTerminal();
    window.IgorekPlugins?.unmount?.();
    clearTimeout(workspaceChatTimer); workspaceChatGeneration++;
    if (["chat", "plugins"].includes(state.panel.workspace.active?.kind)) byId("workspace-special").dataset.tab = "";
    workspaceBrowserCall("hide").catch(() => {});
    return;
  }
  for (const tab of ["plan", "changes", "files", "checkpoints", "subagents", "terminal"]) {
    const active = state.panel.tab === tab;
    const button = ui.sideTabs[tab];
    if (button) {
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    }
    if (ui.sideViews[tab]) ui.sideViews[tab].hidden = !active;
  }
  ui.sidePanelCaption.textContent = PANEL_TAB_CAPTION_KEYS[state.panel.tab]
    ? t(PANEL_TAB_CAPTION_KEYS[state.panel.tab]) : t("panel.data");
  const special = state.panel.tab === "workspace";
  byId("workspace-special").hidden = !special;
  if (special) { renderWorkspaceSpecial(); return; }
  if (state.panel.tab === "plan") renderPanelSection(ui.sideViews.plan, buildPlanView);
  else if (state.panel.tab === "changes") renderPanelSection(ui.sideViews.changes, buildChangesView);
  else if (state.panel.tab === "files") renderPanelSection(ui.sideViews.files, buildFilesView);
  else if (state.panel.tab === "subagents") renderPanelSection(ui.sideViews.subagents, buildSubagentsView);
  else if (state.panel.tab === "terminal") renderTerminalTab();
  else if (state.panel.tab === "checkpoints") renderPanelSection(ui.sideViews.checkpoints, buildCheckpointsView);
}
function sessionRequired() {
  return state.selected ? null : panelEmpty(t("panel.session_required"), t("panel.session_required_hint"));
}
function buildChangesView() {
  const model = state.panel;
  const guard = sessionRequired();
  if (guard) return guard;
  if (model.fileView && model.fileView.origin === "changes") return buildFileViewer(model);
  return window.IgorekChanges.render({
    model: model.review, data: model.changes.data || {}, diff: model.changesDiff,
    loading: ["loading", "idle"].includes(model.changes.status), error: model.changes.error,
    english: lang === "en", buildDiff,
    onSelect: path => openChangesDiff(state.selected, path),
    onRefresh: () => loadChanges(state.selected),
    onMode: mode => { model.review.mode = mode; model.review.base = ""; model.changes.data = null; model.changesDiff = null; loadChanges(state.selected); },
    onBase: base => { model.review.base = base; model.changes.data = null; model.changesDiff = null; loadChanges(state.selected); },
    onUntracked: value => { model.review.includeUntracked = value; model.changes.data = null; model.changesDiff = null; loadChanges(state.selected); },
    onView: path => viewChangedFile(path),
  });
}
function buildChangeRow(file, onOpen) {
  const row = element("button", "change-row");
  row.type = "button";
  row.title = t("panel.show_diff");
  const copy = element("span", "change-copy");
  copy.append(element("span", "change-path", file.path || ""));
  const meta = element("span", "change-meta");
  const changeKey = CHANGE_STATUS_KEYS[file.status];
  meta.append(element("span", "change-status " + (file.status || "modified"), changeKey ? t(changeKey) : t("panel.change.modified")));
  meta.append(element("span", "change-additions", "+" + Number(file.additions || 0)));
  meta.append(element("span", "change-deletions", "−" + Number(file.deletions || 0)));
  copy.append(meta);
  row.append(copy, element("span", "cp-open", "→"));
  row.addEventListener("click", onOpen);
  return row;
}
const DIFF_SOURCE_KEYS = { added: "panel.diff_source_added", unavailable: "panel.diff_source_unavailable" };
function diffStatsText(data) {
  if (!data) return "";
  const parts = ["+" + Number(data.additions || 0), "−" + Number(data.deletions || 0)];
  if (data.source && data.source !== "checkpoint") {
    const sourceText = DIFF_SOURCE_KEYS[data.source] ? t(DIFF_SOURCE_KEYS[data.source]) : data.source;
    parts.push(t("panel.diff_source", { source: sourceText }));
  }
  if (data.truncated) parts.push(t("panel.diff_partial"));
  return parts.join(" · ");
}
function buildDiff(model) {
  const fragment = document.createDocumentFragment();
  if (model.status === "loading" || model.status === "idle") {
    fragment.append(panelLoading());
    return fragment;
  }
  if (model.status === "error") {
    fragment.append(element("p", "side-error", model.error));
    return fragment;
  }
  const data = model.data || {};
  const head = element("div", "diff-head");
  head.append(element("div", "diff-path", data.path || model.path || ""));
  head.append(element("div", "diff-stats", diffStatsText(data)));
  fragment.append(head);
  const body = element("div", "diff-body");
  if (data.source === "unavailable") {
    body.append(element("p", "diff-note", data.truncated
      ? t("panel.diff_too_large")
      : (data.note || t("panel.diff_no_baseline"))));
    fragment.append(body);
    return fragment;
  }
  const hunks = Array.isArray(data.hunks) ? data.hunks : [];
  if (!hunks.length) {
    body.append(element("p", "diff-note", t("panel.diff_no_changes")));
    fragment.append(body);
    return fragment;
  }
  let previousEnd = 0;
  for (const hunk of hunks) {
    const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(hunk.header || "");
    let oldNumber = match ? Number(match[1]) : 0, newNumber = match ? Number(match[2]) : 0;
    if (oldNumber > previousEnd + 1) body.append(element("div", "diff-gap", (oldNumber - previousEnd - 1) + (lang === "en" ? " unchanged lines" : " строк без изменений")));
    body.append(element("div", "diff-hunk-head", hunk.header || "@@"));
    const lines = element("div", "diff-lines");
    for (const line of hunk.lines || []) {
      const kind = line.type === "+" ? "add" : line.type === "-" ? "del" : "context";
      const row = element("div", "diff-line " + kind);
      row.append(element("span", "diff-number", line.type === "+" ? "" : String(oldNumber++)), element("span", "diff-number", line.type === "-" ? "" : String(newNumber++)));
      row.append(element("span", "diff-sign", line.type === " " ? " " : String(line.type || " ")));
      const text = element("span", "diff-text");
      const code = element("code", "", line.text ?? "");
      text.append(code); row.append(text);
      if (FILE_VIEWER && hunks.reduce((n, h) => n + (h.lines || []).length, 0) < 1500) FILE_VIEWER.applyHighlight(code, line.text ?? "", FILE_VIEWER.grammarFor({ name: data.path || model.path || "" }));
      lines.append(row);
    }
    body.append(lines);
    previousEnd = oldNumber - 1;
  }
  if (data.truncated) body.append(element("p", "diff-note", t("panel.diff_partial")));
  fragment.append(body);
  return fragment;
}
const FILE_VIEW_TEXT_LIMIT = 256 * 1024;
const FILE_VIEW_IMAGE_LIMIT = 2 * 1024 * 1024;
const FILE_VIEWER = window.IgorekFileViewer || null;
const FILE_VIEWER_LIMITS = (FILE_VIEWER && FILE_VIEWER.limits) || {};
const FILE_VIEW_SPREADSHEET_LIMIT = FILE_VIEWER_LIMITS.spreadsheetBytes || 10 * 1024 * 1024;
const FILE_VIEW_HIGHLIGHT_LIMIT = FILE_VIEWER_LIMITS.highlightBytes || 100 * 1024;
const FILE_VIEW_ROWS_PER_PAGE = FILE_VIEWER_LIMITS.rowsPerPage || 100;
const FILE_VIEW_MARKDOWN_EXTENSIONS = new Set(["md", "markdown", "mdown", "mkd"]);
const FILE_VIEW_MARKUP_EXTENSIONS = new Set(["html", "htm", "xhtml", "svg"]);
const FILE_VIEW_MARKUP_MIME = new Set(["text/html", "application/xhtml+xml", "image/svg+xml"]);
const FILE_VIEW_IMAGE_MIME = new Set([
  "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/svg+xml", "image/avif",
]);
const FILE_VIEW_TEXT_MIME = new Set([
  "application/json", "application/xml", "application/javascript", "application/x-sh",
  "application/x-yaml", "application/yaml", "application/toml", "application/sql",
]);
const FILE_VIEW_TEXT_EXTENSIONS = new Set([
  "txt", "log", "md", "markdown", "rst", "csv", "tsv", "json", "jsonl", "ndjson", "yaml", "yml",
  "toml", "ini", "cfg", "conf", "py", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "html", "htm",
  "css", "scss", "sass", "less", "sh", "bash", "zsh", "fish", "rs", "go", "java", "kt", "kts",
  "c", "h", "cc", "cpp", "hpp", "cs", "rb", "php", "pl", "lua", "swift", "dart", "sql", "xml",
  "svg", "graphql", "gql", "env", "diff", "patch",
]);
const SPREADSHEET_EXTENSIONS = new Set(["xlsx", "xls", "xlsm", "xlsb"]);
const SPREADSHEET_MIME = new Set([
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "application/vnd.ms-excel.sheet.macroenabled.12",
  "application/vnd.ms-excel.sheet.binary.macroenabled.12",
]);
const SPREADSHEET_EXCLUDED_EXTENSIONS = new Set(["csv", "tsv", "txt"]);
const CODE_LANGUAGE_LABELS = {
  py: "python", pyi: "python", js: "javascript", mjs: "javascript", cjs: "javascript",
  ts: "typescript", yml: "yaml", sh: "bash", rs: "rust", kt: "kotlin", md: "markdown",
  htm: "html", jsonl: "json",
};
function fileViewerExtension(file) {
  const name = String((file && file.name) || "").toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1) : "";
}
function fileViewerKind(file) {
  if (FILE_VIEWER && FILE_VIEWER.kind) return FILE_VIEWER.kind(file);
  const mime = String((file && file.mime_type) || "").toLowerCase().split(";")[0].trim();
  const extension = fileViewerExtension(file);
  if (FILE_VIEW_MARKUP_MIME.has(mime) || FILE_VIEW_MARKUP_EXTENSIONS.has(extension)) return "html";
  if (SPREADSHEET_EXTENSIONS.has(extension)) return "spreadsheet";
  if (SPREADSHEET_MIME.has(mime) && !SPREADSHEET_EXCLUDED_EXTENSIONS.has(extension)) return "spreadsheet";
  if (mime.startsWith("image/") || FILE_VIEW_IMAGE_MIME.has(mime)) return "image";
  if (mime === "text/markdown" || FILE_VIEW_MARKDOWN_EXTENSIONS.has(extension)) return "markdown";
  if (mime.startsWith("text/") || FILE_VIEW_TEXT_MIME.has(mime) || FILE_VIEW_TEXT_EXTENSIONS.has(extension)) return "text";
  return "binary";
}
function downloadFileUrl(sessionId, fileId) {
  return "/api/sessions/" + encode(sessionId) + "/files/" + encode(fileId || "");
}
async function readCappedBody(response, limit) {
  if (!response.body || typeof response.body.getReader !== "function") {
    const buffer = await response.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    return bytes.length > limit
      ? { bytes: bytes.slice(0, limit), truncated: true }
      : { bytes, truncated: false };
  }
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  let truncated = false;
  while (true) {
    const step = await reader.read();
    if (step.done) break;
    const chunk = step.value || new Uint8Array(0);
    if (total + chunk.length > limit) {
      chunks.push(chunk.subarray(0, limit - total));
      total = limit;
      truncated = true;
      try { await reader.cancel(); } catch (error) { /* thread already closed */ }
      break;
    }
    chunks.push(chunk);
    total += chunk.length;
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return { bytes, truncated };
}
function looksBinary(bytes) {
  const edge = Math.min(bytes.length, 8192);
  for (let index = 0; index < edge; index += 1) {
    if (bytes[index] === 0) return true;
  }
  return false;
}
function base64FromBytes(bytes) {
  let binary = "";
  const step = 0x8000;
  for (let index = 0; index < bytes.length; index += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(index, index + step));
  }
  return window.btoa(binary);
}
function viewUnavailable(view, title, hint) {
  view.kind = "binary";
  view.status = "ready";
  view.fallbackTitle = title;
  view.fallbackHint = hint;
  renderPanel();
}
async function loadFileViewerContent() {
  const model = state.panel;
  const view = model.fileView;
  if (!view || !state.selected) return;
  const sessionId = state.selected;
  const file = view.file || {};
  if (view.kind === "binary") {
    viewUnavailable(
      view, t("viewer.unavailable_title"),
      t("viewer.binary_hint"),
    );
    return;
  }
  if (view.kind === "image" && Number(file.size || 0) > FILE_VIEW_IMAGE_LIMIT) {
    viewUnavailable(
      view, t("viewer.image_too_big"),
      t("viewer.image_limit", { size: formatBytes(FILE_VIEW_IMAGE_LIMIT) }),
    );
    return;
  }
  if (view.kind === "spreadsheet") {
    await loadSpreadsheetContent(view);
    if (state.panel.fileView === view && view.status === "ready") rememberWorkspaceFile(file);
    return;
  }
  view.status = "loading";
  view.error = "";
  renderPanel();
  try {
    const response = await fetch(downloadFileUrl(sessionId, file.id), { credentials: "same-origin" });
    if (state.panel.fileView !== view) return;
    if (response.status === 401) {
      showConnection();
      throw new Error(t("error.connection_closed"));
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || t("viewer.read_failed", { status: response.status }));
    }
    if (view.kind === "image") {
      const buffer = await response.arrayBuffer();
      if (state.panel.fileView !== view) return;
      view.dataUrl = "data:" + (file.mime_type || "application/octet-stream") + ";base64,"
        + base64FromBytes(new Uint8Array(buffer));
    } else {
      const capped = await readCappedBody(response, FILE_VIEW_TEXT_LIMIT);
      if (state.panel.fileView !== view) return;
      if (looksBinary(capped.bytes)) {
        viewUnavailable(
          view, t("viewer.binary_title"),
          t("viewer.binary_preview_off"),
        );
        return;
      }
      view.text = new TextDecoder("utf-8", { fatal: false }).decode(capped.bytes);
      view.truncated = capped.truncated;
    }
    view.status = "ready";
    rememberWorkspaceFile(file);
    if (view.kind === "html" && !view.mode) view.mode = view.truncated ? "code" : "preview";
  } catch (error) {
    if (state.panel.fileView !== view) return;
    view.status = "error";
    view.error = error.message;
  }
  renderPanel();
}
function closeFileViewerSession() {
  const view = state.panel.fileView;
  if (!view || !view.book) return;
  try { view.book.dispose(); } catch (error) { /* The worker has already completed */ }
  view.book = null;
}
// Closing the panel does not reset the selected file but frees the parsed
// book: when the panel is reopened again, the table will load anew.
function suspendFileViewer() {
  const view = state.panel.fileView;
  if (!view) return;
  const wasSpreadsheet = view.kind === "spreadsheet" && view.status !== "error";
  closeFileViewerSession();
  if (wasSpreadsheet) {
    view.status = "idle";
    view.sheetStatus = "idle";
    view.sheetRows = null;
  }
}
function ensureWorkbookSession(view) {
  if (view.book && !view.book.isDisposed()) return view.book;
  if (!FILE_VIEWER || !FILE_VIEWER.createWorkbookSession) return null;
  view.book = FILE_VIEWER.createWorkbookSession({ workerUrl: "/workers/xlsx-worker.js" });
  return view.book;
}
function spreadsheetErrorMessage(error) {
  const code = error && error.code;
  if (code === "timeout") return t("viewer.sheet_timeout");
  if (code === "encrypted") return t("viewer.sheet_encrypted");
  if (code === "corrupt") return t("viewer.sheet_corrupt");
  if (code === "cancelled") return "";
  return t("viewer.sheet_error", { error: (error && error.message) || "" });
}
function spreadsheetTooBig(size) {
  if (FILE_VIEWER && FILE_VIEWER.spreadsheetTooBig) return FILE_VIEWER.spreadsheetTooBig(size);
  return Number(size || 0) > FILE_VIEW_SPREADSHEET_LIMIT;
}
async function loadSpreadsheetContent(view) {
  const sessionId = state.selected;
  const file = view.file || {};
  if (spreadsheetTooBig(file.size)) {
    view.status = "error";
    view.error = t("viewer.sheet_too_big", { size: formatBytes(FILE_VIEW_SPREADSHEET_LIMIT) });
    renderPanel();
    return;
  }
  view.status = "loading";
  view.error = "";
  view.sheets = [];
  view.sheetRows = null;
  view.sheetStatus = "idle";
  renderPanel();
  try {
    const response = await fetch(downloadFileUrl(sessionId, file.id), { credentials: "same-origin" });
    if (state.panel.fileView !== view) return;
    if (response.status === 401) {
      showConnection();
      throw new Error(t("error.connection_closed"));
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || t("viewer.read_failed", { status: response.status }));
    }
    const declared = Number(response.headers.get("content-length") || 0);
    if (declared > FILE_VIEW_SPREADSHEET_LIMIT) {
      view.status = "error";
      view.error = t("viewer.sheet_too_big", { size: formatBytes(FILE_VIEW_SPREADSHEET_LIMIT) });
      renderPanel();
      return;
    }
    const capped = await readCappedBody(response, FILE_VIEW_SPREADSHEET_LIMIT);
    if (state.panel.fileView !== view) return;
    // Truncated bytes are not parsed: an incomplete ZIP would incorrectly flag as «damaged».
    if (capped.truncated) {
      view.status = "error";
      view.error = t("viewer.sheet_too_big", { size: formatBytes(FILE_VIEW_SPREADSHEET_LIMIT) });
      renderPanel();
      return;
    }
    const book = ensureWorkbookSession(view);
    if (!book) throw new Error(t("viewer.sheet_unavailable"));
    const sheets = await book.parse(capped.bytes);
    if (state.panel.fileView !== view) return;
    view.sheets = sheets;
    view.status = "ready";
    if (sheets.length) await loadSpreadsheetPage(view, 0, 0);
  } catch (error) {
    if (state.panel.fileView !== view) return;
    if (error && error.code === "cancelled") return;
    view.status = "error";
    view.error = spreadsheetErrorMessage(error);
  }
  renderPanel();
}
async function loadSpreadsheetPage(view, sheetIndex, page) {
  const meta = view.sheets[sheetIndex];
  if (!meta) return;
  const book = view.book;
  if (!book || book.isDisposed()) return;
  const start = Math.max(0, page) * FILE_VIEW_ROWS_PER_PAGE;
  const end = Math.min(start + FILE_VIEW_ROWS_PER_PAGE, meta.rows) - 1;
  view.sheetIndex = sheetIndex;
  view.sheetPage = Math.max(0, page);
  view.sheetStatus = "loading";
  view.sheetError = "";
  renderPanel();
  try {
    const result = await book.sheet(sheetIndex, start, Math.max(start, end));
    if (state.panel.fileView !== view) return;
    view.sheetRows = result;
    view.sheetStatus = "ready";
  } catch (error) {
    if (state.panel.fileView !== view) return;
    if (error && error.code === "cancelled") return;
    view.sheetStatus = "error";
    view.sheetError = spreadsheetErrorMessage(error);
    if (error && error.code === "timeout") {
      view.status = "error";
      view.error = view.sheetError;
    }
  }
  renderPanel();
}
async function openFileViewer(file, origin) {
  if (!state.selected || !file) return;
  const tab = PANEL_TAB_CAPTION_KEYS[origin] ? origin : "files";
  const item = state.panel.workspace.open("file", state.selected + ":" + file.id, file.name || file.path);
  activateWorkspaceTab(item);
  state.panel.tab = tab;
  item.origin = tab;
  state.panel.fileView = item.fileView || {
    file: { ...file }, origin: tab, kind: fileViewerKind(file),
    status: "idle", error: "", text: "", dataUrl: "", truncated: false,
    fallbackTitle: "", fallbackHint: "", mode: "",
    book: null, sheets: [], sheetIndex: 0, sheetPage: 0,
    sheetStatus: "idle", sheetError: "", sheetRows: null,
  };
  item.fileView = state.panel.fileView;
  renderPanel();
  if (state.panel.fileView.status === "idle") await loadFileViewerContent();
}
async function ensureSessionFiles(sessionId) {
  const model = state.panel;
  if (model.files.status === "ready" && model.files.data) {
    return (model.files.data && model.files.data.files) || [];
  }
  const data = await api("/api/sessions/" + encode(sessionId) + "/files");
  if (state.selected === sessionId) model.files = { status: "ready", error: "", data };
  return (data && data.files) || [];
}
async function viewChangedFile(path) {
  const sessionId = state.selected;
  if (!sessionId) return;
  try {
    const files = await ensureSessionFiles(sessionId);
    const name = String(path || "").split("/").pop();
    const exact=files.find((item)=>item.path===path);
    const candidates=files.filter((item)=>!item.path&&item.name===name);
    const match = exact||(candidates.length===1?candidates[0]:null);
    if (!match) {
      toast(t("viewer.not_in_attachments"));
      return;
    }
    await openFileViewer({ ...match, path }, "changes");
  } catch (error) {
    toast(error.message, "error");
  }
}
// «Open in folder»: the path is known for files from «Changes»; for bridge attachments,
// only the name exists, so the server searches by the working directory and accepts
// a match only if the result is unambiguous.
async function revealFile(view) {
  const file = (view && view.file) || {};
  const path = String(file.path || file.name || "");
  if (!state.selected || !path) return;
  try {
    await api("/api/sessions/" + encode(state.selected) + "/reveal", {
      method: "POST", body: JSON.stringify({ path }),
    });
    toast(t("reveal.opened"));
  } catch (error) {
    if (error.code === "no_display") toast(t("reveal.no_display"));
    else toast(t("reveal.failed", { error: error.message }));
  }
}
function appendViewerTruncation(fragment, view) {
  if (!view.truncated) return;
  fragment.append(element(
    "p", "side-note",
    t("viewer.truncated", { size: formatBytes(FILE_VIEW_TEXT_LIMIT) }),
  ));
}
function previewFileUrl(sessionId, fileId) {
  return downloadFileUrl(sessionId, fileId) + "?preview=1";
}
function fileViewerLanguage(file) {
  const extension = fileViewerExtension(file);
  if (!extension) return t("viewer.language_file");
  return CODE_LANGUAGE_LABELS[extension] || extension;
}
function fileViewerGrammar(file) {
  if (FILE_VIEWER && FILE_VIEWER.grammarFor) return FILE_VIEWER.grammarFor(file);
  return "";
}
function buildCodeBlock(text, language, grammar) {
  const wrapper = element("div", "md-code file-viewer-code");
  const head = element("div", "md-code-head");
  head.append(element("span", "md-code-language", language || t("viewer.language_code")));
  const copy = element("button", "md-code-copy", t("viewer.copy"));
  copy.type = "button";
  copy.addEventListener("click", () => {
    const clipboard = window.navigator && window.navigator.clipboard;
    if (!clipboard || !clipboard.writeText) {
      copy.textContent = t("viewer.copy_unavailable");
      return;
    }
    clipboard.writeText(text).then(
      () => { copy.textContent = t("viewer.copied"); },
      () => { copy.textContent = t("viewer.copy_unavailable"); },
    );
  });
  head.append(copy);
  const pre = element("pre", "md-pre file-viewer-text");
  pre.tabIndex = 0;
  const code = element("code", language ? "language-" + language : "");
  code.textContent = text;
  pre.append(code);
  wrapper.append(head, pre);
  if (FILE_VIEWER && FILE_VIEWER.applyHighlight) {
    FILE_VIEWER.applyHighlight(code, text, grammar).then((result) => {
      if (result && result.reason === "limit" && wrapper.isConnected) {
        wrapper.append(element(
          "p", "side-note",
          t("viewer.highlight_limit", { size: formatBytes(FILE_VIEW_HIGHLIGHT_LIMIT) }),
        ));
      }
    }).catch(() => {});
  }
  return wrapper;
}
function viewerModeButton(label, mode, view) {
  const active = view.mode === mode;
  const button = element("button", "mini-button" + (active ? " active" : ""), label);
  button.type = "button";
  button.setAttribute("aria-pressed", String(active));
  button.title = mode === "preview" ? t("viewer.preview_title") : t("viewer.code_title");
  button.addEventListener("click", () => {
    if (view.mode === mode) return;
    view.mode = mode;
    renderPanel();
  });
  return button;
}
// The preview document is loaded from the ?preview=1 route: it carries its own
// sandboxed CSP, because a srcdoc frame inherits the interface CSP and would
// render without inline styles or scripts. sandbox="allow-scripts" alone keeps
// the document in an opaque origin with no access to the interface.
function buildMarkupPreview(file) {
  const box = element("div", "file-viewer-preview");
  const frame = document.createElement("iframe");
  frame.className = "file-viewer-frame";
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.setAttribute("title", t("viewer.frame_title", { name: file.name || t("file.default_name") }));
  frame.src = previewFileUrl(state.selected, file.id);
  box.append(frame);
  return box;
}
function spreadsheetColumnLabel(index) {
  if (FILE_VIEWER && FILE_VIEWER.columnLabel) return FILE_VIEWER.columnLabel(index);
  return String(Number(index) + 1);
}
function buildSpreadsheetGrid(view, meta) {
  const box = element("div", "sheet-scroll");
  const table = element("table", "sheet-grid");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  headRow.append(element("th", "sheet-corner", "#"));
  for (let col = 0; col < meta.cols; col += 1) {
    headRow.append(element("th", "sheet-col", spreadsheetColumnLabel(meta.colStart + col)));
  }
  head.append(headRow);
  table.append(head);
  const body = document.createElement("tbody");
  const rows = (view.sheetRows && view.sheetRows.rows) || [];
  const start = Number(view.sheetRows && view.sheetRows.start) || 0;
  rows.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.append(element("th", "sheet-row", String(meta.rowStart + start + index + 1)));
    for (let col = 0; col < meta.cols; col += 1) {
      const td = document.createElement("td");
      const value = row[col];
      if (value !== undefined && value !== null) td.textContent = String(value);
      if (!td.textContent) td.classList.add("sheet-empty");
      tr.append(td);
    }
    body.append(tr);
  });
  table.append(body);
  box.append(table);
  return box;
}
function buildSpreadsheetPager(view, meta) {
  const pages = Math.max(1, Math.ceil(meta.rows / FILE_VIEW_ROWS_PER_PAGE));
  const pager = element("div", "sheet-pager");
  const previous = element("button", "mini-button sheet-page-prev", t("viewer.sheet_prev"));
  previous.type = "button";
  previous.disabled = view.sheetPage <= 0;
  previous.addEventListener("click", () => {
    loadSpreadsheetPage(view, view.sheetIndex, view.sheetPage - 1).catch(() => {});
  });
  const next = element("button", "mini-button sheet-page-next", t("viewer.sheet_next"));
  next.type = "button";
  next.disabled = view.sheetPage >= pages - 1;
  next.addEventListener("click", () => {
    loadSpreadsheetPage(view, view.sheetIndex, view.sheetPage + 1).catch(() => {});
  });
  const from = meta.rows ? view.sheetPage * FILE_VIEW_ROWS_PER_PAGE + 1 : 0;
  const to = Math.min(meta.rows, (view.sheetPage + 1) * FILE_VIEW_ROWS_PER_PAGE);
  pager.append(
    previous,
    element("span", "sheet-page-status", t("viewer.sheet_page", { from, to, total: meta.rows })),
    next,
  );
  return pager;
}
function buildSpreadsheetView(view) {
  const wrap = element("div", "file-viewer-sheet");
  if (!view.sheets.length) {
    wrap.append(panelEmpty(t("viewer.sheet_empty"), t("viewer.sheet_empty_hint")));
    return wrap;
  }
  const tabs = element("div", "sheet-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", t("viewer.sheet_tabs_aria"));
  view.sheets.forEach((sheet, index) => {
    const active = index === view.sheetIndex;
    const name = sheet.name || t("viewer.sheet_untitled");
    const tab = element("button", "sheet-tab" + (active ? " active" : ""), name);
    tab.type = "button";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(active));
    tab.title = name;
    tab.addEventListener("click", () => {
      if (index === view.sheetIndex && view.sheetStatus === "ready") return;
      loadSpreadsheetPage(view, index, 0).catch(() => {});
    });
    tabs.append(tab);
  });
  wrap.append(tabs);
  const meta = view.sheets[view.sheetIndex];
  if (!meta) return wrap;
  if (meta.images && meta.images.length) {
    const images = element("div", "sheet-charts");
    meta.images.forEach((embedded, index) => {
      const card = element("figure", "sheet-chart sheet-chart-image");
      const image = document.createElement("img");
      image.className = "file-viewer-image";
      image.src = embedded.src;
      image.alt = t("viewer.chart_title") + " " + (index + 1);
      image.loading = "lazy";
      card.append(image);
      images.append(card);
    });
    wrap.append(images);
  }
  if (meta.charts && meta.charts.length && FILE_VIEWER && FILE_VIEWER.renderSpreadsheetCharts) {
    wrap.append(FILE_VIEWER.renderSpreadsheetCharts(meta.charts, {
      title: t("viewer.chart_title"), series: t("viewer.chart_series"),
      unsupported: t("viewer.chart_unsupported"), empty: t("viewer.chart_empty"), limit: t("viewer.chart_limit"),
    }));
  }
  if (meta.chartLimit) wrap.append(element("p", "side-note", t("viewer.chart_limit")));
  if (meta.truncated) {
    wrap.append(element(
      "p", "side-note",
      t("viewer.sheet_truncated", { rows: meta.rows, cols: meta.cols }),
    ));
  }
  wrap.append(element("p", "side-note", t("viewer.sheet_note")));
  if (view.sheetStatus === "loading" || view.sheetStatus === "idle") {
    wrap.append(panelLoading());
    return wrap;
  }
  if (view.sheetStatus === "error") {
    wrap.append(element("p", "side-error", view.sheetError || t("viewer.sheet_error", { error: "" })));
    return wrap;
  }
  if (!meta.rows || !meta.cols) {
    if ((meta.charts && meta.charts.length) || (meta.images && meta.images.length)) return wrap;
    wrap.append(panelEmpty(t("viewer.sheet_empty"), t("viewer.sheet_empty_hint")));
    return wrap;
  }
  wrap.append(buildSpreadsheetGrid(view, meta));
  if (meta.rows > FILE_VIEW_ROWS_PER_PAGE) wrap.append(buildSpreadsheetPager(view, meta));
  return wrap;
}
function buildFileViewer(model) {
  const view = model.fileView;
  const file = view.file || {};
  const fragment = document.createDocumentFragment();
  const note = (file.name || t("file.default_name")) + " · " + (file.mime_type || t("file.default_name")) + " · " + formatBytes(file.size);
  const download = element("a", "mini-button file-viewer-download", t("viewer.download"));
  download.href = downloadFileUrl(state.selected, file.id);
  download.setAttribute("download", file.name || "file");
  const reveal = element("button", "mini-button file-viewer-reveal", t("viewer.reveal"));
  reveal.type = "button";
  reveal.title = t("viewer.reveal_title");
  reveal.addEventListener("click", () => { revealFile(view); });
  const backLabel = view.origin === "changes" ? t("panel.back_to_diff") : t("panel.back_to_files");
  const actions = [
    panelBackButton(backLabel, () => { if (model.workspace.active?.kind === "file") { const id = model.workspace.active.id; closeWorkspaceTab(id); switchPanelTab(view.origin); } else { closeFileViewerSession(); model.fileView = null; renderPanel(); } }),
  ];
  if (view.kind === "html" && !view.truncated) {
    actions.push(
      viewerModeButton(t("viewer.preview_mode"), "preview", view),
      viewerModeButton(t("viewer.code_mode"), "code", view),
    );
  }
  actions.push(download, reveal, panelRefreshButton(() => { loadFileViewerContent().catch(() => {}); }));
  fragment.append(panelToolbar(note, actions));
  if (view.status === "loading" || view.status === "idle") {
    fragment.append(panelLoading());
    return fragment;
  }
  if (view.status === "error") {
    fragment.append(element("p", "side-error", view.error || t("viewer.error_fallback")));
    return fragment;
  }
  if (view.kind === "image" && view.dataUrl) {
    const figure = element("figure", "file-viewer-figure");
    const image = document.createElement("img");
    image.className = "file-viewer-image";
    image.src = view.dataUrl;
    image.alt = file.name || t("viewer.image_alt");
    figure.append(image);
    fragment.append(figure);
    return fragment;
  }
  if (view.kind === "spreadsheet") {
    fragment.append(buildSpreadsheetView(view));
    return fragment;
  }
  if (view.kind === "html") {
    if (view.mode !== "code") {
      fragment.append(buildMarkupPreview(file));
      return fragment;
    }
    fragment.append(buildCodeBlock(view.text || "", fileViewerLanguage(file), fileViewerGrammar(file)));
    appendViewerTruncation(fragment, view);
    return fragment;
  }
  if (view.kind === "markdown" && view.text) {
    const body = element("div", "file-viewer-markdown");
    if (window.IgorekMarkdown) window.IgorekMarkdown.renderMarkdown(view.text, body);
    else body.textContent = view.text;
    fragment.append(body);
    appendViewerTruncation(fragment, view);
    return fragment;
  }
  if (view.kind === "text" && view.text) {
    fragment.append(buildCodeBlock(view.text, fileViewerLanguage(file), fileViewerGrammar(file)));
    appendViewerTruncation(fragment, view);
    return fragment;
  }
  const box = element("div", "side-empty");
  box.append(
    element("strong", "", view.fallbackTitle || t("viewer.unavailable_title")),
    element("span", "", view.fallbackHint || t("viewer.fallback_hint")),
  );
  fragment.append(box);
  return fragment;
}
function buildFilesView() {
  const model = state.panel;
  const guard = sessionRequired();
  if (guard) return guard;
  if (model.fileView && model.fileView.origin === "files") return buildFileViewer(model);
  const fragment = document.createDocumentFragment();
  const files = (model.files.data && model.files.data.files) || [];
  fragment.append(panelToolbar(
    model.files.status === "ready" ? t("panel.files_count", { n: files.length }) : t("panel.caption.files"),
    [panelRefreshButton(() => loadFiles(state.selected))],
  ));
  if (model.files.status === "loading" || model.files.status === "idle") {
    fragment.append(panelLoading());
    return fragment;
  }
  if (model.files.status === "error") {
    fragment.append(element("p", "side-error", model.files.error));
    return fragment;
  }
  if (!files.length) {
    fragment.append(panelEmpty(t("panel.files_none"), t("panel.files_none_hint")));
    return fragment;
  }
  if(model.files.data?.partial)fragment.append(element('p','side-error',lang==='en'?'Some file changes could not be indexed.':'Часть файловых изменений недоступна.'));
  for (const file of files) {
    const row = element("div", "file-row");
    const open = element("button", "file-open");
    open.type = "button";
    open.title = t("panel.file_open");
    const copy = element("span", "file-copy");
    copy.append(element("span", "file-name", file.path || file.name || t("file.default_name")));
    copy.append(element("span", "file-meta", (file.mime_type || t("file.default_name")) + " · " + formatBytes(file.size)));
    open.append(copy);
    open.addEventListener("click", () => openFileViewer(file, "files"));
    row.append(open);
    const actions = element("span", "file-actions");
    const view = element("button", "file-action-button", t("panel.file_view"));
    view.type = "button";
    view.title = t("panel.file_open");
    view.addEventListener("click", () => openFileViewer(file, "files"));
    const download = element("a", "file-action-button", t("panel.file_download"));
    download.href = downloadFileUrl(state.selected, file.id);
    download.setAttribute("download", file.name || "file");
    download.rel = "noopener";
    download.title = t("panel.download_file");
    actions.append(view, download);
    row.append(actions);
    fragment.append(row);
  }
  return fragment;
}
function checkpointReason(reason) {
  const text = String(reason || "").replace(/^checkpoint:\s*/, "");
  return text || t("checkpoints.snapshot");
}
function checkpointTime(value) {
  const date = new Date(String(value || ""));
  if (Number.isNaN(date.getTime())) return String(value || "");
  return new Intl.DateTimeFormat(intlLocale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
}
function buildCheckpointsView() {
  const model = state.panel;
  const guard = sessionRequired();
  if (guard) return guard;
  const cp = model.cp;
  if (cp.hash && cp.diff) {
    const fragment = document.createDocumentFragment();
    fragment.append(panelToolbar(t("checkpoints.version", { short: cp.short }), [
      panelBackButton(t("panel.back_to_files"), () => { cp.diff = null; renderPanel(); }),
      panelRefreshButton(() => openCheckpointDiff(state.selected, cp.hash, cp.diff.path)),
    ]));
    fragment.append(buildDiff(cp.diff));
    return fragment;
  }
  if (cp.hash) {
    const fragment = document.createDocumentFragment();
    fragment.append(panelToolbar(t("checkpoints.files_of_version", { short: cp.short }), [
      panelBackButton(t("panel.back"), () => { model.cp = { hash: "", short: "", status: "idle", changes: null, error: "", diff: null }; renderPanel(); }),
    ]));
    if (cp.status === "loading") {
      fragment.append(panelLoading());
      return fragment;
    }
    if (cp.status === "error") {
      fragment.append(element("p", "side-error", cp.error));
      return fragment;
    }
    const files = (cp.changes && cp.changes.files) || [];
    if (!files.length) {
      fragment.append(panelEmpty(t("checkpoints.no_changes"), t("checkpoints.no_changes_hint")));
      return fragment;
    }
    const list = element("div", "side-list");
    for (const file of files) {
      list.append(buildChangeRow(file, () => openCheckpointDiff(state.selected, cp.hash, file.path)));
    }
    fragment.append(list);
    return fragment;
  }
  const fragment = document.createDocumentFragment();
  const data = model.checkpoints.data;
  const list = (data && data.checkpoints) || [];
  fragment.append(panelToolbar(
    model.checkpoints.status === "ready" ? t("checkpoints.count", { n: list.length }) : t("checkpoints.history"),
    [panelRefreshButton(() => loadCheckpoints(state.selected, false))],
  ));
  if (model.checkpoints.status === "loading" || model.checkpoints.status === "idle") {
    fragment.append(panelLoading());
    return fragment;
  }
  if (model.checkpoints.status === "error") {
    fragment.append(element("p", "side-error", model.checkpoints.error));
    return fragment;
  }
  if (data && data.available === false) {
    fragment.append(panelEmpty(t("checkpoints.unavailable"), t("checkpoints.unavailable_hint")));
    return fragment;
  }
  if (!list.length) {
    fragment.append(panelEmpty(t("checkpoints.none"), t("checkpoints.none_hint")));
    return fragment;
  }
  const rows = element("div", "side-list");
  for (const checkpoint of list) {
    const row = element("button", "cp-row");
    row.type = "button";
    row.title = t("checkpoints.open");
    const copy = element("span", "cp-copy");
    copy.append(element("span", "change-path", checkpointReason(checkpoint.reason)));
    const meta = element("span", "cp-meta");
    meta.append(element("span", "cp-hash", checkpoint.short_hash || String(checkpoint.hash || "").slice(0, 8)));
    meta.append(element("span", "", checkpointTime(checkpoint.timestamp)));
    copy.append(meta);
    row.append(copy, element("span", "cp-open", "→"));
    row.addEventListener("click", () => openCheckpoint(state.selected, checkpoint));
    rows.append(row);
  }
  fragment.append(rows);
  if (data && data.has_more) {
    const more = element("button", "secondary-button side-more", model.checkpoints.loadingMore ? t("panel.loading") : t("checkpoints.more"));
    more.type = "button";
    more.disabled = Boolean(model.checkpoints.loadingMore);
    more.addEventListener("click", () => loadCheckpoints(state.selected, true));
    fragment.append(more);
  }
  return fragment;
}

async function pairBrowser(event) {
  event.preventDefault();
  ui.pairError.textContent = "";
  const button = ui.pairForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api("/api/pair", { method: "POST", body: JSON.stringify({ code: ui.pairCode.value }) });
    ui.pairCode.value = "";
    await initialize();
    toast(t("connect.paired"));
  } catch (error) {
    ui.pairError.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
async function logout() {
  try {
    await api("/api/logout", { method: "POST", body: JSON.stringify({}) });
    showConnection();
    toast(t("logout.toast"));
  } catch (error) {
    toast(error.message, "error");
  }
}
async function initialize() {
  const status = await api("/api/status");
  if (!status.paired) { showConnection(); return; }
  showWorkspace(status.device);
  state.uiNavigation = [];
  if (window.IgorekDesktop?.navigation) {
    try {
      const navigation = await window.IgorekDesktop.navigation('catalog');
      if (Array.isArray(navigation.routes)) state.uiNavigation = navigation.routes;
    } catch {}
  }
  state.accessReady = true;
  refreshComposerAccess();
  await loadSessions();
  if (state.selected && state.sessions.some((item) => item.session_id === state.selected)) {
    await selectSession(state.selected);
  } else if (state.sessions.length) {
    await selectSession(state.sessions[0].session_id);
  } else {
    ui.welcome.hidden = false;
    ui.conversation.hidden = true;
  }
}

let workspaceDisplayedTab = null;
let workspaceBrowserState = { tabs: [], history: [], downloads: [] };
let workspaceBrowserSequence = Promise.resolve();
let workspaceSurfaceObserver = null;
let workspaceDownloadsView=null;
let workspaceChatTimer = 0;
let workspaceChatGeneration = 0;
let workspaceTabDrag=null;
let workspaceTabSkipClick=false;
function workspaceBrowserCall(operation, fields = {}) {
  if (!window.IgorekDesktop?.workspaceBrowser) return Promise.resolve({ tabs: [], history: [], downloads: [] });
  const next = workspaceBrowserSequence.catch(() => {}).then(() => window.IgorekDesktop.workspaceBrowser(operation, fields));
  workspaceBrowserSequence = next;
  return next.then(value => { workspaceBrowserState = value;renderWorkspaceFavorites();updateWorkspaceFavoriteButton();return value; });
}
function workspaceGlyph(name) {
  const shapes = {
    access: [["circle",{"cx":"12","cy":"12","r":"10"}],["path",{"d":"M12 6v8"}],["circle",{"cx":"12","cy":"18","r":"1.2","fill":"currentColor","stroke":"none"}]],
    globe: [["circle",{"cx":"12","cy":"12","r":"9"}],["path",{"d":"M3 12h18M12 3c5 5 5 13 0 18M12 3c-5 5-5 13 0 18"}]],
    play: [["path",{"d":"m8 5 11 7-11 7Z"}]],
    stop: [["rect",{"x":"6","y":"6","width":"12","height":"12","rx":"1"}]],
    plus: [["path",{"d":"M12 4v16M4 12h16"}]],
    expand: [["path",{"d":"M8 4H4v4M16 20h4v-4"}]],
    collapse: [["path",{"d":"M4 8h4V4M20 16h-4v4"}]],
    split: [["rect",{"x":"3","y":"4","width":"18","height":"16","rx":"3"}],["path",{"d":"M15 4v16"}]],
    changes: [["rect",{"x":"4","y":"4","width":"16","height":"16","rx":"3"}],["path",{"d":"M8 12h8M12 8v8"}]],
    files: [["path",{"d":"M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"}]],
    plan: [["path",{"d":"M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"}]],
    terminal: [["rect",{"x":"3","y":"4","width":"18","height":"16","rx":"3"}],["path",{"d":"m7 9 3 3-3 3M13 15h4"}]],
    checkpoints: [["path",{"d":"M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"}]],
    subagents: [["circle",{"cx":"9","cy":"8","r":"3"}],["path",{"d":"M3 21v-3a6 6 0 0 1 12 0v3M17 5a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-3-5"}]],
    chat: [["path",{"d":"M21 11a8 8 0 0 1-8 8H7l-4 3v-6a8 8 0 0 1 7-13h3a8 8 0 0 1 8 8Z"}],["path",{"d":"M12 7v8M8 11h8"}]],
    plugins: [["path",{"d":"M8 3h4a3 3 0 1 0 6 0h3v7a3 3 0 1 0 0 6v5h-7a3 3 0 1 0-6 0H3v-7a3 3 0 1 0 0-6V3h5Z"}]],
    settings: [["circle",{"cx":"12","cy":"12","r":"3"}],["path",{"d":"M9 3 8 6 5 7 3 12l2 5 3 1 1 3h6l1-3 3-1 2-5-2-5-3-1-1-3Z"}]],
    search: [["circle",{"cx":"10","cy":"10","r":"7"}],["path",{"d":"m15 15 6 6"}]],
    chevron: [["path",{"d":"m7 10 5 5 5-5"}]],
    folder: [["path",{"d":"M3 7V5h6l2 2h10v13H3ZM3 10h18"}]],
    image: [["rect",{"x":"3","y":"3","width":"18","height":"18","rx":"3"}],["circle",{"cx":"8","cy":"8","r":"2"}],["path",{"d":"m3 17 5-5 5 5 3-3 5 5"}]],
    download: [["path",{"d":"M12 3v12m-5-5 5 5 5-5M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"}]],
    opencode: [["path",{"d":"M12 16H4V8H12V16Z","fill":"currentColor","stroke":"none","opacity":".45"}],["path",{"d":"M12 4H4V16H12V4ZM16 20H0V0H16V20Z","fill":"currentColor","stroke":"none"}]],
    back: [["path",{"d":"m14 5-7 7 7 7M7 12h14"}]],
    forward: [["path",{"d":"m10 5 7 7-7 7M3 12h14"}]],
    reload: [["path",{"d":"M20 7a9 9 0 1 0 1 8M20 2v6h-6"}]],
    close: [["path",{"d":"m6 6 12 12M18 6 6 18"}]],
    more: [["circle",{"cx":"5","cy":"12","r":"1"}],["circle",{"cx":"12","cy":"12","r":"1"}],["circle",{"cx":"19","cy":"12","r":"1"}]],
    send: [["path",{"d":"M12 19V5m-6 6 6-6 6 6"}]],
    file: [["path",{"d":"M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8ZM14 2v6h6M8 13h8M8 17h6"}]]
  };
  if (name === 'ChatGPT') { const logo = document.querySelector('#igorek-open-chatgpt svg'); if (logo) return logo.cloneNode(true); }
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute('viewBox', name === 'opencode' ? '0 0 16 20' : '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); for (const [tag, attributes] of shapes[name] || shapes.globe) { const child = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [key, value] of Object.entries(attributes)) child.setAttribute(key, value); svg.append(child); } return svg;
}
function workspaceTabGlyph(tab) { if(tab.kind==='downloads')return 'download';return tab.kind === 'tool' ? tab.key : tab.kind === 'start' || tab.kind === 'browser' ? window.IgorekWorkspaceTabs.isChatGPT(workspaceBrowserState.tabs.find(item => item.id === tab.id)?.url) ? 'ChatGPT' : 'globe' : tab.kind; }

function workspaceLabel(tab) {
  if (!tab) return '';
  if (tab.kind === 'opencode') return 'OpenCode';
  if (tab.kind === 'tool') return t('panel.tab.' + tab.key);
  if (tab.kind === 'browser') { const page = workspaceBrowserState.tabs.find(item => item.id === tab.id); return window.IgorekWorkspaceTabs.isChatGPT(page?.url) ? 'ChatGPT' : page?.title || tab.title || (lang === 'en' ? 'Website' : 'Сайт'); }
  if (tab.kind === 'downloads') return lang === 'en' ? 'Downloads' : 'История загрузок';
  if (tab.kind === 'start') return lang === 'en' ? 'New tab' : 'Новая вкладка';
  if (tab.kind === 'plugins') return lang === 'en' ? 'Additional tools' : 'Дополнительные инструменты';
  if (tab.kind === 'chat') return tab.title || (lang === 'en' ? 'Side chat' : 'Боковой чат');
  return tab.title || 'Файл';
}
function workspaceIcon(tab) {
  return ({ start: '＋', browser: '◎', chat: '◯', plugins: '◇', file: '▧' })[tab.kind] || ({ plan: '☷', changes: '⊞', files: '▱', checkpoints: '◷', subagents: '♧', terminal: '›_' })[tab.key] || '□';
}
function activateWorkspaceTab(tab) {
  if (!tab) return;
  if (workspaceDisplayedTab && workspaceDisplayedTab !== tab) {
    workspaceDisplayedTab.fileView = state.panel.fileView;
    if (state.panel.fileView?.status === 'loading') state.panel.fileView.status = 'idle';
    suspendFileViewer();
    window.IgorekPlugins?.unmount?.();
    clearTimeout(workspaceChatTimer); workspaceChatGeneration++;
  }
  state.panel.workspace.select(tab.id);
  workspaceDisplayedTab = tab;
  state.panel.fileView = tab.fileView || null;
  state.panel.tab = tab.kind === 'tool' ? tab.key : tab.kind === 'file' ? tab.origin || 'files' : 'workspace';
  workspaceBrowserCall('select', { id: tab.kind === 'browser' && workspaceBrowserState.tabs.some(item => item.id === tab.id) ? tab.id : null }).catch(() => {});
  renderPanel();
  if (state.panel.fileView?.status === 'idle') loadFileViewerContent().catch(() => {});
}
function newWorkspaceTab() { try { activateWorkspaceTab(state.panel.workspace.open('start')); openPanel(); } catch (error) { toast(error.message, 'error'); } }
function closeWorkspaceTab(id) {
  const tab = state.panel.workspace.tabs.find(item => item.id === id);
  if (!tab) return;
  if (tab.kind === 'browser') workspaceBrowserCall('close', { id }).catch(() => {});
  if (tab.kind === 'chat') releaseWorkspaceChatAttachments(tab);
  if (tab.fileView?.book) { try { tab.fileView.book.dispose(); } catch {} }
  if (tab.kind === 'tool' && tab.key === 'terminal') unmountTerminal();
  const active = state.panel.workspace.close(id);
  activateWorkspaceTab(active);
}
function renderWorkspaceTabs() {
  const strip = byId('workspace-tabs');
  const model = state.panel.workspace;
  if(workspaceTabDrag)return;
  const chatgpt = byId('igorek-open-chatgpt');
  const activeChatGPT = state.panel.open && model.active?.kind === 'browser' && window.IgorekWorkspaceTabs.isChatGPT(workspaceBrowserState.tabs.find(item => item.id === model.activeId)?.url);
  if (chatgpt) { chatgpt.classList.toggle('active', activeChatGPT); chatgpt.setAttribute('aria-pressed', String(activeChatGPT)); }
  const opencode=byId('igorek-open-opencode');if(opencode){const active=state.panel.open&&model.active?.kind==='opencode';opencode.classList.toggle('active',active);opencode.setAttribute('aria-pressed',String(active));}
  strip.classList.toggle('workspace-tabs-dense', model.tabs.length * 80 > strip.clientWidth);
  const signature = JSON.stringify(model.tabs.map(tab => [tab.id, workspaceLabel(tab), tab.id === model.activeId]));
  if (strip.dataset.signature === signature) return;
  strip.dataset.signature = signature; strip.replaceChildren();
  for (const tab of model.tabs) {
    const row = element('div', 'workspace-tab' + (tab.id === model.activeId ? ' active' : ''));
    row.dataset.tabId=tab.id;
    const select = element('button', 'workspace-tab-select'); select.type = 'button'; select.id = 'select-' + tab.id;
    select.setAttribute('role', 'tab'); select.setAttribute('aria-controls', tab.kind === 'tool' ? 'side-view-' + tab.key : tab.kind === 'file' ? 'side-view-' + (tab.origin || 'files') : 'workspace-special'); select.setAttribute('aria-selected', String(tab.id === model.activeId)); select.tabIndex = tab.id === model.activeId ? 0 : -1;
    select.title = workspaceLabel(tab); const icon = element('span', 'workspace-tab-icon'); icon.append(workspaceGlyph(workspaceTabGlyph(tab))); select.append(icon, element('span', '', workspaceLabel(tab)));
    select.addEventListener('click', () => activateWorkspaceTab(tab));
    select.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Delete'].includes(event.key)) return;
      event.preventDefault(); if (event.key === 'Delete') closeWorkspaceTab(tab.id);
      else { const index = model.tabs.indexOf(tab), next = event.key === 'Home' ? 0 : event.key === 'End' ? model.tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + model.tabs.length) % model.tabs.length; activateWorkspaceTab(model.tabs[next]); }
      byId('select-' + model.activeId)?.focus();
    });
    const close = element('button', 'workspace-tab-close'); close.append(workspaceGlyph('close')); close.type = 'button'; close.title = (lang === 'en' ? 'Close ' : 'Закрыть ') + workspaceLabel(tab); close.setAttribute('aria-label', close.title); close.addEventListener('click', () => closeWorkspaceTab(tab.id));
    row.append(select, close); strip.append(row);
  }
  byId('workspace-tab-new').replaceChildren(workspaceGlyph('plus'));
  ui.panelFullscreen.replaceChildren(workspaceGlyph(state.panel.fullscreen ? 'collapse' : 'expand'));
  ui.closePanel.replaceChildren(workspaceGlyph('split'));
}
function finishWorkspaceTabDrag(cancel=false){
  const drag=workspaceTabDrag;if(!drag)return;workspaceTabDrag=null;
  const model=state.panel.workspace,strip=byId('workspace-tabs');
  if(cancel&&drag.dragging){const order=new Map(drag.order.map((id,index)=>[id,index]));model.tabs.sort((a,b)=>(order.get(a.id)??Infinity)-(order.get(b.id)??Infinity));}
  workspaceTabSkipClick=drag.dragging;
  document.body.classList.remove('workspace-tabs-dragging');
  for(const row of strip.children){row.classList.remove('dragging');row.style.order='';}
  if(drag.button.hasPointerCapture(drag.pointerId))drag.button.releasePointerCapture(drag.pointerId);
  if(drag.dragging){strip.dataset.signature='';renderWorkspaceTabs();byId('select-'+drag.id)?.focus();}
  else renderWorkspaceTabs();
}
const workspaceTabStrip=byId('workspace-tabs');
workspaceTabStrip.addEventListener('pointerdown',event=>{
  workspaceTabSkipClick=false;
  const button=event.target.closest('.workspace-tab-select');
  if(event.button!==0||event.isPrimary===false||!button||workspaceTabDrag)return;
  const id=button.closest('.workspace-tab')?.dataset.tabId;
  if(!state.panel.workspace.tabs.some(tab=>tab.id===id))return;
  workspaceTabDrag={id,button,pointerId:event.pointerId,x:event.clientX,order:state.panel.workspace.tabs.map(tab=>tab.id),dragging:false};
  button.setPointerCapture(event.pointerId);
});
workspaceTabStrip.addEventListener('pointermove',event=>{
  const drag=workspaceTabDrag;if(!drag||event.pointerId!==drag.pointerId)return;
  if(!drag.dragging&&Math.abs(event.clientX-drag.x)<6)return;
  drag.dragging=true;event.preventDefault();document.body.classList.add('workspace-tabs-dragging');
  const row=drag.button.closest('.workspace-tab');row.classList.add('dragging');
  const bounds=workspaceTabStrip.getBoundingClientRect();if(event.clientY<bounds.top-12||event.clientY>bounds.bottom+12)return;
  const others=[...workspaceTabStrip.children].filter(node=>node.dataset.tabId!==drag.id);
  const index=others.filter(node=>{const r=node.getBoundingClientRect();return event.clientX>=r.left+r.width/2;}).length;
  const model=state.panel.workspace;if(!model.move(drag.id,index))return;
  for(const [position,tab] of model.tabs.entries()){const node=[...workspaceTabStrip.children].find(node=>node.dataset.tabId===tab.id);if(node)node.style.order=String(position);}
});
workspaceTabStrip.addEventListener('pointerup',event=>{if(workspaceTabDrag?.pointerId!==event.pointerId)return;const r=workspaceTabStrip.getBoundingClientRect();finishWorkspaceTabDrag(event.clientY<r.top-12||event.clientY>r.bottom+12);});
workspaceTabStrip.addEventListener('pointercancel',event=>{if(workspaceTabDrag?.pointerId===event.pointerId)finishWorkspaceTabDrag(true);});
workspaceTabStrip.addEventListener('lostpointercapture',event=>{if(workspaceTabDrag?.pointerId===event.pointerId&&!workspaceTabDrag.button.hasPointerCapture(event.pointerId))finishWorkspaceTabDrag(true);});
workspaceTabStrip.addEventListener('click',event=>{if(workspaceTabSkipClick&&event.detail>0){workspaceTabSkipClick=false;event.preventDefault();event.stopImmediatePropagation();}},true);
workspaceTabStrip.addEventListener('keydown',event=>{if(event.key==='Escape'&&workspaceTabDrag){event.preventDefault();finishWorkspaceTabDrag(true);}});
window.addEventListener('blur',()=>finishWorkspaceTabDrag(true));
function workspaceRecentKey() { return 'igorek.workspace.recent.' + String(state.device?.device_id || state.device?.id || 'local'); }
function workspaceRecent() { try { return window.IgorekWorkspaceTabs.recentList(JSON.parse(localStorage.getItem(workspaceRecentKey()) || '[]')); } catch { return []; } }
function rememberWorkspaceFile(file) {
  const entry = { session: state.selected, file: { id: file.id, name: file.name, path: file.path, mime_type: file.mime_type, size: file.size }, at: Date.now() };
  const list = [entry, ...workspaceRecent().filter(item => item.session !== entry.session || item.file.id !== file.id)].slice(0, 20);
  try { localStorage.setItem(workspaceRecentKey(), JSON.stringify(list)); } catch {}
}
async function reopenWorkspaceFile(item) {
  if (!state.sessions.some(session => session.session_id === item.session)) { toast(lang === 'en' ? 'The chat is unavailable.' : 'Чат этого файла недоступен.'); return; }
  if (state.selected !== item.session) await selectSession(item.session);
  if (state.selected !== item.session) return;
  await openFileViewer(item.file, 'files'); openPanel();
}
function workspaceButton(label, icon, run) {
  const button = element('button', 'workspace-tool'); button.type = 'button'; const glyph = element('span', 'workspace-tool-icon'); glyph.append(workspaceGlyph(icon)); button.append(glyph, element('span', '', label)); button.addEventListener('click', run); return button;
}
async function workspaceOpenSite(raw, tab = null) {
  if (!window.IgorekDesktop?.workspaceBrowser) { toast(lang === 'en' ? 'Web tabs require the desktop app.' : 'Веб-вкладки доступны в десктопном приложении.'); return; }
  let url;
  try { url = new URL(window.IgorekWorkspaceTabs.normalizeSite(raw)); }
  catch { toast('Введите HTTPS-адрес или поисковый запрос.', 'error'); return; }
  if(url.href==='chrome://webrtc-internals/'){try{await workspaceBrowserCall('webrtc');}catch(error){toast(error.message,'error');}return;}
  tab = tab || state.panel.workspace.active;
  if (!tab || !['start', 'browser'].includes(tab.kind)) tab = state.panel.workspace.open('browser');
  try {
    await workspaceBrowserCall('open', { id: tab.id, url: url.href });
    tab.kind = 'browser'; tab.title = url.hostname; activateWorkspaceTab(tab); openPanel();
  } catch (error) { toast(error.message, 'error'); }
}
window.IgorekOpenBrowserPanel=()=>{showMainView();const tab=state.panel.workspace.active;if(state.panel.open&&['start','browser'].includes(tab?.kind)){closePanel();return false;}if(!['start','browser'].includes(tab?.kind))activateWorkspaceTab(state.panel.workspace.open('start'));openPanel();return true;};
window.IgorekShowBrowserPanel=()=>{showMainView();if(!['start','browser'].includes(state.panel.workspace.active?.kind))activateWorkspaceTab(state.panel.workspace.open('start'));openPanel();return true;};
window.IgorekShowAgentBrowser=async(id,browserState)=>{
  if(typeof id!=='string'||!/^[-a-zA-Z0-9]{1,64}$/.test(id))return false;
  showMainView();
  if(browserState&&Array.isArray(browserState.tabs))workspaceBrowserState=browserState;
  const model=state.panel.workspace;let tab=model.tabs.find(item=>item.id===id);
  if(!tab){tab=model.open('browser');tab.id=id;model.activeId=id;}
  tab.kind='browser';activateWorkspaceTab(tab);openPanel();await workspaceBrowserSequence.catch(()=>{});return state.panel.open&&model.activeId===id;
};
function workspaceAddress(host, tab, browser) {
  const toolbar = element('div', 'workspace-address'), navigation = element('div', 'workspace-navigation'); toolbar.append(navigation);
  for (const [operation, label, icon] of [['back', 'Назад', '←'], ['forward', 'Вперёд', '→'], ['reload', 'Обновить', '↻']]) {
    const button = element('button', 'workspace-icon-button'); button.append(workspaceGlyph(operation)); button.type = 'button'; button.title = label; button.setAttribute('aria-label', label);
    button.disabled = !browser || operation === 'back' && !browser.can_back || operation === 'forward' && !browser.can_forward;
    button.addEventListener('click', () => workspaceBrowserCall(operation).catch(error => toast(error.message, 'error'))); navigation.append(button);
  }
  const form = element('form', 'workspace-url-form'), input = element('input'); input.id = 'workspace-url'; input.setAttribute('aria-label', lang === 'en' ? 'Search or enter URL' : 'Поиск или ввод URL'); input.placeholder = lang === 'en' ? 'Search or enter URL' : 'Поиск или ввод URL'; input.value = browser?.url ? new URL(browser.url).hostname : ''; input.dataset.url = browser?.url || ''; input.autocomplete = 'off'; input.spellcheck = false;
  input.addEventListener('focus', () => { input.value = input.dataset.url; input.select(); }); input.addEventListener('blur', () => { if (input.dataset.url) { try { input.value = new URL(input.dataset.url).hostname; } catch {} } }); form.append(input); form.addEventListener('submit', event => { event.preventDefault(); if (input.value.trim()) workspaceOpenSite(input.value.trim(), tab); }); toolbar.append(form);
  const utilities = element('div', 'workspace-utilities');
  const control=element('button','workspace-agent-control');control.type='button';control.hidden=!workspaceBrowserState.require_handoff;control.textContent=workspaceBrowserState.manual?'◉ Вы':'◇ Агент';control.title=workspaceBrowserState.manual?'Передать управление агенту':'Забрать управление себе';control.setAttribute('aria-label',control.title);control.addEventListener('click',()=>workspaceBrowserCall('control',{manual:!workspaceBrowserState.manual}).catch(error=>toast(error.message,'error')));utilities.append(control);
  const favorite=element('button','workspace-icon-button workspace-favorite-current','☆');favorite.type='button';favorite.hidden=!browser?.url;favorite.title='Добавить в избранное';favorite.setAttribute('aria-label',favorite.title);favorite.addEventListener('click',()=>workspaceFavoriteDialog(workspaceBrowserState.tabs.find(item=>item.id===tab.id)||browser));utilities.append(favorite);
  const chats = element('button', 'workspace-icon-button workspace-chat-home'); chats.type='button'; chats.title='Чаты ChatGPT'; chats.setAttribute('aria-label',chats.title); chats.append(workspaceGlyph('chat')); chats.hidden=!window.IgorekWorkspaceTabs.isChatGPT(browser?.url); chats.addEventListener('click',()=>workspaceOpenSite(window.IgorekWorkspaceTabs.sites.chatgpt,tab)); utilities.append(chats);
  for (const [name, label, action] of [['plugins', 'Дополнительные инструменты', () => activateWorkspaceTab(state.panel.workspace.open('plugins'))], ['download', 'Загрузки', () => workspaceBrowserList('downloads', 'Загрузки')]]) { const button = element('button', 'workspace-icon-button workspace-' + name); button.type = 'button'; button.title = label; button.setAttribute('aria-label', label); button.append(workspaceGlyph(name)); button.addEventListener('click', action); utilities.append(button); } toolbar.append(utilities);
  const menu = element('button', 'workspace-icon-button workspace-more'); menu.append(workspaceGlyph('more')); menu.type = 'button'; menu.title = lang === 'en' ? 'Browser actions' : 'Действия браузера'; menu.setAttribute('aria-label', menu.title); menu.addEventListener('click', () => workspaceBrowserMenu(menu, workspaceBrowserState.tabs.find(item=>item.id===tab.id)||browser)); toolbar.append(menu); host.append(toolbar);
}
function updateWorkspaceFavoriteButton() {
  const button=byId('workspace-special').querySelector('.workspace-favorite-current');if(!button)return;
  const browser=workspaceBrowserState.tabs.find(item=>item.id===state.panel.workspace.active?.id);
  button.hidden=!browser?.url;const saved=(workspaceBrowserState.favorites||[]).some(item=>item.url===browser?.url);
  button.textContent=saved?'★':'☆';button.setAttribute('aria-pressed',String(saved));
  button.title=lang==='en'?(saved?'Edit favorite':'Add to favorites'):(saved?'Изменить избранное':'Добавить в избранное');button.setAttribute('aria-label',button.title);
}
function renderWorkspaceFavorites() {
  const host=byId('workspace-favorites');if(!host)return;
  const rows=workspaceBrowserState.favorites||[],signature=JSON.stringify([rows,workspaceBrowserState.favorites_error,lang]);if(host.dataset.signature===signature)return;host.dataset.signature=signature;host.replaceChildren();
  const heading=element('div','workspace-recents-head');heading.append(element('h2','',lang==='en'?'Favorites':'Избранное'));
  const add=element('button','workspace-icon-button workspace-favorite-add','+');add.type='button';add.title=lang==='en'?'Add website':'Добавить сайт';add.setAttribute('aria-label',add.title);add.disabled=!window.IgorekDesktop?.workspaceBrowser;add.addEventListener('click',()=>workspaceFavoriteDialog());heading.append(add);host.append(heading);
  const list=element('div','workspace-recommended');list.append(workspaceButton('ChatGPT','ChatGPT',()=>workspaceOpenSite(window.IgorekWorkspaceTabs.sites.chatgpt)),workspaceButton('GitHub','globe',()=>workspaceOpenSite(window.IgorekWorkspaceTabs.sites.github)));
  for(const item of rows){
    const chip=element('div','workspace-favorite');chip.dataset.favoriteId=item.id;
    const open=workspaceButton(item.title,'globe',()=>workspaceOpenSite(item.url));open.classList.add('workspace-favorite-open');open.title=item.url;
    const remove=element('button','workspace-icon-button workspace-favorite-remove','×');remove.type='button';remove.title=(lang==='en'?'Remove favorite: ':'Удалить из избранного: ')+item.title;remove.setAttribute('aria-label',remove.title);
    remove.addEventListener('click',async()=>{remove.disabled=true;try{await workspaceBrowserCall('removeFavorite',{id:item.id});renderWorkspaceFavorites();}catch(error){toast(error.message,'error');remove.disabled=false;}});
    chip.append(open,remove);list.append(chip);
  }
  host.append(list);if(workspaceBrowserState.favorites_error)host.append(element('p','side-note',lang==='en'?'Could not read favorites.':'Не удалось прочитать избранное.'));
}
function workspaceFavoriteDialog(browser) {
  const saved=(workspaceBrowserState.favorites||[]).find(item=>item.url===browser?.url);
  const dialog=element('dialog','workspace-list-dialog workspace-favorite-dialog'),form=element('form');
  dialog.append(element('h2','',lang==='en'?'Save website':'Сохранить сайт'));
  const urlLabel=element('label','',lang==='en'?'Address':'Адрес'),url=element('input');url.name='url';url.type='text';url.inputMode='url';url.required=true;url.maxLength=2048;url.placeholder='example.com';url.value=browser?.url||'';urlLabel.append(url);
  const titleLabel=element('label','',lang==='en'?'Name (optional)':'Название (необязательно)'),title=element('input');title.name='title';title.maxLength=64;title.value=saved?.title||String(browser?.title||'').slice(0,64);titleLabel.append(title);
  const notice=element('p','side-note');notice.setAttribute('role','status');const actions=element('div','workspace-favorite-actions'),cancel=element('button','',lang==='en'?'Cancel':'Отмена'),save=element('button','',lang==='en'?'Save':'Сохранить');cancel.type='button';save.type='submit';cancel.addEventListener('click',()=>dialog.close());actions.append(cancel,save);form.append(urlLabel,titleLabel,notice,actions);dialog.append(form);
  form.addEventListener('submit',async event=>{event.preventDefault();save.disabled=true;notice.textContent='';try{await workspaceBrowserCall('addFavorite',{url:url.value,title:title.value});renderWorkspaceFavorites();updateWorkspaceFavoriteButton();dialog.close();}catch(error){notice.textContent=error.message;save.disabled=false;}});
  document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();url.focus();
}
function workspaceBrowserMenu(anchor, browser) {
  const menu = element('div', 'workspace-browser-menu'); menu.popover = 'auto';
  const run = (label, action) => { const button = element('button', '', label); button.type = 'button'; button.addEventListener('click', () => { menu.hidePopover(); Promise.resolve(action()).catch(error => toast(error.message, 'error')); }); menu.append(button); };
  if (browser) {
    run('Поиск на странице', () => { const form = element('form', 'workspace-find'), input = element('input'), find = element('button', '', 'Найти'), close = element('button', '', '×'); input.placeholder = 'Поиск на странице'; input.setAttribute('aria-label', input.placeholder); find.type = 'submit'; close.type = 'button'; close.setAttribute('aria-label', 'Закрыть поиск'); form.append(input, find, close); byId('workspace-special').prepend(form); form.addEventListener('submit', event => { event.preventDefault(); workspaceBrowserCall('find', { text: input.value }).catch(() => {}); }); close.addEventListener('click', () => { form.remove(); workspaceBrowserCall('find', { text: '' }).catch(() => {}); }); input.focus(); });
    run('Печать', () => workspaceBrowserCall('print'));
    if (window.IgorekWorkspaceTabs.isChatGPT(browser.url)) { run('Чаты ChatGPT',()=>workspaceOpenSite(window.IgorekWorkspaceTabs.sites.chatgpt));run('Изображения ChatGPT',()=>workspaceOpenSite(window.IgorekWorkspaceTabs.sites.images));run('Остановить голос и обновить страницу', () => workspaceBrowserCall('stopVoice'));run('Сбросить разрешение микрофона',()=>workspaceBrowserCall('resetMicrophone')); }
    const zoom = element('div', 'workspace-zoom'); zoom.append(element('span', '', 'Масштаб'));
    const minus=element('button','','−'),reset=element('button','','100%'),plus=element('button','','+');
    minus.type=reset.type=plus.type='button';minus.setAttribute('aria-label','Уменьшить масштаб');reset.setAttribute('aria-label','Сбросить масштаб');plus.setAttribute('aria-label','Увеличить масштаб');
    const current=()=>workspaceBrowserState.tabs.find(item=>item.id===browser.id)||browser;
    const sync=()=>{const value=current().zoom||1;reset.textContent=Math.round(value*100)+'%';minus.disabled=value<=.5;plus.disabled=value>=2;};
    const change=async delta=>{minus.disabled=reset.disabled=plus.disabled=true;try{const value=delta===0?1:Math.max(.5,Math.min(2,Math.round(((current().zoom||1)+delta)*10)/10));await workspaceBrowserCall('zoom',{value,id:browser.id});}catch(error){toast(error.message,'error');}finally{reset.disabled=false;sync();}};
    minus.addEventListener('click',()=>change(-.1));reset.addEventListener('click',()=>change(0));plus.addEventListener('click',()=>change(.1));zoom.append(minus,reset,plus);sync();menu.append(zoom);
  }
  for (const [kind, title] of [['downloads', 'Загрузки'], ['history', 'История']]) run(title, () => workspaceBrowserList(kind, title));
  run('Очистить данные просмотра', () => workspaceBrowserCall('clear').then(() => { for (const tab of state.panel.workspace.tabs) if (tab.kind === 'browser') tab.kind = 'start'; activateWorkspaceTab(state.panel.workspace.active); }));
  run('Дополнительные инструменты', () => { activateWorkspaceTab(state.panel.workspace.open('plugins')); });
  document.body.append(menu); menu.addEventListener('toggle', event => { if (event.newState === 'closed') menu.remove(); });
  menu.showPopover(); const rect = anchor.getBoundingClientRect(); menu.style.top = Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8) + 'px'; menu.style.left = Math.max(8, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8)) + 'px';
}
function workspaceBrowserList(kind, title) {
  if(kind==='downloads'){try{activateWorkspaceTab(state.panel.workspace.open('downloads'));openPanel();}catch(error){toast(error.message,'error');}return;}
  const dialog = element('dialog', 'workspace-list-dialog'); dialog.append(element('h2', '', title));
  for (const item of workspaceBrowserState[kind] || []) {
    const button = element('button', 'workspace-recent', kind === 'history' ? item.title || item.url : item.name + ' · ' + (item.state === 'completed' ? 'Готово' : item.state === 'interrupted' ? 'Прервано' : item.state === 'cancelled' ? 'Отменено' : 'Загружается'));
    button.type = 'button'; if (kind === 'downloads') button.disabled = item.state !== 'completed';
    button.addEventListener('click', () => { dialog.close(); if (kind === 'history') workspaceOpenSite(item.url); else workspaceBrowserCall('openDownload', { id: item.id }).catch(error => toast(error.message, 'error')); }); dialog.append(button);
  }
  if (!(workspaceBrowserState[kind] || []).length) dialog.append(element('p', 'side-note', 'Пока пусто'));
  const close = element('button', 'mini-button', 'Закрыть'); close.type = 'button'; close.addEventListener('click', () => dialog.close()); dialog.append(close); document.body.append(dialog); dialog.addEventListener('close', () => dialog.remove()); dialog.showModal();
}
function renderWorkspaceSpecial() {
  const host = byId('workspace-special'), tab = state.panel.workspace.active;
  host.setAttribute('role', 'tabpanel'); if (tab) host.setAttribute('aria-labelledby', 'select-' + tab.id);
  if (!tab) return;
  if(host.dataset.tab===tab.id&&tab.kind==='opencode'){window.IgorekOpenCode.resume();return;}
  if(host.dataset.tab===tab.id&&tab.kind==='downloads'){workspaceDownloadsView?.update(workspaceBrowserState);return;}
  if (host.dataset.tab === tab.id && host.dataset.kind === tab.kind && (tab.kind === 'chat' || tab.kind === 'plugins' || tab.kind === 'browser')) { updateWorkspaceBrowser(); return; }
  workspaceSurfaceObserver?.disconnect(); window.IgorekPlugins?.unmount?.(); host.dataset.tab = tab.id; host.dataset.kind = tab.kind; host.replaceChildren();workspaceDownloadsView=null;
  if(tab.kind==='opencode'){window.IgorekOpenCode.mount(host,()=>state.panel.open&&state.panel.workspace.active===tab);return;}
  if(tab.kind==='downloads'){workspaceDownloadsView=window.IgorekDownloads.create({host,model:tab,call:workspaceBrowserCall,glyph:workspaceGlyph});return;}
  if (tab.kind === 'plugins') { window.IgorekPlugins?.mount?.(host); return; }
  if (tab.kind === 'chat') { buildWorkspaceChat(host, tab); return; }
  const browser = workspaceBrowserState.tabs.find(item => item.id === tab.id);
  workspaceAddress(host, tab, browser);
  if (tab.kind === 'browser') {
    const surface = element('div', 'workspace-browser-surface'); surface.id = 'workspace-browser-surface'; host.append(surface);
    workspaceSurfaceObserver = new ResizeObserver(() => workspaceBrowserCall('layout').catch(() => {})); workspaceSurfaceObserver.observe(surface);
    workspaceBrowserCall('layout').catch(() => {}); return;
  }
  const home = element('div', 'workspace-home'); home.append(element('h2', '', lang === 'en' ? 'Tools' : 'Инструменты'));
  const grid = element('div', 'workspace-tools');
  for (const key of ['changes', 'terminal', 'files', 'plan', 'checkpoints', 'subagents']) grid.append(workspaceButton(t('panel.tab.' + key), key, () => switchPanelTab(key)));
  if(window.IgorekDesktop?.github)grid.append(workspaceButton('Проверка кода', 'changes', () => window.IgorekReviews.open()));
  if(window.IgorekDesktop?.workspaceBrowser)grid.append(workspaceButton('История загрузок','download',()=>workspaceBrowserList('downloads','История загрузок')));
  grid.append(workspaceButton(lang === 'en' ? 'Side chat' : 'Боковой чат', 'chat', () => activateWorkspaceTab(state.panel.workspace.open('chat'))));
  grid.append(workspaceButton(lang === 'en' ? 'Additional tools' : 'Доп. инструменты', 'plugins', () => activateWorkspaceTab(state.panel.workspace.open('plugins')))); home.append(grid);
  const favorites=element('section','workspace-favorites');favorites.id='workspace-favorites';home.append(favorites);
  const heading = element('div', 'workspace-recents-head'); heading.append(element('h2', '', lang === 'en' ? 'Recent files' : 'Недавние файлы'));
  const clear = element('button', 'workspace-icon-button', '×'); clear.type = 'button'; clear.title = 'Очистить список недавних'; clear.setAttribute('aria-label', clear.title); clear.addEventListener('click', () => { localStorage.removeItem(workspaceRecentKey()); host.dataset.tab = ''; renderPanel(); }); heading.append(clear); home.append(heading);
  const recent = workspaceRecent(); for (const item of recent) { const button = workspaceButton(item.file.name, 'file', () => reopenWorkspaceFile(item)); button.classList.add('workspace-recent'); button.title = item.file.path || item.file.name; button.append(element('time', '', new Date(item.at).toLocaleDateString(lang === 'en' ? 'en' : 'ru'))); home.append(button); }
  if (!recent.length) home.append(element('p', 'side-note', lang === 'en' ? 'Files you open here appear in this list.' : 'Здесь появятся файлы, которые ты открывал в панели.'));
  host.append(home);renderWorkspaceFavorites();
}
function updateWorkspaceBrowser() {
  const tab = state.panel.workspace.active, browser = workspaceBrowserState.tabs.find(item => item.id === tab?.id);
  if (tab?.kind !== 'browser' || !browser) return;
  const input = byId('workspace-url'); if (input) { input.dataset.url = browser.url; if (document.activeElement !== input) { try { input.value = new URL(browser.url).hostname; } catch { input.value = ''; } } }
  updateWorkspaceFavoriteButton();
  const control=byId('workspace-special').querySelector('.workspace-agent-control');if(control){control.hidden=!workspaceBrowserState.require_handoff;control.textContent=workspaceBrowserState.manual?'◉ Вы':'◇ Агент';control.title=workspaceBrowserState.manual?'Передать управление агенту':'Забрать управление себе';control.setAttribute('aria-label',control.title);}
  const chats = byId('workspace-special').querySelector('.workspace-chat-home'); if (chats) chats.hidden=!window.IgorekWorkspaceTabs.isChatGPT(browser.url);
  const buttons = byId('workspace-special').querySelectorAll('.workspace-navigation > button'); if (buttons[0]) buttons[0].disabled = !browser.can_back; if (buttons[1]) buttons[1].disabled = !browser.can_forward;
  workspaceBrowserCall('layout').catch(() => {});
}
function releaseWorkspaceChatAttachments(tab) {
  for(const item of tab.attachments || [])if(item.previewUrl)URL.revokeObjectURL(item.previewUrl);
  tab.attachments=[];
}
function buildWorkspaceChat(host, tab) {
  const head=element('div','workspace-chat-head'),picker=element('select','workspace-chat-picker');
  picker.setAttribute('aria-label','Чат в боковой вкладке');
  const empty=element('option','','Выбери чат');empty.value='';picker.append(empty);
  for(const session of state.sessions){const option=element('option','',session.title||session.session_id);option.value=session.session_id;picker.append(option);}
  if(tab.session&&!state.sessions.some(s=>s.session_id===tab.session)){const option=element('option','',tab.title||tab.session);option.value=tab.session;picker.append(option);}
  picker.value=tab.session||'';picker.disabled=Boolean(tab.reviewPR);head.append(picker);
  const messages=element('div','workspace-chat-messages conversation-scroll'),inner=element('div','conversation-inner'),context=element('div','session-context workspace-chat-profile'),timeline=element('div','timeline'),indicator=ui.indicator.cloneNode(true);
  for(const node of [indicator,...indicator.querySelectorAll('[id]')])node.removeAttribute('id');
  timeline.setAttribute('aria-live','polite');indicator.hidden=true;inner.append(context,timeline,indicator);messages.append(inner);
  const wrap=element('div','composer-wrap workspace-chat-composer-wrap'),form=element('form','composer workspace-chat-compose'),attachments=element('div','composer-attachments'),row=element('div','composer-row'),input=element('textarea'),note=element('p','composer-mode-note'),bottom=element('div','composer-bottom'),left=element('div','composer-left'),actions=element('div','composer-actions'),status=element('p','workspace-chat-notice');
  input.rows=1;input.placeholder=t('composer.placeholder');input.setAttribute('aria-label',t('composer.input_aria'));input.value=tab.draft||'';row.append(input);
  const attach=element('button','attach-button','+'),fileInput=element('input');attach.type='button';attach.title=t('composer.attach_aria');attach.setAttribute('aria-label',attach.title);fileInput.type='file';fileInput.multiple=true;fileInput.hidden=true;
  const modes=element('div','mode-toggle');modes.setAttribute('role','group');modes.setAttribute('aria-label',t('composer.mode_aria'));
  const plan=element('button','mode-button',t('composer.mode_plan')),execute=element('button','mode-button',t('composer.mode_execute'));plan.type=execute.type='button';modes.append(plan,execute);left.append(attach,fileInput,modes,createComposerAccessButton());
  const meta=element('div','composer-meta'),chip=element('span','composer-model'),send=element('button','send-button workspace-chat-send'),stop=element('button','stop-button');meta.append(chip);
  send.type='submit';send.title=t('composer.send_aria');send.setAttribute('aria-label',send.title);send.append(element('span','',t('composer.send')),element('b','','↑'));
  stop.type='button';stop.append(element('span','',t('composer.stop')),element('b','','■'));stop.hidden=true;
  actions.append(meta,stop,send);bottom.append(left,actions);form.append(attachments,row,note,bottom);status.setAttribute('role','status');wrap.append(status,form,element('div','composer-caption',t('composer.caption')));host.append(head,messages,wrap);
  tab.attachments=tab.attachments||[];
  let loading=false,snapshot=null,sending=false,sessionGeneration=0,pending=false;
  const nodes=new Map(),generation=++workspaceChatGeneration;
  const valid=()=>workspaceChatGeneration===generation&&state.panel.open&&state.panel.workspace.active===tab;
  const resize=()=>{input.style.height='auto';input.style.height=Math.min(input.scrollHeight,170)+'px';};
  const sync=()=>{
    send.disabled=sending||!tab.session||!snapshot?.meta?.profile||Boolean(snapshot?.meta?.agent_session)||(!input.value.trim()&&!tab.attachments.length);
    picker.disabled=sending||Boolean(tab.reviewPR);attach.disabled=sending;fileInput.disabled=sending;
    plan.disabled=execute.disabled=sending;stop.hidden=!snapshot?.running_task_id;form.hidden=Boolean(snapshot?.meta?.agent_session);
    form.classList.toggle('plan-mode',Boolean(tab.planMode));plan.classList.toggle('active',Boolean(tab.planMode));execute.classList.toggle('active',!tab.planMode);plan.setAttribute('aria-pressed',String(Boolean(tab.planMode)));execute.setAttribute('aria-pressed',String(!tab.planMode));note.hidden=!tab.planMode;note.textContent=t('composer.mode_plan_note');
  };
  const renderFiles=()=>{
    attachments.replaceChildren();attachments.hidden=!tab.attachments.length;
    for(const item of tab.attachments){
      const card=element('span','attachment-chip '+(item.previewUrl?'attachment-image-card ':'' )+item.status);card.title=item.name;
      if(item.previewUrl){const preview=element('button','attachment-thumbnail'),image=element('img');preview.type='button';preview.setAttribute('aria-label','Посмотреть '+item.name);image.src=item.previewUrl;image.alt=item.name;preview.append(image);preview.addEventListener('click',()=>showAttachmentPreview(item));card.append(preview);}else card.append(element('span','attachment-name',item.name));
      card.append(element('span','attachment-size',item.status==='uploading'?t('attachments.loading'):formatBytes(item.size)));
      const remove=element('button','attachment-remove','×');remove.type='button';remove.disabled=sending;remove.setAttribute('aria-label',t('attachments.remove_aria',{name:item.name}));remove.addEventListener('click',()=>{if(item.previewUrl)URL.revokeObjectURL(item.previewUrl);tab.attachments=tab.attachments.filter(f=>f!==item);renderFiles();});card.append(remove);attachments.append(card);
    }sync();
  };
  const addFiles=files=>{
    if(sending)return;
    for(const file of files){if(!file?.size)continue;if(tab.attachments.length>=ATTACHMENT_LIMIT){toast(t('attachments.limit',{n:ATTACHMENT_LIMIT}));break;}
      if(tab.attachments.some(f=>f.name===file.name&&f.size===file.size&&f.file.lastModified===file.lastModified))continue;
      tab.attachments.push({file,name:file.name,size:file.size,status:'pending',uploadId:'',previewUrl:['image/png','image/jpeg','image/webp','image/gif','image/bmp','image/avif'].includes(file.type)?URL.createObjectURL(file):''});
    }renderFiles();
  };
  attach.addEventListener('click',()=>fileInput.click());fileInput.addEventListener('change',()=>{addFiles(fileInput.files||[]);fileInput.value='';});input.addEventListener('paste',event=>{const files=clipboardPasteFiles(event);if(files.length){event.preventDefault();addFiles(files);}});
  input.addEventListener('input',()=>{tab.draft=input.value;resize();sync();});input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!send.disabled)form.requestSubmit();}});
  plan.addEventListener('click',()=>{tab.planMode=true;sync();});execute.addEventListener('click',()=>{tab.planMode=false;sync();});
  const draw=data=>{
    const pinned=messages.scrollHeight-messages.scrollTop-messages.clientHeight<60;
    context.replaceChildren(element('span','context-dot'),element('span','',data.meta?.profile||t('session.profile_fallback')),element('span','context-separator','·'),element('span','',data.meta?.cwd||''));
    chip.replaceChildren(element('span','composer-model-dot'),element('span','composer-model-profile',data.meta?.profile||t('session.profile_fallback')));
    if(data.model||data.meta?.model)chip.append(element('span','composer-model-separator','·'),element('span','composer-model-name',data.model||data.meta.model));chip.title=[data.meta?.profile,data.model||data.meta?.model].filter(Boolean).join(' · ');
    const taskRows=new Map((data.tasks||[]).map(task=>[task.task_id,task])),items=activityItems(sessionMessageItems(data.messages||[])),last=new Map();items.forEach((item,index)=>{if(item.task_id)last.set(item.task_id,index);});
    const ordered=[],retained=new Set();
    for(const [index,item] of items.entries()){
      if(!['user','assistant','tool','tool_group'].includes(item.type))continue;
      const cached=nodes.get(item.id);if(cached&&item.type==='tool')item.open=cached.item.open;
      const failure=item.type==='assistant'?window.IgorekWorkspaceTabs.taskError(taskRows.get(item.task_id),data.meta?.profile||'профиль'):'';
      if(failure)item.text=failure;
      let node=cached?.node;
      if(!node){node=item.type==='tool_group'?renderActivityGroup(item,()=>draw(snapshot),'side:'+tab.id+':'+tab.session):item.type==='tool'?renderTool(item,()=>draw(snapshot)):renderMessage(item,{sessionId:tab.session,changed:load});nodes.set(item.id,{node,item});}else{timelineUpdaters.get(node)?.(item);cached.item=item;}
      if(item.type==='assistant'){
        node.classList.toggle('workspace-chat-error',Boolean(failure));let help=node.querySelector('.workspace-chat-message-actions');
        if(failure&&!help){help=element('div','workspace-chat-message-actions');const settings=element('button','workspace-chat-settings');settings.type='button';settings.title='Настройки исполнителя';settings.setAttribute('aria-label',settings.title);settings.append(workspaceGlyph('settings'),element('span','','Настройки'));settings.addEventListener('click',()=>window.IgorekDesktop?.mcp('settings'));help.append(settings);node.querySelector('.message-content').append(help);}else if(!failure)help?.remove();
      }
      retained.add(item.id);ordered.push(node);
      if(item.task_id&&last.get(item.task_id)===index){const task=taskRows.get(item.task_id);if(task&&['ok','fail','cancelled'].includes(task.status)){
        const id='finish-'+item.task_id;let line=nodes.get(id)?.node;if(!line){line=element('div');nodes.set(id,{node:line});}
        line.className='status-line '+(task.status==='ok'?'ok':'warn');line.textContent=t(task.status==='ok'?'task.finished':'task.finished_status',{status:task.status});retained.add(id);ordered.push(line);
      }}
    }
    let cursor=timeline.firstChild;for(const node of ordered){if(node===cursor)cursor=cursor.nextSibling;else timeline.insertBefore(node,cursor);}while(cursor){const next=cursor.nextSibling;cursor.remove();cursor=next;}for(const id of nodes.keys())if(!retained.has(id))nodes.delete(id);
    indicator.hidden=!data.running_task_id;if(pinned)messages.scrollTop=messages.scrollHeight;sync();
  };
  const load=async()=>{
    if(!valid())return;if(loading){pending=true;return;}clearTimeout(workspaceChatTimer);
    const id=tab.session,epoch=sessionGeneration;
    if(!id||document.hidden){workspaceChatTimer=setTimeout(load,3000);return;}
    loading=true;
    try{const data=await api('/api/sessions/'+encode(id));if(!valid()||tab.session!==id||sessionGeneration!==epoch)return;snapshot=data;status.textContent='';draw(data);}
    catch(error){if(valid()&&tab.session===id&&sessionGeneration===epoch)status.textContent=error.message;}
    finally{loading=false;if(valid()){const delay=pending?0:3000;pending=false;workspaceChatTimer=setTimeout(load,delay);}}
  };
  picker.addEventListener('change',()=>{tab.session=picker.value;tab.title=picker.selectedOptions[0]?.textContent;tab.draft='';input.value='';sessionGeneration++;snapshot=null;nodes.clear();timeline.replaceChildren();context.replaceChildren();chip.replaceChildren();indicator.hidden=true;releaseWorkspaceChatAttachments(tab);renderFiles();renderWorkspaceTabs();load();});
  stop.addEventListener('click',async()=>{const id=tab.session;stop.disabled=true;try{await api('/api/sessions/'+encode(id)+'/cancel',{method:'POST',body:JSON.stringify({mode:'cancel'})});if(valid()&&tab.session===id)load();}catch(error){if(valid())status.textContent=error.message;}finally{stop.disabled=false;}});
  form.addEventListener('submit',async event=>{
    event.preventDefault();const id=tab.session,text=input.value.trim(),files=tab.attachments.slice(),planMode=Boolean(tab.planMode);if(!id||send.disabled||snapshot?.meta?.agent_session)return;
    sending=true;renderFiles();
    try{
      if(window.IgorekAgent&&!(await window.IgorekAgent.ensureProfile(snapshot?.meta?.profile)))return;
      if(!valid()||tab.session!==id)return;
      for(const item of files){if(item.uploadId)continue;item.status='uploading';renderFiles();const result=await api('/api/sessions/'+encode(id)+'/uploads',{method:'POST',body:item.file,headers:{'Content-Type':item.file.type||'application/octet-stream','X-File-Name':encodeURIComponent(item.name)}});item.uploadId=String(result.upload_id||'');item.status=item.uploadId?'uploaded':'error';if(!item.uploadId)throw Error(t('attachments.error'));if(!valid()||tab.session!==id)return;}
      const sent = await api('/api/sessions/'+encode(id)+'/messages',{method:'POST',body:JSON.stringify({text,idempotency_key:crypto.randomUUID(),upload_ids:files.map(item=>item.uploadId),plan_mode:planMode,desktop_ui:Boolean(state.uiNavigation?.length),demo_language:lang})});
      if (sent?.demo && sent.session_id) await selectSession(sent.session_id);
      if(tab.session===id){if(input.value.trim()===text){tab.draft='';input.value='';}for(const item of files){if(item.previewUrl)URL.revokeObjectURL(item.previewUrl);}tab.attachments=tab.attachments.filter(item=>!files.includes(item));}
      if(valid()){renderFiles();resize();load();}
    }catch(error){if(valid())status.textContent=error.message;}finally{sending=false;if(valid())renderFiles();}
  });
  renderFiles();resize();load();
}
byId('workspace-tab-new').addEventListener('click', newWorkspaceTab);
new ResizeObserver(() => { const strip = byId('workspace-tabs'); strip.classList.toggle('workspace-tabs-dense', state.panel.workspace.tabs.length * 80 > strip.clientWidth); }).observe(byId('workspace-tabs'));
window.IgorekDesktop?.onWorkspacePopup?.(url => { try { workspaceOpenSite(url, state.panel.workspace.open('start')); } catch (error) { toast(error.message, 'error'); } });
window.IgorekDesktop?.onWorkspaceBrowserState?.(value => { workspaceBrowserState = value;renderWorkspaceFavorites();updateWorkspaceFavoriteButton();if(state.panel.workspace.active?.kind==='downloads')workspaceDownloadsView?.update(value); renderWorkspaceTabs(); if (state.panel.open && state.panel.workspace.active?.kind === 'browser') updateWorkspaceBrowser(); });
window.addEventListener('resize', () => workspaceBrowserCall('layout').catch(() => {}));
window.IgorekSelectBrowserTab = async id => {
  if(!window.IgorekDesktop||!state.paired)return false;
  const tab=state.panel.workspace.tabs.find(item=>item.id===id&&item.kind==='browser');
  if(!tab)return false;
  showMainView();activateWorkspaceTab(tab);openPanel();
  const reply=await workspaceBrowserCall('select',{id});
  return reply.active===id;
};
window.addEventListener('igorek:open-chatgpt-tab', () => { showMainView(); const tab = state.panel.workspace.tabs.find(item => item.kind === 'browser' && item.title === 'ChatGPT'); if (tab) { activateWorkspaceTab(tab); openPanel(); } else { const start = state.panel.workspace.open('start'); workspaceOpenSite(window.IgorekWorkspaceTabs.sites.chatgpt, start).then(() => { start.title = 'ChatGPT'; }); } });
window.addEventListener('igorek:open-opencode-tab',()=>{showMainView();activateWorkspaceTab(state.panel.workspace.open('opencode'));openPanel();});

ui.pairForm.addEventListener("submit", pairBrowser);
ui.sessionForm.addEventListener("submit", createSession);
byId("nav-updates").addEventListener("click", async () => {
  if (typeof window.IgorekDesktop?.checkUpdates === "function") {
    try { await window.IgorekDesktop.checkUpdates(); }
    catch { toast(lang === "en" ? "Update check is unavailable. Try again later." : "Проверка обновлений недоступна. Повторите позже."); }
    return;
  }
  const dialog = element("dialog", "updates-dialog"), title = element("h2", "", lang === "en" ? "Igorek updates" : "Обновления Игорька");
  const version = element("p", "access-note", lang === "en" ? "Version: …" : "Версия: …");
  const note = element("p", "", lang === "en" ? "Open the Electron desktop to check and download official GitHub releases." : "Проверка и скачивание официальных GitHub-релизов доступны в desktop-приложении Игорька.");
  const close = element("button", "secondary-button", lang === "en" ? "Close" : "Закрыть"); close.type = "button";
  close.addEventListener("click", () => dialog.close()); dialog.addEventListener("close", () => dialog.remove());
  dialog.append(title, version, note, close); document.body.append(dialog); dialog.showModal();
  try { const about = await api("/api/about"); version.textContent = (lang === "en" ? "Version: " : "Версия: ") + String(about.version || "—"); }
  catch { version.textContent = lang === "en" ? "Version unavailable" : "Версия недоступна"; }
});
ui.composer.querySelector(".composer-left").append(createComposerAccessButton());
window.addEventListener("resize", positionComposerAccess);
window.addEventListener("focus", () => { if (state.accessReady) refreshComposerAccess(); });
window.setInterval(() => { if (state.accessReady && !document.hidden) refreshComposerAccess(); }, 60000);
async function openSummarySource(file,session){
  if(!state.paired||state.selected!==session)return;
  try{
  if(file.kind==='file'){
    const data=await api('/api/sessions/'+encode(session)+'/files');
    if(state.selected!==session||!state.paired)return;
    const files=Array.isArray(data.files)?data.files:[];
    const named=files.filter(item=>item.name===file.name);
    const current=file.path?files.find(item=>item.path===file.path):files.find(item=>item.id===file.id)||(named.length===1?named[0]:null);
    if(!current){toast(t('viewer.not_in_attachments'));return;}
    await openFileViewer(current,'files');openPanel();return;
  }
  if(['web','pull_request'].includes(file.kind)){await workspaceOpenSite(file.url);return;}
  const previewUrl='/api/sessions/'+encode(session)+'/uploads/'+encode(file.upload_id);
  if(['image/png','image/jpeg','image/webp','image/gif','image/bmp','image/avif'].includes(file.mime_type))showAttachmentPreview({name:file.name,previewUrl});
  else{const link=element('a');link.href=previewUrl;link.download=file.name;document.body.append(link);link.click();link.remove();}
  }catch(error){if(state.paired&&state.selected===session)toast(error.message,'error');}
}
window.IgorekSummary = window.IgorekSessionSummary.mount({
  host:ui.conversation,getState:()=>state,getLanguage:()=>lang,api,
  openChanges:()=>{switchPanelTab('changes');openPanel();},
  attach:()=>ui.attachInput.click(),
  openSource:openSummarySource,
});
new ResizeObserver(()=>{ui.conversation.style.setProperty('--summary-composer-height',ui.composer.parentElement.getBoundingClientRect().height+'px');}).observe(ui.composer.parentElement);
ui.composer.addEventListener("submit", sendMessage);
document.getElementById('demo-start')?.addEventListener('click', () => {
  if (!state.selected || state.sending || ui.message.value.trim() || state.attachments.length) return;
  ui.message.value = '/demo';
  ui.composer.requestSubmit();
});
ui.modePlan.addEventListener("click", () => setComposerMode(MODE_PLAN));
ui.modeExecute.addEventListener("click", () => setComposerMode(MODE_EXECUTE));
ui.stop.addEventListener("click", stopSession);
ui.attach.addEventListener("click", () => ui.attachInput.click());
ui.mic.addEventListener("click", toggleMic);
ui.attachInput.addEventListener("change", () => {
  addAttachments(ui.attachInput.files || []);
  ui.attachInput.value = "";
});
ui.logout.addEventListener("click", logout);
byId("new-session").addEventListener("click", openNewSession);
byId("welcome-new-session").addEventListener("click", openNewSession);
byId("cancel-dialog").addEventListener("click", () => ui.dialog.close());
byId("close-dialog").addEventListener("click", () => ui.dialog.close());
byId("open-sidebar").addEventListener("click", () => document.body.classList.add("sidebar-open"));
byId("close-sidebar").addEventListener("click", () => document.body.classList.remove("sidebar-open"));
byId("sidebar-scrim").addEventListener("click", () => document.body.classList.remove("sidebar-open"));
ui.brand.addEventListener("click", openDashboard);
ui.settingsButton.addEventListener("click", openSettings);
ui.panelButton.addEventListener("click", togglePanel);
ui.closePanel.addEventListener("click", closePanel);
ui.panelFullscreen.addEventListener("click", togglePanelFullscreen);
ui.panelResize.addEventListener("pointerdown", startPanelResize);
ui.panelResize.addEventListener("dblclick", resetPanelWidth);
ui.panelResize.addEventListener("keydown", handlePanelResizeKeydown);
applyPanelWidth(panelWidthEffective());
for (const tab of Object.keys(ui.sideTabs)) {
  ui.sideTabs[tab].addEventListener("click", () => switchPanelTab(tab));
}
ui.closeSettings.addEventListener("click", () => ui.settingsDialog.close());
ui.closeDashboard.addEventListener("click", () => ui.dashboardDialog.close());
ui.openLogs.addEventListener("click", openLogs);
ui.closeLogs.addEventListener("click", closeLogs);
ui.logsRefresh.addEventListener("click", () => { logsRefresh().catch((error) => toast(error.message, "error")); });
ui.logsDialog.addEventListener("close", stopLogsAuto);
ui.logsDialog.addEventListener("cancel", persistLogsSize);
// A native resize drag ends with a click event on the dialog itself, and such a
// click must not be mistaken for a backdrop click (it would close the window
// right after resizing). Backdrop clicks land outside the dialog rectangle and
// are dispatched without a drag between press and release.
let logsPressPoint = null;
ui.logsDialog.addEventListener("pointerdown", (event) => {
  logsPressPoint = event.target === ui.logsDialog ? { x: event.clientX, y: event.clientY } : null;
});
ui.logsDialog.addEventListener("click", (event) => {
  if (event.target !== ui.logsDialog) return;
  const press = logsPressPoint;
  logsPressPoint = null;
  const rect = ui.logsDialog.getBoundingClientRect();
  const inside = event.clientX >= rect.left && event.clientX <= rect.right
    && event.clientY >= rect.top && event.clientY <= rect.bottom;
  if (inside) return;
  const dragged = press && (Math.abs(event.clientX - press.x) > 6 || Math.abs(event.clientY - press.y) > 6);
  if (dragged) return;
  closeLogs();
});
logsAutoSwitch = switchButton(false, false, toggleLogsAuto);
logsAutoSwitch.setAttribute("aria-label", t("logs.auto_aria"));
ui.logsAutoSlot.append(logsAutoSwitch);
logsExpandButton = element("button", "mini-button logs-expand", t("logs.expand"));
logsExpandButton.type = "button";
logsExpandButton.setAttribute("aria-pressed", "false");
logsExpandButton.title = t("logs.expand_title");
logsExpandButton.addEventListener("click", toggleLogsExpanded);
ui.logsRefresh.parentNode.insertBefore(logsExpandButton, ui.logsRefresh);
if (typeof window.ResizeObserver === "function") {
  new ResizeObserver(() => {
    window.clearTimeout(logsResizeTimer);
    logsResizeTimer = window.setTimeout(persistLogsSize, 220);
  }).observe(ui.logsDialog);
  new ResizeObserver(() => scheduleRailLayout()).observe(ui.scroll);
}
window.addEventListener("resize", () => {
  handleViewportResize();
  const model = logsSizeModel();
  if (!ui.logsDialog.open || model.expanded || !model.width || !model.height) return;
  Object.assign(model, clampLogsSize(model.width, model.height));
  applyLogsSize();
});
// Page reload: freeing the Excel worker before document export.
window.addEventListener("pagehide", () => { closeFileViewerSession(); });
ui.settingsDialog.addEventListener("close", () => {
  window.clearInterval(state.settingsTimer);
  state.settingsTimer = 0;
});
ui.settingsDialog.addEventListener("click", (event) => { if (event.target === ui.settingsDialog) ui.settingsDialog.close(); });
ui.dashboardDialog.addEventListener("click", (event) => { if (event.target === ui.dashboardDialog) ui.dashboardDialog.close(); });
ui.openAgents.addEventListener("click", openAgents);
ui.closeAgents.addEventListener("click", closeAgents);
ui.agentsDialog.addEventListener("click", (event) => { if (event.target === ui.agentsDialog) closeAgents(); });
ui.agentsRefresh.addEventListener("click", () => { loadExternalSessions(true); });
ui.agentsSearch.addEventListener("input", () => {
  agentsModel().query = ui.agentsSearch.value;
  agentsModel().visible = 0;
  renderAgents();
});
ui.agentsMore.addEventListener("click", () => {
  const model = agentsModel();
  model.visible = (model.visible || AGENTS_PAGE_SIZE) + AGENTS_PAGE_SIZE;
  renderAgentsList();
});
ui.agentsBack.addEventListener("click", () => {
  agentsModel().preview = null;
  renderAgents();
});
ui.agentsPreviewRefresh.addEventListener("click", () => { loadExternalSessionDetail(false); });
ui.agentsLoadMore.addEventListener("click", () => { loadExternalSessionDetail(true); });
ui.agentsImport.addEventListener("click", () => { importExternalSession(); });
window.setInterval(refreshOpenCodeUsageIfVisible, 60000);
ui.scroll.addEventListener("scroll", () => {
  const pinned = scrollIsPinned();
  updateRailActive();
  if (state.programmaticScroll && pinned) return;
  state.pinned = pinned;
});
ui.pairCode.addEventListener("input", () => {
  const raw = ui.pairCode.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 12);
  ui.pairCode.value = (raw.match(/.{1,4}/g) || []).join("-");
});
ui.message.addEventListener("input", () => {
  autoSizeMessage();
  pruneTrackedSkills();
  refreshMention();
});
ui.message.addEventListener("paste", handleMessagePaste);
ui.message.addEventListener("click", refreshMention);
ui.message.addEventListener("keyup", (event) => {
  if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) refreshMention();
});
ui.message.addEventListener("blur", closeMentionMenu);
ui.mentionMenu.addEventListener("mousedown", (event) => event.preventDefault());
ui.message.addEventListener("keydown", (event) => {
  if (!window.IgorekDesktop && (event.metaKey || event.ctrlKey) && event.key === "Enter") ui.composer.requestSubmit();
  else if (
    event.key === "Tab" &&
    !event.shiftKey &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.altKey &&
    mentionModel().menu === "closed"
  ) {
    event.preventDefault();
    toggleComposerMode();
  } else mentionKeydown(event);
});
document.addEventListener("keydown", (event) => {
  if (!window.IgorekDesktop && (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openNewSession();
  }
  if (event.key === "Escape") document.body.classList.remove("sidebar-open");
});
window.addEventListener('igorek:chat-shortcut', event => {
  if (!window.IgorekDesktop || document.querySelector('dialog:modal')) return;
  const action = event.detail?.action;
  if (action === 'focusComposer') { if (state.selected && !ui.message.disabled) ui.message.focus(); return; }
  if (action === 'sendMessage') { if (state.selected && document.activeElement === ui.message && !ui.send.disabled) ui.composer.requestSubmit(); return; }
  const sessions = [...state.sessions].sort((a, b) => Number(b.updated_at || 0) - Number(a.updated_at || 0));
  if (action === 'pinChat') {
    const current = sessions.find(item => item.session_id === state.selected);
    const button = [...ui.sessions.querySelectorAll('.session-item')].find(row => row.dataset.sessionId === state.selected)?.querySelector('.session-pin');
    if (current && button && !button.disabled) button.click();
    return;
  }
  let index = sessions.findIndex(item => item.session_id === state.selected);
  if (action === 'recentChat') index = event.detail.index;
  else if (action === 'nextChat') index = (index + 1) % sessions.length;
  else if (action === 'previousChat') index = index < 0 ? sessions.length - 1 : (index - 1 + sessions.length) % sessions.length;
  else return;
  if (!Number.isInteger(index) || index < 0 || index >= sessions.length || (action === 'recentChat' && index > 8)) return;
  if (sessions[index].session_id !== state.selected) selectSession(sessions[index].session_id);
});
ui.dialog.addEventListener("click", (event) => { if (event.target === ui.dialog) ui.dialog.close(); });
ui.agentsCwd.addEventListener("input", () => {
  const preview = agentsModel().preview;
  if (preview) preview.cwdTouched = true;
  renderAgentsCwdHint();
});
document.addEventListener("click", (event) => {
  if (!ui.cwdCombo.contains(event.target)) sessionCwdCombo.close();
  if (!ui.agentsCwdCombo.contains(event.target)) agentsCwdCombo.close();
});
document.addEventListener("click", (event) => {
  if (mentionModel().menu === "closed") return;
  if (ui.mentionMenu.contains(event.target) || event.target === ui.message) return;
  closeMentionMenu();
});
ui.dialog.addEventListener("close", () => { sessionCwdCombo.close(); state.firstRunExample = false; });
ui.agentsDialog.addEventListener("close", () => agentsCwdCombo.close());
lang = readStoredLang();
composerMode = readStoredMode();
// render.js renders code block labels before app.js loads — it takes them from here.
window.IgorekI18n = { t, get lang() { return lang; } };
applyStaticI18n();
renderComposerMode();
renderRuntime();
renderMicButton();
window.IgorekVoice?.init();
initialize().catch((error) => {
  showConnection();
  ui.pairError.textContent = error.message;
});

window.setInterval(() => {
  if (document.hidden || !state.selected) return;
  if (state.session?.meta?.agent_session || (state.panel.open && state.panel.tab === "subagents")) {
    loadSubagents(state.selected).catch(() => {});
  }
  loadApprovals(state.selected).catch(() => {});
}, 2000);

function renderUserQuestion(item) {
  const card = element("article", "event-card approval-card");
  const form = element("form", "event-copy");
  form.append(element("strong", "", item.input?.title || (lang === "en" ? "Agent question" : "Вопрос агента")));
  appendApprovalOrigin(form, item);
  const fields = [];
  let bound = item;
  for (const [index, question] of (item.input?.questions || []).entries()) {
    const group=element('fieldset','question-group');
    group.append(element('legend','',question.question||question.header||''));
    const name=question.id||String(index),choices=[];
    const input=element('input','question-answer');input.name=name;
    input.type=question.is_secret?'password':'text';input.autocomplete='off';
    input.setAttribute('aria-label',question.options?.length?'Свой ответ':question.question||question.header||'Ответ');
    if(question.options?.length){
      const options=element('div','question-options');
      for(const [optionIndex,option]of question.options.entries()){
        const label=element('label','question-option'),choice=element('input','');
        choice.type=question.is_multiselect?'checkbox':'radio';
        choice.name='question-'+item.request_id+'-'+index;choice.value=option.label;
        const copy=element('span','');copy.append(element('strong','',option.label));
        if(option.description)copy.append(element('small','',option.description));
        label.append(choice,copy);options.append(label);choices.push(choice);
        if(!question.is_multiselect)choice.addEventListener('change',()=>{input.value='';});
      }
      input.placeholder=question.is_multiselect?'Дополнить своим ответом':'Или свой вариант';
      if(!question.is_multiselect)input.addEventListener('input',()=>{if(input.value)for(const choice of choices)choice.checked=false;});
      group.append(options);
    }
    group.append(input);form.append(group);fields.push({name,input,choices});
  }
  const submit = element("button", "approval-button", lang === "en" ? "Send answer" : "Ответить");
  submit.type = "submit"; form.append(submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); submit.disabled = true;
    try {
      const answers=Object.fromEntries(fields.map(({name,input,choices})=>{
        const selected=choices.filter(choice=>choice.checked).map(choice=>choice.value);
        if(input.value.trim())selected.push(input.value.trim());
        return [name,{answers:selected}];
      }));
      if(Object.values(answers).some(value=>!value.answers.length)){toast('Ответь на каждый вопрос или выбери вариант.');submit.disabled=false;return;}
      await api("/api/tasks/" + encode(bound.task_id) + "/user-input/" + encode(bound.request_id), {
        method: "POST", body: JSON.stringify({ response: { answers }, cancelled: false }),
      });
      bound.resolved = "allow_once"; renderTimeline();
    } catch (error) { toast(error.message, "error"); submit.disabled = false; }
  });
  card.append(form);
  // Until permission is granted, the form structure remains stable; the updater only maintains an up-to-date
  // link because upsert replaces the element object in state.items.
  return registerTimelineUpdater(card, (next) => { bound = next; });
}

async function loadAgentJournal(sessionId, generation) {
  let after = 0;
  while (state.selected === sessionId && state.selectGeneration === generation) {
    const page = await api("/api/sessions/" + encode(sessionId) + "/events?after=" + after + "&limit=500");
    if (state.selected !== sessionId || state.selectGeneration !== generation) return;
    const events = page.events || [];
    for (const event of events) {
      if (state.seenSeq.has(event.seq)) continue;
      state.seenSeq.add(event.seq);
      state.lastSeq = Math.max(state.lastSeq, event.seq || 0);
      handleEvent(event); scanJournalEvent(event);
    }
    const next = events.length ? events[events.length - 1].seq : after;
    if (events.length < 500 || next <= after) break;
    after = next;
  }
}

function appendApprovalOrigin(container, item) {
  if (!item.session_id || item.session_id === state.selected) return;
  const button = element("button", "mini-button", lang === "en" ? "Open child agent" : "Открыть дочернего агента");
  button.type = "button";
  button.addEventListener("click", () => selectSession(item.session_id));
  container.append(button);
}


let pendingReviewSession=null;
function openReviewChat(id,title,draft){
  const model=state.panel.workspace;
  let tab=model.tabs.find(t=>t.kind==='chat'&&t.reviewPR===id);
  if(!tab)tab=model.open('chat');
  tab.session=id;tab.reviewPR=id;tab.title=title;tab.draft=draft;
  byId('workspace-special').dataset.tab='';
  state.panel.fullscreen=false;activateWorkspaceTab(tab);openPanel();
}
window.IgorekReviews=window.IgorekGitHubReviews.mount({
 host:byId('github-reviews'),bridge:(op,args)=>window.IgorekDesktop.github(op,args),
 show:()=>{showMainView();state.workspaceView='reviews';ui.workspace.hidden=true;document.body.classList.add('gh-review-mode');byId('nav-home').classList.remove('active');byId('nav-home').removeAttribute('aria-current');byId('nav-reviews').classList.add('active');byId('nav-reviews').setAttribute('aria-current','page');ui.title.textContent='Проверка кода';state.panel.fullscreen=false;renderPanel();},
 chat:openReviewChat,sessions:()=>state.sessions,markdown:(text,host)=>window.IgorekMarkdown.renderMarkdown(text,host),locationChanged:scheduleAppNavigation,
 openSite:url=>workspaceOpenSite(url),
 create:(pr,draft)=>{openNewSession();pendingReviewSession={pr,draft};}
});
byId('nav-reviews').addEventListener('click',()=>window.IgorekReviews.open());
ui.dialog.addEventListener('close',()=>{pendingReviewSession=null;});
window.IgorekDesktop?.onGitHubLocked?.(()=>{pendingReviewSession=null;window.IgorekReviews.hide({forget:true});if(state.workspaceView==='reviews')showMainView();});
if(window.IgorekNavigationHistory){
  const history=new window.IgorekNavigationHistory.History({
    restore:async route=>{
      if(!state.paired)throw Error('Подключение закрыто');
      if(route.session&&route.session!==state.selected){await selectSession(route.session);if(state.selected!==route.session||state.session?.session_id!==route.session)throw Error('Чат недоступен');}
      if(route.view==='scheduled')await showScheduledView();
      else if(route.view==='plugins')await window.IgorekPlugins.show();
      else if(route.view==='reviews'){await window.IgorekReviews.open();if(route.pull)await window.IgorekReviews.select(route.pull);}
      else showMainView();
      if(route.panel){const tab=state.panel.workspace.tabs.find(item=>item.id===route.tab);if(tab)activateWorkspaceTab(tab);openPanel();}else closePanel();
      return appNavigationRoute();
    },
    changed:detail=>{window.dispatchEvent(new CustomEvent('igorek:navigation-state',{detail}));if(!detail.busy)scheduleAppNavigation();},
  });
  window.IgorekAppHistory=Object.freeze({schedule:scheduleAppNavigation,record:value=>history.record(value),back:()=>history.back().catch(error=>toast(error.message,'error')),forward:()=>history.forward().catch(error=>toast(error.message,'error')),state:()=>history.state(),reset:()=>history.reset()});
  scheduleAppNavigation();
}
