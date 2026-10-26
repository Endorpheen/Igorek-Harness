import pytest

from factory.core import boltbook_client as bc
from test_boltbook_workflow_contracts import candidate, isolated as isolated, save

BAD_DECISIONS = [
    "not-a-decision",
    ["draft_comment"],
    {"action": "draft_comment", "score": "high"},
    {"action": "draft_comment", "score": ["6"]},
    {"action": "draft_comment", "score": True},
    {"action": "draft_comment", "score": float("nan")},
    {"action": "draft_comment", "score": float("inf")},
]


def _comment_item(**overrides):
    row = candidate("p", kind="comment", candidate_id="comment:p:c1", post_id="p", parent_comment_id="parent")
    row["interaction"] = {"reply_to_us": True, "mentions_us": False, "under_own_post": False}
    row["decision"] = {"action": "draft_comment", "score": 6, "reasons": ["reply_to_agent"]}
    row.update(overrides)
    return row


def _post_item(score, *, decision_action="draft_comment", reasons=None, comment_count=2):
    row = candidate("p", kind="post", candidate_id="post:p1")
    row["decision"] = {
        "action": decision_action,
        "score": score,
        "reasons": reasons or ["question_or_discussion_prompt", "low_comment_count"],
    }
    row["comment_count"] = comment_count
    return row


def test_autonomous_brief_uses_only_fresh_statuses_and_skips_junk_records(isolated):
    save(
        bc.BOLTBOOK_INBOX_STATE_PATH,
        [
            None,
            {"status": "sent", "title": "Already sent topic"},
            {"status": "new", "title": ""},
            {"status": "new", "kind": "post", "title": "Fresh community topic", "decision": {"score": 7}},
            {"status": "draft", "kind": "comment", "title": "Drafty question", "decision": {"score": 3}},
        ],
    )
    brief = bc._build_autonomous_post_brief()
    assert "Fresh community topic" in brief
    assert "Drafty question" in brief
    assert "Already sent topic" not in brief


def test_autonomous_brief_dedupes_titles_case_insensitively(isolated):
    save(
        bc.BOLTBOOK_INBOX_STATE_PATH,
        [
            {"status": "new", "title": "Fresh Topic", "decision": {"score": 7}},
            {"status": "new", "title": "fresh TOPIC", "decision": {"score": 8}},
            {"status": "new", "title": "Other topic", "decision": {"score": 3}},
        ],
    )
    brief = bc._build_autonomous_post_brief()
    assert brief.count("Fresh Topic") == 1
    assert brief.count("Other topic") == 1


def test_autonomous_brief_respects_max_topic_items_bound(isolated):
    save(
        bc.BOLTBOOK_INBOX_STATE_PATH,
        [
            {"status": "new", "title": "First topic", "decision": {"score": 7}},
            {"status": "new", "title": "Second topic", "decision": {"score": 6}},
            {"status": "new", "title": "Third topic", "decision": {"score": 5}},
        ],
    )
    brief = bc._build_autonomous_post_brief(max_topic_items=2)
    assert "Second topic" in brief
    assert "Third topic" not in brief
    assert brief.count("- [post] ") == 2


def test_autonomous_brief_max_topic_items_zero_returns_fallback(isolated):
    save(
        bc.BOLTBOOK_INBOX_STATE_PATH,
        [
            {"status": "new", "title": "First topic", "decision": {"score": 7}},
            {"status": "new", "title": "Second topic", "decision": {"score": 6}},
        ],
    )
    brief = bc._build_autonomous_post_brief(max_topic_items=0)
    assert "First topic" not in brief
    assert "Second topic" not in brief


@pytest.mark.parametrize("decision", BAD_DECISIONS)
def test_autonomous_brief_reports_zero_score_for_invalid_decision(isolated, decision):
    save(
        bc.BOLTBOOK_INBOX_STATE_PATH,
        [
            {
                "status": "new",
                "kind": "post",
                "title": "Broken topic",
                "decision": decision,
            }
        ],
    )
    brief = bc._build_autonomous_post_brief()
    assert "Broken topic" in brief
    assert "score=0" in brief


def test_autonomous_brief_recent_sent_block_caps_at_five_sent_posts(isolated):
    save(
        bc.BOLTBOOK_POST_DRAFTS_PATH,
        [
            {"draft_id": f"post-draft:{index}", "status": "sent", "title": f"Sent topic {index}"}
            for index in range(1, 8)
        ]
        + [{"status": "generated", "title": "Not sent topic"}],
    )
    brief = bc._build_autonomous_post_brief()
    sent_lines = [line for line in brief.splitlines() if line.startswith("- Sent topic")]
    assert len(sent_lines) == 5


def test_autonomous_comment_candidate_rejects_sent_or_existing_draft(isolated):
    sent = _comment_item(draft={"status": "sent", "content": "x"})
    generated = _comment_item(draft={"status": "generated", "content": "x"})
    assert bc._is_autonomous_comment_candidate(sent, min_score=6) is False
    assert bc._is_autonomous_comment_candidate(generated, min_score=6) is False


def test_autonomous_comment_candidate_rejects_low_score_or_wrong_decision(isolated):
    low = _comment_item(decision={"action": "draft_comment", "score": 1, "reasons": ["reply_to_agent"]})
    ignored = _comment_item(decision={"action": "ignore", "score": 0, "reasons": []})
    not_comment = _post_item(6, decision_action="ignore")
    assert bc._is_autonomous_comment_candidate(low, min_score=6) is False
    assert bc._is_autonomous_comment_candidate(ignored, min_score=6) is False
    assert bc._is_autonomous_comment_candidate(not_comment, min_score=6) is False


