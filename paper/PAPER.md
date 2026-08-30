# Tool-calling reliability of locally-served models in a self-hosted agent ecosystem

**Erwin Lejeune** · Clanker Labs · 2026-08-30
Artefact: `Clanker-Labs/lebenchmark` · Raw data: `results/`

---

## Abstract

A self-hosted agent ecosystem moved its model calls from a cloud provider to a
DGX Spark serving four local aliases. Its documentation recorded that the Spark
"returns a tool call as prose in roughly 8% of calls", measured on twelve calls,
and its agent loops were written around that figure. We ran 1310 calls across
the four aliases on the ecosystem's own sixteen-tool belt.

The failure is real and the shape reproduces verbatim, but it is not a property
of the Spark. It is a property of the model: `coder` serialises calls into prose
at 6.0% [3.6, 10.0], while the fleet default `chat` does so at 0.5% [0.1, 2.6]
and the two smaller aliases never did in 431 calls. The two models also fail in
different *syntaxes*, so a parser written against one does not catch the other.

Two results were not being counted at all. First, grading exposed a measurement
artefact rather than a model defect: `chat` initially scored worst at 68.5%
end-to-end because it asked permission before restarting applications — which
its tool description explicitly instructs — and the grader counted obedience as
refusal. Re-scoring the same stored responses put it at 86.6%, level with
`coder`. Second, the reasoning-token budget is a hard cliff rather than a
gradient: on a second agent turn `chat` returns no answer at all in 100% of
calls at 1024 tokens, 46.7% at 2048, and 0% at 8192.

Practically: the smaller aliases are the most reliable tool callers here
(`fast` and `vision` at 94.9% end-to-end, against 86.6% for both large models),
`coder` is 17× faster per call than `chat` and the only alias that needs a
prose-call parser, and no budget below 4096 is safe for the thinking model.

## 1. Why this was measured

The chezmoi ecosystem runs an agent, LeClanker, that manages nineteen
self-hosted applications from a chat surface. Since 2026-08-20 its model calls
have been served locally by LeHarness on a DGX Spark rather than by a cloud
provider. Every application in the fleet reaches models through that one
gateway, so the gateway's reliability is the fleet's reliability.

One number governs how the agent loop is written. From `docs/AGENTS.md`:

> **The Spark returns a tool call as prose in roughly 8% of calls.** Measured:
> 11/12, then 1 failure printing `<tools>{"name": ...}</tools>` with
> `finish_reason: stop`. It is not a broken gateway and not a wrong model — it
> is a rate. Anything calling tools in a loop must expect it and retry rather
> than treat one bad response as a configuration problem.

The interpretation is correct and it changed the code: loops retry instead of
erroring. The evidence is one failure in twelve calls. Its Wilson 95% interval
runs from **1.5% to 35.4%**.

That range spans three different engineering decisions. At 1.5%, a single retry
is enough and nobody notices. At 8%, a two-step task fails roughly one time in
six and retries are load-bearing. At 35%, the model is not usable for tool
calling and the fleet should be pointed back at a cloud provider. The
observation cannot distinguish between them, and no amount of re-reading it
will.

This paper reports what a properly powered measurement says instead. It also
reports three things that were not being counted at all, each of which turned
out to matter more than expected.

## 2. System under test

| | |
|---|---|
| Host | NVIDIA DGX Spark (GB10, aarch64, 121.7 GiB unified memory) |
| Serving | LeHarness 0.32.14, engine **Ollama**, preset `qwen3-next-80b` |
| Interface | OpenAI `/v1/chat/completions`, no authentication (tailnet-gated) |
| Client | Intel NUC on the same tailnet, one hop away |

Four aliases are served, and the aliases rather than the model names are the
stable interface — the preset has already been changed once, from vLLM serving
DeepSeek-R1 to the current Ollama build, and applications were not reconfigured.

| alias | model | note |
|---|---|---|
| `chat` | Qwen3-Next-80B-A3B-Instruct | thinking model; the fleet default |
| `coder` | Qwen3-Coder-30B-A3B | no thinking |
| `vision` | Qwen3-VL-8B | accepts images; benchmarked on text only |
| `fast` | Qwen3-8B | small always-on lane |

### 2.1 The tool belt

Sixteen tools, transcribed by hand from LeClanker's `src/leclanker/tools/*.py`:
`ecosystem_status`, `ecosystem_app`, `chat_search`, `chat_list`, `chat_read`,
`memory_search`, `memory_save`, `memory_forget`, `home_states`, `home_control`,
`home_history`, `network_status`, `search_brain`, `capture_note`,
`remember_fact`, `brain_today`.

