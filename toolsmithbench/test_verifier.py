import sys
sys.path.insert(0, ".")
from toolsmithbench.verifier import STLVerifier

v = STLVerifier("toolsmithbench/oracle/stl_ground_truth.json")
r = v.verify(
    "toolsmithbench/fixtures/stl_validator_provided.py",
    task_id="stl_ep1_broken_validator",
    tool_authored=True,
    tool_registered=False,
    tool_reused=False,
    steps_taken=1,
)
print(vars(r))
