"""Comparison correctness and isolation boundaries for the changes workbench."""
import hashlib
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.core.checkpoint_manager import CheckpointManager
from factory.core.review_changes import ChangesReview, ReviewError, checkpoint_repositories, checkpoint_text
from factory.core.session_changes import TaskChangeTracker, load_task_changes


def git(root, *args):
    env={'PATH':os.environ['PATH'],'HOME':str(root),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','LC_ALL':'C.UTF-8'}
    return subprocess.check_output(['git',*args],cwd=root,env=env).decode().strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path,'init','-b','main');git(tmp_path,'config','user.name','Test');git(tmp_path,'config','user.email','test@example.invalid')
    (tmp_path/'a.py').write_text('one\ntwo\n');(tmp_path/'old.txt').write_text('old\n');(tmp_path/'gone.txt').write_text('gone\n')
    git(tmp_path,'add','a.py','old.txt','gone.txt');git(tmp_path,'commit','-m','baseline')
    return tmp_path


def review(root, mode='working', base=''):
    result=ChangesReview(root,session_files=[],repositories=[],include_untracked=True);result.prepare(mode,base);return result


def test_working_counts_diff_deletions_renames_untracked_and_staged(repository):
    root=repository;(root/'a.py').write_text('one\nTWO\nnew\n');(root/'gone.txt').unlink()
    git(root,'mv','old.txt','renamed.txt');(root/'new.txt').write_text('new\n')
    result=review(root);data=result.result();files={f['path']:f for f in data['files']}
    assert files['a.py']['additions']==2 and files['a.py']['deletions']==1
    assert files['gone.txt']['status']=='deleted' and files['gone.txt']['deletions']==1
    assert files['renamed.txt']['status']=='renamed' and files['renamed.txt']['old_path']=='old.txt'
    assert files['new.txt']['status']=='added'
    for path,entry in files.items():
        diff=result.result(path)
        assert (diff['additions'],diff['deletions'])==(entry['additions'],entry['deletions'])
    assert all(line['type']=='-' for h in result.result('gone.txt')['hunks'] for line in h['lines'])


def test_branch_uses_merge_base_and_validated_refs(repository):
    root=repository;git(root,'branch','target');(root/'a.py').write_text('committed\n');git(root,'add','a.py');git(root,'commit','-m','change')
    assert review(root).result()['files']==[]
    data=review(root,'branch','target').result();assert data['files'][0]['path']=='a.py';assert 'target' in data['base_label']
    with pytest.raises(ReviewError): review(root,'branch','--output=/tmp/file')
    with pytest.raises(ReviewError): review(root,'branch','HEAD~999')


def test_session_pins_first_task_baseline_and_avoids_summed_counts(tmp_path):
    root=tmp_path/'work';root.mkdir();data=tmp_path/'data'
    file=root/'a.txt';file.write_text('old\n');manager=CheckpointManager(data_root=str(data));assert manager.ensure_checkpoint(working_dir=str(root),reason='first')
    tracker=TaskChangeTracker(task_id='first',workdir=str(root),checkpoint_manager=manager,data_root=str(data));file.write_text('middle\n');tracker.record_write(str(file),'middle\n')
    record=load_task_changes('first',data_root=str(data));assert record['baseline_revision']
    manager.new_turn();assert manager.ensure_checkpoint(working_dir=str(root),reason='second');file.write_text('final\n')
    result=ChangesReview(root,session_files=[{'path':'a.txt','status':'modified','additions':19,'deletions':18}],repositories=checkpoint_repositories(root,data,None),baseline=record['baseline_revision']);result.prepare('session')
    assert result.result()['files'][0]['additions']==1
    diff=result.result('a.txt');assert any(line['text']=='old' for h in diff['hunks'] for line in h['lines'])


def test_old_history_uses_first_nonempty_checkpoint_with_visible_warning(tmp_path):
    root=tmp_path/'work';root.mkdir();(root/'a.txt').write_text('before\n');data=tmp_path/'data';manager=CheckpointManager(data_root=str(data));assert manager.ensure_checkpoint(working_dir=str(root),reason='first')
    (root/'a.txt').write_text('after\n');result=ChangesReview(root,session_files=[{'path':'a.txt','status':'modified'}],repositories=checkpoint_repositories(root,data,None));result.prepare('session')
    assert result.result()['warning'] and result.result()['files'][0]['deletions']==1


def test_guest_checkpoint_hash_is_rehydrated_on_host(tmp_path):
    root=tmp_path/'host-workspace';root.mkdir();(root/'a.txt').write_text('before\n');durable=root/'.igorek-host-data';manager=CheckpointManager(data_root=str(durable));assert manager.ensure_checkpoint(working_dir=str(root),reason='first')
    actual=manager._shadow_repo(str(root));guest=durable/'checkpoints'/hashlib.sha256(b'/workspace').hexdigest()[:16];actual.rename(guest)
    (root/'a.txt').write_text('after\n');repositories=checkpoint_repositories(root,tmp_path/'host-data',durable)
    available,old=checkpoint_text(root,'a.txt',repositories);assert available and old=='before\n'
    result=ChangesReview(root,session_files=[{'path':'a.txt','status':'modified'}],repositories=repositories);result.prepare('session');assert result.result('a.txt')['deletions']==1


