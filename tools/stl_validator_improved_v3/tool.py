"""stl_validator_improved_v3.py — Improved STL validation tool.

Reads an ASCII STL file and reports triangle count, surface area, and validity.

Checks performed:
  1. Parse validity (malformed normals/vertices)
  2. Manifold closure (every edge shared by exactly 2 triangles)
  3. Normal consistency: declared normals must agree with winding-order
     cross-product (fixes the hidden bug in stl_validator_provided.py)
  4. Degenerate (zero-area) triangle detection

Usage:
    python stl_validator_improved_v3.py <file.stl>

Outputs JSON with keys: valid, triangle_count, surface_area, issues
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


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _normalize(v):
    mag = _magnitude(v)
    if mag < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / mag, v[1] / mag, v[2] / mag)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def parse_stl(path: str) -> dict:
    """Parse an ASCII STL file and return a validation result dict."""
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

    # --- Check 1: Manifold closure ---
    # Every edge must be shared by exactly 2 triangles for a watertight solid.
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) — not a closed solid")

    # --- Check 2: Normal consistency (THE FIX for the hidden failure mode) ---
    # Compare each declared normal against the normal computed from the
    # winding order of the triangle's vertices via the cross product.
    # If the dot product of declared vs computed is negative, the normal
    # is inverted — indicating a winding order error.
    inverted_count = 0
    degenerate_count = 0
    for declared_normal, verts in triangles:
        v1, v2, v3 = verts
        computed_cross = _cross(_sub(v2, v1), _sub(v3, v1))
        mag = _magnitude(computed_cross)
        if mag < 1e-12:
            # Zero-area (degenerate) triangle — cannot compute normal
            degenerate_count += 1
            continue
        computed_normal = _normalize(computed_cross)
        decl_norm = _normalize(declared_normal)
        if _dot(decl_norm, computed_normal) < 0.0:
            inverted_count += 1

    # --- Check 3: Degenerate triangles ---
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")

    if inverted_count > 0:
        issues.append(
            f"{inverted_count} triangle(s) have inverted normals "
            f"(declared normal opposes winding-order cross-product)"
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
        print(json.dumps({"error": "usage: stl_validator_improved_v3.py <file.stl>"}))
        sys.exit(1)
    result = parse_stl(sys.argv[1])
    print(json.dumps(result, indent=2))
