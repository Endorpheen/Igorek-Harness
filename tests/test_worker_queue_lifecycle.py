"""Actual queue, heartbeat and persistence with synthetic owned task execution."""
import json
import threading
import time
from unittest.mock import Mock

import pytest

from factory.core import worker as w
from factory.core.schema import open_db
from factory.core.worker_presence import consumers
from test_worker_execution_contracts import worker_factory as worker_factory
from test_worker_stuck_task_recovery import _insert_task


class StopAfterWait:
    def __init__(self):self.event=threading.Event();self.waits=[]
    def is_set(self):return self.event.is_set()
    def set(self):self.event.set()
    def wait(self,seconds):self.waits.append(seconds);self.set();return True


def insert(identifier,profile,status='queued'):
    with open_db(w.DB_PATH) as db:_insert_task(db,identifier,profile,status=status)


@pytest.mark.parametrize('status',['ok','fail','retry','waiting_approval','cancelled'])
def test_real_worker_loop_claims_only_its_profile_and_persists_terminal_result(worker_factory,monkeypatch,status):
    worker=worker_factory();stop=threading.Event()
    insert('owned-task',worker.profile);insert('foreign-task','other-fixture-profile')
    seen=[]
    def execute(task):
        seen.append(task['task_id'])
        assert consumers(worker.profile,worker.config)
        with open_db(w.DB_PATH) as db:
            assert db.execute("SELECT status FROM tasks WHERE task_id='owned-task'").fetchone()[0]=='processing'
        stop.set()
        return {'status':status,'output':{'text':'fixture result'},'error':'fixture error' if status=='fail' else '', 'usage':{'prompt':5,'completion':2}}
    monkeypatch.setattr(worker,'execute_task',execute)
    worker.run(stop_event=stop)
    assert seen==['owned-task'] and worker.tasks_processed==1
    assert worker._presence is None and not consumers(worker.profile,worker.config)
    with open_db(w.DB_PATH) as db:
        row=db.execute("SELECT status,output_text,usage_prompt,usage_completion,completed_at FROM tasks WHERE task_id='owned-task'").fetchone()
        assert row[:4]==(status,'fixture result',5,2)
        assert (row[4] is None)==(status=='waiting_approval')
        assert db.execute("SELECT status FROM tasks WHERE task_id='foreign-task'").fetchone()[0]=='queued'
        event=db.execute('SELECT details FROM events WHERE task_id=? AND event_type=?',('owned-task','task_'+status)).fetchone()
        assert event and json.loads(event[0])['status']==status
    assert not any(thread.name=='heartbeat-owned-ta' for thread in threading.enumerate())


@pytest.mark.parametrize('raises',[False,True])
def test_real_heartbeat_runs_while_execution_is_active_and_stops_afterward(worker_factory,monkeypatch,raises):
    worker=worker_factory();worker.heartbeat_interval=.01;tick=threading.Event()
    insert('heartbeat-owned',worker.profile,status='processing')
    original=worker._touch_task
    def touch(identifier):
        result=original(identifier);tick.set();return result
    def execute(task):
        assert tick.wait(2),'owned heartbeat did not run'
        if raises:raise RuntimeError('synthetic execution failure')
        return {'status':'ok'}
    monkeypatch.setattr(worker,'_touch_task',touch);monkeypatch.setattr(worker,'execute_task',execute)
    with open_db(w.DB_PATH) as db:
        if raises:
            with pytest.raises(RuntimeError,match='synthetic execution failure'):worker._execute_task_with_heartbeat(db,{'task_id':'heartbeat-owned'})
        else:assert worker._execute_task_with_heartbeat(db,{'task_id':'heartbeat-owned'})=={'status':'ok'}
    assert tick.is_set()
    assert not any(thread.name=='heartbeat-heartbea' for thread in threading.enumerate())


def test_idle_worker_exits_on_stop_and_uses_configured_poll_interval(worker_factory):
    worker=worker_factory();worker.poll_interval=.123;stop=StopAfterWait()
    worker.run(stop_event=stop)
    assert stop.waits==[.123] and worker.tasks_processed==0
    assert worker._presence is None


