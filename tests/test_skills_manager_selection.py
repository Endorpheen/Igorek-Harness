import unittest

from factory.core.skills_manager import Skill, SkillsManager


class SkillsManagerSelectionTest(unittest.TestCase):
    def make_manager(self) -> SkillsManager:
        manager = SkillsManager(skills_dirs=[])
        manager.is_loaded = True
        manager.skills = {
            "arxiv": Skill(
                skill_id="arxiv",
                name="arxiv",
                body_path="/tmp/arxiv/SKILL.md",
                triggers=["paper", "research", "academic"],
                description="Search academic papers from arXiv.",
            ),
            "openskills": Skill(
                skill_id="openskills",
                name="OpenSkills - Набор Агентов для AI",
                body_path="/tmp/openskills.md",
                triggers=["skill", "skills", "AI"],
                description="Collection of AI skills.",
            ),
            "github-code-review": Skill(
                skill_id="github-code-review",
                name="github-code-review",
                body_path="/tmp/github-code-review/SKILL.md",
                triggers=["review", "code", "github"],
                description="Review code changes.",
            ),
        }
        return manager

    def test_explicit_skill_name_is_selected_first(self):
        manager = self.make_manager()

        selected = manager.find_relevant_skills(
            "используй skill arxiv и найди свежую статью про tool use",
            max_skills=3,
        )

        self.assertGreaterEqual(len(selected), 1)
        self.assertEqual(selected[0].skill_id, "arxiv")
        self.assertIn("arxiv", [skill.skill_id for skill in selected])

    def test_slash_skill_name_is_selected_first(self):
        manager = self.make_manager()

        selected = manager.find_relevant_skills("/arxiv найди статью", max_skills=3)

        self.assertGreaterEqual(len(selected), 1)
        self.assertEqual(selected[0].skill_id, "arxiv")

    def test_explicitly_mentioned_skills_are_reported(self):
        manager = self.make_manager()

        selected = manager.find_explicitly_mentioned_skills(
            "используй skill arxiv и найди статью",
        )

        self.assertEqual([skill.skill_id for skill in selected], ["arxiv"])

    def test_dollar_skill_name_is_explicitly_selected(self):
        manager = self.make_manager()

        selected = manager.find_explicitly_mentioned_skills(
            "$arxiv найди статью",
        )

        self.assertEqual([skill.skill_id for skill in selected], ["arxiv"])


if __name__ == "__main__":
    unittest.main()
