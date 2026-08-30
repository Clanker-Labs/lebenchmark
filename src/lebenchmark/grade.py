"""Deciding what a response actually was.

The whole benchmark turns on one distinction: the model *emitted a tool call*
versus the model *talked about calling a tool*. The chezmoi docs record this as
"the Spark returns a tool call as prose in roughly 8% of calls", with the
observed shape being ``<tools>{"name": ...}</tools>`` arriving as ordinary
content with ``finish_reason: stop``. A caller that only checks
``message.tool_calls`` sees an empty list and a chatty answer, and concludes the
model refused — when it in fact chose the right tool and serialised it into the
wrong field.

Grading therefore classifies every response into one of five outcomes, and
counts prose-with-tool-syntax separately from a genuine refusal. Collapsing
those two would hide the failure this repo exists to measure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .toolbelt import KNOWN_APPS, TOOLS_BY_NAME


class Outcome(StrEnum):
    #: Structured `tool_calls` on the message. The only shape a caller can use.
    TOOL_CALL = "tool_call"
    #: No `tool_calls`, but the content carries a serialised call. The failure
    #: mode this repo was built to quantify.
    PROSE_TOOL_SYNTAX = "prose_tool_syntax"
    #: Ordinary prose with no call in it. Correct for an abstention task, a
    #: refusal for a tool task.
    PROSE_PLAIN = "prose_plain"
    #: Neither content nor tool calls. Usually a thinking model that spent its
    #: budget reasoning — `finish_reason: length` with empty content.
    EMPTY = "empty"
    #: Prose that asks the user to confirm before acting, on a task whose tool
    #: description tells the agent to do exactly that. Obedience, not failure.
    CONFIRMATION = "confirmation"
    #: Transport or HTTP failure. Not the model's fault; kept out of rates.
    ERROR = "error"


# Ordered most- to least-specific. Each must imply a *serialised call*, not a
# mention: "I'll use ecosystem_app" is a plain-prose refusal, not this failure.
_TOOL_SYNTAX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tools_tag", re.compile(r"<tools>\s*[\[{].*?[\]}]\s*</tools>", re.DOTALL)),
    ("tool_call_tag", re.compile(r"<tool_call>\s*[\[{].*?[\]}]\s*</tool_call>", re.DOTALL)),
    ("special_token", re.compile(r"<\|tool_call\|>|\[TOOL_CALL\]|\[/?TOOL_CALLS?\]")),
    ("function_tag", re.compile(r"<function\s*=\s*[\w.]+\s*>", re.IGNORECASE)),
    # A JSON object pairing a name with an argument bag. Requires both keys, so
    # prose quoting a schema fragment does not trip it.
    (
        "json_name_args",
        re.compile(
            r"\{[^{}]*\"name\"\s*:\s*\"[\w.]+\"[^{}]*"
            r"\"(?:arguments|parameters|args)\"\s*:\s*[\{\"]",
            re.DOTALL,
        ),
    ),
)


def detect_tool_syntax(content: str) -> str | None:
    """Return the name of the prose-serialisation shape found, or None."""
    if not content:
        return None
    for label, pattern in _TOOL_SYNTAX_PATTERNS:
        if pattern.search(content):
            return label
    # Last resort: a fenced JSON block that names a tool we actually published.
    for block in re.findall(r"```(?:json|tool_code)?\s*(.+?)```", content, re.DOTALL):
        try:
            parsed = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for item in candidates:
            if isinstance(item, dict) and item.get("name") in TOOLS_BY_NAME:
                return "fenced_json"
    return None


# A confirmation is a question, and it names the act. Both halves are required:
# "I can restart moude." is not a confirmation, and "Are you sure?" attached to
# nothing is not one either. Only consulted on tasks flagged `destructive`, so a
# model that asks a rhetorical question elsewhere is unaffected.
_CONFIRM_CUE = re.compile(
    r"\b(confirm|are you sure|would you like me to|shall i|do you want me to|"
    r"okay to|ok to|is it safe|before i (?:proceed|do|restart|stop)|"
    r"let me know (?:if|when)|proceed\?)",
    re.IGNORECASE,
)


def seeks_confirmation(content: str) -> bool:
    return bool(content) and "?" in content and bool(_CONFIRM_CUE.search(content))


def classify(
    ok: bool,
    content: str,
    tool_calls: list[dict[str, Any]],
) -> tuple[Outcome, str | None]:
    if not ok:
        return Outcome.ERROR, None
    if tool_calls:
        return Outcome.TOOL_CALL, None
    if shape := detect_tool_syntax(content):
        return Outcome.PROSE_TOOL_SYNTAX, shape
    if not content.strip():
        return Outcome.EMPTY, None
    return Outcome.PROSE_PLAIN, None


@dataclass(slots=True)
class Grade:
    outcome: Outcome
    #: Which prose serialisation shape was seen, when outcome is PROSE_TOOL_SYNTAX.
    syntax_shape: str | None = None
    #: The tool the model picked, if it emitted a structured call.
    called_tool: str | None = None
    #: Arguments parsed from the structured call. None if they would not parse.
    called_args: dict[str, Any] | None = None
    #: Task expected a tool and the model emitted a structured one.
    emitted_call: bool = False
    #: Structured call named the expected tool.
    right_tool: bool = False
    #: Arguments satisfy the published schema (required, unknown keys, enums).
    args_schema_ok: bool = False
    #: Arguments satisfy the task's own expectations.
    args_match: bool = False
    #: An `app` argument naming something not in the dashboard registry.
    hallucinated_app: str | None = None
    #: Everything the task asked for.
    correct: bool = False
    notes: list[str] = field(default_factory=list)


def _parse_args(call: dict[str, Any]) -> dict[str, Any] | None:
    raw = (call.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _check_schema(tool_name: str, args: dict[str, Any]) -> list[str]:
    """Return a list of schema violations. Empty means valid."""
    spec = TOOLS_BY_NAME.get(tool_name)
    if spec is None:
        return [f"unknown tool {tool_name!r}"]
    params = spec["function"]["parameters"]
    properties: dict[str, Any] = params.get("properties", {})
    problems = []
    for key in params.get("required", []):
        if key not in args or args[key] in (None, ""):
            problems.append(f"missing required {key!r}")
    for key, value in args.items():
        if key not in properties:
            problems.append(f"unknown argument {key!r}")
            continue
        prop = properties[key]
        if (allowed := prop.get("enum")) and value not in allowed:
            problems.append(f"{key}={value!r} not in {allowed}")
        expected = prop.get("type")
        if expected == "integer" and not isinstance(value, int):
            problems.append(f"{key} should be integer, got {type(value).__name__}")
        if expected == "string" and not isinstance(value, str):
            problems.append(f"{key} should be string, got {type(value).__name__}")
    return problems


def _match_expectations(args: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Check a task's own argument expectations.

    Each entry is ``arg: {equals: x}`` for an exact match or
    ``arg: {contains_any: [...]}`` for a substring test, case-insensitive. Free
    text is never compared exactly — asking a model to phrase a saved fact in
    one specific way would measure wording, not tool use.
    """
    problems = []
    for key, rule in expected.items():
        if key not in args:
            problems.append(f"expected argument {key!r} absent")
            continue
        value = args[key]
        if "equals" in rule and str(value).strip().lower() != str(rule["equals"]).strip().lower():
            problems.append(f"{key}={value!r} != {rule['equals']!r}")
        if "contains_any" in rule:
            haystack = str(value).lower()
            if not any(str(n).lower() in haystack for n in rule["contains_any"]):
                problems.append(f"{key}={value!r} contains none of {rule['contains_any']}")
        if "one_of" in rule and str(value).strip().lower() not in {
            str(o).lower() for o in rule["one_of"]
        }:
            problems.append(f"{key}={value!r} not one of {rule['one_of']}")
    return problems


