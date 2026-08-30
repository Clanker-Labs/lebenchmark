"""Loading and validating the task suite.

Validation is strict and happens before a run starts. A typo in an expected
tool name would otherwise show up as a model that "failed every trial", and the
run costs hours — finding out afterwards is expensive in the one resource this
benchmark actually spends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .toolbelt import TOOLS_BY_NAME

KINDS = ("tool", "abstain")
_RULES = ("equals", "contains_any", "one_of")


@dataclass(slots=True)
class Task:
    id: str
    kind: str
    prompts: list[str]
    expect_tool: str | None = None
    #: Other tools that are also a defensible reading of the request. The belt
    #: has genuine near-duplicates — `memory_save` and `remember_fact` both save
    #: a durable fact, to different stores, and nothing in a user's phrasing
    #: picks between them. Scoring one as the only right answer measures the
    #: ambiguity of the belt, not the model.
    accept_tools: list[str] = field(default_factory=list)
    expect_args: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The tool's own description instructs the agent to confirm before acting.
    #: A model that asks instead of acting is obeying, not failing — see
    #: `grade.py`. Without this flag the benchmark penalises exactly the
    #: behaviour the tool description asks for.
    destructive: bool = False
    rationale: str = ""

    @property
    def acceptable_tools(self) -> tuple[str, ...]:
        return tuple(filter(None, (self.expect_tool, *self.accept_tools)))

    def prompt_for(self, rep: int) -> tuple[int, str]:
        """Cycle through paraphrases so repetitions spread across all of them."""
        index = rep % len(self.prompts)
        return index, self.prompts[index]


class TaskError(ValueError):
    pass


def _validate(task: Task, source: Path) -> None:
    where = f"{source.name}:{task.id}"
    if task.kind not in KINDS:
        raise TaskError(f"{where}: kind must be one of {KINDS}, got {task.kind!r}")
    if not task.prompts:
        raise TaskError(f"{where}: needs at least one prompt")
    if len(set(task.prompts)) != len(task.prompts):
        raise TaskError(f"{where}: duplicate prompts")

    if task.kind == "abstain":
        if task.expect_tool or task.expect_args or task.accept_tools or task.destructive:
            raise TaskError(f"{where}: an abstain task must not expect a tool or arguments")
        return

    if not task.expect_tool:
        raise TaskError(f"{where}: a tool task needs expect_tool")
    if task.expect_tool not in TOOLS_BY_NAME:
        raise TaskError(
            f"{where}: expect_tool {task.expect_tool!r} is not on the belt "
            f"({', '.join(sorted(TOOLS_BY_NAME))})"
        )

    for alt in task.accept_tools:
        if alt not in TOOLS_BY_NAME:
            raise TaskError(f"{where}: accept_tools names {alt!r}, which is not on the belt")
        if alt == task.expect_tool:
            raise TaskError(f"{where}: accept_tools repeats expect_tool {alt!r}")

    properties = TOOLS_BY_NAME[task.expect_tool]["function"]["parameters"]["properties"]
    for arg, rule in task.expect_args.items():
        if arg not in properties:
            raise TaskError(f"{where}: expects argument {arg!r}, which {task.expect_tool} has no")
        if not isinstance(rule, dict) or not rule:
            raise TaskError(f"{where}: rule for {arg!r} must be a non-empty mapping")
        for key in rule:
            if key not in _RULES:
                raise TaskError(f"{where}: unknown rule {key!r} for {arg!r}; use one of {_RULES}")
        # An `equals` against an enum that cannot contain it is always a typo.
        allowed = properties[arg].get("enum")
        if allowed and "equals" in rule and rule["equals"] not in allowed:
            raise TaskError(f"{where}: {arg}={rule['equals']!r} is not in the enum {allowed}")


def load(directory: str | Path = "tasks", only: list[str] | None = None) -> list[Task]:
    path = Path(directory)
    files = sorted(path.glob("*.yaml"))
    if not files:
        raise TaskError(f"no task files in {path}/")

    tasks: list[Task] = []
    seen: dict[str, Path] = {}
    for source in files:
        entries = yaml.safe_load(source.read_text()) or []
        for entry in entries:
            task = Task(
                id=entry["id"],
                kind=entry["kind"],
                prompts=list(entry["prompts"]),
                expect_tool=entry.get("expect_tool"),
                accept_tools=list(entry.get("accept_tools") or []),
                expect_args=entry.get("expect_args") or {},
                destructive=bool(entry.get("destructive")),
                rationale=(entry.get("rationale") or "").strip(),
            )
            if task.id in seen:
                raise TaskError(f"duplicate task id {task.id!r} in {source.name} and {seen[task.id].name}")
            seen[task.id] = source
            _validate(task, source)
            tasks.append(task)

    if only:
        wanted = set(only)
        unknown = wanted - {t.id for t in tasks}
        if unknown:
            raise TaskError(f"unknown task ids: {', '.join(sorted(unknown))}")
        tasks = [t for t in tasks if t.id in wanted]
    return tasks
