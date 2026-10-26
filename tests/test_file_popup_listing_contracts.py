"""File mentions list files only and keep a selected long-list item visible."""

import subprocess
from unittest.mock import Mock

import pytest

from factory.core.tui.bottom_pane import file_search_popup as popup


def test_long_list_navigation_keeps_selected_file_in_visible_rows(tmp_path):
    view = popup.FileSearchPopup(str(tmp_path))
    view._all_files = [f'file-{i:02d}.py' for i in range(20)]
    view.on_composer_text_change('@')
    for _ in range(12):
        view.move_down()
    assert view.selected_file() == 'file-12.py'
    assert any('► file-12.py' in line for line in view.render_lines(40))
    view.move_up()
    assert any('► file-11.py' in line for line in view.render_lines(40))
    view.on_composer_text_change('@file-01')
    assert view.selected_file() == 'file-01.py' and view.scroll.offset == 0


@pytest.mark.parametrize('failure', [OSError('fixture rg unavailable'), subprocess.TimeoutExpired('rg', 5)])
def test_fallback_lists_only_files_and_hides_git_metadata(tmp_path, monkeypatch, failure):
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested/data.txt').write_text('fixture')
    (tmp_path / 'top.txt').write_text('fixture')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git/config').write_text('fixture not listed')
    monkeypatch.setattr(popup.subprocess, 'run', Mock(side_effect=failure))
    assert popup._list_repo_files(tmp_path) == ['nested/data.txt', 'top.txt']


def test_rg_listing_is_sorted_and_uses_only_the_given_directory(tmp_path, monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess(['rg'], 0, 'z.txt\na.txt\n', ''))
    monkeypatch.setattr(popup.subprocess, 'run', run)
    assert popup._list_repo_files(tmp_path) == ['a.txt', 'z.txt']
    assert run.call_args.kwargs['cwd'] == str(tmp_path) and run.call_args.kwargs['timeout'] == 5
    assert run.call_args.args[0] == ['rg', '--files', '--hidden', '--glob', '!.git']


def test_unavailable_both_listing_backends_produces_empty_result(tmp_path, monkeypatch):
    monkeypatch.setattr(popup.subprocess, 'run', Mock(side_effect=OSError('fixture')))
    monkeypatch.setattr(popup.glob, 'glob', Mock(side_effect=PermissionError('fixture')))
    assert popup._list_repo_files(tmp_path) == []


def test_popup_scanning_no_match_reset_and_narrow_labels(tmp_path):
    view = popup.FileSearchPopup(str(tmp_path))
    assert view.render_lines(40) == ['  scanning files...']
    view._all_files = ['directory/long-file-name.py']
    view.on_composer_text_change('@LONG')
    assert view.selected_file() == 'directory/long-file-name.py'
    assert any('…' in line for line in view.render_lines(15))
    view.on_composer_text_change('@absent')
    assert view.selected_file() is None and view.render_lines(40) == ['  no matches']
    view.move_up()
    view.move_down()
    view.on_composer_text_change('ordinary text')
    assert view.results == [] and view.query == '' and view.scroll.total == 0
