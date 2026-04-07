from toolsmithbench.task import TaskSpec, register_task

_INSTRUCTIONS = (
    "The provided STL files (repair_001.stl, repair_002.stl, repair_003.stl) have "
    "inverted face normals that will cause manufacturing failures. "
    "Author a repair tool that fixes inverted normals in place by flipping the "
    "declared normal vectors to match the winding order, then validates the result. "
    "Check the tool library before writing anything new. "
    "Your repair tool must accept a single STL file path as a command-line argument, "
    "repair it in place, and print a JSON object to stdout: "
    '{"valid": true/false, "repaired": <int — number of normals flipped>, "issues": [...]}.'
)

spec = TaskSpec(
    task_id="stl_ep6_repair",
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
        "oracle": "toolsmithbench/oracle/stl_ep6_ground_truth.json",
        "normal_check_required": False,
    },
    fixtures_dir="toolsmithbench/fixtures/repair",
)

register_task(spec)
