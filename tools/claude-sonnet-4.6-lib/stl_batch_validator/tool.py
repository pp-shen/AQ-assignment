"""stl_batch_validator.py — Validate a batch of ASCII STL files for manufacturing.

Fixes the known bug in stl_validator_provided.py:
  - Normal consistency threshold was -0.99 (only catches nearly-perfectly-inverted
    normals). Correct check: if dot(declared_normal, winding_normal) < 0, the
    declared normal is in the wrong hemisphere => inverted.

Checks performed:
  - Inverted normals (dot product of declared vs winding-order normal < 0)
  - Open edges (non-closed / non-manifold mesh)
  - Non-manifold edges (edge shared by >2 faces)
  - Degenerate triangles (zero area)
  - Duplicate faces
  - Malformed / missing geometry

Usage:
  python stl_batch_validator.py  # validates supplier_001.stl .. supplier_020.stl
  # or import and call parse_stl(path) directly
"""
import json
import math
import os


def _cross(a, b):
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )


def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _magnitude(v):
    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)


def _normalize(v):
    m = _magnitude(v)
    if m < 1e-10:
        return v
    return (v[0]/m, v[1]/m, v[2]/m)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def parse_stl(path: str) -> dict:
    issues = []
    issue_types = set()
    triangles = []  # list of (declared_normal, [v1, v2, v3])

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
                issues.append(f"Malformed facet normal at line {i+1}")
                issue_types.add("malformed_facet")
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
                                vertices.append((
                                    float(vparts[1]),
                                    float(vparts[2]),
                                    float(vparts[3])
                                ))
                            except (IndexError, ValueError):
                                issues.append(f"Malformed vertex at line {i+1}")
                                issue_types.add("malformed_vertex")
                        i += 1

            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f"Incomplete triangle near line {i+1}")
                issue_types.add("incomplete_triangle")
            continue

        i += 1

    if not triangles:
        issues.append("No valid faces found")
        issue_types.add("no_faces")
        return {"valid": False, "issues": issues, "issue_types": list(issue_types),
                "triangle_count": 0, "surface_area": 0.0}

    # --- Degenerate triangle check ---
    degenerate_count = 0
    for _, verts in triangles:
        area = _triangle_area(*verts)
        if area < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s)")
        issue_types.add("degenerate_triangles")

    # --- Manifold check: every edge must be shared by exactly 2 triangles ---
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j+1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)

    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) - not a closed solid")
        issue_types.add("open_edges")
    if non_manifold_edges > 0:
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s)")
        issue_types.add("non_manifold_edges")

    # --- Normal consistency check (FIXED: threshold is 0, not -0.99) ---
    # If dot(declared, winding) < 0, the declared normal points opposite to winding
    inverted_count = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # degenerate, skip
        winding_normal = _normalize(cross)
        dn_mag = _magnitude(declared_normal)
        if dn_mag < 1e-10:
            continue  # zero declared normal, skip
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < 0:
            inverted_count += 1

    if inverted_count > 0:
        issues.append(f"{inverted_count} face(s) have inverted normals relative to winding order")
        issue_types.add("inverted_normals")

    # --- Duplicate face check ---
    face_signatures = set()
    duplicate_count = 0
    for _, verts in triangles:
        rounded = tuple(sorted([tuple(round(x, 6) for x in v) for v in verts]))
        if rounded in face_signatures:
            duplicate_count += 1
        else:
            face_signatures.add(rounded)
    if duplicate_count > 0:
        issues.append(f"{duplicate_count} duplicate face(s) detected")
        issue_types.add("duplicate_faces")

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
        "issue_types": list(issue_types),
    }


def pick_reason(issue_types):
    """Pick a single canonical reason string from the set of issue types."""
    priority = [
        "inverted_normals",
        "open_edges",
        "non_manifold_edges",
        "degenerate_triangles",
        "duplicate_faces",
        "no_faces",
        "malformed_facet",
        "malformed_vertex",
        "incomplete_triangle",
    ]
    for p in priority:
        if p in issue_types:
            return p
    return issue_types[0] if issue_types else "unknown"


def main():
    results = []
    files = [f"supplier_{i:03d}.stl" for i in range(1, 21)]

    for fname in files:
        path = fname
        if not os.path.exists(path):
            results.append({"file": fname, "valid": False, "reason": "file_not_found"})
            continue

        result = parse_stl(path)
        print(f"{fname}: valid={result['valid']}, triangles={result['triangle_count']}, "
              f"area={result['surface_area']}, issues={result['issues']}")

        if result["valid"]:
            results.append({"file": fname, "valid": True})
        else:
            reason = pick_reason(result["issue_types"])
            results.append({"file": fname, "valid": False, "reason": reason})

    report = {"results": results}
    with open("validation_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\nReport written to validation_report.json")


if __name__ == "__main__":
    main()
