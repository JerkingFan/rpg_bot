"""Тесты чистых функций RPG-слоя."""
import unittest

from services.rpg import (
    DAILY_COIN_CAP,
    DAILY_XP_CAP,
    mastery_bar,
    rank_for_level,
    skill_for_topic,
    xp_bar,
)


class SkillMappingTests(unittest.TestCase):
    def test_known_skills(self):
        self.assertEqual(skill_for_topic("Алгебра 7кл §1"), "algebra")
        self.assertEqual(skill_for_topic("Геометрия. Треугольники"), "geometry")
        self.assertEqual(skill_for_topic("Тригонометрия: sin и cos"), "trigonometry")
        self.assertEqual(skill_for_topic("Производная функции"), "derivatives")

    def test_unknown_falls_back_to_algebra(self):
        self.assertEqual(skill_for_topic(None), "algebra")
        self.assertEqual(skill_for_topic(""), "algebra")
        self.assertEqual(skill_for_topic("Логика"), "algebra")


class RankTests(unittest.TestCase):
    def test_ranks(self):
        self.assertEqual(rank_for_level(1), "Пепел")
        self.assertEqual(rank_for_level(5), "Искра")
        self.assertEqual(rank_for_level(10), "Пламя")
        self.assertEqual(rank_for_level(15), "Феникс")
        self.assertEqual(rank_for_level(25), "Апостол")
        self.assertEqual(rank_for_level(100), "Апостол")


class BarTests(unittest.TestCase):
    def test_xp_bar_bounds(self):
        self.assertEqual(len(xp_bar(0)), 10)
        self.assertTrue(xp_bar(0).startswith("░"))
        self.assertEqual(xp_bar(99), "█████████░")
        self.assertEqual(xp_bar(100), "░░░░░░░░░░")

    def test_mastery_bar_clamps(self):
        self.assertEqual(mastery_bar(-10), "░" * 10)
        self.assertEqual(mastery_bar(0), "░" * 10)
        self.assertEqual(mastery_bar(100), "█" * 10)
        self.assertEqual(mastery_bar(150), "█" * 10)


class CapTests(unittest.TestCase):
    def test_caps_are_positive(self):
        self.assertGreater(DAILY_XP_CAP, 0)
        self.assertGreater(DAILY_COIN_CAP, 0)


if __name__ == "__main__":
    unittest.main()
