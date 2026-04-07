from toolsmithbench.task import TaskSpec, register_task

_INSTRUCTIONS = (
    "You have received a batch of 20 STL files from an external supplier "
    "(supplier_001.stl through supplier_020.stl). "
    "Identify all files that would fail manufacturing validation and produce "
    "a report. Check the tool library before writing anything new. "
    "Write your final results to validation_report.json in the following format: "
    '{"results": [{"file": "supplier_001.stl", "valid": true}, '
    '{"file": "supplier_002.stl", "valid": false, "reason": "inverted_normals"}, ...]}. '
    "Every supplier file must have an entry in the results list."
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
    fixtures_dir="toolsmithbench/fixtures/batch",
)

register_task(spec)
