# lebenchmark

Measures how reliably locally-served models call tools, on the tool surface this
ecosystem actually runs.

The question it exists to answer is in `docs/AGENTS.md` of the chezmoi repo:

> **The Spark returns a tool call as prose in roughly 8% of calls.** Measured:
> 11/12, then 1 failure printing `<tools>{"name": ...}</tools>` with
> `finish_reason: stop`.

That is an honest observation, honestly reported. It also cannot carry the
weight put on it. One failure in twelve has a 95% confidence interval running
from **1.5% to 35.4%** — consistent with a rate nobody would ever notice, and
with one that breaks every agent loop on the box. You cannot write a retry
policy against that. This repo turns the anecdote into a measurement.

It is a benchmark, not a monitor. It does not page anyone and it does not run on
a timer. You run it when you change the engine, swap the preset, or want to know
whether a model is worth pointing the ecosystem at.

## What it measures

Four things, on every model served by the gateway.

**Tool-call emission.** Did a structured `tool_calls` array come back, or did the
model serialise the call into ordinary prose where no caller will look for it?
These are counted separately from a genuine refusal, because they are different
bugs with different fixes: prose-with-a-call-in-it is a parsing problem you can
retry through, a refusal is a prompting problem you cannot.

**Tool-use correctness.** Given sixteen real tools, does it pick the right one,
fill in the required arguments, respect the enums, and stay inside the set of
apps that actually exist on this machine?

**Knowing when not to.** Three tasks need no tool at all. A model that calls
something every turn scores well on the first two metrics and is dangerous in
production — a spurious `ecosystem_app` call restarts an app nobody asked about.

**Cost in time.** Latency, time-to-first-token, tokens per second, and how
throughput responds to concurrency. TTFT is measured separately from total
latency because it is the number a person feels: a thinking model that reasons
for twenty seconds and then emits its answer in one burst reads as a hang, not
as slow output.

Plus one sweep aimed at a specific documented failure: how the reasoning-token
budget changes the rate of empty answers on a second agent turn. See
`docs/methodology.md`.

## The study site

The whole thing is written up as an interactive site in `docs/`, published at
**<https://clanker-labs.github.io/lebenchmark/>** — confidence intervals you can
drag, the response classifier running in the browser, and the budget cliff as a
chart rather than a paragraph. Every figure is generated from the run's
`raw.jsonl` by `make sitedata`; the page hardcodes no numbers.

```bash
make site        # serve it locally on :8899
```

## What the first run found

`results/20260830T132135Z-ollama-qwen3-next-80b-full/` — 1310 calls, 4.6 hours,
written up in `paper/PAPER.md`.

- The documented failure is **real and reproduces verbatim**, but it belongs to
  `coder` (6.0% [3.6, 10.0]), not to the fleet default `chat` (0.5% [0.1, 2.6]).
  `fast` and `vision` did not do it once in 431 calls.
- The two models fail in **different syntaxes**, so a parser written against one
  recovers nothing from the other.
- The **8B aliases are the most reliable tool callers** — `fast` and `vision` at
  94.9% end to end, against 86.6% for both `chat` and `coder`.
- Once a model calls the right tool, arguments are essentially always valid:
  2 schema failures in 764 calls. All the loss is in *choosing* the tool.
- Below 4096 reasoning tokens `chat` returns **no answer at all** on a second
  agent turn — 100% at 1024, 46.7% at 2048, 0% at 8192. It is a cliff, not a
  gradient.
- `coder` is **17× faster per call** than `chat` at the same accuracy.

And one about benchmarking rather than about the models: the first grading put
`chat` last at 68.5% because it asked permission before restarting an app —
which its tool description tells it to do — and the grader scored obedience as
refusal. Re-scoring the stored responses put it at 86.6%.

## The tool belt is not a toy

The sixteen tools in `src/lebenchmark/toolbelt.py` are transcribed from
`src/leclanker/tools/*.py` — the belt LeClanker really hands a model. This
matters more than it sounds. Tool-call reliability depends on how many tools are
on the belt and how similar they are to each other, and a benchmark built on
three well-separated toy functions measures a situation this ecosystem is never
in. `memory_save`, `remember_fact` and `capture_note` all write text somewhere,
and telling them apart is a real part of the job.

