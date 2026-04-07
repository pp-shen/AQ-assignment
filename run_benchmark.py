#!/usr/bin/env python3
"""run_benchmark.py — reproduce ToolsmithBench results for the STL sequence.

Usage:
    python run_benchmark.py                          # full sweep: 6 eps × all models
    python run_benchmark.py --test                   # quick check: ep4/5/6, model 1
    python run_benchmark.py --no-library             # lib ON vs OFF control
    python run_benchmark.py --clean-library          # wipe claude library first
    python run_benchmark.py --episodes ep1,ep2,ep3   # only these episode IDs
                                                     # ("ep1" or "stl_ep1_broken_validator")

Flags can be combined.  Example:
    python run_benchmark.py --clean-library --episodes ep1,ep2,ep3 --no-library
"""
from __future__ import annotations

import argparse
import logging
import shutil
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
# Retry-on-failure is automatic for every episode now — no per-task flag needed.
_ALL_EPISODES: list[tuple[str, dict]] = [
    ("stl_ep1_broken_validator", {}),
    ("stl_ep2_batch_processing", {}),
    ("stl_ep3_binary_variant",   {}),
    ("stl_ep4_unit_conversion",  {}),
    ("stl_ep5_large_batch",      {}),
    ("stl_ep6_repair",           {}),
]

# Just the three new tasks — used by --test mode
_NEW_EPISODES: list[tuple[str, dict]] = [
    ("stl_ep4_unit_conversion",  {}),
    ("stl_ep5_large_batch",      {}),
    ("stl_ep6_repair",           {}),
]

_TOOLS_BASE = Path(__file__).parent / "tools"

# Map of every known episode id, plus short aliases (ep1 → stl_ep1_broken_validator).
_EPISODE_BY_ID: dict[str, tuple[str, dict]] = dict(_ALL_EPISODES)
_EPISODE_ALIASES: dict[str, str] = {
    f"ep{i + 1}": task_id
    for i, (task_id, _) in enumerate(_ALL_EPISODES)
}


def _resolve_episode_filter(spec: str) -> list[tuple[str, dict]]:
    """Parse --episodes value into a [(task_id, kwargs), ...] list.

    Accepts full task IDs (stl_ep1_broken_validator) or short aliases (ep1).
    Preserves the order the user typed them in.
    """
    selected: list[tuple[str, dict]] = []
    for raw in spec.split(","):
        name = raw.strip()
        if not name:
            continue
        task_id = _EPISODE_ALIASES.get(name, name)
        if task_id not in _EPISODE_BY_ID:
            valid = ", ".join(sorted(set(_EPISODE_BY_ID) | set(_EPISODE_ALIASES)))
            raise SystemExit(f"unknown episode {name!r}. Valid: {valid}")
        selected.append((task_id, _EPISODE_BY_ID[task_id]))
    if not selected:
        raise SystemExit("--episodes was empty")
    return selected


def _clean_library(model_label: str = "claude-sonnet-4.6") -> None:
    """Delete tools/<model_label>/ so the next run starts with an empty library.

    Also clears the -lib and -nolib variants used by --no-library.

    Files that can't be deleted (typically Windows file locks or read-only
    bits from a previous interrupted run) are skipped with a warning rather
    than raising — the next run will overwrite them anyway.
    """
    def _on_rm_error(func, path, exc_info):
        # Try to clear a read-only bit and retry once; otherwise skip.
        try:
            import os, stat
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not delete %s: %s — skipping", path, exc)

    for suffix in ("", "-lib", "-nolib"):
        target = _TOOLS_BASE / f"{model_label}{suffix}"
        if not target.exists():
            logger.info("nothing to remove at %s", target)
            continue

        # Python 3.12+ deprecated onerror in favour of onexc; pass whichever the
        # installed version supports so we work on 3.11 and 3.12+.
        try:
            shutil.rmtree(target, onexc=_on_rm_error)  # type: ignore[call-arg]
        except TypeError:
            shutil.rmtree(target, onerror=_on_rm_error)
        logger.info("removed %s", target)


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


