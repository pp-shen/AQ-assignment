from toolsmithbench.task import TaskSpec, register_task

_INSTRUCTIONS = (
    "Validate these STL files and report surface areas in mm². "
    "Note: the provided tool may have unit issues — it reports surface area in mm² "
    "but assumes the coordinate values are already in mm. "
    "These files use inches (1 inch = 25.4 mm, so 1 in² = 645.16 mm²). "
    "Check the tool library before writing anything new. "
    "Your tool must accept a single STL file path as a command-line argument and "
    'print a JSON object to stdout: {"valid": true/false, "surface_area_mm2": <float>, "issues": [...]}.'
)

spec = TaskSpec(
    task_id="stl_ep4_unit_conversion",
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
        "oracle": "toolsmithbench/oracle/stl_ep4_ground_truth.json",
        "normal_check_required": True,
    },
    fixtures_dir="toolsmithbench/fixtures/units",
)

register_task(spec)
