import json
import math
import os
import struct
import sys

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


def _norm(v):
    m = _mag(v)
    if m <= EPS:
        return None
    return (v[0] / m, v[1] / m, v[2] / m)


def _detect_binary(path):
    size = os.path.getsize(path)
    if size < 84:
        return False
    with open(path, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) != 4:
            return False
        tri_count = struct.unpack('<I', count_bytes)[0]
        expected = 84 + tri_count * 50
        if expected == size:
            return True
        if not header.lstrip().startswith(b'solid'):
            return True
    return False


def _parse_binary(path):
    issues = []
    triangles = []
    with open(path, 'rb') as f:
        f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) != 4:
            return [], ['Binary STL too short for triangle count']
        tri_count = struct.unpack('<I', count_bytes)[0]
        for idx in range(tri_count):
            rec = f.read(50)
            if len(rec) != 50:
                issues.append(f'Triangle record {idx} truncated')
                break
            vals = struct.unpack('<12fH', rec)
            n = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((n, [v1, v2, v3]))
        if f.read(1):
            issues.append('Binary STL has trailing extra data')
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
            if i >= len(lines) or lines[i].strip() != 'outer loop':
                issues.append(f"Missing 'outer loop' after facet at line {i}")
                continue
            i += 1
            for _ in range(3):
                if i >= len(lines):
                    break
                vline = lines[i].strip()
                if not vline.startswith('vertex'):
                    issues.append(f'Malformed vertex at line {i + 1}')
                    i += 1
                    continue
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
            while i < len(lines) and lines[i].strip() not in ('endfacet', 'facet normal'):
                i += 1
            if i < len(lines) and lines[i].strip() == 'endfacet':
                i += 1
            continue
        i += 1
    if not triangles:
        issues.append('No valid faces found')
    return triangles, issues


def validate(path):
    issues = []
    try:
        is_binary = _detect_binary(path)
        triangles, parse_issues = _parse_binary(path) if is_binary else _parse_ascii(path)
        issues.extend(parse_issues)
    except Exception as e:
        return {'valid': False, 'issues': [f'Failed to read STL: {e}']}

    edge_count = {}
    degenerate = 0
    inverted_normals = 0

    for declared_normal, verts in triangles:
        v1, v2, v3 = verts
        e1 = _sub(v2, v1)
        e2 = _sub(v3, v1)
        computed = _cross(e1, e2)
        area2 = _mag(computed)
        if area2 <= 1e-9:
            degenerate += 1
        else:
            dn = _norm(declared_normal)
            cn = _norm(computed)
            if dn is not None and cn is not None and _dot(dn, cn) < 0.0:
                inverted_normals += 1
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
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
    if inverted_normals:
        issues.append(f'Mesh has {inverted_normals} facet(s) with inverted declared normals')

    return {'valid': len(issues) == 0, 'issues': issues}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'issues': ['usage: validator.py <file.stl>']}))
        sys.exit(1)
    print(json.dumps(validate(sys.argv[1])))
