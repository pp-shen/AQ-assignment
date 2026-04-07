"""stl_validator.py — Validates both ASCII and binary STL files for manufacturing suitability.

Checks:
  - Correct parsing of ASCII and binary STL formats
  - Triangle count (at least 4 for a closed solid)
  - Manifold integrity (every edge shared by exactly 2 triangles)
  - Face normal consistency (declared normal vs winding order)
  - Degenerate triangles (zero-area faces)
  - Duplicate faces

Usage: python stl_validator.py <file.stl>
Output: JSON {"valid": true/false, "issues": [...]}
"""

import json
import math
import struct
import sys


# ---------- vector helpers ----------

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


# ---------- format detection ----------

def _is_binary_stl(path: str) -> bool:
    """Detect binary STL by reading the header and triangle count,
    then checking that the file size matches the expected binary size.
    Falls back to False (ASCII) if the file starts with 'solid' AND
    the binary size doesn't match."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(80)
            if len(header) < 80:
                return False
            count_bytes = fh.read(4)
            if len(count_bytes) < 4:
                return False
            triangle_count = struct.unpack("<I", count_bytes)[0]
            # Binary STL size: 80 (header) + 4 (count) + 50*n (triangles)
            import os
            file_size = os.path.getsize(path)
            expected_size = 80 + 4 + 50 * triangle_count
            if file_size == expected_size and triangle_count > 0:
                return True
            # If it starts with 'solid', it's likely ASCII
            if header.lstrip().startswith(b'solid'):
                return False
            # Otherwise treat as binary
            return True
    except Exception:
        return False


# ---------- parsers ----------

def _parse_binary(path: str):
    """Parse a binary STL file. Returns (triangles, issues).
    triangles: list of (declared_normal, [v1, v2, v3])
    """
    issues = []
    triangles = []

    with open(path, "rb") as fh:
        header = fh.read(80)  # skip header
        count_data = fh.read(4)
        if len(count_data) < 4:
            issues.append("Binary STL: file too short to contain triangle count")
            return triangles, issues

        triangle_count = struct.unpack("<I", count_data)[0]

        for i in range(triangle_count):
            data = fh.read(50)  # 12 floats * 4 bytes + 2 attribute bytes
            if len(data) < 50:
                issues.append(f"Binary STL: unexpected end of file at triangle {i + 1}")
                break
            values = struct.unpack("<12fH", data)
            nx, ny, nz = values[0], values[1], values[2]
            v1 = (values[3], values[4], values[5])
            v2 = (values[6], values[7], values[8])
            v3 = (values[9], values[10], values[11])
            declared_normal = (nx, ny, nz)
            triangles.append((declared_normal, [v1, v2, v3]))

    if not triangles:
        issues.append("No valid faces found")

    return triangles, issues


def _parse_ascii(path: str):
    """Parse an ASCII STL file. Returns (triangles, issues).
    triangles: list of (declared_normal, [v1, v2, v3])
    """
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

    return triangles, issues


# ---------- validation checks ----------

def _check_manifold(triangles):
    """Check that every edge is shared by exactly 2 triangles."""
    issues = []
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
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s) (shared by >2 faces)")

    return issues


def _check_normals(triangles):
    """Check declared normals vs winding-order normals.
    A face normal is considered inverted if the dot product with the
    winding-order normal is negative (threshold < 0, not -0.99).
    """
    issues = []
    inverted_count = 0
    zero_normal_count = 0

    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # degenerate triangle, skip normal check

        winding_normal = _normalize(cross)
        dn_mag = _magnitude(declared_normal)

        # If declared normal is zero vector, skip (some exporters use (0,0,0))
        if dn_mag < 1e-10:
            zero_normal_count += 1
            continue

        similarity = _dot(_normalize(declared_normal), winding_normal)
        # Fix: threshold should be < 0 (inverted), not < -0.99
        if similarity < 0:
            inverted_count += 1

    if inverted_count > 0:
        issues.append(f"{inverted_count} face(s) have normals inverted relative to winding order")
    if zero_normal_count > 0:
        # Not necessarily an error — some exporters use zero normals
        # but flag as a warning
        issues.append(f"{zero_normal_count} face(s) have zero-length declared normals")

    return issues


def _check_degenerate(triangles):
    """Check for zero-area (degenerate) triangles."""
    issues = []
    degenerate_count = 0
    for _, verts in triangles:
        area = _triangle_area(*verts)
        if area < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")
    return issues


def _check_duplicates(triangles):
    """Check for duplicate faces."""
    issues = []
    seen = set()
    duplicate_count = 0
    for _, verts in triangles:
        key = tuple(sorted(tuple(round(x, 6) for x in v) for v in verts))
        if key in seen:
            duplicate_count += 1
        else:
            seen.add(key)
    if duplicate_count > 0:
        issues.append(f"{duplicate_count} duplicate face(s) found")
    return issues


def _check_minimum_faces(triangles):
    """A valid closed solid needs at least 4 triangles."""
    issues = []
    if 0 < len(triangles) < 4:
        issues.append(f"Too few faces ({len(triangles)}) to form a closed solid (minimum 4)")
    return issues


# ---------- main entry point ----------

def validate_stl(path: str) -> dict:
    """Validate an STL file (ASCII or binary) for manufacturing suitability."""
    is_binary = _is_binary_stl(path)

    if is_binary:
        triangles, parse_issues = _parse_binary(path)
        format_detected = "binary"
    else:
        triangles, parse_issues = _parse_ascii(path)
        format_detected = "ascii"

    issues = list(parse_issues)

    if triangles:
        issues += _check_minimum_faces(triangles)
        issues += _check_degenerate(triangles)
        issues += _check_manifold(triangles)
        issues += _check_normals(triangles)
        issues += _check_duplicates(triangles)

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles) if triangles else 0.0

    return {
        "valid": len(issues) == 0,
        "format": format_detected,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator.py <file.stl>"}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result))
