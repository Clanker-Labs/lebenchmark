"""Emit the JSON the study site draws from.

The site never hardcodes a number. Everything it plots comes out of a run's
`raw.jsonl` through this module, so a re-run or a re-grade updates the charts
by regenerating one file. A figure that disagrees with the data it describes is
the most expensive kind of mistake in a write-up, and the cheapest way to avoid
it is to not type the numbers twice.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .grade import Outcome
from .report import read, regrade, summarise
from .stats import wilson
from .tasks import load

MODEL_ORDER = ("chat", "coder", "fast", "vision")


def _rate(r: Any) -> dict[str, Any]:
    return {"point": r.point, "low": r.low, "high": r.high, "n": r.trials, "k": r.successes}


def build(run_dir: str | Path, tasks_dir: str | Path = "tasks") -> dict[str, Any]:
    rows_raw = read(run_dir)
    rows = regrade(rows_raw, tasks_dir)
    summary = summarise(rows, tasks_dir)
    manifest = json.loads((Path(run_dir) / "manifest.json").read_text())

    models = [m for m in MODEL_ORDER if m in summary["models"]]
    models += [m for m in summary["models"] if m not in models]

    toolcall = [r for r in rows if r["experiment"] == "toolcall"]

    # The same rows scored by the grader that shipped with the run, so the site
    # can show what the correction actually moved rather than asserting it.
    first_pass: dict[str, Any] = {}
    for model in models:
        live = [r for r in rows_raw
                if r["experiment"] == "toolcall" and r["model"] == model
                and r["task_kind"] == "tool" and r["outcome"] != Outcome.ERROR]
        first_pass[model] = _rate(wilson(sum(1 for r in live if r["correct"]), len(live)))

    per_task: dict[str, dict[str, Any]] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in toolcall:
        grouped[(row["task_id"], row["model"])].append(row)
    for (task_id, model), rs in grouped.items():
        live = [r for r in rs if r["outcome"] != Outcome.ERROR]
        per_task.setdefault(task_id, {})[model] = _rate(
            wilson(sum(1 for r in live if r["correct"]), len(live))
        )

    tasks = {t.id: {"kind": t.kind, "expect": t.expect_tool, "destructive": t.destructive,
                    "prompts": t.prompts, "rationale": t.rationale}
             for t in load(tasks_dir)}

    budget: dict[str, list[dict[str, Any]]] = {}
    for model, caps in summary["budget"].items():
        budget[model] = [
            {"max_tokens": int(cap), "n": b["n"],
             "empty": _rate(b["empty"]), "answered": _rate(b["answered"]),
             "another_call": _rate(b["another_call"]),
             "truncated": _rate(b["truncated"]),
             "mean_completion_tokens": b["mean_completion_tokens"]}
            for cap, b in sorted(caps.items())
        ]

    # Verbatim examples of each failure, for the site's classifier playground.
    examples = []
    seen: set[str] = set()
    for row in toolcall:
        if row["outcome"] in (Outcome.PROSE_TOOL_SYNTAX, Outcome.CONFIRMATION) and row["content"]:
            key = f"{row['model']}:{row['outcome']}:{row['syntax_shape']}"
            if key in seen:
                continue
            seen.add(key)
            examples.append({"model": row["model"], "task": row["task_id"],
                             "outcome": str(row["outcome"]), "shape": row["syntax_shape"],
                             "content": row["content"][:700]})

    return {
        "generated_from": str(Path(run_dir).name),
        "engine": ((manifest.get("harness") or {}).get("runtime") or {}).get("engine", "unknown"),
        "preset": ((manifest.get("harness") or {}).get("runtime") or {}).get("preset", "unknown"),
        "started_at": manifest.get("started_at"),
        "elapsed_s": manifest.get("elapsed_s"),
        "temperature": manifest.get("temperature"),
        "max_tokens": manifest.get("max_tokens"),
        "tool_belt_size": manifest.get("tool_belt_size"),
        "total_calls": summary["totals"]["calls"],
        "errors": summary["totals"]["errors"],
        "models": models,
        "emission": {m: {
            "structured": _rate(summary["models"][m].emission),
            "prose_call": _rate(summary["models"][m].prose_call),
            "refusal": _rate(summary["models"][m].refusal),
            "confirmation": _rate(summary["models"][m].confirmation),
            "empty": _rate(summary["models"][m].empty),
        } for m in models},
        "accuracy": {m: {
            "tool_choice": _rate(summary["models"][m].tool_choice),
            "args_schema": _rate(summary["models"][m].args_schema),
            "end_to_end": _rate(summary["models"][m].task_success),
            "abstention": _rate(summary["models"][m].abstention),
            "first_pass_end_to_end": first_pass[m],
        } for m in models},
        "speed": {m: {
            "mean": summary["models"][m].latency_all.mean,
            "p50": summary["models"][m].latency_all.p50,
            "p95": summary["models"][m].latency_all.p95,
            "gen_tok_s": summary["models"][m].tokens_per_s,
            "ttft_p50": (summary["latency"].get(m) or {}).get("ttft").p50
                        if summary["latency"].get(m) else None,
            "mean_completion_tokens": (summary["latency"].get(m) or {}).get(
                "mean_completion_tokens"),
        } for m in models},
        "syntax_shapes": {m: dict(summary["models"][m].syntax_shapes) for m in models},
        "budget": budget,
        "per_task": per_task,
        "tasks": tasks,
        "outcome_counts": {m: dict(Counter(
            r["outcome"] for r in toolcall if r["model"] == m)) for m in models},
        "examples": examples,
    }
