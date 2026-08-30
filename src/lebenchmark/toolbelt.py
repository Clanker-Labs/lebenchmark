"""The tool belt under test.

These are not invented tools. They are the schemas LeClanker actually hands to
a model when it runs as the household agent, transcribed from
``src/leclanker/tools/*.py`` in Clanker-Labs/LeClanker. That matters: tool-call
reliability depends on the belt, and a benchmark built on three toy tools would
measure a situation this ecosystem never runs in.

Nothing here executes. A tool call is graded against the schema and discarded —
the benchmark never restarts an app or writes a memory.
"""

from __future__ import annotations

from typing import Any

# Apps the dashboard's registry knows about. Used both for the `app` enum and to
# detect names a model made up.
KNOWN_APPS = (
    "moude",
    "clankergram",
    "ai212",
    "leclanker",
    "jinsen",
    "jinsen-health",
    "jinsen-lists",
    "selflix",
    "selfmail",
    "selfkey",
    "guessflix",
    "safepill",
    "larecherche",
    "theworld",
    "theroundtable",
    "tradesights",
    "selftrade",
    "localflow",
    "cortex",
)

APP_ACTIONS = ("start", "stop", "restart", "logs")


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS: tuple[dict[str, Any], ...] = (
    _fn(
        "ecosystem_status",
        "Status of every app on this machine (from the chezmoi dashboard): which "
        "are running, per-service health, and known gotchas.",
        {},
        [],
    ),
    _fn(
        "ecosystem_app",
        "Manage one app on this machine. action is one of: start | stop | restart | "
        "logs. Runs the app's own registered commands (never arbitrary shell). "
        "Confirm with Erwin before stopping something he may be using.",
        {
            "app": {"type": "string", "description": "App name, e.g. moude."},
            "action": {"type": "string", "enum": list(APP_ACTIONS)},
        },
        ["app", "action"],
    ),
    _fn(
        "chat_search",
        "Search every OTHER conversation — all Telegram chats, Community topics — "
        "for something that was said. Use when asked what was decided or discussed "
        "somewhere else.",
        {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "Max results, default 12."},
        },
        ["query"],
    ),
    _fn(
        "chat_list",
        "List every conversation you have — Telegram chats, Community topics — with "
        "their thread ids.",
        {},
        [],
    ),
    _fn(
        "chat_read",
        "Read the recent history of ANOTHER conversation, by its thread id.",
        {
            "thread_id": {"type": "string"},
            "limit": {"type": "integer", "description": "Max messages, default 25."},
        },
        ["thread_id"],
    ),
    _fn(
        "memory_search",
        "Search long-term memory (emails, Obsidian notes, saved facts, past "
        "conversations). Optional source filter: gmail | obsidian | chat | manual.",
        {
            "query": {"type": "string"},
            "source": {"type": "string", "enum": ["gmail", "obsidian", "chat", "manual"]},
        },
        ["query"],
    ),
    _fn(
        "memory_save",
        "Save ONE durable fact to long-term memory so it survives this conversation.",
        {
            "fact": {"type": "string"},
            "title": {"type": "string"},
            "tags": {"type": "string", "description": "Comma-separated."},
        },
        ["fact"],
    ),
    _fn(
        "memory_forget",
        "Delete a memory item by id (from memory_search results).",
        {"memory_id": {"type": "integer"}},
        ["memory_id"],
    ),
    _fn(
        "home_states",
        "Current state of Home Assistant entities. Optional domain filter, e.g. "
        "light, switch, sensor, climate.",
        {"domain": {"type": "string"}},
        [],
    ),
    _fn(
        "home_control",
        "Call a Home Assistant service on one entity — turn something on or off, "
        "set a temperature.",
        {
            "domain": {"type": "string", "description": "e.g. light, switch, climate."},
            "service": {"type": "string", "description": "e.g. turn_on, turn_off."},
            "entity_id": {"type": "string", "description": "e.g. light.living_room."},
            "data_json": {"type": "string", "description": "Extra service data as JSON."},
        },
        ["domain", "service", "entity_id"],
    ),
    _fn(
        "home_history",
        "Recent state history for one Home Assistant entity.",
        {"entity_id": {"type": "string"}},
        ["entity_id"],
    ),
    _fn(
        "network_status",
        "Live tailnet (Tailscale) traffic: total throughput and per-node transfer.",
        {},
        [],
    ),
    _fn(
        "search_brain",
        "Search the household brain: shared notes, personal vault, everything "
        "captured there.",
        {"query": {"type": "string"}},
        ["query"],
    ),
    _fn(
        "capture_note",
        "Write a line into today's daily note in the household brain.",
        {"text": {"type": "string"}},
        ["text"],
    ),
    _fn(
        "remember_fact",
        "Save a durable fact to the household brain — a preference, a decision, a "
        "standing arrangement.",
        {
            "body": {"type": "string"},
            "kind": {"type": "string", "enum": ["fact", "preference", "decision"]},
            "subject": {"type": "string"},
        },
        ["body"],
    ),
    _fn(
        "brain_today",
        "What is on today, from the household brain: events, open tasks, what "
        "changed. Computed without a model, so it is fast and it is not a guess.",
        {},
        [],
    ),
)

TOOLS_BY_NAME = {t["function"]["name"]: t for t in TOOLS}

SYSTEM_PROMPT = (
    "You are LeClanker, the household agent on Erwin's home server. You manage "
    "the machine's apps, the household brain and the home automation. Use the "
    "tools you have been given when the request needs live state or an action. "
    "Answer directly when it does not."
)
