#!/usr/bin/env python3
"""run_benchmark.py — reproduce ToolsmithBench results for the STL sequence.

Usage:
    python run_benchmark.py

Runs episodes 1, 2, and 3 in order, verifies each, writes results/, and prints
a terminal summary.  The OPENROUTER_API_KEY environment variable must be set.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Make the package importable when run from the project root.
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")

import toolsmithbench.tasks.stl_ep1_broken_validator  # noqa: E402 — registers task
import toolsmithbench.tasks.stl_ep2_batch_processing  # noqa: E402 — registers task
import toolsmithbench.tasks.stl_ep3_binary_variant    # noqa: E402 — registers task
from toolsmithbench.agents.claude_agent import ClaudeAgent
from toolsmithbench.reporting import generate_reports
from toolsmithbench.runner import Runner
from toolsmithbench.task import get_task
from toolsmithbench.tool_library import ToolLibrary
from toolsmithbench.verifier import STLVerifier, VerifierResult


# ---------------------------------------------------------------------------
# Tool selection — library-first, no filename guessing
# ---------------------------------------------------------------------------

def _find_library_tool() -> Path | None:
    """Return the most recently registered STL validation tool from the library.

    Filters for tools tagged with both 'stl' and 'validation', sorts by
    created_at descending, and returns the tool.py path for the winner.
    Returns None if the library has no matching tool.
    """
    library = ToolLibrary()
    candidates = [
        t for t in library.list_tools()
        if {"stl", "validation"} & set(t.get("tags", []))
    ]
    if not candidates:
        logger.warning("no stl/validation tool found in tool library")
        return None

    candidates.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    best = candidates[0]
    tool_path = library.tools_dir / best["tool_id"] / "tool.py"
    if tool_path.exists():
        logger.info(
            "using library tool %r (created %s)",
            best["tool_id"], best.get("created_at", "?"),
        )
        return tool_path

    logger.warning("library tool %r manifest exists but tool.py missing", best["tool_id"])
    return None


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------

def _trace_flags(trace: list[dict]) -> dict:
    """Derive tool_authored / tool_registered / tool_reused from the trace."""
    tool_authored = any(
        e["action"] == "write_file"
        and e.get("result") == "ok"
        and e.get("args", {}).get("path", "").endswith(".py")
        and e.get("args", {}).get("path") != "stl_validator_provided.py"
        for e in trace
    )
    tool_registered = any(
        e["action"] == "tool_library_register" and e.get("result") == "ok"
        for e in trace
    )
    tool_reused = any(e.get("tool_library_lookup") for e in trace)
    return dict(tool_authored=tool_authored, tool_registered=tool_registered, tool_reused=tool_reused)


def _is_trivial_run(trace: list[dict], working_dir: Path) -> bool:
    """True when the run looks like an immediate parse failure rather than real work."""
    return len(trace) < 3 and not (working_dir / "validation_report.json").exists()


# ---------------------------------------------------------------------------
# Episode runner
# ---------------------------------------------------------------------------

def run_episode(task_id: str, *, retry_if_trivial: bool = False) -> tuple[VerifierResult, list[dict]]:
    """Run one episode end-to-end and return (VerifierResult, trace).

    Args:
        task_id:          Task to run.
        retry_if_trivial: When True, retry once if the agent finishes in fewer
                          than 3 steps without producing the expected output
                          (indicates a parse failure on the first API call).
    """
    task = get_task(task_id)
    agent = ClaudeAgent()   # fresh agent per episode; tool library is the cross-episode memory
    runner = Runner()

    _banner(f"Episode: {task_id}")
    trace, working_dir = runner.run(task, agent)
    logger.info("working_dir: %s", working_dir)

    if retry_if_trivial and _is_trivial_run(trace, working_dir):
        logger.warning(
            "%s finished in %d step(s) with no report — looks like a parse failure, "
            "retrying once",
            task_id, len(trace),
        )
        _banner(f"Episode: {task_id} (retry)")
        agent = ClaudeAgent()
        trace, working_dir = runner.run(task, agent)
        logger.info("retry working_dir: %s", working_dir)

    tool_path = _find_library_tool()
    logger.info("tool under evaluation: %s", tool_path)

    result = STLVerifier(task.verifier_config["oracle"]).verify(
        tool_path,
        task_id=task_id,
        working_dir=working_dir,
        steps_taken=len(trace),
        **_trace_flags(trace),
    )
    return result, trace


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _banner(text: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n{text}\n{bar}")


def _print_result(r: VerifierResult) -> None:
    status = "PASS ✓" if r.passed else "FAIL ✗"
    gain = f"{r.reuse_gain:.1%}" if r.reuse_gain is not None else "n/a"
    print(f"  Status      : {status}")
    print(f"  Score       : {r.score:.2f}")
    print(f"  Steps       : {r.steps_taken}")
    print(f"  Authored    : {r.tool_authored}  "
          f"Registered: {r.tool_registered}  "
          f"Reused: {r.tool_reused}")
    print(f"  Reuse gain  : {gain}")
    if r.failure_reason:
        print(f"  Failure     : {r.failure_reason}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    result1, _ = run_episode("stl_ep1_broken_validator")
    result2, _ = run_episode("stl_ep2_batch_processing", retry_if_trivial=True)
    result3, _ = run_episode("stl_ep3_binary_variant")

    # Amortization gain for each episode vs ep1 (the baseline).
    ep1_steps = result1.steps_taken
    if ep1_steps > 0:
        if result2.steps_taken < ep1_steps:
            result2.reuse_gain = (ep1_steps - result2.steps_taken) / ep1_steps
        if result3.steps_taken < ep1_steps:
            result3.reuse_gain = (ep1_steps - result3.steps_taken) / ep1_steps

    all_results = [result1, result2, result3]
    generate_reports(all_results)

    _banner("BENCHMARK SUMMARY")
    for r in all_results:
        print(f"\n{r.task_id}")
        _print_result(r)

    overall_passed = sum(r.passed for r in all_results)
    avg_score = sum(r.score for r in all_results) / len(all_results)
    print(f"\nOverall : {overall_passed}/3 passed  |  avg score {avg_score:.2f}")

    _print_amortization(result1, result2, result3)
    print("\nReports written to results/")


def _print_amortization(
    ep1: VerifierResult, ep2: VerifierResult, ep3: VerifierResult
) -> None:
    """Print a compact amortization table comparing ep2 and ep3 against ep1."""
    baseline = ep1.steps_taken
    if baseline == 0:
        return

    print("\nAmortization vs ep1 baseline:")
    for ep, label in ((ep2, "ep2"), (ep3, "ep3")):
        saved = baseline - ep.steps_taken
        if saved > 0:
            print(f"  {label}: {ep.steps_taken} steps  ({saved} fewer, "
                  f"{saved/baseline:.1%} reduction)")
        elif saved == 0:
            print(f"  {label}: {ep.steps_taken} steps  (no reduction)")
        else:
            print(f"  {label}: {ep.steps_taken} steps  ({-saved} more than ep1)")

    # Also show ep3 vs ep2 directly.
    ep2_steps = ep2.steps_taken
    if ep2_steps > 0:
        saved32 = ep2_steps - ep3.steps_taken
        if saved32 > 0:
            print(f"\n  ep3 vs ep2: {saved32} fewer steps "
                  f"({saved32/ep2_steps:.1%} reduction over ep2)")
        elif saved32 < 0:
            print(f"\n  ep3 vs ep2: {-saved32} more steps than ep2")


if __name__ == "__main__":
    main()