**Nothing executes.** A tool call is graded against the schema and thrown away.
The benchmark never restarts an app, never writes a memory, never touches Home
Assistant. It needs no credentials beyond reaching the gateway.

## Run it from scratch

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), and network reach to an
OpenAI-compatible endpoint. On this fleet that is the Spark, which has no auth —
tailnet reachability is the access control — so there is no key to configure.

```bash
git clone https://github.com/Clanker-Labs/lebenchmark.git
cd lebenchmark
make setup

# Is the gateway there, and which aliases does it serve?
make probe BASE_URL=http://spark.example-tailnet.ts.net:8000/v1

# ~5 minutes. Proves the harness end to end.
make smoke BASE_URL=http://spark.example-tailnet.ts.net:8000/v1

# The real thing. Hours, not minutes — check `make plan` first.
make plan
make run BASE_URL=http://spark.example-tailnet.ts.net:8000/v1
```

`make run` writes `results/<timestamp>-<engine>-<preset>/`:

| file | what it is |
|---|---|
| `manifest.json` | endpoint, engine, preset, models, task list, settings, planned call count |
| `raw.jsonl` | one line per call — prompt, response, grade, timings. The primary record |
| `summary.json` | the aggregates, with confidence intervals |
| `report.md` | those aggregates as tables |

Point it at your own gateway by copying `.env.example` to `.env` — endpoint,
model ids, run size and concurrency all live there, and the defaults assume a
local Ollama rather than any particular machine.

Re-grade an old run without spending another hour on the GPU:

```bash
uv run lebenchmark report results/<run-id>
```

That works because `raw.jsonl` keeps the response, not just the verdict. When
the classifier learns a new prose-serialisation shape, every past run can be
re-scored against it.

### Point it at something else

Any OpenAI-compatible endpoint works, which is the whole reason the numbers mean
anything — a local rate is not interpretable without a baseline to sit beside.

```bash
# LeClanker's gateway rather than the Spark directly
make run BASE_URL=http://127.0.0.1:8484/v1 MODELS=chat,fast

# a cloud baseline
make run BASE_URL=https://openrouter.ai/api/v1 MODELS=anthropic/claude-sonnet-4.5 \
         API_KEY=$OPENROUTER_API_KEY
```

## Reading a result honestly

Three things to hold on to, all of them learned the hard way while building this.

**A rate without an interval is not a result.** Every percentage the report
prints carries a Wilson 95% interval and its `n`. If two models' intervals
overlap, the run did not distinguish them, however different the point
estimates look.

**Temperature 0 does not give you repetitions.** At `temperature=0` the Spark
returns byte-identical output to a repeated prompt — verified, 8/8. Fifty
repetitions there measure one sentence fifty times. The suite therefore runs at
LeClanker's production `temperature=0.3` and every task carries four
paraphrases, so a trial samples both the model's stochasticity and the phrasing
people actually use.

**A result is only about the engine and preset that produced it.** The Spark's
engine is explicitly not a constant; the alias `chat` has already meant a vLLM
DeepSeek-R1 and an Ollama Qwen3-Next-80B. `manifest.json` records what was
serving at the time, and comparing across presets without reading it is
comparing two different machines.

## Layout

```
src/lebenchmark/
  toolbelt.py   the 16 tools under test, from LeClanker
  tasks.py      loading and strict validation of the suite
  client.py     one OpenAI-compatible client, streaming and not
  grade.py      the five-way outcome classifier — the core instrument
  stats.py      Wilson intervals, quantiles, two-proportion test
  fixtures.py   a realistic tool result, for the second-turn experiments
  run.py        planning, scheduling, JSONL output
  report.py     aggregation and tables
tasks/          the suite, as data
docs/           methodology, from-scratch guide, limitations
results/        committed runs
paper/          the write-up
```

## Documentation

- `docs/from-scratch.md` — build the whole thing yourself, and why each decision went the way it did
- `docs/methodology.md` — what is measured, how it is graded, what the sample sizes buy
- `docs/limitations.md` — what these numbers do not license you to say
