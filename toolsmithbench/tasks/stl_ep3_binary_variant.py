from toolsmithbench.task import TaskSpec, register_task

_INSTRUCTIONS = (
    "You have received STL files from a new supplier (binary_001.stl through "
    "binary_010.stl). Some files may be in binary STL format rather than ASCII. "
    "Validate all files for manufacturing suitability. "
    "Check the tool library before writing anything new — you may be able to "
    "reuse or extend an existing tool. "
    "Your validation tool must accept a single STL file path as a command-line "
    "argument and print a JSON object to stdout: "
    '{"valid": true/false, "issues": [...]}. '
    "It must handle both binary and ASCII STL files correctly."
)

spec = TaskSpec(
    task_id="stl_ep3_binary_variant",
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
        "oracle": "toolsmithbench/oracle/stl_ep3_ground_truth.json",
        "normal_check_required": True,
    },
    episode_sequence="stl_sequence",
    episode_number=3,
    fixtures_dir="toolsmithbench/fixtures/binary",
)

register_task(spec)
