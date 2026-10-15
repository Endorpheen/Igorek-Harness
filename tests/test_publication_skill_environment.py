"""Skill instructions never interpolate ambient environment values into prompts."""
from factory.core.skills_manager import Skill, SkillsManager


def test_selected_skill_keeps_variable_references_without_exposing_values(tmp_path, monkeypatch):
    source = tmp_path / 'SKILL.md'
    source.write_text('Use $TAVILY_API_KEY and ${FIXTURE_PRIVATE_VALUE} in an authorized tool.\n')
    skill = Skill(skill_id='fixture', name='Fixture', body_path=str(source), triggers=['fixture'])
    manager = SkillsManager(skills_dirs=[])
    monkeypatch.setattr(manager, 'find_relevant_skills', lambda *args, **kwargs: [skill])
    monkeypatch.setattr(manager, 'get_available_skills_summary', lambda **kwargs: '')
    monkeypatch.setattr(manager, '_record_skill_selection', lambda *args: None)
    monkeypatch.setenv('TAVILY_API_KEY', 'synthetic-ambient-canary')
    monkeypatch.setenv('FIXTURE_PRIVATE_VALUE', 'synthetic-private-canary')
    prompt, environment = manager.build_skill_runtime_context('fixture')
    assert '$TAVILY_API_KEY' in prompt
    assert '${FIXTURE_PRIVATE_VALUE}' in prompt
    assert 'synthetic-ambient-canary' not in prompt
    assert 'synthetic-private-canary' not in prompt
    assert environment == {}
