from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TaskSpec:
    task_id: str
    family: str                     # "terminal", "repository", "sequence"
    instructions: str               # exactly what the agent is told, no more
    allowed_actions: list[str]      # e.g. ["write_file", "run_python", "read_file"]
    tool_library_enabled: bool      # whether agent can access persistent tool library
    verifier_config: dict           # passed directly to verifier, never shown to agent
    episode_sequence: str | None = None   # e.g. "stl_sequence" — links related episodes
    episode_number: int | None = None     # 1, 2, 3 within a sequence
    fixtures_dir: str | None = None       # pre-populate working dir with *.stl from here


# Registry maps task_id -> TaskSpec
_REGISTRY: dict[str, TaskSpec] = {}


def register_task(spec: TaskSpec) -> None:
    """Add a TaskSpec to the global registry."""
    _REGISTRY[spec.task_id] = spec


def get_task(task_id: str) -> TaskSpec:
    """Look up a task by ID. Raises KeyError if not found."""
    if task_id not in _REGISTRY:
        raise KeyError(f"Unknown task_id: {task_id!r}")
    return _REGISTRY[task_id]


def list_tasks() -> list[TaskSpec]:
    """Return all registered tasks."""
    return list(_REGISTRY.values())
