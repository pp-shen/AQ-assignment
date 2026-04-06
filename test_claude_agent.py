import sys
sys.path.insert(0, ".")
import logging
logging.basicConfig(level=logging.WARNING)


from toolsmithbench.tasks import stl_ep1_broken_validator
from toolsmithbench.task import get_task
from toolsmithbench.runner import Runner
from toolsmithbench.agents.claude_agent import ClaudeAgent

task = get_task("stl_ep1_broken_validator")
agent = ClaudeAgent()
runner = Runner()
trace, working_dir = runner.run(task, agent)

print(f"Steps taken: {len(trace)}")
print(f"Working dir: {working_dir}")
for event in trace:
    print(f"  Step {event['step']}: {event['action']}")