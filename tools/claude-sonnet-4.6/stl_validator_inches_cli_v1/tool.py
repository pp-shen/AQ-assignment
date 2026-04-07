"""stl_validator_inches_cli.py

Validates an ASCII or binary STL file whose coordinates are in inches.
Reports surface area converted to mm² (1 in² = 645.16 mm²).

Usage:
    python stl_validator_inches_cli.py <file.stl>

Outputs a JSON object to stdout:
    {"valid": true/false, "surface_area_mm2": <float>, "issues": [...]}
"""
import json
import math
import struct
import sys

# Conversion factor: 1 inch = 25.4 mm  =>  1 in² = 25.4² mm²
IN2_TO_MM2 = 25.4 ** 2  # 645.16


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _is_binary_stl(path):
    """Return True if the file looks like a binary STL."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(80)
            if len(header) < 80:
                return False
            count_bytes = fh.read(4)
            if len(count_bytes) < 4:
                return False
            count = struct.unpack("<I", count_bytes)[0]
            expected_size = 80 + 4 + count * 50
            fh.seek(0, 2)
            actual_size = fh.tell()
            return actual_size == expected_size
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_ascii(path):
    issues = []
    triangles = []  # list of (declared_normal_tuple, [v1, v2, v3])

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

    return triangles, issues


def _parse_binary(path):
    issues = []
    triangles = []

    with open(path, "rb") as fh:
        fh.read(80)  # header
        count_bytes = fh.read(4)
        if len(count_bytes) < 4:
            issues.append("Binary STL too short to read triangle count")
            return triangles, issues
        count = struct.unpack("<I", count_bytes)[0]

        for idx in range(count):
            data = fh.read(50)
            if len(data) < 50:
                issues.append(f"Unexpected EOF reading triangle {idx + 1}")
                break
            vals = struct.unpack("<12fH", data)
            declared_normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((declared_normal, [v1, v2, v3]))

    return triangles, issues


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(path):
    issues = []

    # Parse
    if _is_binary_stl(path):
        triangles, parse_issues = _parse_binary(path)
    else:
        triangles, parse_issues = _parse_ascii(path)
    issues.extend(parse_issues)

    if not triangles:
        issues.append("No valid faces found")
        return {
            "valid": False,
            "surface_area_mm2": 0.0,
            "issues": issues,
        }

    # --- Manifold check: every edge must appear exactly twice ---
    edge_count = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) — not a closed solid")
    if non_manifold_edges > 0:
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s)")

    # --- Degenerate triangle check ---
    degenerate_count = 0
    for _, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate triangle(s) (zero area)")

    # --- Duplicate face check ---
    face_set = set()
    duplicate_count = 0
    for _, verts in triangles:
        rounded = frozenset(tuple(round(x, 6) for x in v) for v in verts)
        if rounded in face_set:
            duplicate_count += 1
        else:
            face_set.add(rounded)
    if duplicate_count > 0:
        issues.append(f"{duplicate_count} duplicate face(s)")

    # --- Normal consistency check ---
    # Flag normals that point in the wrong hemisphere (dot < 0) relative to
    # the winding-order normal. Near-zero declared normals (0,0,0) are skipped.
    inverted_count = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # degenerate, skip
        if _magnitude(declared_normal) < 1e-10:
            continue  # no declared normal to check
        winding_normal = _normalize(cross)
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < -0.5:  # clearly in the wrong hemisphere
            inverted_count += 1
    if inverted_count > 0:
        issues.append(f"{inverted_count} face(s) with normals inverted relative to winding order")

    # --- Surface area (in inches²) → convert to mm² ---
    surface_area_in2 = sum(_triangle_area(*verts) for _, verts in triangles)
    surface_area_mm2 = surface_area_in2 * IN2_TO_MM2

    return {
        "valid": len(issues) == 0,
        "surface_area_mm2": round(surface_area_mm2, 4),
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_inches_cli.py <file.stl>"}))
        sys.exit(1)
    result = validate(sys.argv[1])
    print(json.dumps(result))