Using the real belt rather than a small clean one is a deliberate cost. Belt
size and inter-tool similarity both drive tool-selection error, and three of
these sixteen — `memory_save`, `remember_fact`, `capture_note` — all write text
somewhere. Distinguishing them is part of the job the agent actually does, and a
benchmark on three well-separated toy functions cannot observe it.

No tool executes. Calls are validated against the schema and discarded.

## 3. Method

Fifteen tasks: twelve requiring a tool, three requiring none. Each carries four
paraphrases. Full definitions are in `tasks/`, and `docs/methodology.md` gives
the grading rules.

### 3.1 Two conditions that had to be measured before the design could be fixed

**Temperature.** Repetitions are only samples if the system is stochastic. At
`temperature=0` this gateway is not: the same prompt sent eight times to `fast`
and eight times to `chat` returned byte-identical tool calls, 8/8 in both cases.
Fifty repetitions at temperature 0 would have measured one prompt fifty times
while producing a table that looked like a sample of fifty. All results below
use LeClanker's production `temperature=0.3` and `max_tokens=8192`.

**Concurrency.** Generation throughput on this engine does not respond to
concurrency, while latency degrades roughly linearly with it:

| alias | gen tok/s @1 | gen tok/s @4 | mean latency @1 → @4 |
|---|---:|---:|---|
| `fast` | 38.8 | 39.9 | 8.2 s → 24.0 s |
| `coder` | 62.7 | 74.3 | 1.5 s → 4.2 s |
| `chat` | 58.0 | 58.3 | 21.2 s → 52.0 s |
| `vision` | 37.7 | 40.7 | 9.8 s → 27.0 s |

Ollama serialises requests. Four workers complete the same work in the same
wall-clock while each request waits behind three others, so a run at concurrency
4 costs nothing extra and returns latency figures inflated by queueing that
bought nothing. **All results below were collected at concurrency 1.**

A practical corollary: run duration is completion tokens divided by generation
rate, not call count. `chat` emits 1228 completion tokens to produce a one-line
tool call, against `coder`'s 96 — it generates 1.5× faster per token and is 14×
slower per call.

### 3.2 Five outcomes

Each response is classified before it is scored:

| outcome | meaning |
|---|---|
| `tool_call` | structured `tool_calls` — the only shape a caller can consume |
| `prose_tool_syntax` | no `tool_calls`, but the content carries a serialised call |
| `prose_plain` | ordinary prose with no call in it |
| `empty` | neither content nor calls |
| `error` | transport failure; excluded from all rates |

The separation of `prose_tool_syntax` from `prose_plain` is the point of the
instrument. They present identically to a naive `if response.tool_calls:` check
and are different bugs: a serialised call means the model chose correctly and
wrote its answer into a field nobody reads, which a retry usually clears; plain
prose means it did not choose a tool, which retrying does not fix.

Detection requires a serialised call and never a mention — "I'll use
`ecosystem_app` to restart it" must classify as `prose_plain`. A false positive
here inflates the headline rate directly, so the negative cases are unit-tested.

### 3.3 Scoring

Tool tasks are scored in four layers — emission, tool choice, schema validity,
task-specific argument expectations — and end-to-end success requires all four.
Closed sets (tool names, enum members, app names) are compared exactly; free
text by substring, because grading a model on how it phrases a saved fact
measures wording rather than tool use.

Abstention tasks are correct only on `prose_plain`. A structured call is wrong
because a spurious `ecosystem_app` restarts an application nobody asked about; a
serialised call is wrong for the same reason; an empty response is wrong because
saying nothing is a failure rather than restraint.

Rates are reported as Wilson 95% intervals throughout. Model pairs are compared
with a pooled two-proportion z test.

### 3.4 Reasoning-budget sweep

A second documented failure, from `docs/spark.md`, is that an agentic turn on
`chat` once returned no final answer — the reasoning consumed the token budget.
Reproducing it requires a second turn, so the harness constructs one: a user
request, an assistant message carrying an `ecosystem_status` call, a tool message
containing the real status of all nineteen applications, then an open question
about it. `max_tokens` sweeps 512 → 8192 with tools still bound, as they are in
production, so a model may answer, call another tool, or return nothing.

## 4. Results

1310 calls, 275.6 minutes, one transport error. 216 tool trials and 54
abstention trials per alias. All intervals are Wilson 95%.

### 4.1 The documented failure is real, reproduces exactly, and is model-specific

