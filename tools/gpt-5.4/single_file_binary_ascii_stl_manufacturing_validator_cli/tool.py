import json
import math
import os
import struct
import sys

EPS = 1e-10
ROUND_DIGITS = 6
TEST_MODE = '--selftest'


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
    return math.sqrt(_dot(v, v))


def _normalize(v):
    m = _magnitude(v)
    if m < EPS:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _looks_like_binary(data):
    if len(data) < 84:
        return False
    tri_count = struct.unpack('<I', data[80:84])[0]
    expected = 84 + tri_count * 50
    return expected == len(data)


def parse_binary_stl(data, issues):
    triangles = []
    tri_count = struct.unpack('<I', data[80:84])[0]
    offset = 84
    for idx in range(tri_count):
        chunk = data[offset:offset + 50]
        if len(chunk) != 50:
            issues.append(f'Incomplete binary triangle record at index {idx}')
            break
        vals = struct.unpack('<12fH', chunk)
        declared = (vals[0], vals[1], vals[2])
        v1 = (vals[3], vals[4], vals[5])
        v2 = (vals[6], vals[7], vals[8])
        v3 = (vals[9], vals[10], vals[11])
        triangles.append((declared, [v1, v2, v3]))
        offset += 50
    return triangles


def parse_ascii_stl_text(text, issues):
    triangles = []
    lines = text.splitlines()
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
                while i < len(lines) and lines[i].strip() != 'endfacet':
                    i += 1
            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f'Incomplete triangle near line {i + 1}')
        i += 1
    return triangles


def parse_stl(path):
    issues = []
    with open(path, 'rb') as fh:
        data = fh.read()
    if _looks_like_binary(data):
        fmt = 'binary'
        triangles = parse_binary_stl(data, issues)
    else:
        fmt = 'ascii'
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('latin1', errors='replace')
        triangles = parse_ascii_stl_text(text, issues)
    return fmt, triangles, issues


def validate_file(path):
    issues = []
    if not os.path.exists(path):
        return {'valid': False, 'issues': ['File not found']}
    fmt, triangles, parse_issues = parse_stl(path)
    issues.extend(parse_issues)
    if not triangles:
        issues.append('No valid faces found')
        return {'valid': False, 'issues': issues, 'format': fmt, 'triangle_count': 0}

    edge_count = {}
    degenerate = 0
    inverted = 0
    for declared_normal, verts in triangles:
        rounded = [tuple(round(x, ROUND_DIGITS) for x in v) for v in verts]
        area_vec = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        if rounded[0] == rounded[1] or rounded[1] == rounded[2] or rounded[0] == rounded[2] or _magnitude(area_vec) < EPS:
            degenerate += 1
        else:
            if _magnitude(declared_normal) >= EPS:
                similarity = _dot(_normalize(declared_normal), _normalize(area_vec))
                if similarity < -0.99:
                    inverted += 1
        for j in range(3):
            edge = tuple(sorted((rounded[j], rounded[(j + 1) % 3])))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for c in edge_count.values() if c == 1)
    non_manifold = sum(1 for c in edge_count.values() if c > 2)

    if degenerate:
        issues.append(f'Mesh has {degenerate} degenerate triangle(s)')
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if non_manifold:
        issues.append(f'Mesh has {non_manifold} non-manifold edge(s)')
    if inverted:
        issues.append(f'Mesh has {inverted} facet normal(s) inverted relative to winding order')

    return {'valid': len(issues) == 0, 'issues': issues, 'format': fmt, 'triangle_count': len(triangles)}


def _selftest():
    import tempfile
    ascii_stl = '\n'.join([
        'solid t',
        'facet normal 0 0 1',
        'outer loop',
        'vertex 0 0 0',
        'vertex 1 0 0',
        'vertex 0 1 0',
        'endloop',
        'endfacet',
        'endsolid t',
    ])
    with tempfile.NamedTemporaryFile('w', suffix='.stl', delete=False) as f:
        f.write(ascii_stl)
        ascii_path = f.name
    r1 = validate_file(ascii_path)
    os.unlink(ascii_path)
    assert r1['format'] == 'ascii'
    assert r1['triangle_count'] == 1
    assert any('open edge' in s for s in r1['issues'])

    header = b'Binary STL Test'.ljust(80, b' ')
    tri = struct.pack('<12fH', 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0)
    binary_data = header + struct.pack('<I', 1) + tri
    with tempfile.NamedTemporaryFile('wb', suffix='.stl', delete=False) as f:
        f.write(binary_data)
        binary_path = f.name
    r2 = validate_file(binary_path)
    os.unlink(binary_path)
    assert r2['format'] == 'binary'
    assert r2['triangle_count'] == 1
    assert any('open edge' in s for s in r2['issues'])
    print(json.dumps({'valid': True, 'issues': []}))


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == TEST_MODE:
        _selftest()
        sys.exit(0)
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'issues': ['usage: single_file_binary_ascii_stl_manufacturing_validator_cli.py <file.stl>']}))
        sys.exit(1)
    result = validate_file(sys.argv[1])
    print(json.dumps({'valid': result['valid'], 'issues': result['issues']}))
