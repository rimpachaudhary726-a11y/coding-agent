import unittest
import calc

class TestCalc(unittest.TestCase):
    def test_divide_normal(self):
        self.assertEqual(calc.divide(10, 2), 5)
        self.assertEqual(calc.divide(-4, 2), -2)
        self.assertEqual(calc.divide(5.0, 2), 2.5)

    def test_divide_by_zero(self):
        self.assertIsNone(calc.divide(10, 0))

    def test_divide_invalid_type(self):
        with self.assertRaises(TypeError):
            calc.divide('10', 2)
        with self.assertRaises(TypeError):
            calc.divide(10, '2')
        with self.assertRaises(TypeError):
            calc.divide('10', '2')

if __name__ == '__main__':
    unittest.main()