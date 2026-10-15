import unittest
import sys
import types
import tempfile
from pathlib import Path
from unittest import mock

inference = types.ModuleType("paroquant.inference")
inference.GenerationParams = object
inference.build_prompt = lambda *args, **kwargs: ""
paroquant = types.ModuleType("paroquant")
paroquant.inference = inference
sys.modules.setdefault("paroquant", paroquant)
sys.modules.setdefault("paroquant.inference", inference)

from factory.api_server_core.request_state import (
    _resolve_self_inspection_project_file,
    _structural_intent_reroute,
)
from factory.api_server_core.schemas import UserIntent


class RequestStateRoutingTest(unittest.TestCase):
    def test_self_inspection_skill_reroutes_project_analysis_to_local_runtime(self):
        rerouted = _structural_intent_reroute(
            "Используй skill igorek-self-inspection и скажи состояние проекта",
            UserIntent.PROJECT_ANALYSIS,
            UserIntent,
        )

        self.assertEqual(rerouted, UserIntent.LOCAL_IMPLEMENTATION)

    def test_self_inspection_short_state_file_resolves_to_policy_allowed_root(self):
        # Deterministic: build the policy-allowed roots in temp dirs so the test
        # does not depend on machine layout. A non-project root with handoff.md
        # is listed first; the project-shaped root (pyproject.toml + factory/)
        # must win.
        with tempfile.TemporaryDirectory() as decoy_tmp, tempfile.TemporaryDirectory() as project_tmp:
            decoy = Path(decoy_tmp)
            (decoy / "handoff.md").write_text("decoy", encoding="utf-8")

            project = Path(project_tmp)
            (project / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (project / "factory").mkdir()
            (project / "handoff.md").write_text("project", encoding="utf-8")

            with mock.patch(
                "factory.api_server_core.request_state.policy_list",
                return_value=[str(decoy), str(project)],
            ):
                resolved = _resolve_self_inspection_project_file(
                    "handoff.md",
                    "Используй skill igorek-self-inspection и прочитай handoff.md",
                )

        self.assertEqual(resolved, str((project / "handoff.md").resolve()))


if __name__ == "__main__":
    unittest.main()
