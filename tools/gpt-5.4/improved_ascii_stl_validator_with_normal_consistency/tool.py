import json
import math
import sys
from typing import List, Tuple, Dict


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _magnitude(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _normalize(v):
    mag = _magnitude(v)
    if mag == 0:
        return (0.0, 0.0, 0.0)
    return (v[0]/mag, v[1]/mag, v[2]/mag)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def parse_stl(path: str) -> dict:
    issues: List[str] = []
    triangles: List[Tuple[Tuple[float,float,float], List[Tuple[float,float,float]]]] = []

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

    if not triangles:
        issues.append('No valid faces found')

    edge_count: Dict[tuple, int] = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    nonmanifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges > 0:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if nonmanifold_edges > 0:
        issues.append(f'Mesh has {nonmanifold_edges} non-manifold edge(s) shared by more than 2 triangles')

    inverted_normals = 0
    zero_area = 0
    zero_declared_normals = 0
    for declared_normal, verts in triangles:
        v1, v2, v3 = verts
        implied = _cross(_sub(v2, v1), _sub(v3, v1))
        mag = _magnitude(implied)
        if mag == 0:
            zero_area += 1
            continue
        implied_n = _normalize(implied)
        declared_n = _normalize(declared_normal)
        if _magnitude(declared_n) == 0:
            zero_declared_normals += 1
            continue
        if _dot(implied_n, declared_n) < 0.999:
            inverted_normals += 1

    if zero_area > 0:
        issues.append(f'Mesh has {zero_area} degenerate zero-area triangle(s)')
    if zero_declared_normals > 0:
        issues.append(f'Mesh has {zero_declared_normals} facet(s) with zero declared normal')
    if inverted_normals > 0:
        issues.append(f'Mesh has {inverted_normals} face normal(s) inconsistent with vertex winding')

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)
    return {
        'valid': len(issues) == 0,
        'triangle_count': len(triangles),
        'surface_area': round(surface_area, 6),
        'issues': issues,
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: improved_ascii_stl_validator_with_normal_consistency.py <file.stl>'}))
        sys.exit(1)
    print(json.dumps(parse_stl(sys.argv[1])))
