"""Command routing and delivery summaries with fake Telegram/model transports."""
from unittest.mock import Mock

import pytest

from factory.core import telegram_bridge as t,smarthelp
from test_telegram_workflow_contracts import bridge as bridge,session as session,sent


@pytest.mark.parametrize('command,field,value',[
    ('/provider openai','provider_override','openai'),
    ('/PROVIDER@TestBot openrouter','provider_override','openrouter'),
    ('/model selected-fixture','model_override','selected-fixture'),
    ('/profile local','profile_override','local'),
])
def test_successful_selection_commands_update_only_session_state(bridge,session,monkeypatch,command,field,value):
    forward=Mock();monkeypatch.setattr(bridge,'_forward_user_message',forward)
    bridge._dispatch_message(session,command,attachments=[])
    assert getattr(session,field)==value
    assert 'переключ' in sent(bridge)
    assert getattr(bridge._load_sessions()['chat-1'],field)==value
    forward.assert_not_called()


@pytest.mark.parametrize('queue',[False,True])
def test_plugins_forwarding_requires_native_app_server_transport(bridge,session,monkeypatch,queue):
    monkeypatch.setattr(bridge,'_use_task_queue_transport',lambda state:queue)
    forward=Mock();monkeypatch.setattr(bridge,'_forward_user_message',forward)
    bridge._dispatch_message(session,'/plugins',attachments=[{'path':'fixture.txt'}])
    if queue:
        forward.assert_not_called();assert 'app-server/OpenAI' in sent(bridge)
    else:forward.assert_called_once_with(session,'/plugins',attachments=[])


@pytest.mark.parametrize('command,active',[
    ('on',True),('start',True),('enable',True),
    ('off',False),('exit',False),('stop',False),('disable',False),
])
def test_smarthelp_aliases_persist_the_same_mode(bridge,session,command,active):
    bridge._dispatch_message(session,'/smarthelp '+command,attachments=[])
    assert session.smarthelp_active is active
    assert bridge._load_sessions()['chat-1'].smarthelp_active is active


@pytest.mark.parametrize('active',[False,True])
def test_smarthelp_status_and_query_use_the_selected_mode_without_real_model(bridge,session,monkeypatch,active):
    session.smarthelp_active=active
    quick=Mock(return_value=('fixture quick reply','fixture log'))
    agent=Mock()
    monkeypatch.setattr(smarthelp,'smarthelp_query',quick)
    monkeypatch.setattr(bridge,'_send_smarthelp_agent_answer',agent)
    bridge._dispatch_message(session,'/smarthelp',attachments=[])
    assert ('включён' if active else 'выключен') in sent(bridge)
    bridge._dispatch_message(session,'/smarthelp fixture question',attachments=[])
    if active:
        agent.assert_called_once_with(session,'fixture question');quick.assert_not_called()
    else:
        quick.assert_called_once_with('fixture question');agent.assert_not_called()
        assert 'fixture quick reply' in sent(bridge)
    bridge.telegram.send_chat_action.assert_called_once_with('chat-1','typing')


@pytest.mark.parametrize('error',[smarthelp.SmarthelpError('fixture provider refused'),RuntimeError('fixture unavailable')])
@pytest.mark.parametrize('active',[False,True])
def test_smarthelp_failures_are_reported_without_forwarding_to_another_model(bridge,session,monkeypatch,error,active):
    session.smarthelp_active=active
    monkeypatch.setattr(smarthelp,'smarthelp_query',Mock(side_effect=error))
    monkeypatch.setattr(bridge,'_send_smarthelp_agent_answer',Mock(side_effect=error))
    forward=Mock();monkeypatch.setattr(bridge,'_forward_user_message',forward)
    bridge._dispatch_message(session,'/smarthelp fixture question',attachments=[])
    assert str(error) in sent(bridge)
    forward.assert_not_called()


