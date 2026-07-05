"""
agent.py — the core reasoning loop, plus fan-out for big tasks.

Core loop: send the conversation + tool schema to the model, execute
whatever tool_calls come back, feed results back in, repeat until the
model responds with plain text and no more tool calls (task done).

Fan-out: for tasks that name multiple files/targets, this spins up one
independent Core Agent per target, running in parallel (ThreadPoolExecutor,
same primitive main-11.py already imports). This is the "tree" — a root
call decides whether a task needs to fan out, then each leaf is a full
instance of run_agent() scoped to one file, and results merge back up
into a single summary. Not literally 1000 agents for every request —
just for requests that actually name that many independent targets.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from provider_pool import ask_ai
from tools import TOOL_SCHEMA, TOOL_FUNCTIONS, _matches_any, _DESTRUCTIVE_PATTERNS
from firebase_tools import FIREBASE_TOOL_SCHEMA, FIREBASE_TOOL_FUNCTIONS
from github_tools import GITHUB_TOOL_SCHEMA, GITHUB_TOOL_FUNCTIONS
import task_memory

TOOL_SCHEMA = TOOL_SCHEMA + FIREBASE_TOOL_SCHEMA + GITHUB_TOOL_SCHEMA
TOOL_FUNCTIONS = {**TOOL_FUNCTIONS, **FIREBASE_TOOL_FUNCTIONS, **GITHUB_TOOL_FUNCTIONS}

SYSTEM_PROMPT = """You are a coding agent with direct access to the filesystem \
and shell via tools. You can read, write, and edit files, search the codebase, \
run commands, and commit to git. Work step by step: investigate before you \
change anything, make the smallest correct change, and verify your work \
(run tests or the relevant command) before declaring the task done. \
When a task is genuinely finished, reply with plain text and no further tool calls.

When looking for a file, check the project root first (list_directory(".")) \
before searching subfolders. Ignore artifacts/, node_modules/, lib/, .cache/, \
and other tooling/dependency folders unless the user's request specifically \
points there — the user's own code almost always lives at the project root.

If the project has a test suite, call detect_and_run_tests after making code \
changes, before declaring the task done — don't just assume a fix works."""

MAX_TURNS = 40


def _execute_tool_call(tool_call, confirm_destructive=False, confirm_callback=None):
    name = tool_call["function"]["name"]
    try:
        args = json.loads(tool_call["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return f"ERROR: could not parse arguments for {name}"

    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return f"ERROR: unknown tool '{name}'"

    if name == "run_bash":
        command = args.get("command", "")
        if confirm_destructive:
            args["confirmed"] = True
        elif confirm_callback and _matches_any(command.strip(), _DESTRUCTIVE_PATTERNS):
            # Real pause here — ask a human before running anything destructive,
            # instead of just reporting CONFIRMATION_REQUIRED back to the model
            # and moving on.
            allowed = confirm_callback(command)
            if allowed:
                args["confirmed"] = True
            else:
                return f"Command declined by user: '{command}'. Not run. Try a different approach."

    try:
        return func(**args)
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}"
    except Exception as e:
        return f"ERROR: {name} raised an exception: {e}"


def run_agent(task, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True):
    """
    Runs the core tool-calling loop until the model stops calling tools
    or MAX_TURNS is hit.

    `on_step` — optional callback(message_dict) for live CLI output.
    `auto_confirm` — skips confirmation entirely, destructive commands just
        run (used by fan-out leaves running unattended).
    `confirm_callback` — optional callback(command) -> bool, called for real
        when a destructive command needs a yes/no from an actual human
        (see cli.py for the interactive version). Ignored if auto_confirm=True.
    `use_memory` — pull in relevant past-session summaries for this project
        and save a summary of this task when it finishes. Set False for
        fan-out leaves to avoid many parallel writers hitting the same file.

    Returns the final plain-text response.
    """
    memory_context = ""
    if use_memory:
        relevant = task_memory.retrieve_relevant(task)
        memory_context = task_memory.format_for_prompt(relevant)

    system_content = SYSTEM_PROMPT
    if memory_context:
        system_content += "\n\n" + memory_context

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": task},
    ]

    for turn in range(MAX_TURNS):
        message = ask_ai(messages, tools=TOOL_SCHEMA)

        if isinstance(message, dict) and "error" in message:
            return f"ERROR: {message['error']}"

        # message is the raw API message dict — normalize access
        content = message.get("content") if isinstance(message, dict) else message.content
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else message.tool_calls

        if on_step:
            on_step({"turn": turn, "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            final = content or "(no response)"
            if use_memory:
                task_memory.add_task_summary(task, final)
            return final

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            tc_dict = tc if isinstance(tc, dict) else {
                "id": tc.id,
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            result = _execute_tool_call(
                tc_dict, confirm_destructive=auto_confirm, confirm_callback=confirm_callback
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc_dict["id"],
                "content": str(result)[:6000],  # cap so one huge result can't blow the context
            })

    timeout_msg = "Reached max turns without finishing — task may be too large for one run."
    if use_memory:
        task_memory.add_task_summary(task, timeout_msg)
    return timeout_msg


def fan_out(task_template, targets, max_workers=8, auto_confirm=True):
    """
    Runs run_agent() once per target in parallel, filling {target} into
    task_template for each. Example:
      fan_out("Review {target} for bugs and list any you find.",
               ["auth.py", "payments.py", "db.py"])
    Returns {target: result} for all targets. max_workers caps real
    concurrency (and therefore concurrent API calls) — raise cautiously,
    your Cerebras key pool is the real ceiling on how many can run at once
    without hitting rate limits. use_memory is off for leaves to avoid
    many parallel workers writing to the same .agent_memory.json at once.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_target = {
            executor.submit(
                run_agent, task_template.format(target=t),
                None, auto_confirm, None, False
            ): t
            for t in targets
        }
        for future in as_completed(future_to_target):
            target = future_to_target[future]
            try:
                results[target] = future.result()
            except Exception as e:
                results[target] = f"ERROR: {e}"
    return results
