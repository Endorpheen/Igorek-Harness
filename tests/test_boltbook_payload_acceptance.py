"""Independent acceptance of malformed social-candidate fields and valid positives."""

import pytest

from factory.core import boltbook_client as bc
from test_boltbook_workflow_contracts import candidate, isolated as isolated, save


def comment(**overrides):
    value = candidate('p', kind='comment', interaction={'reply_to_us': True})
    value.update(overrides)
    return value


@pytest.mark.parametrize('score', [None, True, False, 'high', [], {}, ['6'], float('nan'), float('inf'), -float('inf')])
def test_malformed_score_is_rejected_even_at_zero_threshold(score):
    value = comment(decision={'action': 'draft_comment', 'score': score, 'reasons': ['reply_to_agent']})
    assert bc._is_autonomous_comment_candidate(value, min_score=0) is False
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [value])
    assert bc._select_autonomous_comment_candidate(min_score=0) is None


@pytest.mark.parametrize('score', [6, 6.5, '6'])
def test_valid_numeric_score_remains_eligible(score):
    value = comment(decision={'action': 'draft_comment', 'score': score, 'reasons': []})
    assert bc._is_autonomous_comment_candidate(value, min_score=6) is True
    assert bc._is_autonomous_comment_candidate(value, min_score=7) is False


@pytest.mark.parametrize('flag', ['reply_to_us', 'mentions_us', 'under_own_post'])
@pytest.mark.parametrize('value', ['false', 'true', 1, [], {'flag': True}])
def test_only_json_true_can_authorize_an_interaction(flag, value):
    assert bc._is_autonomous_comment_candidate(comment(interaction={flag: value}), min_score=6) is False
    assert bc._is_autonomous_comment_candidate(comment(interaction={flag: True}), min_score=6) is True


@pytest.mark.parametrize('count', [[], {}, 'unknown', True, float('nan'), float('inf')])
def test_malformed_comment_count_cannot_authorize_a_post(count):
    value = candidate('p', comment_count=count)
    assert bc._is_autonomous_comment_candidate(value, min_score=6) is False


@pytest.mark.parametrize('reasons', [True, 3, 'question_or_discussion_prompt low_comment_count', {'question_or_discussion_prompt': True, 'low_comment_count': True}])
def test_wrong_reasons_container_is_not_an_authorization(reasons):
    value = candidate('p', decision={'action': 'draft_comment', 'score': 6, 'reasons': reasons})
    assert bc._is_autonomous_comment_candidate(value, min_score=6) is False


def test_fresh_topic_dedup_and_recent_sent_boundaries_use_actual_saved_state():
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [None, {'status': 'archived', 'title': 'Archived'}, {'status': 'new', 'title': ''}, {'status': 'new', 'title': 'Fresh', 'decision': []}, {'status': 'reviewed', 'title': 'fresh'}, {'status': 'draft', 'title': 'Other', 'decision': {'score': 'not numeric'}}, {'status': 'new', 'title': 'Tail'}])
    save(bc.BOLTBOOK_POST_DRAFTS_PATH, [None, {'status': 'generated', 'title': 'Never sent'}, *[{'status': 'sent', 'title': f'Sent{i}'} for i in range(7)]])
    text = bc._build_autonomous_post_brief(max_topic_items=2)
    assert text.count('Fresh') == 1 and 'fresh (' not in text
    assert 'Other' in text and 'score=0' in text and 'Tail' not in text and 'Archived' not in text
    assert 'Never sent' not in text and all(f'Sent{i}' in text for i in range(5))
    assert 'Sent5' not in text and 'Sent6' not in text


@pytest.mark.parametrize('score', [10**400, str(10**400), 2**53 + 1], ids=['large-int', 'large-int-string', 'precise-int'])
def test_valid_integer_scores_keep_precision_without_float_conversion(score):
    value = comment(title='Precise', status='new', decision={'action': 'draft_comment', 'score': score, 'reasons': []})
    assert bc._is_autonomous_comment_candidate(value, min_score=6) is True
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [value])
    assert f'score={int(score)}' in bc._build_autonomous_post_brief()


@pytest.mark.parametrize('kind', ['post', 'comment'])
@pytest.mark.parametrize('reasons', [None, True, 3, 'question_or_discussion_prompt low_comment_count', {'question_or_discussion_prompt': True}, [True], [3], [{}]])
def test_malformed_reason_shape_cannot_authorize_either_candidate_kind(kind, reasons):
    value = comment() if kind == 'comment' else candidate('p')
    value['decision']['reasons'] = reasons
    assert bc._is_autonomous_comment_candidate(value, min_score=6) is False


@pytest.mark.parametrize('kind,expected', [('comment', True), ('post', False)])
def test_missing_reason_field_preserves_contextual_candidate_rules(kind, expected):
    value = comment() if kind == 'comment' else candidate('p')
    del value['decision']['reasons']
    assert bc._is_autonomous_comment_candidate(value, min_score=6) is expected
