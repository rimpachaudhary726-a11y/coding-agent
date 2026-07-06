# This module provides a simple division function
# Simple division function

def divide(a, b):
    # Input validation: ensure both arguments are numbers (int or float)
    if not isinstance(a, (int, float)):
        raise TypeError(f"Argument 'a' must be a number, got {type(a).__name__}")
    if not isinstance(b, (int, float)):
        raise TypeError(f"Argument 'b' must be a number, got {type(b).__name__}")
    # Avoid division by zero
    if b == 0:
        return None
    return a / b

# Test the function
print(divide(10, 0))
