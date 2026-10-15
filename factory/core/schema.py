from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

TASK_COLUMNS: list[tuple[str, str]] = [
    ("task_id", "TEXT PRIMARY KEY"),
    ("parent_id", "TEXT"),
    ("stage", "TEXT"),
    ("status", "TEXT"),
    ("profile", "TEXT"),
    ("next_profiles", "TEXT"),
    ("capabilities", "TEXT"),
    ("input_text", "TEXT"),
    ("input_files", "TEXT"),
    ("output_text", "TEXT"),
    ("output_files", "TEXT"),
    ("limits_max_tokens", "INTEGER DEFAULT 4000"),
    ("limits_timeout_s", "INTEGER DEFAULT 1200"),
    ("limits_budget_usd", "REAL DEFAULT 0.02"),
    ("usage_prompt", "INTEGER DEFAULT 0"),
    ("usage_completion", "INTEGER DEFAULT 0"),
    ("usage_lat_ms_p50", "INTEGER DEFAULT 0"),
    ("usage_lat_ms_p95", "INTEGER DEFAULT 0"),
    ("cost_usd", "REAL DEFAULT 0.0"),
    ("error_message", "TEXT"),
    ("retry_count", "INTEGER DEFAULT 0"),
    ("max_retries", "INTEGER DEFAULT 3"),
    ("propose_next", "TEXT"),
    ("metadata", "TEXT"),
    ("approval_blocked_tools", "TEXT"),
    ("created_at", "INTEGER"),
    ("updated_at", "INTEGER"),
    ("started_at", "INTEGER"),
    ("completed_at", "INTEGER"),
]

EVENT_COLUMNS: list[tuple[str, str]] = [
    ("event_id", "TEXT PRIMARY KEY"),
    ("task_id", "TEXT"),
    ("event_type", "TEXT"),
    ("profile", "TEXT"),
    ("pid", "INTEGER"),
    ("base_url", "TEXT"),
    ("model", "TEXT"),
    ("details", "TEXT"),
    ("created_at", "INTEGER"),
]

SETTINGS_COLUMNS: list[tuple[str, str]] = [
    ("key", "TEXT PRIMARY KEY"),
    ("value", "TEXT"),
    ("updated_at", "INTEGER"),
]

CREATE_TASKS_TABLE = (
    "CREATE TABLE IF NOT EXISTS tasks ("
    + ", ".join(f"{name} {column_type}" for name, column_type in TASK_COLUMNS)
    + ")"
)

CREATE_EVENTS_TABLE = (
    "CREATE TABLE IF NOT EXISTS events ("
    + ", ".join(f"{name} {column_type}" for name, column_type in EVENT_COLUMNS)
    + ")"
)

CREATE_SETTINGS_TABLE = (
    "CREATE TABLE IF NOT EXISTS settings ("
    + ", ".join(f"{name} {column_type}" for name, column_type in SETTINGS_COLUMNS)
    + ")"
)

CREATE_SCHEDULES_TABLE = """
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    schedule_type TEXT NOT NULL,
    cron_expression TEXT,
    time_value TEXT,
    timezone TEXT,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER,
    updated_at INTEGER,
    last_run_at INTEGER,
    next_run_at INTEGER,
    run_count INTEGER DEFAULT 0,
    metadata TEXT
)
"""

CREATE_SKILLS_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS skills_state (
    skill_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
)
"""

CREATE_AUTONOMOUS_ACTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS autonomous_actions (
    action_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    profile TEXT,
    entity_kind TEXT,
    entity_id TEXT,
    summary TEXT,
    details TEXT,
    created_at INTEGER NOT NULL
)
"""

CREATE_SECURITY_POLICIES_TABLE = """
CREATE TABLE IF NOT EXISTS security_policies (
    policy_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    policy_json TEXT NOT NULL,
    is_active INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
"""

