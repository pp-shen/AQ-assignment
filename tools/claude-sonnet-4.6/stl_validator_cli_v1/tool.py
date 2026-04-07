"""stl_validator.py — validates both ASCII and binary STL files for manufacturing suitability.

Usage: python stl_validator.py <file.stl>
Outputs a JSON object: {"valid": true/false, "issues": [...]}

Checks:
  - Format detection (binary vs ASCII)
  - Binary file size consistency
  - Manifold integrity (open / non-manifold edges)
  - Degenerate triangles (zero-area)
  - Duplicate faces
  - Inverted normals (declared vs winding-order cross-product)
"""
import json
import math
import struct
import sys


# ---------------------------------------------------------------------------
# Vector helpers
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

def _is_binary(path: str) -> bool:
    """Return True if the file appears to be binary STL."""
    with open(path, 'rb') as fh:
        header = fh.read(80)
    try:
        with open(path, 'rb') as fh:
            fh.read(80)  # header
            raw_count = fh.read(4)
            if len(raw_count) < 4:
                return False
            tri_count = struct.unpack('<I', raw_count)[0]
            file_size = 80 + 4 + tri_count * 50
        import os
        actual_size = os.path.getsize(path)
        if actual_size == file_size and tri_count > 0:
            return True
        if any(b > 127 for b in header[:5]):
            return True
    except Exception:
        pass
    try:
        with open(path, 'r', errors='replace') as fh:
            first = fh.read(256).lstrip()
        return not first.lower().startswith('solid')
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Binary parser
# ---------------------------------------------------------------------------

def _parse_binary(path: str) -> tuple:
    """Parse binary STL; return (triangles, issues) where triangles = list of (normal, [v1,v2,v3])."""
    import os
    issues = []
    triangles = []

    with open(path, 'rb') as fh:
        fh.read(80)
        raw_count = fh.read(4)
        if len(raw_count) < 4:
            issues.append("Binary STL too short to contain triangle count")
            return triangles, issues
        tri_count = struct.unpack('<I', raw_count)[0]

        expected_size = 80 + 4 + tri_count * 50
        actual_size = os.path.getsize(path)
        if actual_size != expected_size:
            issues.append(
                f"Binary file size mismatch: expected {expected_size} bytes "
                f"for {tri_count} triangles, got {actual_size} bytes"
            )

        for i in range(tri_count):
            data = fh.read(50)
            if len(data) < 50:
                issues.append(f"Unexpected end of binary data at triangle {i}")
                break
            vals = struct.unpack('<12fH', data)
            normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((normal, [v1, v2, v3]))

    return triangles, issues


# ---------------------------------------------------------------------------
# ASCII parser
# ---------------------------------------------------------------------------

def _parse_ascii(path: str) -> tuple:
    """Parse ASCII STL; return (triangles, issues)."""
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
# Validation logic
# ---------------------------------------------------------------------------

def _round_vertex(v, digits=6):
    return tuple(round(x, digits) for x in v)


def validate(path: str) -> dict:
    issues = []

    try:
        binary = _is_binary(path)
    except FileNotFoundError:
        return {"valid": False, "issues": [f"File not found: {path}"]}

    if binary:
        triangles, parse_issues = _parse_binary(path)
    else:
        triangles, parse_issues = _parse_ascii(path)

    issues.extend(parse_issues)

    if not triangles:
        issues.append("No valid faces found")
        return {"valid": False, "issues": issues}

    # Degenerate triangles
    degenerate_count = sum(
        1 for _, verts in triangles if _triangle_area(*verts) < 1e-12
    )
    if degenerate_count:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")

    # Duplicate faces
    face_set = set()
    duplicate_count = 0
    for _, verts in triangles:
        key = tuple(sorted(_round_vertex(v) for v in verts))
        if key in face_set:
            duplicate_count += 1
        face_set.add(key)
    if duplicate_count:
        issues.append(f"{duplicate_count} duplicate face(s) found")

    # Manifold check
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [_round_vertex(v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j+1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges:
        issues.append(f"Mesh has {open_edges} open edge(s) — not a closed solid")
    if non_manifold_edges:
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s)")

    # Normal consistency check (fixes the provided validator's hidden failure mode)
    inverted_count = 0
    for normal, verts in triangles:
        if _triangle_area(*verts) < 1e-12:
            continue
        computed = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        if _magnitude(normal) < 1e-12:
            continue
        if _dot(computed, normal) < 0:
            inverted_count += 1
    if inverted_count:
        issues.append(f"{inverted_count} face(s) have normals inconsistent with vertex winding order")

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator.py <file.stl>"}))
        sys.exit(1)
    result = validate(sys.argv[1])
    print(json.dumps(result))
