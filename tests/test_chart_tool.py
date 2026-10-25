"""Declarative chart validation and registry execution without side effects."""
import json
from copy import deepcopy
import pytest
from factory.core.chart_tool import validate_chart, render_chart
from factory.core.cc_backend_tools import get_tool_schemas, execute_tool, needs_approval


def sample(kind='bar'):
    return {'type':kind,'title':'Сравнение 🐙','labels':['Первый','Второй'],'series':[{'name':'Модель','values':[0,8]}],'unit':'баллы'}


@pytest.mark.parametrize('kind',['bar','line','pie'])
def test_valid_chart_has_compact_durable_result_and_no_file_access(tmp_path,kind):
    value=sample(kind);before=deepcopy(value)
    result=json.loads(execute_tool('render_chart',value,str(tmp_path)))
    assert result['ok'] and result['kind']=='igorek.chart' and result['schema_version']==1
    assert len(result['chart_id'])==32 and len(json.dumps(result))<500
    assert not list(tmp_path.iterdir()) and value==before
    assert not needs_approval('render_chart',value)
    assert any(s['name']=='render_chart' for s in get_tool_schemas())


def test_update_reuses_id_and_content_hash_changes():
    value=sample();value['chart_id']='example-chart'
    a=json.loads(render_chart(value));value['series'][0]['values'][0]=3
    b=json.loads(render_chart(value))
    assert a['chart_id']==b['chart_id']=='example-chart' and a['revision']!=b['revision']


@pytest.mark.parametrize('change',[
 {'type':'html'},{'title':''},{'title':'x'*161},{'title':'bad\nlabel'},
 {'labels':[]},{'labels':['same','same']},{'labels':['x']},{'labels':['x']*41},
 {'series':[]},{'series':[{'name':'x','values':[1]}]},
 {'series':[{'name':'x','values':[None,1]}]},
 {'series':[{'name':'x','values':[True,1]}]},
 {'series':[{'name':'x','values':[float('nan'),1]}]},
 {'series':[{'name':'x','values':[float('inf'),1]}]},
 {'series':[{'name':'x','values':[10**400,1]}]},
 {'chart_id':'../escape'},{'chart_id':True},{'y_min':True},{'y_max':float('inf')},
 {'y_min':5},{'y_max':4},{'y_min':0,'y_max':0},{'script':'alert(1)'}
])
def test_invalid_arguments_return_actionable_errors(change):
    value=sample();value.update(change)
    result=json.loads(render_chart(value))
    assert result.get('reason_code')=='invalid_chart' and result.get('error')
    assert 'chart_id' not in result


@pytest.mark.parametrize('values',[[-1,2],[0,0]])
def test_pie_rejects_negative_or_zero_total(values):
    value=sample('pie');value['series'][0]['values']=values
    assert 'error' in json.loads(render_chart(value))


def test_bar_bounds_must_include_zero_but_line_may_focus_range():
    value=sample('line');value['series'][0]['values']=[50,60];value.update(y_min=40,y_max=70)
    assert validate_chart(value)['y_min']==40
    value['type']='bar';assert 'error' in json.loads(render_chart(value))


def test_unicode_labels_and_html_are_data_not_executable():
    value=sample();value['title']='<script>alert(1)</script>';value['labels']=['🐙'*100,'x']
    assert validate_chart(value)['labels'][0]=='🐙'*100


def test_schema_is_provider_neutral_and_bounded():
    from factory.core.chart_tool import SCHEMA
    from factory.core.toolsets import resolve_toolset
    assert 'render_chart' in resolve_toolset('session')
    assert SCHEMA['input_schema']['additionalProperties'] is False
    assert SCHEMA['input_schema']['properties']['labels']['maxItems']==40
