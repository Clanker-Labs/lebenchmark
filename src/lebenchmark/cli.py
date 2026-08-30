"""Command line. Four verbs: probe, plan, run, report."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import typer

from . import __version__
from .report import read, regrade, render, summarise, write_summary
from .run import execute, manifest, plan_budget, plan_latency, plan_toolcall
from .tasks import TaskError, load

app = typer.Typer(add_completion=False, help="Benchmark local models on the chezmoi tool surface.")

DEFAULT_BASE = "http://spark.tailec77b2.ts.net:8000/v1"
DEFAULT_MODELS = "chat,coder,fast,vision"
DEFAULT_BUDGETS = "512,1024,2048,4096,8192"


def _models(value: str) -> list[str]:
    return [m.strip() for m in value.split(",") if m.strip()]


def _harness_info(base_url: str) -> dict | None:
    """Ask the ops dashboard what is actually serving.

    A result without the engine and preset behind it is not reproducible — the
    alias `chat` has meant two different models on two different presets. Best
    effort: pointing the benchmark at a gateway with no dashboard is fine.
    """
    root = base_url.rsplit("/v1", 1)[0].rstrip("/")
    for candidate in (root.replace(":8000", ":8701"), root):
        try:
            r = httpx.get(f"{candidate}/api/status", timeout=8)
            if r.status_code == 200:
                payload = r.json()
                return {
                    "source": f"{candidate}/api/status",
                    "runtime": payload.get("runtime"),
                    "hardware": payload.get("hardware"),
                    "models": payload.get("models"),
                }
        except Exception:  # noqa: BLE001, S112 — no dashboard is a normal case, not an error
            continue
    return None


@app.command()
def probe(base_url: str = typer.Option(DEFAULT_BASE, "--base-url")) -> None:
    """Check the endpoint is there and say what it serves."""
    info = _harness_info(base_url)
    if info:
        runtime = info.get("runtime") or {}
        hardware = info.get("hardware") or {}
        typer.echo(f"harness   {info['source']}")
        typer.echo(f"engine    {runtime.get('engine')} / preset {runtime.get('preset')}")
        typer.echo(f"model     {runtime.get('model')} (alias {runtime.get('served_alias')})")
        typer.echo(f"hardware  {hardware.get('product')} {hardware.get('ram_gb')} GiB "
                   f"{hardware.get('arch')}")
    else:
        typer.echo("harness   no ops dashboard found (fine — engine will be recorded as unknown)")
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", timeout=15,
                      headers={"Authorization": "Bearer lebenchmark"})
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
        typer.echo(f"aliases   {', '.join(ids)}")
    except Exception as exc:
        typer.echo(f"gateway   UNREACHABLE: {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc


@app.command()
def plan(
    models: str = typer.Option(DEFAULT_MODELS, "--models"),
    reps: int = typer.Option(48, "--reps", help="Trials per task per model."),
    budget_reps: int = typer.Option(25, "--budget-reps"),
    latency_reps: int = typer.Option(25, "--latency-reps"),
    budgets: str = typer.Option(DEFAULT_BUDGETS, "--budgets"),
    tasks_dir: str = typer.Option("tasks", "--tasks"),
    rate: float = typer.Option(0.4, "--rate", help="Assumed calls/second, for the estimate."),
) -> None:
    """Say how many calls a run would make, and roughly how long it would take."""
    suite = load(tasks_dir)
    names = _models(models)
    budget_models = [m for m in names if m in ("chat", "coder")] or names[:1]
    units = (
        plan_toolcall(names, suite, reps)
        + plan_budget(budget_models, suite, budget_reps, [int(b) for b in budgets.split(",")])
        + plan_latency(names, suite, latency_reps)
    )
    counts: dict[str, int] = {}
    for u in units:
        counts[u.experiment] = counts.get(u.experiment, 0) + 1
    typer.echo(f"models     {', '.join(names)}")
    typer.echo(f"tasks      {len(suite)} ({sum(len(t.prompts) for t in suite)} distinct prompts)")
    for name, count in counts.items():
        typer.echo(f"  {name:<9}{count} calls")
    typer.echo(f"total      {len(units)} calls")
    typer.echo(f"estimate   ~{len(units) / rate / 3600:.1f} h at {rate} calls/s")


@app.command()
def run(
    base_url: str = typer.Option(DEFAULT_BASE, "--base-url"),
    models: str = typer.Option(DEFAULT_MODELS, "--models"),
    reps: int = typer.Option(48, "--reps"),
    budget_reps: int = typer.Option(25, "--budget-reps"),
    latency_reps: int = typer.Option(25, "--latency-reps"),
    budgets: str = typer.Option(DEFAULT_BUDGETS, "--budgets"),
    concurrency: int = typer.Option(4, "--concurrency"),
    tasks_dir: str = typer.Option("tasks", "--tasks"),
    only: str = typer.Option("", "--only", help="Comma-separated task ids."),
    results_dir: str = typer.Option("results", "--results"),
    label: str = typer.Option("", "--label", help="Suffix for the run directory."),
    skip_budget: bool = typer.Option(False, "--skip-budget"),
    skip_latency: bool = typer.Option(False, "--skip-latency"),
) -> None:
    """Run the suite and write results/<run-id>/."""
    try:
        suite = load(tasks_dir, only=_models(only) if only else None)
    except TaskError as exc:
        typer.echo(f"task suite is invalid: {exc}", err=True)
        raise typer.Exit(2) from exc

    names = _models(models)
    harness = _harness_info(base_url)
    runtime = (harness or {}).get("runtime") or {}

    units = plan_toolcall(names, suite, reps)
    if not skip_budget:
        budget_models = [m for m in names if m in ("chat", "coder")] or names[:1]
        units += plan_budget(
            budget_models, suite, budget_reps, [int(b) for b in budgets.split(",")]
        )
    if not skip_latency:
        units += plan_latency(names, suite, latency_reps)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = f"{runtime.get('engine', 'unknown')}-{runtime.get('preset', 'unknown')}"
    run_id = f"{stamp}-{slug}" + (f"-{label}" if label else "")
    out_dir = Path(results_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = manifest(base_url, names, suite, units, concurrency, harness)
    (out_dir / "manifest.json").write_text(json.dumps(meta, indent=2))

    typer.echo(f"run       {run_id}")
    typer.echo(f"calls     {len(units)} across {len(names)} models at concurrency {concurrency}")
    typer.echo(f"writing   {out_dir}/raw.jsonl")

    started = time.time()

    def progress(done: int, total: int, _elapsed: float, _trial: object) -> None:
        if done % 25 and done != total:
            return
        rate = done / max(time.time() - started, 1e-9)
        remaining = (total - done) / rate if rate else 0
        sys.stderr.write(
            f"\r  {done}/{total}  {rate:.2f} calls/s  ~{remaining / 60:.0f} min left    "
        )
        sys.stderr.flush()

    execute(units, base_url, out_dir, concurrency=concurrency, on_progress=progress)
    sys.stderr.write("\n")

    elapsed = time.time() - started
    meta["finished_at"] = datetime.now(UTC).isoformat()
    meta["elapsed_s"] = round(elapsed, 1)
    (out_dir / "manifest.json").write_text(json.dumps(meta, indent=2))

    rows = read(out_dir)
    summary = summarise(rows, tasks_dir)
    write_summary(summary, out_dir)
    (out_dir / "report.md").write_text(render(summary, meta))
    typer.echo(f"done      {len(rows)} calls in {elapsed / 60:.1f} min")
    typer.echo(f"report    {out_dir}/report.md")


@app.command()
def report(
    run_dir: str = typer.Argument(..., help="A results/<run-id> directory."),
    tasks_dir: str = typer.Option("tasks", "--tasks"),
) -> None:
    """Re-aggregate an existing run. Never touches the gateway."""
    path = Path(run_dir)
    rows = read(path)
    meta_path = path / "manifest.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {
        "base_url": "unknown", "started_at": "unknown", "temperature": "unknown",
        "max_tokens": "unknown", "tool_belt_size": "unknown", "harness": None,
    }
    # Re-score against the current grader rather than trusting the verdicts
    # written at run time. That is the point of storing responses.
    summary = summarise(regrade(rows, tasks_dir), tasks_dir)
    write_summary(summary, path)
    text = render(summary, meta)
    (path / "report.md").write_text(text)
    typer.echo(text)


@app.command()
def calibrate(
    base_url: str = typer.Option(DEFAULT_BASE, "--base-url"),
    models: str = typer.Option(DEFAULT_MODELS, "--models"),
    concurrencies: str = typer.Option("1,2,4,8", "--concurrencies"),
    reps: int = typer.Option(8, "--reps", help="Calls per (model, concurrency) cell."),
    out: str = typer.Option("", "--out", help="Write JSON here as well as printing."),
) -> None:
    """Measure throughput against concurrency, and size a run from real numbers.

    Run this before committing hours to `run`. It answers the two questions that
    decide the schedule: how many calls per second this engine will actually do,
    and whether raising concurrency changes that.
    """
    from .run import calibrate as measure

    prompts = [t.prompts[0] for t in load("tasks")][:4]
    rows = measure(base_url, _models(models), [int(c) for c in concurrencies.split(",")], reps, prompts)
    typer.echo(f"{'model':<8}{'conc':>5}{'calls/s':>10}{'gen tok/s':>11}"
               f"{'mean lat':>10}{'mean ctok':>11}")
    for row in rows:
        typer.echo(
            f"{row['model']:<8}{row['concurrency']:>5}{row['calls_per_s']:>10.3f}"
            f"{row['gen_tokens_per_s']:>11.1f}{row['mean_latency_s'] or 0:>9.1f}s"
            f"{row['mean_completion_tokens'] or 0:>11}"
        )
    if out:
        Path(out).write_text(json.dumps(rows, indent=2))
        typer.echo(f"written   {out}")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
