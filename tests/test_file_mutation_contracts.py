"""Actual temporary file mutation, checkpoint ordering and error isolation."""
import json
from unittest.mock import Mock

import pytest

from factory.core import cc_backend_tools as tools


@pytest.fixture
def work(tmp_path,monkeypatch):
    monkeypatch.delenv('OBSIDIAN_VAULT_PATH',raising=False)
    root=tmp_path/'workspace';root.mkdir()
    return root


@pytest.mark.parametrize('suffix',['.json','.excalidraw'])
@pytest.mark.parametrize('content,expected_fixed',[
    ('{"name":"fixture"}',False),
    ('{\n// fixture comment\n"name":"Привет 🐙"\n}',True),
    ('{this remains invalid}',False),
])
def test_write_preserves_json_or_repairs_only_supported_comments(work,suffix,content,expected_fixed):
    path=work/('fixture'+suffix)
    result=tools._tool_write({'file_path':path.name,'content':content},str(work),'workspace',None)
    if expected_fixed:
        assert json.loads(path.read_text())=={'name':'Привет 🐙'}
        assert 'JSON нормализован' in result
    else:
        assert path.read_text()==content and 'JSON нормализован' not in result


@pytest.mark.parametrize('tracker_error',[False,True])
def test_checkpoint_captures_old_content_before_atomic_write_and_tracker_is_best_effort(work,tracker_error):
    path=work/'note.txt';path.write_text('old')
    before=[]
    manager=Mock()
    manager.ensure_checkpoint_for_path.side_effect=lambda **kwargs:before.append((path.read_text(),kwargs))
    tracker=Mock()
    if tracker_error:
        tracker.record_write.side_effect=RuntimeError('fixture tracker unavailable')
    result=tools._tool_write({'file_path':path.name,'content':'new 🐙'},str(work),'workspace',manager,tracker)
    assert before==[('old',{'target_path':str(path),'workdir':str(work),'reason':'before Write','authorized_root':str(work)})]
    assert path.read_text()=='new 🐙' and 'successfully' in result
    tracker.record_write.assert_called_once_with(str(path),'new 🐙')


@pytest.mark.parametrize('operation',['write','edit'])
def test_checkpoint_failure_stops_mutation_before_changing_bytes(work,operation):
    path=work/'note.txt';path.write_text('old')
    manager=Mock();manager.ensure_checkpoint_for_path.side_effect=RuntimeError('fixture checkpoint unavailable')
    with pytest.raises(RuntimeError,match='checkpoint unavailable'):
        if operation=='write':
            tools._tool_write({'file_path':path.name,'content':'new'},str(work),'workspace',manager)
        else:
            tools._tool_edit({'file_path':path.name,'old_string':'old','new_string':'new'},str(work),'workspace',manager)
    assert path.read_bytes()==b'old'


@pytest.mark.parametrize('replace_all,expected',[(False,'new old old'),(True,'new new new')])
@pytest.mark.parametrize('tracker_error',[False,True])
def test_edit_records_exact_replacement_and_respects_replace_all(work,replace_all,expected,tracker_error):
    path=work/'note.txt';path.write_text('old old old')
    before=[];manager=Mock();manager.ensure_checkpoint_for_path.side_effect=lambda **kw:before.append(path.read_text())
    tracker=Mock()
    if tracker_error:
        tracker.record_edit.side_effect=RuntimeError('fixture tracker unavailable')
    result=tools._tool_edit({'file_path':path.name,'old_string':'old','new_string':'new','replace_all':replace_all},str(work),'workspace',manager,tracker)
    assert before==['old old old'] and path.read_text()==expected and 'successfully' in result
    tracker.record_edit.assert_called_once_with(str(path),'old','new')


@pytest.mark.parametrize('old,new,fragment',[(None,'new','cannot be empty'),('', 'new','cannot be empty'),('   ','new','cannot be empty'),('old',None,'must be a string'),('old',8,'must be a string'),('old','old','must differ'),('absent','new','not found')])
def test_invalid_edits_leave_file_unchanged_and_do_not_record_success(work,old,new,fragment):
    path=work/'note.txt';path.write_text('old')
    tracker=Mock()
    result=tools._tool_edit({'file_path':path.name,'old_string':old,'new_string':new},str(work),'workspace',None,tracker)
    assert fragment in result and path.read_text()=='old' and not tracker.record_edit.called


def test_edit_can_delete_selected_text_but_does_not_create_a_missing_file(work):
    path=work/'note.txt';path.write_text('keep remove keep')
    assert 'successfully' in tools._tool_edit({'file_path':path.name,'old_string':' remove','new_string':''},str(work),'workspace',None)
    assert path.read_text()=='keep keep'
    result=tools._tool_edit({'file_path':'missing.txt','old_string':'x','new_string':'y'},str(work),'workspace',None)
    assert result.startswith('File not found:') and not (work/'missing.txt').exists()


def test_configured_vault_is_a_distinct_authorized_checkpoint_root(work,monkeypatch):
    vault=work.parent/'vault';vault.mkdir();monkeypatch.setenv('OBSIDIAN_VAULT_PATH',str(vault))
    manager=Mock();target=vault/'note.md'
    tools._tool_write({'file_path':str(target),'content':'fixture note'},str(work),'workspace',manager)
    assert target.read_text()=='fixture note'
    assert manager.ensure_checkpoint_for_path.call_args.kwargs['authorized_root']==str(vault)
    result=tools._tool_write({'file_path':'local.md','content':'local note'},str(work),'workspace',None)
    assert str(vault/'local.md') in result and (work/'local.md').read_text()=='local note'


@pytest.mark.parametrize('extension,content,fragment',[
    ('.png',b'not really png','изображением'),('.bin',b'abc\x00def','бинарным'),
    ('.txt',b'\x01'*100,'бинарным'),
])
def test_non_text_read_does_not_render_binary_as_assistant_text(work,extension,content,fragment):
    path=work/('fixture'+extension);path.write_bytes(content)
    assert fragment in tools._tool_read({'file_path':path.name},str(work),'workspace')
    assert path.read_bytes()==content


def test_text_read_handles_invalid_utf8_and_an_empty_file_without_mutation(work):
    path=work/'note.txt';path.write_bytes(b'a\xffb\n')
    assert tools._tool_read({'file_path':path.name},str(work),'workspace')=='a\ufffdb\n'
    path.write_text('')
    assert tools._tool_read({'file_path':path.name},str(work),'workspace')==''


@pytest.mark.parametrize('pattern',['needle','-leading','Привет'])
def test_actual_ripgrep_treats_pattern_as_data_and_searches_only_temporary_workspace(work,pattern):
    path=work/'note.txt';path.write_text('first\nneedle\n-leading\nПривет\n')
    result=tools._tool_grep({'pattern':pattern},str(work),'workspace')
    assert str(path) in result and pattern in result
    assert tools._tool_grep({'pattern':'absent'},str(work),'workspace')=='No matches found.'


@pytest.mark.parametrize('failure',[FileNotFoundError('rg missing'),RuntimeError('fixture unavailable')])
def test_grep_reports_execution_failure_without_a_successful_result(work,monkeypatch,failure):
    monkeypatch.setattr(tools.subprocess,'run',Mock(side_effect=failure))
    result=tools._tool_grep({'pattern':'fixture'},str(work),'workspace')
    assert result==('ripgrep (rg) not found' if isinstance(failure,FileNotFoundError) else 'Error: fixture unavailable')
