from __future__ import annotations

import json
import logging
import os

import urllib.request

logger = logging.getLogger(__name__)

_URL = "https://openrouter.ai/api/v1/chat/completions"

# OpenRouter model ID for Claude Sonnet 4.6.
_MODEL = "anthropic/claude-sonnet-4.6"

_SYSTEM_PROMPT = """\
CRITICAL: Your response must be a single JSON object and nothing else. Do not write any text before or after the JSON. Do not explain what you are doing. Just output the JSON.

You are a tool-authoring agent running inside a benchmark harness for STL geometry validation.

Your job:
1. Investigate the provided STL validation tool for correctness problems.
2. Author improved tools when you find gaps.
3. Test every tool you write before registering it.
4. Store working tools in the persistent tool library so they can be reused later.

## Response format

Respond with ONLY a single JSON object — no prose, no markdown fences, no explanation.

{"action": "<action_name>", "args": {<args_object>}}

## Available actions

read_file
  Read a file from the working directory.
  args: {"path": "filename.py"}

write_file
  Write a file to the working directory (how you author tools).
  args: {"path": "filename.py", "content": "...source code..."}

run_python
  Execute a Python file and return stdout, stderr, and returncode.
  args: {"path": "filename.py"}

list_files
  List all files in the working directory.
  args: {}

tool_library_search
  Search the persistent tool library by tags. Always do this before writing a new tool.
  args: {"tags": ["stl", "validation"]}

tool_library_register
  Store an authored tool in the persistent library.
  args: {
    "tool_id": "snake_case_unique_id",
    "code": "...full source code...",
    "manifest": {
      "name": "Human Readable Name",
      "description": "One-sentence description of what it does",
      "tags": ["stl", "validation"],
      "authored_in_task": "stl_ep1_broken_validator"
    }
  }

done
  Signal that you have completed the task.
  args: {}

## Rules

- Always call tool_library_search before writing a new tool.
- Test tools with run_python before registering them.
- Respond with ONLY the JSON object — nothing else.
"""


class ClaudeAgent:
    """Benchmark agent that calls OpenRouter via direct HTTP POST.

    Maintains full conversation history across steps so the model has
    context of every prior action and result.
    """

    def __init__(self, model: str = _MODEL) -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENROUTER_API_KEY environment variable is not set")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._model = model
        self._history: list[dict] = []

    def reset(self) -> None:
        """Clear conversation history. Call between episodes to prevent cross-contamination."""
        self._history = []

    def step(self, observation: dict) -> tuple[str, dict]:
        """Send the current observation to the model and parse its (action, args) response.

        Args:
            observation: dict from the runner — contains instructions,
                         allowed_actions, files, and last_action_result.

        Returns:
            (action, args) — action is a string, args is a dict.
            Falls back to ("done", {}) on any API or parse failure.
        """
        self._history.append(
            {"role": "user", "content": _format_observation(observation)}
        )

        messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + self._history

        try:
            body = json.dumps(
                {"model": self._model, "max_tokens": 4096, "messages": messages}
            ).encode()
            req = urllib.request.Request(_URL, data=body, headers=self._headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = json.loads(resp.read())["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.error("API call failed: %s", exc)
            return ("done", {})

        self._history.append({"role": "assistant", "content": raw})

        action, args = _parse_response(raw)
        logger.info("action=%r  args_keys=%s", action, list(args.keys()))
        return action, args


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_observation(obs: dict) -> str:
    """Render a runner observation as a user message."""
    parts: list[str] = []

    if obs.get("last_action_result") is None:
        # First step: show full task briefing.
        parts.append(f"## Task\n{obs['instructions']}")
        parts.append(f"\nAllowed actions: {', '.join(obs['allowed_actions'])}")
        files = obs.get("files", [])
        parts.append("\nFiles in working directory:\n" + "\n".join(f"  {f}" for f in files))
    else:
        # Subsequent steps: show the result of the last action.
        result = obs["last_action_result"]
        parts.append("Action result:\n" + json.dumps(result, indent=2))
        files = obs.get("files", [])
        parts.append("\nFiles in working directory:\n" + "\n".join(f"  {f}" for f in files))

    return "\n".join(parts)


def _parse_response(raw: str) -> tuple[str, dict]:
    """Parse the model's JSON response into (action, args).

    Uses brace-counting to extract the first complete {...} block from the
    response text, so leading prose, markdown fences, or trailing commentary
    don't cause a parse failure.
    Falls back to ("done", {}) if no valid JSON object is found.
    """
    start = raw.find("{")
    if start == -1:
        logger.warning("No JSON object found in model response:\n%s", raw)
        return ("done", {})

    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(raw[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = raw[start : i + 1]
                try:
                    data = json.loads(candidate)
                    action = str(data.get("action", "done"))
                    args = data.get("args", {})
                    if not isinstance(args, dict):
                        args = {}
                    return action, args
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Extracted JSON block failed to parse: %s\nBlock: %s", exc, candidate
                    )
                    return ("done", {})

    logger.warning("Unmatched braces in model response:\n%s", raw)
    return ("done", {})
