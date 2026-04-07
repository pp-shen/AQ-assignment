from toolsmithbench.task import TaskSpec, register_task

_INSTRUCTIONS = (
    "You have received a batch of 20 STL files from an external supplier. "
    "Identify all files that would fail manufacturing validation and produce "
    "a report listing which files are invalid and why. "
    "Check the tool library before writing anything new."
)

spec = TaskSpec(
    task_id="stl_ep2_batch_processing",
    family="sequence",
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
        "oracle": "toolsmithbench/oracle/stl_ep2_ground_truth.json",
        "normal_check_required": True,
    },
    episode_sequence="stl_sequence",
    episode_number=2,
)

register_task(spec)
