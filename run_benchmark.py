#!/usr/bin/env python3
"""run_benchmark.py — reproduce ToolsmithBench results for the STL sequence.

Usage:
    python run_benchmark.py           # full sweep: all 6 episodes × all models
    python run_benchmark.py --test    # quick check: ep4/5/6 only, first model only
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")

import toolsmithbench.tasks.stl_ep1_broken_validator  # noqa: E402
import toolsmithbench.tasks.stl_ep2_batch_processing  # noqa: E402
import toolsmithbench.tasks.stl_ep3_binary_variant    # noqa: E402
import toolsmithbench.tasks.stl_ep4_unit_conversion   # noqa: E402
import toolsmithbench.tasks.stl_ep5_large_batch       # noqa: E402
import toolsmithbench.tasks.stl_ep6_repair            # noqa: E402
from toolsmithbench.agents.claude_agent import ClaudeAgent
from toolsmithbench.reporting import generate_model_comparison, generate_reports
from toolsmithbench.runner import Runner
from toolsmithbench.task import get_task
from toolsmithbench.tool_library import ToolLibrary
from toolsmithbench.verifier import STLVerifier, VerifierResult

# (openrouter_model_id, short_label used for directory names)
_MODELS: list[tuple[str, str]] = [
    ("anthropic/claude-sonnet-4.6", "claude-sonnet-4.6"),
    ("openai/gpt-5.4",              "gpt-5.4"),
]

# Full episode list: (task_id, extra kwargs for run_episode)
_ALL_EPISODES: list[tuple[str, dict]] = [
    ("stl_ep1_broken_validator", {}),
    ("stl_ep2_batch_processing", {"retry_if_trivial": True}),
    ("stl_ep3_binary_variant",   {}),
    ("stl_ep4_unit_conversion",  {}),
    ("stl_ep5_large_batch",      {"retry_if_trivial": True}),
    ("stl_ep6_repair",           {}),
]

# Just the three new tasks — used by --test mode
_NEW_EPISODES: list[tuple[str, dict]] = [
    ("stl_ep4_unit_conversion",  {}),
    ("stl_ep5_large_batch",      {"retry_if_trivial": True}),
    ("stl_ep6_repair",           {}),
]

_TOOLS_BASE = Path(__file__).parent / "tools"


# ---------------------------------------------------------------------------
# Tool selection
# ---------------------------------------------------------------------------

def _find_library_tool(tools_dir: Path) -> Path | None:
    library = ToolLibrary(tools_dir)
    candidates = [
        t for t in library.list_tools()
        if {"stl", "validation"} & set(t.get("tags", []))
    ]
    if not candidates:
        logger.warning("no stl/validation tool found in %s", tools_dir)
        return None
    candidates.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    best = candidates[0]
    tool_path = library.tools_dir / best["tool_id"] / "tool.py"
    if tool_path.exists():
        logger.info("using library tool %r (created %s)", best["tool_id"], best.get("created_at", "?"))
        return tool_path
    logger.warning("library tool %r has no tool.py on disk", best["tool_id"])
    return None


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------

def _trace_flags(trace: list[dict]) -> dict:
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
    return len(trace) < 3 and not (working_dir / "validation_report.json").exists()


# ---------------------------------------------------------------------------
# Episode runner
# ---------------------------------------------------------------------------

def run_episode(
    task_id: str,
    *,
    model: str,
    tools_dir: Path,
    retry_if_trivial: bool = False,
) -> tuple[VerifierResult, list[dict]]:
    task = get_task(task_id)
    agent = ClaudeAgent(model=model)
    runner = Runner()

    _banner(f"Episode: {task_id}  [{model}]")
    trace, working_dir = runner.run(task, agent, tools_dir=tools_dir)
    logger.info("working_dir: %s", working_dir)

    if retry_if_trivial and _is_trivial_run(trace, working_dir):
        logger.warning(
            "%s finished in %d step(s) with no report — retrying once", task_id, len(trace)
        )
        _banner(f"Episode: {task_id}  [{model}]  (retry)")
        agent = ClaudeAgent(model=model)
        trace, working_dir = runner.run(task, agent, tools_dir=tools_dir)
        logger.info("retry working_dir: %s", working_dir)

    tool_path = _find_library_tool(tools_dir)
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
# Model run
# ---------------------------------------------------------------------------

def run_model(
    model_id: str,
    model_label: str,
    episodes: list[tuple[str, dict]],
) -> list[VerifierResult]:
    """Run *episodes* for *model_id* with an isolated tool library."""
    tools_dir = _TOOLS_BASE / model_label
    tools_dir.mkdir(parents=True, exist_ok=True)

    results: list[VerifierResult] = []
    for task_id, extra in episodes:
        result, _ = run_episode(task_id, model=model_id, tools_dir=tools_dir, **extra)
        results.append(result)

    # Amortization: compare every episode vs the first one that ran.
    if results:
        baseline = results[0].steps_taken
        for r in results[1:]:
            if baseline > 0 and r.steps_taken < baseline:
                r.reuse_gain = (baseline - r.steps_taken) / baseline

    results_dir = Path(__file__).parent / "results" / model_label
    generate_reports(results, results_dir=results_dir)

    return results


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _banner(text: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n{text}\n{bar}")


def _print_model_summary(model_label: str, results: list[VerifierResult]) -> None:
    _banner(f"Results: {model_label}")
    for r in results:
        status = "PASS ✓" if r.passed else "FAIL ✗"
        gain = f"{r.reuse_gain:.1%}" if r.reuse_gain is not None else "n/a"
        print(f"  {r.task_id}")
        print(f"    {status}  score={r.score:.2f}  steps={r.steps_taken}"
              f"  authored={r.tool_authored}  reused={r.tool_reused}  gain={gain}")
    overall_passed = sum(r.passed for r in results)
    avg_score = sum(r.score for r in results) / len(results)
    print(f"\n  Overall: {overall_passed}/{len(results)} passed  |  avg score {avg_score:.2f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    test_mode = "--test" in sys.argv
    episodes = _NEW_EPISODES if test_mode else _ALL_EPISODES
    models   = _MODELS[:1]  if test_mode else _MODELS

    if test_mode:
        _banner("TEST MODE — ep4/5/6 only, first model only")

    results_by_model: dict[str, list[VerifierResult]] = {}
    for model_id, model_label in models:
        _banner(f"MODEL: {model_label}")
        results_by_model[model_label] = run_model(model_id, model_label, episodes)

    if not test_mode:
        generate_model_comparison(results_by_model)

    for model_label, results in results_by_model.items():
        _print_model_summary(model_label, results)

    print("\nReports written to results/")


if __name__ == "__main__":
    main()
