"""Subscription token lifecycle checks use synthetic data and fake provider calls."""
from unittest.mock import Mock

import pytest

from factory.core import chatgpt_auth as a


def grant(**changes):
    return {'scope':a.SCOPES,'token_type':'Bearer','access_token':'fixture-access','refresh_token':'fixture-refresh','expires_in':3600,**changes}


@pytest.mark.parametrize('changes',[{'token_type':'basic'},{'access_token':''},{'refresh_token':'bad token'},{'access_token':None},{'expires_in':0},{'expires_in':86401},{'expires_in':'bad'},{'scope':[]}])
def test_malformed_token_reply_never_becomes_saved_credentials(changes):
    with pytest.raises(a.ChatGPTAuthError,match='chatgpt_invalid_response'):a.token_set(grant(**changes),now=100)


@pytest.mark.parametrize('expires,accepted',[(150,True),(99,False)])
def test_earliest_refresh_boundary_does_not_rotate_early_or_use_expired_token(expires,accepted):
    saved={'client_id':'fixture','access_token':'fixture-access','refresh_token':'fixture-refresh','expires_at':expires,'earliest_refresh_at':200}
    provider=Mock(side_effect=AssertionError('no early provider request'))
    if accepted:assert a.refresh(saved,fetch=provider,now=100) is saved
    else:
        with pytest.raises(a.ChatGPTAuthError,match='chatgpt_login_required'):a.refresh(saved,fetch=provider,now=100)
    provider.assert_not_called()


def test_no_refresh_token_revoke_is_local_and_idempotent():
    provider=Mock(side_effect=AssertionError('no revoke request'))
    assert a.revoke({},fetch=provider) is True;provider.assert_not_called()


@pytest.mark.parametrize('payload',[{}, {'models':None},{'models':{}}])
def test_invalid_model_catalog_has_typed_error(payload):
    with pytest.raises(a.ChatGPTAuthError,match='chatgpt_invalid_response'):a.models('fixture',fetch=lambda *args,**kwargs:payload)


def test_model_catalog_ignores_hidden_duplicate_and_unusable_slugs():
    rows=[None,{'visibility':'hidden','slug':'hidden'},{'visibility':'list','slug':''},{'visibility':'list','slug':'bad slug'},{'visibility':'list','slug':'x'*257},{'visibility':'list','slug':'fixture','display_name':'Fixture'},{'visibility':'list','slug':'fixture'}]
    result=a.models('fixture',fetch=lambda *args,**kwargs:{'models':rows})
    assert result['models']==[{'id':'fixture','name':'Fixture','api_format':'openai-responses','free':False}]


def test_empty_model_catalog_is_not_claimed_as_success():
    with pytest.raises(a.ChatGPTAuthError,match='chatgpt_no_models'):a.models('fixture',fetch=lambda *args,**kwargs:{'models':[]})
