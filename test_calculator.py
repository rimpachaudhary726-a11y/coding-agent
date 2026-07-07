"""Tests for the simple calculator module.

These tests use the built‑in assert statement so they can be run directly with
`python -m pytest` or simply executed as a script.
"""

from calculator import add, subtract, multiply, divide

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0, 0) == 0

def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(0, 5) == -5
    assert subtract(-2, -3) == 1

def test_multiply():
    assert multiply(4, 5) == 20
    assert multiply(-2, 3) == -6
    assert multiply(0, 100) == 0

def test_divide():
    assert divide(10, 2) == 5
    assert divide(-9, 3) == -3
    assert divide(5, 2) == 2.5

def test_divide_by_zero():
    try:
        divide(1, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError when dividing by zero")
