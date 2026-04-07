"""stl_validator_improved.py — Improved STL validation tool.

Validates ASCII STL files for:
- Manifold integrity (open edges)
- Degenerate triangles (zero area)
- Duplicate faces
- Normal consistency (declared normals vs. vertex winding order)
"""
import json
import math
import sys


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _magnitude(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _normalize(v):
    m = _magnitude(v)
    if m < 1e-10:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def parse_stl(path: str) -> dict:
    issues = []
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
                issues.append(f"Malformed facet normal at line {i + 1}")
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
                        i += 1

            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f"Incomplete triangle near line {i + 1}")
            continue

        i += 1

    if not triangles:
        issues.append("No valid faces found")
        return {
            "valid": False,
            "triangle_count": 0,
            "surface_area": 0.0,
            "issues": issues,
        }

    # --- Manifold check: every edge must be shared by exactly 2 triangles ---
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    if open_edges > 0:
        issues.append(f"open_edges: Mesh has {open_edges} open edge(s) — not a closed solid")

    # --- Degenerate triangle check ---
    degenerate_count = 0
    for _, verts in triangles:
        area = _triangle_area(*verts)
        if area < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"degenerate_triangles: {degenerate_count} degenerate (zero-area) triangle(s)")

    # --- Duplicate face check ---
    face_set = set()
    duplicate_count = 0
    for _, verts in triangles:
        rounded = tuple(sorted([tuple(round(x, 6) for x in v) for v in verts]))
        if rounded in face_set:
            duplicate_count += 1
        else:
            face_set.add(rounded)
    if duplicate_count > 0:
        issues.append(f"duplicate_faces: {duplicate_count} duplicate face(s)")

    # --- Normal consistency check ---
    inverted_count = 0
    for declared_normal, verts in triangles:
        computed = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        mag = _magnitude(computed)
        if mag < 1e-10:
            continue  # degenerate, skip
        computed_norm = _normalize(computed)
        declared_norm = _normalize(declared_normal)
        # If dot product is negative, the declared normal points opposite to winding order
        if _dot(computed_norm, declared_norm) < -0.5:
            inverted_count += 1
    if inverted_count > 0:
        issues.append(f"inverted_normals: {inverted_count} face(s) have normals inconsistent with winding order")

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_improved.py <file.stl>"}))
        sys.exit(1)
    print(json.dumps(parse_stl(sys.argv[1])))
