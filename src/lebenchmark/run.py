"""Executing a run and writing it down.

Everything lands in one JSONL file, one line per call, written as the call
completes. That is deliberate: a four-hour run against a machine on a tailnet
will sometimes be interrupted, and a partial JSONL is still analysable while a
partial in-memory aggregate is nothing at all.
"""

from __future__ import annotations

import json
import platform
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .client import Client, Response
from .config import Config
from .fixtures import BUDGET_QUESTION, ecosystem_status_payload
from .grade import grade
from .tasks import Task
from .toolbelt import SYSTEM_PROMPT, TOOLS

#: LeClanker's own production settings (src/leclanker/config.py). The benchmark
#: runs at these, not at temperature 0 — at 0 the Spark is byte-deterministic,
#: so repeated trials of one prompt return one answer N times and a "rate"
#: computed from them is a rate over nothing.
PROD_TEMPERATURE = 0.3
PROD_MAX_TOKENS = 8192


@dataclass(slots=True)
class Trial:
    """One call, and everything needed to re-grade it later without re-running."""

    experiment: str
    model: str
    task_id: str
    task_kind: str
    rep: int
    prompt_index: int
    prompt: str
    temperature: float
    max_tokens: int
    streaming: bool
    ok: bool
    outcome: str
    correct: bool
    latency_s: float
    ttft_s: float | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    called_tool: str | None
    called_args: dict[str, Any] | None
    syntax_shape: str | None
    hallucinated_app: str | None
    emitted_call: bool
    right_tool: bool
    args_schema_ok: bool
    args_match: bool
    content: str
    #: Length only. Storing whole chains of thought would multiply the JSONL
    #: several times over for a number that is only ever used as "was there any".
    reasoning_chars: int
    notes: list[str]
    started_at: str


def _record(
    experiment: str,
    model: str,
    task: Task,
    rep: int,
    prompt_index: int,
    prompt: str,
    temperature: float,
    max_tokens: int,
    streaming: bool,
    response: Response,
    started_at: str,
) -> Trial:
    g = grade(task, response.ok, response.content, response.tool_calls)
    return Trial(
        experiment=experiment,
        model=model,
        task_id=task.id,
        task_kind=task.kind,
        rep=rep,
        prompt_index=prompt_index,
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        ok=response.ok,
        outcome=str(g.outcome),
        correct=g.correct,
        latency_s=round(response.latency_s, 4),
        ttft_s=round(response.ttft_s, 4) if response.ttft_s is not None else None,
        finish_reason=response.finish_reason,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        called_tool=g.called_tool,
        called_args=g.called_args,
        syntax_shape=g.syntax_shape,
        hallucinated_app=g.hallucinated_app,
        emitted_call=g.emitted_call,
        right_tool=g.right_tool,
        args_schema_ok=g.args_schema_ok,
        args_match=g.args_match,
        # Truncated: a full thinking-model transcript times 3000 makes the JSONL
        # unwieldy, and 2000 characters is more than enough to see what went wrong.
        content=response.content[:2000],
        reasoning_chars=len(response.reasoning),
        notes=g.notes + ([response.error] if response.error else []),
        started_at=started_at,
    )


@dataclass(slots=True)
class Unit:
    """One scheduled call, resolved before the run starts so the plan is inspectable."""

    experiment: str
    model: str
    task: Task
    rep: int
    prompt_index: int
    prompt: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    max_tokens: int
    temperature: float
    streaming: bool