| alias | n | structured call | **prose call** | asked to confirm | empty |
|---|---:|---|---|---|---|
| `chat` | 216 | 89.8% [85.1, 93.2] | **0.5% [0.1, 2.6]** | 58.3% | 0.0% |
| `coder` | 216 | 94.0% [90.0, 96.4] | **6.0% [3.6, 10.0]** | 0.0% | 0.0% |
| `fast` | 215 | 100.0% [98.2, 100.0] | **0.0% [0.0, 1.8]** | 0.0% | 0.0% |
| `vision` | 216 | 100.0% [98.3, 100.0] | **0.0% [0.0, 1.7]** | 0.0% | 0.0% |

The single `chat` failure is the documented shape, character for character:

```
<tools>
{"name": "network_status", "arguments": {}}
</tools>
```

with `finish_reason: stop`. The original observation was not a misreading.

But `chat` is the fleet default, and at 0.5% [0.1, 2.6] its rate is
inconsistent with the 8% the loops are written around. The 8% belongs to
`coder`, whose thirteen failures take an entirely different form — Qwen's
XML-ish function syntax, with a stray closing tag from a template that never
opened:

```
I'll stop the ai212 app for you. Let me do that right away.

<function=ecosystem_app>
<parameter=app>
ai212
</parameter>
<parameter=action>
stop
</parameter>
</function>
</tool_call>
```

This matters more than the rate. A retry policy is cheap, but a *parser* written
against `<tools>{...}</tools>` recovers nothing from the `coder` form, and one
written against `<function=...>` recovers nothing from `chat`'s. The failure is
not "the Spark sometimes emits prose"; it is "each model emits prose in its own
syntax", and the recovery path has to be per-model or shape-agnostic.

The `coder` failures are also concentrated rather than uniform. All thirteen
fall on three of the twelve tool tasks — `stop_colloquial` (6), `home_control`
(5), `chat_search` (2) — and none on the other nine. The rate is a property of
the request, not a constant background, so an aggregate figure understates the
risk on the requests that actually trigger it and overstates it everywhere
else.

### 4.2 A safety instruction read as a benchmark failure

The first grading of this run put `chat` last at **68.5%** end-to-end. It was an
artefact of the grader.

`ecosystem_app`'s description ends: *"Confirm with Erwin before stopping
something he may be using."* On the two destructive tasks, `chat` frequently
answered:

> I need to confirm with you before restarting moude. Are you sure you want to
> proceed? This will stop the app temporarily.

That is the tool description being obeyed. The grader saw no `tool_calls` and
recorded a refusal. Adding a `confirmation` outcome and re-scoring the same
stored responses — no further GPU time — gives:

| alias | end-to-end, first grading | end-to-end, corrected |
|---|---|---|
| `chat` | 68.5% [62.0, 74.3] | **86.6% [81.4, 90.5]** |
| `coder` | 80.1% [74.3, 84.9] | **86.6% [81.4, 90.5]** |
| `fast` | 90.7% [86.1, 93.9] | **94.9% [91.1, 97.1]** |
| `vision` | 89.8% [85.1, 93.2] | **94.9% [91.1, 97.1]** |

`chat` asked to confirm on 58.3% of its destructive-task calls; no other alias
did so once. The ranking inverted for the model that behaved most carefully.

We report this prominently because the artefact is not specific to this
benchmark. Any tool-use evaluation scoring "did it emit a call" penalises
exactly the caution that safety-oriented tool descriptions are written to
produce, and the penalty is invisible unless someone reads the prose.

A second, smaller grading error worked the same way. Scoring `memory_save` as
the only correct answer to "Remember this: …" put every alias between 0% and
28%. Of 72 calls, 52 chose `remember_fact` — a different tool that also saves a
durable fact, to a different store, with nothing in either description telling a
user's phrasing which to use. That is a finding about the belt: two tools this
close, with no disambiguating cue, get chosen between near-randomly, and the fix
belongs in LeClanker's tool descriptions rather than in the benchmark.

### 4.3 Corrected tool-use accuracy

| alias | tool choice | args schema-valid | args match task | **end to end** |
|---|---|---|---|---|
| `chat` | 85.6% [79.9, 89.8] | 100.0% | 100.0% | **86.6% [81.4, 90.5]** |
| `coder` | 92.1% [87.6, 95.1] | 100.0% | 100.0% | **86.6% [81.4, 90.5]** |
| `fast` | 95.8% [92.2, 97.8] | 99.0% | 100.0% | **94.9% [91.1, 97.1]** |
| `vision` | 94.9% [91.1, 97.1] | 100.0% | 100.0% | **94.9% [91.1, 97.1]** |

