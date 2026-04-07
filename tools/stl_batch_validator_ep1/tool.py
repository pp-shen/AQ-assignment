"""STL Batch Validator - checks manifold closure, inverted normals, degenerate triangles."""
import json
import math
import glob


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

def _magnitude(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)

def _normalize(v):
    m = _magnitude(v)
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)

def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def parse_stl(path: str) -> dict:
    issues = []
    issue_types = set()
    triangles = []

    with open(path, "r", errors="replace") as fh:
        lines = fh.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("facet normal"):
            parts = line.split()
            try:
                declared_normal = (float(parts[2]), float(parts[3]), float(parts[4]))
            except (IndexError, ValueError):
                issues.append(f"Malformed facet normal at line {i + 1}")
                issue_types.add("malformed")
                i += 1
                continue

            vertices = []
            i += 1
            if i < len(lines) and lines[i].strip() == "outer loop":
                i += 1
                for _ in range(3):
                    if i < len(lines):
                        vline = lines[i].strip()
                        if vline.startswith("vertex"):
                            vparts = vline.split()
                            try:
                                vertices.append(
                                    (float(vparts[1]), float(vparts[2]), float(vparts[3]))
                                )
                            except (IndexError, ValueError):
                                issues.append(f"Malformed vertex at line {i + 1}")
                                issue_types.add("malformed")
                        i += 1

            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f"Incomplete triangle near line {i + 1}")
                issue_types.add("malformed")
            continue
        i += 1

    if not triangles:
        issues.append("No valid faces found")
        issue_types.add("no_faces")

    # Manifold check
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1
    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) - not a closed solid")
        issue_types.add("open_edges")

    # Degenerate triangle check
    degenerate_count = sum(1 for _, verts in triangles if _triangle_area(*verts) < 1e-12)
    if degenerate_count > 0:
        issues.append(f"Mesh has {degenerate_count} degenerate (zero-area) triangle(s)")
        issue_types.add("degenerate_triangles")

    # Inverted normal check (winding order)
    inverted_count = 0
    for declared_normal, verts in triangles:
        computed = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        if _magnitude(computed) < 1e-12:
            continue
        if _magnitude(declared_normal) < 1e-12:
            continue
        if _dot(_normalize(computed), _normalize(declared_normal)) < 0.0:
            inverted_count += 1
    if inverted_count > 0:
        issues.append(f"Mesh has {inverted_count} inverted normal(s)")
        issue_types.add("inverted_normals")

    return {
        "valid": len(issues) == 0,
        "triangle_count": len(triangles),
        "surface_area": round(sum(_triangle_area(*v) for _, v in triangles), 6),
        "issues": issues,
        "issue_types": list(issue_types),
    }


def classify_reason(issue_types):
    for reason in ["inverted_normals", "open_edges", "degenerate_triangles", "no_faces", "malformed"]:
        if reason in issue_types:
            return reason
    return "unknown"


def main():
    files = sorted(glob.glob("supplier_*.stl"))
    results = []
    for stl_file in files:
        result = parse_stl(stl_file)
        entry = {"file": stl_file, "valid": result["valid"]}
        if not result["valid"]:
            issue_types = result["issue_types"]
            entry["reason"] = ", ".join(sorted(issue_types)) if len(issue_types) > 1 else classify_reason(issue_types)
        results.append(entry)
        status = "PASS" if result["valid"] else f"FAIL ({entry.get('reason', '')})"
        print(f"{stl_file}: {status}")
        for issue in result["issues"]:
            print(f"  - {issue}")

    with open("validation_report.json", "w") as f:
        json.dump({"results": results}, f, indent=2)
    print(f"\nReport written to validation_report.json")
    print(f"Valid: {sum(1 for r in results if r['valid'])}/{len(results)}")


if __name__ == "__main__":
    main()
