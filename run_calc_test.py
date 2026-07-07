"""Simple script to test the calculator functions manually."""

from calculator import add, subtract, multiply, divide

def main():
    print("add(2, 3) =", add(2, 3))
    print("subtract(5, 3) =", subtract(5, 3))
    print("multiply(4, 5) =", multiply(4, 5))
    print("divide(10, 2) =", divide(10, 2))
    # Test divide by zero handling
    try:
        divide(1, 0)
    except ValueError as e:
        print("divide by zero correctly raised ValueError:", e)

if __name__ == "__main__":
    main()
