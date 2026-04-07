import sys
sys.path.insert(0, ".")
import logging
logging.basicConfig(level=logging.WARNING)

import toolsmithbench.tasks.stl_ep1_broken_validator
import toolsmithbench.tasks.stl_ep2_batch_processing
from toolsmithbench.task import get_task
from toolsmithbench.runner import Runner
from toolsmithbench.agents.claude_agent import ClaudeAgent

# Run episode 1 first so the tool gets registered
print("=== Episode 1 ===")
task1 = get_task("stl_ep1_broken_validator")
trace1, wd1 = Runner().run(task1, ClaudeAgent())
print(f"Steps taken: {len(trace1)}")
for event in trace1:
    print(f"  Step {event['step']}: {event['action']}")

# Run episode 2 — should reuse the tool from episode 1
print("\n=== Episode 2 ===")
task2 = get_task("stl_ep2_batch_processing")
trace2, wd2 = Runner().run(task2, ClaudeAgent())
print(f"Steps taken: {len(trace2)}")
for event in trace2:
    print(f"  Step {event['step']}: {event['action']}")

print(f"\nAmortization: ep1={len(trace1)} steps, ep2={len(trace2)} steps")