from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Source code for the placeholder broken validator.
# Hidden failure mode: silently accepts every file without checking normals.
_BROKEN_VALIDATOR_SOURCE = '''\
"""stl_validator_provided.py — provided STL validation tool.

Reads an ASCII STL file and reports triangle count and validity.

NOTE: This validator does NOT check that declared face normals are
consistent with the normal implied by vertex winding order.  Files
with inverted normals will be reported as valid.
"""
import json
import sys


def parse_stl(path: str) -> dict:
    triangles = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("facet normal"):
                triangles += 1
    # Always reports valid — does not verify normal consistency.
    return {"valid": True, "triangle_count": triangles}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_provided.py <file.stl>"}))
        sys.exit(1)
    result = parse_stl(sys.argv[1])
    print(json.dumps(result))
'''


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
        """Write the placeholder broken validator into the working directory."""
        dest = self.working_dir / "stl_validator_provided.py"
        if not dest.exists():
            dest.write_text(_BROKEN_VALIDATOR_SOURCE, encoding="utf-8")
