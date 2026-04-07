import json
import math
import struct
import sys
from collections import Counter, defaultdict

EPS = 1e-9
ROUND_DP = 6


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def mag(v):
    return math.sqrt(dot(v, v))


def norm(v):
    m = mag(v)
    if m < EPS:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def triangle_area(v1, v2, v3):
    return 0.5 * mag(cross(sub(v2, v1), sub(v3, v1)))


def signed_tet_volume(v1, v2, v3):
    return dot(v1, cross(v2, v3)) / 6.0


def q(v):
    return tuple(round(x, ROUND_DP) for x in v)


def detect_binary(data):
    if len(data) >= 84:
        tri_count = struct.unpack('<I', data[80:84])[0]
        expected = 84 + tri_count * 50
        if expected == len(data):
            return True
    return False


def parse_binary(data):
    issues = []
    if len(data) < 84:
        return [], ['Binary STL too short']
    tri_count = struct.unpack('<I', data[80:84])[0]
    expected = 84 + tri_count * 50
    if expected != len(data):
        return [], [f'Binary STL size mismatch: header count implies {expected} bytes, file has {len(data)}']
    triangles = []
    offset = 84
    for i in range(tri_count):
        rec = data[offset:offset + 50]
        if len(rec) != 50:
            issues.append(f'Incomplete binary triangle record at index {i}')
            break
        vals = struct.unpack('<12fH', rec)
        declared = (vals[0], vals[1], vals[2])
        v1 = (vals[3], vals[4], vals[5])
        v2 = (vals[6], vals[7], vals[8])
        v3 = (vals[9], vals[10], vals[11])
        triangles.append((declared, [v1, v2, v3]))
        offset += 50
    return triangles, issues


def parse_ascii(data):
    issues = []
    try:
        text = data.decode('utf-8', errors='replace')
    except Exception:
        return [], ['Could not decode ASCII STL']
    lines = text.splitlines()
    triangles = []
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
                    break
                parts = vline.split()
                try:
                    verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except Exception:
                    issues.append(f'Malformed vertex at line {i + 1}')
                i += 1
            if len(verts) != 3:
                issues.append(f'Incomplete triangle near line {i + 1}')
                continue
            if i >= len(lines) or lines[i].strip() != 'endloop':
                issues.append(f'Missing endloop near line {i + 1}')
                continue
            i += 1
            if i >= len(lines) or lines[i].strip() != 'endfacet':
                issues.append(f'Missing endfacet near line {i + 1}')
                continue
            triangles.append((declared, verts))
        i += 1
    if not triangles:
        issues.append('No valid faces found')
    return triangles, issues


def load_stl(path):
    with open(path, 'rb') as f:
        data = f.read()
    if detect_binary(data):
        tris, issues = parse_binary(data)
        return 'binary', tris, issues
    tris, issues = parse_ascii(data)
    return 'ascii', tris, issues


def validate(path):
    issues = []
    fmt, triangles, parse_issues = load_stl(path)
    issues.extend(parse_issues)
    if not triangles:
        return {'valid': False, 'issues': issues}

    edge_counts = Counter()
    oriented = defaultdict(int)
    degenerate = 0
    zero_normals = 0
    normal_mismatch = 0
    area = 0.0
    volume = 0.0

    for declared, verts in triangles:
        v1, v2, v3 = verts
        area_i = triangle_area(v1, v2, v3)
        area += area_i
        volume += signed_tet_volume(v1, v2, v3)
        if area_i < EPS:
            degenerate += 1
            continue
        wn = norm(cross(sub(v2, v1), sub(v3, v1)))
        dn = norm(declared)
        if mag(declared) < EPS:
            zero_normals += 1
        else:
            sim = dot(dn, wn)
            if sim < 0.0:
                normal_mismatch += 1
        rv = [q(v) for v in verts]
        for a, b in ((rv[0], rv[1]), (rv[1], rv[2]), (rv[2], rv[0])):
            edge_counts[tuple(sorted((a, b)))] += 1
            oriented[(a, b)] += 1

    if degenerate:
        issues.append(f'{degenerate} degenerate triangle(s)')
    if zero_normals:
        issues.append(f'{zero_normals} face normal(s) are zero-length')
    if normal_mismatch:
        issues.append(f'{normal_mismatch} face normal(s) disagree with vertex winding')

    open_edges = sum(1 for c in edge_counts.values() if c == 1)
    nonmanifold_edges = sum(1 for c in edge_counts.values() if c > 2)
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s)')
    if nonmanifold_edges:
        issues.append(f'Mesh has {nonmanifold_edges} non-manifold edge(s)')

    inconsistent_orientation = 0
    for undirected, count in edge_counts.items():
        if count == 2:
            a, b = undirected
            if oriented[(a, b)] != 1 or oriented[(b, a)] != 1:
                inconsistent_orientation += 1
    if inconsistent_orientation:
        issues.append(f'Mesh has {inconsistent_orientation} inconsistently oriented shared edge(s)')

    if area < 1e-9:
        issues.append('Surface area is zero')
    if abs(volume) < 1e-9:
        issues.append('Signed volume is zero; mesh is not a proper closed solid')

    return {'valid': len(issues) == 0, 'issues': issues}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'issues': ['usage: validate_stl_cli.py <file.stl>']}))
        sys.exit(1)
    try:
        result = validate(sys.argv[1])
    except Exception as e:
        result = {'valid': False, 'issues': [f'Unhandled error: {e}']}
    print(json.dumps(result))
