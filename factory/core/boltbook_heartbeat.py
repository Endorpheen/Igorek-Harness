from __future__ import annotations

import threading
import time
from typing import Optional

from core.boltbook_client import (
    BoltbookClient,
    DEFAULT_HEARTBEAT_INTERVAL_S,
    DEFAULT_SKILL_REFRESH_INTERVAL_S,
    DEFAULT_AUTONOMOUS_COMMENT_COOLDOWN_S,
    DEFAULT_AUTONOMOUS_MAX_COMMENTS_PER_HOUR,
    DEFAULT_AUTONOMOUS_MAX_COMMENTS_PER_DAY,
    DEFAULT_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR,
    DEFAULT_AUTONOMOUS_POST_COOLDOWN_S,
    DEFAULT_AUTONOMOUS_POST_WINDOW_S,
    DEFAULT_AUTONOMOUS_POST_MAX_PER_WINDOW,
    autonomous_comment_tick,
    autonomous_post_tick,
    show_persona_state,
)
from core.autonomous_actions import log_autonomous_action
from core.logger import get_logger


class BoltbookHeartbeatLoop:
    def __init__(
        self,
        loop_interval_s: int = 60,
        heartbeat_interval_s: int = DEFAULT_HEARTBEAT_INTERVAL_S,
        skill_refresh_interval_s: int = DEFAULT_SKILL_REFRESH_INTERVAL_S,
        feed_limit: int = 15,
        feed_sort: str = "new",
        autonomous_comments_enabled: bool = False,
        autonomous_profile: str = "codex",
        autonomous_min_score: int = 6,
        autonomous_timeout_s: int = 180,
        autonomous_comment_cooldown_s: int = DEFAULT_AUTONOMOUS_COMMENT_COOLDOWN_S,
        autonomous_max_comments_per_hour: int = DEFAULT_AUTONOMOUS_MAX_COMMENTS_PER_HOUR,
        autonomous_max_comments_per_day: int = DEFAULT_AUTONOMOUS_MAX_COMMENTS_PER_DAY,
        autonomous_own_post_max_comments_per_hour: int = DEFAULT_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR,
        autonomous_posts_enabled: bool = False,
        autonomous_post_profile: str = "codex",
        autonomous_post_submolt: str = "general",
        autonomous_post_timeout_s: int = 240,
        autonomous_post_cooldown_s: int = DEFAULT_AUTONOMOUS_POST_COOLDOWN_S,
        autonomous_post_window_s: int = DEFAULT_AUTONOMOUS_POST_WINDOW_S,
        autonomous_post_max_per_window: int = DEFAULT_AUTONOMOUS_POST_MAX_PER_WINDOW,
    ):
        self.loop_interval_s = max(1, int(loop_interval_s))
        self.heartbeat_interval_s = max(1, int(heartbeat_interval_s))
        self.skill_refresh_interval_s = max(1, int(skill_refresh_interval_s))
        self.feed_limit = max(1, int(feed_limit))
        self.feed_sort = str(feed_sort or "new")
        self.autonomous_comments_enabled = bool(autonomous_comments_enabled)
        self.autonomous_profile = str(autonomous_profile or "codex")
        self.autonomous_min_score = max(1, int(autonomous_min_score))
        self.autonomous_timeout_s = max(30, int(autonomous_timeout_s))
        self.autonomous_comment_cooldown_s = max(1, int(autonomous_comment_cooldown_s))
        self.autonomous_max_comments_per_hour = max(1, int(autonomous_max_comments_per_hour))
        self.autonomous_max_comments_per_day = max(1, int(autonomous_max_comments_per_day))
        self.autonomous_own_post_max_comments_per_hour = max(1, int(autonomous_own_post_max_comments_per_hour))
        self.autonomous_posts_enabled = bool(autonomous_posts_enabled)
        self.autonomous_post_profile = str(autonomous_post_profile or "codex")
        self.autonomous_post_submolt = str(autonomous_post_submolt or "general")
        self.autonomous_post_timeout_s = max(60, int(autonomous_post_timeout_s))
        self.autonomous_post_cooldown_s = max(1, int(autonomous_post_cooldown_s))
        self.autonomous_post_window_s = max(60, int(autonomous_post_window_s))
        self.autonomous_post_max_per_window = max(1, int(autonomous_post_max_per_window))
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.logger = get_logger("boltbook_heartbeat")

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            try:
                BoltbookClient.from_local_credentials()
            except FileNotFoundError:
                self.logger.info(
                    "boltbook_heartbeat_disabled",
                    "Boltbook heartbeat disabled: local credentials not found",
                )
                return False
            self.running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            persona_summary = "persona=disabled"
            if self.autonomous_comments_enabled:
                try:
                    persona_state = show_persona_state()
                    active = persona_state.get("active") or {}
                    persona = persona_state.get("persona") or {}
                    persona_summary = (
                        f"persona={active.get('key') or '-'}"
                        f" ({active.get('title') or '-'})"
                        f", mode={persona.get('mode') or '-'}"
                    )
                except Exception:
                    persona_summary = "persona=unavailable"
            self.logger.info(
                "boltbook_heartbeat_started",
                f"Boltbook heartbeat thread started [{persona_summary}]",
                loop_interval_s=self.loop_interval_s,
                heartbeat_interval_s=self.heartbeat_interval_s,
                skill_refresh_interval_s=self.skill_refresh_interval_s,
                feed_limit=self.feed_limit,
                feed_sort=self.feed_sort,
                autonomous_comments_enabled=self.autonomous_comments_enabled,
                autonomous_profile=self.autonomous_profile,
                autonomous_posts_enabled=self.autonomous_posts_enabled,
                autonomous_post_profile=self.autonomous_post_profile,
                autonomous_post_submolt=self.autonomous_post_submolt,
            )
            return True

    def stop(self) -> None:
        with self._lock:
            self.running = False
            if self._thread:
                self._thread.join(timeout=5)
                self._thread = None
            self.logger.info("boltbook_heartbeat_stopped", "Boltbook heartbeat thread stopped")

    def is_initialized(self) -> bool:
        return self.running

    def _run_loop(self) -> None:
        while self.running:
            try:
                self.run_once()
            except Exception as exc:
                self.logger.error("boltbook_heartbeat_loop_error", f"Boltbook heartbeat loop error: {exc}")
            for _ in range(self.loop_interval_s):
                if not self.running:
                    break
                time.sleep(1)

    def run_once(self) -> dict:
        result = BoltbookClient.from_local_credentials().heartbeat_tick(
            feed_limit=self.feed_limit,
            feed_sort=self.feed_sort,
            interval_s=self.heartbeat_interval_s,
            skill_refresh_interval_s=self.skill_refresh_interval_s,
        )
        status = str(result.get("status", ""))
        if status == "ok":
            heartbeat_result = result.get("result") or {}
            log_autonomous_action(
                source="heartbeat",
                action_type="heartbeat_check",
                status="ok",
                profile=self.autonomous_profile if self.autonomous_comments_enabled else None,
                entity_kind="boltbook",
                entity_id="heartbeat",
                summary="Boltbook heartbeat check completed",
                details={
                    "claimed_status": heartbeat_result.get("status"),
                    "feed_checked": heartbeat_result.get("feed_checked"),
                    "feed_count": heartbeat_result.get("feed_count"),
                    "interaction_count": heartbeat_result.get("interaction_count"),
                    "watched_posts_count": heartbeat_result.get("watched_posts_count"),
                },
                created_at=int(result.get("checked_at") or int(time.time())),
            )
            self.logger.info(
                "boltbook_heartbeat_ok",
                "Boltbook heartbeat check completed",
                claimed_status=heartbeat_result.get("status"),
                feed_checked=heartbeat_result.get("feed_checked"),
                feed_count=heartbeat_result.get("feed_count"),
            )
            if self.autonomous_comments_enabled:
                try:
                    auto_result = autonomous_comment_tick(
                        profile=self.autonomous_profile,
                        min_score=self.autonomous_min_score,
                        timeout_s=self.autonomous_timeout_s,
                        cooldown_s=self.autonomous_comment_cooldown_s,
                        max_comments_per_hour=self.autonomous_max_comments_per_hour,
                        max_comments_per_day=self.autonomous_max_comments_per_day,
                        own_post_max_comments_per_hour=self.autonomous_own_post_max_comments_per_hour,
                    )
                    auto_status = str(auto_result.get("status") or "")
                    if auto_status == "ok":
                        send_result = auto_result.get("send_result") or {}
                        remote_result = send_result.get("remote_result") or {}
                        remote_comment = remote_result.get("comment") or {}
                        log_autonomous_action(
                            source="autonomous_comment",
                            action_type="comment_sent",
                            status="ok",
                            profile=self.autonomous_profile,
                            entity_kind="comment_candidate",
                            entity_id=str(auto_result.get("candidate_id") or ""),
                            summary="Boltbook autonomous comment sent",
                            details={
                                "candidate_id": auto_result.get("candidate_id"),
                                "remote_comment_id": remote_comment.get("id"),
                                "message": remote_result.get("message"),
                            },
                            created_at=int(auto_result.get("checked_at") or int(time.time())),
                        )
                        self.logger.info(
                            "boltbook_autonomous_comment_sent",
                            "Boltbook autonomous comment sent",
                            candidate_id=auto_result.get("candidate_id"),
                        )
                    elif auto_status in {"empty", "skipped"}:
                        log_autonomous_action(
                            source="autonomous_comment",
                            action_type="comment_tick",
                            status=auto_status,
                            profile=self.autonomous_profile,
                            entity_kind="comment_candidate",
                            entity_id=str(auto_result.get("candidate_id") or ""),
                            summary="Boltbook autonomous comment idle",
                            details={
                                "reason": auto_result.get("reason"),
                                "message": auto_result.get("message"),
                                "retry_in_seconds": auto_result.get("retry_in_seconds"),
                            },
                            created_at=int(auto_result.get("checked_at") or int(time.time())),
                        )
                        self.logger.debug(
                            "boltbook_autonomous_comment_idle",
                            "Boltbook autonomous comment idle",
                            auto_status=auto_status,
                            reason=auto_result.get("reason"),
                        )
                    else:
                        self.logger.warn(
                            "boltbook_autonomous_comment_unexpected",
                            "Boltbook autonomous comment returned unexpected status",
                            auto_status=auto_status,
                        )
                except Exception as exc:
                    log_autonomous_action(
                        source="autonomous_comment",
                        action_type="comment_tick",
                        status="error",
                        profile=self.autonomous_profile,
                        entity_kind="comment_candidate",
                        entity_id=None,
                        summary="Boltbook autonomous comment error",
                        details={"error": str(exc)},
                        created_at=int(time.time()),
                    )
                    self.logger.error(
                        "boltbook_autonomous_comment_error",
                        f"Boltbook autonomous comment error: {exc}",
                    )
            if self.autonomous_posts_enabled:
                try:
                    post_result = autonomous_post_tick(
                        profile=self.autonomous_post_profile,
                        submolt=self.autonomous_post_submolt,
                        timeout_s=self.autonomous_post_timeout_s,
                        cooldown_s=self.autonomous_post_cooldown_s,
                        window_s=self.autonomous_post_window_s,
                        max_posts_per_window=self.autonomous_post_max_per_window,
                    )
                    post_status = str(post_result.get("status") or "")
                    if post_status == "ok":
                        send_result = post_result.get("send_result") or {}
                        remote_result = send_result.get("remote_result") or {}
                        remote_post = remote_result.get("post") or {}
                        log_autonomous_action(
                            source="autonomous_post",
                            action_type="post_sent",
                            status="ok",
                            profile=self.autonomous_post_profile,
                            entity_kind="post_draft",
                            entity_id=str(post_result.get("draft_id") or ""),
                            summary="Boltbook autonomous post sent",
                            details={
                                "draft_id": post_result.get("draft_id"),
                                "remote_post_id": remote_post.get("id"),
                                "title": remote_post.get("title"),
                                "message": remote_result.get("message"),
                            },
                            created_at=int(post_result.get("checked_at") or int(time.time())),
                        )
                        self.logger.info(
                            "boltbook_autonomous_post_sent",
                            "Boltbook autonomous post sent",
                            draft_id=post_result.get("draft_id"),
                            remote_post_id=remote_post.get("id"),
                        )
                    elif post_status in {"empty", "skipped"}:
                        log_autonomous_action(
                            source="autonomous_post",
                            action_type="post_tick",
                            status=post_status,
                            profile=self.autonomous_post_profile,
                            entity_kind="post_draft",
                            entity_id=str(post_result.get("draft_id") or ""),
                            summary="Boltbook autonomous post idle",
                            details={
                                "reason": post_result.get("reason"),
                                "message": post_result.get("message"),
                                "retry_in_seconds": post_result.get("retry_in_seconds"),
                                "window_s": post_result.get("window_s"),
                                "max_posts_per_window": post_result.get("max_posts_per_window"),
                            },
                            created_at=int(post_result.get("checked_at") or int(time.time())),
                        )
                        self.logger.debug(
                            "boltbook_autonomous_post_idle",
                            "Boltbook autonomous post idle",
                            auto_status=post_status,
                            reason=post_result.get("reason"),
                        )
                    else:
                        self.logger.warn(
                            "boltbook_autonomous_post_unexpected",
                            "Boltbook autonomous post returned unexpected status",
                            auto_status=post_status,
                        )
                except Exception as exc:
                    log_autonomous_action(
                        source="autonomous_post",
                        action_type="post_tick",
                        status="error",
                        profile=self.autonomous_post_profile,
                        entity_kind="post_draft",
                        entity_id=None,
                        summary="Boltbook autonomous post error",
                        details={"error": str(exc)},
                        created_at=int(time.time()),
                    )
                    self.logger.error(
                        "boltbook_autonomous_post_error",
                        f"Boltbook autonomous post error: {exc}",
                    )
        elif status == "skipped":
            log_autonomous_action(
                source="heartbeat",
                action_type="heartbeat_check",
                status="skipped",
                profile=self.autonomous_profile if self.autonomous_comments_enabled else None,
                entity_kind="boltbook",
                entity_id="heartbeat",
                summary="Boltbook heartbeat skipped: not due yet",
                details={
                    "reason": result.get("reason"),
                    "next_due_at": result.get("nextDueAt"),
                    "retry_in_seconds": result.get("retryInSeconds"),
                },
                created_at=int(result.get("checked_at") or int(time.time())),
            )
            self.logger.debug(
                "boltbook_heartbeat_skipped",
                "Boltbook heartbeat skipped: not due yet",
                next_due_at=result.get("nextDueAt"),
                retry_in_seconds=result.get("retryInSeconds"),
            )
        else:
            log_autonomous_action(
                source="heartbeat",
                action_type="heartbeat_check",
                status=status or "unexpected",
                profile=self.autonomous_profile if self.autonomous_comments_enabled else None,
                entity_kind="boltbook",
                entity_id="heartbeat",
                summary="Boltbook heartbeat returned unexpected status",
                details={"returned_status": status},
                created_at=int(time.time()),
            )
            self.logger.warn(
                "boltbook_heartbeat_unexpected",
                "Boltbook heartbeat returned unexpected status",
                returned_status=status,
            )
        return result
