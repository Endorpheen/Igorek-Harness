from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .codex_native_policy import describe_app_server_request
from .security_store import SecurityStore


@dataclass(frozen=True)
class NativeApprovalEnvelope:
    approval_id: str
    method: str
    summary: str
    target: str
    default_scope: str
    policy_type: str
    ui_type: str
    params: dict[str, Any]


class ApprovalRuntimeBridge:
    """Bridge native app-server approvals into the shared security store."""

    def __init__(self, store: SecurityStore | None = None) -> None:
        self.store = store or SecurityStore()
        self.store.ensure_bootstrap()

    def observe_native_request(
        self,
        *,
        session_id: str,
        task_id: str | None,
        requested_by: str,
        method: str,
        params: dict[str, Any],
        item_snapshot: dict[str, Any] | None = None,
        native_request_id: str | None = None,
    ) -> NativeApprovalEnvelope:
        descriptor = describe_app_server_request(method, params, item_snapshot=item_snapshot)
        approval_id = self.store.create_approval(
            approval_type=descriptor.policy_type,
            target=descriptor.target or descriptor.summary,
            task_id=task_id,
            session_id=session_id,
            reason=f"{method}: {descriptor.summary}",
            requested_by=requested_by,
        )
        self.store.log_event(
            event_type="approval_observed",
            task_id=task_id,
            session_id=session_id,
            decision="pending",
            target=descriptor.target or descriptor.summary,
            details={
                "approval_id": approval_id,
                "approval_type": descriptor.policy_type,
                "native_request_id": native_request_id or "",
                "native_method": method,
                "default_scope": descriptor.default_scope,
                "requested_by": requested_by,
            },
        )
        return NativeApprovalEnvelope(
            approval_id=approval_id,
            method=method,
            summary=descriptor.summary,
            target=descriptor.target,
            default_scope=descriptor.default_scope,
            policy_type=descriptor.policy_type,
            ui_type=descriptor.ui_type,
            params=params,
        )

    def resolve_native_request(
        self,
        *,
        approval_id: str,
        approved: bool,
        resolved_by: str,
        resolution_note: str,
    ) -> dict[str, Any]:
        return self.store.resolve_approval(
            approval_id,
            status="approved" if approved else "rejected",
            resolved_by=resolved_by,
            resolution_note=resolution_note,
        )

    def reject_pending_for_session(
        self,
        *,
        session_id: str,
        requested_by: str | None = None,
        resolution_note: str,
        resolved_by: str = "approval_runtime:session_closed",
    ) -> list[str]:
        pending = self.store.list_approvals(
            status="pending",
            limit=500,
            session_id=session_id,
            requested_by=requested_by,
        )
        resolved_ids: list[str] = []
        for item in pending:
            approval_id = str(item.get("approval_id") or "")
            if not approval_id:
                continue
            try:
                self.resolve_native_request(
                    approval_id=approval_id,
                    approved=False,
                    resolved_by=resolved_by,
                    resolution_note=resolution_note,
                )
                resolved_ids.append(approval_id)
            except Exception:
                continue
        return resolved_ids
