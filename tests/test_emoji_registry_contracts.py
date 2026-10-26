import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import custom_emoji_registry as subject


class TestEmojiRegistryContracts(unittest.TestCase):
    def test_register_from_json_creates_custom_1_with_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            with (
                patch.object(subject, "REGISTRY_PATH", new=registry_path),
                patch.object(subject, "_registry", new={}),
                patch.object(subject, "_next_alias_num", new=1),
            ):
                registered = subject.register_from_json(
                    {
                        "text": "A",
                        "entities": [
                            {
                                "type": "custom_emoji",
                                "custom_emoji_id": "123",
                                "offset": 0,
                                "length": 1,
                            }
                        ],
                    }
                )
                self.assertEqual(len(registered), 1)
                self.assertEqual(registered[0]["alias"], "custom_1")
                self.assertEqual(registered[0]["fallback"], "A")

                entry = subject.get_emoji("123")
                self.assertEqual(entry, registered[0])
                self.assertEqual(subject.get_emoji_by_alias("custom_1"), entry)
                self.assertEqual(subject.list_all(), [entry])
                self.assertIn("custom_1", subject.format_for_prompt())
                self.assertIn("fallback: A", subject.format_for_prompt())

                self.assertTrue(registry_path.exists())
                data = json.loads(registry_path.read_text(encoding="utf-8"))
                self.assertEqual(data["123"]["alias"], "custom_1")
                self.assertEqual(data["123"]["fallback"], "A")

    def test_register_same_id_keeps_single_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "registry.json"
            with (
                patch.object(subject, "REGISTRY_PATH", new=registry_path),
                patch.object(subject, "_registry", new={}),
                patch.object(subject, "_next_alias_num", new=1),
            ):
                subject.register_from_json(
                    {
                        "text": "A",
                        "entities": [
                            {
                                "type": "custom_emoji",
                                "custom_emoji_id": "123",
                                "offset": 0,
                                "length": 1,
                            }
                        ],
                    },
                    alias="fixture",
                )
                subject.register_from_json(
                    {
                        "text": "B",
                        "entities": [
                            {
                                "type": "custom_emoji",
                                "custom_emoji_id": "123",
                                "offset": 0,
                                "length": 1,
                            }
                        ],
                    },
                    alias="ignored",
                )

                self.assertEqual(len(subject.list_all()), 1)
                entry = subject.list_all()[0]
                self.assertEqual(entry["alias"], "fixture")
                self.assertEqual(entry["fallback"], "B")

                subject._registry.clear()
                subject._load()

                reloaded = subject.get_emoji("123")
                self.assertEqual(reloaded["alias"], "fixture")
                self.assertEqual(reloaded["fallback"], "B")