def _turn(prompt: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def _second_turn(prompt: str) -> list[dict[str, Any]]:
    """A user turn, a tool call, its result, then a follow-up question.

    This is the shape an agent loop is actually in when the budget failure
    bites, and the shape a single-turn benchmark never reaches.
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_status_0",
                    "type": "function",
                    "function": {"name": "ecosystem_status", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_status_0",
            "content": ecosystem_status_payload(),
        },
        {"role": "user", "content": BUDGET_QUESTION},
    ]


def plan_toolcall(models: list[str], tasks: list[Task], reps: int) -> list[Unit]:
    units = []
    for model in models:
        for task in tasks:
            for rep in range(reps):
                index, prompt = task.prompt_for(rep)
                units.append(
                    Unit(
                        experiment="toolcall",
                        model=model,
                        task=task,
                        rep=rep,
                        prompt_index=index,
                        prompt=prompt,
                        messages=_turn(prompt),
                        tools=list(TOOLS),
                        max_tokens=PROD_MAX_TOKENS,
                        temperature=PROD_TEMPERATURE,
                        streaming=False,
                    )
                )
    return units


def plan_budget(
    models: list[str], tasks: list[Task], reps: int, budgets: list[int]
) -> list[Unit]:
    """Sweep max_tokens on a second agent turn carrying a full tool result."""
    anchor = next((t for t in tasks if t.id == "fleet_status"), tasks[0])
    units = []
    for model in models:
        for budget in budgets:
            for rep in range(reps):
                index, prompt = anchor.prompt_for(rep)
                units.append(
                    Unit(
                        experiment="budget",
                        model=model,
                        task=anchor,
                        rep=rep,
                        prompt_index=index,
                        prompt=prompt,
                        messages=_second_turn(prompt),
                        tools=list(TOOLS),
                        max_tokens=budget,
                        temperature=PROD_TEMPERATURE,
                        streaming=False,
                    )
                )
    return units


def plan_latency(models: list[str], tasks: list[Task], reps: int) -> list[Unit]:
    """Streaming calls, to get time-to-first-token rather than only a total."""
    anchor = next((t for t in tasks if t.id == "restart_app"), tasks[0])
    units = []
    for model in models:
        for rep in range(reps):
            index, prompt = anchor.prompt_for(rep)
            units.append(
                Unit(
                    experiment="latency",
                    model=model,
                    task=anchor,
                    rep=rep,
                    prompt_index=index,
                    prompt=prompt,
                    messages=_turn(prompt),
                    tools=list(TOOLS),
                    max_tokens=PROD_MAX_TOKENS,
                    temperature=PROD_TEMPERATURE,
                    streaming=True,
                )
            )
    return units


def execute(
    units: list[Unit],
    base_url: str,
    out_dir: Path,
    concurrency: int = 4,
    on_progress: Any = None,
) -> Path:
    """Run every unit and stream results to ``raw.jsonl``.

    Units are interleaved by model rather than run model-by-model. A four-hour
    run drifts — the Spark gets other traffic, the room warms up — and grouping
    all of one model's calls together turns that drift into a fake difference
    between models.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw.jsonl"

    work: queue.Queue[Unit | None] = queue.Queue()
    for unit in _interleave(units):
        work.put(unit)
    for _ in range(concurrency):
        work.put(None)

    write_lock = threading.Lock()
    counter = {"done": 0}
    started = time.time()

    def worker() -> None:
        with Client(base_url, api_key=Config.load().api_key) as client:
            while True:
                unit = work.get()
                if unit is None:
                    return
                started_at = datetime.now(UTC).isoformat()
                call = client.complete_streaming if unit.streaming else client.complete
                response = call(
                    model=unit.model,
                    messages=unit.messages,
                    tools=unit.tools,
                    max_tokens=unit.max_tokens,
                    temperature=unit.temperature,
                )
                trial = _record(
                    unit.experiment, unit.model, unit.task, unit.rep, unit.prompt_index,
                    unit.prompt, unit.temperature, unit.max_tokens, unit.streaming,
                    response, started_at,
                )
                with write_lock, raw_path.open("a") as fh:
                    fh.write(json.dumps(asdict(trial)) + "\n")
                    counter["done"] += 1
                    if on_progress:
                        on_progress(counter["done"], len(units), time.time() - started, trial)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return raw_path


def _interleave(units: list[Unit]) -> Iterator[Unit]:
    """Round-robin across models, preserving each model's own order."""
    by_model: dict[str, list[Unit]] = {}
    for unit in units:
        by_model.setdefault(unit.model, []).append(unit)
    lanes = list(by_model.values())
    for i in range(max((len(lane) for lane in lanes), default=0)):
        for lane in lanes:
            if i < len(lane):
                yield lane[i]


def manifest(
    base_url: str,
    models: list[str],
    tasks: list[Task],
    units: list[Unit],
    concurrency: int,
    harness: dict[str, Any] | None,
) -> dict[str, Any]:
    """What was run, on what, with what. Written next to the results.

    A result without the engine and preset that produced it is not reproducible:
    the Spark's engine is explicitly not a constant, and the same alias `chat`
    has already meant two different models on two different presets.
    """
    counts: dict[str, int] = {}
    for unit in units:
        counts[unit.experiment] = counts.get(unit.experiment, 0) + 1
    return {
        "lebenchmark_version": __version__,
        "started_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "models": models,
        "concurrency": concurrency,
        "temperature": PROD_TEMPERATURE,
        "max_tokens": PROD_MAX_TOKENS,
        "tasks": [
            {"id": t.id, "kind": t.kind, "expect_tool": t.expect_tool,
             "paraphrases": len(t.prompts)}
            for t in tasks
        ],
        "planned_calls": len(units),
        "planned_by_experiment": counts,
        "tool_belt_size": len(TOOLS),
        "harness": harness,
        "client_host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }


def calibrate(
    base_url: str,
    models: list[str],
    concurrencies: list[int],
    reps: int,
    prompts: list[str],
) -> list[dict[str, Any]]:
    """Measure throughput against concurrency, per model.

    Worth its own experiment rather than a footnote. The intuition every caller
    brings — that running four requests at once gets four times the work done —
    is false on this gateway, and being wrong about it costs twice: the run
    takes just as long, and every latency number it produces is inflated by
    queueing that was never going to buy anything.
    """
    from concurrent import futures

    out = []
    for model in models:
        for concurrency in concurrencies:
            jobs = [prompts[i % len(prompts)] for i in range(reps)]
            started = time.perf_counter()
            with Client(base_url, api_key=Config.load().api_key) as client:
                def one(prompt: str, _c: Client = client, _m: str = model) -> Response:
                    return _c.complete(
                        model=_m,
                        messages=_turn(prompt),
                        tools=list(TOOLS),
                        max_tokens=PROD_MAX_TOKENS,
                        temperature=PROD_TEMPERATURE,
                    )

                with futures.ThreadPoolExecutor(concurrency) as pool:
                    responses = list(pool.map(one, jobs))
            wall = time.perf_counter() - started
            live = [r for r in responses if r.ok]
            completion = sum(r.completion_tokens or 0 for r in live)
            out.append(
                {
                    "model": model,
                    "concurrency": concurrency,
                    "calls": len(jobs),
                    "errors": len(responses) - len(live),
                    "wall_s": round(wall, 2),
                    "calls_per_s": round(len(jobs) / wall, 4),
                    "gen_tokens_per_s": round(completion / wall, 2),
                    "mean_latency_s": round(
                        sum(r.latency_s for r in live) / len(live), 3
                    ) if live else None,
                    "mean_completion_tokens": round(completion / len(live)) if live else None,
                }
            )
    return out
