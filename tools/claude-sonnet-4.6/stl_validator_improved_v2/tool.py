"""stl_validator_improved.py — Improved STL validation tool.

Handles both ASCII and binary STL files.
Checks:
  - Parse validity (well-formed facets/vertices)
  - Manifold integrity (every edge shared by exactly 2 triangles)
  - Non-manifold edges (edge shared by >2 triangles)
  - Degenerate triangles (zero area)
  - Duplicate faces
  - Normal consistency (declared normal vs. winding-order computed normal)
  - Binary file size consistency (for binary STL)

Returns a dict with:
  - valid: bool
  - triangle_count: int
  - surface_area: float
  - issues: list of str
  - format: 'ascii' | 'binary'
"""
import json
import math
import struct
import sys

NORMAL_ANGLE_THRESHOLD_DEG = 10.0  # degrees; normals within this are considered consistent


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
    if m < 1e-15:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def _computed_normal(v1, v2, v3):
    return _normalize(_cross(_sub(v2, v1), _sub(v3, v1)))


def _is_binary_stl(path):
    """Heuristic: binary STLs start with an 80-byte header followed by a uint32 count.
    ASCII STLs start with 'solid'."""
    with open(path, 'rb') as f:
        header = f.read(80)
    try:
        with open(path, 'rb') as f:
            f.read(80)  # header
            count_bytes = f.read(4)
            if len(count_bytes) < 4:
                return False
            count = struct.unpack('<I', count_bytes)[0]
            import os
            expected_size = 80 + 4 + count * 50
            actual_size = os.path.getsize(path)
            if actual_size == expected_size and count > 0:
                return True
    except Exception:
        pass
    try:
        text = header.decode('ascii', errors='replace').strip()
        if text.lower().startswith('solid'):
            return False
    except Exception:
        pass
    return False


def _parse_ascii_stl(path):
    """Parse ASCII STL, return (triangles, issues).
    triangles = list of (declared_normal, [v1, v2, v3])
    """
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
                issues.append(f'Malformed facet normal at line {i + 1}')
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
                                issues.append(f'Malformed vertex at line {i + 1}')
                        i += 1

            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f'Incomplete triangle near line {i + 1}')
            continue

        i += 1

    return triangles, issues


def _parse_binary_stl(path):
    """Parse binary STL, return (triangles, issues)."""
    import os
    issues = []
    triangles = []

    file_size = os.path.getsize(path)
    with open(path, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            issues.append('Truncated binary STL: missing triangle count')
            return triangles, issues

        count = struct.unpack('<I', count_bytes)[0]
        expected_size = 80 + 4 + count * 50
        if file_size != expected_size:
            issues.append(
                f'Binary STL file size mismatch: expected {expected_size} bytes '
                f'for {count} triangles, got {file_size} bytes'
            )

        for idx in range(count):
            data = f.read(50)
            if len(data) < 50:
                issues.append(f'Truncated triangle data at triangle {idx + 1}')
                break
            nx, ny, nz = struct.unpack('<fff', data[0:12])
            v1 = struct.unpack('<fff', data[12:24])
            v2 = struct.unpack('<fff', data[24:36])
            v3 = struct.unpack('<fff', data[36:48])
            triangles.append(((nx, ny, nz), [v1, v2, v3]))

    return triangles, issues


def validate_stl(path: str) -> dict:
    """Validate an STL file (ASCII or binary) for manufacturing suitability."""
    issues = []

    is_binary = _is_binary_stl(path)
    fmt = 'binary' if is_binary else 'ascii'

    if is_binary:
        triangles, parse_issues = _parse_binary_stl(path)
    else:
        triangles, parse_issues = _parse_ascii_stl(path)

    issues.extend(parse_issues)

    if not triangles:
        issues.append('No valid faces found')
        return {
            'valid': False,
            'triangle_count': 0,
            'surface_area': 0.0,
            'issues': issues,
            'format': fmt,
        }

    # Degenerate triangle check
    degenerate_count = 0
    for _, verts in triangles:
        area = _triangle_area(*verts)
        if area < 1e-15:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f'{degenerate_count} degenerate triangle(s) with zero area')

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
        issues.append(f'{duplicate_count} duplicate face(s) found')

    # Manifold check
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)

    if open_edges > 0:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if non_manifold_edges > 0:
        issues.append(f'Mesh has {non_manifold_edges} non-manifold edge(s) (shared by >2 triangles)')

    # Normal consistency check (THE KEY FIX vs. provided validator)
    inverted_count = 0
    zero_normal_declared = 0
    for declared_normal, verts in triangles:
        computed = _computed_normal(*verts)
        if _magnitude(computed) < 0.5:
            continue
        decl_mag = _magnitude(declared_normal)
        if decl_mag < 0.5:
            zero_normal_declared += 1
            continue
        decl_norm = _normalize(declared_normal)
        dot = _dot(decl_norm, computed)
        dot = max(-1.0, min(1.0, dot))
        angle_deg = math.degrees(math.acos(dot))
        if angle_deg > NORMAL_ANGLE_THRESHOLD_DEG:
            inverted_count += 1

    if zero_normal_declared > 0:
        issues.append(f'{zero_normal_declared} face(s) have zero-length declared normal')
    if inverted_count > 0:
        issues.append(
            f'{inverted_count} face(s) have normals inconsistent with winding order '
            f'(inverted normals) — angle > {NORMAL_ANGLE_THRESHOLD_DEG}°'
        )

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        'valid': len(issues) == 0,
        'triangle_count': len(triangles),
        'surface_area': round(surface_area, 6),
        'issues': issues,
        'format': fmt,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: stl_validator_improved.py <file.stl>'}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result, indent=2))
