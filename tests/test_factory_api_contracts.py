"""Real loopback HTTP contracts with isolated SQLite and provider boundaries."""

import http.client
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory import factory_api as api


class Scheduler:
    running = True

    def __init__(self):
        self.items = {}
        self.runs = []

    def create_schedule(self, **fields):
        self.items["schedule-1"] = dict(fields, id="schedule-1")
        return "schedule-1"

    def get_schedule(self, identifier):
        item = self.items.get(identifier)
        return SimpleNamespace(to_dict=lambda: dict(item)) if item else None

    def list_schedules(self):
        return [self.get_schedule(k) for k in self.items]

    def update_schedule(self, identifier, **fields):
        if identifier not in self.items:
            return False
        self.items[identifier].update(fields)
        return True

    def delete_schedule(self, identifier):
        return self.items.pop(identifier, None) is not None

    def run_schedule_now(self, identifier):
        if identifier not in self.items:
            return False
        self.runs.append(identifier)
        return True


class Runtime:
    def __init__(self):
        self.calls = []

    def start_session(self, **fields):
        self.calls.append(("start", fields))
        return {"session_id": "session-1", "thread_id": "thread-1"}

    def get_session(self, identifier):
        if identifier == "missing":
            raise KeyError(identifier)
        return {"session_id": identifier}

    def list_events(self, identifier, **fields):
        self.calls.append(("events", fields))
        return [{"seq": 1}]

    def send_message(self, identifier, text):
        self.calls.append(("message", text))
        return {"status": "accepted"}

    def resolve_request(self, identifier, **fields):
        self.calls.append(("approval", fields))
        return {"approved": fields["approved"]}

    def close_session(self, identifier):
        if identifier == "missing":
            raise KeyError(identifier)
        self.calls.append(("close", identifier))


