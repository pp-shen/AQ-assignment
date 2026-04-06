# ToolsmithBench Architecture

## What this is
A benchmark harness that evaluates whether LLM agents can author tools
to fix broken or incomplete interfaces, rather than just use fixed tools.
The domain is CAD geometry processing, specifically STL file validation
and repair — a realistic engineering context where silent tool failures
have real downstream consequences.

## Core thesis
Most benchmarks measure tool use. This measures tool authoring —
can an agent detect a broken interface, write a fix, store it,
and reuse it later across related tasks?

## Project structure
```
toolsmithbench/
  task.py                   # TaskSpec dataclass and task registry
  tool_library.py           # Persistent tool storage and retrieval
  runner.py                 # Main agent execution loop
  verifier.py               # Scoring and verification (separate from runner)
  reporting.py              # Aggregates results into summary outputs
  envs/
    base_env.py             # Base environment interface
    stl_env.py              # STL file environment: exposes broken tools + test files
  tasks/
    stl_ep1_broken_validator.py   # Episode 1: detect and fix broken STL validator
    stl_ep2_batch_processing.py   # Episode 2: batch validation, reuse ep1 tool
    stl_ep3_binary_variant.py     # Episode 3: binary STL format, reuse or extend
    repo_task1_bom_parser.py      # Repo task 1: broken bill-of-materials parser
    repo_task2_bom_migration.py   # Repo task 2: BOM format migration, reuse ep1 tool
    artifact_task1_mesh_report.py # Artifact task: generate mesh quality report
  tools/                    # Agent-authored tools are stored here at runtime
    .gitkeep
  oracle/                   # Hidden ground truth files, never exposed to agent
    stl_ground_truth.json
    bom_ground_truth.json
  fixtures/                 # STL and BOM files provided to the agent
    valid/
    broken/
    binary/
  logs/                     # Structured trace logs from runs
  results/                  # Verifier outputs and summary reports
```

---

## Layer 1 — Task Spec

A `TaskSpec` is a Python dataclass describing one benchmark task.
The task registry maps task IDs to TaskSpec instances so the runner
can load any task by ID.

```python
@dataclass
class TaskSpec:
    task_id: str
    family: str                  # "terminal", "repository", "sequence"
    instructions: str            # exactly what the agent is told, no more
    allowed_actions: list[str]   # e.g. ["write_file", "run_python", "read_file"]
    tool_library_enabled: bool   # whether agent can access persistent tool library
    verifier_config: dict        # passed directly to the verifier, not shown to agent
    episode_sequence: str | None # e.g. "stl_sequence" — links related episodes
    episode_number: int | None   # 1, 2, 3 within a sequence
```

Tasks are never aware of the verifier internals. The agent only
sees `instructions` and `allowed_actions`.

---

## Layer 2 — Environment / Sandbox

Each task has an environment that exposes the state the agent
sees and acts on. The environment provides:
- A working directory the agent can read and write
- The broken or incomplete tool the agent is given as a starting point
- Any fixture files (STL files, BOM files) the agent needs
- A defined set of actions the agent can call

The environment does **not** know about scoring or ground truth.
It just exposes state and actions.

### STL Environment (`stl_env.py`)
Exposes:
- `read_file(path)` — read a file from the working directory
- `write_file(path, content)` — write a file (this is how agents author tools)
- `run_python(path)` — execute a Python file and return stdout/stderr
- `list_files()` — list files in the working directory
- The broken STL parser pre-loaded into the working directory
- A set of fixture STL files with known (but hidden) correctness properties

### The broken tool the agent is given
`stl_validator_provided.py` — a validator that:
- Reads ASCII STL files and reports triangle count, surface area, and validity
- **Hidden failure mode:** silently accepts faces where the declared normal
  is inconsistent with the normal computed from vertex winding order
- Outputs `{"valid": true}` for broken meshes, making it appear to work

This is realistic: bad face normals are a common STL export bug that
causes failures in 3D printing and FEA simulation, but look correct
to a naive string parser.

---

## Layer 3 — Tool Library

A persistent on-disk store of tools the agent has authored.
Persists between episodes so reuse is real, not simulated.

Each tool is stored as a directory under `tools/`:
```
tools/
  stl_normal_checker/
    tool.py           # the authored tool code
    manifest.json     # metadata
```

`manifest.json` schema:
```json
{
  "tool_id": "stl_normal_checker",
  "name": "STL Normal Consistency Checker",
  "description": "Validates that face normals match vertex winding order",
  "tags": ["stl", "validation", "geometry", "normals"],
  "authored_in_task": "stl_ep1_broken_validator",
  "created_at": "2026-04-06T12:00:00Z"
}
```

### Methods
- `register(tool_id, code, manifest)` — save a new tool to disk
- `lookup(name)` — retrieve a tool by exact name
- `search(tags)` — retrieve tools matching one or more tags
- `list_tools()` — return all manifests so the agent can browse the library

### What makes reuse measurable
The verifier records whether the agent called `tool_library.search()`
or `tool_library.lookup()` before writing a new tool. If it did and
reused an existing tool, that is flagged as a reuse event in the trace.
Episode 2 scores are compared against episode 1 scores and step counts
to compute amortization gain.

---

## Layer 4 — Runner

The main execution loop. This is the engine of the harness.

Responsibilities:
- Load a task by ID from the registry
- Initialize the correct environment for that task
- Feed the agent the task instructions
- Route agent actions through the environment
- On each step, append a structured event to the trace log
- Detect when the agent signals completion
- Hand off to the verifier with the agent's output and the full trace

