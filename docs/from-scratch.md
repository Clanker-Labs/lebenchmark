# Building this from scratch

A guide to doing the whole thing yourself, in the order the decisions actually
have to be made. It is written as an argument rather than a tutorial: every step
below exists because a shortcut was tried first and turned out to measure the
wrong thing.

If you only want to run the benchmark, the README is enough. Read this if you
want to build one for a different fleet, or if you want to know why a number
here should be believed.

## Before you write anything: find the claim

A benchmark with no claim under test becomes a scoreboard, and a scoreboard
gets built to whatever is easy to count. Start from something somebody already
wrote down and is acting on.

Here it was two lines in `docs/AGENTS.md`:

> The Spark returns a tool call as prose in roughly 8% of calls. Measured:
> 11/12, then 1 failure.

That sentence was already load-bearing — it is the stated reason agent loops on
this box retry. So the first useful thing to compute is not a benchmark at all.
It is the confidence interval on the evidence:

```python
>>> from lebenchmark.stats import wilson
>>> wilson(1, 12)
8.3% [1.5, 35.4] (n=12)
```

That interval is the whole justification for the repo. Do this step first. If
the existing number turns out to be adequately supported, you have saved
yourself an evening.

## Step 1: take the real tool belt, not a clean one

The obvious start is three tidy functions. It is wrong, and wrong in a direction
that flatters the result.

Tool-call reliability depends on how many tools are on the belt and how
confusable they are. LeClanker hands a model sixteen, and three of them —
`memory_save`, `remember_fact`, `capture_note` — all write text somewhere.
Choosing between those is a real part of the job and the source of real
failures. A three-tool benchmark cannot see any of it.

So `toolbelt.py` is a transcription of `src/leclanker/tools/*.py`: same names,
same descriptions, same enums, same required arguments. When LeClanker's belt
changes, this file has to change with it, and a run from before the change is
not comparable to one after.

**Nothing executes.** Calls are graded against the schema and thrown away. This
is not only safety, though restarting apps at 3 a.m. would be reason enough — it
is what lets the suite run against a production gateway with no credentials and
no side effects.

## Step 2: settle temperature before you design anything else

Everything about sample size depends on whether repetitions are samples. Check;
do not assume.

```python
# eight identical requests, temperature 0
sigs = {call("fast", temp=0.0) for _ in range(8)}
len(sigs)  # 1
```

One. Byte-identical, eight times, on both `fast` and `chat`. At temperature 0
this gateway is a lookup table, and fifty repetitions of a prompt measure one
prompt fifty times while looking exactly like a sample of fifty.

At `0.3` — LeClanker's production setting — output varies. That settles it: the
suite runs at the temperature the ecosystem runs at, and would even if it were
inconvenient, because a failure rate at a temperature nobody uses is not a fact
about the system.

This also decides the task format. Since stochastic variation alone explores a
narrow neighbourhood, every task carries **four paraphrases** and repetitions
cycle through them. "Kill ai212" and "stop ai212" must both reach
`action: "stop"`, and only one of those is the word in the enum.

## Step 3: classify the response before you score it

This is the part to get right, and the part that is tempting to skip.

The naive check is `if response.tool_calls:` — success or failure. That check
cannot see the failure the repo exists for. When the model emits
`<tools>{"name": "ecosystem_app", ...}</tools>` as ordinary content with
`finish_reason: stop`, the naive check sees an empty `tool_calls` array and a
chatty answer, and records a refusal. It was not a refusal. The model picked the
right tool and serialised it into a field nobody reads.

Those are different bugs with different fixes. A serialised call is a parsing
problem that a retry usually clears. A genuine refusal is a prompting problem
that retrying does nothing about. One number covering both tells you a rate and
hides which lever to pull.

Hence five outcomes: `tool_call`, `prose_tool_syntax`, `prose_plain`, `empty`,
`error`. `empty` is separate because a thinking model that spends its budget
reasoning returns nothing at all, which is neither a refusal nor a call.

The detector's hard requirement is **no false positives**. Every pattern must
match a serialised call and never a mention:

```
"I'll use ecosystem_app to restart it."              -> prose_plain
"The action field accepts start, stop, restart."      -> prose_plain
'<tools>{"name": "ecosystem_app", ...}</tools>'       -> prose_tool_syntax
```