class Skills:
    def __init__(self, root):
        self.skills_path = str(root)
        self.is_loaded = True
        self.all_skills = {
            "fixture": SimpleNamespace(skill_id="fixture", name="Fixture")
        }
        self.skills = dict(self.all_skills)

    def load_settings(self):
        return {
            "skills_path": self.skills_path,
            "skills_auto_load": "true",
            "skills_smart_selection": "false",
        }

    def get_metadata(self):
        return [
            {"skill_id": s.skill_id, "name": s.name} for s in self.all_skills.values()
        ]

    def get_active_count(self):
        return len(self.skills)

    def load_skills(self):
        return True

    def install_skill_from_markdown(self, name, content, overwrite):
        if name == "duplicate":
            return False, "File already exists"
        if name == "rejected":
            return False, "Invalid name"
        return True, str(Path(self.skills_path) / (name + ".md"))

    def find_skill_by_name(self, name):
        return next((s for s in self.all_skills.values() if s.name == name), None)

    def set_skill_enabled(self, identifier, enabled):
        if enabled:
            self.skills[identifier] = self.all_skills[identifier]
        else:
            self.skills.pop(identifier, None)
        return True, "Updated"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path))
    db = api.get_db_path()
    assert Path(db).is_relative_to(tmp_path)
    api.initialize_database(db)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(api, "DB_PATH", db)
    monkeypatch.setattr(api, "WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(api, "FACTORY_DIR", tmp_path)
    monkeypatch.setattr(api, "API_KEY", "fixture-key")
    monkeypatch.setattr(api, "CORS_ORIGINS", ["https://fixture.invalid"])
    scheduler = Scheduler()
    runtime = Runtime()
    skills = Skills(tmp_path / "skills")
    monkeypatch.setattr(api, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(api, "SCHEDULER_INITIALIZED", True)
    monkeypatch.setattr(api, "APP_SERVER_RUNTIME", runtime)
    monkeypatch.setattr(api, "skills_manager", skills)
    monkeypatch.setattr(api, "SKILLS_AVAILABLE", True)
    store = api.SecurityStore(db)
    store.ensure_bootstrap()
    monkeypatch.setattr(api, "SECURITY_STORE", store)
    httpd = api.BoundedHTTPServer(("127.0.0.1", 0), api.FactoryAPIHandler)
    thread = threading.Thread(
        target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()

    def request(method, path, body=None, raw=None, headers=None):
        data = (
            raw
            if raw is not None
            else json.dumps(body, ensure_ascii=False).encode()
            if body is not None
            else None
        )
        h = {
            "X-API-Key": "fixture-key",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=3)
        try:
            conn.request(method, path, body=data, headers=h)
            response = conn.getresponse()
            binary = response.read()
            return (
                response.status,
                json.loads(binary) if binary else {},
                dict(response.getheaders()),
            )
        finally:
            conn.close()

    yield SimpleNamespace(
        request=request,
        scheduler=scheduler,
        runtime=runtime,
        skills=skills,
        store=store,
        workspace=workspace,
        db=db,
    )
    httpd.shutdown()
    httpd.server_close()
    thread.join(3)
    assert not thread.is_alive()


def test_task_creation_query_stop_and_delete_use_real_database(server):
    status, created, _ = server.request(
        "POST",
        "/task",
        {
            "text": "Fixture task",
            "profile": "fixture",
            "capabilities": ["read"],
            "metadata": {"label": "fixture"},
        },
    )
    assert status == 201 and created["status"] == "queued"
    identifier = created["task_id"]
    with api.open_db(server.db) as conn:
        row = conn.execute(
            "SELECT input_text,profile,metadata FROM tasks WHERE task_id=?",
            (identifier,),
        ).fetchone()
    assert (
        row[:2] == ("Fixture task", "fixture")
        and json.loads(row[2])["created_via"] == "factory_api"
    )
    assert server.request("GET", "/tasks")[1]["tasks"][0]["task_id"] == identifier
    assert (
        server.request("GET", "/task/" + identifier)[1]["metadata"]["label"]
        == "fixture"
    )
    assert server.request("PUT", "/task/" + identifier + "/stop")[0] == 200
    assert server.request("GET", "/task/" + identifier)[1]["status"] == "dead_letter"
    owned = server.workspace / identifier
    owned.mkdir()
    (owned / "fixture.txt").write_text("fixture")
    assert (
        server.request("DELETE", "/task/" + identifier)[0] == 200 and not owned.exists()
    )
    assert server.request("GET", "/task/" + identifier)[0] == 404


@pytest.mark.parametrize("status_value", ["ok", "fail", "processing"])
def test_task_details_include_output_errors_and_both_workspace_locations(
    server, status_value
):
    identifier = "fixture-task-123"
    with api.open_db(server.db) as conn:
        conn.execute(
            "INSERT INTO tasks (task_id,status,output_text,error_message,metadata) VALUES (?,?,?,?,?)",
            (identifier, status_value, "Fixture output", "Fixture error", "not-json"),
        )
    result = server.workspace / identifier / "results"
    result.mkdir(parents=True)
    (result / "result.txt").write_text("fixture")
    (result / "copy_metadata.json").write_text("{}")
    temporary = server.workspace / ("temp_execution_" + identifier[:8])
    temporary.mkdir()
    (temporary / "temporary.txt").write_text("fixture")
    (temporary / "input.json").write_text("{}")
    for payload in [
        server.request("GET", "/task/" + identifier)[1],
        server.request("GET", "/tasks")[1]["tasks"][0],
    ]:
        assert (
            payload["metadata"] == "not-json"
            and payload["error_message"] == "Fixture error"
        )
        assert sorted(payload["workspace_files"]) == [
            "result.txt",
            "temp/temporary.txt",
        ]


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/task/missing"),
        ("PUT", "/task/missing/stop"),
        ("DELETE", "/task/missing"),
        ("GET", "/unknown"),
        ("POST", "/unknown"),
        ("PATCH", "/unknown"),
        ("PUT", "/unknown"),
        ("DELETE", "/unknown"),
    ],
)
def test_unknown_resources_return_not_found(server, method, path):
    assert server.request(method, path, {})[0] == 404


def test_task_requires_text_and_invalid_json_is_client_error(server):
    assert server.request("POST", "/task", {})[0] == 400
    assert server.request("POST", "/task", raw=b"{")[0] == 400


def test_health_readiness_and_cors_headers(server):
    assert server.request("GET", "/health", headers={"X-API-Key": ""})[0] == 200
    status, body, _ = server.request("GET", "/ready")
    assert status == 200 and all(body["checks"]["tables"].values())
    status, _, headers = server.request(
        "OPTIONS", "/tasks", headers={"Origin": "https://fixture.invalid"}
    )
    assert (
        status == 200
        and headers["Access-Control-Allow-Origin"] == "https://fixture.invalid"
    )
    assert (
        server.request("GET", "/tasks", headers={"Origin": "https://other.invalid"})[0]
        == 403
    )


def test_ready_reports_unstarted_scheduler_without_starting_it(server, monkeypatch):
    monkeypatch.setattr(api, "SCHEDULER_INITIALIZED", False)
    status, body, _ = server.request("GET", "/ready")
    assert status == 503 and not body["checks"]["scheduler_initialized"]


@pytest.mark.parametrize("key,expected", [("", 401), ("wrong", 401)])
def test_private_routes_require_configured_credentials(server, key, expected):
    assert server.request("GET", "/tasks", headers={"X-API-Key": key})[0] == expected
    assert (
        server.request(
            "POST", "/task", {"text": "No write"}, headers={"X-API-Key": key}
        )[0]
        == expected
    )
    with api.open_db(server.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_missing_server_key_fails_closed(server, monkeypatch):
    monkeypatch.setattr(api, "API_KEY", "")
    assert server.request("GET", "/tasks")[0] == 503


def test_schedule_crud_and_manual_trigger_use_only_fixture_scheduler(server):
    payload = {
        "name": "Fixture",
        "agent_id": "fixture",
        "payload": {"text": "Fixture"},
        "time_value": "12:30",
        "timezone": "Europe/Moscow",
        "enabled": False,
    }
    status, body, _ = server.request("POST", "/schedules", payload)
    assert status == 201
    identifier = body["schedule_id"]
    assert server.scheduler.items[identifier]["tz"] == "Europe/Moscow"
    assert len(server.request("GET", "/schedules")[1]["schedules"]) == 1
    assert server.request("GET", "/schedules/" + identifier)[1]["name"] == "Fixture"
    assert (
        server.request("PATCH", "/schedules/" + identifier, {"name": "Changed"})[0]
        == 200
    )
    assert server.request("POST", "/schedules/" + identifier + "/run", {})[
        0
    ] == 200 and server.scheduler.runs == [identifier]
    assert server.request("DELETE", "/schedules/" + identifier)[0] == 200
    assert server.request("GET", "/schedules/" + identifier)[0] == 404
    assert (
        server.request("PATCH", "/schedules/" + identifier, {"name": "missing"})[0]
        == 404
    )
    assert server.request("DELETE", "/schedules/" + identifier)[0] == 404
    assert server.request("POST", "/schedules/" + identifier + "/run", {})[0] == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "Fixture"},
        {"name": "Fixture", "agent_id": "fixture"},
        {"name": "Fixture", "agent_id": "fixture", "payload": {}},
        {
            "name": "Fixture",
            "agent_id": "fixture",
            "payload": {},
            "schedule_type": "cron",
        },
    ],
)
def test_schedule_requires_fields_and_type_specific_time_data(server, payload):
    assert server.request("POST", "/schedules", payload)[0] == 400


