from __future__ import annotations

import json

from .autonomous_actions import list_autonomous_actions, summarize_autonomous_actions
from .boltbook_client import (
    BoltbookClient,
    approve_draft,
    approve_post_draft,
    edit_draft,
    edit_post_draft,
    generate_comment_drafts,
    generate_post_draft,
    get_draft,
    get_post_draft,
    list_drafts,
    list_inbox_candidates,
    list_persona_presets,
    list_post_drafts,
    list_reaction_candidates,
    next_draft as get_next_draft,
    next_reaction as get_next_reaction,
    review_inbox_candidates,
    send_approved_draft,
    send_post_draft,
    set_persona_state,
    show_persona_state,
)


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def status() -> dict:
    return BoltbookClient.from_local_credentials().status()


def me() -> dict:
    return BoltbookClient.from_local_credentials().me()


def feed(sort: str = "new", limit: int = 15) -> dict:
    return BoltbookClient.from_local_credentials().feed(sort=sort, limit=limit)


def posts(sort: str = "new", limit: int = 15, submolt: str | None = None) -> dict:
    return BoltbookClient.from_local_credentials().posts(sort=sort, limit=limit, submolt=submolt)


def comments(post_id: str, sort: str = "new", limit: int = 100) -> dict:
    return BoltbookClient.from_local_credentials().post_comments(post_id=post_id, sort=sort, limit=limit)


def submolts(sort: str = "new", limit: int = 20) -> dict:
    return BoltbookClient.from_local_credentials().submolts(sort=sort, limit=limit)


def subscribe_submolt(submolt: str) -> dict:
    return BoltbookClient.from_local_credentials().subscribe_submolt(submolt=submolt)


def search(query: str, result_type: str = "all", limit: int = 10, author: str | None = None, submolt: str | None = None) -> dict:
    return BoltbookClient.from_local_credentials().search(
        query=query,
        result_type=result_type,
        limit=limit,
        author=author,
        submolt=submolt,
    )


def heartbeat_check(feed_limit: int = 15, feed_sort: str = "new") -> dict:
    return BoltbookClient.from_local_credentials().heartbeat_check(feed_limit=feed_limit, feed_sort=feed_sort)


def heartbeat_tick(
    feed_limit: int = 15,
    feed_sort: str = "new",
    interval_s: int = 20 * 60,
    skill_refresh_interval_s: int = 24 * 60 * 60,
    force: bool = False,
) -> dict:
    return BoltbookClient.from_local_credentials().heartbeat_tick(
        feed_limit=feed_limit,
        feed_sort=feed_sort,
        interval_s=interval_s,
        skill_refresh_interval_s=skill_refresh_interval_s,
        force=force,
    )


def inbox(status: str | None = None, limit: int = 20) -> dict:
    return list_inbox_candidates(status=status, limit=limit)


def review_inbox(limit: int = 20) -> dict:
    return review_inbox_candidates(limit=limit)


def reactions(limit: int = 20, min_score: int = 5) -> dict:
    return list_reaction_candidates(limit=limit, min_score=min_score)


def next_reaction(min_score: int = 5) -> dict:
    return get_next_reaction(min_score=min_score)


def generate_drafts(
    profile: str = "codex",
    limit: int = 20,
    min_score: int = 5,
    force: bool = False,
    timeout_s: int = 600,
) -> dict:
    return generate_comment_drafts(
        profile=profile,
        limit=limit,
        min_score=min_score,
        force=force,
        timeout_s=timeout_s,
    )


def drafts(status: str | None = None, limit: int = 20) -> dict:
    return list_drafts(status=status, limit=limit)


def generate_post(
    profile: str,
    submolt: str,
    brief: str,
    timeout_s: int = 600,
) -> dict:
    return generate_post_draft(profile=profile, submolt=submolt, brief=brief, timeout_s=timeout_s)


def post_drafts(status: str | None = None, limit: int = 20) -> dict:
    return list_post_drafts(status=status, limit=limit)


def show_post_draft(draft_id: str) -> dict:
    return get_post_draft(draft_id=draft_id)


def update_post_draft(draft_id: str, title: str | None = None, content: str | None = None) -> dict:
    return edit_post_draft(draft_id=draft_id, title=title, content=content)


def approve_post(draft_id: str) -> dict:
    return approve_post_draft(draft_id=draft_id)


def send_post(draft_id: str) -> dict:
    return send_post_draft(draft_id=draft_id)


def next_draft() -> dict:
    return get_next_draft()


def show_draft(candidate_id: str) -> dict:
    return get_draft(candidate_id=candidate_id)


def update_draft(candidate_id: str, content: str) -> dict:
    return edit_draft(candidate_id=candidate_id, content=content)


def approve(candidate_id: str) -> dict:
    return approve_draft(candidate_id=candidate_id)


def send(candidate_id: str) -> dict:
    return send_approved_draft(candidate_id=candidate_id)


def actions(limit: int = 20, source: str | None = None, action_type: str | None = None, status: str | None = None) -> dict:
    return list_autonomous_actions(limit=limit, source=source, action_type=action_type, status=status)


def post_analysis(hours: int = 24, sent_limit: int = 20) -> dict:
    return summarize_autonomous_actions(hours=hours, sent_limit=sent_limit)


def persona_list() -> dict:
    return list_persona_presets()


def persona_show() -> dict:
    return show_persona_state()


def persona_set(
    *,
    mode: str | None = None,
    fixed: str | None = None,
    rotation: list[str] | None = None,
) -> dict:
    return set_persona_state(mode=mode, fixed=fixed, rotation=rotation)
