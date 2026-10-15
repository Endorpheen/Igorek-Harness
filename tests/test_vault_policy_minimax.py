"""Regression tests for Vault policy agent/service allowlists.

The `minimax` cc-backend provider (added in 0.1.520) requires the
`cc-backend` agent to list `minimax` in its `services` allowlist so
the gateway can materialize `MINIMAX_API_KEY` via the Vault broker.

These tests are structural: they assert the DEFAULT_POLICY in
`factory.core.vault.policy` and the runtime policy.yaml both contain
the service for the cc-backend agent. If either drifts, the gateway
fails with `RuntimeError: Igorek Vault denied service 'minimax' ...`.
"""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from factory.core.vault.policy import DEFAULT_POLICY, PolicyEngine


class VaultPolicyMinimaxCcBackendTest(unittest.TestCase):
    """`cc-backend` agent must allow the `minimax` service."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="igorek-policy-test-")
        self.addCleanup(self.directory.cleanup)
        self.policy_path = Path(self.directory.name) / "policy.yaml"
        PolicyEngine().write_default(self.policy_path)

    def test_default_policy_includes_minimax_for_cc_backend(self) -> None:
        agent = DEFAULT_POLICY.agents.get("cc-backend")
        self.assertIsNotNone(agent, "cc-backend agent must be defined in DEFAULT_POLICY")
        self.assertIn(
            "minimax",
            agent.services,
            "cc-backend agent must allow `minimax` service so gateway can inject MINIMAX_API_KEY",
        )

    def test_default_policy_can_access_service_minimax(self) -> None:
        engine = PolicyEngine()
        allowed, reason = engine.can_access_service("cc-backend", "minimax")
        self.assertTrue(allowed, f"cc-backend should access minimax, got: {reason}")
        self.assertEqual(reason, "allowed by policy")

    def test_default_policy_includes_glm_for_cc_backend(self) -> None:
        agent = DEFAULT_POLICY.agents.get("cc-backend")
        self.assertIsNotNone(agent, "cc-backend agent must be defined in DEFAULT_POLICY")
        self.assertIn(
            "glm",
            agent.services,
            "cc-backend agent must allow `glm` service so gateway can inject GLM_API_KEY",
        )

    def test_default_policy_can_access_service_glm(self) -> None:
        engine = PolicyEngine()
        allowed, reason = engine.can_access_service("cc-backend", "glm")
        self.assertTrue(allowed, f"cc-backend should access glm, got: {reason}")
        self.assertEqual(reason, "allowed by policy")

    def test_runtime_policy_yaml_includes_minimax_for_cc_backend(self) -> None:
        path = self.policy_path
        self.assertTrue(path.exists(), f"runtime policy.yaml must exist at {path}")
        engine = PolicyEngine.from_yaml(path)
        agent = engine.get_agent_policy("cc-backend")
        self.assertIsNotNone(agent)
        self.assertIn(
            "minimax",
            agent.services,
            f"runtime {path} must list minimax in cc-backend.services",
        )

    def test_runtime_policy_yaml_includes_glm_for_cc_backend(self) -> None:
        path = self.policy_path
        self.assertTrue(path.exists(), f"runtime policy.yaml must exist at {path}")
        engine = PolicyEngine.from_yaml(path)
        agent = engine.get_agent_policy("cc-backend")
        self.assertIsNotNone(agent)
        self.assertIn(
            "glm",
            agent.services,
            f"runtime {path} must list glm in cc-backend.services",
        )

    def test_other_cc_backend_services_remain_intact(self) -> None:
        """Compatibility regression: other cc-backend services untouched."""
        agent = DEFAULT_POLICY.agents["cc-backend"]
        for required in (
            "openrouter",
            "opencode-go",
            "opencode",
            "telegram",
            "tavily",
        ):
            self.assertIn(required, agent.services, f"{required} must remain in cc-backend services")

    def test_igorek_agent_still_has_minimax(self) -> None:
        """Compatibility: `igorek` agent's minimax entry must not be removed."""
        agent = DEFAULT_POLICY.agents["igorek"]
        self.assertIn("minimax", agent.services)

    def test_igorek_agent_has_glm(self) -> None:
        """Compatibility: `igorek` agent can request GLM vault materialization."""
        agent = DEFAULT_POLICY.agents["igorek"]
        self.assertIn("glm", agent.services)


if __name__ == "__main__":
    unittest.main()
