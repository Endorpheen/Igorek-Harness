"""Subagent cancellation markers never follow caller-controlled paths."""
from factory.core.subagents import SubagentReporter, cancel_requested


def test_cancel_rejects_traversal_and_linked_directories(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    reporter = SubagentReporter(artifact_workdir=work)
    assert not reporter.request_cancel('../../outside/escaped')
    (work / 'subagents_cancel').symlink_to(outside, target_is_directory=True)
    assert not reporter.request_cancel('sub_fixture')
    assert not list(outside.iterdir())


def test_cancel_rejects_final_links_and_preserves_regular_markers(tmp_path):
    outside = tmp_path / 'outside.txt'
    outside.write_text('unchanged fixture')
    directory = tmp_path / 'subagents_cancel'
    directory.mkdir()
    (directory / 'sub_link').symlink_to(outside)
    reporter = SubagentReporter(artifact_workdir=tmp_path)
    assert not reporter.request_cancel('sub_link')
    assert not cancel_requested(tmp_path, 'sub_link')
    assert outside.read_text() == 'unchanged fixture'
    assert reporter.request_cancel('sub_normal')
    assert cancel_requested(tmp_path, 'sub_normal')
