import sys
sys.path.insert(0, ".")
from toolsmithbench.envs.stl_env import STLEnvironment

env = STLEnvironment("/tmp/stl_test")
assert "stl_validator_provided.py" in env.list_files()
env.write_file("hello.py", "print('ok')")
assert env.read_file("hello.py") == "print('ok')"
assert env.run_python("hello.py")["stdout"].strip() == "ok"
print("STLEnvironment checks passed.")