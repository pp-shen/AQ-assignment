"""stl_batch_validator.py - Improved STL validator with bug fixes, run across all supplier files."""
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


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _magnitude(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _normalize(v):
    m = _magnitude(v)
    if m < 1e-10:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def _is_binary_stl(path):
    try:
        with open(path, 'rb') as f:
            header = f.read(80)
            if len(header) < 80:
                return False
            count_bytes = f.read(4)
            if len(count_bytes) < 4:
                return False
            count = struct.unpack('<I', count_bytes)[0]
            expected_size = 80 + 4 + count * 50
            actual_size = os.path.getsize(path)
            if actual_size == expected_size:
                return True
            if header.lstrip().startswith(b'solid'):
                return False
            return True
    except Exception:
        return False


def _parse_binary_stl(path):
    triangles = []
    issues = []
    try:
        with open(path, 'rb') as f:
            f.read(80)
            count_bytes = f.read(4)
            if len(count_bytes) < 4:
                issues.append('Truncated binary STL file')
                return triangles, issues
            count = struct.unpack('<I', count_bytes)[0]
            for i in range(count):
                data = f.read(50)
                if len(data) < 50:
                    issues.append(f'Truncated triangle data at triangle {i}')
                    break
                nx, ny, nz = struct.unpack('<fff', data[0:12])
                v1 = struct.unpack('<fff', data[12:24])
                v2 = struct.unpack('<fff', data[24:36])
                v3 = struct.unpack('<fff', data[36:48])
                triangles.append(((nx, ny, nz), [v1, v2, v3]))
    except Exception as e:
        issues.append(f'Error reading binary STL: {e}')
    return triangles, issues


def _parse_ascii_stl(path):
    triangles = []
    issues = []
    try:
        with open(path, 'r', errors='replace') as fh:
            lines = fh.readlines()
    except Exception as e:
        issues.append(f'Cannot read file: {e}')
        return triangles, issues

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
                                vertices.append(
                                    (float(vparts[1]), float(vparts[2]), float(vparts[3]))
                                )
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


def validate_stl(path: str) -> dict:
    """Validate an STL file for manufacturing suitability."""
    issues = []
    issue_types = set()

    if _is_binary_stl(path):
        triangles, parse_issues = _parse_binary_stl(path)
    else:
        triangles, parse_issues = _parse_ascii_stl(path)

    issues.extend(parse_issues)
    for pi in parse_issues:
        issue_types.add('parse_error')

    if not triangles:
        issues.append('No valid faces found')
        issue_types.add('no_faces')
        return {
            'valid': False,
            'triangle_count': 0,
            'surface_area': 0.0,
            'issues': issues,
            'issue_types': list(issue_types),
        }

    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = [e for e, cnt in edge_count.items() if cnt == 1]
    non_manifold_edges = [e for e, cnt in edge_count.items() if cnt > 2]

    if open_edges:
        issues.append(f'Mesh has {len(open_edges)} open edge(s) - not a closed solid')
        issue_types.add('open_edges')

    if non_manifold_edges:
        issues.append(f'Mesh has {len(non_manifold_edges)} non-manifold edge(s)')
        issue_types.add('non_manifold_edges')

    degenerate_count = 0
    for _, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            degenerate_count += 1

    if degenerate_count > 0:
        issues.append(f'{degenerate_count} degenerate triangle(s) (zero area)')
        issue_types.add('degenerate_triangles')

    # FIXED: threshold < 0 (not < -0.99 as in buggy original)
    inverted_count = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue
        winding_normal = _normalize(cross)
        dn_norm = _normalize(declared_normal)
        if _magnitude(declared_normal) < 1e-10:
            continue
        similarity = _dot(dn_norm, winding_normal)
        if similarity < 0:
            inverted_count += 1

    if inverted_count > 0:
        issues.append(f'{inverted_count} face(s) with inverted normal(s)')
        issue_types.add('inverted_normals')

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        'valid': len(issues) == 0,
        'triangle_count': len(triangles),
        'surface_area': round(surface_area, 6),
        'issues': issues,
        'issue_types': list(issue_types),
    }


def get_primary_reason(issue_types):
    priority = ['no_faces', 'parse_error', 'open_edges', 'non_manifold_edges',
                'degenerate_triangles', 'inverted_normals']
    for p in priority:
        if p in issue_types:
            return p
    return issue_types[0] if issue_types else 'unknown'


def batch_validate(file_list):
    """Validate a list of STL file paths and return results list."""
    results = []
    for fname in file_list:
        try:
            result = validate_stl(fname)
            entry = {'file': fname, 'valid': result['valid']}
            if not result['valid']:
                entry['reason'] = get_primary_reason(result['issue_types'])
                entry['issues'] = result['issues']
            results.append(entry)
        except Exception as e:
            results.append({'file': fname, 'valid': False, 'reason': 'error', 'issues': [str(e)]})
    return results
