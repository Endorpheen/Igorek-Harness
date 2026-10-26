"""Composer interactions change drafts without silently submitting selections."""

from unittest.mock import Mock

import pytest

from factory.core.tui.bottom_pane.chat_composer import ActivePopup, ChatComposer
from factory.core.tui.bottom_pane.footer import FooterMode
from factory.core.tui.render.renderable import Rect


@pytest.fixture
def composer():
    value = ChatComposer()
    value.file_popup._all_files = ['alpha.py', 'beta.py', 'nested/report.txt']
    value.skill_popup.set_skills([('alpha', 'First fixture'), ('beta', 'Second fixture')])
    value.set_on_submit(Mock())
    value.set_on_queue(Mock())
    value.set_on_interrupt(Mock())
    return value


@pytest.mark.parametrize('key', ['enter', 'tab', 'c-c', 'left', 'right', 'backspace', 'delete', 'c-j', '/', '@', '$', 'x'])
def test_disabled_input_never_mutates_or_dispatches(composer, key):
    composer.set_text('fixture draft')
    composer.input_enabled = False
    composer.handle_key_event(key)
    assert composer.text == 'fixture draft' and composer.cursor == 13
    assert composer.active_popup == ActivePopup.NONE
    composer._on_submit.assert_not_called()
    composer._on_queue.assert_not_called()
    composer._on_interrupt.assert_not_called()


@pytest.mark.parametrize('initial,cursor,key,expected,new_cursor', [('one two  tail', 9, 'c-w', 'one tail', 4), ('Привет мир', 10, 'c-w', 'Привет ', 7), ('abCD\nEF', 2, 'c-k', 'ab\nEF', 2), ('ab\ncd', 4, 'c-a', 'ab\ncd', 3), ('ab\ncd', 1, 'c-e', 'ab\ncd', 2), ('ab\ncd', 4, 'left', 'ab\ncd', 3), ('ab\ncd', 3, 'right', 'ab\ncd', 4), ('ab\ncd', 1, 'delete', 'a\ncd', 1), ('ab\ncd', 4, 'backspace', 'ab\nd', 3), ('ab\ncd', 1, 'down', 'ab\ncd', 4)])
def test_editing_keys_preserve_unrelated_text_and_unicode(composer, initial, cursor, key, expected, new_cursor):
    composer.set_text(initial)
    composer.cursor = cursor
    composer.handle_key_event(key)
    assert (composer.text, composer.cursor) == (expected, new_cursor)
    composer._on_submit.assert_not_called()
    composer._on_queue.assert_not_called()


@pytest.mark.parametrize('trigger,query,selected', [('/', 'hel', '/help '), ('@', 'alpha', 'alpha.py'), ('$', 'alpha', '$alpha ')])
@pytest.mark.parametrize('accept', ['enter', 'tab'])
def test_popup_selection_only_inserts_choice_into_draft(composer, trigger, query, selected, accept):
    composer.handle_key_event(trigger)
    for char in query:
        composer.handle_key_event(char)
    composer.handle_key_event(accept)
    assert composer.text == selected and composer.active_popup == ActivePopup.NONE
    composer._on_submit.assert_not_called()
    composer._on_queue.assert_not_called()


@pytest.mark.parametrize('trigger', ['/', '@', '$'])
def test_popup_backspace_updates_filter_then_closes_at_trigger(composer, trigger):
    composer.handle_key_event(trigger)
    composer.handle_key_event('a')
    composer.handle_key_event('b')
    composer.handle_key_event('backspace')
    assert composer.text == trigger + 'a' and composer.active_popup != ActivePopup.NONE
    composer.handle_key_event('backspace')
    composer.handle_key_event('backspace')
    assert composer.text == '' and composer.active_popup == ActivePopup.NONE


@pytest.mark.parametrize('trigger', ['/', '@', '$'])
@pytest.mark.parametrize('direction', ['up', 'down', 'c-p', 'c-n'])
def test_popup_navigation_and_escape_preserve_draft(composer, trigger, direction):
    composer.handle_key_event(trigger)
    composer.handle_key_event(direction)
    composer.handle_key_event('escape')
    assert composer.text == trigger and composer.active_popup == ActivePopup.NONE
    composer._on_submit.assert_not_called()


@pytest.mark.parametrize('trigger', ['/', '@', '$'])
def test_no_matching_popup_item_does_not_submit_or_erase_query(composer, trigger):
    composer.handle_key_event(trigger)
    for char in 'does-not-exist':
        composer.handle_key_event(char)
    original = composer.text
    composer.handle_key_event('enter')
    assert composer.text == original and composer.active_popup == ActivePopup.NONE
    composer._on_submit.assert_not_called()


def test_remote_image_navigation_and_delete_leave_draft_untouched(composer):
    composer.set_text('keep this draft')
    composer.set_remote_images(['https://fixture.invalid/a', 'https://fixture.invalid/b'])
    composer._select_remote_image(1)
    composer.handle_key_event('up')
    assert composer._remote_image_selected == 1
    composer.handle_key_event('delete')
    assert composer.remote_image_urls == ['https://fixture.invalid/a'] and composer._remote_image_selected == 0
    composer.handle_key_event('backspace')
    assert composer.remote_image_urls == [] and composer._remote_image_selected == -1
    assert composer.text == 'keep this draft'
    composer._select_remote_image(1)
    assert composer._remote_image_selected == -1


def test_remote_image_down_returns_to_text_mode_and_render_labels_are_local(composer):
    composer.set_remote_images(['https://fixture.invalid/private-name'])
    composer._select_remote_image(1)
    lines = []
    composer.render(Rect(0, 0, 60, 8), lines)
    assert any('► [Image #1]' in line for line in lines)
    assert all('fixture.invalid' not in line for line in lines)
    composer.handle_key_event('down')
    composer.handle_key_event('x')
    assert composer.text == 'x'


@pytest.mark.parametrize('trigger,popup', [('/', ActivePopup.COMMAND), ('@', ActivePopup.FILE), ('$', ActivePopup.SKILL)])
def test_popup_layout_reserves_rows_and_cursor_uses_multiline_coordinates(composer, trigger, popup):
    composer.handle_key_event(trigger)
    areas = composer.layout_areas(Rect(4, 7, 60, 20))
    assert composer.active_popup == popup
    assert areas['popup'].height > 0 and areas['composer'].height + areas['popup'].height == 20
    lines = []
    composer.render(Rect(4, 7, 60, 20), lines)
    assert lines and composer.desired_height(60) > 4
    composer.set_text('ab\ncd')
    assert composer.cursor_pos(Rect(4, 7, 60, 20)) == (8, 9)


def test_footer_draft_escape_and_busy_render_states_do_not_dispatch(composer):
    composer.handle_key_event('escape')
    composer.update_footer_mode()
    assert composer.footer_props.mode == FooterMode.COMPOSER_EMPTY and composer.footer_props.esc_backtrack_hint
    composer.handle_key_event('escape')
    composer.set_text('draft')
    composer.quit_shortcut_key = 'ctrl+d'
    composer.update_footer_mode()
    assert composer.footer_props.mode == FooterMode.COMPOSER_HAS_DRAFT
    assert not composer.footer_props.esc_backtrack_hint and composer.footer_props.quit_shortcut_key == 'ctrl+d'
    composer.footer_props.is_task_running = True
    lines = []
    composer.render(Rect(0, 0, 60, 8), lines)
    assert lines == [] and composer.desired_height(60) == 0
    composer._on_submit.assert_not_called()
