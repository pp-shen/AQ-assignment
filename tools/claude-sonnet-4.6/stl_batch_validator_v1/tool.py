"""run_validation.py — validates all 20 supplier STL files and writes validation_report.json."""
import json
import math
import struct
import os


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
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def _is_binary_stl(path):
    with open(path, 'rb') as f:
        header = f.read(80)
    if header[:5].lower() == b'solid':
        try:
            size = os.path.getsize(path)
            with open(path, 'rb') as f:
                f.read(80)
                num_triangles = struct.unpack('<I', f.read(4))[0]
            expected_size = 80 + 4 + num_triangles * 50
            if expected_size == size and num_triangles > 0:
                return True
        except Exception:
            pass
        return False
    return True


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
    return triangles, issues


def parse_binary_stl(path):
    issues = []
    triangles = []
    file_size = os.path.getsize(path)
    with open(path, 'rb') as f:
        f.read(80)
        num_triangles_data = f.read(4)
        if len(num_triangles_data) < 4:
            issues.append('Truncated binary STL header')
            return triangles, issues
        num_triangles = struct.unpack('<I', num_triangles_data)[0]
        expected_size = 80 + 4 + num_triangles * 50
        if expected_size != file_size:
            issues.append(f'Binary file size mismatch: expected {expected_size}, got {file_size}')
        for idx in range(num_triangles):
            data = f.read(50)
            if len(data) < 50:
                issues.append(f'Truncated triangle data at triangle {idx}')
                break
            nx, ny, nz = struct.unpack('<fff', data[0:12])
            v1 = struct.unpack('<fff', data[12:24])
            v2 = struct.unpack('<fff', data[24:36])
            v3 = struct.unpack('<fff', data[36:48])
            triangles.append(((nx, ny, nz), [v1, v2, v3]))
    return triangles, issues


def validate_stl(path):
    issues = []
    try:
        if _is_binary_stl(path):
            triangles, parse_issues = parse_binary_stl(path)
        else:
            triangles, parse_issues = parse_ascii_stl(path)
    except Exception as e:
        return {'valid': False, 'issues': [f'Parse error: {e}']}
    issues.extend(parse_issues)
    if not triangles:
        issues.append('No valid faces found')
        return {'valid': False, 'issues': issues}
    degenerate_count = sum(1 for _, verts in triangles if _triangle_area(*verts) < 1e-12)
    if degenerate_count > 0:
        issues.append(f'degenerate_triangles: {degenerate_count} degenerate triangle(s)')
    face_set = {}
    duplicate_count = 0
    for _, verts in triangles:
        key = tuple(sorted([tuple(round(x, 6) for x in v) for v in verts]))
        if key in face_set:
            duplicate_count += 1
        else:
            face_set[key] = True
    if duplicate_count > 0:
        issues.append(f'duplicate_faces: {duplicate_count} duplicate face(s)')
    edge_count = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1
    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges > 0:
        issues.append(f'open_edges: mesh has {open_edges} open edge(s) — not a closed solid')
    if non_manifold_edges > 0:
        issues.append(f'non_manifold_edges: mesh has {non_manifold_edges} non-manifold edge(s)')
    inverted_count = 0
    for declared_normal, verts in triangles:
        computed_raw = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        computed_norm = _normalize(computed_raw)
        declared_norm = _normalize(declared_normal)
        if _magnitude(declared_normal) < 1e-12 or _magnitude(computed_raw) < 1e-12:
            continue
        if _dot(computed_norm, declared_norm) < 0.0:
            inverted_count += 1
    if inverted_count > 0:
        issues.append(f'inverted_normals: {inverted_count} face(s) have normals inconsistent with winding order')
    return {'valid': len(issues) == 0, 'triangle_count': len(triangles), 'issues': issues}


def classify_reason(issues):
    for issue in issues:
        if 'inverted_normals' in issue:
            return 'inverted_normals'
        if 'open_edges' in issue:
            return 'open_edges'
        if 'non_manifold' in issue:
            return 'non_manifold_edges'
        if 'degenerate' in issue:
            return 'degenerate_triangles'
        if 'duplicate' in issue:
            return 'duplicate_faces'
        if 'No valid faces' in issue:
            return 'no_valid_faces'
        if 'Malformed' in issue or 'Incomplete' in issue or 'Parse error' in issue:
            return 'malformed_geometry'
        if 'size mismatch' in issue or 'Truncated' in issue:
            return 'binary_format_error'
    return 'unknown_issue'


def main():
    files = [f'supplier_{i:03d}.stl' for i in range(1, 21)]
    results = []
    for fname in files:
        result = validate_stl(fname)
        entry = {'file': fname, 'valid': result['valid']}
        if not result['valid']:
            entry['reason'] = classify_reason(result['issues'])
            entry['details'] = result['issues']
        results.append(entry)
    with open('validation_report.json', 'w') as f:
        json.dump({'results': results}, f, indent=2)


if __name__ == '__main__':
    main()