def test_stop_while_waiting_for_execution_lock_prevents_claim(worker_factory,monkeypatch):
    worker=worker_factory();stop=threading.Event();claim=Mock(side_effect=AssertionError('must not claim after stop'))
    monkeypatch.setattr(worker,'claim_task',claim)
    class Lock:
        def __enter__(self):stop.set()
        def __exit__(self,*args):pass
    worker.run(stop_event=stop,execution_lock=Lock())
    claim.assert_not_called();assert worker.tasks_processed==0


def test_periodic_cleanup_does_not_execute_queued_work_in_the_same_iteration(worker_factory,monkeypatch):
    worker=worker_factory();worker.last_cleanup_time=0;stop=threading.Event()
    with open_db(w.DB_PATH) as db:
        _insert_task(db,'stale-owned',worker.profile,updated_at=int(time.time())-600)
    insert('next-owned',worker.profile)
    original=worker.cleanup_stuck_tasks
    def cleanup(**kwargs):original(**kwargs);stop.set()
    monkeypatch.setattr(worker,'cleanup_stuck_tasks',cleanup)
    execute=Mock(side_effect=AssertionError('cleanup iteration should not execute queued work'))
    monkeypatch.setattr(worker,'execute_task',execute)
    worker.run(stop_event=stop)
    execute.assert_not_called()
    with open_db(w.DB_PATH) as db:
        assert db.execute("SELECT status FROM tasks WHERE task_id='stale-owned'").fetchone()[0]=='dead_letter'
        assert db.execute("SELECT status FROM tasks WHERE task_id='next-owned'").fetchone()[0]=='queued'
    assert worker.last_cleanup_time>0


def test_claim_failure_is_reported_and_worker_wait_can_still_stop_it(worker_factory,monkeypatch,capsys):
    worker=worker_factory();stop=StopAfterWait()
    monkeypatch.setattr(worker,'claim_task',Mock(side_effect=RuntimeError('fixture claim failure')))
    worker.run(stop_event=stop)
    assert stop.waits==[worker.poll_interval]
    assert 'fixture claim failure' in capsys.readouterr().out
    assert worker._presence is None


@pytest.mark.parametrize('shutdown_fails',[False,True])
def test_keyboard_interrupt_cleans_worker_presence_even_if_owned_mcp_shutdown_fails(worker_factory,monkeypatch,shutdown_fails):
    from factory.core import mcp_bridge
    worker=worker_factory()
    monkeypatch.setattr(worker,'claim_task',Mock(side_effect=KeyboardInterrupt()))
    shutdown=Mock(side_effect=RuntimeError('fixture shutdown failure') if shutdown_fails else None)
    monkeypatch.setattr(mcp_bridge,'shutdown_mcp_servers',shutdown)
    worker.run(stop_event=threading.Event())
    shutdown.assert_called_once()
    assert worker._presence is None and not consumers(worker.profile,worker.config)


def test_route_next_deduplicates_profiles_and_copies_payload_and_limits(worker_factory):
    worker=worker_factory();insert('parent-owned',worker.profile)
    task={'task_id':'parent-owned','stage':'main','next_profiles':['next-a','next-b'], 'limits_max_tokens':123,'limits_timeout_s':17,'limits_budget_usd':.08}
    result={'propose_next':['next-b','next-c'],'output':{'text':'parent evidence','files':['artifact.txt']}}
    with open_db(w.DB_PATH) as db:
        worker.route_next(db,task,result)
        rows=db.execute("SELECT profile,status,input_text,input_files,limits_max_tokens,limits_timeout_s,limits_budget_usd FROM tasks WHERE parent_id='parent-owned'").fetchall()
    assert {row[0] for row in rows}=={'next-a','next-b','next-c'} and len(rows)==3
    assert all(row[1:]==('queued','parent evidence','["artifact.txt"]',123,17,.08) for row in rows)


def test_empty_next_step_does_not_create_another_task(worker_factory):
    worker=worker_factory();insert('parent-owned',worker.profile)
    with open_db(w.DB_PATH) as db:
        worker.route_next(db,{'task_id':'parent-owned'},{})
        assert db.execute("SELECT count(*) FROM tasks WHERE parent_id='parent-owned'").fetchone()[0]==0
