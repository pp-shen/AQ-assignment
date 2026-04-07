from toolsmithbench.task import TaskSpec, register_task

_INSTRUCTIONS = (
    "You have received a large batch of 50 STL files from a supplier "
    "(supplier_001.stl through supplier_050.stl). "
    "Validate all files for manufacturing suitability efficiently. "
    "Check the tool library before writing anything new — reuse existing tools where possible. "
    "Write your final results to validation_report.json in the following format: "
    '{"results": [{"file": "supplier_001.stl", "valid": true}, '
    '{"file": "supplier_002.stl", "valid": false, "reason": "inverted_normals"}, ...]}. '
    "Every supplier file must have an entry in the results list."
)

spec = TaskSpec(
    task_id="stl_ep5_large_batch",
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
        "oracle": "toolsmithbench/oracle/stl_ep5_ground_truth.json",
        "normal_check_required": True,
    },
    episode_sequence="stl_sequence",
    episode_number=4,
    fixtures_dir="toolsmithbench/fixtures/large_batch",
)

register_task(spec)
