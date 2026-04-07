#!/usr/bin/env python3
"""run_benchmark.py — reproduce ToolsmithBench results for the STL sequence.

Usage:
    python run_benchmark.py

Runs episode 1 then episode 2, verifies each, writes results/, and prints a
terminal summary.  The OPENROUTER_API_KEY environment variable must be set.
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
            "ep2 finished in %d step(s) with no report — looks like a parse failure, "
            "retrying once",
            len(trace),
        )
        _banner(f"Episode: {task_id} (retry)")
        agent.reset()
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
    result1, trace1 = run_episode("stl_ep1_broken_validator")
    result2, trace2 = run_episode("stl_ep2_batch_processing", retry_if_trivial=True)

    # Amortization gain: how much cheaper was ep2 relative to ep1?
    if result1.steps_taken > 0 and result2.steps_taken < result1.steps_taken:
        result2.reuse_gain = (result1.steps_taken - result2.steps_taken) / result1.steps_taken

    generate_reports([result1, result2])

    _banner("BENCHMARK SUMMARY")
    for r in (result1, result2):
        print(f"\n{r.task_id}")
        _print_result(r)

    overall_passed = sum(r.passed for r in (result1, result2))
    avg_score = (result1.score + result2.score) / 2
    print(f"\nOverall : {overall_passed}/2 passed  |  avg score {avg_score:.2f}")
    if result2.reuse_gain is not None:
        saved = result1.steps_taken - result2.steps_taken
        print(f"Amortization: ep2 used {saved} fewer steps than ep1 "
              f"({result2.reuse_gain:.1%} reduction)")
    print("\nReports written to results/")


if __name__ == "__main__":
    main()
