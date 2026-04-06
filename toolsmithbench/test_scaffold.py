import sys
sys.path.insert(0, ".")
from pathlib import Path
from toolsmithbench.task import TaskSpec
from toolsmithbench.tool_library import ToolLibrary
from toolsmithbench.runner import Runner

# Test 1: can you instantiate a TaskSpec
task = TaskSpec(
    task_id="test_task",
    family="terminal",
    instructions="Do something",
    allowed_actions=["write_file", "run_python"],
    tool_library_enabled=True,
    verifier_config={},
    episode_sequence=None,
    episode_number=None
)
print(f"TaskSpec created: {task.task_id}")

# Test 2: can you register and retrieve a tool
library = ToolLibrary(tools_dir=Path("tools/"))
library.register(
    tool_id="test_tool",
    code="def hello(): return 'hello'",
    manifest={
        "tool_id": "test_tool",
        "name": "Test Tool",
        "description": "Just a test",
        "tags": ["test"],
        "authored_in_task": "test_task"
    }
)
result = library.lookup("test_tool")
assert result is not None, "lookup failed"
print(f"Tool registered and retrieved: {result['manifest']['name']}")

# Test 3: can the runner load a task without crashing
runner = Runner()
print("Runner instantiated OK")

print("\nAll scaffold checks passed.")