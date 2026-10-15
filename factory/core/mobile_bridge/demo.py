"""Persisted introductory demo using ordinary queued model turns and tool events."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from ..artifact_files import parent_handle, read_regular_text
from ..cli_chat import _append_entry, _read_session_meta, _session_path, update_session_meta
from ..prompt_assembly import DEMO_CONTENT, desktop_demo_prompt

TOOLS = (None, 'update_plan', "Write", "Read", 'Edit', "Glob", "Grep", 'update_plan')
SAFE_TOOLS = {'Write', 'Read', 'Edit', 'Glob', 'Grep', 'update_plan'}
LABELS = {
    "ru": ("Давайте познакомимся", 'Составь небольшой план', "Создай заметку о своих возможностях", "Прочитай нашу заметку", 'Измени одну фразу в заметке', "Найди созданный файл", "Найди в заметке текст о работе с файлами", 'Подведи итог нашего плана'),
    "en": ("Let's get acquainted", 'Make a short plan', "Create a note about your capabilities", "Read our note", 'Change one sentence in the note', "Find the file we created", "Find the text about working with files", 'Complete our plan'),
}
GUIDANCE = {
    'ru': (
        'Здравствуйте! Я Игорёк. Сейчас покажу, как со мной работать. Вам не нужно искать кнопки: я проведу вас по шагам.',
        'Сначала составим план. Так удобно следить за большой задачей: план и его изменения видны в боковой панели.',
        'Теперь создам небольшую заметку о своих возможностях. В карточке Write можно посмотреть, какой файл и с каким текстом я записываю.',
        'Прочитаем заметку обратно. Read показывает настоящее содержимое файла — так можно разбирать документы и проверять результат.',
        'Дальше изменю только последнюю фразу. Edit полезен для точечных правок, когда весь документ переписывать не нужно.',
        'Найдём заметку среди файлов папки. Glob ищет по имени или расширению, например все текстовые файлы.',
        'А теперь найдём фразу внутри заметки. Grep ищет по содержимому — удобно, когда известно, что написано, но не помнишь, в каком файле.',
        'Осталось отметить выполненные пункты плана. После этого можно дать мне свою задачу или спокойно посмотреть карточки инструментов.',
    ),
    'en': (
        'Hello! I am Igorek. I will walk you through working with me, one step at a time. No need to hunt for buttons.',
        'First, a short plan. It helps track larger tasks; you can see the plan and its changes in the side panel.',
        'I will create a note about my capabilities. Expand the Write card to see which file I create and what goes into it.',
        'Now we will read the note back. Read shows the actual file contents, useful for understanding documents and checking results.',
        'Next, I will change just the last sentence. Edit makes targeted changes without rewriting the whole document.',
        'Let us find our note among the files. Glob searches by name or extension, such as all text files.',
        'Now we will find a phrase inside the note. Grep searches contents when you know what was written but not where.',
        'Finally, I will mark our plan complete. Then you can give me your own task or explore the tool cards at your own pace.',
    ),
}


def start(service, source_session: str, *, key: str, language: str) -> dict:
    from .service import MobileConflict, MobileInvalid

    existing = service.store.lookup_request(key) if key else None
    if existing and existing.get("session_id"):
        return {"session_id": existing["session_id"], "task_id": existing.get("task_id"), "duplicate": True, 'demo': True}
    source = service.get_session(source_session)
    if source.get("running_task_id"):
        raise MobileConflict("Сначала дождитесь завершения текущей задачи или остановите её.")
    profile = str(source["meta"].get("profile") or service.profile)
    service._require_available_profile(profile)
    if service._profile_backend(profile) != "cc-backend":
        raise MobileInvalid("Для /demo выберите профиль встроенного агента в настройках.")
    language = "en" if language == "en" else "ru"
    parent = Path(service.data_dir).resolve() / "demos"
    if service._isolation_status_value(profile) == 'kvm':
        parent = Path(service.workdir).resolve() / 'demos'
    import os
    if os.name == 'nt':
        from ..platform_support.windows_files import pinned_parent
        anchor = pinned_parent
    else:
        anchor = parent_handle
    with anchor(parent / ".entry", create=True):
        pass
    root = tempfile.mkdtemp(prefix="demo-", dir=parent)
    session = service.create_session(profile=profile, cwd=root)
    session_id = session["session_id"]
    demo = {"status": "running", "step": 0, "language": language, "results": [], "task_id": ""}
    path = _session_path(session_id)
    update_session_meta(path, {"demo": demo, "title": "Demo" if language == "en" else "Знакомство с Игорьком"})
    intro = (
        "Let's get to know each other through a short guided tour: conversation, a plan and real file tools. "
        "You can expand tool cards as we go. Everything stays in this separate demo folder. "
        "Use Stop at any time. Your selected model handles these turns, so normal provider usage applies."
        if language == "en" else
        "Давайте познакомимся: вместе попробуем общение, план и настоящую работу с файлами. "
        "По ходу можно раскрывать карточки инструментов. Всё происходит в отдельной папке демонстрации. "
        "Можно остановить в любой момент кнопкой «Стоп». Шаги выполняет выбранная модель: расходуется её обычная квота."
    )
    _append_entry(path, role="assistant", text=intro)
    result = queue_step(service, session_id, demo)
    if key:
        service.store.remember_request(key, kind="demo", session_id=session_id, task_id=result["task_id"])
    return {**result, "session_id": session_id, "demo": True}


def queue_step(service, session_id: str, demo: dict) -> dict:
    step = demo["step"]
    text = GUIDANCE[demo['language']][step]
    _append_entry(_session_path(session_id), role='assistant', text=text)
    if step:
        _notice(service, session_id, f'demo-guide-{session_id}-{step}', text)
    result = service._send_message(
        session_id, desktop_demo_prompt(step, demo["language"]),
        idempotency_key=f"demo:{session_id}:{step}", demo_step=step,
    )
    demo["task_id"] = result["task_id"]
    update_session_meta(_session_path(session_id), {"demo": demo})
    return result


def verified(service, task: dict, demo: dict) -> bool | None:
    if task["status"] != "ok" or not str(task.get("output_text") or "").strip():
        return False
    expected = TOOLS[demo["step"]]
    if expected is None:
        return True
    after = 0
    found = False
    finished = False
    content = DEMO_CONTENT[demo['language']]
    needle = 'files' if demo['language'] == 'en' else 'файлами'
    while True:
        page = service.store.list_events(session_id=task["metadata"]["session"], after_seq=after, limit=500)
        if not page:
            break
        for event in page:
            payload = event["payload"]
            if event.get('task_id') == task['task_id'] and event['kind'] == 'task.finish':
                finished = True
            if event.get("task_id") != task["task_id"] or event["kind"] != "tool.result":
                continue
            if payload.get("tool_name") != expected or payload.get("status") != "done":
                continue
            args = payload.get("tool_input") or {}
            preview = str(payload.get("result_preview") or "")
            if expected in {"Write", "Read", 'Edit'}:
                found |= args.get("file_path") == "demo-note.txt" and (expected != "Read" or needle in preview)
            elif expected == "Glob":
                found |= args.get("pattern") == "*.txt" and "demo-note.txt" in preview
            elif expected == 'Grep':
                found |= args.get("pattern") == needle and needle in preview
            else:
                plan = args.get('plan') or []
                valid_plan = len(plan) == 3 and all(isinstance(item, dict) for item in plan)
                if demo['step'] == 7:
                    found |= valid_plan and all(item.get('status') == 'completed' for item in plan)
                else:
                    found |= valid_plan and [item.get('status') for item in plan] == ['in_progress', 'pending', 'pending']
        after = page[-1]["seq"]
    if not finished:
        return None if time.time() - int(task.get('updated_at') or 0) < 10 else False
    if not found:
        return False
    if expected in {"Write", 'Edit'}:
        root = Path(task["metadata"]["cwd"])
        try:
            if expected == 'Edit':
                old = 'Let us try it together.' if demo['language'] == 'en' else 'Давайте попробуем вместе.'
                new = 'You can now give me your own task.' if demo['language'] == 'en' else 'Теперь можно дать мне свою задачу.'
                content = content.replace(old, new)
            return read_regular_text(root / "demo-note.txt", max_bytes=4096).rstrip('\n') == content.rstrip('\n')
        except (OSError, ValueError):
            return False
    return True


def complete(service, task: dict) -> None:
    """Advance exactly once after a finalized turn; fail closed on missing evidence."""
    if not task["metadata"].get("demo_run"):
        return
    session_id = task["metadata"]["session"]
    path = _session_path(session_id)
    meta = _read_session_meta(path) or {}
    demo = meta.get("demo")
    if not isinstance(demo, dict) or demo.get("status") != "running" or demo.get("task_id") != task["task_id"]:
        return
    passed = verified(service, task, demo)
    if passed is None:
        return
    demo["results"].append({"step": demo["step"], "passed": passed})
    if passed and demo["step"] < len(TOOLS) - 1:
        demo["step"] += 1
        queue_step(service, session_id, demo)
        return
    demo["status"] = "completed" if passed else "stopped"
    update_session_meta(path, {"demo": demo})
    english = demo["language"] == "en"
    lines = ["Here's what worked:" if english else "Вот что получилось:"]
    for item in demo["results"]:
        lines.append(("✓ " if item["passed"] else "○ ") + LABELS[demo["language"]][item["step"]])
    if not passed:
        lines.append("This step didn't finish, so we stopped here. Look at the message or tool card just above; "
                     "if the model could not respond, check its connection in Settings and storage. "
                     "You can try /demo again in a new chat." if english else
                     "Этот шаг не завершился, поэтому дальше не идём. Посмотрите сообщение или карточку инструмента выше; "
                     "если модель не отвечает, проверьте подключение в разделе «Настройки и хранилище». "
                     "Можно попробовать /demo ещё раз в новом чате.")
    lines.append(
        "Now try it yourself: ask to read demo-note.txt again, or close and reopen the app to see this conversation. "
        "This chat stays limited to the demo folder; start a new chat for your own work. "
        "Installation, updates, microphone and notifications still need a human try. No report needed."
        if english else
        "Теперь можно попробовать самому: попросите ещё раз прочитать demo-note.txt или закройте и откройте приложение — "
        "переписка должна сохраниться. Этот чат остаётся ограничен папкой демонстрации; для своей работы создайте новую сессию. "
        "Установку, обновление, микрофон и уведомления эта демонстрация не проверяет. Никаких отчётов писать не нужно."
    )
    lines.append(
        'Next: ask a question or request a plan in chat. If a microphone button is available, try voice input. '
        'The browser and connected services have their own access settings: [open Plugins](igorek://ui/plugins), '
        '[model settings](igorek://ui/models), [notification settings](igorek://ui/notifications) or [browser settings](igorek://ui/browser). '
        'These extra capabilities were not tested by this tour.'
        if english else
        'Куда дальше: задайте свой вопрос или попросите составить план прямо в чате. Если доступна кнопка микрофона, попробуйте голосовой ввод. '
        'Дальше можно открыть [Плагины](igorek://ui/plugins), [настройки модели](igorek://ui/models), '
        '[уведомления](igorek://ui/notifications) или [настройки браузера](igorek://ui/browser). '
        'Эти дополнительные возможности мы сейчас не проверяли.'
    )
    text = "\n\n".join(lines)
    _append_entry(path, role="assistant", text=text)
    _notice(service, session_id, f'demo-summary-{session_id}', text)


def _notice(service, session_id: str, identity: str, text: str) -> None:
    payload = {'round': 0, 'text': text, 'demo_notice': True}
    seq = service.store.append_event(kind='model.round_response', payload=payload,
                                     session_id=session_id, task_id=identity)
    service._publish_event(kind='model.round_response', payload=payload,
                           session_id=session_id, task_id=identity, seq=seq)


def stop(service, session_id: str) -> None:
    path = _session_path(session_id)
    demo = (_read_session_meta(path) or {}).get("demo")
    if isinstance(demo, dict) and demo.get("status") == "running":
        demo["status"] = "stopped"
        update_session_meta(path, {"demo": demo})
        text = ('We stopped the tour. You can still read the conversation and the files already created. '
                'To start again, send /demo from a new chat.' if demo['language'] == 'en' else
                'Демонстрацию остановили. Переписку и уже созданные файлы можно спокойно посмотреть. '
                'Чтобы начать заново, отправьте /demo из нового чата.')
        _append_entry(path, role='assistant', text=text)
        _notice(service, session_id, f'demo-stopped-{session_id}', text)
