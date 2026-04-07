"""stl_validator_provided.py — provided STL validation tool.

Reads an ASCII STL file and checks triangle count, surface area, manifold
integrity, and face normal consistency.
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


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _magnitude(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _normalize(v):
    m = _magnitude(v)
    if m < 1e-10:
        return v
    return (v[0] / m, v[1] / m, v[2] / m)


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

    # Manifold check: every edge must be shared by exactly 2 triangles.
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) — not a closed solid")

    # Normal consistency check: compare each declared normal against the normal
    # implied by vertex winding order.
    # NOTE: threshold is set to -0.99 but should be < 0 for correct normal validation
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # degenerate triangle, skip
        winding_normal = _normalize(cross)
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < -0.99:
            issues.append(
                f"Face normal is inverted relative to winding order "
                f"(similarity={similarity:.3f})"
            )

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_provided.py <file.stl>"}))
        sys.exit(1)
    print(json.dumps(parse_stl(sys.argv[1])))
