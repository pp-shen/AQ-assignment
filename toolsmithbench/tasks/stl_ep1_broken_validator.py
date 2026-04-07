from toolsmithbench.task import TaskSpec, register_task

_INSTRUCTIONS = (
    "You have been given an STL validation tool at stl_validator_provided.py. "
    "Your job is to validate the provided STL files and identify which ones are "
    "suitable for manufacturing. The tool may have limitations. You may author "
    "new tools, test them, and store them in the tool library."
)

spec = TaskSpec(
    task_id="stl_ep1_broken_validator",
    family="terminal",
    instructions=_INSTRUCTIONS,
    allowed_actions=[
        "write_file",
        "run_python",
        "read_file",
        "list_files",
        "tool_library_register",
        "tool_library_search",
    ],
    tool_library_enabled=True,
    verifier_config={
        "oracle": "toolsmithbench/oracle/stl_ground_truth.json",
        "normal_check_required": True,
    },
    episode_sequence="stl_sequence",
    episode_number=1,
    max_steps=25,
)

register_task(spec)
