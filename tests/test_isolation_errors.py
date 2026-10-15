#!/usr/bin/env python3
"""One canonical IsolationError is shared throughout the isolation package.

Previously separate modules defined different exception classes, so handlers
could miss an error raised by a sibling module. Every module must re-export
the same class, with reason/detail attributes, a code property and stable text.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import factory.core.isolation as isolation_package  # noqa: E402
from factory.core.isolation import domain, errors, workspace  # noqa: E402


class IsolationErrorIdentityTest(unittest.TestCase):
    def test_all_modules_expose_the_same_class_object(self) -> None:
        self.assertIs(domain.IsolationError, workspace.IsolationError)
        self.assertIs(domain.IsolationError, errors.IsolationError)
        self.assertIs(domain.IsolationError, isolation_package.IsolationError)

    def test_every_alias_is_a_runtime_error_subclass(self) -> None:
        for cls in (
            domain.IsolationError,
            workspace.IsolationError,
            errors.IsolationError,
            isolation_package.IsolationError,
        ):
            self.assertTrue(issubclass(cls, RuntimeError), cls)

    def test_package_handler_catches_domain_error(self) -> None:
        with self.assertRaises(isolation_package.IsolationError) as ctx:
            raise domain.IsolationError("boom")

        self.assertEqual(ctx.exception.reason, "boom")

    def test_package_handler_catches_workspace_error(self) -> None:
        with self.assertRaises(isolation_package.IsolationError) as ctx:
            raise workspace.IsolationError("boom")

        self.assertEqual(ctx.exception.reason, "boom")

    def test_cross_module_except_catches_domain_error(self) -> None:
        caught: errors.IsolationError | None = None
        try:
            raise domain.IsolationError("boom")
        except workspace.IsolationError as exc:
            caught = exc

        self.assertIsNotNone(caught)
        assert caught is not None
        self.assertEqual(caught.reason, "boom")

    def test_real_domain_refusal_is_caught_via_workspace_alias(self) -> None:
        spec = domain.DomainSpec(
            session_id="   ",
            base_image=Path("/nonexistent/base.qcow2"),
            workspace_host_path=Path("/nonexistent/ws"),
            overlay_dir=Path("/nonexistent/overlays"),
        )

        with self.assertRaises(workspace.IsolationError) as ctx:
            domain.validate_spec(spec)

        self.assertEqual(ctx.exception.reason, "invalid_session_id")

    def test_real_workspace_refusal_is_caught_via_domain_alias(self) -> None:
        policy = workspace.WorkspacePolicy(
            home=Path("/nonexistent-home"),
            data_root=Path("/nonexistent-data"),
            host_uid=os.getuid(),
        )

        with self.assertRaises(domain.IsolationError) as ctx:
            workspace.resolve_workspace_export("   ", policy=policy)

        self.assertEqual(ctx.exception.reason, "invalid_cwd_empty")


class IsolationErrorContractTest(unittest.TestCase):
    def test_code_is_the_reason_before_the_first_colon(self) -> None:
        error = errors.IsolationError("sensitive_path_denied:~/.ssh")

        self.assertEqual(error.code, "sensitive_path_denied")
        self.assertEqual(error.reason, "sensitive_path_denied:~/.ssh")
        self.assertEqual(error.detail, "")

    def test_code_is_taken_from_the_reason_not_the_detail(self) -> None:
        error = errors.IsolationError("x:y", "z:w")

        self.assertEqual(error.code, "x")
        self.assertEqual(error.reason, "x:y")
        self.assertEqual(error.detail, "z:w")

    def test_code_of_a_plain_reason_is_the_whole_reason(self) -> None:
        self.assertEqual(errors.IsolationError("overlay_exists").code, "overlay_exists")

    def test_str_contains_reason_and_detail(self) -> None:
        error = errors.IsolationError("overlay_create_failed", "qemu-img exited 1")

        message = str(error)
        self.assertIn("overlay_create_failed", message)
        self.assertIn("qemu-img exited 1", message)
        self.assertEqual(message, str(error))

    def test_str_without_detail_is_the_reason(self) -> None:
        self.assertEqual(str(errors.IsolationError("overlay_exists")), "overlay_exists")

    def test_detail_defaults_to_empty_and_stays_positional(self) -> None:
        default = errors.IsolationError("reason_only")
        positional = errors.IsolationError("reason_only", "payload")

        self.assertEqual(default.detail, "")
        self.assertEqual(positional.reason, "reason_only")
        self.assertEqual(positional.detail, "payload")

    def test_domain_and_workspace_instances_share_the_contract(self) -> None:
        domain_error = domain.IsolationError("data_root_denied:/data")
        workspace_error = workspace.IsolationError("sensitive_path_denied:~", detail="~")

        self.assertEqual(domain_error.code, "data_root_denied")
        self.assertEqual(workspace_error.code, "sensitive_path_denied")
        self.assertIs(type(domain_error), type(workspace_error))


if __name__ == "__main__":
    unittest.main()
