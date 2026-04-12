"""stl_validator_binary.py — STL validation tool for both ASCII and binary STL files.

Accepts a single STL file path as a command-line argument and prints a JSON
object: {"valid": true/false, "issues": [...]}

Checks:
 - Binary or ASCII format detection
 - Triangle/face count
 - Degenerate triangles (zero-area faces)
 - Non-manifold / open edges (each edge must be shared by exactly 2 faces)
 - Inverted normals (declared normal vs winding-order normal), with correct
   threshold (< 0, not -0.99 as in the provided validator)
 - Duplicate faces
 - Surface area and volume (informational)
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


def _signed_volume(v1, v2, v3):
    """Signed volume contribution of a triangle (for closed-mesh volume)."""
    return _dot(v1, _cross(v2, v3)) / 6.0


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def _is_binary_stl(path: str) -> bool:
    """Return True if the file is a binary STL, False if ASCII."""
    with open(path, "rb") as fh:
        header = fh.read(80)
        if len(header) < 80:
            return False  # Too short to be valid binary; treat as ASCII
        count_bytes = fh.read(4)
        if len(count_bytes) < 4:
            return False
        triangle_count = struct.unpack('<I', count_bytes)[0]
        # Binary STL: header(80) + count(4) + triangles * 50
        expected_size = 80 + 4 + triangle_count * 50
        fh.seek(0, 2)  # seek to end
        actual_size = fh.tell()
        if actual_size == expected_size:
            return True
    # Check if file starts with 'solid' (ASCII indicator)
    with open(path, 'rb') as fh:
        start = fh.read(256)
    try:
        text = start.decode('ascii', errors='replace').strip()
        if text.lower().startswith('solid'):
            return False
    except Exception:
        pass
    return True  # Default to binary if unsure


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_binary(path: str):
    """Parse binary STL. Returns list of (normal, [v1, v2, v3]) tuples."""
    triangles = []
    issues = []
    with open(path, 'rb') as fh:
        header = fh.read(80)
        count_bytes = fh.read(4)
        if len(count_bytes) < 4:
            issues.append("File too short to contain binary STL triangle count")
            return triangles, issues
        triangle_count = struct.unpack('<I', count_bytes)[0]
        for i in range(triangle_count):
            data = fh.read(50)
            if len(data) < 50:
                issues.append(f"Unexpected end of file at triangle {i + 1}")
                break
            vals = struct.unpack('<12fH', data)
            normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((normal, [v1, v2, v3]))
    return triangles, issues


def _parse_ascii(path: str):
    """Parse ASCII STL. Returns list of (normal, [v1, v2, v3]) tuples."""
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
                issues.append(f"Malformed facet normal at line {i + 1}")
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
# Validation logic
# ---------------------------------------------------------------------------

def validate_stl(path: str) -> dict:
    issues = []

    # Detect format
    try:
        binary = _is_binary_stl(path)
    except Exception as e:
        return {"valid": False, "issues": [f"Cannot read file: {e}"]}

    fmt = "binary" if binary else "ascii"

    # Parse
    try:
        if binary:
            triangles, parse_issues = _parse_binary(path)
        else:
            triangles, parse_issues = _parse_ascii(path)
    except Exception as e:
        return {"valid": False, "issues": [f"Parse error: {e}"]}

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

    # --- Degenerate triangle check ---
    degenerate_count = 0
    for _, verts in triangles:
        area = _triangle_area(*verts)
        if area < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")

    # --- Manifold / open edge check ---
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

    # --- Normal consistency check (FIXED: threshold < 0, not -0.99) ---
    inverted_count = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # skip degenerate
        winding_normal = _normalize(cross)
        dn_mag = _magnitude(declared_normal)
        if dn_mag < 1e-10:
            continue  # zero declared normal — skip
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < 0:
            inverted_count += 1
    if inverted_count > 0:
        issues.append(f"{inverted_count} face(s) have normals inverted relative to winding order")

    # --- Duplicate face check ---
    seen_faces: set = set()
    duplicate_count = 0
    for _, verts in triangles:
        key = tuple(sorted(tuple(round(x, 6) for x in v) for v in verts))
        if key in seen_faces:
            duplicate_count += 1
        else:
            seen_faces.add(key)
    if duplicate_count > 0:
        issues.append(f"{duplicate_count} duplicate face(s) found")

    # --- Surface area ---
    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    # --- Signed volume (only meaningful for closed meshes) ---
    volume = None
    if open_edges == 0 and non_manifold_edges == 0:
        volume = abs(sum(_signed_volume(*verts) for _, verts in triangles))

    result = {
        "valid": len(issues) == 0,
        "format": fmt,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }
    if volume is not None:
        result["volume"] = round(volume, 6)
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_binary.py <file.stl>"}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result))
