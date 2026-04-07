import json
import math
import struct
import sys
from collections import Counter

EPS = 1e-6
ROUND_DIGITS = 6


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _mag(v):
    return math.sqrt(_dot(v, v))


def _normalize(v):
    m = _mag(v)
    if m <= EPS:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _triangle_normal(v1, v2, v3):
    return _cross(_sub(v2, v1), _sub(v3, v1))


def _triangle_area(v1, v2, v3):
    return 0.5 * _mag(_triangle_normal(v1, v2, v3))


def _is_binary_stl(data):
    if len(data) < 84:
        return False
    tri_count = struct.unpack_from('<I', data, 80)[0]
    expected = 84 + tri_count * 50
    if expected == len(data):
        return True
    if not data[:5].lower() == b'solid':
        return True
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return True
    return not text.lstrip().startswith('solid')


def parse_binary_stl(path):
    issues = []
    triangles = []
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < 84:
        return triangles, ['File too small to be a valid binary STL'], 'binary'
    tri_count = struct.unpack_from('<I', data, 80)[0]
    expected = 84 + tri_count * 50
    if expected != len(data):
        issues.append(f'Binary STL size mismatch: header expects {tri_count} triangle(s) and {expected} bytes, got {len(data)} bytes')
        tri_count = max(0, (len(data) - 84) // 50)
    offset = 84
    for idx in range(tri_count):
        if offset + 50 > len(data):
            issues.append(f'Triangle record {idx} truncated')
            break
        vals = struct.unpack_from('<12fH', data, offset)
        declared = (vals[0], vals[1], vals[2])
        v1 = (vals[3], vals[4], vals[5])
        v2 = (vals[6], vals[7], vals[8])
        v3 = (vals[9], vals[10], vals[11])
        triangles.append((declared, [v1, v2, v3]))
        offset += 50
    return triangles, issues, 'binary'


def parse_ascii_stl(path):
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
                declared = (float(parts[2]), float(parts[3]), float(parts[4]))
            except Exception:
                issues.append(f'Malformed facet normal at line {i + 1}')
                i += 1
                continue
            i += 1
            if i >= len(lines) or lines[i].strip() != 'outer loop':
                issues.append(f'Missing outer loop after facet at line {i}')
                continue
            i += 1
            verts = []
            for _ in range(3):
                if i >= len(lines):
                    break
                vline = lines[i].strip()
                if not vline.startswith('vertex'):
                    issues.append(f'Expected vertex at line {i + 1}')
                    break
                parts = vline.split()
                try:
                    verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except Exception:
                    issues.append(f'Malformed vertex at line {i + 1}')
                i += 1
            if i < len(lines) and lines[i].strip() == 'endloop':
                i += 1
            if i < len(lines) and lines[i].strip() == 'endfacet':
                i += 1
            if len(verts) == 3:
                triangles.append((declared, verts))
            else:
                issues.append(f'Incomplete triangle near line {i + 1}')
            continue
        i += 1
    if not triangles:
        issues.append('No valid faces found')
    return triangles, issues, 'ascii'


def validate_triangles(triangles, issues):
    edge_counts = Counter()
    surface_area = 0.0
    inverted = 0
    degenerate = 0
    for declared, verts in triangles:
        v1, v2, v3 = verts
        area = _triangle_area(v1, v2, v3)
        surface_area += area
        if area <= EPS:
            degenerate += 1
            continue
        comp_n = _normalize(_triangle_normal(v1, v2, v3))
        decl_n = _normalize(declared)
        if _mag(decl_n) > EPS and _dot(comp_n, decl_n) < 0.0:
            inverted += 1
        rounded = [tuple(round(c, ROUND_DIGITS) for c in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted((rounded[j], rounded[(j + 1) % 3])))
            edge_counts[edge] += 1
    if degenerate:
        issues.append(f'Mesh has {degenerate} degenerate triangle(s)')
    open_edges = sum(1 for c in edge_counts.values() if c == 1)
    nonmanifold = sum(1 for c in edge_counts.values() if c > 2)
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if nonmanifold:
        issues.append(f'Mesh has {nonmanifold} non-manifold edge(s)')
    if inverted:
        issues.append(f'Mesh has {inverted} facet(s) with declared normals inverted relative to vertex winding')
    return surface_area


def parse_stl(path):
    with open(path, 'rb') as f:
        data = f.read()
    if _is_binary_stl(data):
        triangles, issues, fmt = parse_binary_stl(path)
    else:
        triangles, issues, fmt = parse_ascii_stl(path)
    surface_area = validate_triangles(triangles, issues)
    return {
        'valid': len(issues) == 0,
        'issues': issues,
        'triangle_count': len(triangles),
        'surface_area': round(surface_area, 6),
        'format': fmt,
    }


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'issues': ['usage: stl_validator_binary_ascii.py <file.stl>']}))
        sys.exit(1)
    print(json.dumps(parse_stl(sys.argv[1])))
