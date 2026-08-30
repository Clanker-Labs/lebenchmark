# Methodology

What is measured, how it is graded, and what a given sample size actually buys.

## The claim under test

`docs/AGENTS.md` in the chezmoi repo says:

> The Spark returns a tool call as prose in roughly 8% of calls. Measured:
> 11/12, then 1 failure printing `<tools>{"name": ...}</tools>` with
> `finish_reason: stop`. It is not a broken gateway and not a wrong model — it
> is a rate.

The reasoning is right and the observation is real. The number is the problem.
One failure in twelve gives a Wilson 95% interval of **[1.5%, 35.4%]**. Every
downstream decision — how many retries, whether to bother, whether to switch
models — needs to know which end of that you are on, and n=12 cannot say.

Everything below exists to narrow that interval and to check whether the same
model is failing in other ways nobody counted.

## Conditions

| | |
|---|---|
| Endpoint | `/v1/chat/completions`, OpenAI-compatible |
| Temperature | **0.3** — LeClanker's production value |
| max_tokens | **8192** — LeClanker's production value |
| Tools bound | all 16, on every call, `tool_choice: auto` |
| Concurrency | **1** |
| Turns | one, except the reasoning-budget sweep |

### Why temperature 0.3 and not 0

At `temperature=0` this gateway is deterministic. Verified directly: the same
prompt sent eight times to `fast` and eight times to `chat` returned
byte-identical tool calls, 8/8 both times. Repetitions there are not samples.
They are one sample, copied.

At `0.3` — what LeClanker actually runs — output varies. That is the condition
whose failure rate anybody cares about, so it is the condition measured.

### Why four paraphrases per task

Stochastic sampling alone explores a narrow neighbourhood of one sentence. Real
requests arrive phrased many ways, and "kill ai212" has to map onto
`action: "stop"` just as reliably as "stop ai212" does. Each task carries four
phrasings and repetitions cycle through them, so a trial samples both the
model's randomness and the phrasing variance a household actually produces.

### Why concurrency 1

Measured on this engine, throughput does not respond to concurrency:

| model | conc 1 | conc 4 | mean latency 1 → 4 |
|---|---:|---:|---|
| `fast` | 38.8 gen tok/s | 39.9 | 8.2s → 24.0s |
| `chat` | 58.0 | 58.3 | 21.2s → 52.0s |
| `coder` | 62.7 | 74.3 | 1.5s → 4.2s |

Ollama serialises. Four workers get the same work done and each request waits
behind three others, so every latency number comes back inflated by queueing
that bought nothing. `lebenchmark calibrate` reproduces this table on any
endpoint; run it before assuming a different gateway behaves the same way.

## The five outcomes

Every response is classified into exactly one of these before anything else is
scored.

| outcome | meaning |
|---|---|
| `tool_call` | structured `tool_calls` on the message — the only shape a caller can use |
| `prose_tool_syntax` | no `tool_calls`, but the content carries a serialised call |
| `prose_plain` | ordinary prose, no call in it |
| `empty` | neither content nor calls |
| `confirmation` | prose asking the user to confirm, on a task whose tool description says to |
| `error` | transport or HTTP failure |

`prose_tool_syntax` is separated from `prose_plain` because they are different
bugs. A serialised call means the model chose correctly and wrote the answer
into a field nobody reads — a parsing problem, and one a retry usually clears.
Plain prose means it did not choose a tool at all — a prompting problem, and
retrying it changes nothing. Reporting them as one number tells you a rate and
hides which fix applies.

`error` is excluded from every rate. A dropped tailnet connection is not a model
behaviour and should not be charged to one.

### Confirmation is not refusal

`ecosystem_app`'s description ends "Confirm with Erwin before stopping something
he may be using." A model that asks before restarting an app is following the
instruction it was given.

The first full run did not have this outcome, and scored `chat` at 68.5%
end-to-end — bottom of the table — because 58% of its calls on the two
destructive tasks were confirmations counted as refusals. Re-grading the same
responses with the outcome added put it at 86.6%. The benchmark was measuring
obedience and reporting it as incapability.

A response counts as a confirmation only on a task flagged `destructive`, and
only if it contains a question mark *and* a confirmation cue. "I can restart
moude." is not a confirmation and neither is a rhetorical question elsewhere in
the suite.

### Some tasks have more than one right answer

The belt carries genuine near-duplicates. `memory_save` writes a durable fact to
LeClanker's SQLite memory; `remember_fact` writes a durable fact to the
household brain. Nothing in "Remember this: selfkey starts locked" chooses
between them.

