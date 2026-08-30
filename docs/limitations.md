# Limitations

What these numbers do not license you to say.

## It measures one turn, not an agent

Except for the budget sweep, every call is a single turn: system prompt, user
request, sixteen tools, one response. Real agent loops run many turns, feed tool
results back, and accumulate context — and errors there compound in ways one
turn cannot show. A model with 95% single-turn success is not a model with 95%
success on a five-step task; it is closer to 0.95⁵.

Treat the numbers as a floor on the failure rate of a loop, never as its rate.

## It measures the belt it was given

Reliability depends on the belt. Sixteen tools with three near-duplicates is
harder than five well-separated ones, and every number here is conditional on
that specific set. Adding a seventeenth tool changes the results and the run has
to be repeated.

The belt is a transcription of LeClanker's, made by hand. If LeClanker's tools
change and `toolbelt.py` does not, the benchmark is silently measuring an
ecosystem that no longer exists. There is no automated check for this and there
should be.

## It measures the engine and preset that were running

The Spark's engine is not a constant. The alias `chat` has meant a vLLM
DeepSeek-R1 and an Ollama Qwen3-Next-80B, and it will mean something else again.
`manifest.json` records what was serving, and comparing two runs without reading
both manifests is comparing two different machines.

Ollama's own version matters too and is not currently captured beyond the
gateway's reported version string.

## The client is on a different machine

Calls cross the tailnet from the NUC to the Spark. Every latency figure includes
that hop. It is small relative to generation — a few milliseconds against
seconds — but the TTFT numbers in particular are network-inclusive, and a client
on the Spark itself would report lower ones.

This is the right choice for the fleet, where every caller is remote. It is the
wrong number to quote as the model's own latency.

## Grading is exact-match on a small closed set

Tool choice, enums and app names are graded exactly, which is right: they are
closed sets and the dashboard rejects anything else.

Free text is graded by substring, which is cruder. `memory_save` is scored on
whether the saved fact mentions `selfkey` or `lock`, not on whether the fact is
well written. A model that saves "selfkey: lock" scores the same as one that
saves a sentence a human could use later. That difference is real and this
benchmark cannot see it.

Only the first tool call is graded when several are emitted. Multi-call
responses are counted and noted but the second call onward is not scored.

## Abstention is three tasks

Three of fifteen. That is enough to catch a model that calls a tool on every
turn and not enough to characterise its abstention behaviour. The interval on
the abstention rate is correspondingly wide, and it is reported.

The three are also all knowledge or writing questions. A model could abstain
correctly on those and still call a tool on, say, a hypothetical — "if moude
were down, what would you do?" — which is the case most likely to cause an
unwanted restart in practice, and is not tested.

## The default sample size cannot separate close models

18 repetitions × 12 tool tasks = 216 tool trials per model, or ±3.7 percentage
points at 95% near a rate of 8%. That distinguishes 8% from 20%. It does not
distinguish 8% from 12%.

Two models whose intervals overlap were **not** distinguished by the run,
however different their point estimates look. The report prints a two-proportion
p for exactly this reason. Raise `--reps` if you need to separate close models;
the cost is linear.

## The first run did not record reasoning

Some models return chain-of-thought in `message.reasoning`, separate from
`content`. The client discarded that field for the whole of the
`20260830T132135Z` run, so its 56 empty responses can be shown to coincide with
`finish_reason: length` and a large completion-token count — consistent with
reasoning exhausting the budget — but cannot be shown directly to contain
reasoning. The field is captured from `v0.1.0` onward; re-running would settle
it, and re-grading cannot, because the data was never stored.

## Statistical caveats

Trials within a task share a prompt and are not fully independent, so the true
intervals are slightly wider than the Wilson intervals printed. The pairwise
model comparisons are unadjusted for multiplicity: with four models there are
six comparisons, and at α=0.05 roughly one in three runs will show a spurious
"significant" pair somewhere. Treat a single p just under 0.05 as suggestive.

## What it does not measure at all

- **Quality of the eventual answer.** Only whether the call was well-formed.
- **Multi-turn recovery.** Whether a model that fails once succeeds on retry —
  which is the number a retry policy actually needs.
- **Vision.** `vision` is benchmarked on text tool use only. No images are sent.
- **Two-step plans.** `home_control` is scored on acting. `chat` and `coder`
  mostly called `home_states` first, which is a reasonable opening move in a
  multi-turn plan and is indistinguishable here from picking the wrong tool.
- **Cost.** No pricing model, and the local models have no per-token price.
- **Long context.** The largest prompt here is roughly 1.7k tokens. Behaviour at
  30k is not sampled, and it is where agent loops usually live by turn ten.
- **Concurrent load from other apps.** The Spark serves the whole fleet. A run
  competing with real traffic will report worse latency, and nothing here
  detects that it happened.
