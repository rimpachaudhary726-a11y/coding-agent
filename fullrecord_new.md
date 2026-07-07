# Assistant Full Record

This file provides a comprehensive overview of the coding assistant, its capabilities, and the toolset it can use to interact with the repository.

## Who am I?
I am an AI coding assistant powered by OpenAI's GPT‑4 architecture. I can read, write, edit, and navigate files in the project, run shell commands, manage git operations, and even control a headless browser for frontend testing. I work incrementally: I investigate first, make the smallest safe change, run tests, and only then confirm that a task is complete.

## Core Capabilities
- **File Operations**: read, write, edit, stage changes, review diffs, discard pending edits.
- **Directory & Search**: list directory contents, grep‑style search across the codebase, glob patterns.
- **Shell & Git**: run bash commands, commit changes, push branches, create new branches, open pull requests.
- **Testing**: automatically detect and run the project’s test suite (pytest, npm, etc.).
- **Frontend Interaction**: navigate URLs, click elements, type into inputs, take screenshots, crop images, and compare rendered UI to design mockups.
- **Project‑Specific Helpers**: scaffolding for Firebase auth & Firestore, agent‑system generator.
- **Diagnostics**: language‑server diagnostics for Python, AST‑aware find‑definition/callers/references.
- **MCP Integration**: connect to external Model Context Protocol servers for additional capabilities.

## Available Tools (excerpt)
```
read_file(path, line_start?, line_end?)
write_file(path, content)
edit_file(path, old_text, new_text)
stage_write_file(path, content)
stage_edit_file(path, old_text, new_text)
review_pending_changes()
apply_pending_changes(confirmed?)
list_directory(path?)
search_codebase(query, root?, extensions?, use_regex?)
run_bash(command, confirmed?, timeout?)
revert_file(path)
... (and many more – see the full tool list in the system prompt)
```

## How I Work
1. **Investigate** – I explore the repository using the tools above.
2. **Plan** – For tasks with multiple steps I create a to‑do list.
3. **Implement** – I make the smallest change possible, often using `edit_file` or `write_file`.
4. **Verify** – I run the test suite with `detect_and_run_tests` or execute relevant commands.
5. **Confirm** – Once the change passes all checks I mark the task as done.

## My “Code”
The assistant does not have a single source‑code file in this repository. Its behaviour is defined by the system prompt you are reading now, which outlines the toolset and workflow rules. The actual logic runs on the OpenAI platform and interacts with the repository via the functions listed above.

## CLI
Below is an illustrative example of a command‑line interface that could be used to drive the assistant’s functionality. It is *not* part of the repository’s runtime code, but serves as documentation of how one might invoke the provided tools from a script.

```python
#!/usr/bin/env python3
"""Simple CLI wrapper for the AI assistant tools."""
import argparse

# Placeholder imports – in the real environment these would map to the tool functions
# from functions import list_directory, run_bash, detect_and_run_tests, ...

def list_repo(args):
    # Example call to list_directory tool (actual call is via the agent, not Python import)
    print("Listing repository root:")
    # print(list_directory({"path": "."}))

def run_tests(args):
    print("Running test suite:")
    # print(detect_and_run_tests({}))

def main():
    parser = argparse.ArgumentParser(description="AI Assistant CLI")
    subparsers = parser.add_subparsers(dest="command")

    parser_list = subparsers.add_parser("list", help="List files in a directory")
    parser_list.set_defaults(func=list_repo)

    parser_test = subparsers.add_parser("test", help="Run the project test suite")
    parser_test.set_defaults(func=run_tests)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
```

---
*Generated automatically by the AI assistant.*

---
## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


## File: 


