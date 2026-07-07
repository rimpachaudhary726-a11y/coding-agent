"""custom_tools.py — tools the agent has created for itself over time."""



# --- count_python_lines ---
def count_python_lines(file_path: str) -> str:
    """Return the number of lines in the given Python file.
    Returns a string with the count or an error message.
    """
    import os
    if not os.path.exists(file_path):
        return f"ERROR: {file_path} does not exist."
    if not file_path.lower().endswith('.py'):
        return f"ERROR: {file_path} is not a Python file."
    try:
        with open(file_path, 'r', errors='ignore') as f:
            lines = f.readlines()
        count = len(lines)
        return f"{count}"
    except Exception as e:
        return f"ERROR: Could not read file: {e}"


# --- word_count ---
def word_count(path: str) -> int:
    """Return the number of words in the file at *path*.

    A word is defined as any sequence of characters separated by whitespace.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        raise RuntimeError(f"Failed to read file {path}: {e}")
    # Split on any whitespace
    words = content.split()
    return len(words)

