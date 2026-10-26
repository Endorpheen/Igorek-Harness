"""Background social ticks and real owned action logs with all external actions fake."""
import importlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory import factory_api
from factory.core.schema import initialize_database

heartbeat=importlib.import_module(factory_api.BoltbookHeartbeatLoop.__module__)
actions=importlib.import_module(heartbeat.log_autonomous_action.__module__)


@pytest.fixture
def loop(tmp_path,monkeypatch):
    monkeypatch.setattr(actions,'DB_PATH',initialize_database(str(tmp_path/'actions.db')))
    monkeypatch.setattr(actions,'time',SimpleNamespace(time=lambda:100000))
    logger=Mock();monkeypatch.setattr(heartbeat,'get_logger',lambda name:logger)
    client=SimpleNamespace(heartbeat_tick=Mock(return_value={'status':'ok','checked_at':100000,'result':{'status':'claimed','feed_count':3}}))
    credentials=Mock(return_value=client)
    monkeypatch.setattr(heartbeat,'BoltbookClient',SimpleNamespace(from_local_credentials=credentials))
    comment=Mock(return_value={'status':'skipped','reason':'fixture cooldown'})
    post=Mock(return_value={'status':'skipped','reason':'fixture cooldown'})
    monkeypatch.setattr(heartbeat,'autonomous_comment_tick',comment)
    monkeypatch.setattr(heartbeat,'autonomous_post_tick',post)
    monkeypatch.setattr(heartbeat,'show_persona_state',Mock(return_value={'active':{'key':'fixture','title':'Fixture'},'persona':{'mode':'fixed'}}))
    monkeypatch.setattr(heartbeat,'time',SimpleNamespace(time=lambda:100000,sleep=lambda seconds:None))
    created=[]
    class Thread:
        def __init__(self,**kwargs):self.options=kwargs;self.started=False;self.joined=[];created.append(self)
        def start(self):self.started=True
        def join(self,**kwargs):self.joined.append(kwargs)
    monkeypatch.setattr(heartbeat.threading,'Thread',Thread)
    return SimpleNamespace(make=lambda **kwargs:heartbeat.BoltbookHeartbeatLoop(**kwargs),client=client,credentials=credentials,comment=comment,post=post,logger=logger,threads=created)


def test_constructor_bounds_protect_background_waits_and_action_limits(loop):
    value=loop.make(loop_interval_s=0,heartbeat_interval_s=0,skill_refresh_interval_s=0,feed_limit=0,autonomous_min_score=0,autonomous_timeout_s=0,autonomous_comment_cooldown_s=0,autonomous_max_comments_per_hour=0,autonomous_max_comments_per_day=0,autonomous_own_post_max_comments_per_hour=0,autonomous_post_timeout_s=0,autonomous_post_cooldown_s=0,autonomous_post_window_s=0,autonomous_post_max_per_window=0)
    assert value.loop_interval_s==value.feed_limit==value.heartbeat_interval_s==1
    assert value.autonomous_timeout_s==30 and value.autonomous_post_timeout_s==60
    assert value.autonomous_post_window_s==60 and value.autonomous_post_max_per_window==1


def test_missing_credentials_disable_start_without_creating_a_thread(loop):
    loop.credentials.side_effect=FileNotFoundError('fixture missing')
    value=loop.make()
    assert value.start() is False and not value.is_initialized() and loop.threads==[]


@pytest.mark.parametrize('persona_error',[False,True])
def test_start_is_idempotent_and_stop_joins_only_its_thread_stub(loop,monkeypatch,persona_error):
    if persona_error:monkeypatch.setattr(heartbeat,'show_persona_state',Mock(side_effect=RuntimeError('fixture persona unavailable')))
    value=loop.make(autonomous_comments_enabled=True)
    assert value.start() and value.start() and value.is_initialized()
    assert len(loop.threads)==1 and loop.threads[0].started and loop.threads[0].options['daemon']
    assert loop.credentials.call_count==1
    value.stop()
    assert not value.is_initialized() and value._thread is None
    assert loop.threads[0].joined==[{'timeout':5}] and not loop.comment.called and not loop.post.called


