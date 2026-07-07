import sys
from string_evaluator import evaluate_expression

def assert_almost_equal(a, b, tol=1e-9):
    if abs(a - b) > tol:
        raise AssertionError(f"{a} != {b}")

def main():
    # Basic operations
    try:
        assert_almost_equal(evaluate_expression("3 + 5 * 2 - 8 / 4"), 3 + 5 * 2 - 8 / 4)
        assert_almost_equal(evaluate_expression("10 + 2 * 6"), 10 + 2 * 6)
        assert_almost_equal(evaluate_expression("100 * 2 + 12"), 100 * 2 + 12)
        assert_almost_equal(evaluate_expression("100 * 2 + 12 / 4"), 100 * 2 + 12 / 4)
        # Spaces handling
        assert_almost_equal(evaluate_expression("   7   +   3 *   2   "), 7 + 3 * 2)
        # Division by zero
        try:
            evaluate_expression("10 / 0")
            raise AssertionError("Division by zero did not raise")
        except ZeroDivisionError:
            pass
        # Invalid character
        try:
            evaluate_expression("2 & 3")
            raise AssertionError("Invalid character did not raise")
        except SyntaxError:
            pass
    except Exception as e:
        print("Test failed:", e)
        sys.exit(1)
    print("All tests passed")
    sys.exit(0)

if __name__ == "__main__":
    main()
