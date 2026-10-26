"""Search history state derives from actual tool exchanges without network calls."""
import json
from types import SimpleNamespace

import pytest

from factory.api_server_core import workflow_engine as w
from factory.api_server_core.anthropic_messages import strip_system_reminder


def request(rows, final=None):
    messages = [SimpleNamespace(role='user',content='fixture research')]
    for i,(name,command,result,error) in enumerate(rows):
        messages.append(SimpleNamespace(role='assistant',content=[{'type':'tool_use','id':str(i),'name':name,'input':{'command':command}}]))
        messages.append(SimpleNamespace(role='user',content=[{'type':'tool_result','tool_use_id':str(i),'content':result,'is_error':error}]))
    if final is not None:
        messages.append(SimpleNamespace(role='assistant',content=final))
    return SimpleNamespace(messages=messages)


def state(rows, final=None):
    return w.tavily_research_state(request(rows,final),strip_system_reminder)


SEARCH = 'curl https://api.tavily.com/search -d fixture'
URLS = ['https://example.test/one','https://example.test/two']
PAYLOAD = json.dumps({'results':[{'url':url,'title':url.rsplit('/',1)[-1]} for url in URLS]})


@pytest.mark.parametrize('rows', [[], [('Read','irrelevant','file',False)], [('Bash','echo fixture','fixture',False)]])
def test_no_search_history_does_not_invent_workflow(rows):
    result = state(rows)
    assert result[:3] == (None,None,0) and result[-1] is None


@pytest.mark.parametrize('error,text', [(True,'fixture rejected'), (False,'not-json'), (False,'{"results":[]}')])
def test_failed_search_remains_retryable_without_invented_sources(error,text):
    result = state([('Bash',SEARCH,text,error)])
    assert result[:3] == ('tavily_research','search',1)
    assert result[10] == result[11] == []
    assert (result[-1] is not None) is error


def test_successful_search_exposes_only_actual_result_urls():
    result = state([('Bash',SEARCH,PAYLOAD,False)])
    assert result[:3] == ('tavily_research','fetch_sources',1)
    assert result[10] == URLS and result[3] == result[11] == []


def test_fetch_history_ignores_unapproved_and_duplicate_urls():
    rows = [('Bash','curl '+URLS[0],'too early',False), ('Bash',SEARCH,PAYLOAD,False), ('Bash','curl https://unknown.test/secret','unapproved',False), ('Bash','curl '+URLS[0],'<title>One</title><p>evidence</p>',False), ('Bash','curl '+URLS[0],'duplicate',False)]
    result = state(rows)
    assert result[:3] == ('tavily_research','fetch_sources',2)
    assert result[3] == [URLS[0]] and len(result[11]) == 1
    assert 'evidence' in result[11][0] and 'duplicate' not in result[11][0]
    assert 'unapproved' not in str(result)


def test_all_fetched_sources_allow_summary_without_another_search():
    rows = [('Bash',SEARCH,PAYLOAD,False)]+[('Bash','curl '+url,'<p>source evidence</p>',False) for url in URLS]
    result = state(rows)
    assert result[:3] == ('tavily_research','summarize',3)
    assert result[3] == URLS and len(result[11]) == 2


def test_fetch_failure_preserves_error_and_never_becomes_evidence():
    rows = [('Bash',SEARCH,PAYLOAD,False),('Bash','curl '+URLS[0],'fixture denied',True)]
    result = state(rows)
    assert result[3] == [URLS[0]] and result[11] == []
    assert 'fixture denied' in result[-1]
    assert result[1] == 'fetch_sources'


def test_latest_successful_search_replaces_old_allowed_targets():
    old = json.dumps({'results':[{'url':'https://old.test/'}]})
    rows = [('Bash',SEARCH,old,False),('Bash',SEARCH,PAYLOAD,False),('Bash','curl https://old.test/','old',False)]
    result = state(rows)
    assert result[2] == 2 and result[10] == URLS and result[3] == []


@pytest.mark.parametrize('final', ['complete', [{'type':'text','text':'complete'}]])
def test_completed_assistant_answer_closes_research_state(final):
    result = state([('Bash',SEARCH,PAYLOAD,False)],final)
    assert result[:3] == (None,None,0)


@pytest.mark.parametrize('command', ['', '\n', '   \n'])
def test_empty_bash_input_in_tool_history_never_crashes_reconstruction(command):
    result = w._extract_tool_exchanges(request([('Bash',command,'fixture empty input',True)]))
    assert len(result) == 1 and result[0].target == '' and result[0].is_error


@pytest.mark.parametrize('command,expected', [('echo fixture > output.txt','output.txt'),('cat source.py','source.py'),('echo fixture',''),('cat '+'a'*241,''),('echo x > a:b',''),('cat a:b',''),('echo x > '+'a'*241,''),('cat source.py\necho ignored','source.py')])
def test_bash_file_target_is_bounded_and_uses_only_first_line(command,expected):
    assert w._extract_bash_file_target(command) == expected


@pytest.mark.parametrize('layout,name,expected', [('package','fixture','package'),('module','fixture','module'),('both','fixture','package'),('none','missing',None),('module','',None)])
def test_python_module_resolution_uses_owned_project_tree(tmp_path,layout,name,expected):
    if layout in {'package','both'}:
        (tmp_path/'fixture').mkdir()
        (tmp_path/'fixture/__init__.py').write_text('fixture = True\n')
    if layout in {'module','both'}:
        (tmp_path/'fixture.py').write_text('fixture = True\n')
    actual = w.resolve_python_module_path(name,tmp_path)
    want = str(tmp_path/('fixture/__init__.py' if expected=='package' else 'fixture.py')) if expected else None
    assert actual == want
