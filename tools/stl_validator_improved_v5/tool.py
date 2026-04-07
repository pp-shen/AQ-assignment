"""stl_validator_improved.py

Improved ASCII STL validator that fixes the hidden failure mode in
stl_validator_provided.py: it now checks whether declared face normals
are consistent with the normal implied by the vertex winding order.

Also checks:
  - Manifold closure (every edge shared by exactly 2 triangles)
  - Degenerate (zero-area) triangles
  - Malformed / incomplete geometry

Usage:
    python stl_validator_improved.py <file.stl>

Outputs JSON: {valid, triangle_count, surface_area, issues}
"""
import json
import math
import sys

NORMAL_ANGLE_THRESHOLD_DEG = 90.0  # flag if angle between declared and computed > 90 deg


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


def _compute_normal(v1, v2, v3):
    """Return the unit normal from winding order (right-hand rule)."""
    return _normalize(_cross(_sub(v2, v1), _sub(v3, v1)))


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
                                    (
                                        float(vparts[1]),
                                        float(vparts[2]),
                                        float(vparts[3]),
                                    )
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

    # ------------------------------------------------------------------ #
    # 1. Degenerate triangle check
    # ------------------------------------------------------------------ #
    degenerate_count = 0
    for idx, (_, verts) in enumerate(triangles):
        area = _triangle_area(*verts)
        if area < 1e-12:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")

    # ------------------------------------------------------------------ #
    # 2. Manifold check: every edge must be shared by exactly 2 triangles
    # ------------------------------------------------------------------ #
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) -- not a closed solid")
    if non_manifold_edges > 0:
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s)")

    # ------------------------------------------------------------------ #
    # 3. Normal consistency check (THE FIX for the hidden failure mode)
    # ------------------------------------------------------------------ #
    inverted_count = 0
    for tri_idx, (declared_normal, verts) in enumerate(triangles):
        area = _triangle_area(*verts)
        if area < 1e-12:
            continue  # skip degenerate; already reported

        computed_normal = _compute_normal(*verts)
        dn_mag = _magnitude(declared_normal)

        if dn_mag < 1e-12:
            # declared normal is (0,0,0) -- treat as don't-care per STL spec
            continue

        dn_unit = _normalize(declared_normal)
        dot = _dot(dn_unit, computed_normal)
        # dot < 0 means angle > 90 deg => inverted
        if dot < 0:
            inverted_count += 1

    if inverted_count > 0:
        issues.append(
            f"{inverted_count} triangle(s) have inverted normals "
            f"(declared normal opposes winding-order normal)"
        )

    # ------------------------------------------------------------------ #
    # Surface area
    # ------------------------------------------------------------------ #
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
    result = parse_stl(sys.argv[1])
    print(json.dumps(result, indent=2))
