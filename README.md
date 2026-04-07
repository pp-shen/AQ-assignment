# ToolsmithBench

ToolsmithBench is a benchmark harness that measures whether an LLM agent can
*author tools* — not just call them. Each task hands the agent a broken or
incomplete validation tool in an STL geometry domain, asks it to investigate,
fix or replace the tool, and store the result in a persistent on-disk tool
library. Later episodes in the same sequence test whether the agent reuses
its earlier work or re-derives it from scratch. The harness records every
action to a JSONL trace, scores each run against an oracle ground truth, and
reports per-model results plus a side-by-side model comparison.

## Installation

ToolsmithBench has no third-party Python dependencies — only the standard
library. Python 3.11 or newer is required.

```bash
git clone <repo-url>
cd AQ-assignment
export OPENROUTER_API_KEY=sk-or-...
```

The agent talks to OpenRouter via `urllib.request`, so any model accessible
through your OpenRouter account works.

## Running the benchmark

```bash
python run_benchmark.py            # full sweep: 6 episodes × all models
python run_benchmark.py --test     # quick sanity check: ep4/5/6, first model only
```

After a full run, results land in:

```
results/
  claude-sonnet-4.6/        per-model reports
    summary.csv
    report.md
    reuse_analysis.md
  gpt-5.4/
    ...
  model_comparison.md       cross-model side-by-side
logs/                       JSONL traces, one per episode run
tools/<model>/<tool_id>/    persistent tool library, scoped per model
```

## Adding a new task

1. **Create fixtures** under `toolsmithbench/fixtures/<your_dir>/`. STL files
   only — ASCII or binary, the runner copies whatever's there into the
   agent's working directory.
2. **Create an oracle** at `toolsmithbench/oracle/<your_task>_ground_truth.json`
   with `fixtures_dir`, `verification_mode` (`"tool"` or `"report"`), and a
   `cases` list. See `stl_ground_truth.json` for the schema.
3. **Create a task module** at `toolsmithbench/tasks/<your_task>.py` that
   builds a `TaskSpec` and calls `register_task(spec)`. Set
   `fixtures_dir="toolsmithbench/fixtures/<your_dir>"` so the runner seeds
   the working directory.
4. **Import the module** in `run_benchmark.py` so registration runs at
   import time, then add it to `_ALL_EPISODES`.

No verifier changes are needed — the existing `STLVerifier` handles both
`tool` and `report` verification modes via the oracle config.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full breakdown. The short
version:

- **`TaskSpec`** — frozen description of a task, registered at import time.
- **`Runner`** — agent execution loop. Feeds observations to the agent,
  routes actions through `STLEnvironment` and `ToolLibrary`, writes a JSONL
  trace.
- **`STLEnvironment`** — sandboxed working directory with four primitive
  actions (`read_file`, `write_file`, `run_python`, `list_files`).
- **`ToolLibrary`** — persistent on-disk store of agent-authored tools,
  keyed by `tool_id`, scoped per model so each model's tools stay isolated.
- **`STLVerifier`** — post-run scorer. Two modes: `tool` (run the agent's
  registered tool against each oracle fixture) and `report` (read the
  validation report the agent wrote into the working directory). Never
  imported by the runner.
- **`ClaudeAgent`** — OpenRouter HTTP client over `urllib.request`. No
  third-party SDKs. Maintains conversation history within an episode.

## Example results

A representative run across both models and all six tasks:

| Model | Task | Passed | Score | Steps | Authored | Reused | Reuse Gain |
|---|---|:---:|---:|---:|:---:|:---:|---:|
| claude-sonnet-4.6 | stl_ep1_broken_validator | ✓ | 1.00 | 20 | ✓ | ✓ | — |
| claude-sonnet-4.6 | stl_ep2_batch_processing | ✓ | 1.00 | 8 | ✓ | ✓ | 60.0% |
| claude-sonnet-4.6 | stl_ep3_binary_variant | ✗ | 0.00 | 3 | ✗ | ✓ | 85.0% |
| claude-sonnet-4.6 | stl_ep4_unit_conversion | ✓ | 1.00 | 10 | ✓ | ✓ | 50.0% |
| claude-sonnet-4.6 | stl_ep5_large_batch | ✓ | 1.00 | 7 | ✓ | ✓ | 65.0% |
| claude-sonnet-4.6 | stl_ep6_repair | ✗ | 0.00 | 8 | ✓ | ✓ | 60.0% |
| **claude-sonnet-4.6 total** | **6 tasks** | **67%** | **0.67** | | | | |
| gpt-5.4 | stl_ep1_broken_validator | ✗ | 0.00 | 8 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep2_batch_processing | ✓ | 1.00 | 5 | ✓ | ✓ | 37.5% |
| gpt-5.4 | stl_ep3_binary_variant | ✓ | 1.00 | 10 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep4_unit_conversion | ✓ | 1.00 | 8 | ✓ | ✓ | — |
| gpt-5.4 | stl_ep5_large_batch | ✓ | 1.00 | 4 | ✓ | ✓ | 50.0% |
| gpt-5.4 | stl_ep6_repair | ✓ | 1.00 | 16 | ✓ | ✓ | — |
| **gpt-5.4 total** | **6 tasks** | **83%** | **0.83** | | | | |

The numbers above are illustrative — your run will differ. The signal to
watch is the **Reuse Gain** column: episodes 2, 3, and 5 are designed so a
tool authored in ep1 / ep2 directly applies, and the step count should drop
when the agent finds it via `tool_library_search`.