A false positive inflates the headline number directly, which is the one thing
this repo must not do. `tests/test_grade.py` pins all three cases and several
more; write those tests before the detector, not after.

## Step 4: grade in layers

One boolean per call throws away the diagnosis. Score four things separately:

1. did a structured call come back at all
2. was it the right tool
3. were the arguments schema-valid — required present, nothing invented, enums
   respected
4. did the arguments match what the task asked for

A model that scores 100/85/99/99 has a tool-selection problem. One that scores
100/99/70/99 has an argument problem. The end-to-end number — all four — is what
an agent loop actually experiences, and it is the only one to quote on its own.

Compare free text with substrings, never equality. A task that demands one exact
phrasing of a saved fact is measuring wording.

Record a hallucinated app name separately even when the task would have failed
anyway. A call naming an app that does not exist is well-formed and passes
schema validation, so nothing else in the pipeline will catch it.

## Step 5: include tasks with no right tool

A suite made only of tool tasks rewards a model that calls something every turn.
That model tops the table and is dangerous in production, because a spurious
`ecosystem_app` call restarts an app nobody asked about.

Three of the fifteen tasks need no tool. One of them, `hardware_opinion`, is
deliberately baited: it asks about this machine's own hardware, so it reads like
a status question while being pure background knowledge.

## Step 6: measure the machine before you schedule the run

Do not guess throughput. The first estimate here was 0.4 calls/s from a probe
using short completions; the real figure on the full belt was 0.09, and a run
budgeted at two hours was really nine.

Two things came out of measuring properly, both of which changed the design:

**Concurrency buys nothing.** Generation throughput is flat from 1 to 8 workers
(`fast` 38.8 → 39.9 tok/s, `chat` 58.0 → 58.3) while mean latency triples.
Ollama serialises. So the run uses **concurrency 1** — same wall-clock, and
latency numbers that are not inflated by queueing that bought nothing.

**Wall-clock is completion tokens, nothing else.** `chat` spends 1228 tokens
emitting a one-line tool call, against `coder`'s 96. `chat` generates *faster*
per token and is fourteen times slower per call. Estimate a run as total
completion tokens ÷ generation rate; call counts alone will mislead you by an
order of magnitude.

`lebenchmark calibrate` does this measurement, and `lebenchmark plan` turns it
into a schedule. Run both before committing an evening.

## Step 7: write down the raw responses

Store the response, not the verdict. `raw.jsonl` keeps the prompt, the content,
the parsed call, every sub-check and the timings, one line per call, flushed as
each call returns.

Two things fall out of that, both of which you will want:

- **Re-grading is free.** The next engine will serialise calls in some new
  shape. Teach the detector that shape and re-run `lebenchmark report` over
  every past run; no GPU time.
- **An interrupted run is still a result.** A four-hour job against a machine on
  a tailnet will sometimes not finish. A partial JSONL is analysable. A partial
  in-memory aggregate is nothing.

Record the engine and preset in `manifest.json` too. The Spark's engine is
explicitly not a constant — the alias `chat` has meant a vLLM DeepSeek-R1 and an
Ollama Qwen3-Next-80B — and a result without that context is not reproducible.

## Step 8: never print a rate without its interval

The failure that started all of this was not a wrong measurement. It was a right
measurement quoted without its uncertainty. Do not repeat it in the artefact
built to correct it.

Every rate in `report.md` carries a Wilson 95% interval and its `n`. Use Wilson,
not the normal approximation: at these rates the normal interval goes below zero
and reports certainty it has not got, and at zero observed failures it collapses
to a point, which is a lie — no failures in 100 calls is entirely consistent
with a 3% rate.

When two models are compared, run the two-proportion test and print the p. If
the intervals overlap, the run did not distinguish them, and the table should
make that impossible to miss.

## Rebuild it for another fleet

The parts to replace, in order of how much thought each needs:

| file | change |
|---|---|
| `toolbelt.py` | your agent's real tool schemas, and the valid values for closed arguments |
| `tasks/*.yaml` | requests your users really make, four phrasings each, plus abstention tasks |
| `fixtures.py` | a realistic tool result of the size your loop actually feeds back |
| `grade.py` | only if your models serialise calls in a shape the detector does not know |

`client.py`, `stats.py`, `run.py` and `report.py` should need nothing. They only
assume an OpenAI-compatible endpoint, which is the point — the same suite has to
run against a cloud model or the numbers have no baseline to sit beside.
