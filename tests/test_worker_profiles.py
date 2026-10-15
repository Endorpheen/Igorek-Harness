"""Regression tests for multi-profile worker execution in the gateway stack.

Invariant: the active runtime profile is always serviced, and additional profiles are added only via explicit `FACTORY_WORKER_PROFILES` list. Unknown profiles should not cause stack crashes — they are rejected with a message.
"""

from __future__ import annotations

import unittest
from unittest import mock

from factory.core import cli_run


def _fake_profile_config(known: set[str]):
    def _get(profile: str, **_kwargs):
        if profile not in known:
            raise KeyError(profile)
        return {"backend": "cc-backend", "provider": "opencode", "model": "test-model"}

    return _get


class ResolveWorkerProfilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = mock.patch.dict("os.environ", {}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        import os

        os.environ.pop("FACTORY_WORKER_PROFILES", None)
        patcher = mock.patch.object(
            cli_run, "get_profile_config", _fake_profile_config({"active", "extra-a", "extra-b"})
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_active_profile_only_when_env_is_absent(self) -> None:
        self.assertEqual(cli_run._resolve_worker_profiles("active"), ["active"])

    def test_extra_profiles_are_appended_in_order(self) -> None:
        import os

        os.environ["FACTORY_WORKER_PROFILES"] = "extra-a,extra-b"
        self.assertEqual(cli_run._resolve_worker_profiles("active"), ["active", "extra-a", "extra-b"])

    def test_active_profile_is_not_duplicated(self) -> None:
        import os

        os.environ["FACTORY_WORKER_PROFILES"] = "extra-a,active,extra-a"
        self.assertEqual(cli_run._resolve_worker_profiles("active"), ["active", "extra-a"])

    def test_blank_entries_are_ignored(self) -> None:
        import os

        os.environ["FACTORY_WORKER_PROFILES"] = " , extra-b ,, "
        self.assertEqual(cli_run._resolve_worker_profiles("active"), ["active", "extra-b"])

    def test_off_switch_disables_extras(self) -> None:
        import os

        for value in ("off", "0", "no", "false", "disabled", "none"):
            with self.subTest(value=value):
                os.environ["FACTORY_WORKER_PROFILES"] = value
                self.assertEqual(cli_run._resolve_worker_profiles("active"), ["active"])

    def test_unknown_profile_is_skipped(self) -> None:
        import os

        os.environ["FACTORY_WORKER_PROFILES"] = "extra-a,ghost"
        with mock.patch("builtins.print") as printed:
            profiles = cli_run._resolve_worker_profiles("active")
        self.assertEqual(profiles, ["active", "extra-a"])
        self.assertTrue(
            any("ghost" in str(call.args[0]) for call in printed.call_args_list if call.args),
            "пропущенный профиль должен быть озвучен в stdout",
        )

    def test_unknown_active_profile_raises(self) -> None:
        with self.assertRaises(KeyError):
            cli_run._resolve_worker_profiles("missing")


if __name__ == "__main__":
    unittest.main()
