"""verifier.py — post-run scoring for ToolsmithBench.

This module is intentionally isolated from the runner and all environments.
It is never imported during a benchmark run — only called after the agent
has finished and control has been handed back to the harness.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).parent


@dataclass
class VerifierResult:
    task_id: str
    passed: bool
    score: float                  # 0.0–1.0 partial credit
    failure_reason: str | None
    tool_authored: bool           # did the agent write a new tool?
    tool_registered: bool         # did the agent store it in the tool library?
    tool_reused: bool             # did the agent reuse an existing library tool?
    steps_taken: int
    reuse_gain: float | None = None  # populated for episode 2+ by the reporter


class STLVerifier:
    """Scores an agent run against the oracle ground truth.

    Two verification modes are supported, selected by the oracle's
    ``verification_mode`` field:

    * ``"tool"`` (default) — runs the agent-authored Python tool against each
      oracle fixture file and inspects its JSON stdout.
    * ``"report"`` — reads a JSON report file the agent wrote to the working
      directory and compares each entry against the oracle labels.

    Usage (tool mode)::

        result = STLVerifier("toolsmithbench/oracle/stl_ground_truth.json").verify(
            tool_path="path/to/agent_tool.py",
            task_id="stl_ep1_broken_validator",
            tool_authored=True,
            tool_registered=True,
            tool_reused=False,
            steps_taken=7,
        )

    Usage (report mode)::

        result = STLVerifier("toolsmithbench/oracle/stl_ep2_ground_truth.json").verify(
            tool_path=None,
            task_id="stl_ep2_batch_processing",
            working_dir=Path("/tmp/tsb_stl_ep2_..."),
            tool_authored=True,
            tool_registered=True,
            tool_reused=True,
            steps_taken=5,
        )
    """

    def __init__(self, oracle_path: str | Path) -> None:
        self.oracle_path = Path(oracle_path)
        self._oracle = json.loads(self.oracle_path.read_text(encoding="utf-8"))
        self._fixtures_dir = _PACKAGE_ROOT / self._oracle["fixtures_dir"]
        self._mode = self._oracle.get("verification_mode", "tool")
        self._report_file = self._oracle.get("report_file", "validation_report.json")

    def verify(
        self,
        tool_path: str | Path | None,
        task_id: str,
        *,
        working_dir: Path | None = None,
        tool_authored: bool = True,
        tool_registered: bool = False,
        tool_reused: bool = False,
        steps_taken: int = 0,
    ) -> VerifierResult:
        """Score this run and return a VerifierResult.

        Args:
            tool_path:       Path to the agent-authored Python tool (tool mode).
                             May be None in report mode.
            task_id:         Task identifier, passed through to VerifierResult.
            working_dir:     Directory the agent wrote files into (required for
                             report mode; ignored in tool mode).
            tool_authored:   Whether the agent wrote a new tool during the run.
            tool_registered: Whether the agent stored the tool in the library.
            tool_reused:     Whether the agent reused an existing library tool.
            steps_taken:     Number of steps recorded in the trace log.
        """
        if self._mode == "report":
            return self._verify_report(
                task_id=task_id,
                working_dir=working_dir,
                tool_authored=tool_authored,
                tool_registered=tool_registered,
                tool_reused=tool_reused,
                steps_taken=steps_taken,
            )

        # Default: tool mode
        return self._verify_tool(
            tool_path=Path(tool_path) if tool_path is not None else None,
            task_id=task_id,
            tool_authored=tool_authored,
            tool_registered=tool_registered,
            tool_reused=tool_reused,
            steps_taken=steps_taken,
        )

    # ------------------------------------------------------------------
    # Tool mode — run agent's .py against each fixture, inspect stdout
    # ------------------------------------------------------------------

    def _verify_tool(
        self,
        tool_path: Path | None,
        task_id: str,
        *,
        tool_authored: bool,
        tool_registered: bool,
        tool_reused: bool,
        steps_taken: int,
    ) -> VerifierResult:
        cases = self._oracle["cases"]
        normal_check_required = self._oracle.get("normal_check_required", True)

        if tool_path is None:
            return VerifierResult(
                task_id=task_id,
                passed=False,
                score=0.0,
                failure_reason="no tool path provided for tool-mode verification",
                tool_authored=tool_authored,
                tool_registered=tool_registered,
                tool_reused=tool_reused,
                steps_taken=steps_taken,
            )

        correct = 0
        failures: list[str] = []
        normal_check_passed = True

        for case in cases:
            fixture = self._fixtures_dir / case["file"]
            expected = case["valid"]
            failure_mode = case.get("failure_mode")

            output = self._run_tool(tool_path, fixture)

            if output is None:
                failures.append(f"{case['file']}: tool produced no valid JSON output")
                if failure_mode == "inverted_normals":
                    normal_check_passed = False
                continue

            predicted = output.get("valid")

            if predicted is None:
                failures.append(f"{case['file']}: tool output missing 'valid' key")
                if failure_mode == "inverted_normals":
                    normal_check_passed = False
                continue

            if predicted == expected:
                correct += 1
            else:
                failures.append(
                    f"{case['file']}: expected valid={expected}, got valid={predicted}"
                )

            if failure_mode == "inverted_normals" and predicted is not False:
                normal_check_passed = False

        score = correct / len(cases) if cases else 0.0
        passed = (score == 1.0) and (not normal_check_required or normal_check_passed)
        failure_reason = self._build_failure_reason(
            normal_check_required, normal_check_passed, failures
        )

        return VerifierResult(
            task_id=task_id,
            passed=passed,
            score=round(score, 4),
            failure_reason=failure_reason,
            tool_authored=tool_authored,
            tool_registered=tool_registered,
            tool_reused=tool_reused,
            steps_taken=steps_taken,
        )

    def _run_tool(self, tool_path: Path, fixture: Path) -> dict | None:
        """Execute *tool_path* with *fixture* as its argument; return parsed JSON or None."""
        try:
            proc = subprocess.run(
                [sys.executable, str(tool_path), str(fixture)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return json.loads(proc.stdout)
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError):
            return None

    # ------------------------------------------------------------------
    # Report mode — read the agent's validation_report.json and score it
    # ------------------------------------------------------------------

    def _verify_report(
        self,
        task_id: str,
        working_dir: Path | None,
        *,
        tool_authored: bool,
        tool_registered: bool,
        tool_reused: bool,
        steps_taken: int,
    ) -> VerifierResult:
        cases = self._oracle["cases"]
        normal_check_required = self._oracle.get("normal_check_required", True)

        # Load the report the agent wrote.
        if working_dir is None:
            return self._fail(
                task_id, "working_dir not provided for report-mode verification",
                tool_authored, tool_registered, tool_reused, steps_taken,
            )

        report_path = working_dir / self._report_file
        if not report_path.exists():
            return self._fail(
                task_id,
                f"agent did not write {self._report_file} to the working directory",
                tool_authored, tool_registered, tool_reused, steps_taken,
            )

        try:
            raw_report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return self._fail(
                task_id, f"{self._report_file} is not valid JSON: {exc}",
                tool_authored, tool_registered, tool_reused, steps_taken,
            )

        # Accept either {"results": [...]} or a top-level list.
        if isinstance(raw_report, list):
            results_list = raw_report
        elif isinstance(raw_report, dict) and "results" in raw_report:
            results_list = raw_report["results"]
        else:
            return self._fail(
                task_id,
                f"{self._report_file} must be a list or have a 'results' key",
                tool_authored, tool_registered, tool_reused, steps_taken,
            )

        # Build a lookup: filename -> entry dict
        report_by_file: dict[str, dict] = {}
        for entry in results_list:
            if isinstance(entry, dict) and "file" in entry:
                report_by_file[entry["file"]] = entry

        correct = 0
        failures: list[str] = []
        normal_check_passed = True

        for case in cases:
            fname = case["file"]
            expected = case["valid"]
            failure_mode = case.get("failure_mode")

            entry = report_by_file.get(fname)
            if entry is None:
                failures.append(f"{fname}: missing from report")
                if failure_mode == "inverted_normals":
                    normal_check_passed = False
                continue

            predicted = entry.get("valid")
            if predicted is None:
                failures.append(f"{fname}: report entry missing 'valid' key")
                if failure_mode == "inverted_normals":
                    normal_check_passed = False
                continue

            if predicted == expected:
                correct += 1
            else:
                failures.append(
                    f"{fname}: expected valid={expected}, got valid={predicted}"
                )

            if failure_mode == "inverted_normals" and predicted is not False:
                normal_check_passed = False

        score = correct / len(cases) if cases else 0.0
        passed = (score == 1.0) and (not normal_check_required or normal_check_passed)
        failure_reason = self._build_failure_reason(
            normal_check_required, normal_check_passed, failures
        )

        return VerifierResult(
            task_id=task_id,
            passed=passed,
            score=round(score, 4),
            failure_reason=failure_reason,
            tool_authored=tool_authored,
            tool_registered=tool_registered,
            tool_reused=tool_reused,
            steps_taken=steps_taken,
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_failure_reason(
        normal_check_required: bool,
        normal_check_passed: bool,
        failures: list[str],
    ) -> str | None:
        parts: list[str] = []
        if normal_check_required and not normal_check_passed:
            parts.append(
                "tool does not detect inverted face normals "
                "(hidden failure mode not addressed)"
            )
        parts.extend(failures)
        return "; ".join(parts) if parts else None

    @staticmethod
    def _fail(
        task_id: str,
        reason: str,
        tool_authored: bool,
        tool_registered: bool,
        tool_reused: bool,
        steps_taken: int,
    ) -> VerifierResult:
        return VerifierResult(
            task_id=task_id,
            passed=False,
            score=0.0,
            failure_reason=reason,
            tool_authored=tool_authored,
            tool_registered=tool_registered,
            tool_reused=tool_reused,
            steps_taken=steps_taken,
        )
