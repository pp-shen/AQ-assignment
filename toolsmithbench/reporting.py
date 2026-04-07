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
_EP3_TASK = "stl_ep3_binary_variant"


def generate_reports(
    results: list[VerifierResult],
    *,
    results_dir: Path | None = None,
) -> None:
    """Write summary.csv, report.md, and reuse_analysis.md.

    Args:
        results:     VerifierResults for a single model run, in episode order.
        results_dir: Directory to write into.  Defaults to the top-level
                     ``results/`` folder.  Pass ``results / model_label`` to
                     write per-model reports into a subdirectory.
    """
    out = results_dir if results_dir is not None else _RESULTS_DIR
    out.mkdir(parents=True, exist_ok=True)
    _write_csv(results, out)
    _write_report_md(results, out)
    _write_reuse_analysis(results, out)


def generate_model_comparison(
    results_by_model: dict[str, list[VerifierResult]],
) -> None:
    """Write results/model_comparison.md comparing all models side by side.

    Columns: model, task, passed, score, steps, tool_authored, tool_reused,
    reuse_gain.
    """
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _RESULTS_DIR / "model_comparison.md"

    lines: list[str] = []
    lines.append("# Model Comparison\n")
    lines.append(
        "| Model | Task | Passed | Score | Steps"
        " | Authored | Reused | Reuse Gain |"
    )
    lines.append("|---|---|:---:|---:|---:|:---:|:---:|---:|")

    for model_label, results in results_by_model.items():
        for r in results:
            gain = f"{r.reuse_gain:.1%}" if r.reuse_gain is not None else "—"
            lines.append(
                f"| {model_label} | {r.task_id} | {'✓' if r.passed else '✗'}"
                f" | {r.score:.2f} | {r.steps_taken}"
                f" | {_bool(r.tool_authored)} | {_bool(r.tool_reused)} | {gain} |"
            )

        # Per-model summary row.
        if results:
            pass_rate = sum(r.passed for r in results) / len(results)
            avg_score = sum(r.score for r in results) / len(results)
            lines.append(
                f"| **{model_label} total** | **{len(results)} tasks**"
                f" | **{pass_rate:.0%}** | **{avg_score:.2f}**"
                " | | | | |"
            )

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# summary.csv
# ---------------------------------------------------------------------------

def _write_csv(results: list[VerifierResult], out: Path) -> None:
    with (out / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "task_id":         r.task_id,
                "passed":          r.passed,
                "score":           r.score,
                "tool_authored":   r.tool_authored,
                "tool_registered": r.tool_registered,
                "tool_reused":     r.tool_reused,
                "steps_taken":     r.steps_taken,
                "reuse_gain":      r.reuse_gain if r.reuse_gain is not None else "",
            })


# ---------------------------------------------------------------------------
# report.md
# ---------------------------------------------------------------------------

def _write_report_md(results: list[VerifierResult], out: Path) -> None:
    lines: list[str] = []

    lines.append("# ToolsmithBench Results\n")
    lines.append(
        "| Task ID | Passed | Score | Authored | Registered | Reused"
        " | Steps | Reuse Gain |"
    )
    lines.append("|---|:---:|---:|:---:|:---:|:---:|---:|---:|")

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
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# reuse_analysis.md
# ---------------------------------------------------------------------------

def _write_reuse_analysis(results: list[VerifierResult], out: Path) -> None:
    by_task = {r.task_id: r for r in results}
    ep1 = by_task.get(_EP1_TASK)
    ep2 = by_task.get(_EP2_TASK)
    ep3 = by_task.get(_EP3_TASK)

    lines: list[str] = []
    lines.append("# Reuse Analysis: Episodes 1 – 3\n")

    if ep1 is None:
        lines.append(f"*Cannot compute reuse analysis — missing results for: {_EP1_TASK}*\n")
        (out / "reuse_analysis.md").write_text("\n".join(lines), encoding="utf-8")
        return

    # --- Step-count table ------------------------------------------------
    lines.append("## Step counts\n")

    ep_cols = [(ep1, "Episode 1")]
    if ep2 is not None:
        ep_cols.append((ep2, "Episode 2"))
    if ep3 is not None:
        ep_cols.append((ep3, "Episode 3"))

    header = "| |" + "".join(f" {label} |" for _, label in ep_cols)
    sep    = "|---|" + "".join(" ---: |" for _ in ep_cols)
    lines.append(header)
    lines.append(sep)
    lines.append("| Steps taken |" + "".join(f" {ep.steps_taken} |" for ep, _ in ep_cols))
    lines.append("| Score |"       + "".join(f" {ep.score:.2f} |"    for ep, _ in ep_cols))
    lines.append("| Tool reused |" + "".join(
        f" {'—' if ep is ep1 else _bool(ep.tool_reused)} |" for ep, _ in ep_cols
    ))
    lines.append("")

    # --- Per-episode amortization vs ep1 baseline ------------------------
    baseline = ep1.steps_taken
    lines.append("## Amortization vs Episode 1 baseline\n")

    if baseline == 0:
        lines.append("*Episode 1 step count is zero — cannot compute reduction.*")
    else:
        for ep, label in ep_cols[1:]:
            saved = baseline - ep.steps_taken
            if saved > 0:
                pct = saved / baseline
                lines.append(
                    f"**{label}** used **{ep.steps_taken}** steps vs episode 1's "
                    f"**{baseline}**, saving **{saved}** steps (**{pct:.1%}** reduction)."
                )
                if ep.tool_reused:
                    lines.append(
                        " The agent reused a library tool, which accounts for the reduction."
                    )
                else:
                    lines.append(
                        " Note: no tool-library lookup recorded — reduction may not be "
                        "attributable to reuse."
                    )
            elif saved == 0:
                lines.append(
                    f"**{label}** used **{ep.steps_taken}** steps — same as episode 1. "
                    f"No amortization gain."
                )
            else:
                lines.append(
                    f"**{label}** used **{ep.steps_taken}** steps — "
                    f"**{-saved}** more than episode 1's **{baseline}**."
                )
            lines.append("")

    # --- ep3 vs ep2 comparison -------------------------------------------
    if ep2 is not None and ep3 is not None and ep2.steps_taken > 0:
        lines.append("## Episode 3 vs Episode 2\n")
        saved32 = ep2.steps_taken - ep3.steps_taken
        if saved32 > 0:
            pct = saved32 / ep2.steps_taken
            lines.append(
                f"Episode 3 used **{ep3.steps_taken}** steps vs episode 2's "
                f"**{ep2.steps_taken}**, saving **{saved32}** steps (**{pct:.1%}** reduction)."
            )
        elif saved32 == 0:
            lines.append(
                f"Episode 3 used the same number of steps as episode 2 ({ep2.steps_taken})."
            )
        else:
            lines.append(
                f"Episode 3 used **{ep3.steps_taken}** steps — "
                f"**{-saved32}** more than episode 2's **{ep2.steps_taken}**."
            )
        lines.append("")

    (out / "reuse_analysis.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bool(value: bool) -> str:
    return "✓" if value else "✗"
