import json
import math
import struct
import sys
from collections import defaultdict

EPS = 1e-9
ROUND_DP = 6


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
    if m < EPS:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _round_v(v):
    return tuple(round(c, ROUND_DP) for c in v)


def _looks_like_binary(data):
    if len(data) < 84:
        return False
    tri_count = struct.unpack('<I', data[80:84])[0]
    expected = 84 + tri_count * 50
    if expected == len(data):
        return True
    prefix = data[:256].lstrip()
    if prefix.startswith(b'solid'):
        return False
    return False


def _parse_binary(path):
    issues = []
    triangles = []
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < 84:
        return [], ['File too small to be valid binary STL']
    tri_count = struct.unpack('<I', data[80:84])[0]
    expected = 84 + tri_count * 50
    if expected != len(data):
        return [], [f'Binary STL size mismatch: header count={tri_count}, expected {expected} bytes, got {len(data)}']
    offset = 84
    for i in range(tri_count):
        rec = data[offset:offset + 50]
        if len(rec) != 50:
            issues.append(f'Triangle record {i} truncated')
            break
        vals = struct.unpack('<12fH', rec)
        n = (vals[0], vals[1], vals[2])
        v1 = (vals[3], vals[4], vals[5])
        v2 = (vals[6], vals[7], vals[8])
        v3 = (vals[9], vals[10], vals[11])
        triangles.append((n, [v1, v2, v3]))
        offset += 50
    return triangles, issues


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
                        else:
                            issues.append(f'Expected vertex at line {i + 1}')
                        i += 1
                if i < len(lines) and lines[i].strip() == 'endloop':
                    i += 1
                if i < len(lines) and lines[i].strip() == 'endfacet':
                    i += 1
            else:
                issues.append(f'Expected outer loop after facet at line {i + 1}')
            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f'Incomplete triangle near line {i + 1}')
            continue
        i += 1
    return triangles, issues


def parse_stl(path):
    issues = []
    with open(path, 'rb') as f:
        data = f.read()
    is_binary = _looks_like_binary(data)
    if is_binary:
        triangles, parse_issues = _parse_binary(path)
    else:
        triangles, parse_issues = _parse_ascii(path)
    issues.extend(parse_issues)
    if not triangles:
        issues.append('No valid faces found')
        return {'valid': False, 'issues': issues}

    edge_count = defaultdict(int)
    oriented_edges = defaultdict(int)
    area = 0.0
    signed_volume = 0.0
    degenerate = 0

    for declared_normal, verts in triangles:
        v1, v2, v3 = verts
        tri_cross = _cross(_sub(v2, v1), _sub(v3, v1))
        tri_area = 0.5 * _mag(tri_cross)
        area += tri_area
        if tri_area < EPS:
            degenerate += 1
            continue
        signed_volume += _dot(v1, _cross(v2, v3)) / 6.0
        rv = [_round_v(v) for v in verts]
        for j in range(3):
            a = rv[j]
            b = rv[(j + 1) % 3]
            edge_count[tuple(sorted((a, b)))] += 1
            oriented_edges[(a, b)] += 1

        dn = _normalize(declared_normal)
        wn = _normalize(tri_cross)
        if _mag(declared_normal) >= EPS:
            sim = _dot(dn, wn)
            if sim < -0.99:
                issues.append('Face normal is inverted relative to winding order')
            elif sim < 0.9:
                issues.append('Face normal significantly disagrees with geometric normal')

    if degenerate:
        issues.append(f'Mesh has {degenerate} degenerate triangle(s)')

    open_edges = sum(1 for c in edge_count.values() if c == 1)
    nonmanifold = sum(1 for c in edge_count.values() if c > 2)
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if nonmanifold:
        issues.append(f'Mesh has {nonmanifold} non-manifold edge(s)')

    inconsistent = 0
    for undirected, c in edge_count.items():
        if c == 2:
            a, b = undirected
            if oriented_edges[(a, b)] == 2 or oriented_edges[(b, a)] == 2:
                inconsistent += 1
    if inconsistent:
        issues.append(f'Mesh has {inconsistent} edge(s) with inconsistent face winding')

    if area < EPS:
        issues.append('Surface area is zero')
    if abs(signed_volume) < EPS and open_edges == 0:
        issues.append('Closed mesh has near-zero signed volume')

    return {'valid': len(issues) == 0, 'issues': issues}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'issues': ['usage: stl_validator.py <file.stl>']}))
        sys.exit(1)
    print(json.dumps(parse_stl(sys.argv[1])))
