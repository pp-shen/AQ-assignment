"""stl_validator_improved.py - Improved STL validation tool.

Handles both ASCII and binary STL files.
Checks:
  1. Manifold integrity - open edges (count==1) AND non-manifold edges (count>2)
  2. Degenerate triangles (zero-area faces)
  3. Duplicate faces
  4. Inverted normals (declared vs winding order) - any mismatch, not just -0.99
  5. Binary file size consistency
  6. Triangle count > 0
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
    """Detect if the file is a binary STL."""
    try:
        with open(path, 'rb') as f:
            header = f.read(80)
            if len(header) < 80:
                return False
            count_bytes = f.read(4)
            if len(count_bytes) < 4:
                return False
            count = struct.unpack('<I', count_bytes)[0]
            expected_size = 80 + 4 + count * 50
            f.seek(0, 2)
            actual_size = f.tell()
            if actual_size == expected_size:
                return True
            try:
                header.decode('ascii')
                text = header.decode('ascii').strip()
                if text.startswith('solid'):
                    return False
            except UnicodeDecodeError:
                return True
        return False
    except Exception:
        return False


def _parse_binary_stl(path: str) -> tuple:
    """Parse binary STL. Returns (triangles, issues)."""
    issues = []
    triangles = []  # list of (declared_normal, [v1, v2, v3])

    with open(path, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            issues.append("Binary STL: file too short to read triangle count")
            return triangles, issues
        count = struct.unpack('<I', count_bytes)[0]

        expected_size = 80 + 4 + count * 50
        f.seek(0, 2)
        actual_size = f.tell()
        if actual_size != expected_size:
            issues.append(
                f"Binary STL: file size mismatch. Expected {expected_size} bytes "
                f"for {count} triangles, got {actual_size} bytes."
            )

        f.seek(84)
        for i in range(count):
            data = f.read(50)
            if len(data) < 50:
                issues.append(f"Binary STL: truncated data at triangle {i+1}")
                break
            nx, ny, nz = struct.unpack('<fff', data[0:12])
            v1 = struct.unpack('<fff', data[12:24])
            v2 = struct.unpack('<fff', data[24:36])
            v3 = struct.unpack('<fff', data[36:48])
            triangles.append(((nx, ny, nz), [v1, v2, v3]))

    return triangles, issues


def _parse_ascii_stl(path: str) -> tuple:
    """Parse ASCII STL. Returns (triangles, issues)."""
    issues = []
    triangles = []  # list of (declared_normal, [v1, v2, v3])

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


def validate_stl(path: str) -> dict:
    """Validate an STL file (ASCII or binary) for manufacturing suitability."""
    issues = []

    binary = _is_binary_stl(path)

    if binary:
        triangles, parse_issues = _parse_binary_stl(path)
    else:
        triangles, parse_issues = _parse_ascii_stl(path)

    issues.extend(parse_issues)

    if not triangles:
        issues.append("No valid faces found")
        return {
            "valid": False,
            "format": "binary" if binary else "ascii",
            "triangle_count": 0,
            "surface_area": 0.0,
            "issues": issues,
        }

    # Check 1: Degenerate triangles
    degenerate_count = 0
    for _, verts in triangles:
        area = _triangle_area(*verts)
        if area < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"Mesh has {degenerate_count} degenerate (zero-area) triangle(s)")

    # Check 2: Duplicate faces
    face_set = set()
    duplicate_count = 0
    for _, verts in triangles:
        rounded = tuple(sorted([tuple(round(x, 6) for x in v) for v in verts]))
        if rounded in face_set:
            duplicate_count += 1
        else:
            face_set.add(rounded)
    if duplicate_count > 0:
        issues.append(f"Mesh has {duplicate_count} duplicate face(s)")

    # Check 3: Manifold integrity
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)

    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) - not a closed solid")
    if non_manifold_edges > 0:
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s) (shared by 3+ triangles)")

    # Check 4: Normal consistency (any mismatch, not just near-perfect inversions)
    inverted_count = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue
        winding_normal = _normalize(cross)
        dn_mag = _magnitude(declared_normal)
        if dn_mag < 1e-10:
            continue
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < 0:
            inverted_count += 1
    if inverted_count > 0:
        issues.append(
            f"{inverted_count} face normal(s) are inverted relative to winding order"
        )

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "format": "binary" if binary else "ascii",
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