CREATE_SECURITY_APPROVALS_TABLE = """
CREATE TABLE IF NOT EXISTS security_approvals (
    approval_id TEXT PRIMARY KEY,
    task_id TEXT,
    session_id TEXT,
    approval_type TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    requested_by TEXT,
    resolved_by TEXT,
    resolution_note TEXT,
    created_at INTEGER NOT NULL,
    resolved_at INTEGER
)
"""

CREATE_SECURITY_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS security_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT,
    session_id TEXT,
    event_type TEXT NOT NULL,
    decision TEXT,
    target TEXT,
    policy_name TEXT,
    details_json TEXT,
    created_at INTEGER NOT NULL
)
"""

CREATE_USER_INPUT_REQUESTS_TABLE = """
CREATE TABLE IF NOT EXISTS user_input_requests (
    request_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    response_json TEXT,
    created_at INTEGER NOT NULL,
    resolved_at INTEGER
)
"""


CREATE_SESSION_STEER_MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS session_steer_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    message_id TEXT,
    text TEXT NOT NULL,
    input_files_json TEXT,
    selected_skills_json TEXT,
    created_at INTEGER NOT NULL,
    consumed_at INTEGER
)
"""


def _ensure_columns(
    conn: sqlite3.Connection, table_name: str, columns: list[tuple[str, str]]
) -> None:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing = {row[1] for row in cursor.fetchall()}

    for column_name, column_type in columns:
        if column_name in existing:
            continue
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TASKS_TABLE)
    conn.execute(CREATE_EVENTS_TABLE)
    conn.execute(CREATE_SETTINGS_TABLE)
    conn.execute(CREATE_SCHEDULES_TABLE)
    conn.execute(CREATE_SKILLS_STATE_TABLE)
    conn.execute(CREATE_AUTONOMOUS_ACTIONS_TABLE)
    conn.execute(CREATE_SECURITY_POLICIES_TABLE)
    conn.execute(CREATE_SECURITY_APPROVALS_TABLE)
    conn.execute(CREATE_SECURITY_EVENTS_TABLE)
    conn.execute(CREATE_USER_INPUT_REQUESTS_TABLE)
    conn.execute(CREATE_SESSION_STEER_MESSAGES_TABLE)
    _ensure_columns(conn, "tasks", TASK_COLUMNS)
    _ensure_columns(conn, "events", EVENT_COLUMNS)
    _ensure_columns(conn, "settings", SETTINGS_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_security_events_task_created ON security_events(task_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_security_approvals_status_created ON security_approvals(status, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_input_requests_task_status_created "
        "ON user_input_requests(task_id, status, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_steer_task_pending "
        "ON session_steer_messages(task_id, consumed_at, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_steer_session_pending "
        "ON session_steer_messages(session_id, consumed_at, id)"
    )
    conn.commit()


@contextmanager
def open_db(db_path: str | Path, timeout: float = 10.0) -> Iterator[sqlite3.Connection]:
    """Single entry point for opening SQLite connections: `with conn:` semantics plus a guaranteed close().

    Invariant against file descriptor exhaustion (Errno 24 EMFILE):
    sqlite3.Connection.__exit__ only commits/rolls back the transaction,
    it does NOT close the connection — a bare `with sqlite3.connect(...) as conn:`
    keeps the FDs (db + wal) open for the whole process lifetime.
    """
    conn = sqlite3.connect(db_path, timeout=timeout)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize_database(db_path: str) -> str:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # WAL allows concurrent reads and short writes; `busy_timeout` does not replace closing.
    # `sqlite3.Connection.__exit__` commits/rolls back but does not close the connection.
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        ensure_schema(conn)
    return str(path)


def db_tables_check(conn: sqlite3.Connection) -> dict[str, bool]:
    cursor = conn.cursor()
    checks = {}
    for table_name in [
        "tasks",
        "events",
        "settings",
        "schedules",
        "skills_state",
        "autonomous_actions",
        "security_policies",
        "security_approvals",
        "security_events",
        "user_input_requests",
        "session_steer_messages",
    ]:
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        checks[table_name] = cursor.fetchone() is not None
    return checks
