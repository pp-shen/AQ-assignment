class Agent:
    def step(self, observation: dict) -> tuple[str, dict]:
        """
        Args:
            observation: state the agent sees — instructions, available files, last action result, etc.

        Returns:
            action:  str  — one of the task's allowed_actions, or "done" to signal completion
            args:    dict — arguments for that action, e.g. {"path": "tool.py", "content": "..."}
        """
        return ("done", {})