def test_app_server_route_lifecycle_passes_arguments_to_stub_process_boundary(server):
    status, body, _ = server.request(
        "POST",
        "/app-server/sessions",
        {
            "profile": "fixture",
            "cwd": "/fixture/workspace",
            "thread_id": "old",
            "chat_session": "chat",
            "provider_override": "fixture-provider",
            "model_override": "fixture-model",
        },
    )
    assert status == 201 and body["session_id"] == "session-1"
    assert server.runtime.calls[0][1]["cwd"] == "/fixture/workspace"
    assert server.request("GET", "/app-server/sessions/session-1")[0] == 200
    assert server.request(
        "GET", "/app-server/sessions/session-1/events?after=3&limit=900"
    )[1]["events"] == [{"seq": 1}]
    assert server.runtime.calls[-1] == ("events", {"after_seq": 3, "limit": 500})
    assert (
        server.request(
            "POST", "/app-server/sessions/session-1/messages", {"text": " Hello "}
        )[0]
        == 202
    )
    assert server.runtime.calls[-1] == ("message", "Hello")
    assert (
        server.request(
            "POST",
            "/app-server/sessions/session-1/approvals/request-1",
            {"action": "reject", "remember_for_session": True},
        )[1]["approved"]
        is False
    )
    assert server.request("DELETE", "/app-server/sessions/session-1")[0] == 200
    assert server.request("GET", "/app-server/sessions/missing")[0] == 404
    assert server.request("DELETE", "/app-server/sessions/missing")[0] == 404
    assert (
        server.request(
            "POST", "/app-server/sessions/session-1/messages", {"text": " "}
        )[0]
        == 400
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/schedules"),
        ("PATCH", "/schedules/missing"),
        ("POST", "/skills/install"),
        ("POST", "/skills/toggle"),
        ("POST", "/app-server/sessions"),
        ("POST", "/app-server/sessions/session-1/messages"),
        ("POST", "/app-server/sessions/session-1/approvals/request-1"),
        ("POST", "/security/approvals/missing/resolve"),
    ],
)
def test_route_json_decoders_reject_malformed_payloads(server, method, path):
    assert server.request(method, path, raw=b"{")[0] == 400


