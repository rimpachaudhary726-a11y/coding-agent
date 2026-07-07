"""
agent.py — the core reasoning loop, plus fan-out for big tasks.

Features in this version:
  - AGENT.md project memory: auto-loaded from the project root (if present)
    and injected into the system prompt.
  - Plan mode: agent restricted to read-only tools until submit_plan is
    approved by the human.
  - Diff-before-apply: apply_pending_changes (from tools.py) is gated behind
    a human reviewing the full staged diff, the same way destructive bash
    commands are gated.
  - Sub-agent delegation: delegate_subagent spins up a fresh, isolated
    conversation for a self-contained chunk of work and returns just its
    final summary — sequential and synchronous, unlike fan_out's parallel
    multi-target execution.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from provider_pool import ask_ai
from tools import (
    TOOL_SCHEMA, TOOL_FUNCTIONS, _matches_any, _DESTRUCTIVE_PATTERNS,
    review_pending_changes,
)
from firebase_tools import FIREBASE_TOOL_SCHEMA, FIREBASE_TOOL_FUNCTIONS
from github_tools import GITHUB_TOOL_SCHEMA, GITHUB_TOOL_FUNCTIONS
from vision_tools import VISION_TOOL_SCHEMA, VISION_TOOL_FUNCTIONS
from meta_builder import build_agent_system
from custom_tool_registry import CREATE_TOOL_SCHEMA, CREATE_TOOL_FUNCTIONS, load_custom_tools
import task_memory

META_BUILDER_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "build_agent_system",
        "description": "Generate a complete N-agent simulation system (personas with personality/"
                        "routine/relationships/secrets, memory storage, tick loop, and a live web "
                        "viewer) for a given theme.",
        "parameters": {"type": "object", "properties": {
            "theme": {"type": "string"},
            "count": {"type": "integer", "default": 30},
            "output_dir": {"type": "string", "default": "."},
        }, "required": ["theme"]},
    }},
]
META_BUILDER_TOOL_FUNCTIONS = {"build_agent_system": build_agent_system}


# ---------------------------------------------------------------------------
# Plan mode — submit_plan tool. Actual gating happens in _run_loop.
# ---------------------------------------------------------------------------

def submit_plan(plan):
    """Placeholder body — _run_loop intercepts this call before it ever runs."""
    return plan


PLAN_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "submit_plan",
        "description": "Submit your implementation plan for human approval. In plan mode, you "
                        "MUST call this before using any file-writing, editing, or shell tool. "
                        "Describe the concrete steps you intend to take.",
        "parameters": {"type": "object", "properties": {
            "plan": {"type": "string", "description": "The step-by-step plan, in plain text."},
        }, "required": ["plan"]},
    }},
]
PLAN_TOOL_FUNCTIONS = {"submit_plan": submit_plan}

# Tools allowed before a plan has been approved (read-only / planning only)
READ_ONLY_TOOL_NAMES = {
    "read_file", "read_files", "list_directory", "search_codebase",
    "write_todos", "submit_plan", "list_open_issues",
    "browser_navigate", "browser_screenshot", "screen_crop", "visual_self_verify",
    "review_pending_changes",
}


# ---------------------------------------------------------------------------
# Sub-agent delegation
# ---------------------------------------------------------------------------

SUBAGENT_SYSTEM_ADDENDUM = """