Scoring `memory_save` as the only correct answer put every model between 0% and
28% on that task — not because they failed, but because 52 of 72 calls picked
the other reasonable tool. A task may therefore list `accept_tools`. That is a
finding about the belt, not a concession: two tools that overlap this much and
give the caller no cue will be picked between roughly at random, and if that
matters to LeClanker the fix belongs in the tool descriptions.

### Detecting a serialised call

Six patterns, tried most-specific first: `<tools>…</tools>`,
`<tool_call>…</tool_call>`, the `<|tool_call|>` and `[TOOL_CALL]` special
tokens, `<function=name>`, a JSON object pairing `"name"` with
`"arguments"`/`"parameters"`, and a fenced JSON block naming a tool that is
actually on the belt.

Every pattern requires a *serialised call*, never a mention. "I'll use
`ecosystem_app` to restart it" is a plain refusal, and "the action field accepts
start, stop, restart or logs" is a model explaining a schema. Both must classify
as `prose_plain`; a false positive here inflates the headline rate directly, so
`tests/test_grade.py` pins that behaviour.

## Grading a tool task

Four checks, in order. Each is reported separately, because a model that picks
the right tool and fills it in wrongly needs different work than one that picks
the wrong tool.

1. **Emission** — did a structured call come back at all
2. **Tool choice** — is it the tool the task expects
3. **Schema validity** — required arguments present, no invented ones, enums
   respected, types right
4. **Task expectations** — the task's own constraints on argument values

End-to-end success requires all four. Only that number predicts what an agent
loop actually sees; the conditional rates are diagnostic.

Argument expectations use `equals` for closed sets (an app name, an enum member)
and `contains_any` for free text. Free text is never compared exactly. Grading a
model on phrasing a saved fact one specific way would measure wording, not tool
use.

An `app` argument outside the dashboard's registry is recorded separately as a
hallucinated app, whether or not the task expected that app. It is a distinct
failure: the call is well-formed, passes schema validation, and refers to
something that does not exist.

## Grading an abstention task

Three tasks need no tool. Correct means `prose_plain` — an answer, in words.

A structured call is wrong: a spurious `ecosystem_app` restarts an app nobody
asked about. A serialised call in prose is also wrong; the model still reached
for a tool. An empty response is wrong too — saying nothing is a failure, not
restraint.

These tasks exist because a benchmark made only of tool tasks rewards a model
that calls something every turn, and that model is dangerous in production.
`hardware_opinion` is deliberately baited: it asks about this machine's own
hardware, so it reads like a status question while being pure background
knowledge.

## The reasoning-budget sweep

Aimed at a second documented failure, from `docs/spark.md`:

> An agentic turn on `chat` used to return `(no final answer — only the model's
> reasoning)`. Measured with a real `ecosystem_status` payload: 4096 tokens came
> back `finish_reason="length"` with empty content; 8192 finished in 2901 with
> an answer.

This only appears on a **second** turn. The harness builds one: a user request,
an assistant message carrying an `ecosystem_status` tool call, a tool message
containing the full status of all nineteen apps (`fixtures.py`, ~330 tokens),
then an open question about it. `max_tokens` sweeps 512 → 8192.

Tools stay bound, as they are in production. A model may therefore answer in
prose, call another tool, or return nothing, and the report splits all three.
Folding "called another tool" into "answered" would hide exactly the failure
being measured.

## Sample sizes, and what they buy

The interval on a rate near 8%, at 95%:

| n | half-width | can it distinguish 8% from … |
|---:|---|---|
| 12 | ±17 pp | nothing |
| 100 | ±5.5 pp | 20% |
| 216 | ±3.7 pp | 15% |
| 700 | ±2.0 pp | 12% |

The shipped default is 18 repetitions × 12 tool tasks = **216 tool trials per
model**, which is what fits four aliases and both sweeps into an evening on one
GPU. That is a tenfold improvement on n=12 and still not enough to separate 8%
from 12%. Raise `--reps` if you need that; the cost is linear and
`lebenchmark plan` will tell you what it comes to.

Two-model differences are tested with a pooled two-proportion z. If two
intervals overlap, the run did not distinguish those models, however different
the point estimates look.

## What is recorded

`raw.jsonl` holds one line per call: the prompt, the response content (first
2000 characters), the length of any `message.reasoning`, the parsed call, every
sub-check, timings and token counts. The grade is derived, not primary —
`lebenchmark report` re-scores from the stored response rather than trusting the
verdict written at run time.

That is what makes `lebenchmark report` free to re-run. When the classifier
learns a new serialisation shape — and it will, because the next engine will
have its own — every past run can be re-scored against it without spending
another hour of GPU time.