Two things stand out.

**Argument construction is solved; selection is not.** Once a model emits a
structured call to the right tool, the arguments are schema-valid essentially
always: 2 failures in 764 calls, with no invented enum members and no missing
required fields. Every point of loss is in *choosing* the tool. Effort
spent on argument-repair logic in an agent loop is effort spent on the part that
already works.

**The small models win.** `fast` (8B) and `vision` (8B) are indistinguishable
from each other (p=0.99) and both beat the 80B `chat` and 30B `coder`
(p≤0.003), which are indistinguishable from each other (p=1.00). On a belt of
sixteen tools with clear descriptions, parameter count did not buy tool-calling
accuracy on this suite.

The residual failures are concentrated. `home_control` — "turn off the living
room lights" — is 22% for both large models and 94% for both small ones; the
large models call `home_states` first, which is a defensible opening move in a
multi-turn plan and indistinguishable from a wrong answer in a single turn (see
§6). `app_logs` costs every alias something, with `ecosystem_status` the common
substitute.

### 4.4 Knowing when not to call a tool

| alias | n | abstained correctly |
|---|---:|---|
| `chat` | 54 | 100.0% [93.4, 100.0] |
| `coder` | 54 | 100.0% [93.4, 100.0] |
| `fast` | 54 | 98.1% [90.2, 99.7] |
| `vision` | 54 | 66.7% [53.4, 77.8] |

No alias over-called: of the nineteen abstention failures in the run, not one
was a spurious tool call. All
eighteen `vision` failures were **empty responses** — `finish_reason: length`,
a mean of 3658 completion tokens, and no content. On open-ended knowledge
questions, an 8B vision model spent its entire 8192-token budget and returned
nothing usable, 26% of the time.

This is the same failure as §4.5 on a model nobody was watching for it.

### 4.5 The reasoning budget is a cliff, not a gradient

Second agent turn, full `ecosystem_status` payload in context, open question
about it, tools still bound.

**`chat` (thinking model)**

| max_tokens | n | answered | finish=length | mean completion tokens | **returned nothing** |
|---:|---:|---|---|---:|---|
| 512 | 15 | 0.0% | 100.0% | 512 | **100.0% [79.6, 100.0]** |
| 1024 | 15 | 0.0% | 100.0% | 1024 | **100.0% [79.6, 100.0]** |
| 2048 | 15 | 53.3% | 86.7% | 2007 | **46.7% [24.8, 69.9]** |
| 4096 | 15 | 93.3% | 6.7% | 2758 | **6.7% [1.2, 29.8]** |
| 8192 | 15 | 100.0% | 0.0% | 2741 | **0.0% [0.0, 20.4]** |

**`coder` (no thinking)** returned nothing in 0/75 calls at every budget, and
its mean completion length never exceeded 230 tokens.

Three observations.

The transition is abrupt. Between 1024 and 4096 the failure rate goes from
certain to negligible; there is no budget at which the model returns a shorter
answer instead of no answer. Below the threshold the mode of failure is total.

Mean completion tokens plateau at ~2750 once the budget stops binding, so the
task needs roughly 2.7k tokens. The documented setting of 8192 is not
over-provisioned; it is about 3× the requirement, which is the right margin for
a distribution whose failures are all-or-nothing.

`chat` never called a second tool at any budget, while `coder` did on 73–87% of
calls. The thinking model treats a returned tool result as material to answer
from; the non-thinking one treats it as a step in a chain. That is a difference
in loop behaviour, not in accuracy, and it changes how many turns a task costs.

### 4.6 Speed

| alias | mean | p50 | p95 | gen tok/s | TTFT p50 | TTFT p95 |
|---|---|---|---|---:|---|---|
| `chat` | 20.83 s | 12.39 s | 69.70 s | 36.4 | 23.47 s | 50.06 s |
| `coder` | **1.20 s** | 0.71 s | 3.64 s | 52.2 | **0.70 s** | 1.63 s |
| `fast` | 7.66 s | 5.72 s | 17.63 s | 34.1 | 7.56 s | 9.58 s |
| `vision` | 17.19 s | 8.23 s | 40.21 s | 34.3 | 10.00 s | 19.19 s |

`coder` is 17× faster per call than `chat` at the same end-to-end accuracy
(86.6% both, p=1.00). The gap is not generation speed — 52 against 36 tok/s —
but output length: `chat` spends about 1200 tokens producing a one-line tool
call, and thinking is billed in wall-clock.

