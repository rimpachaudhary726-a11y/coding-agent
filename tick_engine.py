"""
tick_engine.py — the heart of the simulation. Each tick = one in-world hour.

UPDATED: resume support.
  - On startup, reads the last logged entry in event_log.json and resumes
    from the next hour instead of always restarting at Day 0, Hour 0.
  - If event_log.json doesn't exist yet (fresh start), behaves exactly as before.
"""

import json
import os
import time
import requests
from memory import AgentMemoryStore

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3-coder")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

TICK_HOURS = list(range(24))
EVENT_LOG_PATH = "event_log.json"
AGENTS_PATH = "agents.json"


def load_agents():
    with open(AGENTS_PATH, "r") as f:
        return json.load(f)["agents"]


def routine_for_hour(agent, hour):
    """Find which routine block covers the current hour (handles midnight-wrap ranges)."""
    routine = agent.get("routine", {})
    for time_range, activity in routine.items():
        if time_range == "variable":
            continue
        if "-" in time_range:
            start, end = time_range.split("-")
            start, end = int(start), int(end)
            if start < end:
                if start <= hour < end:
                    return activity
            else:
                if hour >= start or hour < end:
                    return activity
    return routine.get("variable", "No specific plan this hour.")


def build_prompt(agent, hour, day, memories):
    memory_text = "\n".join(f"- {m.text}" for m in memories) if memories else "No notable memories yet."
    relationships = "\n".join(f"- {name}: {desc}" for name, desc in agent.get("relationships", {}).items())

    return f"""You are {agent['name']}, {agent['role']} in a small Japanese town.

Personality: {agent['personality']}
Backstory: {agent.get('backstory', '')}
Private goal/secret: {agent.get('secret', 'None')}

Current time: Day {day}, Hour {hour}:00
Your usual routine at this hour: {routine_for_hour(agent, hour)}

Relationships:
{relationships or 'No notable relationships defined.'}

Relevant memories:
{memory_text}

What do you do or say right now? Respond in 1-3 sentences, third person,
as a short story beat. Stay strictly in character."""


def call_llm(prompt: str) -> str:
    if not LLM_API_KEY:
        return f"[NO API KEY SET] Would have prompted: {prompt[:60]}..."
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 200}
    try:
        resp = requests.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as e:
        return f"[API ERROR — skipped this turn] {e}"
    except (KeyError, IndexError, ValueError) as e:
        return f"[MALFORMED RESPONSE — skipped this turn] {e}"


def log_event(day, hour, agent_name, action_text):
    entry = {"day": day, "hour": hour, "agent": agent_name, "text": action_text, "logged_at": time.time()}
    log = []
    if os.path.exists(EVENT_LOG_PATH):
        with open(EVENT_LOG_PATH, "r") as f:
            log = json.load(f)
    log.append(entry)
    with open(EVENT_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)
    print(f"[Day {day} {hour:02d}:00] {agent_name}: {action_text}")


def run_tick(day, hour, agents, stores):
    global_tick = day * 24 + hour
    for agent in agents:
        store = stores[agent["id"]]
        memories = store.retrieve_relevant(current_tick=global_tick, top_k=5)
        prompt = build_prompt(agent, hour, day, memories)
        action_text = call_llm(prompt)
        log_event(day, hour, agent["name"], action_text)
        store.add(text=action_text, tick=global_tick, importance=5)


def _resume_point():
    """
    Read the last entry in event_log.json and return the (day, hour) to resume from
    — i.e. the hour AFTER the last one logged. Returns (0, 0) on a fresh start.
    """
    if not os.path.exists(EVENT_LOG_PATH):
        return 0, 0
    try:
        with open(EVENT_LOG_PATH, "r") as f:
            log = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0, 0
    if not log:
        return 0, 0

    last = log[-1]
    last_day = last.get("day", 0)
    last_hour = last.get("hour", 0)

    next_hour = last_hour + 1
    if next_hour >= 24:
        return last_day + 1, 0
    return last_day, next_hour


def main():
    agents = load_agents()
    stores = {a["id"]: AgentMemoryStore(a["id"]) for a in agents}

    start_day, start_hour = _resume_point()
    if (start_day, start_hour) != (0, 0):
        print(f"Resuming simulation from Day {start_day}, Hour {start_hour:02d}:00 "
              f"(found existing {EVENT_LOG_PATH}).")
    else:
        print("Starting fresh simulation at Day 0, Hour 00:00.")

    day = start_day
    first_day = True
    while True:
        hours = TICK_HOURS
        if first_day and start_hour != 0:
            hours = [h for h in TICK_HOURS if h >= start_hour]
        first_day = False

        for hour in hours:
            run_tick(day, hour, agents, stores)
            time.sleep(1)
        day += 1


if __name__ == "__main__":
    main()
