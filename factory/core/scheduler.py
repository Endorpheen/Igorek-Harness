#!/usr/bin/env python3
"""
Scheduler Module for AgentFactory
Handles scheduled task execution using built-in Python modules only.
No external dependencies required.

Supports:
- Daily schedules at specific time (HH:MM format)
- Custom timezone support
"""

import sqlite3
import json
import uuid
import time
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Dict, Any, List
from pathlib import Path

try:
    from .runtime import get_data_root, get_db_path
    from .schema import initialize_database, open_db
except ImportError:  # pragma: no cover - direct script fallback
    from runtime import get_data_root, get_db_path
    from schema import initialize_database, open_db

FACTORY_DIR = Path(get_data_root())
DB_PATH = get_db_path()
initialize_database(DB_PATH)


class Schedule:
    """Represents a single schedule configuration."""

    def __init__(self, data: Dict[str, Any]):
        self.schedule_id = data.get('schedule_id')
        self.name = data.get('name')
        self.agent_id = data.get('agent_id')
        self.payload = data.get('payload')
        self.schedule_type = data.get('schedule_type')  # 'daily', 'cron'
        self.cron_expression = data.get('cron_expression')
        self.time_value = data.get('time_value')  # HH:MM for daily
        self.tz = data.get('timezone', 'UTC')
        self.enabled = bool(data.get('enabled', 1))
        self.created_at = data.get('created_at')
        self.updated_at = data.get('updated_at')
        self.last_run_at = data.get('last_run_at')
        self.next_run_at = data.get('next_run_at')
        self.run_count = data.get('run_count', 0)
        self.metadata = data.get('metadata', '{}')
        self.last_output = data.get('last_output')

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'schedule_id': self.schedule_id,
            'name': self.name,
            'agent_id': self.agent_id,
            'payload': self.payload,
            'schedule_type': self.schedule_type,
            'cron_expression': self.cron_expression,
            'time_value': self.time_value,
            'timezone': self.tz,
            'enabled': self.enabled,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'last_run_at': self.last_run_at,
            'next_run_at': self.next_run_at,
            'run_count': self.run_count,
            'metadata': json.loads(self.metadata) if isinstance(self.metadata, str) else self.metadata,
            'last_output': self.last_output,
        }

    def calculate_next_run(self) -> Optional[int]:
        """Calculate the next run timestamp based on schedule configuration."""
        if not self.enabled:
            return None

        now = datetime.now(timezone.utc)

        if self.schedule_type == 'hourly' and self.time_value:
            try:
                from zoneinfo import ZoneInfo
                hour, minute = map(int, self.time_value.split(':'))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    return None
                metadata = json.loads(self.metadata) if isinstance(self.metadata, str) else self.metadata
                hours = metadata.get('interval_hours', 1) if isinstance(metadata, dict) else 1
                if type(hours) is not int or not 1 <= hours <= 8760:
                    return None
                if hours > 1:
                    anchor = metadata.get('interval_anchor_at')
                    if anchor is None:
                        base = datetime.fromtimestamp(self.created_at or now.timestamp(), timezone.utc)
                        start = base.astimezone(ZoneInfo(self.tz)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                        if start <= base:
                            start += timedelta(days=1)
                        anchor = int(start.timestamp())
                    if type(anchor) is not int or anchor <= 0:
                        return None
                    current = int(now.timestamp())
                    period = hours * 3600
                    return anchor if anchor > current else anchor + ((current - anchor) // period + 1) * period
                # Compare absolute instants to handle repeated/skipped DST hours.
                candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
                for _ in range(180):
                    if candidate.astimezone(ZoneInfo(self.tz)).minute == minute:
                        return int(candidate.timestamp())
                    candidate += timedelta(minutes=1)
                return None
            except (ValueError, KeyError):
                return None

        if self.schedule_type == 'daily' and self.time_value:
            # Parse time value (HH:MM format)
            try:
                hour, minute = map(int, self.time_value.split(':'))

                # Get current time in the schedule's timezone
                if self.tz != 'UTC':
                    try:
                        import zoneinfo
                        tz = zoneinfo.ZoneInfo(self.tz)
                    except ImportError:
                        # Fallback for older Python versions
                        # Simple UTC offset handling
                        tz = timezone.utc
                else:
                    tz = timezone.utc

                now_tz = datetime.now(tz)
                next_run_tz = now_tz.replace(hour=hour, minute=minute, second=0, microsecond=0)

                # If the time has passed today, schedule for tomorrow
                if next_run_tz <= now_tz:
                    next_run_tz += timedelta(days=1)

                # Convert back to UTC timestamp
                return int(next_run_tz.timestamp())
            except (ValueError, IndexError):
                return None

        elif self.schedule_type == 'once' and self.time_value:
            # One-shot schedule: runs once at specified time, then never again
            try:
                hour, minute = map(int, self.time_value.split(':'))

                if self.tz != 'UTC':
                    try:
                        import zoneinfo
                        tz = zoneinfo.ZoneInfo(self.tz)
                    except ImportError:
                        tz = timezone.utc
                else:
                    tz = timezone.utc

                now_tz = datetime.now(tz)
                next_run_tz = now_tz.replace(hour=hour, minute=minute, second=0, microsecond=0)

                # If time has passed, don't reschedule
                if next_run_tz <= now_tz:
                    return None

                return int(next_run_tz.timestamp())
            except (ValueError, IndexError):
                return None

        elif self.schedule_type == 'cron' and self.cron_expression:
            # Simple cron parsing (supports basic patterns: 0 8 * * * for daily at 8am)
            # Format: minute hour day month dow
            try:
                parts = self.cron_expression.strip().split()
                if len(parts) != 5:
                    return None

                cron_min, cron_hour, cron_day, cron_month, cron_dow = [p.strip() for p in parts]

                # Support only "*" or specific numbers (simplified)
                if cron_min == '*':
                    minute = 0
                else:
                    minute = int(cron_min)

                if cron_hour == '*':
                    hour = datetime.now().hour
                else:
                    hour = int(cron_hour)

                # For simplicity, only handle daily-like cron schedules
                # More complex cron would require a full cron parser library
                now_tz = datetime.now(timezone.utc)
                next_run = now_tz.replace(hour=hour, minute=minute, second=0, microsecond=0)

                if next_run <= now_tz:
                    next_run += timedelta(days=1)

                return int(next_run.timestamp())
            except (ValueError, IndexError):
                return None

        return None


class TaskScheduler:
    """
    Task scheduler for AgentFactory.
    Manages scheduled tasks and triggers execution at configured times.
    Uses only built-in Python modules - no external dependencies.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.running = False
        self.check_interval = 30  # Check every 30 seconds
        self._thread: Optional[threading.Thread] = None
        self._task_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._lock = threading.Lock()
        self._readiness_check = None

    def set_readiness_check(self, check):
        self._readiness_check = check

    def set_task_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Set callback function for executing due schedules.
        Callback receives a dict with: schedule_id, agent_id, payload, name, metadata.
        The callback is expected to handle execution (e.g. via GatewayRuntime).
        """
        with self._lock:
            self._task_callback = callback

    def _ensure_db_schema(self, conn: sqlite3.Connection):
        """Ensure schedules table exists."""
        cur = conn.cursor()
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='schedules'
        """)
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE schedules (
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
                    metadata TEXT,
                    last_output TEXT
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_schedules_enabled
                ON schedules(enabled, next_run_at)
            """)
            conn.commit()
        # Add last_output column to existing tables (must run outside the create-if-missing block)
        try:
            cur.execute("ALTER TABLE schedules ADD COLUMN last_output TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    def create_schedule(self, name: str, agent_id: str, payload: str,
                       schedule_type: str = 'daily', time_value: str = None,
                       cron_expression: str = None, tz: str = 'UTC',
                       enabled: bool = True, metadata: Dict = None) -> str:
        """
        Create a new schedule.

        Args:
            name: Schedule name
            agent_id: Agent profile ID to use (e.g., 'zai', 'minimax')
            payload: Task text/payload to execute
            schedule_type: 'daily' or 'cron'
            time_value: Time in HH:MM format (for daily)
            cron_expression: Cron expression (for cron)
            tz: Timezone (default: UTC)
            enabled: Whether schedule is active
            metadata: Additional metadata as dict

        Returns:
            schedule_id: UUID of created schedule
        """
        schedule_id = str(uuid.uuid4())
        current_time = int(time.time())

        metadata_json = json.dumps(metadata or {})

        with open_db(self.db_path) as conn:
            self._ensure_db_schema(conn)
            cursor = conn.cursor()

            # Calculate next run time
            temp_schedule = Schedule({
                'schedule_id': schedule_id,
                'name': name,
                'agent_id': agent_id,
                'payload': payload,
                'schedule_type': schedule_type,
                'time_value': time_value,
                'cron_expression': cron_expression,
                'timezone': tz,
                'enabled': enabled,
                'created_at': current_time,
                'metadata': metadata or {},
            })
            next_run_at = temp_schedule.calculate_next_run()

            cursor.execute("""
                INSERT INTO schedules (
                    schedule_id, name, agent_id, payload, schedule_type,
                    cron_expression, time_value, timezone, enabled,
                    created_at, updated_at, next_run_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                schedule_id, name, agent_id, payload, schedule_type,
                cron_expression, time_value, tz, 1 if enabled else 0,
                current_time, current_time, next_run_at, metadata_json
            ))
            conn.commit()

        return schedule_id

    def get_schedule(self, schedule_id: str) -> Optional[Schedule]:
        """Get a schedule by ID."""
        with open_db(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            self._ensure_db_schema(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM schedules WHERE schedule_id = ?", (schedule_id,))
            row = cursor.fetchone()
            if row:
                return Schedule(dict(row))
        return None

    def list_schedules(self, enabled_only: bool = False) -> list[Schedule]:
        """List all schedules."""
        with open_db(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            self._ensure_db_schema(conn)
            cursor = conn.cursor()

            if enabled_only:
                cursor.execute("""
                    SELECT * FROM schedules WHERE enabled = 1
                    ORDER BY next_run_at ASC
                """)
            else:
                cursor.execute("""
                    SELECT * FROM schedules ORDER BY created_at DESC
                """)

            return [Schedule(dict(row)) for row in cursor.fetchall()]

    def update_schedule(self, schedule_id: str, **kwargs) -> bool:
        """Update schedule properties."""
        allowed_fields = {
            'name', 'agent_id', 'payload', 'schedule_type',
            'cron_expression', 'time_value', 'timezone', 'enabled', 'metadata'
        }

        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False

        # Convert boolean enabled to integer
        if 'enabled' in updates and isinstance(updates['enabled'], bool):
            updates['enabled'] = 1 if updates['enabled'] else 0

        # Convert metadata dict to JSON
        if 'metadata' in updates and isinstance(updates['metadata'], dict):
            updates['metadata'] = json.dumps(updates['metadata'])

        # Build SET clause
        set_clause = ', '.join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values())
        values.append(int(time.time()))  # updated_at
        values.append(schedule_id)

        with open_db(self.db_path) as conn:
            self._ensure_db_schema(conn)
            cursor = conn.cursor()

            # Recalculate next_run_at if schedule fields changed
            schedule_fields = {'schedule_type', 'time_value', 'cron_expression', 'timezone', 'enabled'}
            if schedule_fields & updates.keys():
                cursor.execute(f"UPDATE schedules SET {set_clause}, updated_at = ? WHERE schedule_id = ?", values)
                # Recalculate next run
                cursor.execute("SELECT * FROM schedules WHERE schedule_id = ?", (schedule_id,))
                row = cursor.fetchone()
                if row:
                    temp_schedule = Schedule({
                        'schedule_id': schedule_id,
                        'name': row[1],
                        'agent_id': row[2],
                        'payload': row[3],
                        'schedule_type': row[4],
                        'cron_expression': row[5],
                        'time_value': row[6],
                        'timezone': row[7],
                        'enabled': row[8],
                        'created_at': row[9],
                        'metadata': row[14],
                    })
                    next_run = temp_schedule.calculate_next_run()
                    cursor.execute("UPDATE schedules SET next_run_at = ? WHERE schedule_id = ?", (next_run, schedule_id))
            else:
                cursor.execute(f"UPDATE schedules SET {set_clause}, updated_at = ? WHERE schedule_id = ?", values)

            conn.commit()
            return cursor.rowcount > 0

        return False

    def delete_schedule(self, schedule_id: str) -> bool:
        """Delete a schedule."""
        with open_db(self.db_path) as conn:
            self._ensure_db_schema(conn)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM schedules WHERE schedule_id = ?", (schedule_id,))
            conn.commit()
            return cursor.rowcount > 0

    def run_schedule_now(self, schedule_id: str) -> bool:
        """Manually trigger a schedule to run immediately."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return False

        if self._task_callback:
            metadata: Dict[str, Any] = {}
            if schedule.metadata:
                try:
                    metadata = json.loads(schedule.metadata) if isinstance(schedule.metadata, str) else (schedule.metadata or {})
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            schedule_info: Dict[str, Any] = {
                'schedule_id': schedule.schedule_id,
                'agent_id': schedule.agent_id,
                'payload': schedule.payload,
                'name': schedule.name,
                'metadata': metadata,
                'run_id': uuid.uuid4().hex,
            }
            if self._readiness_check is not None and not self._readiness_check(schedule_info):
                return False
            self._task_callback(schedule_info)

            # Update last_run_at and run_count
            with open_db(self.db_path) as conn:
                self._ensure_db_schema(conn)
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE schedules
                    SET last_run_at = ?, run_count = run_count + 1, next_run_at = ?
                    WHERE schedule_id = ?
                """, (int(time.time()), None if schedule.schedule_type == 'once' else schedule.calculate_next_run(), schedule_id))
                conn.commit()
            return True

        return False

    def _check_and_run_schedules(self):
        """Check for due schedules and run them via the registered callback."""
        if not self._task_callback:
            return

        current_time = int(time.time())

        # 1. Fetch due schedules without holding connection during execution
        with open_db(self.db_path) as conn:
            self._ensure_db_schema(conn)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT schedule_id, agent_id, payload, name, metadata, next_run_at
                FROM schedules
                WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            """, (current_time,))
            due_schedules: List[tuple] = cursor.fetchall()

        # 2. Execute each due schedule (callback may block for minutes)
        for schedule_id, agent_id, payload, name, metadata_raw, due_at in due_schedules:
            try:
                current = self.get_schedule(schedule_id)
                if current is None or not current.enabled or current.next_run_at != due_at:
                    continue
                agent_id, payload, name, metadata_raw = current.agent_id, current.payload, current.name, current.metadata
                metadata: Dict[str, Any] = {}
                if metadata_raw:
                    try:
                        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else (metadata_raw or {})
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}

                schedule_info: Dict[str, Any] = {
                    'schedule_id': schedule_id,
                    'agent_id': agent_id,
                    'payload': payload,
                    'name': name,
                    'metadata': metadata,
                    'run_id': str(due_at),
                    'due_at': due_at,
                }

                # Execute via callback (GatewayRuntime-backed)
                if self._readiness_check is not None and not self._readiness_check(schedule_info):
                    continue
                self._task_callback(schedule_info)

            except Exception as e:
                print(f"Error running schedule {schedule_id}: {e}")
                if self._readiness_check is not None:
                    continue

            # 3. Update schedule timing in separate connection
            try:
                schedule = self.get_schedule(schedule_id)
                if schedule:
                    next_run = schedule.calculate_next_run()
                    with open_db(self.db_path) as conn:
                        self._ensure_db_schema(conn)
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE schedules
                            SET last_run_at = ?, next_run_at = ?, run_count = run_count + 1
                            WHERE schedule_id = ?
                        """, (current_time, next_run, schedule_id))
                        conn.commit()
            except Exception as e:
                print(f"Error updating schedule {schedule_id} after execution: {e}")

    def _run_loop(self):
        """Main scheduler loop."""
        print(f"Scheduler started, checking every {self.check_interval} seconds")
        while self.running:
            try:
                self._check_and_run_schedules()
            except Exception as e:
                print(f"Scheduler error: {e}")

            # Sleep in small intervals to allow for quick shutdown
            for _ in range(self.check_interval):
                if not self.running:
                    break
                time.sleep(1)

    def start(self):
        """Start the scheduler in a background thread."""
        with self._lock:
            if self.running:
                return

            self.running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            print("Scheduler thread started")

    def stop(self):
        """Stop the scheduler."""
        with self._lock:
            self.running = False
            if self._thread:
                self._thread.join(timeout=5)
                self._thread = None
            print("Scheduler stopped")


# Global scheduler instance
_scheduler: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler


def initialize_scheduler(task_callback: Callable[[Dict[str, Any]], None]) -> TaskScheduler:
    """
    Initialize the global scheduler with a task callback.
    The callback receives a dict with: schedule_id, agent_id, payload, name, metadata.
    The callback is expected to handle execution (e.g. via GatewayRuntime).
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    _scheduler.set_task_callback(task_callback)
    return _scheduler
