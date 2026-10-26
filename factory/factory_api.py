#!/usr/bin/env python3
"""
Factory API - REST API for Agent Factory
Provides HTTP endpoints to create and monitor tasks
"""

import json
import sqlite3
import uuid
import time
import argparse
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os
import glob
from pathlib import Path
import sys

# Import scheduler module
sys.path.insert(0, str(Path(__file__).parent))
from core.scheduler import get_scheduler, initialize_scheduler
from core.boltbook_heartbeat import BoltbookHeartbeatLoop
from core.watchdog import TaskWatchdog
from core.logger import get_logger
from core.runtime import get_data_root, get_db_path, get_workspace_root, get_profiles_dir, get_runtime_layout
from core.schema import ensure_schema, db_tables_check as shared_db_tables_check, initialize_database, open_db
from core.security_store import SecurityStore
from core.user_input_store import (
    get_user_input_request,
    list_pending_user_input_requests,
    resolve_user_input_request,
)
from core.codex_app_server_runtime import CodexAppServerRuntime
from core.artifact_files import open_workspace_artifact
from core.http_server import BoundedHTTPServer, HeaderDeadlineMixin

# Import skills manager
try:
    from core.skills_manager import skills_manager
    SKILLS_AVAILABLE = True
except ImportError:
    SKILLS_AVAILABLE = False
    print("[FactoryAPI] Skills manager not available")

# Resolve runtime paths. Installed uv-tool uses ~/.igorek-agent-factory, repo checkout keeps repo-local paths.
FACTORY_DIR = Path(get_data_root())
DB_PATH = get_db_path()
WORKSPACE_ROOT = get_workspace_root()
PROFILES_DIR = get_profiles_dir()
RUNTIME_LAYOUT = get_runtime_layout()
initialize_database(DB_PATH)
API_KEY = os.environ.get("FACTORY_API_KEY", "")
CORS_ORIGINS = [origin.strip() for origin in os.environ.get("FACTORY_CORS_ORIGINS", "").split(",") if origin.strip()]
MODE = os.environ.get("MODE", "local").strip().lower()
WATCHDOG_INTERVAL_S = int(os.environ.get("FACTORY_WATCHDOG_INTERVAL_S", "30"))
STUCK_TIMEOUT_S = int(os.environ.get("FACTORY_STUCK_TIMEOUT_S", "600"))
BOLTBOOK_HEARTBEAT_ENABLED = os.environ.get("FACTORY_BOLTBOOK_HEARTBEAT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
BOLTBOOK_HEARTBEAT_LOOP_INTERVAL_S = int(os.environ.get("FACTORY_BOLTBOOK_HEARTBEAT_LOOP_INTERVAL_S", "60"))
BOLTBOOK_HEARTBEAT_INTERVAL_S = int(os.environ.get("FACTORY_BOLTBOOK_HEARTBEAT_INTERVAL_S", str(20 * 60)))
BOLTBOOK_SKILL_REFRESH_INTERVAL_S = int(os.environ.get("FACTORY_BOLTBOOK_SKILL_REFRESH_INTERVAL_S", str(24 * 60 * 60)))
BOLTBOOK_HEARTBEAT_FEED_LIMIT = int(os.environ.get("FACTORY_BOLTBOOK_HEARTBEAT_FEED_LIMIT", "15"))
BOLTBOOK_HEARTBEAT_FEED_SORT = os.environ.get("FACTORY_BOLTBOOK_HEARTBEAT_FEED_SORT", "new").strip() or "new"
BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED = os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
BOLTBOOK_AUTONOMOUS_PROFILE = os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_PROFILE", "codex").strip() or "codex"
BOLTBOOK_AUTONOMOUS_MIN_SCORE = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_MIN_SCORE", "6"))
BOLTBOOK_AUTONOMOUS_TIMEOUT_S = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_TIMEOUT_S", "180"))
BOLTBOOK_AUTONOMOUS_COMMENT_COOLDOWN_S = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_COMMENT_COOLDOWN_S", "20"))
BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_HOUR = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_HOUR", "2"))
BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_DAY = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_DAY", "8"))
BOLTBOOK_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR = int(
    os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR", "1")
)
BOLTBOOK_AUTONOMOUS_POSTS_ENABLED = os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POSTS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
BOLTBOOK_AUTONOMOUS_POST_PROFILE = os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POST_PROFILE", "codex").strip() or "codex"
BOLTBOOK_AUTONOMOUS_POST_SUBMOLT = os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POST_SUBMOLT", "general").strip() or "general"
BOLTBOOK_AUTONOMOUS_POST_TIMEOUT_S = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POST_TIMEOUT_S", "240"))
BOLTBOOK_AUTONOMOUS_POST_COOLDOWN_S = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POST_COOLDOWN_S", str(4 * 60 * 60)))
BOLTBOOK_AUTONOMOUS_POST_WINDOW_S = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POST_WINDOW_S", str(16 * 60 * 60)))
BOLTBOOK_AUTONOMOUS_POST_MAX_PER_WINDOW = int(os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POST_MAX_PER_WINDOW", "2"))
BOLTBOOK_AUTONOMOUS_POLICY = os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POLICY", "").strip().lower() or "custom"
LOGGER = get_logger("factory_api")
SCHEDULER_INITIALIZED = False
WATCHDOG_INSTANCE = None
BOLTBOOK_HEARTBEAT_INSTANCE = None
SECURITY_STORE = SecurityStore(DB_PATH)
APP_SERVER_RUNTIME = CodexAppServerRuntime()


def _apply_boltbook_policy() -> dict[str, int | bool | str]:
    policy_name = BOLTBOOK_AUTONOMOUS_POLICY
    presets: dict[str, dict[str, int | bool | str]] = {
        "conservative": {
            "comments_enabled": True,
            "posts_enabled": True,
            "min_score": 7,
            "comments_per_hour": 1,
            "comments_per_day": 3,
            "own_post_comments_per_hour": 1,
            "post_window_s": 24 * 60 * 60,
            "posts_per_window": 1,
            "post_cooldown_s": 12 * 60 * 60,
        },
        "balanced": {
            "comments_enabled": True,
            "posts_enabled": True,
            "min_score": 6,
            "comments_per_hour": 1,
            "comments_per_day": 6,
            "own_post_comments_per_hour": 1,
            "post_window_s": 24 * 60 * 60,
            "posts_per_window": 2,
            "post_cooldown_s": 6 * 60 * 60,
        },
        "active": {
            "comments_enabled": True,
            "posts_enabled": True,
            "min_score": 5,
            "comments_per_hour": 2,
            "comments_per_day": 10,
            "own_post_comments_per_hour": 2,
            "post_window_s": 24 * 60 * 60,
            "posts_per_window": 3,
            "post_cooldown_s": 4 * 60 * 60,
        },
    }
    if policy_name not in presets:
        return {
            "policy": "custom",
            "comments_enabled": BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED,
            "posts_enabled": BOLTBOOK_AUTONOMOUS_POSTS_ENABLED,
            "min_score": BOLTBOOK_AUTONOMOUS_MIN_SCORE,
            "comments_per_hour": BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_HOUR,
            "comments_per_day": BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_DAY,
            "own_post_comments_per_hour": BOLTBOOK_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR,
            "post_window_s": BOLTBOOK_AUTONOMOUS_POST_WINDOW_S,
            "posts_per_window": BOLTBOOK_AUTONOMOUS_POST_MAX_PER_WINDOW,
            "post_cooldown_s": BOLTBOOK_AUTONOMOUS_POST_COOLDOWN_S,
        }

    preset = presets[policy_name]
    return {
        "policy": policy_name,
        "comments_enabled": BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED if "FACTORY_BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED" in os.environ else bool(preset["comments_enabled"]),
        "posts_enabled": BOLTBOOK_AUTONOMOUS_POSTS_ENABLED if "FACTORY_BOLTBOOK_AUTONOMOUS_POSTS_ENABLED" in os.environ else bool(preset["posts_enabled"]),
        "min_score": BOLTBOOK_AUTONOMOUS_MIN_SCORE if "FACTORY_BOLTBOOK_AUTONOMOUS_MIN_SCORE" in os.environ else int(preset["min_score"]),
        "comments_per_hour": BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_HOUR if "FACTORY_BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_HOUR" in os.environ else int(preset["comments_per_hour"]),
        "comments_per_day": BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_DAY if "FACTORY_BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_DAY" in os.environ else int(preset["comments_per_day"]),
        "own_post_comments_per_hour": (
            BOLTBOOK_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR
            if "FACTORY_BOLTBOOK_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR" in os.environ
            else int(preset["own_post_comments_per_hour"])
        ),
        "post_window_s": BOLTBOOK_AUTONOMOUS_POST_WINDOW_S if "FACTORY_BOLTBOOK_AUTONOMOUS_POST_WINDOW_S" in os.environ else int(preset["post_window_s"]),
        "posts_per_window": BOLTBOOK_AUTONOMOUS_POST_MAX_PER_WINDOW if "FACTORY_BOLTBOOK_AUTONOMOUS_POST_MAX_PER_WINDOW" in os.environ else int(preset["posts_per_window"]),
        "post_cooldown_s": BOLTBOOK_AUTONOMOUS_POST_COOLDOWN_S if "FACTORY_BOLTBOOK_AUTONOMOUS_POST_COOLDOWN_S" in os.environ else int(preset["post_cooldown_s"]),
    }


BOLTBOOK_POLICY_SETTINGS = _apply_boltbook_policy()


def _schema_file_path() -> str:
    return DB_PATH


def _ensure_db_schema(conn: sqlite3.Connection):
    ensure_schema(conn)


def _ensure_tasks_columns(conn: sqlite3.Connection):
    ensure_schema(conn)


def _ensure_events_columns(conn: sqlite3.Connection):
    ensure_schema(conn)


def _db_tables_check(conn: sqlite3.Connection):
    return shared_db_tables_check(conn)

def _deliver_schedule_output(deliver: dict, output_text: str, name: str, schedule_id: str):
    """Deliver schedule execution output to the configured target platform."""
    platform = str(deliver.get('platform') or '').strip().lower()
    chat_id = str(deliver.get('chat_id') or '').strip()
    if not platform or not chat_id or not output_text:
        return

    if platform == 'telegram':
        bot_token = os.environ.get('FACTORY_TELEGRAM_BOT_TOKEN', '').strip()
        if not bot_token:
            print(f"Scheduler: cannot deliver to Telegram — FACTORY_TELEGRAM_BOT_TOKEN not set")
            return
        try:
            from urllib import request as urllib_request
            from urllib import error as urllib_error
            header = f"[Отложенная задача: {name or schedule_id[:8]}]\n\n"
            from factory.core.telegram_bridge import _convert_markdown_to_telegram_html
            raw_text = header + output_text
            text = _convert_markdown_to_telegram_html(raw_text)[:4000]
            if not text.strip():
                import html as _html
                text = _html.escape(raw_text)[:4000]
            payload = json.dumps({
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }).encode("utf-8")
            req = urllib_request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=30) as resp:
                resp.read()
            print(f"Scheduler: delivered output to Telegram chat {chat_id}")
        except Exception as exc:
            print(f"Scheduler: Telegram delivery failed: {exc}")


def _execute_schedule_via_gateway(schedule_info: dict):
    """
    Execute a due schedule through GatewayRuntime.
    Called by scheduler when a schedule is due.

    Instead of creating a task directly in DB, routes execution through
    GatewayRuntime.handle_message() which adds source/session metadata,
    foundation context, prompt assembly, and waits for completion.
    """
    schedule_id = schedule_info.get('schedule_id', '')
    agent_id = schedule_info.get('agent_id', '')
    payload = schedule_info.get('payload', '')
    name = schedule_info.get('name', '')
    schedule_metadata = schedule_info.get('metadata') or {}

    try:
        from core.gateway_runtime import GatewayRuntime, GatewaySource, GatewayMessage

        runtime = GatewayRuntime(profile=agent_id)
        source = GatewaySource(
            platform="cron",
            chat_id=schedule_id,
            user_id="scheduler",
            username="scheduler",
            chat_session=f"cron-{schedule_id}",
        )
        message = GatewayMessage(text=payload)

        result = runtime.handle_message(
            source=source,
            message=message,
            profile=agent_id,
            timeout_s=600,
        )

        # Extract output text
        task = result.task or {}
        output_text = ""
        if isinstance(task, dict):
            output_text = str(
                task.get("output_text")
                or task.get("result_text")
                or task.get("error_message")
                or ""
            )

        # Save at most 10,000 characters of the latest schedule output.
        with open_db(DB_PATH) as conn:
            _ensure_db_schema(conn)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE schedules SET last_output = ? WHERE schedule_id = ?",
                (output_text[:10000], schedule_id),
            )
            conn.commit()

        # Deliver output if target configured (e.g. Telegram chat)
        deliver = schedule_metadata.get('deliver') if isinstance(schedule_metadata, dict) else None
        if isinstance(deliver, dict) and output_text:
            _deliver_schedule_output(deliver, output_text, name, schedule_id)

        LOGGER.info(
            "schedule_executed",
            f"Schedule {name or schedule_id} executed via GatewayRuntime, status={result.status}",
            schedule_id=schedule_id,
            profile=agent_id,
            status=result.status,
            task_id=result.task_id,
        )
    except Exception as e:
        LOGGER.error(
            "schedule_execution_error",
            f"Error executing schedule {name or schedule_id}: {e}",
            schedule_id=schedule_id,
        )


