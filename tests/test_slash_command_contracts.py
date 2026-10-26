"""Published slash-command metadata keeps aliases and availability consistent."""

from types import SimpleNamespace

import pytest

from factory.core.tui import slash_command as commands


@pytest.mark.parametrize('alias,canonical', [('pet', 'pets'), ('clean', 'stop'), ('setup-default-sandbox', 'elevate-sandbox'), ('sandbox-add-read-dir', 'sandbox-add-read-dir'), ('approve', 'approve'), ('subagents', 'subagents')])
def test_legacy_aliases_resolve_to_canonical_commands(alias, canonical):
    assert commands.SlashCommand.from_str(alias).command() == canonical


@pytest.mark.parametrize('name', ['', '/model', 'MODEL', 'not-a-command', 'model '])
def test_noncanonical_unknown_tokens_are_not_guessed(name):
    with pytest.raises(ValueError, match='unknown slash command'):
        commands.SlashCommand.from_str(name)


def test_every_published_command_round_trips_and_has_a_nonempty_description():
    rows = commands.built_in_slash_commands()
    assert len({name for name, _ in rows}) == len(rows)
    assert [item for _, item in rows[:3]] == [commands.SlashCommand.Model, commands.SlashCommand.Ide, commands.SlashCommand.Permissions]
    for name, item in rows:
        assert commands.SlashCommand.from_str(name) is item
        assert isinstance(item.description(), str) and item.description().strip()


@pytest.mark.parametrize('platform,read_root_visible,copy_visible', [('linux', False, True), ('win32', True, True), ('android', False, False)])
def test_platform_visibility_is_a_declarative_menu_filter(monkeypatch, platform, read_root_visible, copy_visible):
    monkeypatch.setattr(commands, 'sys', SimpleNamespace(platform=platform))
    names = {name for name, _ in commands.built_in_slash_commands()}
    assert ('sandbox-add-read-dir' in names) is read_root_visible
    assert ('copy' in names) is copy_visible
    assert 'rollout' not in names and 'test-approval' not in names


def test_debug_commands_require_an_explicit_debug_opt_in(monkeypatch):
    assert not commands.SlashCommand.Rollout.is_visible()
    monkeypatch.setattr(commands.SlashCommand.Rollout, '_is_debug', True, raising=False)
    assert commands.SlashCommand.Rollout.is_visible()
    assert 'rollout' in dict(commands.built_in_slash_commands())
    assert not commands.SlashCommand.TestApproval.is_visible()


@pytest.mark.parametrize('name,inline,side,busy', [('review', True, False, False), ('model', False, False, False), ('copy', False, True, True), ('diff', False, True, True), ('goal', True, False, True), ('quit', False, False, True), ('new', False, False, False)])
def test_menu_capabilities_match_supported_command_contexts(name, inline, side, busy):
    item = commands.SlashCommand.from_str(name)
    assert item.supports_inline_args() is inline
    assert item.available_in_side_conversation() is side
    assert item.available_during_task() is busy
