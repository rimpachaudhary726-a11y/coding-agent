# Simple division function

def divide(a, b):
    if b == 0:
        return None  # Avoid division by zero
    return a / b

# Test the function
print(divide(10, 0))
