import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.skills_tool import _skill_view


class SkillsToolViewTest(unittest.TestCase):
    def test_skill_view_instructions_md_alias_returns_main_skill_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "github-code-review"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: github-code-review\n"
                "description: Review code changes.\n"
                "---\n"
                "\n"
                "# GitHub Code Review\n"
                "\n"
                "Use git diff for bounded reviews.\n",
                encoding="utf-8",
            )

            with patch(
                "factory.core.skills_tool._resolve_skills_dirs",
                return_value=[str(root)],
            ):
                result = _skill_view(
                    {
                        "name": "github-code-review",
                        "file_path": "instructions.md",
                    }
                )

        self.assertIn('"success": true', result)
        self.assertIn("GitHub Code Review", result)
        self.assertNotIn("Skill file not found", result)


if __name__ == "__main__":
    unittest.main()
