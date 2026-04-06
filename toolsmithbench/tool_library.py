from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).parent.parent / "tools"


class ToolLibrary:
    """Persistent on-disk store of agent-authored tools.

    Each tool lives under tools/<tool_id>/
      tool.py        — the authored tool code
      manifest.json  — metadata (tool_id, name, description, tags, ...)
    """

    def __init__(self, tools_dir: Path = _TOOLS_DIR) -> None:
        self.tools_dir = tools_dir
        self.tools_dir.mkdir(parents=True, exist_ok=True)

    def register(self, tool_id: str, code: str, manifest: dict) -> None:
        """Save a new tool to disk.

        Args:
            tool_id:  Unique identifier, used as the directory name.
            code:     Python source code for the tool.
            manifest: Metadata dict (name, description, tags, authored_in_task, …).
                      Fields tool_id and created_at are added automatically if absent.
        """
        tool_dir = self.tools_dir / tool_id
        tool_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "tool_id": tool_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **manifest,
        }

        (tool_dir / "tool.py").write_text(code, encoding="utf-8")
        (tool_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        logger.info("Registered tool %r at %s", tool_id, tool_dir)

    def lookup(self, name: str) -> dict | None:
        """Retrieve a tool by exact tool_id.

        Returns a dict with keys 'manifest' and 'code', or None if not found.
        """
        tool_dir = self.tools_dir / name
        if not tool_dir.is_dir():
            return None

        manifest = json.loads((tool_dir / "manifest.json").read_text(encoding="utf-8"))
        code = (tool_dir / "tool.py").read_text(encoding="utf-8")
        return {"manifest": manifest, "code": code}

    def search(self, tags: list[str]) -> list[dict]:
        """Return all tools whose manifest tags overlap with the given tags."""
        tag_set = set(tags)
        results = []
        for manifest in self._iter_manifests():
            if tag_set & set(manifest.get("tags", [])):
                results.append(manifest)
        return results

    def list_tools(self) -> list[dict]:
        """Return all manifests so the agent can browse the library."""
        return list(self._iter_manifests())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _iter_manifests(self):
        for tool_dir in sorted(self.tools_dir.iterdir()):
            manifest_path = tool_dir / "manifest.json"
            if manifest_path.is_file():
                yield json.loads(manifest_path.read_text(encoding="utf-8"))
