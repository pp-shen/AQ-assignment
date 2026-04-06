"""verifier.py — post-run scoring for ToolsmithBench.

This module is intentionally isolated from the runner and all environments.
It is never imported during a benchmark run — only called after the agent
has finished and control has been handed back to the harness.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
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
    """Scores an agent-authored STL tool against the oracle ground truth.

    Usage::

        verifier = STLVerifier(oracle_path="toolsmithbench/oracle/stl_ground_truth.json")
        result = verifier.verify(
            tool_path="path/to/agent_tool.py",
            task_id="stl_ep1_broken_validator",
            tool_authored=True,
            tool_registered=True,
            tool_reused=False,
            steps_taken=7,
        )
    """

    def __init__(self, oracle_path: str | Path) -> None:
        self.oracle_path = Path(oracle_path)
        self._oracle = json.loads(self.oracle_path.read_text(encoding="utf-8"))
        self._fixtures_dir = _PACKAGE_ROOT / self._oracle["fixtures_dir"]

    def verify(
        self,
        tool_path: str | Path,
        task_id: str,
        *,
        tool_authored: bool = True,
        tool_registered: bool = False,
        tool_reused: bool = False,
        steps_taken: int = 0,
    ) -> VerifierResult:
        """Run the agent's tool against every oracle case and return a VerifierResult.

        Args:
            tool_path:       Path to the agent-authored Python tool.
            task_id:         Task identifier, passed through to VerifierResult.
            tool_authored:   Whether the agent wrote this tool during the run.
            tool_registered: Whether the agent stored the tool in the library.
            tool_reused:     Whether the agent reused an existing library tool.
            steps_taken:     Number of steps recorded in the trace log.
        """
        tool_path = Path(tool_path)
        cases = self._oracle["cases"]
        normal_check_required = self._oracle.get("normal_check_required", True)

        correct = 0
        failures: list[str] = []
        normal_check_passed = True

        for case in cases:
            fixture = self._fixtures_dir / case["file"]
            expected = case["valid"]
            failure_mode = case.get("failure_mode")

            output = self._run_tool(tool_path, fixture)

            if output is None:
                failures.append(
                    f"{case['file']}: tool produced no valid JSON output"
                )
                if failure_mode == "inverted_normals":
                    normal_check_passed = False
                continue

            predicted = output.get("valid")

            if predicted is None:
                failures.append(
                    f"{case['file']}: tool output is missing the 'valid' key"
                )
                if failure_mode == "inverted_normals":
                    normal_check_passed = False
                continue

            if predicted == expected:
                correct += 1
            else:
                failures.append(
                    f"{case['file']}: expected valid={expected}, got valid={predicted}"
                )

            # Explicit check for the hidden failure mode:
            # the tool must report bad_normals.stl as invalid (valid: false).
            if failure_mode == "inverted_normals" and predicted is not False:
                normal_check_passed = False

        score = correct / len(cases) if cases else 0.0
        passed = (score == 1.0) and (not normal_check_required or normal_check_passed)

        failure_reason: str | None = None
        if not passed:
            parts: list[str] = []
            if normal_check_required and not normal_check_passed:
                parts.append(
                    "tool does not detect inverted face normals "
                    "(hidden failure mode not addressed)"
                )
            parts.extend(failures)
            failure_reason = "; ".join(parts) if parts else None

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
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_tool(self, tool_path: Path, fixture: Path) -> dict | None:
        """Execute *tool_path* with *fixture* as its argument.

        Returns the parsed JSON dict from stdout, or None on any failure.
        """
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