def test_security_approval_api_updates_only_temporary_sqlite_store(server):
    identifier = server.store.create_approval(
        "fixture", "fixture-target", reason="Test approval"
    )
    assert server.request("GET", "/security/status")[1]["pending_approvals"] == 1
    assert server.request("GET", "/security/policies")[1]["policies"]
    assert (
        len(server.request("GET", "/security/approvals?status=pending")[1]["approvals"])
        == 1
    )
    assert (
        server.request("GET", "/security/approvals/" + identifier)[1]["status"]
        == "pending"
    )
    assert (
        server.request(
            "POST",
            "/security/approvals/" + identifier + "/resolve",
            {"status": "invalid"},
        )[0]
        == 400
    )
    status, body, _ = server.request(
        "PATCH",
        "/security/approvals/" + identifier,
        {
            "status": "rejected",
            "resolved_by": "fixture",
            "resolution_note": "Fixture note",
        },
    )
    assert status == 200 and body["status"] == "rejected"
    assert (
        server.request(
            "POST",
            "/security/approvals/" + identifier + "/resolve",
            {"status": "approved"},
        )[0]
        == 409
    )
    assert server.request("GET", "/security/approvals/missing")[0] == 404
    assert server.request("GET", "/security/events")[1]["events"]


def test_skill_routes_expose_metadata_install_reload_and_boolean_toggle(server):
    assert (
        server.request("GET", "/skills")[1]["settings"]["skills_smart_selection"]
        is False
    )
    assert (
        server.request(
            "POST", "/skills/install", {"name": "new", "content": "Fixture content"}
        )[0]
        == 201
    )
    assert (
        server.request(
            "POST", "/skills/install", {"name": "duplicate", "content": "Fixture"}
        )[0]
        == 409
    )
    assert (
        server.request(
            "POST", "/skills/install", {"name": "rejected", "content": "Fixture"}
        )[0]
        == 400
    )
    assert server.request("POST", "/skills/reload", {})[1]["loaded"] is True
    assert (
        server.request(
            "POST", "/skills/toggle", {"skill_id": "fixture.md", "enabled": False}
        )[0]
        == 200
    )
    assert server.skills.get_active_count() == 0
    assert (
        server.request("POST", "/skills/toggle", {"name": "Fixture", "enabled": True})[
            0
        ]
        == 200
    )
    assert server.skills.get_active_count() == 1


