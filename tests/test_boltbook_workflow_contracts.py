"""Offline state, approval and transport contracts for the social client."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from factory.core import boltbook_client as bc


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    names = [
        "CREDENTIALS",
        "CLAIM",
        "STATUS_CACHE",
        "HEARTBEAT_STATE",
        "INBOX_STATE",
        "POST_DRAFTS",
        "PERSONA_STATE",
    ]
    for name in names:
        monkeypatch.setattr(
            bc, f"BOLTBOOK_{name}_PATH", tmp_path / (name.lower() + ".json")
        )
    monkeypatch.setenv("BOLTBOOK_AGENT_NAME", "Fixture Agent")
    monkeypatch.setattr(bc, "_utc_timestamp", lambda: 100000)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected live transport or model call")

    monkeypatch.setattr(bc, "urlopen", forbidden)
    monkeypatch.setattr(bc, "_run_profile_prompt_direct", forbidden)
    return tmp_path


def save(path, items, **other):
    bc._write_json(path, {"items": items, **other})


def post(identifier="p1", **extra):
    return {
        "id": identifier,
        "title": "Fixture question?",
        "content": "Useful fixture content",
        "author": {"username": "Other"},
        **extra,
    }


def candidate(identifier="p1", **extra):
    return {
        "candidate_id": "post:" + identifier,
        "kind": "post",
        "body": "Fixture question?",
        "comment_count": 1,
        "decision": {
            "action": "draft_comment",
            "score": 6,
            "reasons": ["question_or_discussion_prompt", "low_comment_count"],
        },
        **extra,
    }


def test_missing_state_defaults_and_private_json_round_trip(isolated):
    assert bc.load_inbox_state() == {"items": [], "updated_at": 0}
    assert bc.load_post_drafts_state() == {"items": [], "updated_at": 0}
    assert bc.load_heartbeat_state() == {}
    assert bc.load_persona_state()["fixed"] == "balanced"
    bc._write_json(
        bc.BOLTBOOK_CREDENTIALS_PATH, {"api_key": "fixture-only"}, mode=0o600
    )
    assert bc.load_boltbook_credentials()["api_key"] == "fixture-only"
    assert bc.BOLTBOOK_CREDENTIALS_PATH.stat().st_mode & 0o777 == 0o600
    bc.BOLTBOOK_INBOX_STATE_PATH.write_text("[]")
    assert bc.load_inbox_state()["items"] == []


def test_missing_credentials_are_explicit_without_falling_back_to_ambient_values():
    with pytest.raises(FileNotFoundError):
        bc.load_boltbook_credentials()


@pytest.mark.parametrize("mode", ["fixed", "round_robin_daily", "round_robin_hourly"])
def test_persona_state_is_validated_and_persisted(mode):
    result = bc.set_persona_state(
        mode=mode, fixed="witty", rotation=["witty", "balanced", "witty", "unknown"]
    )
    assert result["persona"]["rotation"] == ["witty", "balanced"]
    assert bc.show_persona_state(now=86400)["persona"]["mode"] == mode
    assert bc.list_persona_presets()["count"] == 4


@pytest.mark.parametrize(
    "fields", [{"mode": "unknown"}, {"fixed": "unknown"}, {"rotation": ["unknown"]}]
)
def test_invalid_persona_updates_do_not_write_state(fields):
    with pytest.raises(ValueError):
        bc.set_persona_state(**fields)
    assert not bc.BOLTBOOK_PERSONA_STATE_PATH.exists()


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"remote_result": {"post": {"id": "p"}}}, "p"),
        ({"remote_result": {"post": {"_id": "p"}}}, "p"),
        ({"remote_result": {"post_id": "p"}}, "p"),
        ({"post_id": "p"}, "p"),
        ({}, ""),
        ([], ""),
    ],
)
def test_remote_post_identity_accepts_supported_shapes(payload, expected):
    assert bc._extract_remote_post_id(payload) == expected


@pytest.mark.parametrize("field", ["id", "_id", "post_id", "uuid", "slug"])
def test_post_identity_uses_supported_identifiers(field):
    assert bc._post_candidate_id({field: "fixture"}) == "post:fixture"
    assert bc._post_payload_id({field: "fixture"}) == "fixture"


def test_post_identity_falls_back_to_permalink_or_derived_metadata():
    assert (
        bc._post_candidate_id({"url": "https://fixture.invalid/p"})
        == "url:https://fixture.invalid/p"
    )
    assert (
        bc._post_candidate_id(
            {"title": "Title", "author": {"username": "Other"}, "created_at": 123}
        )
        == "derived:Other:123:Title"
    )
    bc._write_json(bc.BOLTBOOK_CLAIM_PATH, {"handle": "Fixture-Handle"})
    assert bc._is_post_authored_by_local_agent({"author_name": "fixture handle"})
    assert bc._is_post_authored_by_local_agent({"author": {"username": "FixtureAgent"}})
    assert not bc._is_post_authored_by_local_agent({"author": {"username": "Other"}})


def test_feed_refresh_preserves_review_and_draft_state():
    assert (
        bc.update_inbox_from_feed({"posts": [post(), None]}, captured_at=10)[
            "new_candidates"
        ]
        == 1
    )
    state = bc.load_inbox_state()
    state["items"][0].update(
        status="reviewed", draft={"status": "generated", "content": "draft"}
    )
    bc._write_json(bc.BOLTBOOK_INBOX_STATE_PATH, state)
    result = bc.update_inbox_from_feed(
        {"posts": [post(content="Updated fixture")]}, captured_at=20
    )
    current = bc.load_inbox_state()["items"][0]
    assert (
        result["seen_candidates"] == 1
        and current["captured_at"] == 10
        and current["last_seen_at"] == 20
    )
    assert current["status"] == "reviewed" and current["draft"]["content"] == "draft"
    assert current["body"] == "Updated fixture"


def test_feed_refresh_accepts_zero_counters_instead_of_retaining_stale_values():
    bc.update_inbox_from_feed(
        {"posts": [post(comment_count=8, upvotes=2)]}, captured_at=10
    )
    bc.update_inbox_from_feed(
        {"posts": [post(comment_count=0, upvotes=0)]}, captured_at=20
    )
    row = bc.load_inbox_state()["items"][0]
    assert row["comment_count"] == 0 and row["upvotes"] == 0


def test_inbox_interaction_merge_keeps_original_capture_and_accepts_false_values():
    original = {
        "candidate_id": "comment:p:c",
        "kind": "comment",
        "captured_at": 10,
        "status": "reviewed",
        "upvotes": 4,
    }
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [None, {}, original])
    result = bc.update_inbox_from_interactions(
        [None, {}, dict(original, status="new", captured_at=20, upvotes=0)],
        captured_at=30,
    )
    row = bc.load_inbox_state()["items"][0]
    assert result == {"total_candidates": 1, "new_candidates": 0, "seen_candidates": 1}
    assert (
        row["captured_at"] == 10 and row["status"] == "reviewed" and row["upvotes"] == 0
    )
    assert bc.list_inbox_candidates(status="reviewed", limit=1)["count"] == 1
    assert bc.list_inbox_candidates(status="missing")["count"] == 0


def test_thread_replies_mentions_and_self_comments_are_classified():
    watched = candidate(
        draft={"status": "sent", "remote_comment_id": "ours"},
        title="Thread",
        submolt="general",
    )
    payload = {
        "comments": [
            {
                "id": "c1",
                "author": {"name": "Other"},
                "content": "reply",
                "parent_id": "ours",
                "replies": [
                    {
                        "id": "c2",
                        "author": {"name": "Third"},
                        "content": "Fixture Agent question?",
                    }
                ],
            },
            {
                "id": "self",
                "author": {"name": "Fixture Agent"},
                "content": "Fixture Agent self",
            },
            {"id": "unrelated", "author": {"name": "Other"}, "content": "Unrelated"},
            None,
            {},
        ]
    }
    rows = bc._extract_interaction_candidates(
        payload, watched_post=watched, captured_at=10, agent_name="Fixture Agent"
    )
    assert [r["candidate_id"] for r in rows] == ["comment:p1:c1", "comment:p1:c2"]
    assert (
        rows[0]["interaction"]["reply_to_us"] and rows[1]["interaction"]["mentions_us"]
    )
    assert (
        bc._extract_interaction_candidates(
            {}, watched_post=watched, captured_at=10, agent_name="Fixture Agent"
        )
        == []
    )


def test_sent_own_posts_are_watched_and_ignored_as_new_reaction_targets():
    save(
        bc.BOLTBOOK_POST_DRAFTS_PATH,
        [
            {
                "draft_id": "post-draft:1",
                "status": "sent",
                "remote_result": {"post": {"id": "mine"}},
            },
            None,
            {"status": "generated"},
        ],
    )
    assert bc._sent_own_post_ids() == {"mine"}
    assert bc._sent_own_post_watch_entries()[0]["own_post"]
    bc.update_inbox_from_feed({"posts": [post("mine")]}, captured_at=10)
    result = bc.review_inbox_candidates()
    assert result["items"][0]["decision"]["reasons"] == ["own_post_authored_by_agent"]


@pytest.mark.parametrize(
    "updates,action",
    [
        ({"own_post": True}, "ignore"),
        ({"comment_count": 1, "body": "Question?"}, "ignore"),
        ({"comment_count": 1, "body": "Question?", "upvotes": 2}, "draft_comment"),
        (
            {
                "comment_count": 1,
                "body": "Agent question?",
                "upvotes": 2,
                "author_karma": 5,
                "you_follow_author": True,
            },
            "draft_post",
        ),
        ({"kind": "comment", "interaction": {"reply_to_us": True}}, "draft_comment"),
        ({"kind": "comment"}, "ignore"),
    ],
)
def test_review_decisions_follow_engagement_and_interaction_signals(updates, action):
    row = candidate()
    row.update(updates)
    assert bc._decide_candidate_action(row)["action"] == action


def test_reaction_and_draft_lists_filter_sort_and_recommend_next_action():
    generated = candidate(
        "a", draft={"status": "generated", "content": "a", "generated_at": 10}
    )
    approved = candidate(
        "b",
        draft={
            "status": "approved",
            "content": "b",
            "generated_at": 5,
            "approved_at": 20,
        },
    )
    sent = candidate("c", draft={"status": "sent", "content": "c"})
    ignored = candidate("d", decision={"action": "ignore", "score": 0})
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [None, generated, approved, sent, ignored])
    assert bc.list_reaction_candidates()["count"] == 2
    assert bc.list_drafts(status="approved")["count"] == 1
    assert bc.next_draft()["candidate_id"] == "post:b"
    assert "send-draft post:b" in bc.next_draft()["recommended_command"]
    assert bc.get_draft(candidate_id="post:a")["draft"]["content"] == "a"
    assert bc.next_reaction()["status"] == "ok"
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [])
    assert bc.next_draft()["status"] == bc.next_reaction()["status"] == "empty"


def test_comment_generation_limit_bounds_returned_candidates(monkeypatch):
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [candidate("a"), candidate("b")])
    calls = []

    def generate(**fields):
        calls.append(fields)
        return "Generated fixture comment."

    monkeypatch.setattr(bc, "_run_profile_prompt_direct", generate)
    result = bc.generate_comment_drafts(limit=1)
    assert (
        result["generated_count"] == 2 and len(calls) == 2 and len(result["items"]) == 1
    )


def test_comment_draft_approval_edit_and_stubbed_send_preserve_explicit_approval(
    monkeypatch,
):
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [candidate("a")])
    monkeypatch.setattr(
        bc, "_run_profile_prompt_direct", lambda **_: "Generated fixture comment."
    )
    assert bc.generate_comment_drafts(persona_key="witty")["generated_count"] == 1
    assert bc.get_draft(candidate_id="post:a")["draft"]["persona_key"] == "witty"
    assert bc.approve_draft(candidate_id="post:a")["draft_status"] == "approved"
    assert (
        bc.edit_draft(candidate_id="post:a", content=" Updated ")["draft_status"]
        == "generated"
    )
    sent = []

    class Remote:
        def create_post_comment(self, **fields):
            sent.append(fields)
            return {"comment": {"id": "remote-c"}}

    monkeypatch.setattr(
        bc.BoltbookClient, "from_local_credentials", classmethod(lambda cls: Remote())
    )
    with pytest.raises(ValueError, match="approved"):
        bc.send_approved_draft(candidate_id="post:a")
    assert sent == []
    bc.approve_draft(candidate_id="post:a")
    assert bc.send_approved_draft(candidate_id="post:a")["draft_status"] == "sent"
    assert sent == [{"post_id": "a", "content": "Updated", "parent_id": None}]
    with pytest.raises(ValueError, match="sent"):
        bc.edit_draft(candidate_id="post:a", content="later")


def test_post_draft_lifecycle_uses_fake_model_and_transport_only(monkeypatch):
    monkeypatch.setattr(
        bc,
        "_run_profile_prompt_direct",
        lambda **_: '```json\n{"title":"Title","content":"Body"}\n```',
    )
    created = bc.generate_post_draft(submolt="general", brief="Fixture")
    identifier = created["draft_id"]
    assert identifier == "post-draft:1"
    assert bc.list_post_drafts(status="generated")["count"] == 1
    bc.approve_post_draft(draft_id=identifier)
    edited = bc.edit_post_draft(draft_id=identifier, title="Changed", content="Updated")
    assert (
        edited["draft"]["status"] == "generated"
        and edited["draft"]["approved_at"] is None
    )
    sent = []

    class Remote:
        def create_post(self, **fields):
            sent.append(fields)
            return {"post": {"id": "remote-p"}}

    monkeypatch.setattr(
        bc.BoltbookClient, "from_local_credentials", classmethod(lambda cls: Remote())
    )
    with pytest.raises(ValueError, match="approved"):
        bc.send_post_draft(draft_id=identifier)
    bc.approve_post_draft(draft_id=identifier)
    assert bc.send_post_draft(draft_id=identifier)["draft"]["status"] == "sent"
    assert sent[0]["title"] == "Changed" and sent[0]["content"] == "Updated"
    with pytest.raises(ValueError, match="sent"):
        bc.approve_post_draft(draft_id=identifier)
    with pytest.raises(ValueError, match="sent"):
        bc.edit_post_draft(draft_id=identifier, title="late")


@pytest.mark.parametrize(
    "call",
    [
        lambda: bc.get_post_draft(draft_id="missing"),
        lambda: bc.approve_post_draft(draft_id="missing"),
        lambda: bc.edit_post_draft(draft_id="missing", title="x"),
        lambda: bc.get_draft(candidate_id="missing"),
        lambda: bc.approve_draft(candidate_id="missing"),
        lambda: bc.edit_draft(candidate_id="missing", content="x"),
    ],
)
def test_unknown_draft_operations_fail_without_mutating_state(call):
    with pytest.raises(ValueError, match="not found"):
        call()
    assert (
        not bc.BOLTBOOK_INBOX_STATE_PATH.exists()
        and not bc.BOLTBOOK_POST_DRAFTS_PATH.exists()
    )


@pytest.mark.parametrize(
    "state,fields,reason",
    [
        ({"suspended_until": 100100}, {}, "suspended"),
        ({"sent_comment_timestamps": [99999]}, {"cooldown_s": 20}, "cooldown"),
        (
            {"sent_comment_timestamps": [99000, 99500]},
            {"cooldown_s": 1, "max_comments_per_hour": 2},
            "hourly_limit",
        ),
        (
            {"sent_comment_timestamps": [90000, 91000]},
            {"cooldown_s": 1, "max_comments_per_day": 2},
            "daily_limit",
        ),
    ],
)
def test_comment_limits_stop_before_generation_or_transport(state, fields, reason):
    bc._write_json(bc.BOLTBOOK_HEARTBEAT_STATE_PATH, {"autonomous": state})
    result = bc.autonomous_comment_tick(now=100000, **fields)
    assert (
        result["status"] == "skipped"
        and result["reason"] == reason
        and result["retry_in_seconds"] > 0
    )


@pytest.mark.parametrize(
    "history,fields,reason",
    [
        ([99999], {"cooldown_s": 20}, "cooldown"),
        (
            [99000, 99500],
            {"cooldown_s": 1, "window_s": 7200, "max_posts_per_window": 2},
            "window_limit",
        ),
    ],
)
def test_post_limits_stop_before_generation_or_transport(history, fields, reason):
    bc._write_json(
        bc.BOLTBOOK_HEARTBEAT_STATE_PATH,
        {"autonomous": {"posts": {"sent_post_timestamps": history}}},
    )
    result = bc.autonomous_post_tick(now=100000, **fields)
    assert result["status"] == "skipped" and result["reason"] == reason


def test_autonomous_empty_inbox_does_not_generate_or_send():
    assert bc.autonomous_comment_tick(now=100000)["status"] == "empty"


@pytest.mark.parametrize(
    "value,expected", [(None, []), ([0, -1, "invalid", None, 12, "5", 100001], [5, 12])]
)
def test_rate_history_discards_invalid_or_future_entries(value, expected):
    assert bc._normalize_autonomous_history(value, now=100000) == expected


@pytest.mark.parametrize("payload", [[], {}, {"title": "Title"}, {"content": "Body"}])
def test_post_payload_requires_a_title_and_body(payload):
    if isinstance(payload, list):
        with pytest.raises(ValueError):
            bc._extract_json_object(json.dumps(payload))
    else:
        with pytest.raises(ValueError):
            bc._normalize_post_draft_content(payload)


def test_transport_encodes_queries_json_and_headers_without_live_requests(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return '{"status":"ok","text":"Привет"}'.encode()

    def send(request, **kwargs):
        requests.append((request, kwargs))
        return Response()

    monkeypatch.setattr(bc, "urlopen", send)
    client = bc.BoltbookClient("fixture-token")
    assert client.search(query="тест & data", author=None)["text"] == "Привет"
    request, options = requests[-1]
    assert parse_qs(urlparse(request.full_url).query)["q"] == ["тест & data"]
    assert (
        request.get_header("Authorization") == "Bearer fixture-token"
        and options["timeout"] == 30
    )
    client.create_post(submolt="general", title="Title", content="Тело")
    assert json.loads(requests[-1][0].data)["content"] == "Тело"
    client.create_post_comment(post_id="p", content="Reply", parent_id="parent")
    assert json.loads(requests[-1][0].data)["parent_id"] == "parent"
    client.feed()
    client.posts(submolt="test")
    client.post_comments(post_id="p")
    client.submolts()
    client.subscribe_submolt(submolt="test")
    client.me()
    client.status()
    assert bc.BOLTBOOK_STATUS_CACHE_PATH.is_file()


@pytest.mark.parametrize("raw", ["[]", "not JSON"])
def test_transport_rejects_non_object_or_invalid_responses(monkeypatch, raw):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return raw.encode()

    monkeypatch.setattr(bc, "urlopen", lambda *a, **k: Response())
    with pytest.raises(ValueError):
        bc.BoltbookClient("fixture").me()


def test_heartbeat_due_and_force_paths_update_only_fixture_state(monkeypatch):
    client = bc.BoltbookClient("fixture")
    calls = []

    def check(**fields):
        calls.append(fields)
        bc._write_json(bc.BOLTBOOK_HEARTBEAT_STATE_PATH, {"lastBoltbookCheck": 100000})
        return {"status": "claimed"}

    monkeypatch.setattr(client, "heartbeat_check", check)
    bc._write_json(
        bc.BOLTBOOK_HEARTBEAT_STATE_PATH,
        {"lastBoltbookCheck": 99990, "lastSkillRefreshCheck": 99990},
    )
    assert (
        client.heartbeat_tick(now=100000, interval_s=60)["reason"] == "not_due"
        and calls == []
    )
    assert (
        client.heartbeat_tick(now=100000, force=True)["action"] == "heartbeat_check"
        and len(calls) == 1
    )
    assert bc.load_heartbeat_state()["lastSkillRefreshCheck"] == 100000


def test_heartbeat_claimed_feed_and_interactions_use_stub_transport(monkeypatch):
    client = bc.BoltbookClient("fixture")

    def request(method, path, **kwargs):
        if path.endswith("/status"):
            return {"status": "claimed"}
        if path.endswith("/feed"):
            return {"posts": [post()]}
        if "/comments" in path:
            return {
                "comments": [
                    {
                        "id": "reply",
                        "author": {"name": "Other"},
                        "content": "Hello Fixture Agent",
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(client, "_request", request)
    save(
        bc.BOLTBOOK_POST_DRAFTS_PATH,
        [{"status": "sent", "remote_result": {"post": {"id": "own"}}}],
    )
    result = client.heartbeat_check()
    assert (
        result["feed_checked"]
        and result["interactions_checked"]
        and result["watched_posts_count"] == 1
    )
    assert (
        result["interaction_count"] == 1
        and bc.load_heartbeat_state()["lastHeartbeatResult"]["status"] == "claimed"
    )


def test_feed_refresh_distinguishes_missing_counters_from_explicit_zero():
    bc.update_inbox_from_feed(
        {
            "posts": [
                post(
                    comment_count=8,
                    upvotes=2,
                    author={"username": "Other", "karma": 5, "follower_count": 6},
                )
            ]
        },
        captured_at=10,
    )
    bc.update_inbox_from_feed({"posts": [post()]}, captured_at=20)
    row = bc.load_inbox_state()["items"][0]
    assert (
        row["comment_count"],
        row["upvotes"],
        row["author_karma"],
        row["author_follower_count"],
    ) == (8, 2, 5, 6)
    bc.update_inbox_from_feed(
        {
            "posts": [
                post(author={"username": "Other", "karma": 0, "follower_count": 0})
            ]
        },
        captured_at=30,
    )
    row = bc.load_inbox_state()["items"][0]
    assert row["author_karma"] == row["author_follower_count"] == 0


def interaction_candidate():
    return candidate(
        "p",
        candidate_id="comment:p:parent",
        kind="comment",
        post_id="p",
        parent_comment_id="parent",
        interaction={"reply_to_us": True, "under_own_post": True},
    )


def test_autonomous_comment_success_persists_history_with_stubbed_sender(monkeypatch):
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [interaction_candidate()])
    monkeypatch.setattr(bc, "_run_profile_prompt_direct", lambda **_: "Fixture reply.")
    calls = []

    class Remote:
        def create_post_comment(self, **fields):
            calls.append(fields)
            return {"comment": {"id": "remote"}}

    monkeypatch.setattr(
        bc.BoltbookClient, "from_local_credentials", classmethod(lambda cls: Remote())
    )
    result = bc.autonomous_comment_tick(now=100000)
    assert result["status"] == "ok" and calls == [
        {"post_id": "p", "content": "Fixture reply.", "parent_id": "parent"}
    ]
    state = bc.load_heartbeat_state()["autonomous"]
    assert (
        state["sent_comment_timestamps"]
        == state["own_post_sent_comment_timestamps"]
        == [100000]
    )
    assert state["last_remote_comment_id"] == "remote"
    assert bc.load_inbox_state()["items"][0]["draft"]["status"] == "sent"


def test_autonomous_comment_rate_rejection_records_suspension_without_live_send(
    monkeypatch,
):
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [interaction_candidate()])
    monkeypatch.setattr(bc, "_run_profile_prompt_direct", lambda **_: "Fixture reply.")

    class Remote:
        def create_post_comment(self, **fields):
            raise RuntimeError("429 fixture response")

    monkeypatch.setattr(
        bc.BoltbookClient, "from_local_credentials", classmethod(lambda cls: Remote())
    )
    with pytest.raises(RuntimeError, match="429"):
        bc.autonomous_comment_tick(now=100000)
    state = bc.load_heartbeat_state()["autonomous"]
    assert state["suspended_until"] == 100300 and state["last_error_at"] == 100000
    assert bc.autonomous_comment_tick(now=100001)["reason"] == "suspended"


def test_own_post_comment_limit_precedes_generation():
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [interaction_candidate()])
    bc._write_json(
        bc.BOLTBOOK_HEARTBEAT_STATE_PATH,
        {"autonomous": {"own_post_sent_comment_timestamps": [99900]}},
    )
    assert bc.autonomous_comment_tick(now=100000)["reason"] == "own_post_hourly_limit"


def test_autonomous_post_success_runs_local_approval_and_stubbed_transport(monkeypatch):
    save(bc.BOLTBOOK_INBOX_STATE_PATH, [candidate(title="Discussion fixture")])
    monkeypatch.setattr(
        bc,
        "_run_profile_prompt_direct",
        lambda **_: '{"title":"Fixture title","content":"Fixture body"}',
    )
    calls = []

    class Remote:
        def create_post(self, **fields):
            calls.append(fields)
            return {"post": {"id": "remote-post"}}

    monkeypatch.setattr(
        bc.BoltbookClient, "from_local_credentials", classmethod(lambda cls: Remote())
    )
    result = bc.autonomous_post_tick(now=100000)
    assert result["status"] == "ok" and len(calls) == 1
    assert bc.get_post_draft(draft_id=result["draft_id"])["draft"]["status"] == "sent"
    assert bc.load_heartbeat_state()["autonomous"]["posts"]["sent_post_timestamps"] == [
        100000
    ]
    assert bc.autonomous_post_tick(now=100001)["reason"] == "cooldown"


def test_post_ids_and_json_parsing_reject_invalid_inputs_without_network():
    assert (
        bc._next_post_draft_id(
            [
                None,
                {"draft_id": "other"},
                {"draft_id": "post-draft:no"},
                {"draft_id": "post-draft:9"},
            ]
        )
        == "post-draft:10"
    )
    assert bc._extract_json_object('prefix {"title":"Тест"} suffix') == {
        "title": "Тест"
    }
    for text in ["", "no JSON", "[]"]:
        with pytest.raises(ValueError):
            bc._extract_json_object(text)
