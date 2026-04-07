import json, math, os, struct, sys


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
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _normalize(v):
    m = _magnitude(v)
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def _signed_volume(v1, v2, v3):
    return _dot(v1, _cross(v2, v3)) / 6.0


def _is_binary_stl(path):
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
    if not header.lstrip().startswith(b'solid'):
        return True
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
            return triangles, ['Binary STL missing triangle count'], 'binary'
        count = struct.unpack('<I', count_bytes)[0]
        for idx in range(count):
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
    return triangles, issues, 'binary'


def parse_stl(path):
    if _is_binary_stl(path):
        triangles, issues, fmt = _parse_binary(path)
    else:
        triangles, issues, fmt = _parse_ascii(path)
    if not triangles:
        issues.append('No valid faces found')
    edge_count = {}
    directed_edges = {}
    degenerate = 0
    for t_idx, (_, verts) in enumerate(triangles):
        area = _triangle_area(*verts)
        if area < 1e-12:
            degenerate += 1
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            a = rounded[j]
            b = rounded[(j + 1) % 3]
            undirected = tuple(sorted([a, b]))
            edge_count[undirected] = edge_count.get(undirected, 0) + 1
            directed_edges.setdefault(undirected, []).append((a, b, t_idx))
    if degenerate:
        issues.append(f'Mesh has {degenerate} degenerate triangle(s)')
    nonmanifold = sum(1 for c in edge_count.values() if c > 2)
    open_edges = sum(1 for c in edge_count.values() if c == 1)
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if nonmanifold:
        issues.append(f'Mesh has {nonmanifold} non-manifold edge(s) shared by more than 2 triangles')
    inconsistent = 0
    for edge, uses in directed_edges.items():
        if len(uses) == 2:
            if uses[0][0] == uses[1][0] and uses[0][1] == uses[1][1]:
                inconsistent += 1
    if inconsistent:
        issues.append(f'Mesh has {inconsistent} inconsistent shared edge orientation(s)')
    normal_mismatch = 0
    for declared_normal, verts in triangles:
        cross = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        if _magnitude(cross) < 1e-12:
            continue
        dn = _normalize(declared_normal)
        if _magnitude(dn) < 1e-12:
            continue
        wn = _normalize(cross)
        if _dot(dn, wn) < -0.99:
            normal_mismatch += 1
    if normal_mismatch:
        issues.append(f'{normal_mismatch} facet normal(s) are inverted relative to vertex winding')
    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)
    volume = sum(_signed_volume(*verts) for _, verts in triangles)
    if triangles and open_edges == 0 and nonmanifold == 0 and abs(volume) < 1e-12:
        issues.append('Closed mesh has near-zero signed volume')
    return {
        'format': fmt,
        'valid': len(issues) == 0,
        'triangle_count': len(triangles),
        'surface_area': round(surface_area, 6),
        'signed_volume': round(volume, 6),
        'issues': issues,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: improved_stl_validator_ascii_binary.py <file.stl>'}))
        sys.exit(1)
    print(json.dumps(parse_stl(sys.argv[1])))
