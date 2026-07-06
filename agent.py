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
from meta_builder import build_agent_system
from custom_tool_registry import CREATE_TOOL_SCHEMA, CREATE_TOOL_FUNCTIONS, load_custom_tools
import task_memory

META_BUILDER_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "build_agent_system",
        "description": "Generate a complete N-agent simulation system (personas with personality/"
                        "routine/relationships/secrets, memory storage, tick loop, and a live web "
                        "viewer) for a given theme. Use this when asked to build a multi-agent "
                        "simulation, agent society, or agent town for any theme.",
        "parameters": {"type": "object", "properties": {
            "theme": {"type": "string", "description": "e.g. 'a hospital emergency room', 'a space station crew'"},
            "count": {"type": "integer", "default": 30},
            "output_dir": {"type": "string", "default": "."},
        }, "required": ["theme"]},
    }},
]
META_BUILDER_TOOL_FUNCTIONS = {"build_agent_system": build_agent_system}

# Load any tools the agent has created for itself in past sessions
_custom_schema, _custom_functions, _custom_load_errors = load_custom_tools()
for _err in _custom_load_errors:
    print(f"[custom tools] {_err}")

TOOL_SCHEMA = (
    TOOL_SCHEMA + FIREBASE_TOOL_SCHEMA + GITHUB_TOOL_SCHEMA
    + META_BUILDER_TOOL_SCHEMA + CREATE_TOOL_SCHEMA + _custom_schema
)
TOOL_FUNCTIONS = {
    **TOOL_FUNCTIONS, **FIREBASE_TOOL_FUNCTIONS, **GITHUB_TOOL_FUNCTIONS,
    **META_BUILDER_TOOL_FUNCTIONS, **CREATE_TOOL_FUNCTIONS, **_custom_functions,
}


def _refresh_custom_tools():
    """
    Called right after create_tool runs successfully, so the new tool is
    usable on the VERY NEXT turn of the current session too — not just
    after restarting cli.py. Mutates TOOL_SCHEMA/TOOL_FUNCTIONS in place
    (not reassigns) since ask_ai() and _execute_tool_call() both read
    these as module-level globals.
    """
    schema_list, functions, errors = load_custom_tools()
    existing_names = {entry["function"]["name"] for entry in TOOL_SCHEMA}
    for entry in schema_list:
        name = entry["function"]["name"]
        if name not in existing_names:
            TOOL_SCHEMA.append(entry)
    TOOL_FUNCTIONS.update(functions)
    return errors

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
changes, before declaring the task done — don't just assume a fix works.

If a task genuinely needs a capability none of your existing tools provide \
(not just a task that's hard — one where no combination of existing tools \
can do it), use create_tool to write and permanently save a new one. Prefer \
existing tools whenever they can do the job; only create a new tool when \
truly necessary, since every tool you create persists for all future tasks.

If you're stuck on a bug after a few real attempts — you can reproduce it \
but can't tell why it's happening — consider whether a diagnostic tool \
would help (e.g. one that traces variable values, generates edge-case test \
inputs, or diffs behavior before/after a change) rather than continuing to \
guess blindly. Build that tool, use it to actually see what's happening, \
then fix the real cause. Don't create a tool as a substitute for reasoning \
about a bug you already understand well enough to fix directly.

For any task with 3 or more distinct steps, call write_todos first to lay \
out the plan, then update it as steps complete — this gives the person a \
visible view of progress instead of a silent chain of actions. Skip it for \
simple one- or two-step tasks; it adds noise there, not clarity.

If an edit makes things worse — tests that were passing now fail, or you've \
introduced an error that wasn't there before, and a quick follow-up fix \
isn't working — use revert_file to get back to the last known-good state \
before trying a different approach, rather than layering more changes on \
top of a broken one. Only works on files already tracked by git; check the \
result and fall back to fixing forward if revert isn't available."""

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
        result = func(**args)
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}"
    except Exception as e:
        return f"ERROR: {name} raised an exception: {e}"

    if name == "create_tool" and isinstance(result, str) and result.startswith("Tool '"):
        # New tool saved successfully — make it usable on the very next turn
        # of THIS session too, not just after restarting cli.py.
        refresh_errors = _refresh_custom_tools()
        if refresh_errors:
            result += f"\n(Note: {'; '.join(refresh_errors)})"

    return result


def run_agent(task, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True):
    """
    Runs the core tool-calling loop for a SINGLE, standalone task until the
    model stops calling tools or MAX_TURNS is hit. Each call starts a fresh
    conversation — use run_conversation (below) instead if you want follow-up
    messages to build on what was just discussed in the same session.

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

    final = _run_loop(messages, on_step, auto_confirm, confirm_callback)
    if use_memory:
        task_memory.add_task_summary(task, final)
    return final


class Conversation:
    """
    Maintains real message history across multiple turns in the same
    session — so "now also handle empty input" builds on what was just
    discussed, instead of starting blind like separate run_agent() calls
    would. This is the actual gap between "one-shot task runner" and
    "ongoing session", closed here without changing run_agent()'s
    existing single-shot behavior (fan_out and scripted use still use
    run_agent directly).

    Memory (task_memory) is applied once at conversation start based on
    the first message, then the growing conversation itself carries
    context for later turns — same as how a real back-and-forth works.
    """

    def __init__(self, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True):
        self.on_step = on_step
        self.auto_confirm = auto_confirm
        self.confirm_callback = confirm_callback
        self.use_memory = use_memory
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._memory_applied = False
        self._first_task = None

    def send(self, task):
        """Send a new message in this ongoing conversation, get the reply."""
        if not self._memory_applied and self.use_memory:
            relevant = task_memory.retrieve_relevant(task)
            memory_context = task_memory.format_for_prompt(relevant)
            if memory_context:
                self.messages[0]["content"] += "\n\n" + memory_context
            self._memory_applied = True
            self._first_task = task

        self.messages.append({"role": "user", "content": task})
        final = _run_loop(self.messages, self.on_step, self.auto_confirm, self.confirm_callback)
        self.messages.append({"role": "assistant", "content": final})

        if self.use_memory:
            # Summarize the FIRST task of the conversation for cross-session
            # recall (summarizing every follow-up would fragment the memory
            # into confusing partial fragments).
            task_memory.add_task_summary(self._first_task or task, final)

        return final


def _run_loop(messages, on_step, auto_confirm, confirm_callback):
    """Shared tool-calling loop used by both run_agent() and Conversation.send()."""
    consecutive_empty = 0

    for turn in range(MAX_TURNS):
        message = ask_ai(messages, tools=TOOL_SCHEMA)

        if isinstance(message, dict) and "error" in message:
            return f"ERROR: {message['error']}"

        content = message.get("content") if isinstance(message, dict) else message.content
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else message.tool_calls

        if on_step:
            on_step({"turn": turn, "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            if not content or not content.strip():
                # Empty content + no tool calls is NOT a real completion — it's
                # usually a truncated/cut-off response from the provider. Treating
                # it as "done" would silently report success on a stalled task.
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    return "ERROR: model returned empty responses repeatedly — task did not complete. Try again or break the task into smaller steps."
                messages.append({
                    "role": "assistant",
                    "content": content or "",
                })
                messages.append({
                    "role": "user",
                    "content": "Your last response was empty. Please continue: either call a tool to keep working, or give a real final answer.",
                })
                continue
            return content

        consecutive_empty = 0

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
                "content": str(result)[:6000],
            })

    return "Reached max turns without finishing — task may be too large for one run."


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
