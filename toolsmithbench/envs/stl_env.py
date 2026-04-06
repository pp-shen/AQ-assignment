from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class STLEnvironment:
    """Sandbox environment for STL benchmark tasks.

    Exposes four actions to the agent:
        read_file(path)           — read a file from the working directory
        write_file(path, content) — write a file (how agents author tools)
        run_python(path)          — execute a Python file, return stdout/stderr
        list_files()              — list files in the working directory

    On init the broken validator is copied into the working directory as
    ``stl_validator_provided.py`` so the agent has a starting point.
    """

    def __init__(self, working_dir: str | Path) -> None:
        self.working_dir = Path(working_dir).resolve()
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self._seed_broken_validator()

    # ------------------------------------------------------------------
    # Actions exposed to the agent
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> str:
        """Return the text contents of *path* (relative to working directory).

        Raises FileNotFoundError if the file does not exist.
        """
        return self._resolve(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        """Write *content* to *path* (relative to working directory).

        Creates intermediate directories if needed.
        """
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def run_python(self, path: str) -> dict:
        """Execute the Python file at *path* and return stdout/stderr.

        Returns:
            {
                "stdout": str,
                "stderr": str,
                "returncode": int,
            }
        """
        script = self._resolve(path)
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(self.working_dir),
            capture_output=True,
            text=True,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    def list_files(self) -> list[str]:
        """Return relative paths of all files in the working directory."""
        return sorted(
            str(p.relative_to(self.working_dir))
            for p in self.working_dir.rglob("*")
            if p.is_file()
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        """Resolve *path* relative to the working directory."""
        resolved = (self.working_dir / path).resolve()
        # Guard against directory-traversal outside the sandbox.
        resolved.relative_to(self.working_dir)  # raises ValueError if escaped
        return resolved

    def _seed_broken_validator(self) -> None:
        """Copy the broken validator from fixtures into the working directory."""
        src = _FIXTURES_DIR / "stl_validator_provided.py"
        dest = self.working_dir / "stl_validator_provided.py"
        if not dest.exists():
            shutil.copy2(src, dest)
