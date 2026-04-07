import json
import math
import os
import struct
import sys


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _magnitude(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _normalize(v):
    mag = _magnitude(v)
    if mag == 0:
        return (0.0, 0.0, 0.0)
    return (v[0] / mag, v[1] / mag, v[2] / mag)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def _is_binary_stl(path):
    try:
        size = os.path.getsize(path)
        if size < 84:
            return False
        with open(path, 'rb') as f:
            header = f.read(80)
            count_bytes = f.read(4)
            if len(count_bytes) != 4:
                return False
            count = struct.unpack('<I', count_bytes)[0]
            expected = 84 + count * 50
            if expected == size:
                return True
            if header[:5].lower() != b'solid':
                return True
    except Exception:
        return False
    return False


def _parse_ascii(path):
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
                                vertices.append((float(vparts[1]), float(vparts[2]), float(vparts[3])))
                            except (IndexError, ValueError):
                                issues.append(f'Malformed vertex at line {i + 1}')
                        i += 1
            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f'Incomplete triangle near line {i + 1}')
            continue
        i += 1
    return triangles, issues, 'ascii'


def _parse_binary(path):
    issues = []
    triangles = []
    with open(path, 'rb') as f:
        f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) != 4:
            return [], ['Binary STL missing triangle count'], 'binary'
        count = struct.unpack('<I', count_bytes)[0]
        for idx in range(count):
            data = f.read(50)
            if len(data) != 50:
                issues.append(f'Binary STL truncated at triangle {idx}')
                break
            vals = struct.unpack('<12fH', data)
            declared_normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((declared_normal, [v1, v2, v3]))
        if f.read(1):
            issues.append('Binary STL has trailing extra bytes')
    return triangles, issues, 'binary'


def parse_stl(path):
    if _is_binary_stl(path):
        triangles, issues, fmt = _parse_binary(path)
    else:
        triangles, issues, fmt = _parse_ascii(path)

    if not triangles:
        issues.append('No valid faces found')

    edge_count = {}
    degenerate = 0
    inverted = 0
    zero_normals = 0

    for declared_normal, verts in triangles:
        area = _triangle_area(*verts)
        if area == 0:
            degenerate += 1
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1
        implied = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        implied_n = _normalize(implied)
        declared_n = _normalize(declared_normal)
        if _magnitude(implied) == 0:
            continue
        if _magnitude(declared_normal) == 0:
            zero_normals += 1
            continue
        if _dot(implied_n, declared_n) < 0.0:
            inverted += 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)

    if open_edges > 0:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if non_manifold_edges > 0:
        issues.append(f'Mesh has {non_manifold_edges} non-manifold edge(s)')
    if degenerate > 0:
        issues.append(f'Mesh has {degenerate} degenerate triangle(s)')
    if inverted > 0:
        issues.append(f'Mesh has {inverted} triangle(s) with inverted declared normals')
    if zero_normals > 0:
        issues.append(f'Mesh has {zero_normals} triangle(s) with zero declared normals')

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)
    return {
        'format': fmt,
        'valid': len(issues) == 0,
        'triangle_count': len(triangles),
        'surface_area': round(surface_area, 6),
        'issues': issues,
        'manufacturing_suitable': len(issues) == 0,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: robust_binary_ascii_stl_manufacturing_validator.py <file.stl>'}))
        sys.exit(1)
    print(json.dumps(parse_stl(sys.argv[1])))
