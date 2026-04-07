import json
import math
import os
import struct
import sys
from collections import defaultdict, deque

EPS = 1e-9
ROUND_DP = 6


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def mag(v):
    return math.sqrt(dot(v, v))


def normalize(v):
    m = mag(v)
    if m < EPS:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def rounded_vertex(v):
    return (round(v[0], ROUND_DP), round(v[1], ROUND_DP), round(v[2], ROUND_DP))


def is_binary_stl(path):
    size = os.path.getsize(path)
    if size < 84:
        return False
    with open(path, 'rb') as f:
        header = f.read(84)
    if len(header) < 84:
        return False
    tri_count = struct.unpack('<I', header[80:84])[0]
    expected = 84 + tri_count * 50
    return expected == size


def parse_binary_stl(path):
    issues = []
    triangles = []
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < 84:
        return triangles, ['File too small to be valid STL']
    tri_count = struct.unpack('<I', data[80:84])[0]
    expected = 84 + tri_count * 50
    if expected != len(data):
        issues.append('Binary STL size does not match triangle count in header')
    offset = 84
    max_tris = max(0, (len(data) - 84) // 50)
    actual = min(tri_count, max_tris)
    for i in range(actual):
        chunk = data[offset:offset + 50]
        if len(chunk) < 50:
            issues.append(f'Incomplete binary triangle record at index {i}')
            break
        vals = struct.unpack('<12fH', chunk)
        n = (vals[0], vals[1], vals[2])
        v1 = (vals[3], vals[4], vals[5])
        v2 = (vals[6], vals[7], vals[8])
        v3 = (vals[9], vals[10], vals[11])
        triangles.append((n, [v1, v2, v3]))
        offset += 50
    if not triangles:
        issues.append('No valid faces found')
    return triangles, issues


def parse_ascii_stl(path):
    issues = []
    triangles = []
    try:
        with open(path, 'r', errors='replace') as fh:
            lines = fh.readlines()
    except Exception as e:
        return [], [f'Failed to read ASCII STL: {e}']
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('facet normal'):
            parts = line.split()
            try:
                declared_normal = (float(parts[2]), float(parts[3]), float(parts[4]))
            except Exception:
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
                            except Exception:
                                issues.append(f'Malformed vertex at line {i + 1}')
                        i += 1
            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f'Incomplete triangle near line {i + 1}')
            continue
        i += 1
    if not triangles:
        issues.append('No valid faces found')
    return triangles, issues


def parse_stl(path):
    if is_binary_stl(path):
        return parse_binary_stl(path)
    return parse_ascii_stl(path)


def validate_triangles(triangles, issues):
    edge_count = defaultdict(int)
    directed_edge_faces = defaultdict(list)
    face_edges = []
    degenerate_count = 0
    zero_normal_count = 0
    area = 0.0
    signed_volume = 0.0

    for idx, (declared_normal, verts) in enumerate(triangles):
        rv = [rounded_vertex(v) for v in verts]
        repeated = rv[0] == rv[1] or rv[1] == rv[2] or rv[0] == rv[2]
        e1 = sub(verts[1], verts[0])
        e2 = sub(verts[2], verts[0])
        c = cross(e1, e2)
        c_mag = mag(c)
        tri_area = 0.5 * c_mag
        area += tri_area
        if repeated or tri_area < EPS:
            degenerate_count += 1
        else:
            dn = normalize(declared_normal)
            wn = normalize(c)
            if mag(declared_normal) < EPS:
                zero_normal_count += 1
            else:
                sim = dot(dn, wn)
                if sim < 0.0:
                    issues.append(f'Face normal disagrees with winding order at triangle {idx} (similarity={sim:.3f})')
        signed_volume += dot(verts[0], cross(verts[1], verts[2])) / 6.0
        edges = []
        for j in range(3):
            a = rv[j]
            b = rv[(j + 1) % 3]
            undirected = tuple(sorted((a, b)))
            edge_count[undirected] += 1
            directed_edge_faces[(a, b)].append(idx)
            edges.append((a, b, undirected))
        face_edges.append(edges)

    if degenerate_count > 0:
        issues.append(f'Mesh has {degenerate_count} degenerate triangle(s)')
    if zero_normal_count > 0:
        issues.append(f'Mesh has {zero_normal_count} face(s) with zero declared normal')
    if area < EPS:
        issues.append('Surface area is zero')
    if abs(signed_volume) < EPS:
        issues.append('Signed volume is zero or near zero')

    open_edges = sum(1 for c in edge_count.values() if c == 1)
    nonmanifold_edges = sum(1 for c in edge_count.values() if c > 2)
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s)')
    if nonmanifold_edges:
        issues.append(f'Mesh has {nonmanifold_edges} non-manifold edge(s)')

    adjacency = defaultdict(list)
    same_dir_inconsistencies = 0
    for face_idx, edges in enumerate(face_edges):
        for a, b, _ in edges:
            opp = directed_edge_faces[(b, a)]
            same = directed_edge_faces[(a, b)]
            for nbr in opp:
                if nbr != face_idx:
                    adjacency[face_idx].append(nbr)
            if len(same) > 1:
                same_dir_inconsistencies += 1
    if same_dir_inconsistencies:
        issues.append(f'Mesh has {same_dir_inconsistencies} orientation-inconsistent shared edge reference(s)')

    visited = set()
    components = 0
    for i in range(len(triangles)):
        if i in visited:
            continue
        components += 1
        dq = deque([i])
        visited.add(i)
        while dq:
            cur = dq.popleft()
            for nb in adjacency[cur]:
                if nb not in visited:
                    visited.add(nb)
                    dq.append(nb)
    if components > 1 and triangles:
        issues.append(f'Mesh has {components} disconnected component(s)')

    return {'valid': len(issues) == 0, 'issues': issues}


def main():
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'issues': ['usage: stl_validate_cli.py <file.stl>']}))
        sys.exit(1)
    path = sys.argv[1]
    triangles, parse_issues = parse_stl(path)
    result = validate_triangles(triangles, list(parse_issues))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
