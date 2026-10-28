"""Task cancellation must reach an already running registered local command."""
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from factory.core import running_commands as commands
from factory.core.cli_tasks import cancel_task, create_task
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database, open_db
from factory.core.tool_runtime_context import clear_runtime_context, set_runtime_context


pytestmark = pytest.mark.skipif(os.name != 'posix' or not Path('/proc/self/stat').exists(), reason='Real process-group acceptance requires Linux procfs')


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path / 'data'))
    monkeypatch.setenv('IGOREK_BASH_SANDBOX', 'off')
    from factory.core import opencode_models, openrouter_pricing
    monkeypatch.setattr(opencode_models, '_fetch_models', lambda: None)
    monkeypatch.setattr(openrouter_pricing, '_fetch_models', lambda timeout_s: None)
    initialize_database(get_db_path())
    return tmp_path


@pytest.mark.parametrize('ignore_term', [False, True])
def test_runtime_interrupt_reaps_running_process_instead_of_waiting_for_tool_timeout(context, ignore_term):
    interrupt = threading.Event();result = {};ready = context / 'ready'
    script = 'import signal,time;from pathlib import Path;'
    if ignore_term: script += 'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
    script += f'Path({str(ready)!r}).write_text("ready");time.sleep(30)'
    def run():
        set_runtime_context(task_id='fixture-task', interrupt_event=interrupt)
        try:
            with commands.command_context('fixture-session', 'fixture-task', 'fixture-tool'):
                result['value'] = commands.run_process([sys.executable, '-I', '-c', script], label='fixture sleeper', capture_output=True, text=True, timeout=3)
        except BaseException as error: result['error'] = error
        finally: clear_runtime_context()
    worker = threading.Thread(target=run, daemon=True);worker.start()
    deadline = time.monotonic() + 4
    while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)
    assert ready.exists(), 'Fixture process did not start'
    started = time.monotonic();interrupt.set()
    try:
        worker.join(5)
        assert not worker.is_alive()
        assert 'error' not in result, type(result.get('error')).__name__
        assert result['value'].returncode < 0 and 'Command stopped by user' in result['value'].stderr
        assert time.monotonic() - started < 2.5
        assert commands.list_commands('fixture-session') == []
    finally:
        for row in commands.list_commands('fixture-session'):commands.request_stop('fixture-session', row['id'])
        worker.join(4)


def test_real_cancelled_task_interrupts_bash_before_its_timeout(context, monkeypatch):
    from factory.core import cc_backend_executor as executor
    from factory.core.cc_backend_tools import get_tool_schemas
    import shlex
    task = create_task(text='Run temporary fixture command', profile='fixture')
    with open_db(get_db_path()) as db:db.execute("UPDATE tasks SET status='processing' WHERE task_id=?", (task['task_id'],))
    ready = context / 'executor-ready'
    script = f'from pathlib import Path;import time;Path({str(ready)!r}).write_text("ready");time.sleep(30)'
    command = shlex.quote(sys.executable) + ' -I -c ' + shlex.quote(script)
    calls, result = [], {}
    def post(url, payload, *args, **kwargs):
        calls.append(payload)
        assert len(calls) == 1, 'Cancelled task requested another model round'
        return {'content': [{'type': 'tool_use', 'id': 'fixture-tool', 'name': 'Bash', 'input': {'command': command, 'timeout': 3000}}], 'usage': {}}, 1
    monkeypatch.setattr(executor, '_http_post', post)
    def run():
        try:
            result['value'] = executor.execute_cc_backend_task(base_url='http://127.0.0.1:1', model='fixture', system_prompt='fixture', user_message='Run the fixture', task_id=task['task_id'], current_session='fixture-session', workdir=str(context), artifact_workdir=str(context / 'trace'), enabled_tools=[x for x in get_tool_schemas() if x['name'] == 'Bash'], metadata={'approved_session_tools': ['Bash'], 'model_context_window': 65536})
        except BaseException as error:result['error'] = error
    worker = threading.Thread(target=run, daemon=True);worker.start()
    deadline = time.monotonic() + 6
    while not ready.exists() and time.monotonic() < deadline:time.sleep(.01)
    assert ready.exists(), 'Bash fixture did not execute'
    started = time.monotonic();assert cancel_task(task['task_id'])
    try:
        worker.join(6)
        assert not worker.is_alive() and 'error' not in result
        assert time.monotonic() - started < 2.5
        assert result['value']['status'] == 'fail' and result['value']['failure_reason'] == 'interrupted'
        assert len(calls) == 1 and commands.list_commands('fixture-session') == []
    finally:
        for row in commands.list_commands('fixture-session'):commands.request_stop('fixture-session', row['id'])
        worker.join(4)
