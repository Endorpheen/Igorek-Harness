from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .runtime import get_data_root

BOOTSTRAP_STEPS = ["user_name", "agent_name", "mission", "style", "notes"]
DEFAULT_BOOTSTRAP_STATE = {
    "complete": False,
    "step": "user_name",
    "answers": {},
    "started_at": None,
    "completed_at": None,
}
MEMORY_WRITE_BLOCK_RE = re.compile(
    r"\[\[MEMORY_WRITE\]\]\s*scope:\s*(long|daily)\s*fact:\s*(.*?)\s*\[\[/MEMORY_WRITE\]\]",
    re.IGNORECASE | re.DOTALL,
)
EXPLICIT_MEMORY_PATTERNS = [
    re.compile(r"^\s*/remember\s+(.*)$", re.IGNORECASE | re.DOTALL),
]


@dataclass(frozen=True)
class FoundationPaths:
    data_root: Path
    bootstrap: Path
    identity: Path
    user: Path
    soul: Path
    memory_index: Path
    memory_dir: Path
    state: Path


def get_foundation_paths(data_dir: str | None = None) -> FoundationPaths:
    data_root = Path(data_dir).expanduser() if data_dir else get_data_root()
    return FoundationPaths(
        data_root=data_root,
        bootstrap=data_root / "BOOTSTRAP.md",
        identity=data_root / "IDENTITY.md",
        user=data_root / "USER.md",
        soul=data_root / "SOUL.md",
        memory_index=data_root / "MEMORY.md",
        memory_dir=data_root / "memory",
        state=data_root / ".bootstrap-state.json",
    )


def daily_memory_path(data_dir: str | None = None, ts: float | None = None) -> Path:
    paths = get_foundation_paths(data_dir)
    stamp = time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))
    return paths.memory_dir / f"{stamp}.md"


def _read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(default))


