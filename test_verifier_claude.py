import sys
sys.path.insert(0, ".")

import toolsmithbench.tasks.stl_ep1_broken_validator
from toolsmithbench.task import get_task
from toolsmithbench.runner import Runner
from toolsmithbench.verifier import STLVerifier
from toolsmithbench.agents.claude_agent import ClaudeAgent

task = get_task("stl_ep1_broken_validator")
trace, working_dir = Runner().run(task, ClaudeAgent())

# Find the agent-authored tool — any .py the agent wrote besides the provided validator.
authored = [p for p in working_dir.glob("*.py") if p.name != "stl_validator_provided.py"]
tool_path = authored[0] if authored else working_dir / "stl_validator_provided.py"

result = STLVerifier("toolsmithbench/oracle/stl_ground_truth.json").verify(
    tool_path,
    task_id=task.task_id,
    tool_authored=bool(authored),
    tool_registered=any(e["action"] == "tool_library_register" for e in trace),
    tool_reused=any(e["tool_library_lookup"] for e in trace),
    steps_taken=len(trace),
)
print(vars(result))