def test_active_smarthelp_composes_attachments_and_reports_agent_failure(bridge,session,monkeypatch):
    session.smarthelp_active=True
    agent=Mock(side_effect=RuntimeError('fixture agent failure'))
    monkeypatch.setattr(bridge,'_send_smarthelp_agent_answer',agent)
    bridge._dispatch_message(session,'describe fixture',attachments=[{'path':'/owned/fixture.png','kind':'image','name':'fixture.png'}])
    assert 'fixture.png' in agent.call_args.args[1]
    assert 'изображение уже прикреплено' in agent.call_args.args[1]
    assert 'Ошибка SmartHelp Agent: fixture agent failure' in sent(bridge)


@pytest.mark.parametrize('models',[[],['fixture-model','other-fixture']])
def test_model_listing_is_useful_with_or_without_suggestions(bridge,session,monkeypatch,models):
    monkeypatch.setattr(t,'suggested_models_for_provider',lambda provider:models)
    text=bridge._models_list_text(session)
    if models:
        assert 'fixture-model (current)' in text and 'other-fixture' in text
    else:assert '/model <name>' in text


@pytest.mark.parametrize('queued',[[],[{'server_name':'fixture-server','reason':''}],[{'server_name':'fixture-server','reason':'fixture reason'}]])
def test_pending_mcp_or_native_approvals_are_listed_without_executing_them(bridge,session,monkeypatch,queued):
    monkeypatch.setattr(bridge,'_queued_mcp_approvals',lambda state:queued)
    session.pending_approvals=[t.PendingApproval('approval-fixture','command',{},'fixture command','/owned/file','turn')]
    bridge._send_pending_approvals(session)
    text=sent(bridge)
    if queued:assert 'fixture-server' in text
    else:assert 'fixture command -> /owned/file' in text


def test_turn_summary_lists_approvals_and_handles_empty_output(bridge,session):
    approval=t.PendingApproval('approval-fixture','command',{},'fixture approval','','turn')
    bridge._send_turn_outcome('chat-1',{'status':'waiting_on_approval','approvals':[approval,{'unsupported':'shape'}]})
    assert 'fixture approval' in sent(bridge) and '/approve_session' in sent(bridge)
    bridge._send_turn_outcome('chat-1',{'status':'completed'})
    assert 'без текстового вывода' in sent(bridge)


def test_turn_summary_keeps_memory_notice_separate_from_answer(bridge,session):
    bridge.memory_manager.review_turn.return_value=[{'kind':'fixture'}]
    bridge.memory_manager.format_memory_update_message.return_value='fixture memory notice'
    bridge._send_turn_outcome('chat-1',{'status':'completed','messages':['first fixture answer','second fixture answer']})
    text=sent(bridge)
    assert 'first fixture answer' in text and 'second fixture answer' in text and 'fixture memory notice' in text
    assert bridge.memory_manager.review_turn.call_count==2


@pytest.mark.parametrize('limit',[20,200,3500])
@pytest.mark.parametrize('separator',[' ','\n'])
def test_text_chunking_preserves_words_and_enforces_delivery_limit(limit,separator):
    text=separator.join(['one','two','six']*400)
    chunks=t._chunk_text(text,limit=limit)
    assert chunks and all(0<len(chunk)<=limit for chunk in chunks)
    assert ' '.join(chunks).split()==text.split()


@pytest.mark.parametrize('limit',[1,17,3500])
def test_unbroken_text_chunking_preserves_every_character(limit):
    text='я🐙'*4000
    chunks=t._chunk_text(text,limit=limit)
    assert ''.join(chunks)==text and all(len(chunk)<=limit for chunk in chunks)


def test_custom_emojis_are_only_injected_outside_code(monkeypatch):
    monkeypatch.setattr(t,'_get_custom_emoji_map',lambda:{'🐙':'fixture-emoji'})
    result=t._inject_custom_emojis('🐙 <code>🐙</code> <pre><code class="language-python">🐙</code></pre>')
    assert result.count('<tg-emoji')==1
    assert '<code>🐙</code>' in result and 'language-python">🐙' in result
