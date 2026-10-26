"""Skill lifecycle commands against owned package directories and SQLite state."""
import json
from pathlib import Path

import pytest

from factory.core import cli_skills as cli
from factory.core import skills_manager as manager_module
from factory.core.schema import open_db


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    db=tmp_path/'state/factory.db';db.parent.mkdir()
    monkeypatch.setattr(manager_module,'DB_PATH',str(db))
    monkeypatch.setattr(cli,'get_db_path',lambda:str(db))
    manager=manager_module.SkillsManager(skills_dirs=[str(tmp_path/'skills')])
    monkeypatch.setattr(cli,'skills_manager',manager)
    monkeypatch.setattr(cli,'get_data_root',lambda:tmp_path)
    return tmp_path,db,manager


@pytest.mark.parametrize('name,expected',[(' Fixture Name ','fixture-name'),('test___name','test-name'),('A--B','a-b'),('one.2','one-2')])
def test_skill_names_become_portable_package_names(name,expected):
    assert cli.normalize_skill_name(name)==expected


@pytest.mark.parametrize('name',['','  ','---','🐙','a'*64])
def test_invalid_skill_name_is_rejected_before_creating_a_package(runtime,name):
    root,_,_=runtime
    with pytest.raises(ValueError):cli.init_skill(name,data_dir=str(root))
    assert not (root/'skills').exists()


def test_init_list_disable_enable_preserves_package_files_and_state(runtime):
    root,_,_=runtime
    result=cli.init_skill('Fixture Skill',data_dir=str(root),description='Useful fixture workflow',triggers=['fixture','deterministic'],backends=['claude','cc-backend'],with_scripts=True,with_references=True,with_assets=True)
    package=Path(result['skill_dir']);original=(package/'SKILL.md').read_bytes()
    assert all((package/name).is_dir() for name in ['scripts','references','assets'])
    listed=cli.list_skills(data_dir=str(root))
    assert listed['count']==listed['active_count']==1 and listed['status']=='ok'
    identity=listed['skills'][0]['skill_id']
    disabled=cli.set_skill_enabled(identity,enabled=False,data_dir=str(root))
    assert disabled['enabled'] is False and cli.list_skills(data_dir=str(root))['active_count']==0
    enabled=cli.set_skill_enabled(identity,enabled=True,data_dir=str(root))
    assert enabled['enabled'] is True and cli.list_skills(data_dir=str(root))['active_count']==1
    assert (package/'SKILL.md').read_bytes()==original


def test_reinitialization_requires_force_and_does_not_delete_resource_files(runtime):
    root,_,_=runtime
    first=cli.init_skill('fixture',data_dir=str(root),description='First')
    package=Path(first['skill_dir']);(package/'notes.txt').write_text('keep')
    with pytest.raises(FileExistsError):cli.init_skill('fixture',data_dir=str(root),description='Second')
    assert 'First' in (package/'SKILL.md').read_text()
    cli.init_skill('fixture',data_dir=str(root),description='Second',force=True)
    assert 'Second' in (package/'SKILL.md').read_text() and (package/'notes.txt').read_text()=='keep'


def test_explicit_package_root_does_not_write_default_runtime(runtime):
    root,_,_=runtime
    custom=root/'custom'
    result=cli.init_skill('fixture',data_dir=str(root),path=str(custom))
    assert Path(result['skill_dir'])==custom/'fixture' and not (root/'skills').exists()
    assert cli.list_skills(path=str(custom))['count']==1


def test_unknown_skill_cannot_change_another_skills_state(runtime):
    root,_,_=runtime
    cli.init_skill('fixture',data_dir=str(root),description='Fixture skill')
    with pytest.raises(ValueError,match='not found'):
        cli.set_skill_enabled('missing',enabled=False,data_dir=str(root))
    assert cli.list_skills(data_dir=str(root))['active_count']==1


def test_activation_preserves_selection_preferences_in_real_settings_table(runtime):
    root,_,manager=runtime
    manager.save_settings(str(root/'old'),auto_load=False,smart_selection=False)
    result=cli.activate_local_skills(data_dir=str(root))
    saved=manager.load_settings()
    assert result['status']=='ok' and Path(saved['skills_path'])==root/'skills'
    assert saved['skills_auto_load']==saved['skills_smart_selection']=='false'


@pytest.mark.parametrize('configured',[None,'not JSON','[]','["  "]'])
def test_bad_multiroot_settings_fall_back_to_legacy_root(runtime,configured):
    root,_,manager=runtime
    settings={'skills_path':str(root/'legacy')}
    if configured is not None:settings['skills_dirs']=configured
    manager.load_settings=lambda:settings
    directories=cli._resolve_skills_dirs()
    assert directories==([] if configured=='["  "]' else [str(root/'legacy')])
    assert cli._resolve_skills_root()==root/'legacy'


def test_multiroot_settings_are_loaded_without_losing_explicit_path_precedence(runtime):
    root,_,manager=runtime
    manager.load_settings=lambda:{'skills_dirs':json.dumps([str(root/'one'),' ',str(root/'two')]),'skills_path':str(root/'legacy')}
    assert cli._resolve_skills_dirs()==[str(root/'one'),str(root/'two')]
    assert cli._resolve_skills_dirs(path=str(root/'explicit'))==[str(root/'explicit')]
    assert cli._resolve_skills_dirs(data_dir=str(root/'data'))==[str(root/'data/skills')]


def test_usage_summary_keeps_unknown_ids_and_orders_real_usage_rows(runtime):
    root,db,_=runtime
    with open_db(str(db)) as connection:
        connection.execute('INSERT INTO skills_usage(skill_id,selected_count,last_selected_at,updated_at) VALUES(?,?,?,?)',('older',1,12,12))
        connection.execute('INSERT INTO skills_usage(skill_id,selected_count,last_selected_at,updated_at) VALUES(?,?,?,?)',('frequent',4,20,20))
        connection.commit()
    result=cli.skills_usage_summary(data_dir=str(root),limit=1)
    assert result['count']==1 and result['items']==[{'skill_id':'frequent','name':'frequent','enabled':None,'selected_count':4,'last_selected_at':20,'updated_at':20}]
    assert cli.skills_usage_summary(data_dir=str(root),limit=0)['count']==1
