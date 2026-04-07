"""stl_validator_improved.py — improved STL validation tool.

Supports both ASCII and binary STL files. Checks:
- Triangle count
- Surface area
- Manifold integrity (open edges and non-manifold edges)
- Face normal consistency (correct threshold)
- Degenerate (zero-area) triangles
- Duplicate triangles
"""
import json
import math
import struct
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


def _is_binary_stl(path: str) -> bool:
    """Heuristic: binary STL starts with 80-byte header + 4-byte count."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(80)
            if len(header) < 80:
                return False
            count_bytes = fh.read(4)
            if len(count_bytes) < 4:
                return False
            count = struct.unpack("<I", count_bytes)[0]
            # Each triangle is 50 bytes in binary STL
            fh.seek(0, 2)  # seek to end
            file_size = fh.tell()
            expected_size = 84 + count * 50
            # If sizes match, it's binary; also check it doesn't start with 'solid'
            if header[:5] == b"solid":
                # Could still be binary — check size
                if file_size == expected_size and count > 0:
                    return True
                return False
            return file_size == expected_size and count > 0
    except Exception:
        return False


def _parse_binary_stl(path: str):
    """Parse binary STL. Returns (triangles, issues) where triangles is
    list of (declared_normal, [v1, v2, v3])."""
    issues = []
    triangles = []
    with open(path, "rb") as fh:
        fh.read(80)  # skip header
        count_bytes = fh.read(4)
        if len(count_bytes) < 4:
            issues.append("Binary STL: could not read triangle count")
            return triangles, issues
        count = struct.unpack("<I", count_bytes)[0]
        for idx in range(count):
            data = fh.read(50)
            if len(data) < 50:
                issues.append(f"Binary STL: truncated at triangle {idx}")
                break
            vals = struct.unpack("<12fH", data)
            nx, ny, nz = vals[0], vals[1], vals[2]
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append(((nx, ny, nz), [v1, v2, v3]))
    return triangles, issues


def _parse_ascii_stl(path: str):
    """Parse ASCII STL. Returns (triangles, issues)."""
    issues = []
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

    return triangles, issues


def validate_stl(path: str) -> dict:
    """Validate an STL file (ASCII or binary). Returns a dict with:
    - valid (bool)
    - format (str: 'ascii' or 'binary')
    - triangle_count (int)
    - surface_area (float)
    - issues (list of str)
    """
    issues = []

    # Parse
    if _is_binary_stl(path):
        triangles, parse_issues = _parse_binary_stl(path)
        fmt = "binary"
    else:
        triangles, parse_issues = _parse_ascii_stl(path)
        fmt = "ascii"
    issues.extend(parse_issues)

    if not triangles:
        issues.append("No valid faces found")
        return {
            "valid": False,
            "format": fmt,
            "triangle_count": 0,
            "surface_area": 0.0,
            "issues": issues,
        }

    # Degenerate triangle check
    degenerate_count = 0
    for idx, (_, verts) in enumerate(triangles):
        area = _triangle_area(*verts)
        if area < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(
            f"Mesh has {degenerate_count} degenerate (zero-area) triangle(s)"
        )

    # Duplicate face check
    face_set = set()
    duplicate_count = 0
    for _, verts in triangles:
        rounded = tuple(sorted(tuple(round(x, 6) for x in v) for v in verts))
        if rounded in face_set:
            duplicate_count += 1
        else:
            face_set.add(rounded)
    if duplicate_count > 0:
        issues.append(f"Mesh has {duplicate_count} duplicate triangle(s)")

    # Manifold check: every edge must be shared by exactly 2 triangles.
    edge_count: dict = {}
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
        issues.append(
            f"Mesh has {non_manifold_edges} non-manifold edge(s) (shared by >2 triangles)"
        )

    # Normal consistency check
    # FIX over provided validator: threshold is 0.0 (flag any opposing normal)
    # The provided validator used -0.99, catching only perfectly-inverted normals.
    inverted_normals = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # degenerate triangle, skip
        winding_normal = _normalize(cross)
        dn_mag = _magnitude(declared_normal)
        if dn_mag < 1e-10:
            continue  # zero declared normal, skip
        similarity = _dot(_normalize(declared_normal), winding_normal)
        # Flag if declared normal is pointing in roughly opposite direction
        if similarity < 0.0:
            inverted_normals += 1

    if inverted_normals > 0:
        issues.append(
            f"Mesh has {inverted_normals} face(s) with normals inverted relative to "
            f"winding order"
        )

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "format": fmt,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_improved.py <file.stl>"}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result, indent=2))