`chat`'s p95 of 69.7 s against a p50 of 12.4 s is the number that matters for a
chat surface. The tail is not a slow response; it is a response that has not
arrived.

## 5. What we changed, and what we would tell someone else

### For this ecosystem

**The 8% figure belongs on `coder`, not on the Spark.** `docs/AGENTS.md` should
say so. Loops that default to `chat` are retrying against a 0.5% rate; loops
that use `coder` — which the docs at one point recommended as a workaround for
the reasoning-budget problem — face 6%.

**A prose-call parser must be shape-agnostic.** The two models fail in different
syntaxes and a parser written from either sample recovers nothing from the
other. Recovering the call is worth more than retrying it: in all thirteen
`coder` cases the tool and arguments were correct and only the envelope was
wrong.

**Keep `LLM_MAX_TOKENS` at 8192 and treat 4096 as the floor.** The failure below
that is total, not degraded.

**`vision` needs a budget guard for open-ended prose,** or should not serve it.
It fails the same way `chat` does, and nothing in the docs warns about it
because nobody was looking at an 8B vision model for that.

**Two memory tools with no disambiguating cue is a bug in the belt.** Models
split 52/12 between `remember_fact` and `memory_save` on a plain "remember
this". Either the descriptions should say which store a user means, or one tool
should route.

**Consider `coder` as the default for tool dispatch.** Same accuracy as `chat`,
17× faster, with a known and recoverable failure envelope. `chat` earns its cost
on the second turn, where it answers from a tool result rather than chaining
another call.

### For anyone building a tool-use benchmark

**Check determinism before choosing a sample size.** At temperature 0 this
gateway is byte-identical across repetitions. Fifty trials would have produced a
table indistinguishable from a real sample of fifty and a confidence interval
that was pure fiction.

**Read the prose your grader is discarding.** The largest single error in this
study was a grader that turned obedience into failure, and it was only visible
by reading responses that had been scored 0. It cost 18 percentage points on one
model and inverted the ranking.

**Store responses, not verdicts.** Every correction here — the confirmation
outcome, the accepted-alternative tool — was applied to the completed run for
free. Only the un-stored `message.reasoning` field could not be recovered, and
that is the one thing needing another 4.6 hours.

**A rate without an interval is not a result.** The measurement that prompted
this work was correct. It was reported as "roughly 8%" when the evidence
supported "somewhere between 1.5% and 35%", and a policy was built on the point
estimate.

## 6. Threats to validity

**Single-turn.** Except for the budget sweep, every measurement is one turn.
Agent loops run many, and errors compound: a model at 95% single-turn success is
nearer 0.95⁵ over five steps. These numbers bound a loop's failure rate from
below and do not estimate it.

**Conditional on this belt.** Reliability depends on how many tools are bound
and how confusable they are. Every figure here is conditional on these sixteen.
The belt is also a hand transcription; if LeClanker's tools change and
`toolbelt.py` does not, the benchmark silently measures an ecosystem that no
longer exists.

**Conditional on this engine and preset.** The alias `chat` has meant two
different models on two different presets. `manifest.json` records what was
serving; comparing runs without reading it compares two machines.

**Network-inclusive latency.** The client is on the NUC, one tailnet hop from
the Spark. Latency and TTFT include that hop. It is the right number for this
fleet, where every caller is remote, and the wrong number to quote as the
model's own.

**Trials are not fully independent.** Repetitions within a task share one of
four prompts, so true intervals are slightly wider than the Wilson intervals
printed.

**Multiplicity.** Four models give six pairwise comparisons, unadjusted. At
α=0.05, roughly one run in three will show a spurious significant pair; a single
p just under 0.05 is suggestive, not conclusive.

**Abstention is undersampled.** Three tasks, all knowledge or writing questions.
The case most likely to cause an unwanted restart in practice — a hypothetical,
"if moude were down, what would you do?" — is not tested.

**Unmeasured load.** The Spark serves the whole fleet. A run competing with real
traffic reports worse latency and nothing here detects that it happened.

## 7. Reproducing this

```bash
git clone https://github.com/Clanker-Labs/lebenchmark.git
cd lebenchmark && make setup
uv run lebenchmark calibrate            # size the run on your own hardware
uv run lebenchmark run --models chat,coder,fast,vision --reps 18 --concurrency 1
```

Raw per-call records are in `results/<run-id>/raw.jsonl`, one JSON object per
call including the response content, so `lebenchmark report` can re-score any
past run without further GPU time — which matters, because the next engine will
serialise calls in some shape the classifier has not seen yet.
