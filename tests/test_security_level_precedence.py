import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from factory.core.worker import _resolve_level_runtime

_RESOLVE = "factory.core.security_settings.resolve_security_level"
_LOAD = "factory.core.security_settings._load_valid_record"


def _level_record(level: str) -> dict:
    return {
        "level": level,
        "expires_at": None,
        "source": "test",
        "is_weakened": False,
        "baseline": level,
    }


class ResolveLevelRuntimeTest(unittest.TestCase):
    """Runtime level resolution in the worker: `_resolve_level_runtime`.

    The resolver retrieves the level from `security_settings` (session/global/baseline),
    **not** from `task_metadata`, and projects it into runtime fields:
    `filesystem_mode`/`guard_mode` (cc-backend) and `security_mode` (external backends).
    The `baseline` sets the profile via `profile_filesystem_mode`.
    """

    def test_standard_maps_to_workspace_on_observe(self):
        with patch(_RESOLVE, return_value=_level_record("standard")):
            result = _resolve_level_runtime({}, None)
        self.assertEqual(
            result,
            {
                "filesystem_mode": "workspace",
                "guard_mode": "on",
                "security_mode": "observe",
            },
        )

    def test_full_maps_to_full_dangerous_off_disabled(self):
        with patch(_RESOLVE, return_value=_level_record("full")):
            result = _resolve_level_runtime({}, {})
        self.assertEqual(
            result,
            {
                "filesystem_mode": "full-dangerous",
                "guard_mode": "off",
                "security_mode": "disabled",
            },
        )

    def test_trusted_maps_to_workspace_warn_observe(self):
        with patch(_RESOLVE, return_value=_level_record("trusted")):
            result = _resolve_level_runtime({}, None)
        self.assertEqual(
            result,
            {
                "filesystem_mode": "workspace",
                "guard_mode": "warn",
                "security_mode": "observe",
            },
        )

    def test_task_metadata_security_level_is_ignored(self):
        # When `metadata={'security_level':'full'}` is set globally with `standard`, the level does not change, and the fact of ignoring is logged in the trace. `session_id` is strictly derived from `task_metadata['session']`.
        with patch(_RESOLVE, return_value=_level_record("standard")) as resolve:
            buf = io.StringIO()
            with redirect_stdout(buf):
                result = _resolve_level_runtime(
                    {},
                    {"security_level": "full", "session": "srv-1"},
                )
        self.assertEqual(result["filesystem_mode"], "workspace")
        self.assertEqual(result["guard_mode"], "on")
        self.assertEqual(result["security_mode"], "observe")
        resolve.assert_called_once_with(session_id="srv-1", when_no_level="workspace")
        self.assertIn(
            "ignoring task_metadata.security_level='full'",
            buf.getvalue(),
        )

    def test_no_security_level_key_emits_no_trace(self):
        with patch(_RESOLVE, return_value=_level_record("standard")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                _resolve_level_runtime({}, {"session": "srv-1"})
        self.assertNotIn("ignoring task_metadata.security_level", buf.getvalue())

    def test_full_dangerous_profile_without_level_stays_standard(self):
        # The `full-dangerous` profile does NOT automatically assign `full` level: `baseline = standard`.
        with patch(_LOAD, return_value=None):
            result = _resolve_level_runtime(
                {"filesystem_mode": "full-dangerous"},
                {"session": "srv-1"},
            )
        self.assertEqual(
            result,
            {
                "filesystem_mode": "workspace",
                "guard_mode": "on",
                "security_mode": "observe",
            },
        )

    def test_user_read_profile_without_level_preserves_baseline(self):
        with patch(_LOAD, return_value=None):
            result = _resolve_level_runtime(
                {"filesystem_mode": "user-read"},
                None,
            )
        self.assertEqual(
            result,
            {
                "filesystem_mode": "user-read",
                "guard_mode": None,
                "security_mode": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