def _is_failed_run(
    trace: list[dict], working_dir: Path, *, library_enabled: bool
) -> tuple[bool, str]:
    """Decide whether a run produced nothing useful and should be retried.

    Returns ``(should_retry, reason)``.  A run is considered failed when the
    agent ended via ``done`` without leaving behind any of the artefacts a
    verifier needs:

    * trivially short trace (≤2 steps) with no validation report — usually
      a parse failure on the very first response, and
    * any episode that called ``done`` without registering a tool, writing
      a ``.py`` file, or producing ``validation_report.json`` — covers a
      mid-run parse failure where the fallback ``("done", {})`` aborts the
      loop after the agent already did some work.

    The library-disabled mode swaps the "registered" check for "any .py
    authored", since registration is impossible by design in that mode.
    """
    has_report = (working_dir / "validation_report.json").exists()

    # Case 1: trivially short run (existing behaviour).
    if len(trace) < 3 and not has_report:
        return True, f"trivial run ({len(trace)} step(s))"

    # The episode ended via "done" if the last event's action is "done".
    if not trace or trace[-1].get("action") != "done":
        return False, ""

    # Case 2: ended in done without producing anything the verifier can score.
    tool_registered = any(
        e["action"] == "tool_library_register" and e.get("result") == "ok"
        for e in trace
    )
    py_authored = any(
        e["action"] == "write_file"
        and e.get("result") == "ok"
        and e.get("args", {}).get("path", "").endswith(".py")
        and e.get("args", {}).get("path") != "stl_validator_provided.py"
        for e in trace
    )

    if has_report:
        return False, ""
    if library_enabled and tool_registered:
        return False, ""
    if not library_enabled and py_authored:
        return False, ""

    return True, "agent called done without registering a tool or writing a report"


# ---------------------------------------------------------------------------
# Episode runner
# ---------------------------------------------------------------------------

def run_episode(
    task_id: str,
    *,
    model: str,
    tools_dir: Path,
    library_enabled: bool = True,
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

    failed, reason = _is_failed_run(trace, working_dir, library_enabled=library_enabled)
    if failed:
        logger.warning("%s: %s — retrying once with a fresh agent", task_id, reason)
        _banner(f"Episode: {label}  (retry — {reason})")
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

_NO_LIBRARY_DEFAULT_EPISODES: list[tuple[str, dict]] = [
    ("stl_ep1_broken_validator", {}),
    ("stl_ep2_batch_processing", {}),
    ("stl_ep3_binary_variant",   {}),
]

_NO_LIBRARY_MODEL = ("anthropic/claude-sonnet-4.6", "claude-sonnet-4.6")


def _run_no_library_comparison(episodes: list[tuple[str, dict]]) -> None:
    """Run claude-sonnet-4.6 on *episodes* twice — once with library, once without.

    Both runs use isolated tools/ subdirectories so they cannot influence
    each other.  Prints a side-by-side comparison and the standard per-run
    summary for each.
    """
    model_id, model_label = _NO_LIBRARY_MODEL

    _banner("CONTROL — library ON")
    with_lib = run_model(
        model_id, model_label, episodes,
        library_enabled=True,
        tools_dir_label=f"{model_label}-lib",
    )

    _banner("CONTROL — library OFF")
    without_lib = run_model(
        model_id, model_label, episodes,
        library_enabled=False,
        tools_dir_label=f"{model_label}-nolib",
    )

    _print_model_summary(f"{model_label}  (lib ON)",  with_lib)
    _print_model_summary(f"{model_label}  (lib OFF)", without_lib)
    _print_library_comparison(with_lib, without_lib)
    print("\nReports written to results/")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ToolsmithBench runner")
    parser.add_argument("--test", action="store_true",
                        help="quick check: ep4/5/6, first model only")
    parser.add_argument("--no-library", action="store_true",
                        help="control comparison — library ON vs library OFF")
    parser.add_argument("--clean-library", action="store_true",
                        help="wipe tools/claude-sonnet-4.6/ (and -lib/-nolib variants) before running")
    parser.add_argument("--episodes",
                        help="comma-separated episode IDs to run "
                             "(e.g. 'ep1,ep2,ep3' or full task IDs)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.clean_library:
        _banner("CLEANING LIBRARY")
        _clean_library()

    episode_override = _resolve_episode_filter(args.episodes) if args.episodes else None

    if args.no_library:
        episodes = episode_override or _NO_LIBRARY_DEFAULT_EPISODES
        _run_no_library_comparison(episodes)
        return

    if args.test:
        episodes = episode_override or _NEW_EPISODES
        models   = _MODELS[:1]
        _banner("TEST MODE — first model only")
    else:
        episodes = episode_override or _ALL_EPISODES
        models   = _MODELS

    results_by_model: dict[str, list[VerifierResult]] = {}
    for model_id, model_label in models:
        _banner(f"MODEL: {model_label}")
        results_by_model[model_label] = run_model(model_id, model_label, episodes)

    if not args.test and len(models) > 1:
        generate_model_comparison(results_by_model)

    for model_label, results in results_by_model.items():
        _print_model_summary(model_label, results)

    print("\nReports written to results/")


if __name__ == "__main__":
    main()
