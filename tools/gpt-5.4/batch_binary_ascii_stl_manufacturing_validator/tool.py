import json
import math
import os
import struct
import sys
from collections import Counter


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
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def tri_area(v1, v2, v3):
    return 0.5 * mag(cross(sub(v2, v1), sub(v3, v1)))


def is_binary_stl(path):
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
    try:
        with open(path, 'rb') as f:
            prefix = f.read(256)
        prefix.decode('ascii')
        return False
    except Exception:
        return True


def parse_ascii(path):
    with open(path, 'r', errors='replace') as f:
        lines = f.readlines()
    triangles = []
    issues = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('facet normal'):
            parts = line.split()
            try:
                declared = (float(parts[2]), float(parts[3]), float(parts[4]))
            except Exception:
                issues.append(f'Malformed facet normal at line {i+1}')
                i += 1
                continue
            i += 1
            if i >= len(lines) or lines[i].strip() != 'outer loop':
                issues.append(f'Missing outer loop after facet at line {i+1}')
                continue
            i += 1
            verts = []
            for _ in range(3):
                if i >= len(lines):
                    break
                parts = lines[i].strip().split()
                if len(parts) >= 4 and parts[0] == 'vertex':
                    try:
                        verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                    except Exception:
                        issues.append(f'Malformed vertex at line {i+1}')
                else:
                    issues.append(f'Expected vertex at line {i+1}')
                i += 1
            while i < len(lines) and lines[i].strip() != 'endfacet':
                i += 1
            if i < len(lines):
                i += 1
            if len(verts) == 3:
                triangles.append((declared, verts))
            else:
                issues.append(f'Incomplete triangle near line {i+1}')
            continue
        i += 1
    return triangles, issues


def parse_binary(path):
    triangles = []
    issues = []
    with open(path, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) != 4:
            return [], ['Binary STL missing triangle count']
        count = struct.unpack('<I', count_bytes)[0]
        for idx in range(count):
            rec = f.read(50)
            if len(rec) != 50:
                issues.append(f'Triangle record {idx} truncated')
                break
            vals = struct.unpack('<12fH', rec)
            declared = tuple(vals[0:3])
            v1 = tuple(vals[3:6])
            v2 = tuple(vals[6:9])
            v3 = tuple(vals[9:12])
            triangles.append((declared, [v1, v2, v3]))
    return triangles, issues


def validate_triangles(triangles, initial_issues=None):
    issues = list(initial_issues or [])
    edge_counter = Counter()
    degenerate = 0
    inverted = 0
    surface_area = 0.0
    for declared, verts in triangles:
        area = tri_area(*verts)
        surface_area += area
        if area < 1e-12:
            degenerate += 1
        e = [tuple(round(c, 6) for c in v) for v in verts]
        for i in range(3):
            edge = tuple(sorted((e[i], e[(i + 1) % 3])))
            edge_counter[edge] += 1
        n = cross(sub(verts[1], verts[0]), sub(verts[2], verts[0]))
        if mag(n) >= 1e-12 and mag(declared) >= 1e-12:
            if dot(norm(n), norm(declared)) < -0.99:
                inverted += 1
    open_edges = sum(1 for c in edge_counter.values() if c == 1)
    non_manifold_edges = sum(1 for c in edge_counter.values() if c > 2)
    if not triangles:
        issues.append('No valid faces found')
    if degenerate:
        issues.append(f'Mesh has {degenerate} degenerate triangle(s)')
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if non_manifold_edges:
        issues.append(f'Mesh has {non_manifold_edges} non-manifold edge(s)')
    if inverted:
        issues.append(f'Mesh has {inverted} inverted declared normal(s) relative to winding order')
    return {
        'valid': len(issues) == 0,
        'triangle_count': len(triangles),
        'surface_area': round(surface_area, 6),
        'issues': issues,
        'manufacturing_suitable': len(issues) == 0,
    }


def validate_file(path):
    if is_binary_stl(path):
        triangles, issues = parse_binary(path)
        result = validate_triangles(triangles, issues)
        result['format'] = 'binary'
        return result
    triangles, issues = parse_ascii(path)
    result = validate_triangles(triangles, issues)
    result['format'] = 'ascii'
    return result


def main():
    files = [f for f in os.listdir('.') if f.lower().endswith('.stl')]
    print(json.dumps({
        'files': [{ 'file': f, 'result': validate_file(f)} for f in files]
    }, indent=2))


if __name__ == '__main__':
    main()
