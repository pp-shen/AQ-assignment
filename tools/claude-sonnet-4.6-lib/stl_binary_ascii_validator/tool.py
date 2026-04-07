"""stl_validator.py -- STL validation tool supporting both binary and ASCII formats.

Reads an STL file (binary or ASCII), checks triangle count, surface area,
manifold integrity, and face normal consistency.

Usage: python stl_validator.py <file.stl>
Output: JSON object {"valid": true/false, "issues": [...]}
"""
import json
import math
import struct
import sys


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

def _is_binary_stl(path: str) -> bool:
    """Return True if the file looks like a binary STL."""
    with open(path, "rb") as f:
        header = f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            return False
        tri_count = struct.unpack_from("<I", count_bytes)[0]
        # Check file size matches binary STL formula: 80 + 4 + 50*n
        f.seek(0, 2)  # end of file
        file_size = f.tell()
        expected_size = 80 + 4 + 50 * tri_count
        if file_size == expected_size and tri_count > 0:
            return True

    # Check if the header starts with 'solid' -- that's a strong ASCII hint.
    # But some binary STL files also start with 'solid', so size check wins.
    try:
        text_start = header.decode("ascii", errors="strict")[:5]
        if text_start.strip().startswith("solid"):
            return False  # Treat as ASCII if size didn't match binary formula
    except UnicodeDecodeError:
        pass

    return False


# ---------------------------------------------------------------------------
# Binary STL parser
# ---------------------------------------------------------------------------

def _parse_binary_stl(path: str):
    """Parse binary STL. Returns (triangles, issues).
    triangles = list of (declared_normal, [v1, v2, v3])
    """
    issues = []
    triangles = []

    with open(path, "rb") as f:
        header = f.read(80)  # 80-byte header (ignored)
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            issues.append("Binary STL: file too short to read triangle count")
            return triangles, issues

        tri_count = struct.unpack_from("<I", count_bytes)[0]

        for i in range(tri_count):
            chunk = f.read(50)  # 12 floats (normal + 3 vertices) + 2 attr bytes
            if len(chunk) < 50:
                issues.append(f"Binary STL: unexpected end of file at triangle {i + 1}")
                break

            vals = struct.unpack_from("<12f", chunk)  # 12 floats
            normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((normal, [v1, v2, v3]))

    return triangles, issues


# ---------------------------------------------------------------------------
# ASCII STL parser
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Shared validation logic
# ---------------------------------------------------------------------------

def _validate_triangles(triangles, issues):
    """Run manifold and normal checks on parsed triangles. Modifies issues in place."""
    if not triangles:
        issues.append("No valid faces found")
        return

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
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s) (shared by >2 faces)")

    # Normal consistency check.
    inverted_count = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # degenerate triangle
        winding_normal = _normalize(cross)
        declared_mag = _magnitude(declared_normal)
        if declared_mag < 1e-10:
            continue  # zero-length declared normal, skip
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < -0.99:
            inverted_count += 1

    if inverted_count > 0:
        issues.append(
            f"{inverted_count} face(s) have normals inverted relative to winding order"
        )

    # Degenerate triangle check
    degenerate_count = 0
    for _, verts in triangles:
        area = _triangle_area(*verts)
        if area < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_stl(path: str) -> dict:
    """Validate an STL file (binary or ASCII). Returns a dict with valid/issues."""
    issues = []

    try:
        binary = _is_binary_stl(path)
    except OSError as e:
        return {"valid": False, "issues": [f"Cannot open file: {e}"]}

    try:
        if binary:
            triangles, parse_issues = _parse_binary_stl(path)
            format_detected = "binary"
        else:
            triangles, parse_issues = _parse_ascii_stl(path)
            format_detected = "ascii"
    except Exception as e:
        return {"valid": False, "issues": [f"Parse error: {e}"]}

    issues.extend(parse_issues)
    _validate_triangles(triangles, issues)

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

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
