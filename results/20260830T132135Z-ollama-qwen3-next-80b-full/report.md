# lebenchmark run

- **Endpoint** `http://spark.example-tailnet.ts.net:8000/v1`
- **Engine** ollama, preset `qwen3-next-80b`
- **Started** 2026-08-30T13:21:35.681906+00:00
- **Settings** temperature 0.3, max_tokens 8192, 16 tools on the belt
- **Calls** 1310 (1 transport errors, excluded from rates)

## Tool-call emission

Of calls on tasks that require a tool. `prose call` is the documented failure: a serialised call arriving as ordinary content. `asked to confirm` is of the two destructive tasks only, whose tool description instructs the agent to confirm before acting — it is compliance, and is scored as success.

| model | n | structured call | prose call | plain refusal | asked to confirm | empty |
|---|---:|---|---|---|---|---|
| `chat` | 216 | 89.8% [85.1, 93.2] | 0.5% [0.1, 2.6] | 0.0% | 58.3% | 0.0% |
| `coder` | 216 | 94.0% [90.0, 96.4] | 6.0% [3.6, 10.0] | 0.0% | 0.0% | 0.0% |
| `fast` | 215 | 100.0% [98.2, 100.0] | 0.0% [0.0, 1.8] | 0.0% | 0.0% | 0.0% |
| `vision` | 216 | 100.0% [98.3, 100.0] | 0.0% [0.0, 1.7] | 0.0% | 0.0% | 0.0% |

## Tool-use correctness

`tool choice` and the argument columns are conditional on a structured call having been emitted; `end to end` is not, so it is the number that predicts what an agent loop sees.

| model | tool choice | args valid | args match task | end to end |
|---|---|---|---|---|
| `chat` | 85.6% [79.9, 89.8] | 100.0% | 100.0% | **86.6%** [81.4, 90.5] |
| `coder` | 92.1% [87.6, 95.1] | 100.0% | 100.0% | **86.6%** [81.4, 90.5] |
| `fast` | 95.8% [92.2, 97.8] | 99.0% | 100.0% | **94.9%** [91.1, 97.1] |
| `vision` | 94.9% [91.1, 97.1] | 100.0% | 100.0% | **94.9%** [91.1, 97.1] |

## Knowing when not to call a tool

Three tasks need no tool. Correct means answering in plain prose.

| model | n | abstained correctly |
|---|---:|---|
| `chat` | 54 | 100.0% [93.4, 100.0] |
| `coder` | 54 | 100.0% [93.4, 100.0] |
| `fast` | 54 | 98.1% [90.2, 99.7] |
| `vision` | 54 | 66.7% [53.4, 77.8] |

## What the failures looked like

- `chat` prose-serialisation shapes: `tools_tag` ×1
- `coder` prose-serialisation shapes: `function_tag` ×13

## Speed

Totals are from the non-streaming suite under the run's concurrency, so they include queueing. TTFT is from the streaming suite.

| model | mean | p50 | p95 | completion tok/s | TTFT p50 | TTFT p95 |
|---|---|---|---|---:|---|---|
| `chat` | 20.83s | 12.39s | 69.70s | 36.4 | 23.47s | 50.06s |
| `coder` | 1.20s | 0.71s | 3.64s | 52.2 | 0.70s | 1.63s |
| `fast` | 7.66s | 5.72s | 17.63s | 34.1 | 7.56s | 9.58s |
| `vision` | 17.19s | 8.23s | 40.21s | 34.3 | 10.00s | 19.19s |

## Reasoning budget

A second agent turn carrying a full `ecosystem_status` result, then an open question about it. Tools stay bound, as they are in production, so a model may answer, call another tool, or return nothing. The last column is the documented failure: the budget went on reasoning and no content came back.

**`chat`**

| max_tokens | n | answered in prose | called another tool | finish=length | mean completion tokens | **returned nothing** |
|---:|---:|---|---|---|---:|---|
| 512 | 15 | 0.0% [0.0, 20.4] | 0.0% | 100.0% | 512 | **100.0%** [79.6, 100.0] |
| 1024 | 15 | 0.0% [0.0, 20.4] | 0.0% | 100.0% | 1024 | **100.0%** [79.6, 100.0] |
| 2048 | 15 | 53.3% [30.1, 75.2] | 0.0% | 86.7% | 2007 | **46.7%** [24.8, 69.9] |
| 4096 | 15 | 93.3% [70.2, 98.8] | 0.0% | 6.7% | 2758 | **6.7%** [1.2, 29.8] |
| 8192 | 15 | 100.0% [79.6, 100.0] | 0.0% | 0.0% | 2741 | **0.0%** [0.0, 20.4] |

**`coder`**

| max_tokens | n | answered in prose | called another tool | finish=length | mean completion tokens | **returned nothing** |
|---:|---:|---|---|---|---:|---|
| 512 | 15 | 80.0% [54.8, 93.0] | 73.3% | 6.7% | 230 | **0.0%** [0.0, 20.4] |
| 1024 | 15 | 46.7% [24.8, 69.9] | 86.7% | 0.0% | 169 | **0.0%** [0.0, 20.4] |
| 2048 | 15 | 53.3% [30.1, 75.2] | 86.7% | 0.0% | 182 | **0.0%** [0.0, 20.4] |
| 4096 | 15 | 53.3% [30.1, 75.2] | 80.0% | 0.0% | 181 | **0.0%** [0.0, 20.4] |
| 8192 | 15 | 66.7% [41.7, 84.8] | 80.0% | 0.0% | 207 | **0.0%** [0.0, 20.4] |

## Per task

End-to-end success. A task where every model fails is usually a task problem; a task where they split is a model difference.

| task | `chat` | `coder` | `fast` | `vision` |
|---|---|---|---|---|
| `app_logs` | 78% | 78% | 72% | 100% |
| `brain_today` | 100% | 100% | 100% | 100% |
| `capture_note` | 100% | 100% | 100% | 100% |
| `chat_list` | 100% | 100% | 100% | 100% |
| `chat_search` | 100% | 83% | 100% | 100% |
| `explain_homelab` | 100% | 100% | 100% | 100% |
| `explain_idempotent` | 100% | 100% | 100% | 78% |
| `fleet_status` | 100% | 100% | 100% | 100% |
| `hardware_opinion` | 100% | 100% | 94% | 22% |
| `home_control` | 22% | 22% | 100% | 94% |
| `memory_save` | 100% | 100% | 78% | 78% |
| `memory_search` | 83% | 100% | 89% | 67% |
| `network_status` | 94% | 100% | 100% | 100% |
| `restart_app` | 89% | 100% | 100% | 100% |
| `stop_colloquial` | 72% | 56% | 100% | 100% |

## Are the differences real?

Pooled two-proportion z on end-to-end success. p below 0.05 means the run distinguished them.

| A | B | A | B | p |
|---|---|---|---|---|
| `chat` | `coder` | 86.6% | 86.6% | 1.0000 |
| `chat` | `fast` | 86.6% | 94.9% | 0.0030 |
| `chat` | `vision` | 86.6% | 94.9% | 0.0028 |
| `coder` | `fast` | 86.6% | 94.9% | 0.0030 |
| `coder` | `vision` | 86.6% | 94.9% | 0.0028 |
| `fast` | `vision` | 94.9% | 94.9% | 0.9911 |
