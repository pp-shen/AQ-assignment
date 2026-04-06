from __future__ import annotations

import logging
from datetime import datetime, timezone

from toolsmithbench.task import TaskSpec

logger = logging.getLogger(__name__)


class Runner:
    """Main agent execution loop.

    Responsibilities (full implementation):
    - Load a task by ID from the registry
    - Initialize the correct environment for that task
    - Feed the agent the task instructions
    - Route agent actions through the environment
    - Append structured events to the trace log on each step
    - Detect when the agent signals completion
    - Hand off to the verifier with the agent's output and full trace

    The runner does NOT score anything — it never sees ground truth.
    """

    def run(self, task: TaskSpec, agent) -> None:
        """Execute a single task with the given agent.

        Args:
            task:  A TaskSpec describing the benchmark task.
            agent: An agent object (interface TBD in later implementation).
        """
        start_ts = datetime.now(timezone.utc).isoformat()
        logger.info(
            "Runner.run START  task_id=%r  agent=%r  ts=%s",
            task.task_id,
            repr(agent),
            start_ts,
        )

        # TODO: initialize environment for task.family
        # TODO: feed agent task.instructions and task.allowed_actions
        # TODO: step loop — route actions, append trace events, detect completion
        # TODO: hand off trace + working directory to verifier

        end_ts = datetime.now(timezone.utc).isoformat()
        logger.info(
            "Runner.run END    task_id=%r  agent=%r  ts=%s",
            task.task_id,
            repr(agent),
            end_ts,
        )