@pytest.mark.parametrize('status',['ok','skipped','unknown',''])
def test_heartbeat_status_is_persisted_and_disabled_actions_are_never_called(loop,status):
    payload={'status':status,'checked_at':100000,'result':{'status':'claimed'},'reason':'fixture not due'}
    loop.client.heartbeat_tick.return_value=payload
    value=loop.make()
    assert value.run_once()==payload
    assert not loop.comment.called and not loop.post.called
    rows=actions.list_autonomous_actions(source='heartbeat')['items']
    assert len(rows)==1 and rows[0]['status']==(status or 'unexpected')
    assert rows[0]['action_type']=='heartbeat_check'


@pytest.mark.parametrize('kind',['comment','post'])
@pytest.mark.parametrize('status',['ok','empty','skipped','unknown'])
def test_enabled_action_status_is_recorded_without_real_publication(loop,kind,status):
    result={'status':status,'checked_at':100000,'candidate_id':'fixture-candidate','draft_id':'fixture-draft','reason':'fixture reason','send_result':{'remote_result':{'comment':{'id':'fixture-comment'},'post':{'id':'fixture-post','title':'Fixture'},'message':'fixture result'}}}
    target=loop.comment if kind=='comment' else loop.post;target.return_value=result
    value=loop.make(autonomous_comments_enabled=kind=='comment',autonomous_posts_enabled=kind=='post',autonomous_profile='fixture',autonomous_post_profile='fixture')
    value.run_once();assert target.call_count==1
    other=loop.post if kind=='comment' else loop.comment;assert not other.called
    source='autonomous_comment' if kind=='comment' else 'autonomous_post'
    rows=actions.list_autonomous_actions(source=source)['items']
    if status=='unknown':assert rows==[] and loop.logger.warn.called
    else:
        assert len(rows)==1 and rows[0]['status']==status and rows[0]['profile']=='fixture'
        assert rows[0]['action_type']==(kind+'_sent' if status=='ok' else kind+'_tick')


def test_comment_failure_does_not_block_an_independent_enabled_post_tick(loop):
    loop.comment.side_effect=RuntimeError('fixture comment unavailable')
    loop.post.return_value={'status':'empty','checked_at':100000,'reason':'no fixture draft'}
    value=loop.make(autonomous_comments_enabled=True,autonomous_posts_enabled=True)
    value.run_once()
    assert loop.comment.called and loop.post.called
    comment=actions.list_autonomous_actions(source='autonomous_comment')['items'][0]
    assert comment['status']=='error' and comment['details']['error']=='fixture comment unavailable'
    assert actions.list_autonomous_actions(source='autonomous_post')['items'][0]['status']=='empty'


def test_post_failure_is_persisted_with_action_context(loop):
    loop.post.side_effect=RuntimeError('fixture post unavailable')
    value=loop.make(autonomous_posts_enabled=True,autonomous_post_profile='fixture')
    value.run_once()
    row=actions.list_autonomous_actions(source='autonomous_post',status='error')['items'][0]
    assert row['profile']=='fixture' and row['details']=={'error':'fixture post unavailable'}


def test_loop_error_is_logged_and_stop_flag_breaks_wait_without_spinning(loop,monkeypatch):
    value=loop.make(loop_interval_s=3);value.running=True
    value.run_once=Mock(side_effect=RuntimeError('fixture heartbeat unavailable'))
    waits=[]
    def sleep(seconds):waits.append(seconds);value.running=False
    monkeypatch.setattr(heartbeat,'time',SimpleNamespace(time=lambda:100000,sleep=sleep))
    value._run_loop()
    assert value.run_once.call_count==1 and waits==[1] and loop.logger.error.called


def test_action_summary_counts_real_owned_rows_and_keeps_sent_comment_metadata(loop):
    value=loop.make(autonomous_comments_enabled=True,autonomous_profile='fixture')
    loop.comment.return_value={'status':'ok','checked_at':100000,'candidate_id':'fixture-candidate','send_result':{'remote_result':{'comment':{'id':'fixture-comment'},'message':'fixture message'}}}
    value.run_once()
    loop.comment.return_value={'status':'skipped','checked_at':100000,'reason':'fixture cooldown'};value.run_once()
    summary=actions.summarize_autonomous_actions(hours=1,sent_limit=1)
    assert summary['status']=='ok' and summary['window_hours']==1
    assert summary['sent_comments'][0]['remote_comment_id']=='fixture-comment'
    assert summary['autonomous_tick_breakdown']==[{'status':'skipped','reason':'fixture cooldown','count':1}]
    assert actions.list_autonomous_actions(action_type='heartbeat_check',limit=1)['count']==1
