"""Reviewed skill resource checks authored by Flint on temporary files."""

import os
import tempfile
import unittest
from pathlib import Path

from factory.core.skills_manager import Skill


class TestSkillResourcesFlint(unittest.TestCase):
    def test_to_metadata_dict_does_not_read_or_cache_body(self):
        with tempfile.TemporaryDirectory() as td:
            skill = Skill(
                skill_id="s1",
                name="Skill One",
                body_path=os.path.join(td, "missing.md"),
                triggers=["x"],
                description="desc",
            )
            meta = skill.to_metadata_dict()
            self.assertEqual(meta["skill_id"], "s1")
            self.assertEqual(meta["description"], "desc")
            self.assertEqual(meta["triggers_count"], 1)
            self.assertEqual(skill.content, "")

    def test_to_dict_loads_and_caches_body(self):
        with tempfile.TemporaryDirectory() as td:
            body_path = Path(td) / "SKILL.md"
            body_path.write_text("---\ntitle: x\n---\nHello body", encoding="utf-8")
            skill = Skill(
                skill_id="s2", name="Skill Two", body_path=str(body_path), triggers=[]
            )
            self.assertEqual(skill.to_dict()["content"], "Hello body")
            body_path.write_text("Changed body", encoding="utf-8")
            self.assertEqual(skill.to_dict()["content"], "Hello body")

        with tempfile.TemporaryDirectory() as td:
            skill = Skill(
                skill_id="s3",
                name="Skill Three",
                body_path=os.path.join(td, "nope.md"),
                triggers=[],
            )
            self.assertEqual(skill.to_dict()["content"], "")

    def test_load_content_frontmatter_cache_and_no_closing(self):
        with tempfile.TemporaryDirectory() as td:
            body_path = Path(td) / "SKILL.md"
            body_path.write_text("---\ntitle: x\n---\nActual content", encoding="utf-8")
            skill = Skill(
                skill_id="s4", name="Skill Four", body_path=str(body_path), triggers=[]
            )
            self.assertEqual(skill.load_content(), "Actual content")
            body_path.write_text("Other content", encoding="utf-8")
            self.assertEqual(skill.load_content(), "Actual content")

        with tempfile.TemporaryDirectory() as td:
            body_path = Path(td) / "SKILL.md"
            body_path.write_text(
                "---\ntitle: x\nNo closing here\nKeep me", encoding="utf-8"
            )
            skill = Skill(
                skill_id="s5", name="Skill Five", body_path=str(body_path), triggers=[]
            )
            with self.subTest(case="no closing marker keeps content"):
                self.assertEqual(
                    skill.load_content(), "---\ntitle: x\nNo closing here\nKeep me"
                )

    def test_bundled_files(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / "pkg"
            (pkg / "scripts").mkdir(parents=True)
            (pkg / "scripts" / "a.py").write_text("x")
            (pkg / "scripts" / "sub").mkdir()
            (pkg / "scripts" / "sub" / "b.py").write_text("x")
            (pkg / "references").mkdir(parents=True)
            (pkg / "references" / "r.md").write_text("x")
            (pkg / "assets").mkdir(parents=True)
            (pkg / "assets" / "i.png").write_text("x")
            (pkg / "SKILL.md").write_text("x")

            skill = Skill(
                skill_id="s6",
                name="Skill Six",
                body_path="",
                triggers=[],
                package_dir=str(pkg),
            )
            files = skill.bundled_files()
            self.assertNotIn("SKILL.md", files)
            self.assertIn(str(Path("scripts/a.py")), files)
            self.assertIn(str(Path("scripts/sub/b.py")), files)
            self.assertIn(str(Path("references/r.md")), files)
            self.assertIn(str(Path("assets/i.png")), files)
            self.assertEqual(files, sorted(files))
            self.assertEqual(len(files), 4)

            with self.subTest(case="limit"):
                self.assertEqual(
                    skill.bundled_files(limit=2), sorted(skill.bundled_files())[:2]
                )

        with tempfile.TemporaryDirectory() as td:
            with self.subTest(case="missing package_dir"):
                skill = Skill(
                    skill_id="s7",
                    name="Skill Seven",
                    body_path="",
                    triggers=[],
                    package_dir="",
                )
                self.assertEqual(skill.bundled_files(), [])
            with self.subTest(case="package_dir not a dir"):
                skill = Skill(
                    skill_id="s8",
                    name="Skill Eight",
                    body_path="",
                    triggers=[],
                    package_dir=os.path.join(td, "missing"),
                )
                self.assertEqual(skill.bundled_files(), [])

    def test_resource_dirs_and_env_files(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / "pkg"
            (pkg / "scripts").mkdir(parents=True)
            (pkg / "references").mkdir(parents=True)
            (pkg / "env").mkdir(parents=True)
            (pkg / "env" / "b.env").write_text("x")
            (pkg / "env" / "a.env").write_text("x")
            (pkg / "env" / "c.txt").write_text("x")

            skill = Skill(
                skill_id="s9",
                name="Skill Nine",
                body_path="",
                triggers=[],
                package_dir=str(pkg),
            )
            self.assertEqual(skill.resource_dirs(), ["scripts", "references"])
            self.assertEqual([p.name for p in skill.env_files()], ["a.env", "b.env"])

        with tempfile.TemporaryDirectory() as td:
            with self.subTest(case="no package_dir"):
                skill = Skill(
                    skill_id="s10",
                    name="Skill Ten",
                    body_path="",
                    triggers=[],
                    package_dir="",
                )
                self.assertEqual(skill.resource_dirs(), [])
                self.assertEqual(list(skill.env_files()), [])
            with self.subTest(case="missing resources"):
                skill = Skill(
                    skill_id="s11",
                    name="Skill Eleven",
                    body_path="",
                    triggers=[],
                    package_dir=os.path.join(td, "missing"),
                )
                self.assertEqual(skill.resource_dirs(), [])
                self.assertEqual(list(skill.env_files()), [])

    def test_supports_backend(self):
        skill = Skill(
            skill_id="s12",
            name="Skill Twelve",
            body_path="",
            triggers=[],
            backend_filter=["Claude"],
        )
        self.assertTrue(skill.supports_backend("Claude"))
        self.assertTrue(skill.supports_backend("  claude  "))
        self.assertFalse(skill.supports_backend("Codex"))

        with self.subTest(case="no filter allows any backend"):
            skill = Skill(
                skill_id="s13",
                name="Skill Thirteen",
                body_path="",
                triggers=[],
                backend_filter=None,
            )
            self.assertTrue(skill.supports_backend("Anything"))
        with self.subTest(case="empty filter allows any backend"):
            skill = Skill(
                skill_id="s14",
                name="Skill Fourteen",
                body_path="",
                triggers=[],
                backend_filter=[],
            )
            self.assertTrue(skill.supports_backend("Anything"))


if __name__ == "__main__":
    unittest.main()
