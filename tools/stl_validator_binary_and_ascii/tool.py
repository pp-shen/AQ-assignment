"""stl_validator_binary.py -- STL validation tool supporting both binary and ASCII formats.

Usage: python stl_validator_binary.py <file.stl>
Outputs: JSON object {"valid": true/false, "issues": [...]}

Checks:
  - File parseable (binary or ASCII)
  - At least one triangle
  - No degenerate (zero-area) triangles
  - Manifold mesh (every edge shared by exactly 2 triangles)
  - No inverted normals (declared normal consistent with winding-order cross product)
"""
import json
import math
import struct
import sys


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def _cross(a, b):
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )


def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _magnitude(v):
    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)


def _normalize(v):
    m = _magnitude(v)
    if m == 0:
        return (0.0, 0.0, 0.0)
    return (v[0]/m, v[1]/m, v[2]/m)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _is_binary_stl(path: str) -> bool:
    """Detect binary STL by checking if file size matches 80+4+50*n."""
    import os
    with open(path, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
    if len(count_bytes) < 4:
        return False
    tri_count = struct.unpack('<I', count_bytes)[0]
    file_size = os.path.getsize(path)
    expected = 80 + 4 + 50 * tri_count
    if file_size == expected:
        return True
    return False


# ---------------------------------------------------------------------------
# Binary STL parser
# ---------------------------------------------------------------------------

def _parse_binary(path: str):
    """Parse binary STL. Returns (triangles, issues).
    triangles = list of (declared_normal, [v1, v2, v3])
    """
    issues = []
    triangles = []
    with open(path, 'rb') as f:
        header = f.read(80)  # skip header
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            issues.append("Binary STL: truncated file (missing triangle count)")
            return triangles, issues
        tri_count = struct.unpack('<I', count_bytes)[0]
        for idx in range(tri_count):
            chunk = f.read(50)
            if len(chunk) < 50:
                issues.append(f"Binary STL: truncated at triangle {idx+1}")
                break
            # 12 bytes normal, 36 bytes vertices (3x12), 2 bytes attr
            nx, ny, nz = struct.unpack('<fff', chunk[0:12])
            v1 = struct.unpack('<fff', chunk[12:24])
            v2 = struct.unpack('<fff', chunk[24:36])
            v3 = struct.unpack('<fff', chunk[36:48])
            declared_normal = (nx, ny, nz)
            triangles.append((declared_normal, [v1, v2, v3]))
    return triangles, issues


# ---------------------------------------------------------------------------
# ASCII STL parser
# ---------------------------------------------------------------------------

def _parse_ascii(path: str):
    """Parse ASCII STL. Returns (triangles, issues)."""
    issues = []
    triangles = []
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

    return triangles, issues


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

def _validate_triangles(triangles, issues):
    """Run geometry checks on parsed triangles. Appends issues in-place."""
    if not triangles:
        issues.append("No valid faces found")
        return

    # Degenerate triangles and inverted normals
    degenerate_count = 0
    inverted_count = 0
    AREA_EPS = 1e-12

    for idx, (declared_normal, verts) in enumerate(triangles):
        v1, v2, v3 = verts
        area = _triangle_area(v1, v2, v3)
        if area < AREA_EPS:
            degenerate_count += 1
            continue  # skip normal check for degenerate triangles

        # Compute normal from winding order
        computed_normal = _cross(_sub(v2, v1), _sub(v3, v1))
        # Check if declared normal is roughly in the same direction
        dn_mag = _magnitude(declared_normal)
        if dn_mag > 1e-10:  # skip zero declared normals
            dot = _dot(declared_normal, computed_normal)
            if dot < 0:
                inverted_count += 1

    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s)")
    if inverted_count > 0:
        issues.append(f"{inverted_count} triangle(s) with inverted normals (winding order mismatch)")

    # Manifold check: every edge shared by exactly 2 triangles
    edge_count = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j+1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) -- not a closed solid")
    if non_manifold_edges > 0:
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s) (shared by >2 triangles)")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_stl(path: str) -> dict:
    """Validate an STL file (binary or ASCII). Returns dict with valid/issues."""
    issues = []
    try:
        if _is_binary_stl(path):
            triangles, parse_issues = _parse_binary(path)
            fmt = "binary"
        else:
            triangles, parse_issues = _parse_ascii(path)
            fmt = "ascii"
    except Exception as e:
        return {"valid": False, "issues": [f"Failed to read file: {e}"]}

    issues.extend(parse_issues)
    _validate_triangles(triangles, issues)

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "format": fmt,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_binary.py <file.stl>"}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result))
