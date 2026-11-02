"""Тесты проверки ответов. Запуск: .venv/bin/python -m unittest discover -s tests"""
import unittest

from services.answer_checker import check_answer


class CheckAnswerTests(unittest.TestCase):
    def assertAccepted(self, student, expected):
        ok, reason = check_answer(student, expected)
        self.assertTrue(ok, f"{student!r} должен совпасть с {expected!r}: {reason}")

    def assertRejected(self, student, expected):
        ok, reason = check_answer(student, expected)
        self.assertFalse(ok, f"{student!r} не должен совпадать с {expected!r}: {reason}")

    def test_exact_and_numeric(self):
        self.assertAccepted("2", "2")
        self.assertAccepted("2,5", "2.5")
        self.assertAccepted("2.0000", "2")
        self.assertAccepted("1e3", "1000")
        self.assertAccepted("sqrt(4)", "2")
        self.assertAccepted("2 1/2", "2.5")

    def test_number_inside_formula_is_not_enough(self):
        self.assertRejected("12", "2")
        self.assertRejected("-2", "2")
        self.assertRejected("2, 5", "2")
        self.assertRejected("2.0001", "2")

    def test_units_and_equation_form(self):
        self.assertAccepted("x=2", "2")
        self.assertAccepted("30°", "30")
        self.assertAccepted("5 см", "5")

    def test_alternatives(self):
        self.assertAccepted("2", "2|3")
        self.assertAccepted("3", "2|3")
        self.assertRejected("4", "2|3")

    def test_pairs_and_intervals(self):
        self.assertAccepted("(2;3)", "(2;3)")
        self.assertRejected("(3;2)", "(2;3)")
        self.assertAccepted("(-inf, 2)", "x<2")
        self.assertAccepted("x<2", "(-∞; 2)")

    def test_symbolic(self):
        self.assertAccepted("2x", "x*2")
        self.assertAccepted("x^2", "x*x")
        self.assertAccepted("sin(x)", "sin(x)")

    def test_unsafe_input_is_rejected_quickly(self):
        self.assertRejected("9**9**9", "2")
        self.assertRejected("().__class__.__base__", "2")
        self.assertRejected("__import__('os').system('ls')", "2")


if __name__ == "__main__":
    unittest.main()
