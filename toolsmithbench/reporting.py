"""reporting.py — aggregate VerifierResults into summary outputs.

Intentionally imports nothing from runner, verifier environments, or agents.
Call generate_reports(results) after all tasks have been verified.
"""
from __future__ import annotations

import csv
from pathlib import Path

from toolsmithbench.verifier import VerifierResult

_RESULTS_DIR = Path(__file__).parent.parent / "results"

_CSV_FIELDS = [
    "task_id", "passed", "score",
    "tool_authored", "tool_registered", "tool_reused",
    "steps_taken", "reuse_gain",
]

# episode_sequence tasks whose step counts feed the reuse analysis
_EP1_TASK = "stl_ep1_broken_validator"
_EP2_TASK = "stl_ep2_batch_processing"


def generate_reports(results: list[VerifierResult]) -> None:
    """Write summary.csv, report.md, and reuse_analysis.md to results/."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(results)
    _write_report_md(results)
    _write_reuse_analysis(results)


# ---------------------------------------------------------------------------
# summary.csv
# ---------------------------------------------------------------------------

def _write_csv(results: list[VerifierResult]) -> None:
    path = _RESULTS_DIR / "summary.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "task_id":        r.task_id,
                "passed":         r.passed,
                "score":          r.score,
                "tool_authored":  r.tool_authored,
                "tool_registered":r.tool_registered,
                "tool_reused":    r.tool_reused,
                "steps_taken":    r.steps_taken,
                "reuse_gain":     r.reuse_gain if r.reuse_gain is not None else "",
            })


# ---------------------------------------------------------------------------
# report.md
# ---------------------------------------------------------------------------

def _write_report_md(results: list[VerifierResult]) -> None:
    lines: list[str] = []

    lines.append("# ToolsmithBench Results\n")
    lines.append(
        "| Task ID | Passed | Score | Authored | Registered | Reused"
        " | Steps | Reuse Gain |"
    )
    lines.append(
        "|---|:---:|---:|:---:|:---:|:---:|---:|---:|"
    )

    for r in results:
        gain = f"{r.reuse_gain:.1%}" if r.reuse_gain is not None else "—"
        lines.append(
            f"| {r.task_id} | {'✓' if r.passed else '✗'} | {r.score:.2f}"
            f" | {_bool(r.tool_authored)} | {_bool(r.tool_registered)}"
            f" | {_bool(r.tool_reused)} | {r.steps_taken} | {gain} |"
        )

    if results:
        pass_rate = sum(r.passed for r in results) / len(results)
        avg_score = sum(r.score for r in results) / len(results)
        lines.append(
            f"| **{len(results)} tasks total** | **{pass_rate:.0%}** | **{avg_score:.2f}**"
            " | | | | | |"
        )

    lines.append("")
    (_RESULTS_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# reuse_analysis.md
# ---------------------------------------------------------------------------

def _write_reuse_analysis(results: list[VerifierResult]) -> None:
    by_task = {r.task_id: r for r in results}
    ep1 = by_task.get(_EP1_TASK)
    ep2 = by_task.get(_EP2_TASK)

    lines: list[str] = []
    lines.append("# Reuse Analysis: Episode 1 vs Episode 2\n")

    if ep1 is None or ep2 is None:
        missing = ", ".join(
            t for t, r in [(_EP1_TASK, ep1), (_EP2_TASK, ep2)] if r is None
        )
        lines.append(f"*Cannot compute reuse analysis — missing results for: {missing}*\n")
        (_RESULTS_DIR / "reuse_analysis.md").write_text("\n".join(lines), encoding="utf-8")
        return

    lines.append("## Step counts\n")
    lines.append("| | Episode 1 | Episode 2 |")
    lines.append("|---|---:|---:|")
    lines.append(f"| Steps taken | {ep1.steps_taken} | {ep2.steps_taken} |")
    lines.append(f"| Score | {ep1.score:.2f} | {ep2.score:.2f} |")
    lines.append(f"| Tool reused | — | {_bool(ep2.tool_reused)} |")
    lines.append("")

    if ep1.steps_taken > 0 and ep2.steps_taken < ep1.steps_taken:
        saved = ep1.steps_taken - ep2.steps_taken
        gain_pct = saved / ep1.steps_taken
        lines.append("## Amortization gain\n")
        lines.append(
            f"Episode 2 used **{ep2.steps_taken}** steps vs episode 1's "
            f"**{ep1.steps_taken}**, saving **{saved}** steps "
            f"(**{gain_pct:.1%}** reduction)."
        )
        if ep2.tool_reused:
            lines.append(
                "\nThe agent reused a library tool from episode 1, which accounts "
                "for the step reduction."
            )
        else:
            lines.append(
                "\nNote: the agent did *not* record a tool-library lookup in episode 2 — "
                "the step reduction may not be attributable to reuse."
            )
    elif ep1.steps_taken > 0 and ep2.steps_taken >= ep1.steps_taken:
        lines.append("## Amortization gain\n")
        lines.append(
            f"Episode 2 used **{ep2.steps_taken}** steps — no fewer than episode 1's "
            f"**{ep1.steps_taken}**. No amortization gain was observed."
        )
        if not ep2.tool_reused:
            lines.append(" The agent did not reuse the episode 1 tool.")
    else:
        lines.append("*Step count data unavailable for amortization calculation.*")

    lines.append("")
    (_RESULTS_DIR / "reuse_analysis.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bool(value: bool) -> str:
    return "✓" if value else "✗"