@pytest.mark.parametrize(
    "payload", [{}, {"name": "Fixture"}, {"name": "Fixture", "content": "  "}]
)
def test_skill_install_requires_content_and_name(server, payload):
    assert server.request("POST", "/skills/install", payload)[0] == 400


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"enabled": "yes", "skill_id": "fixture"},
        {"enabled": True},
        {"enabled": True, "skill_id": "../outside"},
        {"enabled": True, "skill_id": "missing"},
    ],
)
def test_skill_toggle_rejects_invalid_or_missing_targets(server, payload):
    assert server.request("POST", "/skills/toggle", payload)[0] in {400, 404}


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/skills"),
        ("POST", "/skills/install"),
        ("POST", "/skills/reload"),
        ("POST", "/skills/toggle"),
    ],
)
def test_unavailable_skills_are_explicit(server, monkeypatch, method, path):
    monkeypatch.setattr(api, "SKILLS_AVAILABLE", False)
    assert server.request(method, path, {})[0] == 503


def test_user_input_pending_and_resolution_are_scoped_to_the_task(server):
    import core.user_input_store as ui

    item = ui.create_user_input_request(
        task_id="fixture-task", payload={"question": "Choose a fixture"}
    )
    identifier = item["request_id"]
    assert (
        len(server.request("GET", "/task/fixture-task/user-input")[1]["requests"]) == 1
    )
    assert (
        server.request(
            "POST", "/task/other/user-input/" + identifier, {"response": {"value": "x"}}
        )[0]
        == 404
    )
    assert (
        server.request(
            "POST",
            "/task/fixture-task/user-input/" + identifier,
            {"response": {"value": "chosen"}},
        )[0]
        == 200
    )
    assert (
        server.request(
            "POST", "/task/fixture-task/user-input/" + identifier, {"response": {}}
        )[0]
        == 409
    )


def raises(error):
    def fail(*args, **kwargs):
        raise error

    return fail


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/tasks", None),
        ("GET", "/task/fixture", None),
        ("POST", "/task", {"text": "Fixture"}),
        ("PUT", "/task/fixture/stop", {}),
        ("DELETE", "/task/fixture", {}),
    ],
)
def test_database_failure_returns_a_causal_error_without_a_success_status(
    server, monkeypatch, method, path, body
):
    monkeypatch.setattr(api, "open_db", raises(OSError("fixture database unavailable")))
    status, result, _ = server.request(method, path, body)
    assert status == 500 and "fixture database unavailable" in result["error"]


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/schedules", None),
        ("GET", "/schedules/fixture", None),
        (
            "POST",
            "/schedules",
            {
                "name": "Fixture",
                "agent_id": "fixture",
                "payload": {},
                "time_value": "12:00",
            },
        ),
        ("PATCH", "/schedules/fixture", {"name": "Fixture"}),
        ("DELETE", "/schedules/fixture", {}),
        ("POST", "/schedules/fixture/run", {}),
    ],
)
def test_scheduler_failure_is_reported_without_creating_or_running_a_job(
    server, monkeypatch, method, path, body
):
    monkeypatch.setattr(
        api, "get_scheduler", raises(OSError("fixture scheduler unavailable"))
    )
    status, result, _ = server.request(method, path, body)
    assert status == 500 and "fixture scheduler unavailable" in result["error"]
    assert server.scheduler.items == {} and server.scheduler.runs == []


