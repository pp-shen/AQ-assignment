"""stl_validator_binary_ascii.py

Validates both binary and ASCII STL files for manufacturing suitability.

Checks:
  - Format detection (binary vs ASCII)
  - No degenerate (zero-area) triangles
  - Manifold closure (every edge shared by exactly 2 triangles)
  - No non-manifold edges (edge shared by >2 triangles)
  - Inverted face normals (declared normal vs winding-order cross-product)

Usage:
    python stl_validator_binary_ascii.py <file.stl>

Outputs JSON: {"valid": true/false, "format": "binary"|"ascii",
               "triangle_count": N, "surface_area": F, "issues": [...]}
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
    if m == 0:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _file_size(path: str) -> int:
    import os
    return os.path.getsize(path)


def _is_binary(path: str) -> bool:
    """Return True if the file is a binary STL, False if ASCII."""
    with open(path, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            return False
        num_triangles = struct.unpack('<I', count_bytes)[0]
        expected_size = 80 + 4 + num_triangles * 50
    actual_size = _file_size(path)
    # Binary: file size matches expected binary layout exactly
    if actual_size == expected_size:
        return True
    # ASCII: header starts with 'solid'
    try:
        text = header.decode('ascii', errors='replace').strip()
        if text.lower().startswith('solid'):
            return False
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Binary STL parser
# ---------------------------------------------------------------------------

def _parse_binary(path: str):
    """Parse a binary STL file. Returns (triangles, issues)."""
    triangles = []
    issues = []
    with open(path, 'rb') as f:
        f.read(80)  # skip 80-byte header
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            issues.append("Binary STL: file too short to read triangle count")
            return triangles, issues
        num_triangles = struct.unpack('<I', count_bytes)[0]
        for i in range(num_triangles):
            data = f.read(50)
            if len(data) < 50:
                issues.append(f"Binary STL: truncated triangle data at triangle {i+1}")
                break
            vals = struct.unpack('<12fH', data)
            normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((normal, [v1, v2, v3]))
    return triangles, issues


# ---------------------------------------------------------------------------
# ASCII STL parser
# ---------------------------------------------------------------------------

def _parse_ascii(path: str):
    """Parse an ASCII STL file. Returns (triangles, issues)."""
    triangles = []
    issues = []
    with open(path, 'r', errors='replace') as fh:
        lines = fh.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('facet normal'):
            parts = line.split()
            try:
                declared_normal = (float(parts[2]), float(parts[3]), float(parts[4]))
            except (IndexError, ValueError):
                issues.append(f"Malformed facet normal at line {i+1}")
                i += 1
                continue

            vertices = []
            i += 1
            if i < len(lines) and lines[i].strip() == 'outer loop':
                i += 1
                for _ in range(3):
                    if i < len(lines):
                        vline = lines[i].strip()
                        if vline.startswith('vertex'):
                            vparts = vline.split()
                            try:
                                vertices.append((
                                    float(vparts[1]),
                                    float(vparts[2]),
                                    float(vparts[3])
                                ))
                            except (IndexError, ValueError):
                                issues.append(f"Malformed vertex at line {i+1}")
                        i += 1

            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f"Incomplete triangle near line {i+1}")
            continue
        i += 1

    if not triangles:
        issues.append("No valid faces found")

    return triangles, issues


# ---------------------------------------------------------------------------
# Geometry checks
# ---------------------------------------------------------------------------

def _check_triangles(triangles):
    """Run geometry checks on parsed triangles. Returns list of issue strings."""
    issues = []
    AREA_EPSILON = 1e-12
    NORMAL_DOT_THRESHOLD = 0.0

    degenerate_count = 0
    inverted_count = 0
    edge_count: dict = {}

    for idx, (declared_normal, verts) in enumerate(triangles):
        v1, v2, v3 = verts

        # --- Degenerate triangle check ---
        area = _triangle_area(v1, v2, v3)
        if area < AREA_EPSILON:
            degenerate_count += 1
            continue  # skip further checks for degenerate triangles

        # --- Inverted normal check ---
        # Compute the cross product from winding order (not normalized)
        computed_cross = _cross(_sub(v2, v1), _sub(v3, v1))
        dn_mag = _magnitude(declared_normal)
        if dn_mag > 1e-12:
            # Declared normal and computed cross product must point same direction
            dot = _dot(declared_normal, computed_cross)
            if dot < NORMAL_DOT_THRESHOLD:
                inverted_count += 1

        # --- Edge manifold check ---
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    if degenerate_count > 0:
        issues.append(f"Mesh has {degenerate_count} degenerate (zero-area) triangle(s)")

    if inverted_count > 0:
        issues.append(f"Mesh has {inverted_count} triangle(s) with inverted normals")

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)

    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) - not a closed solid")
    if non_manifold_edges > 0:
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s)")

    return issues


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------

def validate_stl(path: str) -> dict:
    """Validate an STL file (binary or ASCII). Returns result dict."""
    import os
    if not os.path.exists(path):
        return {"valid": False, "issues": [f"File not found: {path}"]}

    try:
        is_bin = _is_binary(path)
    except Exception as e:
        return {"valid": False, "issues": [f"Format detection error: {e}"]}

    fmt = "binary" if is_bin else "ascii"

    try:
        if is_bin:
            triangles, parse_issues = _parse_binary(path)
        else:
            triangles, parse_issues = _parse_ascii(path)
    except Exception as e:
        return {
            "valid": False,
            "format": fmt,
            "triangle_count": 0,
            "surface_area": 0.0,
            "issues": [f"Parse error: {e}"]
        }

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


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_binary_ascii.py <file.stl>"}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result))
