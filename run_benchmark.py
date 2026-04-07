#!/usr/bin/env python3
"""run_benchmark.py — reproduce ToolsmithBench results for the STL sequence.

Usage:
    python run_benchmark.py              # full sweep: all 6 episodes × all models
    python run_benchmark.py --test       # quick check: ep4/5/6, first model only
    python run_benchmark.py --no-library # control comparison (lib ON vs OFF)
                                         # claude-sonnet-4.6 × ep1/ep2/ep3
"""
from __future__ import annotations

import logging
import sys
from dataclasses import replace
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


def _find_working_dir_tool(working_dir: Path) -> Path | None:
    """Fallback tool finder for runs where the tool library is disabled.

    Picks the most recently modified .py file in *working_dir* that isn't the
    provided validator. Returns None if nothing was authored.
    """
    candidates = [
        p for p in working_dir.glob("*.py")
        if p.name != "stl_validator_provided.py"
    ]
    if not candidates:
        logger.warning("no authored .py file found in %s", working_dir)
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    logger.info("using working-dir tool: %s", candidates[0].name)
    return candidates[0]


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
    library_enabled: bool = True,
    retry_if_trivial: bool = False,
) -> tuple[VerifierResult, list[dict]]:
    task = get_task(task_id)
    if not library_enabled:
        # TaskSpec is frozen at import time; create a per-run override.
        task = replace(task, tool_library_enabled=False)

    agent = ClaudeAgent(model=model)
    runner = Runner()

    label = f"{task_id}  [{model}]" + ("  (no-library)" if not library_enabled else "")
    _banner(f"Episode: {label}")
    trace, working_dir = runner.run(task, agent, tools_dir=tools_dir)
    logger.info("working_dir: %s", working_dir)

    if retry_if_trivial and _is_trivial_run(trace, working_dir):
        logger.warning(
            "%s finished in %d step(s) with no report — retrying once", task_id, len(trace)
        )
        _banner(f"Episode: {label}  (retry)")
        agent = ClaudeAgent(model=model)
        trace, working_dir = runner.run(task, agent, tools_dir=tools_dir)
        logger.info("retry working_dir: %s", working_dir)

    if library_enabled:
        tool_path = _find_library_tool(tools_dir)
    else:
        tool_path = _find_working_dir_tool(working_dir)
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
    *,
    library_enabled: bool = True,
    tools_dir_label: str | None = None,
    write_reports: bool = True,
) -> list[VerifierResult]:
    """Run *episodes* for *model_id* with an isolated tool library.

    Args:
        library_enabled:  Pass False to disable tool_library_search /
                          tool_library_register for every episode.
        tools_dir_label:  Override the tools/ subdirectory.  Defaults to
                          *model_label*; pass a distinct label (e.g.
                          "claude-sonnet-4.6-nolib") to keep this run's
                          state isolated from other runs.
        write_reports:    Whether to write per-model reports to results/.
    """
    tools_dir = _TOOLS_BASE / (tools_dir_label or model_label)
    tools_dir.mkdir(parents=True, exist_ok=True)

    results: list[VerifierResult] = []
    for task_id, extra in episodes:
        result, _ = run_episode(
            task_id,
            model=model_id,
            tools_dir=tools_dir,
            library_enabled=library_enabled,
            **extra,
        )
        results.append(result)

    # Amortization: compare every episode vs the first one that ran.
    if results:
        baseline = results[0].steps_taken
        for r in results[1:]:
            if baseline > 0 and r.steps_taken < baseline:
                r.reuse_gain = (baseline - r.steps_taken) / baseline

    if write_reports:
        results_dir = Path(__file__).parent / "results" / (tools_dir_label or model_label)
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


def _print_library_comparison(
    with_lib: list[VerifierResult],
    without_lib: list[VerifierResult],
) -> None:
    """Print a side-by-side table comparing library-on vs library-off runs."""
    _banner("LIBRARY ON vs OFF — claude-sonnet-4.6")

    by_id_on  = {r.task_id: r for r in with_lib}
    by_id_off = {r.task_id: r for r in without_lib}
    task_ids  = [r.task_id for r in with_lib]

    header = f"  {'task':30s}  {'lib ON':>22s}    {'lib OFF':>22s}    {'Δ steps':>9s}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    on_total = off_total = 0
    for tid in task_ids:
        on  = by_id_on[tid]
        off = by_id_off.get(tid)
        on_str  = f"{'PASS' if on.passed else 'FAIL'} score={on.score:.2f} steps={on.steps_taken}"
        if off is None:
            print(f"  {tid:30s}  {on_str:>22s}    {'(not run)':>22s}    {'—':>9s}")
            continue
        off_str = f"{'PASS' if off.passed else 'FAIL'} score={off.score:.2f} steps={off.steps_taken}"
        delta   = off.steps_taken - on.steps_taken
        delta_s = f"{delta:+d}" if delta != 0 else "0"
        print(f"  {tid:30s}  {on_str:>22s}    {off_str:>22s}    {delta_s:>9s}")
        on_total  += on.steps_taken
        off_total += off.steps_taken

    print("  " + "-" * (len(header) - 2))
    total_delta = off_total - on_total
    print(f"  {'TOTAL steps':30s}  {on_total:>22d}    {off_total:>22d}"
          f"    {total_delta:+d}")
    if on_total > 0:
        print(f"\n  Library reduced total steps by "
              f"{(off_total - on_total) / off_total:.1%} "
              f"({off_total} → {on_total})" if off_total > 0 else "")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_NO_LIBRARY_EPISODES: list[tuple[str, dict]] = [
    ("stl_ep1_broken_validator", {}),
    ("stl_ep2_batch_processing", {"retry_if_trivial": True}),
    ("stl_ep3_binary_variant",   {}),
]

_NO_LIBRARY_MODEL = ("anthropic/claude-sonnet-4.6", "claude-sonnet-4.6")


def _run_no_library_comparison() -> None:
    """Run claude-sonnet-4.6 on ep1/2/3 twice — once with library, once without.

    Both runs use isolated tools/ subdirectories so they cannot influence
    each other.  Prints a side-by-side comparison and the standard per-run
    summary for each.
    """
    model_id, model_label = _NO_LIBRARY_MODEL

    _banner("CONTROL — library ON")
    with_lib = run_model(
        model_id, model_label, _NO_LIBRARY_EPISODES,
        library_enabled=True,
        tools_dir_label=f"{model_label}-lib",
    )

    _banner("CONTROL — library OFF")
    without_lib = run_model(
        model_id, model_label, _NO_LIBRARY_EPISODES,
        library_enabled=False,
        tools_dir_label=f"{model_label}-nolib",
    )

    _print_model_summary(f"{model_label}  (lib ON)",  with_lib)
    _print_model_summary(f"{model_label}  (lib OFF)", without_lib)
    _print_library_comparison(with_lib, without_lib)
    print("\nReports written to results/")


def main() -> None:
    if "--no-library" in sys.argv:
        _run_no_library_comparison()
        return

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
