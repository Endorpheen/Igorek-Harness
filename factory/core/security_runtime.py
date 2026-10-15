from __future__ import annotations

import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

try:
    from .logger import get_logger
    from .security_store import SecurityStore
except ImportError:  # pragma: no cover - direct script fallback
    from logger import get_logger
    from security_store import SecurityStore


LOGGER = get_logger("security_runtime")


class SecurityRuntime:
    def __init__(self, store: SecurityStore | None = None):
        self.store = store or SecurityStore()
        self.store.ensure_bootstrap()
        self.mode = os.environ.get("FACTORY_SECURITY_RUNTIME_MODE", "disabled").strip().lower()
        if self.mode not in {"disabled", "observe", "enforce"}:
            self.mode = "disabled"

    def status(self) -> dict[str, Any]:
        active = self.store.get_active_policy()
        return {
            "mode": self.mode,
            "active_policy": active["name"],
            "policy": active["policy"],
        }

    def _approval_required(self, policy: dict[str, Any], approval_type: str) -> bool:
        dynamic = policy.get("dynamic", {})
        required = dynamic.get("approval_required_for", [])
        return approval_type in required if isinstance(required, list) else False

    def _execution_context(self) -> str:
        comments_enabled = os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        posts_enabled = os.environ.get("FACTORY_BOLTBOOK_AUTONOMOUS_POSTS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
        heartbeat_enabled = os.environ.get("FACTORY_BOLTBOOK_HEARTBEAT_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
        if heartbeat_enabled and (comments_enabled or posts_enabled):
            return "autonomous_boltbook"
        return "interactive"

    def _allowed_cloud_route(self, policy: dict[str, Any], route: str) -> bool:
        dynamic = policy.get("dynamic", {})
        allowlist = dynamic.get("cloud_model_routes", [])
        return route in allowlist if isinstance(allowlist, list) else False

    def _context_auto_approve_rules(self, policy: dict[str, Any], execution_context: str) -> dict[str, Any]:
        dynamic = policy.get("dynamic", {})
        contexts = dynamic.get("auto_approve_contexts", {})
        if not isinstance(contexts, dict):
            return {}
        rules = contexts.get(execution_context, {})
        return rules if isinstance(rules, dict) else {}

    def request_approval(
        self,
        approval_type: str,
        target: str,
        reason: str,
        task_id: str | None,
        session_id: str | None,
        requested_by: str = "security_runtime",
    ) -> str:
        return self.store.create_approval(
            approval_type=approval_type,
            target=target,
            task_id=task_id,
            session_id=session_id,
            reason=reason,
            requested_by=requested_by,
        )

    def _build_policy_observations(
        self,
        command: list[str],
        backend: str,
        profile: str,
        model: str,
        provider: str,
        cwd: str,
        policy: dict[str, Any],
    ) -> list[dict[str, str]]:
        observations: list[dict[str, str]] = []
        command_flags = set(command[1:])
        executable = Path(command[0]).name if command else backend
        static_policy = policy.get("static", {})

        allowed_tools = static_policy.get("allowed_tools", [])
        if isinstance(allowed_tools, list) and allowed_tools and backend not in allowed_tools:
            observations.append(
                {
                    "approval_type": "exec_binary",
                    "target": executable,
                    "reason": f"Backend '{backend}' is not in allowed_tools policy",
                }
            )

        if "--dangerously-skip-permissions" in command_flags or "--dangerously-bypass-approvals-and-sandbox" in command_flags:
            observations.append(
                {
                    "approval_type": "exec_binary",
                    "target": executable,
                    "reason": "Backend command uses dangerous permissions bypass flags",
                }
            )

        route_target = f"{provider}:{model}" if provider or model else backend
        if self._approval_required(policy, "cloud_model_route") and route_target and not self._allowed_cloud_route(policy, route_target):
            observations.append(
                {
                    "approval_type": "cloud_model_route",
                    "target": route_target,
                    "reason": f"Route '{route_target}' is outside cloud_model_routes allowlist",
                }
            )

        if static_policy.get("workspace_only") and not cwd:
            observations.append(
                {
                    "approval_type": "write_outside_workspace",
                    "target": cwd or "<unknown>",
                    "reason": f"Workspace-only policy could not verify cwd for profile '{profile}'",
                }
            )

        return observations

    def _matches_auto_approve(
        self,
        observation: dict[str, str],
        command: list[str],
        execution_context: str,
        policy: dict[str, Any],
    ) -> bool:
        rules = self._context_auto_approve_rules(policy, execution_context)
        if not rules:
            return False

        rule = rules.get(observation["approval_type"])
        if not isinstance(rule, dict):
            return False

        targets = rule.get("targets", [])
        if isinstance(targets, list) and targets and observation["target"] not in targets:
            return False

        required_flags = rule.get("required_flags", [])
        if isinstance(required_flags, list) and required_flags:
            command_flags = set(command[1:])
            if not all(flag in command_flags for flag in required_flags):
                return False

        required_prefix = str(rule.get("command_prefix", "")).strip()
        if required_prefix:
            executable = Path(command[0]).name if command else ""
            if executable != required_prefix:
                return False

        return True

    def _approval_request_key(self, execution_context: str) -> str:
        return f"security_runtime:{execution_context}"

    def _reuse_enabled(self, execution_context: str) -> bool:
        return execution_context != "interactive"

    def _interactive_retry_reuse_allowed(
        self,
        approval: dict[str, Any] | None,
        task_metadata: dict[str, Any] | None,
    ) -> bool:
        if not approval or not isinstance(task_metadata, dict):
            return False

        allowed_ids = task_metadata.get("interactive_retry_approval_ids")
        if not isinstance(allowed_ids, list):
            return False

        approval_id = str(approval.get("approval_id") or "")
        if not approval_id or approval_id not in {str(item) for item in allowed_ids}:
            return False

        resolved_by = str(approval.get("resolved_by") or "")
        if resolved_by != "tui:user":
            return False
        return True

    def _interactive_session_reuse_allowed(
        self,
        approval: dict[str, Any] | None,
        task_metadata: dict[str, Any] | None,
    ) -> bool:
        if not approval or not isinstance(task_metadata, dict):
            return False

        allowed_ids = task_metadata.get("interactive_session_approval_ids")
        if not isinstance(allowed_ids, list):
            return False

        approval_id = str(approval.get("approval_id") or "")
        if not approval_id or approval_id not in {str(item) for item in allowed_ids}:
            return False

        resolved_by = str(approval.get("resolved_by") or "")
        return resolved_by == "tui:session"

    def _review_observation(
        self,
        observation: dict[str, str],
        *,
        command: list[str],
        task_id: str | None,
        session_id: str,
        status: dict[str, Any],
        execution_context: str,
        policy: dict[str, Any],
        task_metadata: dict[str, Any] | None,
        mode: str,
    ) -> dict[str, Any]:
        request_key = self._approval_request_key(execution_context)
        existing = self.store.find_latest_approval(
            approval_type=observation["approval_type"],
            target=observation["target"],
            requested_by=request_key,
        )
        if existing and (
            self._reuse_enabled(execution_context)
            or (
                execution_context == "interactive"
                and (
                    self._interactive_retry_reuse_allowed(existing, task_metadata)
                    or self._interactive_session_reuse_allowed(existing, task_metadata)
                )
            )
        ):
            decision = str(existing.get("status") or "pending")
            self.store.log_event(
                event_type="approval_reused",
                task_id=task_id,
                session_id=session_id,
                decision=decision,
                target=observation["target"],
                policy_name=status["active_policy"],
                details={
                    "approval_id": existing.get("approval_id"),
                    "approval_type": observation["approval_type"],
                    "execution_context": execution_context,
                    "reason": observation["reason"],
                },
            )
            return {
                "decision": decision,
                "approval_id": existing.get("approval_id"),
                "source": "resolved_history",
            }

        approval_id = self.request_approval(
            approval_type=observation["approval_type"],
            target=observation["target"],
            reason=observation["reason"],
            task_id=task_id,
            session_id=session_id,
            requested_by=request_key,
        )

        if self._matches_auto_approve(
            observation=observation,
            command=command,
            execution_context=execution_context,
            policy=policy,
        ):
            resolved = self.store.resolve_approval(
                approval_id=approval_id,
                status="approved",
                resolved_by="policy:auto",
                resolution_note=f"auto-approved for {execution_context}",
            )
            self.store.log_event(
                event_type="approval_auto_approved",
                task_id=task_id,
                session_id=session_id,
                decision="approved",
                target=observation["target"],
                policy_name=status["active_policy"],
                details={
                    "approval_id": approval_id,
                    "approval_type": observation["approval_type"],
                    "execution_context": execution_context,
                    "reason": observation["reason"],
                },
            )
            return {
                "decision": str(resolved.get("status") or "approved"),
                "approval_id": approval_id,
                "source": "policy_auto_approve",
            }

        self.store.log_event(
            event_type="approval_observed",
            task_id=task_id,
            session_id=session_id,
            decision="pending",
            target=observation["target"],
            policy_name=status["active_policy"],
            details={
                "approval_id": approval_id,
                "approval_type": observation["approval_type"],
                "reason": observation["reason"],
                "mode": mode,
                "execution_context": execution_context,
            },
        )
        return {
            "decision": "pending",
            "approval_id": approval_id,
            "source": "pending_request",
        }

    def execute_command(
        self,
        command: list[str],
        input_payload: str,
        env: dict[str, str],
        cwd: str,
        timeout_s: int,
        task_id: str | None,
        profile: str,
        backend: str,
        model: str = "",
        provider: str = "",
        task_metadata: dict[str, Any] | None = None,
        stdout_line_callback=None,
        *,
        security_mode: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        effective_mode = security_mode if security_mode in {"disabled", "observe", "enforce"} else self.mode
        session_id = str(uuid.uuid4())
        status = self.status()
        policy = status.get("policy", {})
        command_preview = " ".join(command[:6])
        execution_context = self._execution_context()

        self.store.log_event(
            event_type="session_created",
            task_id=task_id,
            session_id=session_id,
            decision="allow",
            target=f"{profile}:{backend}",
            policy_name=status["active_policy"],
            details={
                "mode": effective_mode,
                "cwd": cwd,
                "profile": profile,
                "backend": backend,
                "model": model,
                "provider": provider,
                "command_preview": command_preview,
                "execution_context": execution_context,
            },
        )

        if effective_mode in {"observe", "enforce"}:
            LOGGER.info(
                "runtime_exec",
                "Security runtime executed compatibility path",
                task_id=task_id,
                session_id=session_id,
                mode=effective_mode,
                backend=backend,
            )
            observations = self._build_policy_observations(
                command=command,
                backend=backend,
                profile=profile,
                model=model,
                provider=provider,
                cwd=cwd,
                policy=policy,
            )
            self.store.log_event(
                event_type="policy_evaluated",
                task_id=task_id,
                session_id=session_id,
                decision="observe",
                target=f"{profile}:{backend}",
                policy_name=status["active_policy"],
                details={
                    "mode": effective_mode,
                    "profile": profile,
                    "backend": backend,
                    "model": model,
                    "provider": provider,
                    "command_preview": command_preview,
                    "execution_context": execution_context,
                    "observations": observations,
                },
            )
            approval_reviews: list[dict[str, Any]] = []
            for observation in observations:
                review = self._review_observation(
                    observation,
                    command=command,
                    task_id=task_id,
                    session_id=session_id,
                    status=status,
                    execution_context=execution_context,
                    policy=policy,
                    task_metadata=task_metadata,
                    mode=effective_mode,
                )
                approval_reviews.append(
                    {
                        "approval_type": observation["approval_type"],
                        "target": observation["target"],
                        "reason": observation["reason"],
                        **review,
                    }
                )

            blocking_reviews = [item for item in approval_reviews if item["decision"] != "approved"]
            if effective_mode == "enforce" and blocking_reviews:
                self.store.log_event(
                    event_type="command_blocked",
                    task_id=task_id,
                    session_id=session_id,
                    decision="deny",
                    target=" ".join(command[:4]),
                    policy_name=status["active_policy"],
                    details={
                        "backend": backend,
                        "profile": profile,
                        "model": model,
                        "provider": provider,
                        "execution_context": execution_context,
                        "blocking_reviews": blocking_reviews,
                    },
                )
                reasons = "; ".join(
                    f"{item['approval_type']}={item['decision']}:{item['target']}" for item in blocking_reviews
                )
                raise PermissionError(f"Security policy blocked command execution: {reasons}")

        if effective_mode == "enforce":
            self.store.log_event(
                event_type="runtime_mode_fallback",
                task_id=task_id,
                session_id=session_id,
                decision="allow",
                target="backend_command",
                policy_name=status["active_policy"],
                details={"reason": "foundation stage keeps compatibility execution path"},
            )

        if stdout_line_callback is None:
            proc = subprocess.run(
                command,
                input=input_payload,
                text=True,
                capture_output=True,
                env=env,
                cwd=cwd,
                timeout=timeout_s,
            )
        else:
            proc = self._run_with_stdout_callback(
                command=command,
                input_payload=input_payload,
                env=env,
                cwd=cwd,
                timeout_s=timeout_s,
                stdout_line_callback=stdout_line_callback,
            )

        self.store.log_event(
            event_type="command_executed",
            task_id=task_id,
            session_id=session_id,
            decision="allow",
            target=" ".join(command[:4]),
            policy_name=status["active_policy"],
            details={
                "returncode": proc.returncode,
                "mode": effective_mode,
                "backend": backend,
                "profile": profile,
                "model": model,
                "provider": provider,
                "command_preview": command_preview,
                "execution_context": execution_context,
            },
        )

        return proc, session_id

    def _run_with_stdout_callback(
        self,
        *,
        command: list[str],
        input_payload: str,
        env: dict[str, str],
        cwd: str,
        timeout_s: int,
        stdout_line_callback,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=cwd,
        )
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []

        def _read_stdout() -> None:
            if process.stdout is None:
                return
            for line in process.stdout:
                stdout_parts.append(line)
                try:
                    stdout_line_callback(line)
                except Exception:
                    continue

        def _read_stderr() -> None:
            if process.stderr is None:
                return
            for line in process.stderr:
                stderr_parts.append(line)

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            if process.stdin is not None:
                try:
                    process.stdin.write(input_payload)
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            returncode = process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            raise
        finally:
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)

        return subprocess.CompletedProcess(
            args=command,
            returncode=returncode,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
        )
