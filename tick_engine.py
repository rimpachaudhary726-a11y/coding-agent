"""
The tick engine — the heart of the simulation.

Each tick represents one in-world hour. For every agent, we:
  1. Build a prompt from their personality, routine, relationships, and
     the most relevant memories
  2. Ask the LLM what they do/say next
  3. Log that action to the shared event feed (what the live viewer reads)
  4. Write it back into that agent's memory (and any other agent involved)

Run with: python tick_engine.py

Configure your API key via environment variable before running:
  export LLM_API_KEY="your-key-here"

Works with any OpenAI-compatible endpoint (OpenRouter, Groq, Cerebras, etc.)
by changing LLM_BASE_URL and LLM_MODEL below.
"""

import json
import os
import time
import requests

from memory import AgentMemoryStore

# --- Configuration -----------------------------------------------------
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3-coder")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

TICK_HOURS = list(range(24))  # one full day cycle, hour by hour
EVENT_LOG_PATH = "event_log.json"
AGENTS_PATH = "agents.json"


def load_agents():
    with open(AGENTS_PATH, "r") as f:
        data = json.load(f)
    return data["agents"]


def routine_for_hour(agent, hour):
    """Find which routine block covers the current hour."""
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
            else:  # wraps past midnight, e.g. "21-6"
                if hour >= start or hour < end:
                    return activity
    return routine.get("variable", "No specific plan this hour.")


def build_prompt(agent, hour, day, memories):
    memory_text = "\n".join(f"- {m.text}" for m in memories) if memories else "No notable memories yet."
    relationships = "\n".join(f"- {name}: {desc}" for name, desc in agent.get("relationships", {}).items())

    return f"""You are {agent['name']}, {agent['role']} in a small Japanese town.

Personality: {agent['personality']}
Backstory: {agent.get('backstory', '')}
Private goal/secret (this quietly influences you, never state it outright unless it truly fits the moment): {agent.get('secret', 'None')}

Current time: Day {day}, Hour {hour}:00
Your usual routine at this hour: {routine_for_hour(agent, hour)}

Relationships:
{relationships or 'No notable relationships defined.'}

Relevant memories:
{memory_text}

What do you do or say right now? Respond in 1-3 sentences, third person,
as a short story beat. Include dialogue in quotes if you speak to someone.
Stay strictly in character. Do not narrate for other agents, only {agent['name']}.
"""


def call_llm(prompt: str) -> str:
    if not LLM_API_KEY:
        return f"[NO API KEY SET] Would have prompted: {prompt[:60]}..."

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
    }
    try:
        resp = requests.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except requests.exceptions.RequestException as e:
        # A single failed/rate-limited call used to raise and kill the whole
        # tick loop (every remaining agent that hour, and every hour after).
        # Now it just skips this agent's turn for this tick and the sim
        # keeps running — same resilience pattern as provider_pool.py's ask_ai.
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


def main():
    agents = load_agents()
    stores = {a["id"]: AgentMemoryStore(a["id"]) for a in agents}

    day = 0
    while True:
        for hour in TICK_HOURS:
            run_tick(day, hour, agents, stores)
            time.sleep(1)  # small pause between ticks; tune as needed
        day += 1


if __name__ == "__main__":
    main()
