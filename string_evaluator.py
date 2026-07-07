"""
Utility to evaluate simple arithmetic expressions without using eval.
Supported operators: +, -, *, / with standard precedence and left-to-right associativity.
Parentheses are NOT supported (as not requested).
"""

from __future__ import annotations

import re
from typing import List, Tuple

# Token types
NUMBER = "NUMBER"
OP = "OP"

Token = Tuple[str, str]  # (type, value)

def tokenize(expr: str) -> List[Token]:
    """Convert the expression string into a list of tokens.

    Handles integers and floats, and the four basic operators. Ignores whitespace.
    """
    token_specification = [
        (NUMBER, r"\d+(?:\.\d*)?"),  # Integer or decimal number
        (OP, r"[+\-*/]"),             # Arithmetic operators
        ("SKIP", r"[ \t]+"),        # Skip over spaces and tabs
    ]
    tok_regex = "|".join(f"(?P<{name}>{pattern})" for name, pattern in token_specification)
    get_token = re.compile(tok_regex).match
    pos = 0
    tokens: List[Token] = []
    while pos < len(expr):
        m = get_token(expr, pos)
        if not m:
            raise SyntaxError(f"Unexpected character {expr[pos]!r} at position {pos}")
        typ = m.lastgroup
        if typ == "SKIP":
            pos = m.end()
            continue
        val = m.group(typ)
        tokens.append((typ, val))
        pos = m.end()
    return tokens

# Operator precedence
_PRECEDENCE = {
    "+": 1,
    "-": 1,
    "*": 2,
    "/": 2,
}

def shunting_yard(tokens: List[Token]) -> List[Token]:
    """Convert infix tokens to Reverse Polish Notation using the Shunting‑Yard algorithm."""
    output: List[Token] = []
    operators: List[Token] = []
    for typ, val in tokens:
        if typ == NUMBER:
            output.append((typ, val))
        elif typ == OP:
            while operators:
                top_typ, top_val = operators[-1]
                if top_typ == OP and _PRECEDENCE[top_val] >= _PRECEDENCE[val]:
                    output.append(operators.pop())
                else:
                    break
            operators.append((typ, val))
        else:
            raise SyntaxError(f"Unsupported token type: {typ}")
    while operators:
        output.append(operators.pop())
    return output

def evaluate_rpn(rpn_tokens: List[Token]) -> float:
    """Evaluate a list of tokens in Reverse Polish Notation and return the result."""
    stack: List[float] = []
    for typ, val in rpn_tokens:
        if typ == NUMBER:
            stack.append(float(val))
        elif typ == OP:
            if len(stack) < 2:
                raise SyntaxError("Insufficient values in expression")
            b = stack.pop()
            a = stack.pop()
            if val == "+":
                res = a + b
            elif val == "-":
                res = a - b
            elif val == "*":
                res = a * b
            elif val == "/":
                if b == 0:
                    raise ZeroDivisionError("division by zero")
                res = a / b
            else:
                raise SyntaxError(f"Unknown operator {val}")
            stack.append(res)
        else:
            raise SyntaxError(f"Unsupported token type {typ}")
    if len(stack) != 1:
        raise SyntaxError("The user input has too many values")
    return stack[0]

def evaluate_expression(expr: str) -> float:
    """Public API – evaluate a simple arithmetic expression string.

    Parameters
    ----------
    expr: str
        Expression like "3 + 5 * 2 - 8 / 4". Whitespace is ignored.

    Returns
    -------
    float
        Result of the evaluation.
    """
    tokens = tokenize(expr)
    rpn = shunting_yard(tokens)
    return evaluate_rpn(rpn)

if __name__ == "__main__":
    # Simple manual test runner
    test_cases = [
        ("3 + 5 * 2 - 8 / 4", 3 + 5 * 2 - 8 / 4),
        ("10 + 2 * 6", 10 + 2 * 6),
        ("100 * 2 + 12", 100 * 2 + 12),
        ("100 * ( 2 + 12 )", None),  # parentheses not supported – should raise
        ("100 * ( 2 + 12 ) / 14", None),
    ]
    for expr, expected in test_cases:
        try:
            result = evaluate_expression(expr)
            print(expr, "=", result, "expected", expected)
        except Exception as e:
            print(expr, "raised", type(e).__name__, e)
