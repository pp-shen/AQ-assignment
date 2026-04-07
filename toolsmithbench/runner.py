from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from toolsmithbench.envs.stl_env import STLEnvironment
from toolsmithbench.task import TaskSpec
from toolsmithbench.tool_library import ToolLibrary

logger = logging.getLogger(__name__)

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_MAX_STEPS = 20

# Actions that count as a tool-library lookup for trace annotation.
_LIBRARY_LOOKUP_ACTIONS = {"tool_library_search", "tool_library_lookup"}


class Runner:
    """Main agent execution loop.

    Feeds observations to an agent, routes its actions through the STL
    environment, records a structured trace, and writes it to disk.

    Does NOT score anything — the verifier is called by the harness after
    ``run()`` returns.

    Agent interface::

        class Agent:
            def step(self, observation: dict) -> tuple[str, dict]:
                ...  # returns (action, args)
    """

    def run(
        self, task: TaskSpec, agent, *, tools_dir: Path | None = None
    ) -> tuple[list[dict], Path]:
        """Execute *task* with *agent*.

        Args:
            task:      Task specification.
            agent:     Agent instance with a ``step(observation) -> (action, args)`` method.
            tools_dir: Override the tool library storage directory.  Pass a
                       model-specific path to keep each model's tools isolated.
                       Defaults to the package-level ``tools/`` directory.

        Returns:
            trace:       List of structured trace events (one per step).
            working_dir: Path to the environment's working directory,
                         ready for the verifier to inspect.
        """
        working_dir = Path(tempfile.mkdtemp(prefix=f"tsb_{task.task_id}_"))
        env = STLEnvironment(working_dir)

        if task.fixtures_dir:
            project_root = Path(__file__).parent.parent
            src_dir = project_root / task.fixtures_dir
            for stl_file in sorted(src_dir.glob("*.stl")):
                shutil.copy2(stl_file, working_dir / stl_file.name)
            logger.info("seeded %d STL files from %s", len(list(src_dir.glob("*.stl"))), src_dir)

        library = ToolLibrary(tools_dir) if tools_dir is not None else ToolLibrary()
        library_enabled = task.tool_library_enabled
        trace: list[dict] = []

        logger.info("run START  task_id=%r  working_dir=%s", task.task_id, working_dir)

        observation: dict = {
            "task_id": task.task_id,
            "instructions": task.instructions,
            "allowed_actions": task.allowed_actions,
            "files": env.list_files(),
            "last_action_result": None,
        }

        for step in range(1, _MAX_STEPS + 1):
            action, args = agent.step(observation)
            timestamp = datetime.now(timezone.utc).isoformat()

            if action == "done":
                event = _make_event(step, task.task_id, action, args, "ok", False, timestamp)
                trace.append(event)
                logger.info("step %d  action=done — agent signalled completion", step)
                break

            result, is_library_lookup = _dispatch(
                env, library, action, args, library_enabled=library_enabled
            )

            event = _make_event(
                step, task.task_id, action, args, result, is_library_lookup, timestamp
            )
            trace.append(event)
            logger.info("step %d  action=%r  result=%r", step, action, result)

            observation = {
                "task_id": task.task_id,
                "instructions": task.instructions,
                "allowed_actions": task.allowed_actions,
                "files": env.list_files(),
                "last_action_result": result,
            }
        else:
            logger.warning("run STOPPED — reached max steps (%d)", _MAX_STEPS)

        _write_trace(trace, task.task_id)
        logger.info("run END  task_id=%r  steps=%d", task.task_id, len(trace))
        return trace, working_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dispatch(
    env: STLEnvironment,
    library: ToolLibrary,
    action: str,
    args: dict,
    *,
    library_enabled: bool = True,
) -> tuple[object, bool]:
    """Route *action* to the environment or the tool library.

    Returns ``(result, is_library_lookup)`` where *result* is whatever the
    handler returns and *is_library_lookup* flags whether the action was a
    tool-library query (used for trace annotation).

    When *library_enabled* is False, ``tool_library_search`` and
    ``tool_library_register`` short-circuit with an explanatory error so
    the agent can observe that the library is unavailable.
    """
    is_library_lookup = action in _LIBRARY_LOOKUP_ACTIONS
    try:
        if action == "read_file":
            result = env.read_file(args["path"])
        elif action == "write_file":
            env.write_file(args["path"], args["content"])
            result = "ok"
        elif action == "run_python":
            result = env.run_python(args["path"])
        elif action == "list_files":
            result = env.list_files()
        elif action == "tool_library_search":
            if not library_enabled:
                result = {"error": "tool library disabled for this run"}
            else:
                result = library.search(args.get("tags", []))
        elif action == "tool_library_register":
            if not library_enabled:
                result = {"error": "tool library disabled for this run"}
            else:
                library.register(args["tool_id"], args["code"], args.get("manifest", {}))
                result = "ok"
        else:
            result = {"error": f"unknown action {action!r}"}
    except Exception as exc:  # noqa: BLE001
        result = {"error": str(exc)}

    return result, is_library_lookup


def _make_event(
    step: int,
    task_id: str,
    action: str,
    args: dict,
    result: object,
    tool_library_lookup: bool,
    timestamp: str,
) -> dict:
    return {
        "step": step,
        "task_id": task_id,
        "action": action,
        "args": args,
        "result": result,
        "tool_library_lookup": tool_library_lookup,
        "timestamp": timestamp,
    }


def _write_trace(trace: list[dict], task_id: str) -> Path:
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = _LOGS_DIR / f"{task_id}_{ts}.jsonl"
    with log_path.open("w", encoding="utf-8") as fh:
        for event in trace:
            fh.write(json.dumps(event) + "\n")
    logger.info("trace written to %s", log_path)
    return log_path