def test_binary_large_unknown_and_paths_are_honest(repository,tmp_path):
    root=repository;(root/'binary').write_bytes(b'a\0b');(root/'large').write_bytes(b'x'*2_000_001)
    result=review(root);data=result.result();assert data['totals']['unknown']==2
    for path in ['../outside','/etc/passwd','.codex/config']:
        with pytest.raises(ReviewError): result.result(path)
    outside=tmp_path.parent/'outside-review.txt';outside.write_text('private');(root/'link').symlink_to(outside)
    assert 'link' not in [f['path'] for f in review(root).result()['files']]
    with pytest.raises(ReviewError): result.result('not-in-comparison')


def test_file_limits_and_runtime_exclusions(repository):
    root=repository
    for i in range(4): (root/f'new{i}').write_text('x\n')
    (root/'.igorek-host-data').mkdir();(root/'.igorek-host-data'/'private').write_text('secret')
    result=ChangesReview(root,session_files=[],repositories=[],excluded=['.igorek-host-data'],include_untracked=True);result.prepare('working')
    with patch('factory.core.review_changes.MAX_UNTRACKED',2):
        data=result.result();assert data['omitted']>=2
    assert not any(f['path'].startswith('.igorek-host-data') for f in result.result()['files'])


def test_without_baseline_does_not_invent_line_counts(tmp_path):
    result=ChangesReview(tmp_path,session_files=[{'path':'a.txt','status':'modified','additions':5,'deletions':3}],repositories=[]);result.prepare('session');data=result.result()
    assert data['files'][0]['additions'] is None and data['totals']['unknown']==1


def test_diff_header_like_source_lines_are_counted_and_preserved():
    from factory.core.session_changes import count_line_changes
    from factory.core.session_diff import build_diff_hunks
    old='-- old\n';new='++ new\n'
    assert count_line_changes(old,new)==(1,1)
    hunks,_=build_diff_hunks(old,new)
    assert hunks[0]['lines']==[{'type':'-','text':'-- old'},{'type':'+','text':'++ new'}]


def test_new_repository_shows_staged_and_untracked_additions(tmp_path):
    git(tmp_path,'init','-b','main')
    (tmp_path/'staged.py').write_text('x = 1\n');git(tmp_path,'add','staged.py')
    (tmp_path/'new.py').write_text('y = 2\n')
    data=review(tmp_path).result()
    assert data['totals']['files']==2 and data['totals']['additions']==2
    assert all(f['status']=='added' for f in data['files'])


def test_selected_diff_keeps_list_revision_after_a_commit(repository):
    root=repository;(root/'a.py').write_text('changed\n')
    listed=review(root).result();revision=listed['revision']
    git(root,'add','a.py');git(root,'commit','-m','change')
    (root/'a.py').write_text('changed\nnew\n')
    selected=review(root);selected.pin_revision(revision);diff=selected.result('a.py')
    assert diff['additions']==2 and diff['deletions']==2
    with pytest.raises(ReviewError): selected.pin_revision('HEAD')


def test_overview_reads_blobs_in_one_batch(repository):
    root=repository
    for i in range(25): (root/f'file{i}.py').write_text('before\n')
    git(root,'add',*[f'file{i}.py' for i in range(25)]);git(root,'commit','-m','many files')
    for i in range(25): (root/f'file{i}.py').write_text('after\n')
    from factory.core import review_changes as module
    result=review(root)
    with patch.object(module,'git_read',wraps=module.git_read) as reader:
        assert result.result()['totals']['files']==25
    assert sum(call.args[1][:2]==['cat-file','--batch'] for call in reader.call_args_list)==1


def test_review_never_executes_repository_clean_filters(repository):
    root=repository;(root/'.gitattributes').write_text('*.py filter=evil\n')
    git(root,'add','.gitattributes');git(root,'commit','-m','attributes')
    marker=root/'executed-filter'
    git(root,'config','filter.evil.clean',f"touch '{marker}'")
    git(root,'config','filter.evil.required','true')
    (root/'a.py').write_text('changed\n')
    result=review(root).result()
    assert not marker.exists()
    assert any(f['path']=='a.py' for f in result['files'])


def test_default_totals_exclude_untracked_dependencies_and_explicit_opt_in(repository):
    root=repository;(root/'a.py').write_text('change\n')
    deps=root/'.toolchain-android';deps.mkdir();(deps/'library.js').write_text('library\n'*100)
    default=ChangesReview(root,session_files=[],repositories=[]);default.prepare('branch','main');data=default.result()
    assert data['scope']=='tracked' and data['totals']['files']==1
    assert data['totals']['additions']==1 and data['excluded_untracked']==1
    assert not data['totals']['partial'] and data['omitted']==0
    included=review(root,'branch','main').result()
    assert included['scope']=='tracked_and_untracked' and included['totals']['additions']==101


def test_unavailable_counts_have_structural_reasons_and_partial_totals(repository):
    root=repository;(root/'binary').write_bytes(b'x\0y');(root/'large').write_bytes(b'x'*2_000_001)
    data=review(root).result()
    assert data['totals']['unavailable']=={'binary':1,'too_large':1}
    assert data['totals']['counted_files']==0 and data['totals']['partial']
    assert all(f['unavailable_reason'] in {'binary','too_large'} for f in data['files'])


def test_session_missing_baseline_and_limits_are_not_reported_as_complete(tmp_path):
    files=[{'path':'a.txt','status':'modified'},{'path':'b.txt','status':'modified'}]
    result=ChangesReview(tmp_path,session_files=files,repositories=[]);result.prepare('session')
    with patch('factory.core.review_changes.MAX_FILES',1): data=result.result()
    assert data['totals']['unavailable']=={'no_baseline':1}
    assert data['omitted_by_reason']=={'file_limit':1} and data['totals']['partial']
