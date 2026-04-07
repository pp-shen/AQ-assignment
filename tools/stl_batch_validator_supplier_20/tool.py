"""
Batch STL validator using proven logic from tool library.
Checks: manifold closure, inverted normals (winding-order), degenerate triangles.
Outputs validation_report.json.
"""
import json
import math
import os
import glob


def _cross(a, b):
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )


def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _magnitude(v):
    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)


def _normalize(v):
    m = _magnitude(v)
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0]/m, v[1]/m, v[2]/m)


def parse_stl(path: str) -> dict:
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
                issues.append(f'Malformed facet normal at line {i+1}')
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
                                vertices.append((
                                    float(vparts[1]),
                                    float(vparts[2]),
                                    float(vparts[3])
                                ))
                            except (IndexError, ValueError):
                                issues.append(f'Malformed vertex at line {i+1}')
                        i += 1

            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f'Incomplete triangle near line {i+1}')
            continue

        i += 1

    if not triangles:
        issues.append('No valid faces found')
        return {'valid': False, 'triangle_count': 0, 'surface_area': 0.0, 'issues': issues}

    edge_count = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j+1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges > 0:
        issues.append(f'open_edges: mesh has {open_edges} open edge(s) - not a closed solid')
    if non_manifold_edges > 0:
        issues.append(f'non_manifold: mesh has {non_manifold_edges} non-manifold edge(s)')

    inverted_count = 0
    degenerate_count = 0
    for declared_normal, verts in triangles:
        v1, v2, v3 = verts
        computed = _cross(_sub(v2, v1), _sub(v3, v1))
        mag = _magnitude(computed)
        if mag < 1e-12:
            degenerate_count += 1
            continue
        computed_norm = _normalize(computed)
        dn_mag = _magnitude(declared_normal)
        if dn_mag < 1e-12:
            continue
        dn_norm = _normalize(declared_normal)
        dot = _dot(computed_norm, dn_norm)
        if dot < 0.0:
            inverted_count += 1

    if degenerate_count > 0:
        issues.append(f'degenerate_triangles: {degenerate_count} zero-area triangle(s)')
    if inverted_count > 0:
        issues.append(f'inverted_normals: {inverted_count} face(s) with inverted normals')

    surface_area = sum(
        0.5 * _magnitude(_cross(_sub(v[1], v[0]), _sub(v[2], v[0])))
        for _, v in triangles
    )

    return {
        'valid': len(issues) == 0,
        'triangle_count': len(triangles),
        'surface_area': round(surface_area, 6),
        'issues': issues,
    }


def classify_reason(issues):
    reasons = []
    for iss in issues:
        if 'inverted_normals' in iss:
            reasons.append('inverted_normals')
        elif 'open_edges' in iss:
            reasons.append('open_edges')
        elif 'non_manifold' in iss:
            reasons.append('non_manifold')
        elif 'degenerate_triangles' in iss:
            reasons.append('degenerate_triangles')
        elif 'No valid faces' in iss:
            reasons.append('no_valid_faces')
        else:
            reasons.append('invalid')
    return ', '.join(reasons) if reasons else 'invalid'


def main():
    files = sorted(glob.glob('supplier_*.stl'))
    if not files:
        print('No supplier_*.stl files found!')
        return

    results = []
    for filepath in files:
        filename = os.path.basename(filepath)
        result = parse_stl(filepath)
        entry = {'file': filename, 'valid': result['valid']}
        if not result['valid']:
            entry['reason'] = classify_reason(result['issues'])
        results.append(entry)
        status = 'PASS' if result['valid'] else f'FAIL ({entry.get("reason", "")})'
        print(f'  {filename}: {status}')
        if result['issues']:
            for iss in result['issues']:
                print(f'    - {iss}')

    report = {'results': results}
    with open('validation_report.json', 'w') as fh:
        json.dump(report, fh, indent=2)

    total = len(results)
    passed = sum(1 for r in results if r['valid'])
    failed = total - passed
    print(f'\nSummary: {passed}/{total} passed, {failed} failed')
    print('Report written to validation_report.json')


if __name__ == '__main__':
    main()
