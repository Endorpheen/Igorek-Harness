"""Terminal ownership, history and notification contracts without native I/O."""
import io
from types import SimpleNamespace

import pytest

from factory.core.tui import tui as module


@pytest.fixture
def owned_output(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(module, 'sys', SimpleNamespace(stdout=output))
    monkeypatch.setattr(module, 'shutil', SimpleNamespace(get_terminal_size=lambda fallback: SimpleNamespace(lines=10, columns=40)))
    return output


@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('callback_fails', [False, True])
def test_external_callback_always_releases_terminal_ownership(monkeypatch, owned_output, alternate, callback_fails):
    view = module.Tui()
    order = []
    view.alt_screen_active.store(alternate)
    monkeypatch.setattr(module, 'set_modes', lambda: order.append('set_modes'))
    monkeypatch.setattr(module, 'flush_terminal_input_buffer', lambda: order.append('flush'))
    def callback():
        assert view.event_broker.is_paused() and not view.is_alt_screen_active()
        order.append('callback')
        if callback_fails:
            raise ValueError('owned callback error')
        return 'owned result'
    mode = SimpleNamespace(restore=lambda: order.append('restore'))
    if callback_fails:
        with pytest.raises(ValueError, match='owned callback error'):
            view.with_restored(mode, callback)
    else:
        assert view.with_restored(mode, callback) == 'owned result'
    assert order == ['restore', 'callback', 'set_modes', 'flush']
    assert not view.event_broker.is_paused() and view.is_alt_screen_active() is alternate
    raw = owned_output.getvalue()
    assert raw == (module.ANSI_ALT_SCREEN_LEAVE + module.ANSI_ALT_SCREEN_ENTER if alternate else '')


@pytest.mark.parametrize('broken_stage', ['restore', 'set_modes'])
def test_best_effort_restore_failure_does_not_skip_callback_or_event_resume(monkeypatch, owned_output, broken_stage):
    view = module.Tui()
    calls = []
    def stage(name):
        calls.append(name)
        if name == broken_stage:
            raise OSError('owned restoration failure')
    monkeypatch.setattr(module, 'set_modes', lambda: stage('set_modes'))
    monkeypatch.setattr(module, 'flush_terminal_input_buffer', lambda: stage('flush'))
    assert view.with_restored(SimpleNamespace(restore=lambda: stage('restore')), lambda: stage('callback')) is None
    assert calls == ['restore', 'callback', 'set_modes', 'flush'] and not view.event_broker.is_paused()


def test_history_flush_is_ordered_single_use_and_clear_discards_both_queues(owned_output):
    view = module.Tui()
    assert not view.flush_pending_history_lines() and owned_output.getvalue() == ''
    view.add_history_line('first')
    view.add_history_line('второй 🐙')
    assert view.flush_pending_history_lines()
    assert owned_output.getvalue() == '\r\nfirst\r\nвторой 🐙\r\n'
    assert not view.flush_pending_history_lines()
    view.add_history_line('discard')
    lines = ['deferred']
    view.insert_history_lines_with_wrap_policy(lines, 'owned wrap')
    lines.append('must not mutate queued batch')
    assert view.pending_history_lines[0].lines == ['deferred']
    assert view.pending_history_lines[0].wrap_policy == 'owned wrap'
    view.clear_pending_history_lines()
    assert view.pending_history_lines == view.pending_history_lines_legacy == []


@pytest.mark.parametrize('zellij', [False, True])
def test_viewport_clamps_height_and_scrolls_only_on_overflow(owned_output, zellij):
    view = module.Tui()
    view.is_zellij = zellij
    view.viewport.y = 8
    assert view.update_inline_viewport(7)
    assert (view.viewport.y, view.viewport.width, view.viewport.height) == (3, 40, 7)
    if zellij:
        assert owned_output.getvalue() == '\x1b[9;0H' + '\n' * 5
    else:
        assert owned_output.getvalue() == '\x1b[1;10r' + '\x1bM' * 5 + '\x1b[r'
    owned_output.seek(0)
    owned_output.truncate(0)
    assert not view.update_inline_viewport(7) and owned_output.getvalue() == ''
    view.viewport.y = 0
    assert not view.update_inline_viewport(100) and view.viewport.height == 10


@pytest.mark.parametrize('condition,focused,expected', [
    (module.NotificationConditionKind.Never, False, False),
    (module.NotificationConditionKind.Unfocused, False, True),
    (module.NotificationConditionKind.Always, True, True),
])
def test_notification_policy_gates_delivery(condition, focused, expected):
    view = module.Tui()
    calls = []
    backend = SimpleNamespace(notify=calls.append)
    view.set_notification_settings(backend, module.NotificationCondition(condition))
    view.terminal_focused.store(focused)
    assert view.notify('owned message') is expected
    assert calls == (['owned message'] if expected else [])
    assert view.notification_backend is backend


def test_missing_or_failed_notification_backend_is_safe_and_not_retried():
    view = module.Tui()
    view.terminal_focused.store(False)
    assert not view.notify('none')
    view.notification_backend = object()
    assert not view.notify('missing method')
    calls = []
    def broken(message):
        calls.append(message)
        raise OSError('owned notification failure')
    view.notification_backend = SimpleNamespace(notify=broken)
    assert not view.notify('first') and view.notification_backend is None
    assert not view.notify('second') and calls == ['first']
