# Agent Notes

Notes on how ToolsmithBench was scoped and built, and what I'd change next.

## Scoping decisions

**Why STL / CAD geometry?** I needed a domain where (a) correctness has an
unambiguous ground truth, (b) the file format is simple enough to hand-author
fixtures without external libraries, and (c) the "obvious" tool can be subtly
wrong in a way that survives a casual glance. ASCII STL fits all three —
parsing is a few dozen lines, validity collapses to "manifold + consistent
normals", and a validator that omits the normal check looks complete on a
first read.

**Why 6 tasks?** Two episodes wasn't enough to distinguish "the agent reused a
tool" from "the agent happened to be faster on the second run." Six gives
three episodes inside the canonical sequence (`stl_sequence`: ep1 → ep2 → ep3
→ ep5) plus three terminal tasks (ep4 unit conversion, ep6 repair) that probe
different capabilities while still reusing the same verifier and library
infrastructure.

**What got cut:**
- A multi-format task (STEP, OBJ) — would have required pulling in a parser
  library, which violates the no-third-party-deps constraint that keeps the
  benchmark reproducible.
- A "tool composition" task where the agent must chain two library tools —
  interesting but requires a more expressive action API than the current
  flat dict.
- A streaming / very-large-file task — irrelevant to the tool-authoring
  question I actually wanted to measure.
- Per-task agent prompts — the system prompt is shared across all tasks on
  purpose. Tuning per task would inflate scores without measuring tool reuse.

## How Claude Code was used

**Manually designed (by me):**
- The overall architecture: TaskSpec / Runner / ToolLibrary / STLEnvironment /
  STLVerifier separation, the rule that the verifier never imports anything
  the runner touches, the rule that the tool library lives at a fixed
  on-disk path so it persists across episodes.
- The hidden failure mode: choosing "omits normal consistency check" → later
  "uses too-strict similarity threshold" as a trap that looks like defensive
  engineering. The threshold value (`-0.99` vs the correct `< 0`) and the
  disguising comment.
- Task design: what each episode probes, what failure modes to mix into each
  fixture batch, why ep3 mixes binary and ASCII (forces format detection
  rather than blind reuse), why ep6 is repair rather than validation.
- Verifier scoring rules — `passed = (score == 1.0) and normal_check_passed`,
  the two verification modes (`tool` vs `report`), what counts as
  `tool_authored` vs `tool_reused`.
- Amortization metric: `(ep1_steps - epN_steps) / ep1_steps`, only set when
  the later episode actually used fewer steps.

**Claude Code generated:**
- Boilerplate: dataclasses, the JSONL trace writer, the markdown report
  table, CSV writer, file-traversal guards in STLEnvironment.
- Fixture generation: the 50 STL files for ep5 (and the 20 for ep2) were
  written via a one-shot Python script invoked through Bash — I described
  the geometry and failure-mode distribution, Claude wrote the script.
- Report formatting: `_write_reuse_analysis`, the model comparison table,
  the terminal summary printer.
- Iteration on `_parse_response`: I described the failure mode (model emits
  prose before JSON), Claude wrote the brace-counting extractor.
- Plumbing changes when I changed my mind: switching from `anthropic` SDK →
  `openai` SDK → `urllib.request`, threading `tools_dir` through Runner so
  each model gets its own library, retry-on-trivial-run logic for ep2/ep5.

## What was verified manually

- After scaffolding the runner, I asked for a 5-line smoke test that
  instantiated `STLEnvironment` and exercised all four actions, then ran it.
- After writing each fixture, I ran `stl_validator_provided.py` against it
  and checked the JSON output matched my expectation (good_mesh → valid,
  bad_normals → silently valid, missing_faces → caught open edges).
- After tightening the hidden failure mode, I re-ran all three ep1 fixtures
  through the new validator and confirmed the bad_normals dot products
  (-0.5 and -0.333) slip through the `-0.99` threshold.
- After adding task ep4/5/6, I ran a registration smoke test that imported
  all six task modules, listed registered tasks, and checked fixture counts
  + oracle paths for each.
- End-to-end: ran the full sweep against `claude-sonnet-4.6` once, then
  spot-checked the resulting `tools/<model>/` directory by hand to confirm
  the agent had registered a real tool with sensible tags.

## Known issues and limitations

- **Hidden failure mode is now harder to detect.** With the threshold trap
  (similarity < -0.99) the agent has to actually reason about what threshold
  is correct rather than just notice a missing function. This is more
  realistic but it means ep1 pass rate drops — the benchmark is harder to
  pass cleanly. I think that's the right tradeoff but it deserves a note.
- **ep5 is structurally similar to ep2.** Both are batch validation tasks;
  ep5 just has more files. The intent was to measure whether the agent
  reuses its ep2 tool unchanged, but that signal is weak — a slightly
  different prompt would achieve the same thing without 50 fixtures.
- **The tool library accumulates across runs without cleanup.** Successive
  benchmark runs deposit tools into `tools/<model>/`, and the "most recent
  by created_at" selector picks whichever was registered last. This means
  re-running the benchmark can score against a tool from a previous run.
  There's no `--clean` flag.
- **Occasional OpenRouter parse failures.** The model sometimes prepends
  prose to its JSON response. I added the brace-counting parser and the
  CRITICAL system-prompt instruction, plus a retry-on-trivial-run for the
  long-form tasks (ep2, ep5). This catches most cases but not all.
- **ep6 repair is fragile.** The verifier runs the agent's tool against the
  fixture and checks `valid: true`, which means a tool that just hardcodes
  `{"valid": true}` would pass. A stricter verifier would re-parse the
  repaired file and confirm the normals are now consistent.
- **No baseline.** I have no measurement of how the agent performs *without*
  a tool library, so I can't actually attribute the step reduction in ep2/3
  to reuse vs. just task familiarity from the conversation history (which
  is reset between episodes, but still — I should measure this).
- **Agents can read the provided tool's source code.** Because
  `stl_validator_provided.py` is a readable Python file in the working
  directory, agents can audit its logic directly rather than inferring its
  failure mode from observed behavior. This shortcuts the intended "detect
  from behavior" signal. A stricter benchmark would provide the tool as a
  compiled binary or API endpoint.

## What I would do next

1. **Remove the hidden-failure-mode hint entirely** from the validator
   filename and any docstring. Right now the file is named
   `stl_validator_provided.py`, which is fine, but a follow-up would name it
   something innocuous (`mesh_check.py`) and rewrite the docstring to read
   like real internal tooling. The point is to test whether the agent
   audits provided code or trusts it.
2. **Add a no-library control condition.** Run each episode twice — once
   with the tool library accessible, once with `tool_library_enabled=False`
   — and report the delta. That's the only way to actually attribute
   amortization gains to reuse.
3. **More task families beyond STL.** Same harness, different domain:
   regex authoring, SQL query repair, log parsing. The verifier and library
   are domain-agnostic; only the environment and fixtures are STL-specific.
4. **Tool library versioning.** Right now `register` overwrites whatever
   manifest already lives at `tools/<tool_id>/`. A real library would keep
   versions, let the agent diff them, and let the verifier pin a specific
   version per task.
5. **A proper cleanup / freeze flag.** `python run_benchmark.py --clean`
   should wipe `tools/`, `logs/`, and `results/` before running, so a fresh
   benchmark run is genuinely fresh.
6. **Measure cost, not just steps.** Step count is a proxy. The real
   amortization story is "how many input tokens did ep2 spend re-deriving
   the validator vs looking it up." The `usage` block from OpenRouter has
   what I need; I just haven't wired it through the trace.
