import sys
sys.path.insert(0, ".")
from toolsmithbench.tasks import stl_ep1_broken_validator
from toolsmithbench.task import get_task
from toolsmithbench.runner import Runner
from toolsmithbench.agents.stub_agent import Agent

task = get_task("stl_ep1_broken_validator")
agent = Agent()
runner = Runner()
trace, working_dir = runner.run(task, agent)

print(f"Steps taken: {len(trace)}")
print(f"Working dir: {working_dir}")
print(f"Last event: {trace[-1]}")