You are a SUB-AGENT handling one isolated, self-contained piece of work \
delegated by a parent agent. You do not have access to delegate_subagent \
yourself, to avoid unbounded recursion. Focus only on the task given. \
When finished, reply with a concise plain-text summary of what you found \
or did — this summary is the ONLY thing the parent agent will see, so make \
it complete enough to act on."""

# Tools excluded from a sub-agent's own tool set (prevents infinite recursion)
SUBAGENT_EXCLUDED_TOOLS = {"delegate_subagent"}


def delegate_subagent(task, max_turns=15):
    """
    Run an isolated, fresh-context sub-conversation for a self-contained task
    (e.g. "investigate why X fails and summarize the root cause", "find all
    usages of Y across the codebase and summarize the pattern"). Returns only
    the sub-agent's final plain-text summary — its tool-call history does
    NOT get added to the parent's context, keeping the parent's context lean.

    Sub-agents cannot delegate further (no recursion) and cannot run
    destructive bash commands (no confirm_callback is wired through), so
    they are safe to run unattended.
    """
    sub_schema = [e for e in TOOL_SCHEMA if e["function"]["name"] not in SUBAGENT_EXCLUDED_TOOLS]
    sub_messages = [
        {"role": "system", "content": SYSTEM_PROMPT + SUBAGENT_SYSTEM_ADDENDUM},
        {"role": "user", "content": task},
    ]
    final, _ = _run_loop(
        sub_messages, on_step=None, auto_confirm=False, confirm_callback=None,
        plan_mode=False, plan_confirm_callback=None, diff_confirm_callback=None,
        tool_schema_override=sub_schema, max_turns=max_turns,
    )
    return final


DELEGATE_SUBAGENT_TOOL_SCHEMA = [
    {"type": "function", "function": {
        "name": "delegate_subagent",
        "description": "Delegate an isolated, self-contained chunk of work to a fresh sub-agent "
                        "with its own clean context (e.g. investigating a bug's root cause, "
                        "researching how something is used across the codebase, reviewing a "
                        "single file in depth). You only receive its final summary, not its "
                        "full tool-call history — use this to keep your own context lean on "
                        "large tasks. The sub-agent cannot run destructive commands or delegate "
                        "further.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "The self-contained task to delegate."},
            "max_turns": {"type": "integer", "default": 15},
        }, "required": ["task"]},
    }},
]
DELEGATE_SUBAGENT_TOOL_FUNCTIONS = {"delegate_subagent": delegate_subagent}


_custom_schema, _custom_functions, _custom_load_errors = load_custom_tools()
for _err in _custom_load_errors:
    print(f"[custom tools] {_err}")

TOOL_SCHEMA = (
    TOOL_SCHEMA + FIREBASE_TOOL_SCHEMA + GITHUB_TOOL_SCHEMA + VISION_TOOL_SCHEMA
    + META_BUILDER_TOOL_SCHEMA + CREATE_TOOL_SCHEMA + PLAN_TOOL_SCHEMA
    + DELEGATE_SUBAGENT_TOOL_SCHEMA + _custom_schema
)
TOOL_FUNCTIONS = {
    **TOOL_FUNCTIONS, **FIREBASE_TOOL_FUNCTIONS, **GITHUB_TOOL_FUNCTIONS, **VISION_TOOL_FUNCTIONS,
    **META_BUILDER_TOOL_FUNCTIONS, **CREATE_TOOL_FUNCTIONS, **PLAN_TOOL_FUNCTIONS,
    **DELEGATE_SUBAGENT_TOOL_FUNCTIONS, **_custom_functions,
}


def _refresh_custom_tools():
    """Hot-reload: makes a newly created tool available in the current session."""
    schema_list, functions, errors = load_custom_tools()
    existing_names = {entry["function"]["name"] for entry in TOOL_SCHEMA}
    for entry in schema_list:
        name = entry["function"]["name"]
        if name not in existing_names:
            TOOL_SCHEMA.append(entry)
    TOOL_FUNCTIONS.update(functions)
    return errors


# ---------------------------------------------------------------------------
# AGENT.md — project-level memory, auto-loaded once per run/conversation.
# ---------------------------------------------------------------------------

AGENT_MD_FILENAMES = ["AGENT.md", "CLAUDE.md", ".agent/AGENT.md"]
MAX_AGENT_MD_CHARS = 8000


def _load_project_context(root="."):
    """
    Look for a project memory file (AGENT.md, falling back to CLAUDE.md)
    in the project root and return its content, truncated to a sane size.
    Returns "" if none exists.
    """
    for filename in AGENT_MD_FILENAMES:
        path = os.path.join(root, filename)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except OSError:
                continue
            if not content:
                continue
            if len(content) > MAX_AGENT_MD_CHARS:
                content = content[:MAX_AGENT_MD_CHARS] + "\n...(truncated)"
            return f"Project context from {filename}:\n{content}"
    return ""


SYSTEM_PROMPT = """You are a coding agent with direct access to the filesystem \
and shell via tools. You can read, write, and edit files, search the codebase, \
run commands, and commit to git. Work step by step: investigate before you \
change anything, make the smallest correct change, and verify your work \
(run tests or the relevant command) before declaring the task done. \
When a task is genuinely finished, reply with plain text and no further tool calls.

When looking for a file, check the project root first (list_directory(".")) \
before searching subfolders. Ignore artifacts/, node_modules/, lib/, .cache/, \
and other tooling/dependency folders unless the user's request specifically \
points there.

If the project has a test suite, call detect_and_run_tests after making code \
changes, before declaring the task done.

For a quick, single-file fix, write_file/edit_file (immediate, no review) are \
fine. For anything touching multiple files, or any change you want reviewed \
as a whole before it hits disk, use stage_write_file / stage_edit_file to \
queue the changes, call review_pending_changes to produce the full diff, then \
apply_pending_changes to commit everything at once (this requires human \
approval and will not silently write anything).

If a task genuinely needs a capability none of your existing tools provide, \
use create_tool to write and permanently save a new one. Prefer existing tools \
whenever they can do the job.

If you're stuck on a bug after a few real attempts, consider whether a diagnostic \
tool would help rather than continuing to guess blindly.

For any task with 3 or more distinct steps, call write_todos first to lay \
out the plan, then update it as steps complete.

If an edit makes things worse, use revert_file to get back to the last \
known-good state before trying a different approach.

For a self-contained chunk of investigation or work that doesn't need to \
pollute your own context with its full tool-call history (e.g. researching \
how something is used across the codebase, or diagnosing one specific bug), \
consider delegate_subagent and just use its returned summary.

For frontend/UI work, use the browser tools (browser_navigate, browser_click, \
browser_type, browser_screenshot) to actually see and interact with what you \
build, the way a human tester would. If a screenshot is too blurry, cluttered, \
or small to read a specific button or error message, use screen_crop to zoom \
into that region before deciding what to do next. After building or editing a \
frontend, take a screenshot and use visual_self_verify against the original \
design mockup (if one was provided) to confirm colors, spacing, and layout \
actually match before declaring the task done."""

PLAN_MODE_ADDENDUM = """

PLAN MODE IS ACTIVE. Before making any file edit, file write, or running any \
shell command, you must first investigate using read-only tools \
(read_file, list_directory, search_codebase, browser tools), then call \
submit_plan with a concrete step-by-step plan. Do not call any writing/editing/ \
shell tool until submit_plan has been approved. If the plan is rejected, revise \
it based on the feedback and call submit_plan again."""

MAX_TURNS = 40


def _execute_tool_call(tool_call, confirm_destructive=False, confirm_callback=None,
                        diff_confirm_callback=None):
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
            allowed = confirm_callback(command)
            if allowed:
                args["confirmed"] = True
            else:
                return f"Command declined by user: '{command}'. Not run."

    if name == "apply_pending_changes" and not args.get("confirmed"):
        if confirm_destructive:
            args["confirmed"] = True
        elif diff_confirm_callback:
            diff_text = review_pending_changes()
            approved, feedback = diff_confirm_callback(diff_text)
            if approved:
                args["confirmed"] = True
            else:
                return (
                    "Changes NOT applied — user rejected the diff."
                    + (f" Feedback: {feedback}" if feedback else "")
                    + " Pending changes remain staged; revise with stage_write_file/"
                      "stage_edit_file and try again, or discard_pending_changes."
                )
        # else: no callback configured — falls through, tool itself returns
        # CONFIRMATION_REQUIRED since args["confirmed"] stays falsy.

    try:
        result = func(**args)
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}"
    except Exception as e:
        return f"ERROR: {name} raised an exception: {e}"

    if name == "create_tool" and isinstance(result, str) and result.startswith("Tool '"):
        refresh_errors = _refresh_custom_tools()
        if refresh_errors:
            result += f"\n(Note: {'; '.join(refresh_errors)})"

    return result


def run_agent(task, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True,
              plan_mode=False, plan_confirm_callback=None, diff_confirm_callback=None):
    """
    Single-shot task runner. Each call starts a fresh conversation.
    use_memory=False for fan-out leaves to avoid parallel writes to .agent_memory.json.

    plan_mode=True restricts the agent to read-only tools until it calls
    submit_plan and plan_confirm_callback(plan_text) returns (approved, feedback).

    diff_confirm_callback(diff_text) -> (approved, feedback) gates
    apply_pending_changes the same way plan_confirm_callback gates submit_plan.
    """
    memory_context = ""
    if use_memory:
        relevant = task_memory.retrieve_relevant(task)
        memory_context = task_memory.format_for_prompt(relevant)

    project_context = _load_project_context()

    system_content = SYSTEM_PROMPT
    if plan_mode:
        system_content += PLAN_MODE_ADDENDUM
    if project_context:
        system_content += "\n\n" + project_context
    if memory_context:
        system_content += "\n\n" + memory_context

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": task},
    ]

    final, _ = _run_loop(messages, on_step, auto_confirm, confirm_callback,
                          plan_mode=plan_mode, plan_confirm_callback=plan_confirm_callback,
                          diff_confirm_callback=diff_confirm_callback)
    if use_memory:
        task_memory.add_task_summary(task, final)
    return final


class Conversation:
    """
    Stateful multi-turn session. Message history grows across turns so
    follow-up messages build on what was just discussed.
    Memory and project context applied once at conversation start (based on
    the first message), then the growing history itself carries context.
    """

    def __init__(self, on_step=None, auto_confirm=False, confirm_callback=None, use_memory=True,
                 plan_mode=False, plan_confirm_callback=None, diff_confirm_callback=None):
        self.on_step = on_step
        self.auto_confirm = auto_confirm
        self.confirm_callback = confirm_callback
        self.use_memory = use_memory
        self.plan_mode = plan_mode
        self.plan_confirm_callback = plan_confirm_callback
        self.diff_confirm_callback = diff_confirm_callback

        system_content = SYSTEM_PROMPT
        if plan_mode:
            system_content += PLAN_MODE_ADDENDUM
        project_context = _load_project_context()
        if project_context:
            system_content += "\n\n" + project_context

        self.messages = [{"role": "system", "content": system_content}]
        self._memory_applied = False
        self._first_task = None
        # Tracks whether the plan for THIS conversation has been approved yet.
        self._plan_approved = not plan_mode

    def send(self, task):
        if not self._memory_applied and self.use_memory:
            relevant = task_memory.retrieve_relevant(task)
            memory_context = task_memory.format_for_prompt(relevant)
            if memory_context:
                self.messages[0]["content"] += "\n\n" + memory_context
            self._memory_applied = True
            self._first_task = task

        self.messages.append({"role": "user", "content": task})
        final, self._plan_approved = _run_loop(
            self.messages, self.on_step, self.auto_confirm, self.confirm_callback,
            plan_mode=self.plan_mode, plan_confirm_callback=self.plan_confirm_callback,
            diff_confirm_callback=self.diff_confirm_callback,
            plan_already_approved=self._plan_approved,
        )
        self.messages.append({"role": "assistant", "content": final})

        if self.use_memory:
            task_memory.add_task_summary(self._first_task or task, final)

        return final


def _filtered_schema_for_plan_state(base_schema, plan_approved):
    """When a plan hasn't been approved yet, only expose read-only tools."""
    if plan_approved:
        return base_schema
    return [entry for entry in base_schema if entry["function"]["name"] in READ_ONLY_TOOL_NAMES]


def _run_loop(messages, on_step, auto_confirm, confirm_callback,
              plan_mode=False, plan_confirm_callback=None, diff_confirm_callback=None,
              plan_already_approved=False, tool_schema_override=None, max_turns=None):
    """
    Shared tool-calling loop used by run_agent(), Conversation.send(), and
    delegate_subagent(). Always returns (final_text, plan_approved_state) so
    callers can track plan approval across turns of a Conversation.

    tool_schema_override lets delegate_subagent run with a reduced tool set
    (excluding delegate_subagent itself, to prevent infinite recursion).
    """
    base_schema = tool_schema_override if tool_schema_override is not None else TOOL_SCHEMA
    turn_limit = max_turns if max_turns is not None else MAX_TURNS
    consecutive_empty = 0
    plan_approved = plan_already_approved or not plan_mode

    for turn in range(turn_limit):
        active_schema = _filtered_schema_for_plan_state(base_schema, plan_approved)
        message = ask_ai(messages, tools=active_schema)

        if isinstance(message, dict) and "error" in message:
            return f"ERROR: {message['error']}", plan_approved

        content = message.get("content") if isinstance(message, dict) else message.content
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else message.tool_calls

        if on_step:
            on_step({"turn": turn, "content": content, "tool_calls": tool_calls})

        if not tool_calls:
            if not content or not content.strip():
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    return "ERROR: model returned empty responses repeatedly — task did not complete.", plan_approved
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({"role": "user", "content": "Your last response was empty. Please continue."})
                continue
            return content, plan_approved

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
            fn_name = tc_dict["function"]["name"]

            if fn_name == "submit_plan" and plan_mode and not plan_approved:
                try:
                    plan_args = json.loads(tc_dict["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    plan_args = {}
                plan_text = plan_args.get("plan", "")

                if plan_confirm_callback:
                    approved, feedback = plan_confirm_callback(plan_text)
                else:
                    approved, feedback = True, ""  # no gate configured — auto-approve

                if approved:
                    plan_approved = True
                    result = "Plan approved by the user. You may now proceed using any tool."
                else:
                    result = (
                        "Plan REJECTED by the user."
                        + (f" Feedback: {feedback}" if feedback else "")
                        + " Revise your plan and call submit_plan again. Do not use any "
                          "writing/editing/shell tool yet."
                    )
                messages.append({
                    "role": "tool", "tool_call_id": tc_dict["id"], "content": result,
                })
                continue

            # Defense in depth: if plan mode is active and unapproved, and the
            # model somehow still emits a non-read-only tool call, block it.
            if plan_mode and not plan_approved and fn_name not in READ_ONLY_TOOL_NAMES:
                messages.append({
                    "role": "tool", "tool_call_id": tc_dict["id"],
                    "content": f"BLOCKED: '{fn_name}' is not allowed until your plan is "
                               f"submitted via submit_plan and approved by the user.",
                })
                continue

            result = _execute_tool_call(
                tc_dict, confirm_destructive=auto_confirm, confirm_callback=confirm_callback,
                diff_confirm_callback=diff_confirm_callback,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc_dict["id"],
                "content": str(result)[:6000],
            })

    return "Reached max turns without finishing — task may be too large for one run.", plan_approved


def fan_out(task_template, targets, max_workers=8, auto_confirm=True):
    """
    Run run_agent() in parallel across many targets.

    Example:
        fan_out("Review {target} for bugs.", ["auth.py", "payments.py", "db.py"])

    Returns {target: result}. use_memory=False on leaves to avoid
    many parallel workers writing to .agent_memory.json simultaneously.

    Note: unlike delegate_subagent (sequential, called BY the agent mid-task),
    fan_out is called externally, before/after the agent runs, to parallelize
    across independent targets.
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