class FactoryAPIHandler(HeaderDeadlineMixin, BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def do_OPTIONS(self):
        if not self._check_cors_origin_or_reject():
            return

        self.send_response(200)
        self._send_cors_headers()
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-API-Key')
        self.end_headers()

    def do_POST(self):
        """Handle POST /task - create new task, POST /schedules - create schedule, POST /schedules/<id>/run - manual run, POST /skills/install - install skill, POST /skills/reload - reload skills"""
        parsed_path = urlparse(self.path)
        request_id = self._request_id()
        if not self._check_cors_origin_or_reject():
            return
        if self._is_mutating_endpoint("POST", parsed_path.path):
            self._log_mutating_request("POST", parsed_path.path, request_id)
        if not self._require_api_key():
            return

        if parsed_path.path == '/task':
            try:
                # Read request body
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))

                # Validate request shapes before creating a queued task.
                if not isinstance(data, dict):
                    self._send_json_response(400, {"error": "Request body must be an object"})
                    return
                if 'text' not in data:
                    self._send_json_response(400, {"error": "Missing 'text' field"})
                    return
                task_text = data['text']
                if not isinstance(task_text, str) or not task_text.strip():
                    self._send_json_response(400, {"error": "'text' must be a non-empty string"})
                    return
                profile = data.get('profile', 'zai')
                profile = 'zai' if profile is None else profile
                capabilities = data.get('capabilities')
                capabilities = [] if capabilities is None else capabilities
                metadata = data.get('metadata')
                metadata = {} if metadata is None else metadata
                if not isinstance(profile, str) or not profile.strip():
                    self._send_json_response(400, {"error": "'profile' must be a non-empty string"})
                    return
                if not isinstance(capabilities, list) or any(not isinstance(item, str) or not item.strip() for item in capabilities):
                    self._send_json_response(400, {"error": "'capabilities' must be an array of non-empty strings"})
                    return
                if not isinstance(metadata, dict):
                    self._send_json_response(400, {"error": "'metadata' must be an object"})
                    return
                profile = profile.strip()

                # Add source info
                metadata['source'] = 'matrix'
                metadata['created_via'] = 'factory_api'

                task_id = str(uuid.uuid4())
                current_time = int(time.time())

                # Insert task into database
                with open_db(DB_PATH) as conn:
                    _ensure_db_schema(conn)
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO tasks (
                            task_id, stage, status, profile, input_text,
                            capabilities, metadata, created_at, updated_at,
                            retry_count, max_retries
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        task_id, 'main', 'queued', profile, task_text,
                        json.dumps(capabilities), json.dumps(metadata),
                        current_time, current_time, 0, 3
                    ))
                    conn.commit()

                self._send_json_response(201, {
                    "task_id": task_id,
                    "status": "queued",
                    "message": "Task created successfully"
                })

            except json.JSONDecodeError:
                self._send_json_response(400, {"error": "Invalid JSON"})
            except Exception as e:
                LOGGER.error("db_error", f"POST /task failed: {e}", request_id=request_id)
                self._send_json_response(500, {"error": f"Internal error: {str(e)}"})
        elif parsed_path.path == '/cleanup':
            try:
                # Read request body
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))

                days_old = data.get('days', 3)  # Default to 3 days

                # Import workspace cleaner
                import sys
                import importlib.util
                
                # Loads the workspace_cleaner module from the core directory.
                cleaner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core', 'workspace_cleaner.py')
                spec = importlib.util.spec_from_file_location("workspace_cleaner", cleaner_path)
                workspace_cleaner_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(workspace_cleaner_module)
                WorkspaceCleaner = workspace_cleaner_module.WorkspaceCleaner

                cleaner = WorkspaceCleaner()
                
                # Get old tasks count before cleanup - expand status filter to include more statuses
                status_filter = ['ok', 'fail', 'dead_letter', 'processing', 'queued', 'ready_for_aggregation']
                old_tasks = cleaner.list_old_tasks(days_old, status_filter)
                initial_count = len(old_tasks)

                # Perform cleanup with expanded status filter
                cleaner.cleanup_old_tasks(days_old, dry_run=False, status_filter=status_filter)
                
                # Calculate results
                final_count = len(cleaner.list_old_tasks(days_old, status_filter))
                deleted_count = initial_count - final_count

                self._send_json_response(200, {
                    "deletedCount": deleted_count,
                    "freedSpace": cleaner.format_size(cleaner.freed_space)
                })

            except json.JSONDecodeError:
                self._send_json_response(400, {"error": "Invalid JSON"})
            except Exception as e:
                LOGGER.error("db_error", f"POST /cleanup failed: {e}", request_id=request_id)
                self._send_json_response(500, {"error": f"Internal error: {str(e)}"})
        elif parsed_path.path == '/skills/install':
            self.install_skill()
        elif parsed_path.path == '/skills/reload':
            self.reload_skills()
        elif parsed_path.path == '/skills/toggle':
            self.toggle_skill()
        elif parsed_path.path == '/schedules':
            self.create_schedule()
        elif parsed_path.path == '/app-server/sessions':
            self.create_app_server_session()
        else:
            path_parts = parsed_path.path.strip('/').split('/')
            if len(path_parts) == 4 and path_parts[0] == 'security' and path_parts[1] == 'approvals' and path_parts[3] == 'resolve':
                self.resolve_security_approval(path_parts[2])
            elif len(path_parts) == 4 and path_parts[0] == 'app-server' and path_parts[1] == 'sessions' and path_parts[3] == 'messages':
                self.send_app_server_message(path_parts[2])
            elif len(path_parts) == 5 and path_parts[0] == 'app-server' and path_parts[1] == 'sessions' and path_parts[3] == 'approvals':
                self.resolve_app_server_approval(path_parts[2], path_parts[4])
            elif len(path_parts) == 3 and path_parts[0] == 'schedules' and path_parts[2] == 'run':
                self.run_schedule(path_parts[1])
            elif len(path_parts) == 4 and path_parts[0] == 'task' and path_parts[2] == 'user-input':
                self.resolve_task_user_input(path_parts[1], path_parts[3])
            else:
                self._send_json_response(404, {"error": "Not found"})

    def do_DELETE(self):
        """Handle DELETE /task/{id} - delete task, DELETE /schedules/{id} - delete schedule"""
        parsed_path = urlparse(self.path)
        request_id = self._request_id()
        if not self._check_cors_origin_or_reject():
            return
        if self._is_mutating_endpoint("DELETE", parsed_path.path):
            self._log_mutating_request("DELETE", parsed_path.path, request_id)
        if not self._require_api_key():
            return

        path_parts = parsed_path.path.strip('/').split('/')

        if len(path_parts) == 2 and path_parts[0] == 'task':
            task_id = path_parts[1]
            self.delete_task(task_id)
        elif len(path_parts) == 3 and path_parts[0] == 'app-server' and path_parts[1] == 'sessions':
            self.delete_app_server_session(path_parts[2])
        elif len(path_parts) == 2 and path_parts[0] == 'schedules':
            schedule_id = path_parts[1]
            self.delete_schedule(schedule_id)
        else:
            self._send_json_response(404, {"error": "Not found"})

    def do_PUT(self):
        """Handle PUT /task/{id}/stop - stop task"""
        parsed_path = urlparse(self.path)
        request_id = self._request_id()
        if not self._check_cors_origin_or_reject():
            return
        if self._is_mutating_endpoint("PUT", parsed_path.path):
            self._log_mutating_request("PUT", parsed_path.path, request_id)
        if not self._require_api_key():
            return

        path_parts = parsed_path.path.strip('/').split('/')

        if len(path_parts) == 3 and path_parts[0] == 'task' and path_parts[2] == 'stop':
            task_id = path_parts[1]
            self.stop_task(task_id)
        else:
            self._send_json_response(404, {"error": "Not found"})

    def do_PATCH(self):
        """Handle PATCH /schedules/{id} - update schedule"""
        parsed_path = urlparse(self.path)
        request_id = self._request_id()
        if not self._check_cors_origin_or_reject():
            return
        if self._is_mutating_endpoint("PATCH", parsed_path.path):
            self._log_mutating_request("PATCH", parsed_path.path, request_id)
        if not self._require_api_key():
            return

        path_parts = parsed_path.path.strip('/').split('/')

        if len(path_parts) == 2 and path_parts[0] == 'schedules':
            schedule_id = path_parts[1]
            self.update_schedule(schedule_id)
        elif len(path_parts) == 3 and path_parts[0] == 'security' and path_parts[1] == 'approvals':
            self.resolve_security_approval(path_parts[2])
        else:
            self._send_json_response(404, {"error": "Not found"})

    def do_GET(self):
        """Handle GET /task/{id} - get task status, GET /tasks - get all tasks, GET /schedules - list schedules, GET /schedules/{id} - get schedule, GET /skills - list skills"""
        if not self._check_cors_origin_or_reject():
            return
        parsed_path = urlparse(self.path)
        path_parts = parsed_path.path.strip('/').split('/')

        if path_parts not in (["health"], ["ready"]) and not self._require_api_key():
            return

        if len(path_parts) >= 3 and path_parts[0] == 'task' and path_parts[2] == 'file':
            # GET /task/{id}/file/{path}
            task_id = path_parts[1]
            file_path = '/'.join(path_parts[3:])
            self.get_task_file(task_id, file_path)
        elif len(path_parts) == 3 and path_parts[0] == 'task' and path_parts[2] == 'user-input':
            self.list_task_user_input(path_parts[1])
        elif len(path_parts) == 2 and path_parts[0] == 'task':
            task_id = path_parts[1]
            self.get_task_status(task_id)
        elif len(path_parts) == 1 and path_parts[0] == 'tasks':
            self.get_all_tasks()
        elif len(path_parts) == 1 and path_parts[0] == 'schedules':
            self.list_schedules()
        elif len(path_parts) == 2 and path_parts[0] == 'schedules':
            schedule_id = path_parts[1]
            self.get_schedule(schedule_id)
        elif len(path_parts) == 1 and path_parts[0] == 'skills':
            self.get_skills()
        elif len(path_parts) == 1 and path_parts[0] == 'health':
            self.health()
        elif len(path_parts) == 1 and path_parts[0] == 'ready':
            self.ready()
        elif len(path_parts) == 2 and path_parts[0] == 'security' and path_parts[1] == 'status':
            self.get_security_status()
        elif len(path_parts) == 2 and path_parts[0] == 'security' and path_parts[1] == 'policies':
            self.get_security_policies()
        elif len(path_parts) == 2 and path_parts[0] == 'security' and path_parts[1] == 'approvals':
            self.get_security_approvals()
        elif len(path_parts) == 3 and path_parts[0] == 'security' and path_parts[1] == 'approvals':
            self.get_security_approval(path_parts[2])
        elif len(path_parts) == 2 and path_parts[0] == 'security' and path_parts[1] == 'events':
            self.get_security_events()
        elif len(path_parts) == 3 and path_parts[0] == 'app-server' and path_parts[1] == 'sessions':
            self.get_app_server_session(path_parts[2])
        elif len(path_parts) == 4 and path_parts[0] == 'app-server' and path_parts[1] == 'sessions' and path_parts[3] == 'events':
            self.get_app_server_events(path_parts[2], parsed_path.query)
        else:
            self._send_json_response(404, {"error": "Not found"})

    def list_task_user_input(self, task_id):
        requests = list_pending_user_input_requests(task_id=task_id)
        self._send_json_response(200, {"requests": requests})

    def resolve_task_user_input(self, task_id, request_id):
        request = get_user_input_request(request_id)
        if request is None or request.get("task_id") != task_id:
            self._send_json_response(404, {"error": "User input request not found"})
            return
        try:
            content_length = int(self.headers.get('Content-Length', '0') or '0')
            post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
            data = json.loads(post_data.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            self._send_json_response(400, {"error": "Invalid JSON"})
            return
        cancelled = bool(data.get("cancelled"))
        response = data.get("response")
        if not isinstance(response, dict):
            response = {"cancelled": cancelled}
        resolved = resolve_user_input_request(
            request_id,
            response=response,
            cancelled=cancelled,
        )
        if resolved is None:
            self._send_json_response(409, {"error": "User input request already resolved"})
            return
        self._send_json_response(200, resolved)

    def create_app_server_session(self):
        try:
            content_length = int(self.headers.get('Content-Length', '0') or '0')
            post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
            data = json.loads(post_data.decode('utf-8') or '{}')
            profile = str(data.get("profile") or "codex").strip() or "codex"
            cwd = data.get("cwd")
            thread_id = data.get("thread_id")
            chat_session = data.get("chat_session")
            provider_override = data.get("provider_override")
            model_override = data.get("model_override")
            payload = APP_SERVER_RUNTIME.start_session(
                profile=profile,
                cwd=str(cwd) if cwd else None,
                thread_id=str(thread_id) if thread_id else None,
                chat_session=str(chat_session) if chat_session else None,
                provider_override=str(provider_override) if provider_override else None,
                model_override=str(model_override) if model_override else None,
            )
            LOGGER.info(
                "app_server_session_started",
                "Managed app-server session started",
                profile=profile,
                session_id=payload.get("session_id"),
                thread_id=payload.get("thread_id"),
                chat_session=chat_session,
            )
            self._send_json_response(201, payload)
        except json.JSONDecodeError:
            self._send_json_response(400, {"error": "Invalid JSON"})
        except ValueError as exc:
            self._send_json_response(400, {"error": str(exc)})
        except Exception as exc:
            LOGGER.error("app_server_session_error", f"POST /app-server/sessions failed: {exc}")
            self._send_json_response(500, {"error": f"Internal error: {exc}"})

    def get_app_server_session(self, session_id: str):
        try:
            payload = APP_SERVER_RUNTIME.get_session(session_id)
            self._send_json_response(200, payload)
        except KeyError:
            self._send_json_response(404, {"error": "Session not found"})
        except Exception as exc:
            LOGGER.error("app_server_session_error", f"GET /app-server/sessions/{session_id} failed: {exc}")
            self._send_json_response(500, {"error": f"Internal error: {exc}"})

    def get_app_server_events(self, session_id: str, query_string: str):
        try:
            query = parse_qs(query_string or "")
            after_seq = int((query.get("after") or ["0"])[0] or 0)
            limit = int((query.get("limit") or ["100"])[0] or 100)
            events = APP_SERVER_RUNTIME.list_events(session_id, after_seq=after_seq, limit=max(1, min(limit, 500)))
            self._send_json_response(200, {"events": events})
        except KeyError:
            self._send_json_response(404, {"error": "Session not found"})
        except Exception as exc:
            LOGGER.error("app_server_session_error", f"GET /app-server/sessions/{session_id}/events failed: {exc}")
            self._send_json_response(500, {"error": f"Internal error: {exc}"})

    def send_app_server_message(self, session_id: str):
        try:
            content_length = int(self.headers.get('Content-Length', '0') or '0')
            post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
            data = json.loads(post_data.decode('utf-8') or '{}')
            text = str(data.get("text") or "").strip()
            if not text:
                self._send_json_response(400, {"error": "Missing text"})
                return
            payload = APP_SERVER_RUNTIME.send_message(session_id, text)
            LOGGER.info(
                "app_server_message_sent",
                "Message sent into managed app-server session",
                session_id=session_id,
                text_preview=text[:120],
            )
            self._send_json_response(202, payload)
        except json.JSONDecodeError:
            self._send_json_response(400, {"error": "Invalid JSON"})
        except KeyError:
            self._send_json_response(404, {"error": "Session not found"})
        except Exception as exc:
            LOGGER.error("app_server_session_error", f"POST /app-server/sessions/{session_id}/messages failed: {exc}")
            self._send_json_response(500, {"error": f"Internal error: {exc}"})

    def resolve_app_server_approval(self, session_id: str, request_id: str):
        try:
            content_length = int(self.headers.get('Content-Length', '0') or '0')
            post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
            data = json.loads(post_data.decode('utf-8') or '{}')
            action = str(data.get("action") or "approve").strip().lower()
            approved = action not in {"reject", "decline", "deny"}
            remember_for_session = bool(data.get("remember_for_session"))
            payload = APP_SERVER_RUNTIME.resolve_request(
                session_id,
                request_id=request_id,
                approved=approved,
                remember_for_session=remember_for_session,
            )
            LOGGER.info(
                "app_server_approval_resolved",
                "Managed app-server approval resolved",
                session_id=session_id,
                request_id=request_id,
                approved=approved,
                remember_for_session=remember_for_session,
                native_policy_type=payload.get("native_policy_type"),
                scope=payload.get("default_scope"),
            )
            self._send_json_response(200, payload)
        except json.JSONDecodeError:
            self._send_json_response(400, {"error": "Invalid JSON"})
        except KeyError:
            self._send_json_response(404, {"error": "Session or request not found"})
        except Exception as exc:
            LOGGER.error("app_server_session_error", f"POST /app-server/sessions/{session_id}/approvals/{request_id} failed: {exc}")
            self._send_json_response(500, {"error": f"Internal error: {exc}"})

    def delete_app_server_session(self, session_id: str):
        try:
            APP_SERVER_RUNTIME.close_session(session_id)
            LOGGER.info(
                "app_server_session_closed",
                "Managed app-server session closed",
                session_id=session_id,
            )
            self._send_json_response(200, {"ok": True})
        except KeyError:
            self._send_json_response(404, {"error": "Session not found"})
        except Exception as exc:
            LOGGER.error("app_server_session_error", f"DELETE /app-server/sessions/{session_id} failed: {exc}")
            self._send_json_response(500, {"error": f"Internal error: {exc}"})

    def get_task_file(self, task_id: str, file_path: str):
        """Get file content from workspace"""
        try:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", task_id):
                raise ValueError("Invalid task id")
            # Remove 'temp/' prefix if present
            if file_path.startswith('temp/'):
                file_path = file_path[5:]
                relative_path = f"temp_execution_{task_id[:8]}/{file_path}"
            else:
                relative_path = f"{task_id}/results/{file_path}"

            # Read and send file content
            with open_workspace_artifact(Path(WORKSPACE_ROOT), relative_path) as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{os.path.basename(file_path)}"')
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(content)

        except FileNotFoundError:
            self._send_json_response(404, {"error": "File not found"})
        except (ValueError, OSError):
            self._send_json_response(400, {"error": "Invalid file path"})
        except Exception as e:
            self._send_json_response(500, {"error": f"Error reading file: {str(e)}"})

    def get_task_status(self, task_id: str):
        """Get task status and info"""
        try:
            with open_db(DB_PATH) as conn:
                _ensure_db_schema(conn)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT status, output_text, metadata, created_at, updated_at,
                           started_at, completed_at, error_message
                    FROM tasks WHERE task_id = ?
                """, (task_id,))

                row = cursor.fetchone()
                if not row:
                    self._send_json_response(404, {"error": "Task not found"})
                    return

                status, output_text, metadata, created_at, updated_at, started_at, completed_at, error_message = row

                # Get workspace files if task is completed
                workspace_files = []
                try:
                    # Check both possible locations:
                    # 1. /workspace/{full_task_id}/results/
                    # 2. /workspace/temp_execution_{task_id[:8]}/
                    task_workspace_full = os.path.join(WORKSPACE_ROOT, task_id)
                    task_workspace_temp = os.path.join(WORKSPACE_ROOT, f"temp_execution_{task_id[:8]}")

                    # Check main workspace folder first
                    if os.path.exists(task_workspace_full):
                        results_dir = os.path.join(task_workspace_full, "results")
                        if os.path.exists(results_dir):
                            for root, dirs, files in os.walk(results_dir):
                                for file in files:
                                    # Skip metadata files
                                    if file in ['copy_metadata.json']:
                                        continue
                                    abs_path = os.path.join(root, file)
                                    # Return relative path from results directory
                                    rel_path = os.path.relpath(abs_path, results_dir)
                                    workspace_files.append(rel_path)

                    # Also check temp execution folder
                    if os.path.exists(task_workspace_temp):
                        for root, dirs, files in os.walk(task_workspace_temp):
                            for file in files:
                                if file in ['input.json', 'result.json', 'copy_metadata.json']:
                                    continue
                                abs_path = os.path.join(root, file)
                                # Return relative path from temp folder
                                rel_path = os.path.relpath(abs_path, task_workspace_temp)
                                workspace_files.append(f"temp/{rel_path}")
                except Exception as e:
                    print(f"Error getting workspace files: {e}")

                response = {
                    "task_id": task_id,
                    "status": status,
                    "result_text": output_text,
                    "workspace_files": workspace_files,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "started_at": started_at,
                    "completed_at": completed_at
                }

                if error_message:
                    response["error_message"] = error_message

                if metadata:
                    try:
                        response["metadata"] = json.loads(metadata)
                    except:
                        response["metadata"] = metadata

                self._send_json_response(200, response)

        except Exception as e:
            LOGGER.error("db_error", f"GET /task/{task_id} failed: {e}")
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def get_all_tasks(self):
        """Get all tasks from database"""
        try:
            with open_db(DB_PATH) as conn:
                _ensure_db_schema(conn)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT task_id, status, profile, input_text, output_text,
                           metadata, created_at, updated_at, started_at,
                           completed_at, error_message
                    FROM tasks
                    ORDER BY created_at DESC
                """)

                rows = cursor.fetchall()
                tasks = []
                
                for row in rows:
                    (task_id, status, profile, input_text, output_text,
                     metadata, created_at, updated_at, started_at,
                     completed_at, error_message) = row
                    
                    # Get workspace files for each task
                    workspace_files = []
                    try:
                        # Check both possible locations
                        task_workspace_full = os.path.join(WORKSPACE_ROOT, task_id)
                        task_workspace_temp = os.path.join(WORKSPACE_ROOT, f"temp_execution_{task_id[:8]}")

                        # Check main workspace folder first
                        if os.path.exists(task_workspace_full):
                            results_dir = os.path.join(task_workspace_full, "results")
                            if os.path.exists(results_dir):
                                for root, dirs, files in os.walk(results_dir):
                                    for file in files:
                                        if file in ['copy_metadata.json']:
                                            continue
                                        abs_path = os.path.join(root, file)
                                        rel_path = os.path.relpath(abs_path, results_dir)
                                        workspace_files.append(rel_path)

                        # Also check temp execution folder
                        if os.path.exists(task_workspace_temp):
                            for root, dirs, files in os.walk(task_workspace_temp):
                                for file in files:
                                    if file in ['input.json', 'result.json', 'copy_metadata.json']:
                                        continue
                                    abs_path = os.path.join(root, file)
                                    rel_path = os.path.relpath(abs_path, task_workspace_temp)
                                    workspace_files.append(f"temp/{rel_path}")
                    except Exception as e:
                        print(f"Error getting workspace files for task {task_id}: {e}")

                    task_data = {
                        "task_id": task_id,
                        "status": status,
                        "profile": profile,
                        "input_text": input_text,
                        "result_text": output_text,
                        "output_text": output_text,
                        "workspace_files": workspace_files,
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "started_at": started_at,
                        "completed_at": completed_at
                    }

                    if error_message:
                        task_data["error_message"] = error_message

                    if metadata:
                        try:
                            task_data["metadata"] = json.loads(metadata)
                        except:
                            task_data["metadata"] = metadata

                    tasks.append(task_data)

                self._send_json_response(200, {"tasks": tasks})

        except Exception as e:
            LOGGER.error("db_error", f"GET /tasks failed: {e}")
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def stop_task(self, task_id: str):
        """Stop a running task"""
        try:
            with open_db(DB_PATH) as conn:
                _ensure_db_schema(conn)
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE tasks SET status = 'dead_letter', updated_at = ?
                    WHERE task_id = ? AND status IN ('queued', 'processing')
                """, (int(time.time()), task_id))
                conn.commit()

                if cursor.rowcount == 0:
                    self._send_json_response(404, {"error": "Task not found or not running"})
                    return

                self._send_json_response(200, {
                    "task_id": task_id,
                    "status": "dead_letter",
                    "message": "Task stopped successfully"
                })

        except Exception as e:
            LOGGER.error("db_error", f"PUT /task/{task_id}/stop failed: {e}", task_id=task_id)
            self._send_json_response(500, {"error": f"Error stopping task: {str(e)}"})

    def delete_task(self, task_id: str):
        """Delete a task from database"""
        try:
            with open_db(DB_PATH) as conn:
                _ensure_db_schema(conn)
                cursor = conn.cursor()
                
                # First, get task info to clean up workspace
                cursor.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,))
                row = cursor.fetchone()
                if not row:
                    self._send_json_response(404, {"error": "Task not found"})
                    return
                
                # Delete workspace files if task is completed
                status = row[0]
                if status in ['ok', 'fail', 'dead_letter']:
                    try:
                        import shutil
                        task_workspace = os.path.join(WORKSPACE_ROOT, task_id)
                        if os.path.exists(task_workspace):
                            shutil.rmtree(task_workspace)
                            print(f"Cleaned up workspace for task {task_id}")
                    except Exception as e:
                        print(f"Error cleaning up workspace for task {task_id}: {e}")

                # Delete task from database
                cursor.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                conn.commit()

                self._send_json_response(200, {
                    "task_id": task_id,
                    "message": "Task deleted successfully"
                })

        except Exception as e:
            LOGGER.error("db_error", f"DELETE /task/{task_id} failed: {e}", task_id=task_id)
            self._send_json_response(500, {"error": f"Error deleting task: {str(e)}"})

    def health(self):
        self._send_json_response(200, {
            "status": "ok",
            "checks": {
                "process": True,
            },
            "ts": int(time.time()),
        })

    def ready(self):
        checks = {
            "db_connect": False,
            "tables": {
                "tasks": False,
                "events": False,
                "settings": False,
            },
            "scheduler_initialized": False,
        }
        runtime = {
            "layout": RUNTIME_LAYOUT,
            "data_root": str(FACTORY_DIR),
            "db_path": DB_PATH,
            "workspace_root": WORKSPACE_ROOT,
            "profiles_dir": PROFILES_DIR,
            "api_port": getattr(self.server, "server_port", None),
        }
        boltbook = {
            "heartbeat_enabled": BOLTBOOK_HEARTBEAT_ENABLED,
            "policy": BOLTBOOK_POLICY_SETTINGS,
        }
        status_code = 503
        status = "error"

        try:
            with open_db(DB_PATH) as conn:
                _ensure_db_schema(conn)
                checks["db_connect"] = True
                checks["tables"] = _db_tables_check(conn)
        except Exception as e:
            LOGGER.error("db_error", f"GET /ready db check failed: {e}")

        checks["scheduler_initialized"] = bool(SCHEDULER_INITIALIZED and get_scheduler().running)

        tables_ok = all(checks["tables"].values())
        if checks["db_connect"] and tables_ok and checks["scheduler_initialized"]:
            status = "ok"
            status_code = 200

        self._send_json_response(status_code, {
            "status": status,
            "checks": checks,
            "runtime": runtime,
            "boltbook": boltbook,
            "ts": int(time.time()),
        })

    def get_security_status(self):
        try:
            SECURITY_STORE.ensure_bootstrap()
            policies = SECURITY_STORE.list_policies()
            pending = SECURITY_STORE.list_approvals(status="pending", limit=20)
            recent = SECURITY_STORE.list_events(limit=20)
            runtime_mode = os.environ.get("FACTORY_SECURITY_RUNTIME_MODE", "disabled").strip().lower()
            active_policy = next((item["name"] for item in policies if item.get("is_active")), "default")
            self._send_json_response(200, {
                "runtime_mode": runtime_mode,
                "policy_count": len(policies),
                "active_policy": active_policy,
                "pending_approvals": len(pending),
                "recent_events": recent[:10],
            })
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def get_security_policies(self):
        try:
            SECURITY_STORE.ensure_bootstrap()
            self._send_json_response(200, {"policies": SECURITY_STORE.list_policies()})
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def get_security_approvals(self):
        try:
            parsed_path = urlparse(self.path)
            query = parse_qs(parsed_path.query)
            status = query.get("status", [None])[0]
            self._send_json_response(200, {"approvals": SECURITY_STORE.list_approvals(status=status, limit=100)})
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def get_security_approval(self, approval_id: str):
        try:
            approval = SECURITY_STORE.get_approval(approval_id)
            if not approval:
                self._send_json_response(404, {"error": "Approval not found"})
                return
            self._send_json_response(200, approval)
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def get_security_events(self):
        try:
            self._send_json_response(200, {"events": SECURITY_STORE.list_events(limit=100)})
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def resolve_security_approval(self, approval_id: str):
        try:
            content_length = int(self.headers.get('Content-Length', '0'))
            post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
            data = json.loads(post_data.decode('utf-8'))
        except json.JSONDecodeError:
            self._send_json_response(400, {"error": "Invalid JSON"})
            return

        status = str(data.get("status", "")).strip().lower()
        if status not in {"approved", "rejected"}:
            self._send_json_response(400, {"error": "Field 'status' must be 'approved' or 'rejected'"})
            return

        resolved_by = str(data.get("resolved_by") or self.headers.get("X-Resolved-By") or "api").strip() or "api"
        resolution_note = str(data.get("resolution_note") or "").strip()

        try:
            approval = SECURITY_STORE.resolve_approval(
                approval_id=approval_id,
                status=status,
                resolved_by=resolved_by,
                resolution_note=resolution_note,
            )
        except KeyError:
            self._send_json_response(404, {"error": "Approval not found"})
            return
        except ValueError as exc:
            message = str(exc)
            if "already resolved" in message:
                self._send_json_response(409, {"error": message})
                return
            self._send_json_response(400, {"error": message})
            return
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})
            return

        self._send_json_response(200, approval)

    # Schedule API methods

    def create_schedule(self):
        """Create a new schedule"""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            # Validate required fields
            required_fields = ['name', 'agent_id', 'payload']
            for field in required_fields:
                if field not in data:
                    self._send_json_response(400, {"error": f"Missing required field: {field}"})
                    return

            name = data['name']
            agent_id = data['agent_id']
            payload = data['payload']
            schedule_type = data.get('schedule_type', 'daily')
            time_value = data.get('time_value')
            cron_expression = data.get('cron_expression')
            tz = data.get('timezone', 'UTC')
            enabled = data.get('enabled', True)
            metadata = data.get('metadata', {})

            # Validate schedule parameters
            if schedule_type == 'daily' and not time_value:
                self._send_json_response(400, {"error": "time_value is required for daily schedules"})
                return
            if schedule_type == 'cron' and not cron_expression:
                self._send_json_response(400, {"error": "cron_expression is required for cron schedules"})
                return

            scheduler = get_scheduler()
            schedule_id = scheduler.create_schedule(
                name=name,
                agent_id=agent_id,
                payload=payload,
                schedule_type=schedule_type,
                time_value=time_value,
                cron_expression=cron_expression,
                tz=tz,
                enabled=enabled,
                metadata=metadata
            )

            schedule = scheduler.get_schedule(schedule_id)
            self._send_json_response(201, {
                "schedule_id": schedule_id,
                "schedule": schedule.to_dict() if schedule else None,
                "message": "Schedule created successfully"
            })

        except json.JSONDecodeError:
            self._send_json_response(400, {"error": "Invalid JSON"})
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def list_schedules(self):
        """List all schedules"""
        try:
            scheduler = get_scheduler()
            schedules = scheduler.list_schedules()
            self._send_json_response(200, {
                "schedules": [s.to_dict() for s in schedules]
            })
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def get_schedule(self, schedule_id: str):
        """Get a specific schedule"""
        try:
            scheduler = get_scheduler()
            schedule = scheduler.get_schedule(schedule_id)
            if not schedule:
                self._send_json_response(404, {"error": "Schedule not found"})
                return
            self._send_json_response(200, schedule.to_dict())
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def update_schedule(self, schedule_id: str):
        """Update a schedule"""
        try:
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            scheduler = get_scheduler()
            success = scheduler.update_schedule(schedule_id, **data)

            if not success:
                self._send_json_response(404, {"error": "Schedule not found or no valid fields to update"})
                return

            schedule = scheduler.get_schedule(schedule_id)
            self._send_json_response(200, {
                "schedule_id": schedule_id,
                "schedule": schedule.to_dict() if schedule else None,
                "message": "Schedule updated successfully"
            })
        except json.JSONDecodeError:
            self._send_json_response(400, {"error": "Invalid JSON"})
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def delete_schedule(self, schedule_id: str):
        """Delete a schedule"""
        try:
            scheduler = get_scheduler()
            success = scheduler.delete_schedule(schedule_id)

            if not success:
                self._send_json_response(404, {"error": "Schedule not found"})
                return

            self._send_json_response(200, {
                "schedule_id": schedule_id,
                "message": "Schedule deleted successfully"
            })
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    def run_schedule(self, schedule_id: str):
        """Manually run a schedule now"""
        try:
            scheduler = get_scheduler()
            success = scheduler.run_schedule_now(schedule_id)

            if not success:
                self._send_json_response(404, {"error": "Schedule not found"})
                return

            self._send_json_response(200, {
                "schedule_id": schedule_id,
                "message": "Schedule executed successfully"
            })
        except Exception as e:
            self._send_json_response(500, {"error": f"Internal error: {str(e)}"})

    # Skills API methods

    def get_skills(self):
        """Get list of all loaded skills (metadata only) with enabled state"""
        try:
            if not SKILLS_AVAILABLE:
                self._send_json_response(503, {
                    "success": False,
                    "error": "Skills manager not available"
                })
                return

            # Loads configurations to retrieve the current state.
            settings = skills_manager.load_settings()

            all_skills = skills_manager.get_metadata()
            active_count = skills_manager.get_active_count()

            response = {
                "success": True,
                "settings": {
                    "skills_path": settings.get('skills_path', skills_manager.skills_path),
                    "skills_auto_load": settings.get('skills_auto_load', 'true') == 'true',
                    "skills_smart_selection": settings.get('skills_smart_selection', 'true') == 'true'
                },
                "loaded": skills_manager.is_loaded,
                "count": len(all_skills),
                "active_count": active_count,
                "skills": all_skills
            }

            self._send_json_response(200, response)

        except Exception as e:
            self._send_json_response(500, {
                "success": False,
                "error": f"Internal error: {str(e)}"
            })

    def install_skill(self):
        """Install a new skill from markdown text"""
        try:
            if not SKILLS_AVAILABLE:
                self._send_json_response(503, {
                    "success": False,
                    "error": "Skills manager not available"
                })
                return

            # Read request body
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            # Validate required fields
            if 'name' not in data:
                self._send_json_response(400, {
                    "success": False,
                    "error": "Missing 'name' field"
                })
                return

            if 'content' not in data:
                self._send_json_response(400, {
                    "success": False,
                    "error": "Missing 'content' field"
                })
                return

            name = data['name']
            content = data['content']
            overwrite = data.get('overwrite', False)

            # Validates the markdown content.
            if not content.strip():
                self._send_json_response(400, {
                    "success": False,
                    "error": "Content cannot be empty"
                })
                return

            # Installing skill
            success, result = skills_manager.install_skill_from_markdown(name, content, overwrite)

            if not success:
                # Check if this is an error for 'file exists' (HTTP 409)
                if "already exists" in result:
                    self._send_json_response(409, {
                        "success": False,
                        "error": result
                    })
                else:
                    self._send_json_response(400, {
                        "success": False,
                        "error": result
                    })
                return

            # Restart skills after installation
            skills_manager.load_settings()
            reload_success = skills_manager.load_skills()

            self._send_json_response(201, {
                "success": True,
                "file_path": result,
                "loaded": reload_success,
                "count": len(skills_manager.skills),
                "message": f"Skill '{name}' installed successfully and reloaded"
            })

        except json.JSONDecodeError:
            self._send_json_response(400, {
                "success": False,
                "error": "Invalid JSON"
            })
        except Exception as e:
            self._send_json_response(500, {
                "success": False,
                "error": f"Internal error: {str(e)}"
            })

    def reload_skills(self):
        """Reload all skills from disk"""
        try:
            if not SKILLS_AVAILABLE:
                self._send_json_response(503, {
                    "success": False,
                    "error": "Skills manager not available"
                })
                return

            # Restart configurations and skills
            skills_manager.load_settings()
            success = skills_manager.load_skills()

            if success:
                self._send_json_response(200, {
                    "success": True,
                    "loaded": True,
                    "count": len(skills_manager.all_skills),
                    "active_count": skills_manager.get_active_count(),
                    "message": f"Successfully reloaded {len(skills_manager.all_skills)} skills ({skills_manager.get_active_count()} enabled)"
                })
            else:
                self._send_json_response(500, {
                    "success": False,
                    "loaded": False,
                    "count": 0,
                    "active_count": 0,
                    "error": "Failed to reload skills. Check if skills_path exists."
                })

        except Exception as e:
            self._send_json_response(500, {
                "success": False,
                "error": f"Internal error: {str(e)}"
            })

    def toggle_skill(self):
        """Toggle skill enabled/disabled state"""
        try:
            if not SKILLS_AVAILABLE:
                self._send_json_response(503, {
                    "success": False,
                    "error": "Skills manager not available"
                })
                return

            # Read request body
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            # Validate required fields
            if 'enabled' not in data:
                self._send_json_response(400, {
                    "success": False,
                    "error": "Missing 'enabled' field"
                })
                return

            enabled = data['enabled']

            # Check the enabled type
            if not isinstance(enabled, bool):
                self._send_json_response(400, {
                    "success": False,
                    "error": "'enabled' must be a boolean"
                })
                return

            # Search for skill - first by skill_id, then by name
            skill_id = data.get('skill_id')
            skill_name = data.get('name')

            if not skill_id and not skill_name:
                self._send_json_response(400, {
                    "success": False,
                    "error": "Missing 'skill_id' or 'name' field"
                })
                return

            # Sanitize skill_id if provided
            if skill_id:
                safe_id = self._sanitize_skill_id(skill_id)
                if not safe_id:
                    self._send_json_response(400, {
                        "success": False,
                        "error": "Invalid skill_id"
                    })
                    return
                skill_id = safe_id

            # Locate skill
            target_skill = None
            if skill_id:
                target_skill = skills_manager.all_skills.get(skill_id)
            elif skill_name:
                target_skill = skills_manager.find_skill_by_name(skill_name)
                if target_skill:
                    skill_id = target_skill.skill_id

            if not target_skill:
                self._send_json_response(404, {
                    "success": False,
                    "error": f"Skill not found"
                })
                return

            # Set state
            success, message = skills_manager.set_skill_enabled(target_skill.skill_id, enabled)

            if not success:
                self._send_json_response(500, {
                    "success": False,
                    "error": message
                })
                return

            self._send_json_response(200, {
                "success": True,
                "skill_id": target_skill.skill_id,
                "enabled": enabled,
                "count": len(skills_manager.all_skills),
                "active_count": skills_manager.get_active_count(),
                "message": message
            })

        except json.JSONDecodeError:
            self._send_json_response(400, {
                "success": False,
                "error": "Invalid JSON"
            })
        except Exception as e:
            self._send_json_response(500, {
                "success": False,
                "error": f"Internal error: {str(e)}"
            })

    def _sanitize_skill_id(self, skill_id: str) -> str:
        """Sanitize skill_id to prevent path traversal"""
        if not skill_id:
            return ""

        # Remove traversal path
        if '..' in skill_id or '/' in skill_id or '\\' in skill_id:
            return ""

        # Remove `.md` extension if present
        skill_id = skill_id.replace('.md', '')

        # Truncate length
        if len(skill_id) > 100 or len(skill_id) < 1:
            return ""

        return skill_id

    def _send_json_response(self, code, data):
        """Send JSON response with proper headers"""
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._send_cors_headers()
        self.end_headers()

        response_json = json.dumps(data, ensure_ascii=False, indent=2)
        self.wfile.write(response_json.encode('utf-8'))

    def _request_id(self) -> str:
        return self.headers.get("X-Request-Id") or str(uuid.uuid4())

    def _log_mutating_request(self, method: str, path: str, request_id: str):
        LOGGER.info(
            "mutating_request",
            f"{method} {path}",
            request_id=request_id,
        )

    def _is_mutating_endpoint(self, method: str, path: str) -> bool:
        if method == "POST":
            if path in ["/task", "/cleanup", "/schedules", "/skills/install", "/skills/reload", "/skills/toggle", "/app-server/sessions"]:
                return True
            path_parts = path.strip("/").split("/")
            if len(path_parts) == 3 and path_parts[0] == "schedules" and path_parts[2] == "run":
                return True
            if len(path_parts) == 3 and path_parts[0] == "task" and path_parts[2] == "stop":
                return True
            if len(path_parts) == 4 and path_parts[0] == "task" and path_parts[2] == "user-input":
                return True
            if len(path_parts) == 4 and path_parts[0] == "app-server" and path_parts[1] == "sessions" and path_parts[3] == "messages":
                return True
            if len(path_parts) == 5 and path_parts[0] == "app-server" and path_parts[1] == "sessions" and path_parts[3] == "approvals":
                return True
            if len(path_parts) == 4 and path_parts[0] == "security" and path_parts[1] == "approvals" and path_parts[3] == "resolve":
                return True
            return False

        if method == "PATCH":
            path_parts = path.strip("/").split("/")
            if len(path_parts) == 2 and path_parts[0] == "schedules":
                return True
            if len(path_parts) == 3 and path_parts[0] == "security" and path_parts[1] == "approvals":
                return True
            return False

        if method == "DELETE":
            path_parts = path.strip("/").split("/")
            if len(path_parts) == 2 and path_parts[0] == "task":
                return True
            if len(path_parts) == 3 and path_parts[0] == "app-server" and path_parts[1] == "sessions":
                return True
            if len(path_parts) == 2 and path_parts[0] == "schedules":
                return True
            return False

        if method == "PUT":
            path_parts = path.strip("/").split("/")
            return len(path_parts) == 3 and path_parts[0] == "task" and path_parts[2] == "stop"

        return False

    def _require_api_key(self) -> bool:
        if not API_KEY:
            LOGGER.error("auth_error", "FACTORY_API_KEY is not configured")
            self._send_json_response(503, {"error": "FACTORY_API_KEY is not configured"})
            return False

        provided_key = self.headers.get("X-API-Key", "")
        if provided_key != API_KEY:
            LOGGER.warn("auth_error", "Invalid API key")
            self._send_json_response(401, {"error": "Invalid API key"})
            return False
        return True

    def _is_cors_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if not CORS_ORIGINS:
            return False
        return origin in CORS_ORIGINS

    def _send_cors_headers(self):
        origin = self.headers.get("Origin")
        self.send_header("Vary", "Origin")
        if origin and origin in CORS_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)

    def _check_cors_origin_or_reject(self) -> bool:
        if self._is_cors_origin_allowed():
            return True
        self._send_json_response(403, {"error": "Origin not allowed"})
        return False

    def log_message(self, format, *args):
        """Override to reduce log spam"""
        return

def main():
    global SCHEDULER_INITIALIZED
    global WATCHDOG_INSTANCE
    global BOLTBOOK_HEARTBEAT_INSTANCE
    parser = argparse.ArgumentParser(description='Factory API Server')
    parser.add_argument('--host', default=None, help='Host to bind to (MODE-based default if omitted)')
    parser.add_argument('--port', type=int, default=8180, help='Port to run on (default: 8180)')
    args = parser.parse_args()

    if args.host:
        bind_host = args.host
    else:
        bind_host = '0.0.0.0' if MODE == 'lan' else '127.0.0.1'

    # Ensure database exists and schema is applied (even if the DB file already exists)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open_db(DB_PATH) as conn:
        _ensure_db_schema(conn)

    # Initialize and start the scheduler
    initialize_scheduler(_execute_schedule_via_gateway)
    scheduler = get_scheduler()
    scheduler.start()
    SCHEDULER_INITIALIZED = True

    WATCHDOG_INSTANCE = TaskWatchdog(
        db_path=DB_PATH,
        ensure_schema=_ensure_db_schema,
        interval_s=WATCHDOG_INTERVAL_S,
        stuck_timeout_s=STUCK_TIMEOUT_S,
    )
    WATCHDOG_INSTANCE.start()

    if BOLTBOOK_HEARTBEAT_ENABLED:
        BOLTBOOK_HEARTBEAT_INSTANCE = BoltbookHeartbeatLoop(
            loop_interval_s=BOLTBOOK_HEARTBEAT_LOOP_INTERVAL_S,
            heartbeat_interval_s=BOLTBOOK_HEARTBEAT_INTERVAL_S,
            skill_refresh_interval_s=BOLTBOOK_SKILL_REFRESH_INTERVAL_S,
            feed_limit=BOLTBOOK_HEARTBEAT_FEED_LIMIT,
            feed_sort=BOLTBOOK_HEARTBEAT_FEED_SORT,
            autonomous_comments_enabled=bool(BOLTBOOK_POLICY_SETTINGS["comments_enabled"]),
            autonomous_profile=BOLTBOOK_AUTONOMOUS_PROFILE,
            autonomous_min_score=int(BOLTBOOK_POLICY_SETTINGS["min_score"]),
            autonomous_timeout_s=BOLTBOOK_AUTONOMOUS_TIMEOUT_S,
            autonomous_comment_cooldown_s=BOLTBOOK_AUTONOMOUS_COMMENT_COOLDOWN_S,
            autonomous_max_comments_per_hour=int(BOLTBOOK_POLICY_SETTINGS["comments_per_hour"]),
            autonomous_max_comments_per_day=int(BOLTBOOK_POLICY_SETTINGS["comments_per_day"]),
            autonomous_own_post_max_comments_per_hour=int(BOLTBOOK_POLICY_SETTINGS["own_post_comments_per_hour"]),
            autonomous_posts_enabled=bool(BOLTBOOK_POLICY_SETTINGS["posts_enabled"]),
            autonomous_post_profile=BOLTBOOK_AUTONOMOUS_POST_PROFILE,
            autonomous_post_submolt=BOLTBOOK_AUTONOMOUS_POST_SUBMOLT,
            autonomous_post_timeout_s=BOLTBOOK_AUTONOMOUS_POST_TIMEOUT_S,
            autonomous_post_cooldown_s=int(BOLTBOOK_POLICY_SETTINGS["post_cooldown_s"]),
            autonomous_post_window_s=int(BOLTBOOK_POLICY_SETTINGS["post_window_s"]),
            autonomous_post_max_per_window=int(BOLTBOOK_POLICY_SETTINGS["posts_per_window"]),
        )
        BOLTBOOK_HEARTBEAT_INSTANCE.start()

    print(f"🚀 Starting Factory API on http://{bind_host}:{args.port}")
    print(f"🌐 MODE={MODE}")
    print(f"🧭 Runtime layout: {RUNTIME_LAYOUT}")
    print(f"🗂️  Data root: {FACTORY_DIR}")
    print(f"📊 Database: {DB_PATH}")
    print(f"📁 Workspace: {WORKSPACE_ROOT}")
    print(f"🧩 Profiles: {PROFILES_DIR}")
    print(
        "🤖 Boltbook policy: "
        f"{BOLTBOOK_POLICY_SETTINGS['policy']} "
        f"(comments={int(BOLTBOOK_POLICY_SETTINGS['comments_per_hour'])}/h,"
        f" {int(BOLTBOOK_POLICY_SETTINGS['comments_per_day'])}/day; "
        f"posts={int(BOLTBOOK_POLICY_SETTINGS['posts_per_window'])}/"
        f"{int(BOLTBOOK_POLICY_SETTINGS['post_window_s']) // 3600}h)"
    )
    print(f"🔗 Endpoints:")
    print(f"   POST /task - Create new task")
    print(f"   GET /tasks - Get all tasks")
    print(f"   GET /task/{{id}} - Get task status")
    print(f"   GET /task/{{id}}/file/{{path}} - Get task file")
    print(f"   GET /task/{{id}}/user-input - List pending user input requests")
    print(f"   POST /task/{{id}}/user-input/{{request_id}} - Resolve user input request")
    print(f"   POST /task/{{id}}/stop - Stop task")
    print(f"   DELETE /task/{{id}} - Delete task")
    print(f"   POST /cleanup - Clean up old tasks")
    print(f"⏰ Schedule Endpoints:")
    print(f"   POST /schedules - Create schedule")
    print(f"   GET /schedules - List all schedules")
    print(f"   GET /schedules/{{id}} - Get schedule details")
    print(f"   PATCH /schedules/{{id}} - Update schedule")
    print(f"   DELETE /schedules/{{id}} - Delete schedule")
    print(f"   POST /schedules/{{id}}/run - Run schedule now")
    print(f"🛠️  Skills Endpoints:")
    print(f"   GET /skills - List all skills")
    print(f"   POST /skills/install - Install new skill")
    print(f"   POST /skills/reload - Reload all skills")
    print(f"🔒 Security Endpoints:")
    print(f"   GET /security/status - Runtime security status")
    print(f"   GET /security/policies - Security policies")
    print(f"   GET /security/approvals - Security approvals")
    print(f"   GET /security/events - Security audit events")
    print(f"🧪 App-server Endpoints:")
    print(f"   POST /app-server/sessions - Start or resume managed codex app-server session")
    print(f"   GET /app-server/sessions/{{id}} - Get managed session status")
    print(f"   GET /app-server/sessions/{{id}}/events - Poll app-server events")
    print(f"   POST /app-server/sessions/{{id}}/messages - Send chat message into managed session")
    print(f"   POST /app-server/sessions/{{id}}/approvals/{{request_id}} - Resolve native approval")
    print(f"   DELETE /app-server/sessions/{{id}} - Close managed session")
    print(f"⚡ Use Ctrl+C to stop the server")
    print()
    LOGGER.info("server_start", "Factory API server started", host=bind_host, port=args.port)

    server = None
    try:
        server = BoundedHTTPServer((bind_host, args.port), FactoryAPIHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped")
        LOGGER.info("server_stop", "Factory API server stopped by KeyboardInterrupt")
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        LOGGER.error("server_error", f"Factory API server error: {e}")
    finally:
        if server is not None:
            server.server_close()
        if BOLTBOOK_HEARTBEAT_INSTANCE:
            BOLTBOOK_HEARTBEAT_INSTANCE.stop()
        if WATCHDOG_INSTANCE:
            WATCHDOG_INSTANCE.stop()
        scheduler.stop()

if __name__ == '__main__':
    main()
