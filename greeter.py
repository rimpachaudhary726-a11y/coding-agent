"""Utility module for greeting users."""

def greet(name: str) -> str:
    """Return a greeting string for the given name.

    This function generates a friendly greeting for the provided name.

    Args:
        name: The name of the person to greet.

    Returns:
        A greeting message in the format "Hello, <name>".
    """
    return "Hello, " + name
