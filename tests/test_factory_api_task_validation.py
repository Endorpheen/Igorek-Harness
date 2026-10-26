"""Task request boundaries reject invalid JSON shapes before database insertion."""
import json

import pytest

from factory import factory_api as api
from test_factory_api_contracts import server as server


@pytest.mark.parametrize('payload',[None,False,7,'text',['text'],[]])
def test_task_body_must_be_an_object(server,payload):
    status,data,_=server.request('POST','/task',raw=json.dumps(payload).encode())
    assert status==400 and data.get('error')
    with api.open_db(server.db) as db:assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==0


@pytest.mark.parametrize('text',[None,False,7,[],{},'', '   '])
def test_task_text_must_be_nonempty_text(server,text):
    status,data,_=server.request('POST','/task',{'text':text})
    assert status==400 and data.get('error')
    with api.open_db(server.db) as db:assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==0


@pytest.mark.parametrize('name,value',[
    ('profile',[]),('profile',{}),('profile',4),('profile',False),('profile','   '),
    ('metadata',[]),('metadata','invalid'),('metadata',7),
    ('capabilities','read'),('capabilities',{}),('capabilities',[4]),('capabilities',['']),
])
def test_invalid_optional_fields_do_not_create_unrunnable_tasks(server,name,value):
    status,data,_=server.request('POST','/task',{'text':'fixture',name:value})
    assert status==400 and data.get('error')
    with api.open_db(server.db) as db:assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==0


@pytest.mark.parametrize('optional',[{}, {'profile':None}, {'capabilities':None}, {'metadata':None}])
def test_missing_or_null_optional_fields_keep_documented_defaults(server,optional):
    status,data,_=server.request('POST','/task',{'text':' fixture text ',**optional})
    assert status==201
    with api.open_db(server.db) as db:
        row=db.execute('SELECT profile,input_text,capabilities,metadata FROM tasks WHERE task_id=?',(data['task_id'],)).fetchone()
    assert row[0]=='zai' and row[1]==' fixture text ' and json.loads(row[2])==[]
    assert json.loads(row[3])['created_via']=='factory_api'


def test_valid_custom_metadata_and_capabilities_survive_validation(server):
    payload={'text':'fixture','profile':'fixture','metadata':{'context':{'label':'value'}},'capabilities':['read','write']}
    status,data,_=server.request('POST','/task',payload);assert status==201
    with api.open_db(server.db) as db:row=db.execute('SELECT capabilities,metadata FROM tasks WHERE task_id=?',(data['task_id'],)).fetchone()
    assert json.loads(row[0])==payload['capabilities'] and json.loads(row[1])['context']==payload['metadata']['context']
