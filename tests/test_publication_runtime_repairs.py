"""Focused behavior checks for executable errors found during publication prep."""
import pytest


def test_acp_overlapping_tool_events_keep_distinct_ids_and_completion_order(monkeypatch):
    from factory.core.acp_bridge import events
    updates = []
    monkeypatch.setattr(events, '_send_update', lambda connection, session, loop, update: updates.append(update))
    monkeypatch.setattr(events, '_build_tool_start', lambda identifier, name, args: ('start', identifier, args))
    monkeypatch.setattr(events, '_build_tool_complete', lambda identifier, name, result: ('complete', identifier, result))
    ids = {}
    callback = events.make_tool_progress_cb(None, 'session-fixture', None, ids)
    callback('tool.started', name='Read', args='{"file_path":"first"}')
    callback('tool.started', name='Read', args={'file_path': 'second'})
    callback('tool.completed', name='Read', result='first result')
    callback('tool.completed', name='Read', result='second result')
    assert updates[0][1] != updates[1][1]
    assert [row[1] for row in updates[:2]] == [row[1] for row in updates[2:]]
    assert not ids


def test_theme_override_works_before_and_after_syntax_theme_initialization(monkeypatch, tmp_path):
    from factory.core.tui.render import highlight
    monkeypatch.setattr(highlight, '_theme', None)
    monkeypatch.setattr(highlight, '_theme_override', None)
    monkeypatch.setattr(highlight, '_codex_home', None)
    monkeypatch.setattr(highlight, '_active_theme_name', None)
    assert highlight.set_theme_override('catppuccin-latte', tmp_path) is None
    theme = highlight.theme_lock()
    assert theme.name == 'catppuccin-latte'
    assert highlight.set_theme_override('catppuccin-latte', tmp_path) is None
    assert highlight.theme_lock().name == theme.name


@pytest.mark.parametrize('explicit', [False, True])
def test_comment_draft_records_actual_selected_persona_without_sending(monkeypatch, explicit):
    from factory.core import boltbook_client as client
    key = 'witty'
    item = {'kind': 'comment', 'decision': {'action': 'draft_comment', 'score': 7}}
    inbox = {'items': [item]}
    captured = []
    monkeypatch.setattr(client, 'load_inbox_state', lambda: inbox)
    monkeypatch.setattr(client, 'load_persona_state', lambda: {'mode': 'fixed', 'fixed': key, 'rotation': [key]})
    monkeypatch.setattr(client, '_build_comment_generation_prompt', lambda value, persona: captured.append(persona) or 'fixture prompt')
    monkeypatch.setattr(client, '_run_profile_prompt_direct', lambda **kwargs: 'draft fixture')
    monkeypatch.setattr(client, '_write_json', lambda *args, **kwargs: None)
    result = client.generate_comment_drafts(persona_key=key if explicit else None)
    assert result['generated_count'] == 1
    assert item['draft']['persona_key'] == captured[0]['key'] == key
    assert item['draft']['status'] == 'generated'
