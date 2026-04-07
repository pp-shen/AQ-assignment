"""stl_validator_binary_ascii_v3.py

Validates both binary and ASCII STL files for manufacturing suitability.
Auto-detects format, checks:
  - Manifold closure (open/non-manifold edges)
  - Inverted face normals via winding-order cross-product
  - Degenerate (zero-area) triangles

Usage: python stl_validator_binary_ascii_v3.py <file.stl>
Output: JSON {valid, format, triangle_count, surface_area, issues}
"""
import json
import math
import struct
import sys


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

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
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _is_binary_stl(path: str) -> bool:
    """Return True if the file appears to be a binary STL.

    Binary STL: 80-byte header + 4-byte uint32 triangle count + 50*N bytes.
    We also check that the file does NOT start with 'solid' when decoded as
    ASCII (some valid binary files have 'solid' in the header though, so we
    use file-size verification as the authoritative test).
    """
    try:
        with open(path, "rb") as fh:
            header = fh.read(80)
            if len(header) < 80:
                return False
            count_bytes = fh.read(4)
            if len(count_bytes) < 4:
                return False
            num_triangles = struct.unpack("<I", count_bytes)[0]
            fh.seek(0, 2)  # seek to end
            file_size = fh.tell()
        expected_size = 80 + 4 + 50 * num_triangles
        if file_size == expected_size:
            return True
        # If size doesn't match, fall back to checking for ASCII 'solid' keyword
        # at the very start (after stripping whitespace)
        text_start = header.lstrip()
        if text_start[:5].lower() == b"solid":
            return False
        return False
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_binary(path: str):
    """Parse binary STL. Returns list of (normal, [v1, v2, v3])."""
    triangles = []
    issues = []
    with open(path, "rb") as fh:
        fh.read(80)  # skip header
        count_bytes = fh.read(4)
        if len(count_bytes) < 4:
            issues.append("Truncated binary STL: missing triangle count")
            return triangles, issues
        num_triangles = struct.unpack("<I", count_bytes)[0]
        for i in range(num_triangles):
            data = fh.read(50)
            if len(data) < 50:
                issues.append(f"Truncated binary STL at triangle {i + 1}")
                break
            vals = struct.unpack("<12fH", data)
            normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((normal, [v1, v2, v3]))
    return triangles, issues


def _parse_ascii(path: str):
    """Parse ASCII STL. Returns list of (normal, [v1, v2, v3])."""
    triangles = []
    issues = []
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
# Validation checks
# ---------------------------------------------------------------------------

def _check_triangles(triangles):
    """Run geometry checks on parsed triangles. Returns list of issue strings."""
    issues = []

    if not triangles:
        issues.append("No valid faces found")
        return issues

    degenerate_count = 0
    inverted_count = 0

    for idx, (declared_normal, verts) in enumerate(triangles):
        v1, v2, v3 = verts

        # Degenerate check
        area = _triangle_area(v1, v2, v3)
        if area < 1e-12:
            degenerate_count += 1
            continue  # skip normal check for degenerate triangles

        # Inverted normal check
        computed_cross = _cross(_sub(v2, v1), _sub(v3, v1))
        computed_normal = _normalize(computed_cross)

        # Only check if declared normal is non-zero
        decl_mag = _magnitude(declared_normal)
        if decl_mag > 1e-12:
            decl_norm = _normalize(declared_normal)
            dot = _dot(computed_normal, decl_norm)
            if dot < 0.0:  # normals point in opposite hemispheres
                inverted_count += 1

    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")
    if inverted_count > 0:
        issues.append(f"{inverted_count} triangle(s) have inverted normals (winding-order mismatch)")

    # Manifold check: every edge must be shared by exactly 2 triangles
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
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s) (shared by >2 triangles)")

    return issues


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_stl(path: str) -> dict:
    binary = _is_binary_stl(path)
    fmt = "binary" if binary else "ascii"

    if binary:
        triangles, parse_issues = _parse_binary(path)
    else:
        triangles, parse_issues = _parse_ascii(path)

    geo_issues = _check_triangles(triangles)
    all_issues = parse_issues + geo_issues

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(all_issues) == 0,
        "format": fmt,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": all_issues,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_binary_ascii_v3.py <file.stl>"}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result))