def _write_text(path: Path, text: str, force: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _append_section(path: Path, heading: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    chunk = f"## {heading}\n{body.strip()}\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8").rstrip()
        if chunk.strip() in existing:
            return
        path.write_text(existing + "\n\n" + chunk, encoding="utf-8")
    else:
        path.write_text(f"# MEMORY\n\n{chunk}", encoding="utf-8")


def _append_bullet(path: Path, heading: str, item: str, root_title: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_item = re.sub(r"\s+", " ", item).strip()
    if not normalized_item:
        return False

    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = root_title.rstrip() + "\n"

    if re.search(rf"^-\s+{re.escape(normalized_item)}$", content, re.MULTILINE):
        return False

    heading_re = re.compile(rf"(^##\s+{re.escape(heading)}\s*$)(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
    bullet = f"- {normalized_item}"
    match = heading_re.search(content)
    if match:
        section = match.group(0).rstrip()
        new_section = section + "\n" + bullet + "\n"
        updated = content[: match.start()] + new_section + content[match.end() :]
    else:
        updated = content.rstrip() + f"\n\n## {heading}\n{bullet}\n"

    path.write_text(updated.rstrip() + "\n", encoding="utf-8")
    return True


def ensure_foundation_files(data_dir: str | None = None, force: bool = False) -> dict[str, str]:
    paths = get_foundation_paths(data_dir)
    paths.data_root.mkdir(parents=True, exist_ok=True)
    paths.memory_dir.mkdir(parents=True, exist_ok=True)

    _write_text(
        paths.bootstrap,
        "# BOOTSTRAP\n\n"
        "Этот файл описывает первый запуск Игорька.\n\n"
        "На первой пустой сессии Игорёк должен: \n"
        "1. Представиться как новый агент.\n"
        "2. Спросить, кто пользователь и как его называть.\n"
        "3. Спросить, как называть самого агента.\n"
        "4. Спросить о назначении агента, стиле общения и важных ограничениях.\n"
        "5. Записать это в IDENTITY.md, USER.md, SOUL.md, MEMORY.md и memory/YYYY-MM-DD.md.\n"
        "6. После завершения bootstrap удалить этот файл или пометить bootstrap завершённым.\n",
        force=force,
    )
    _write_text(paths.identity, "# IDENTITY\n\nBootstrap ещё не завершён.\n", force=force)
    _write_text(paths.user, "# USER\n\nBootstrap ещё не завершён.\n", force=force)
    _write_text(paths.soul, "# SOUL\n\nBootstrap ещё не завершён.\n", force=force)
    _write_text(paths.memory_index, "# MEMORY\n\nДолгосрочная память Игорька.\n", force=force)

    if force or not paths.state.exists():
        state = dict(DEFAULT_BOOTSTRAP_STATE)
        state["started_at"] = int(time.time())
        paths.state.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    daily = daily_memory_path(data_dir)
    if force or not daily.exists():
        _write_text(daily, f"# {daily.stem}\n\nBootstrap/ежедневная память.\n", force=force)

    return {
        "bootstrap": str(paths.bootstrap),
        "identity": str(paths.identity),
        "user": str(paths.user),
        "soul": str(paths.soul),
        "memory_index": str(paths.memory_index),
        "daily_memory": str(daily),
        "state": str(paths.state),
    }


def load_bootstrap_state(data_dir: str | None = None) -> dict:
    paths = get_foundation_paths(data_dir)
    ensure_foundation_files(data_dir=data_dir, force=False)
    return _read_json(paths.state, DEFAULT_BOOTSTRAP_STATE)


def save_bootstrap_state(state: dict, data_dir: str | None = None) -> None:
    paths = get_foundation_paths(data_dir)
    paths.state.parent.mkdir(parents=True, exist_ok=True)
    paths.state.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def bootstrap_complete(data_dir: str | None = None) -> bool:
    state = load_bootstrap_state(data_dir)
    return bool(state.get("complete"))


def bootstrap_active(data_dir: str | None = None) -> bool:
    return not bootstrap_complete(data_dir)


def memory_write_protocol_instructions() -> str:
    return (
        "## Memory write protocol\n"
        "Память обычно обрабатывается отдельным фоновым memory-review после твоего ответа. "
        "В основном ответе не пиши пользователю фразы вроде 'я записал это в память' и не объясняй внутренний memory-flow. "
        "Если runtime не поддерживает отдельный memory-review, ты можешь fallback-режимом запросить сохранение устойчивой памяти, "
        "но только если факт реально пригодится в будущих сессиях. "
        "Особенно подходят: предпочтения пользователя, стабильные личные факты, окружение, долгоживущие проектные правила, повторяющиеся коррекции. "
        "Не сохраняй ход текущего диалога, разовые вопросы, отчёты о выполненной задаче, временные TODO, свои ответы, похвалу/благодарность как long memory или формулировки вида 'пользователь спросил/запросил/попросил'. "
        "Fallback-блок должен быть скрытым и строго такого вида:\n\n"
        "[[MEMORY_WRITE]]\n"
        "scope: long\n"
        "fact: краткий сохранённый факт\n"
        "[[/MEMORY_WRITE]]\n\n"
        "scope=long используй для устойчивых фактов, предпочтений, личности, местоположения, техники, долгих задач. "
        "scope=daily используй только для краткоживущего контекста текущего дня. "
        "fact пиши кратко, без воды, одной-двумя фразами. "
        "Не создавай и не редактируй файлы MEMORY.md, USER.md, IDENTITY.md или SOUL.md напрямую из рабочей директории задачи. "
        "Фоновый memory-review может исправить или удалить точную запись из раздела Saved facts файла MEMORY.md, "
        "если пользователь явно попросил исправление или удаление и указал эту запись; остальные файлы и разделы он не меняет. "
        "Поскольку review выполняется после ответа, не утверждай, что запись уже добавлена, исправлена или удалена; сообщи, что запрос передан на проверку. "
        "Приложение подтвердит изменение отдельной карточкой только после фактической записи. "
        "Если runtime поддерживает только fallback-протокол, используй его только для добавления фактов, не имитируй исправление или удаление новой добавочной записью. "
        "Если память писать не нужно, никакой MEMORY_WRITE блок не добавляй."
    )


def session_search_guidance() -> str:
    return (
        "## Session search\n"
        "У тебя есть отдельный tool `SessionSearch` для recall прошлых Telegram/TUI-сессий. "
        "Это не long-memory, а поиск по прошлым transcript'ам. "
        "Используй `SessionSearch` проактивно, когда пользователь ссылается на прошлые разговоры, "
        "старые решения, прошлые задачи или просит вспомнить, что вы делали раньше. "
        "Если нужен общий обзор недавней активности, вызывай `SessionSearch` без query. "
        "Если нужен recall по теме, передавай query с ключевыми словами."
    )


def foundation_prompt_context(data_dir: str | None = None) -> str:
    if not bootstrap_complete(data_dir):
        return ""

    paths = get_foundation_paths(data_dir)
    sections: list[str] = []
    mapping = [
        ("IDENTITY", paths.identity),
        ("USER", paths.user),
        ("SOUL", paths.soul),
        ("MEMORY", paths.memory_index),
        ("DAILY_MEMORY", daily_memory_path(data_dir)),
    ]
    for label, path in mapping:
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            continue
        sections.append(f"[{label}]\n{content}")

    if not sections:
        return ""

    return (
        "Ниже твоя долговременная foundation-память для этого пользователя. "
        "Это не одноразовая история текущей сессии, а стабильный контекст, общий для всех сессий этого data_dir. "
        "Считай эти сведения приоритетными фактами о себе и пользователе.\n\n"
        + "\n\n".join(sections)
        + "\n\n"
        + memory_write_protocol_instructions()
        + "\n\n"
        + session_search_guidance()
    )


def extract_explicit_memory_write_request(user_text: str) -> tuple[str, str] | None:
    text = (user_text or "").strip()
    if not text:
        return None
    for pattern in EXPLICIT_MEMORY_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        fact = re.sub(r"\s+", " ", match.group(1)).strip(" .,!?:;\n\t")
        if not fact:
            return None
        scope = "daily" if re.search(r"\b(?:сегодня|сегодняшн|на сегодня|до вечера)\b", fact, re.IGNORECASE) else "long"
        return scope, fact
    return None


def extract_memory_write_blocks(text: str) -> tuple[str, list[dict[str, str]]]:
    payload = text or ""
    writes: list[dict[str, str]] = []

    def replacer(match: re.Match[str]) -> str:
        scope = match.group(1).strip().lower()
        fact = re.sub(r"\s+", " ", match.group(2)).strip(" .,!?:;\n\t")
        if fact:
            writes.append({"scope": scope, "fact": fact})
        return ""

    cleaned = MEMORY_WRITE_BLOCK_RE.sub(replacer, payload)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, writes


def strip_memory_status_lines(text: str) -> str:
    """Remove the model’s internal (metadata) phrases about memory writing from the visible response."""
    cleaned_lines: list[str] = []
    for line in (text or "").splitlines():
        normalized = line.strip()
        if re.fullmatch(r"(?i)(я\s+)?записал[аио]?\s+это\s+в\s+память[.!]?", normalized):
            continue
        if re.fullmatch(r"(?i)память\s+обновлена(?:\s*\(.*?\))?[.!]?", normalized):
            continue
        if re.fullmatch(r"(?i)сохран[её]нн?ый\s+факт:.*", normalized):
            continue
        cleaned_lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()


def write_memory_fact(fact: str, data_dir: str | None = None, scope: str = "long", source: str = "user") -> dict[str, object]:
    ensure_foundation_files(data_dir=data_dir, force=False)
    paths = get_foundation_paths(data_dir)
    normalized_fact = re.sub(r"\s+", " ", (fact or "")).strip()
    if not normalized_fact:
        return {"saved": False, "scope": scope, "fact": "", "paths": []}

    daily_path = daily_memory_path(data_dir)
    timestamp = time.strftime("%H:%M", time.localtime())
    daily_item = f"[{timestamp}] ({source}) {normalized_fact}"
    daily_saved = _append_bullet(
        daily_path,
        "Memory writes",
        daily_item,
        f"# {daily_path.stem}\n\nBootstrap/ежедневная память.",
    )

    saved_paths = [str(daily_path)] if daily_saved else []
    long_saved = False
    if scope == "long":
        long_saved = _append_bullet(paths.memory_index, "Saved facts", normalized_fact, "# MEMORY\n\nДолгосрочная память Игорька.")
        if long_saved:
            saved_paths.append(str(paths.memory_index))

    return {
        "saved": bool(saved_paths),
        "scope": scope,
        "fact": normalized_fact,
        "paths": saved_paths,
        "daily_saved": daily_saved,
        "long_saved": long_saved,
    }


def bootstrap_intro_message(data_dir: str | None = None) -> str:
    state = load_bootstrap_state(data_dir)
    answers = state.get("answers") or {}
    if answers:
        return bootstrap_next_prompt(state)
    return (
        "Привет. Я только что запустился и пока ещё не знаю, кто я для тебя. "
        "Давай быстро настроим мою основу: identity, user, soul и память.\n\n"
        "Первый вопрос: как тебя называть?"
    )


def bootstrap_next_prompt(state: dict) -> str:
    step = state.get("step", "user_name")
    prompts = {
        "user_name": "Как тебя называть?",
        "agent_name": "Как ты хочешь называть меня как агента?",
        "mission": "Для чего я тебе нужен? В чём моя основная роль и польза?",
        "style": "Каким должен быть мой стиль общения и работы?",
        "notes": "Есть ли ещё важные ограничения, привычки, предпочтения или контекст, который мне стоит помнить?",
    }
    return prompts.get(step, "Продолжаем bootstrap.")


def _advance_step(step: str) -> str | None:
    try:
        index = BOOTSTRAP_STEPS.index(step)
    except ValueError:
        return None
    if index + 1 >= len(BOOTSTRAP_STEPS):
        return None
    return BOOTSTRAP_STEPS[index + 1]


def _render_identity(answers: dict) -> str:
    return (
        "# IDENTITY\n\n"
        f"- Имя агента: {answers.get('agent_name', 'Игорёк')}\n"
        f"- Создатель/основной пользователь: {answers.get('user_name', 'пользователь')}\n"
        f"- Назначение: {answers.get('mission', '')}\n"
    )


def _render_user(answers: dict) -> str:
    return (
        "# USER\n\n"
        f"- Имя: {answers.get('user_name', '')}\n"
        f"- Важный контекст: {answers.get('notes', 'Пока не заполнено')}\n"
    )


def _render_soul(answers: dict) -> str:
    return (
        "# SOUL\n\n"
        f"- Стиль: {answers.get('style', '')}\n"
        "- Язык общения: русский\n"
        "- Фокус: практичная инженерная помощь без воды\n"
    )


def _write_foundation_summary(answers: dict, data_dir: str | None = None) -> None:
    paths = get_foundation_paths(data_dir)
    _write_text(paths.identity, _render_identity(answers), force=True)
    _write_text(paths.user, _render_user(answers), force=True)
    _write_text(paths.soul, _render_soul(answers), force=True)

    long_term = (
        f"Пользователь: {answers.get('user_name', '')}\n"
        f"Агент: {answers.get('agent_name', 'Игорёк')}\n"
        f"Назначение: {answers.get('mission', '')}\n"
        f"Стиль: {answers.get('style', '')}\n"
        f"Примечания: {answers.get('notes', '') or 'нет'}"
    )
    _append_section(paths.memory_index, "Bootstrap summary", long_term)
    _append_section(daily_memory_path(data_dir), "Bootstrap complete", long_term)

    if paths.bootstrap.exists():
        paths.bootstrap.unlink()


def process_bootstrap_answer(user_text: str, data_dir: str | None = None) -> dict:
    state = load_bootstrap_state(data_dir)
    answers = dict(state.get("answers") or {})
    step = state.get("step", "user_name")
    answers[step] = user_text.strip()

    next_step = _advance_step(step)
    if next_step is None:
        answers.setdefault("agent_name", "Игорёк")
        answers.setdefault("user_name", "пользователь")
        state.update(
            {
                "complete": True,
                "step": "done",
                "answers": answers,
                "completed_at": int(time.time()),
            }
        )
        save_bootstrap_state(state, data_dir)
        _write_foundation_summary(answers, data_dir)
        return {
            "done": True,
            "message": (
                f"Bootstrap завершён. Теперь я {answers.get('agent_name', 'Игорёк')}, "
                f"а тебя я запомнил как {answers.get('user_name', 'пользователь')}. "
                "Я сохранил identity, user, soul и память. Дальше можем работать уже в обычном режиме."
            ),
        }

    state.update({"answers": answers, "step": next_step})
    save_bootstrap_state(state, data_dir)
    return {
        "done": False,
        "message": bootstrap_next_prompt(state),
    }