The runner does **not** score anything. It does not know what
correct looks like.

### Trace log format (one JSON object per line)
```json
{
  "step": 3,
  "task_id": "stl_ep1_broken_validator",
  "action": "write_file",
  "args": {"path": "stl_normal_checker.py"},
  "result": "ok",
  "tool_library_lookup": false,
  "timestamp": "2026-04-06T12:04:22Z"
}
```

---

## Layer 5 — Verifier / Scorer

Completely separate from the runner. The agent never interacts
with this layer directly.

Takes as input:
- The agent's working directory after task completion
- The full trace log
- The task's `verifier_config`

Returns:
```python
@dataclass
class VerifierResult:
    task_id: str
    passed: bool
    score: float             # 0.0 to 1.0 partial credit
    failure_reason: str | None
    tool_authored: bool      # did the agent write a new tool?
    tool_registered: bool    # did the agent store it in the library?
    tool_reused: bool        # did the agent reuse an existing library tool?
    steps_taken: int
    reuse_gain: float | None # only populated for episode 2+
```

### STL verifier logic
Runs the agent's authored tool against the hidden oracle set in
`oracle/stl_ground_truth.json`, which contains STL files with known
correct validity labels. Compares the tool's output against ground
truth. Partial credit is awarded for correctly classifying some files
even if not all.

The verifier also checks that the hidden failure mode was actually
addressed — if the agent's tool still silently accepts bad normals,
it fails even if everything else looks correct.

---

## Layer 6 — Reporting

Takes all verifier outputs and trace logs from a run and produces:
- `results/summary.csv` — one row per task with all VerifierResult fields
- `results/report.md` — a markdown table suitable for the submission
- `results/reuse_analysis.md` — compares episode 1 vs episode 2 step
  counts and scores to show amortization gain

---

## Task set

| Task ID                    | Family     | Episodes | Description                                              |
|---------------------------|------------|----------|----------------------------------------------------------|
| stl_ep1_broken_validator  | Terminal   | 1 of 3   | Detect and fix broken STL normal validator               |
| stl_ep2_batch_processing  | Sequence   | 2 of 3   | Batch validate supplier STL files, reuse ep1 tool        |
| stl_ep3_binary_variant    | Sequence   | 3 of 3   | Binary STL format, decide to reuse or extend ep1 tool    |
| repo_task1_bom_parser     | Repository | 1 of 2   | Detect and fix broken BOM parser (wrong unit conversion) |
| repo_task2_bom_migration  | Repository | 2 of 2   | Migrate BOM format, reuse or extend ep1 checker          |
| artifact_task1_mesh_report| Terminal   | —        | Generate mesh quality report; provided renderer is buggy |

---

## Task details

### stl_ep1_broken_validator
**What the agent is told:** "You have been given an STL validation tool.
Your job is to validate a set of STL files and report which ones are
suitable for manufacturing. Use the provided tool as a starting point."

**Hidden failure mode:** The provided tool does not check normal
consistency. Several fixture files have inverted normals and will be
incorrectly reported as valid.

**What a successful agent does:**
1. Runs the provided tool, notices results look suspicious or tests
   against a known-bad file
2. Identifies the normal consistency gap
3. Authors `stl_normal_checker.py` that computes expected normals
   from vertex cross products and compares to declared normals
4. Tests the new tool against the fixture files
5. Registers it in the tool library with appropriate tags

**Verifier checks:** Does the authored tool correctly classify all
oracle files? Does it specifically catch inverted-normal cases that
the provided tool missed?

---

### stl_ep2_batch_processing
**What the agent is told:** "You have received a batch of 20 STL files
from an external supplier. Identify all files that would fail
manufacturing validation and produce a report."

**What a successful agent does:**
1. Checks the tool library for relevant tools
2. Finds and reuses `stl_normal_checker` from episode 1
3. Applies it at scale across all 20 files
4. Produces a report

**Verifier checks:** Correct classification of oracle batch files.
Reuse event recorded in trace. Step count and token count compared
to episode 1 baseline to measure amortization.

---

### stl_ep3_binary_variant
**What the agent is told:** "Validate a set of STL files. Note that
some files may be in binary STL format."

**What a successful agent does:**
1. Checks tool library — finds existing ASCII checker
2. Recognizes it does not handle binary STL
3. Makes an explicit decision: extend existing tool vs author new one
4. Authors a binary STL reader and either wraps or extends the
   existing checker

**Verifier checks:** Handles both ASCII and binary files correctly.
Explicit reuse-or-extend decision is logged. This task tests
benchmark design judgment question: "can the agent recognize when
to reuse vs build new?"

---

## Key design decisions

- **Verifier is always separate from runner** so the agent cannot
  self-grade or game the scoring
- **Tool library persists to disk** so reuse across episodes is real,
  not simulated in memory
- **At least one task (ep1) has a hidden failure mode** where the
  provided interface produces plausible-looking but wrong output
- **Episode sequence is explicitly designed so ep2 is faster if ep1
  tool is reused** — this makes amortization directly measurable
- **Oracle files are never in the agent's working directory** — they
  live in `oracle/` which is outside the environment sandbox
- **STL domain is chosen deliberately** — it is a real engineering
  format, the failure modes are realistic, and the repair tools are
  genuinely useful and self-contained

## What we are NOT building
- A general-purpose agent framework
- Support for additional geometry formats beyond STL and BOM
- A web interface or GUI
- Parallel task execution
- Automatic hyperparameter tuning
- Integration with external CAD software
