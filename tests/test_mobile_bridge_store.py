import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory.core.mobile_bridge.store import MobileStore
from factory.core.schema import initialize_database


class MobileStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "factory.db")
        initialize_database(self.db_path)
        self.store = MobileStore(db_path=self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _pair(self, name: str = "Pixel", *, ttl_s: int = 300) -> dict:
        created = self.store.create_pairing_code(ttl_s=ttl_s)
        return self.store.complete_pairing(code=created["code"], name=name)


class PairingTest(MobileStoreTestCase):
    def test_pairing_creates_device_and_token_authenticates(self):
        paired = self._pair()

        device = self.store.authenticate(paired["token"])

        self.assertIsNotNone(device)
        self.assertEqual(device["device_id"], paired["device_id"])
        self.assertEqual(device["name"], "Pixel")
        self.assertIsNone(device["revoked_at"])

    def test_pairing_code_is_single_use(self):
        created = self.store.create_pairing_code()
        self.store.complete_pairing(code=created["code"], name="First")

        with self.assertRaises(ValueError) as ctx:
            self.store.complete_pairing(code=created["code"], name="Second")

        self.assertIn("already used", str(ctx.exception))

    def test_pairing_code_expires(self):
        created = self.store.create_pairing_code(ttl_s=60, now=1_000)

        with self.assertRaises(ValueError) as ctx:
            self.store.complete_pairing(code=created["code"], name="Late", now=1_061)

        self.assertIn("expired", str(ctx.exception))

    def test_invalid_pairing_code_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.complete_pairing(code="DEAD-BEEF-0000", name="Nope")

        self.assertIn("invalid", str(ctx.exception))

    def test_pairing_code_accepts_lowercase_and_dashes(self):
        created = self.store.create_pairing_code()
        messy = created["code"].lower().replace("-", " ")

        paired = self.store.complete_pairing(code=messy, name="Pixel")

        self.assertTrue(paired["token"])


class DeviceAuthTest(MobileStoreTestCase):
    def test_authenticate_rejects_unknown_token(self):
        self.assertIsNone(self.store.authenticate("not-a-real-token"))

    def test_authenticate_rejects_empty_token(self):
        self.assertIsNone(self.store.authenticate(""))

    def test_revoked_device_cannot_authenticate(self):
        paired = self._pair()
        self.assertIsNotNone(self.store.authenticate(paired["token"]))

        revoked = self.store.revoke_device(paired["device_id"])

        self.assertTrue(revoked)
        self.assertIsNone(self.store.authenticate(paired["token"]))

    def test_revoke_is_idempotent(self):
        paired = self._pair()

        self.assertTrue(self.store.revoke_device(paired["device_id"]))
        self.assertFalse(self.store.revoke_device(paired["device_id"]))

    def test_list_devices_hides_revoked_by_default(self):
        kept = self._pair(name="Kept")
        gone = self._pair(name="Gone")
        self.store.revoke_device(gone["device_id"])

        active_ids = {d["device_id"] for d in self.store.list_devices()}
        all_ids = {d["device_id"] for d in self.store.list_devices(include_revoked=True)}

        self.assertEqual(active_ids, {kept["device_id"]})
        self.assertEqual(all_ids, {kept["device_id"], gone["device_id"]})

    def test_delete_device_removes_row_from_every_listing(self):
        paired = self._pair(name="Old")

        self.assertTrue(self.store.delete_device(paired["device_id"]))

        self.assertFalse(self.store.delete_device(paired["device_id"]))
        self.assertEqual(self.store.list_devices(), [])
        self.assertEqual(self.store.list_devices(include_revoked=True), [])
        self.assertIsNone(self.store.get_device(paired["device_id"]))

    def test_deleted_device_token_stops_authenticating(self):
        paired = self._pair()
        self.assertIsNotNone(self.store.authenticate(paired["token"]))

        self.store.delete_device(paired["device_id"])

        self.assertIsNone(self.store.authenticate(paired["token"]))

    def test_purge_revoked_devices_keeps_active_ones(self):
        kept = self._pair(name="Kept")
        gone_one = self._pair(name="Gone1")
        gone_two = self._pair(name="Gone2")
        self.store.revoke_device(gone_one["device_id"])
        self.store.revoke_device(gone_two["device_id"])

        deleted = self.store.purge_revoked_devices()

        self.assertEqual(deleted, 2)
        self.assertEqual(
            [d["device_id"] for d in self.store.list_devices(include_revoked=True)],
            [kept["device_id"]],
        )

    def test_purge_revoked_devices_is_noop_without_revoked(self):
        kept = self._pair(name="Kept")

        self.assertEqual(self.store.purge_revoked_devices(), 0)
        self.assertEqual([d["device_id"] for d in self.store.list_devices()], [kept["device_id"]])

    def test_device_token_is_not_stored_in_plaintext(self):
        paired = self._pair()

        raw = Path(self.db_path).read_bytes()
        self.assertNotIn(paired["token"].encode("utf-8"), raw)

        with sqlite3.connect(self.db_path) as conn:
            stored = conn.execute(
                "SELECT token_hash FROM mobile_devices WHERE device_id = ?",
                (paired["device_id"],),
            ).fetchone()

        self.assertIsNotNone(stored)
        self.assertNotEqual(stored[0], paired["token"])
        self.assertEqual(len(stored[0]), 64)

    def test_authentication_updates_last_seen(self):
        paired = self._pair()

        device = self.store.authenticate(paired["token"], now=5_000)

        self.assertIsNotNone(device)
        self.assertEqual(device["last_seen_at"], 5_000)


class EventJournalTest(MobileStoreTestCase):
    def test_seq_is_monotonic(self):
        first = self.store.append_event(kind="task.start", session_id="s1", task_id="t1")
        second = self.store.append_event(kind="tool.result", session_id="s1", task_id="t1")

        self.assertEqual(second, first + 1)
        self.assertEqual(self.store.max_seq(), second)

    def test_list_events_returns_only_after_seq(self):
        self.store.append_event(kind="task.start", session_id="s1")
        second = self.store.append_event(kind="tool.request_batch", session_id="s1")
        self.store.append_event(kind="task.finish", session_id="s1")

        events = self.store.list_events(after_seq=1)

        self.assertEqual([e["seq"] for e in events], [second, second + 1])
        self.assertEqual(events[0]["kind"], "tool.request_batch")

    def test_list_events_filters_by_session(self):
        self.store.append_event(kind="task.start", session_id="s1")
        self.store.append_event(kind="task.start", session_id="s2")

        events = self.store.list_events(session_id="s2")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["session_id"], "s2")

    def test_list_events_respects_limit(self):
        for index in range(5):
            self.store.append_event(kind="tool.result", session_id="s1")

        events = self.store.list_events(limit=2)

        self.assertEqual(len(events), 2)

    def test_payload_roundtrip_preserves_unicode(self):
        payload = {"tool": "Bash", "command": "echo 'привет'", "exit_code": 0}
        self.store.append_event(kind="tool.result", payload=payload, session_id="s1")

        events = self.store.list_events()

        self.assertEqual(events[0]["payload"], payload)

    def test_event_exists_detects_duplicate(self):
        self.store.append_event(kind="task.finish", session_id="s1", task_id="t1")

        self.assertTrue(self.store.event_exists(kind="task.finish", session_id="s1", task_id="t1"))
        self.assertFalse(self.store.event_exists(kind="task.finish", session_id="s1", task_id="t2"))


class PairingCodeCleanupTest(MobileStoreTestCase):
    def test_prune_removes_expired_codes(self):
        self.store.create_pairing_code(ttl_s=60, now=1_000)

        removed = self.store.prune_pairing_codes(older_than_s=3600, now=10_000)

        self.assertEqual(removed, 1)

    def test_prune_keeps_fresh_codes(self):
        self.store.create_pairing_code(ttl_s=600, now=10_000)

        removed = self.store.prune_pairing_codes(older_than_s=3600, now=10_000)

        self.assertEqual(removed, 0)


if __name__ == "__main__":
    unittest.main()