def test_autonomous_comment_candidate_accepts_real_reply_interaction(isolated):
    reply = _comment_item(
        interaction={"reply_to_us": True, "mentions_us": False, "under_own_post": False}
    )
    mention = _comment_item(
        interaction={"reply_to_us": False, "mentions_us": True, "under_own_post": False}
    )
    own = _comment_item(
        interaction={"reply_to_us": False, "mentions_us": False, "under_own_post": True}
    )
    assert bc._is_autonomous_comment_candidate(reply, min_score=6) is True
    assert bc._is_autonomous_comment_candidate(mention, min_score=6) is True
    assert bc._is_autonomous_comment_candidate(own, min_score=6) is True


@pytest.mark.parametrize(
    "interaction",
    [
        {"reply_to_us": "true"},
        {"reply_to_us": "false"},
        {"reply_to_us": 1},
        {"reply_to_us": {"value": True}},
        {"reply_to_us": {"flags": True}},
        {"reply_to_us": False, "mentions_us": False, "under_own_post": False},
    ],
)
def test_autonomous_comment_candidate_rejects_interaction_flags_without_strict_true(
    isolated, interaction
):
    item = _comment_item(interaction=interaction)
    assert bc._is_autonomous_comment_candidate(item, min_score=6) is False


def test_autonomous_comment_candidate_accepts_valid_numeric_and_integer_string_scores(isolated):
    valid_number = _comment_item(decision={"action": "draft_comment", "score": 6, "reasons": ["reply_to_agent"]})
    valid_string = _comment_item(decision={"action": "draft_comment", "score": "6", "reasons": ["reply_to_agent"]})
    assert bc._is_autonomous_comment_candidate(valid_number, min_score=6) is True
    assert bc._is_autonomous_comment_candidate(valid_string, min_score=6) is True


def test_autonomous_comment_candidate_rejects_invalid_scores_even_with_min_score_zero(isolated):
    for score in BAD_DECISIONS:
        item = _comment_item(decision={"action": "draft_comment", "score": score, "reasons": ["reply_to_agent"]})
        assert bc._is_autonomous_comment_candidate(item, min_score=0) is False


def test_autonomous_comment_candidate_accepts_valid_score_with_min_score_zero(isolated):
    item = _post_item(0)
    assert bc._is_autonomous_comment_candidate(item, min_score=0) is True


def test_autonomous_comment_candidate_rejects_invalid_comment_count(isolated):
    bad_count = _post_item(0, comment_count=["many"])
    bad_count_str = _post_item(0, comment_count="high")
    assert bc._is_autonomous_comment_candidate(bad_count, min_score=0) is False
    assert bc._is_autonomous_comment_candidate(bad_count_str, min_score=0) is False


def test_autonomous_comment_candidate_rejects_invalid_reasons_container(isolated):
    no_reasons = _post_item(0, reasons="question_or_discussion_prompt")
    assert bc._is_autonomous_comment_candidate(no_reasons, min_score=0) is False


def test_autonomous_comment_candidate_keeps_large_integer_scores_exact():
    big = _comment_item(decision={"action": "draft_comment", "score": 10**20, "reasons": ["reply_to_agent"]})
    assert bc._safe_decision_score(big["decision"]) == 10**20
    assert bc._is_autonomous_comment_candidate(big, min_score=6) is True


def test_autonomous_comment_decision_uses_strict_interaction_flags():
    reply = _comment_item(
        interaction={"reply_to_us": True, "mentions_us": False, "under_own_post": False}
    )
    weak = _comment_item(
        interaction={"reply_to_us": "true", "mentions_us": False, "under_own_post": False}
    )
    assert bc._decide_candidate_action(reply)["action"] == "draft_comment"
    assert bc._decide_candidate_action(weak)["action"] == "ignore"


def test_autonomous_comment_selection_returns_none_for_corrupted_candidates():
    save(
        bc.BOLTBOOK_INBOX_STATE_PATH,
        [
            _comment_item(
                decision={"action": "draft_comment", "score": ["6"], "reasons": ["reply_to_agent"]}
            )
        ],
    )
    assert bc._select_autonomous_comment_candidate(min_score=0) is None


@pytest.mark.parametrize("kind", ["comment", "post"])
@pytest.mark.parametrize(
    "reasons",
    [
        ["reply_to_agent", 3],
        "reply_to_agent",
        {"reply_to_agent": True},
        5,
    ],
)
def test_autonomous_comment_candidate_rejects_invalid_reasons_for_comment_and_post(kind, reasons):
    item = _comment_item(kind=kind, decision={"action": "draft_comment", "score": 6, "reasons": reasons})
    if kind == "comment":
        item["interaction"] = {"reply_to_us": True, "mentions_us": False, "under_own_post": False}
    assert bc._is_autonomous_comment_candidate(item, min_score=6) is False


@pytest.mark.parametrize(
    ("kind", "reasons"),
    [
        ("comment", []),
        ("comment", ["reply_to_agent"]),
        ("comment", ["reply_to_agent", "agent_mentioned"]),
        ("post", ["question_or_discussion_prompt", "low_comment_count"]),
        ("post", ["question_or_discussion_prompt", "moderate_comment_count"]),
    ],
)
def test_autonomous_comment_candidate_accepts_valid_reasons_for_comment_and_post(kind, reasons):
    item = _comment_item(kind=kind, decision={"action": "draft_comment", "score": 6, "reasons": reasons})
    if kind == "comment":
        item["interaction"] = {"reply_to_us": True, "mentions_us": False, "under_own_post": False}
    else:
        item["comment_count"] = 1
    assert bc._is_autonomous_comment_candidate(item, min_score=6) is True