@pytest.mark.parametrize(
    "method,path,body,boundary",
    [
        ("POST", "/app-server/sessions", {}, "start_session"),
        ("GET", "/app-server/sessions/s", None, "get_session"),
        ("GET", "/app-server/sessions/s/events", None, "list_events"),
        (
            "POST",
            "/app-server/sessions/s/messages",
            {"text": "Fixture"},
            "send_message",
        ),
        ("POST", "/app-server/sessions/s/approvals/r", {}, "resolve_request"),
        ("DELETE", "/app-server/sessions/s", None, "close_session"),
    ],
)
def test_process_provider_failure_has_an_explicit_error_and_never_calls_a_real_process(
    server, monkeypatch, method, path, body, boundary
):
    monkeypatch.setattr(
        server.runtime, boundary, raises(OSError("fixture process unavailable"))
    )
    status, result, _ = server.request(method, path, body)
    assert status == 500 and "fixture process unavailable" in result["error"]


@pytest.mark.parametrize(
    "path,boundary,body",
    [
        ("/app-server/sessions/s/events", "list_events", None),
        ("/app-server/sessions/s/messages", "send_message", {"text": "Fixture"}),
        ("/app-server/sessions/s/approvals/r", "resolve_request", {}),
    ],
)
def test_missing_native_resources_keep_not_found_semantics(
    server, monkeypatch, path, boundary, body
):
    monkeypatch.setattr(server.runtime, boundary, raises(KeyError("missing")))
    assert server.request("GET" if body is None else "POST", path, body)[0] == 404


def test_invalid_native_session_configuration_is_a_client_error(server, monkeypatch):
    monkeypatch.setattr(
        server.runtime, "start_session", raises(ValueError("fixture profile invalid"))
    )
    assert server.request("POST", "/app-server/sessions", {})[0] == 400


@pytest.mark.parametrize(
    "path,boundary",
    [
        ("/security/status", "ensure_bootstrap"),
        ("/security/policies", "list_policies"),
        ("/security/approvals", "list_approvals"),
        ("/security/approvals/a", "get_approval"),
        ("/security/events", "list_events"),
    ],
)
def test_security_store_failure_is_distinct_from_empty_success(
    server, monkeypatch, path, boundary
):
    monkeypatch.setattr(
        server.store, boundary, raises(OSError("fixture store unavailable"))
    )
    status, body, _ = server.request("GET", path)
    assert status == 500 and "fixture store unavailable" in body["error"]


@pytest.mark.parametrize(
    "error,expected",
    [
        (KeyError("missing"), 404),
        (ValueError("fixture invalid resolution"), 400),
        (OSError("fixture store unavailable"), 500),
    ],
)
def test_security_resolution_errors_keep_their_status_class(
    server, monkeypatch, error, expected
):
    monkeypatch.setattr(server.store, "resolve_approval", raises(error))
    assert (
        server.request("POST", "/security/approvals/a/resolve", {"status": "rejected"})[
            0
        ]
        == expected
    )


@pytest.mark.parametrize(
    "path,body,boundary",
    [
        ("/skills", None, "load_settings"),
        (
            "/skills/install",
            {"name": "fixture", "content": "Body"},
            "install_skill_from_markdown",
        ),
        ("/skills/reload", {}, "load_skills"),
        (
            "/skills/toggle",
            {"skill_id": "fixture", "enabled": False},
            "set_skill_enabled",
        ),
    ],
)
def test_skill_adapter_failure_preserves_a_causal_error(
    server, monkeypatch, path, body, boundary
):
    monkeypatch.setattr(
        server.skills, boundary, raises(OSError("fixture skills unavailable"))
    )
    status, result, _ = server.request("GET" if body is None else "POST", path, body)
    assert status == 500 and "fixture skills unavailable" in result["error"]


def test_unsuccessful_reload_and_toggle_are_not_reported_as_success(
    server, monkeypatch
):
    monkeypatch.setattr(server.skills, "load_skills", lambda: False)
    assert server.request("POST", "/skills/reload", {})[1]["loaded"] is False
    monkeypatch.setattr(
        server.skills, "set_skill_enabled", lambda *a: (False, "Fixture denied")
    )
    assert (
        server.request(
            "POST", "/skills/toggle", {"skill_id": "fixture", "enabled": False}
        )[0]
        == 500
    )