def grade(
    task: Any,
    ok: bool,
    content: str,
    tool_calls: list[dict[str, Any]],
) -> Grade:
    outcome, shape = classify(ok, content, tool_calls)
    g = Grade(outcome=outcome, syntax_shape=shape)

    if (
        task.kind == "tool"
        and task.destructive
        and outcome is Outcome.PROSE_PLAIN
        and seeks_confirmation(content)
    ):
        # `ecosystem_app` is documented as "Confirm with Erwin before stopping
        # something he may be using." A model that asks first is following the
        # tool description it was given. Scoring that as a refusal measures
        # compliance and calls it incapability — which is exactly what the first
        # run of this benchmark did, and why this branch exists.
        g.outcome = Outcome.CONFIRMATION
        g.correct = True
        g.notes.append("asked for confirmation before acting, as the tool description instructs")
        return g

    if task.kind == "abstain":
        # Correct means answering without reaching for a tool. Prose that
        # serialises a call still counts as reaching for one.
        g.correct = outcome is Outcome.PROSE_PLAIN
        if outcome is Outcome.TOOL_CALL:
            g.called_tool = (tool_calls[0].get("function") or {}).get("name")
            g.notes.append(f"called {g.called_tool} when no tool was needed")
        elif outcome is Outcome.PROSE_TOOL_SYNTAX:
            g.notes.append("serialised a call in prose when no tool was needed")
        return g

    if outcome is not Outcome.TOOL_CALL:
        g.notes.append(f"no structured call ({outcome})")
        return g

    g.emitted_call = True
    call = tool_calls[0]
    if len(tool_calls) > 1:
        g.notes.append(f"{len(tool_calls)} calls emitted; graded the first")
    g.called_tool = (call.get("function") or {}).get("name")
    g.right_tool = g.called_tool in task.acceptable_tools
    if not g.right_tool:
        g.notes.append(
            f"called {g.called_tool!r}, expected one of {list(task.acceptable_tools)}"
        )
    elif g.called_tool != task.expect_tool:
        g.notes.append(f"called {g.called_tool!r}, an accepted alternative to {task.expect_tool!r}")

    args = _parse_args(call)
    if args is None:
        g.notes.append("arguments were not valid JSON")
        return g
    g.called_args = args

    if isinstance(args.get("app"), str) and args["app"].lower() not in KNOWN_APPS:
        g.hallucinated_app = args["app"]
        g.notes.append(f"app {args['app']!r} is not in the dashboard registry")

    schema_problems = _check_schema(g.called_tool or "", args)
    g.args_schema_ok = not schema_problems
    g.notes.extend(schema_problems)

    expectations = task.expect_args if g.called_tool == task.expect_tool else {}
    expect_problems = _match_expectations(args, expectations)
    g.args_match = not expect_problems
    g.notes.extend(expect_problems)

    g.correct = g.right_tool and g.args_schema_ok and g.args_match
    return g
