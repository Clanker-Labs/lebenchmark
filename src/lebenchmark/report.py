"""Turning a raw JSONL into numbers, and those numbers into tables.

Reads only `raw.jsonl`, never the live gateway. Re-running a report is free, so
when the classifier learns a new prose-serialisation shape every past run can be
re-scored without spending another hour of GPU time.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .grade import Outcome, grade
from .stats import Rate, latency, two_proportion_z, wilson
from .tasks import Task, load

# Errors are transport failures, not model behaviour. They are reported on their
# own line and excluded from every rate — folding them in would blame a model
# for a dropped tailnet connection.
#: Filled in by `summarise` from the loaded suite, so the report does not have to
#: re-read the task files for one lookup.
_DESTRUCTIVE_IDS: set[str] = set()

_MODEL_OUTCOMES = (
    Outcome.TOOL_CALL,
    Outcome.PROSE_TOOL_SYNTAX,
    Outcome.PROSE_PLAIN,
    Outcome.EMPTY,
)


def read(run_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(run_dir) / "raw.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no raw.jsonl in {run_dir}")
    rows = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def regrade(rows: list[dict[str, Any]], tasks_dir: str | Path = "tasks") -> list[dict[str, Any]]:
    """Re-score stored responses against the current grader.

    This is the payoff for writing the response down rather than the verdict.
    The first full run scored `chat` at 68.5% end to end, which was wrong: it
    was asking for confirmation before restarting an app, exactly as
    `ecosystem_app`'s description instructs, and the grader counted obedience as
    a refusal. Fixing that cost no GPU time because `raw.jsonl` had kept what the
    model actually said.

    Rows whose task is no longer in the suite are returned untouched.
    """
    by_id: dict[str, Task] = {t.id: t for t in load(tasks_dir)}
    out = []
    for row in rows:
        task = by_id.get(row["task_id"])
        if task is None:
            out.append(row)
            continue
        if row.get("emitted_call"):
            # `called_args is None` recorded arguments that would not parse.
            # Reconstruct that rather than silently turning it into `{}`.
            arguments = (
                json.dumps(row["called_args"])
                if row.get("called_args") is not None
                else "<<unparseable>>"
            )
            calls = [{"id": "restored", "type": "function",
                      "function": {"name": row.get("called_tool"), "arguments": arguments}}]
        else:
            calls = []
        g = grade(task, row["ok"], row.get("content", ""), calls)
        fresh = dict(row)
        fresh.update(
            outcome=str(g.outcome), correct=g.correct, called_tool=g.called_tool,
            called_args=g.called_args, syntax_shape=g.syntax_shape,
            hallucinated_app=g.hallucinated_app, emitted_call=g.emitted_call,
            right_tool=g.right_tool, args_schema_ok=g.args_schema_ok,
            args_match=g.args_match, notes=g.notes,
        )
        out.append(fresh)
    return out


@dataclass(slots=True)
class ModelSummary:
    model: str
    trials: int
    errors: int
    #: Of the tool tasks: emitted a structured call at all.
    emission: Rate
    #: Of the tool tasks: serialised a call into prose instead. The headline.
    prose_call: Rate
    #: Of the tool tasks: refused in plain prose.
    refusal: Rate
    #: Of destructive tool tasks: asked to confirm before acting, as instructed.
    confirmation: Rate
    #: Of the tool tasks: came back with nothing at all.
    empty: Rate
    #: Of structured calls: named the right tool.
    tool_choice: Rate
    #: Of structured calls with the right tool: arguments passed the schema.
    args_schema: Rate
    #: Of structured calls with the right tool: arguments matched the task.
    args_match: Rate
    #: End to end on tool tasks: right tool, valid arguments, structured.
    task_success: Rate
    #: Of abstention tasks: answered without reaching for a tool.
    abstention: Rate
    latency_all: Any
    tokens_per_s: float
    hallucinated_apps: Counter
    syntax_shapes: Counter


def _rate(numerator: int, denominator: int) -> Rate:
    return wilson(numerator, denominator)


def summarise_model(rows: list[dict[str, Any]]) -> ModelSummary:
    model = rows[0]["model"]
    live = [r for r in rows if r["outcome"] != Outcome.ERROR]
    errors = len(rows) - len(live)

    tool_rows = [r for r in live if r["task_kind"] == "tool"]
    abstain_rows = [r for r in live if r["task_kind"] == "abstain"]
    counts = Counter(r["outcome"] for r in tool_rows)
    n_tool = len(tool_rows)

    emitted = [r for r in tool_rows if r["emitted_call"]]
    right = [r for r in emitted if r["right_tool"]]
    destructive = [r for r in tool_rows if r["outcome"] == Outcome.CONFIRMATION
                   or r["task_id"] in _DESTRUCTIVE_IDS]

    latencies = [r["latency_s"] for r in live if r["latency_s"]]
    completion = sum(r["completion_tokens"] or 0 for r in live)
    elapsed = sum(r["latency_s"] for r in live) or 1.0

    return ModelSummary(
        model=model,
        trials=len(rows),
        errors=errors,
        emission=_rate(counts[Outcome.TOOL_CALL], n_tool),
        prose_call=_rate(counts[Outcome.PROSE_TOOL_SYNTAX], n_tool),
        refusal=_rate(counts[Outcome.PROSE_PLAIN], n_tool),
        confirmation=_rate(
            sum(1 for r in destructive if r["outcome"] == Outcome.CONFIRMATION), len(destructive)
        ),
        empty=_rate(counts[Outcome.EMPTY], n_tool),
        tool_choice=_rate(len(right), len(emitted)),
        args_schema=_rate(sum(1 for r in right if r["args_schema_ok"]), len(right)),
        args_match=_rate(sum(1 for r in right if r["args_match"]), len(right)),
        task_success=_rate(sum(1 for r in tool_rows if r["correct"]), n_tool),
        abstention=_rate(sum(1 for r in abstain_rows if r["correct"]), len(abstain_rows)),
        latency_all=latency(latencies),
        tokens_per_s=completion / elapsed,
        hallucinated_apps=Counter(
            r["hallucinated_app"] for r in tool_rows if r["hallucinated_app"]
        ),
        syntax_shapes=Counter(r["syntax_shape"] for r in tool_rows if r["syntax_shape"]),
    )


def summarise(rows: list[dict[str, Any]], tasks_dir: str | Path = "tasks") -> dict[str, Any]:
    global _DESTRUCTIVE_IDS
    try:
        _DESTRUCTIVE_IDS = {t.id for t in load(tasks_dir) if t.kind == "tool" and t.destructive}
    except Exception:  # noqa: BLE001 — a summary of an old run without its suite still works
        _DESTRUCTIVE_IDS = set()

    toolcall = [r for r in rows if r["experiment"] == "toolcall"]
    budget = [r for r in rows if r["experiment"] == "budget"]
    lat = [r for r in rows if r["experiment"] == "latency"]

    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in toolcall:
        by_model[row["model"]].append(row)
    models = {m: summarise_model(rs) for m, rs in sorted(by_model.items())}

    per_task: dict[str, dict[str, Any]] = {}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in toolcall:
        grouped[(row["model"], row["task_id"])].append(row)
    for (model, task_id), rs in sorted(grouped.items()):
        live = [r for r in rs if r["outcome"] != Outcome.ERROR]
        per_task.setdefault(task_id, {})[model] = {
            "n": len(live),
            "success": _rate(sum(1 for r in live if r["correct"]), len(live)),
            "outcomes": Counter(r["outcome"] for r in live),
        }

    budget_summary: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    grouped_b: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in budget:
        grouped_b[(row["model"], row["max_tokens"])].append(row)
    for (model, cap), rs in sorted(grouped_b.items()):
        live = [r for r in rs if r["outcome"] != Outcome.ERROR]
        # Three outcomes, not two. A model that answers in prose has done the
        # job; one that emits another tool call has not answered but has not
        # failed either; one that returns nothing is the documented failure.
        # Collapsing the middle case into "answered" would hide it.
        budget_summary[model][cap] = {
            "n": len(live),
            "answered": _rate(sum(1 for r in live if r["content"].strip()), len(live)),
            "another_call": _rate(sum(1 for r in live if r["emitted_call"]), len(live)),
            "empty": _rate(sum(1 for r in live if r["outcome"] == Outcome.EMPTY), len(live)),
            "truncated": _rate(sum(1 for r in live if r["finish_reason"] == "length"), len(live)),
            "latency": latency([r["latency_s"] for r in live]),
            "mean_completion_tokens": (
                sum(r["completion_tokens"] or 0 for r in live) / len(live) if live else 0
            ),
        }

    latency_summary: dict[str, Any] = {}
    grouped_l: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in lat:
        grouped_l[row["model"]].append(row)
    for model, rs in sorted(grouped_l.items()):
        live = [r for r in rs if r["outcome"] != Outcome.ERROR]
        ttfts = [r["ttft_s"] for r in live if r["ttft_s"] is not None]
        latency_summary[model] = {
            "n": len(live),
            "ttft": latency(ttfts),
            "total": latency([r["latency_s"] for r in live]),
            "mean_completion_tokens": (
                sum(r["completion_tokens"] or 0 for r in live) / len(live) if live else 0
            ),
        }

    return {
        "models": models,
        "per_task": per_task,
        "budget": dict(budget_summary),
        "latency": latency_summary,
        "totals": {
            "calls": len(rows),
            "errors": sum(1 for r in rows if r["outcome"] == Outcome.ERROR),
        },
    }


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, Rate):
        return {
            "successes": obj.successes, "trials": obj.trials,
            "point": obj.point, "ci95": [obj.low, obj.high],
        }
    if hasattr(obj, "p50") and hasattr(obj, "mean"):
        return {"n": obj.n, "mean": obj.mean, "p50": obj.p50, "p95": obj.p95, "p99": obj.p99}
    if isinstance(obj, ModelSummary):
        return {k: _json_safe(getattr(obj, k)) for k in obj.__slots__}
    if isinstance(obj, Counter):
        return dict(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def write_summary(summary: dict[str, Any], out_dir: Path) -> Path:
    path = out_dir / "summary.json"
    path.write_text(json.dumps(_json_safe(summary), indent=2))
    return path


def render(summary: dict[str, Any], manifest: dict[str, Any]) -> str:
    models: dict[str, ModelSummary] = summary["models"]
    names = list(models)
    out: list[str] = []
    w = out.append

    harness = manifest.get("harness") or {}
    runtime = harness.get("runtime") or {}
    w("# lebenchmark run\n")
    w(f"- **Endpoint** `{manifest['base_url']}`")
    w(f"- **Engine** {runtime.get('engine', 'unknown')}, preset "
      f"`{runtime.get('preset', 'unknown')}`")
    w(f"- **Started** {manifest['started_at']}")
    w(f"- **Settings** temperature {manifest['temperature']}, "
      f"max_tokens {manifest['max_tokens']}, {manifest['tool_belt_size']} tools on the belt")
    w(f"- **Calls** {summary['totals']['calls']} "
      f"({summary['totals']['errors']} transport errors, excluded from rates)\n")

    w("## Tool-call emission\n")
    w("Of calls on tasks that require a tool. `prose call` is the documented "
      "failure: a serialised call arriving as ordinary content. `asked to "
      "confirm` is of the two destructive tasks only, whose tool description "
      "instructs the agent to confirm before acting — it is compliance, and is "
      "scored as success.\n")
    w("| model | n | structured call | prose call | plain refusal | asked to confirm | empty |")
    w("|---|---:|---|---|---|---|---|")
    for name in names:
        m = models[name]
        w(f"| `{name}` | {m.emission.trials} | {m.emission.pct()} {m.emission.ci_pct()} "
          f"| {m.prose_call.pct()} {m.prose_call.ci_pct()} "
          f"| {m.refusal.pct()} | {m.confirmation.pct()} | {m.empty.pct()} |")
    w("")

    w("## Tool-use correctness\n")
    w("`tool choice` and the argument columns are conditional on a structured "
      "call having been emitted; `end to end` is not, so it is the number that "
      "predicts what an agent loop sees.\n")
    w("| model | tool choice | args valid | args match task | end to end |")
    w("|---|---|---|---|---|")
    for name in names:
        m = models[name]
        w(f"| `{name}` | {m.tool_choice.pct()} {m.tool_choice.ci_pct()} "
          f"| {m.args_schema.pct()} | {m.args_match.pct()} "
          f"| **{m.task_success.pct()}** {m.task_success.ci_pct()} |")
    w("")

    w("## Knowing when not to call a tool\n")
    w("Three tasks need no tool. Correct means answering in plain prose.\n")
    w("| model | n | abstained correctly |")
    w("|---|---:|---|")
    for name in names:
        m = models[name]
        w(f"| `{name}` | {m.abstention.trials} | {m.abstention.pct()} {m.abstention.ci_pct()} |")
    w("")

    if any(models[n].hallucinated_apps or models[n].syntax_shapes for n in names):
        w("## What the failures looked like\n")
        for name in names:
            m = models[name]
            if m.syntax_shapes:
                shapes = ", ".join(f"`{k}` ×{v}" for k, v in m.syntax_shapes.most_common())
                w(f"- `{name}` prose-serialisation shapes: {shapes}")
            if m.hallucinated_apps:
                apps = ", ".join(f"`{k}` ×{v}" for k, v in m.hallucinated_apps.most_common(5))
                w(f"- `{name}` app names not in the registry: {apps}")
        w("")

    w("## Speed\n")
    w("Totals are from the non-streaming suite under the run's concurrency, so "
      "they include queueing. TTFT is from the streaming suite.\n")
    w("| model | mean | p50 | p95 | completion tok/s | TTFT p50 | TTFT p95 |")
    w("|---|---|---|---|---:|---|---|")
    for name in names:
        m = models[name]
        lat = summary["latency"].get(name)
        ttft50 = f"{lat['ttft'].p50:.2f}s" if lat and lat["ttft"].n else "—"
        ttft95 = f"{lat['ttft'].p95:.2f}s" if lat and lat["ttft"].n else "—"
        w(f"| `{name}` | {m.latency_all.mean:.2f}s | {m.latency_all.p50:.2f}s "
          f"| {m.latency_all.p95:.2f}s | {m.tokens_per_s:.1f} | {ttft50} | {ttft95} |")
    w("")

    if summary["budget"]:
        w("## Reasoning budget\n")
        w("A second agent turn carrying a full `ecosystem_status` result, then an "
          "open question about it. Tools stay bound, as they are in production, "
          "so a model may answer, call another tool, or return nothing. The last "
          "column is the documented failure: the budget went on reasoning and no "
          "content came back.\n")
        for model, caps in summary["budget"].items():
            w(f"**`{model}`**\n")
            w("| max_tokens | n | answered in prose | called another tool | "
              "finish=length | mean completion tokens | **returned nothing** |")
            w("|---:|---:|---|---|---|---:|---|")
            for cap in sorted(caps):
                b = caps[cap]
                w(f"| {cap} | {b['n']} | {b['answered'].pct()} {b['answered'].ci_pct()} "
                  f"| {b['another_call'].pct()} | {b['truncated'].pct()} "
                  f"| {b['mean_completion_tokens']:.0f} "
                  f"| **{b['empty'].pct()}** {b['empty'].ci_pct()} |")
            w("")

    w("## Per task\n")
    w("End-to-end success. A task where every model fails is usually a task "
      "problem; a task where they split is a model difference.\n")
    header = "| task | " + " | ".join(f"`{n}`" for n in names) + " |"
    w(header)
    w("|---" * (len(names) + 1) + "|")
    for task_id in sorted(summary["per_task"]):
        cells = []
        for name in names:
            entry = summary["per_task"][task_id].get(name)
            cells.append(entry["success"].pct(0) if entry else "—")
        w(f"| `{task_id}` | " + " | ".join(cells) + " |")
    w("")

    if len(names) >= 2:
        w("## Are the differences real?\n")
        w("Pooled two-proportion z on end-to-end success. p below 0.05 means the "
          "run distinguished them.\n")
        w("| A | B | A | B | p |")
        w("|---|---|---|---|---|")
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ma, mb = models[a].task_success, models[b].task_success
                _, p = two_proportion_z(ma.successes, ma.trials, mb.successes, mb.trials)
                w(f"| `{a}` | `{b}` | {ma.pct()} | {mb.pct()} | {p:.4f} |")
        w("")

    return "\n".join(out)
