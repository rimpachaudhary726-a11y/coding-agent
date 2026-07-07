import unittest
from string_evaluator import evaluate_expression

class TestStringEvaluator(unittest.TestCase):
    def test_basic_operations(self):
        self.assertAlmostEqual(evaluate_expression("3 + 5 * 2 - 8 / 4"), 3 + 5 * 2 - 8 / 4)
        self.assertAlmostEqual(evaluate_expression("10 + 2 * 6"), 10 + 2 * 6)
        self.assertAlmostEqual(evaluate_expression("100 * 2 + 12"), 100 * 2 + 12)
        self.assertAlmostEqual(evaluate_expression("100 * 2 + 12 / 4"), 100 * 2 + 12 / 4)
    def test_spaces(self):
        expr = "   7   +   3 *   2   "
        self.assertAlmostEqual(evaluate_expression(expr), 7 + 3 * 2)
    def test_division_by_zero(self):
        with self.assertRaises(ZeroDivisionError):
            evaluate_expression("10 / 0")
    def test_invalid_character(self):
        with self.assertRaises(SyntaxError):
            evaluate_expression("2 & 3")

if __name__ == '__main__':
    unittest.main()
