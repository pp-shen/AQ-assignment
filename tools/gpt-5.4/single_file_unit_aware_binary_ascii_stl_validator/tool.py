import json
import math
import os
import struct
import sys

INCH_TO_MM = 25.4
AREA_SCALE = INCH_TO_MM * INCH_TO_MM
EPS = 1e-9


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


def _triangle_area(v1, v2, v3):
    return 0.5 * _mag(_cross(_sub(v2, v1), _sub(v3, v1)))


def _round_vertex(v):
    return tuple(round(c, 6) for c in v)


def _is_binary_stl(path):
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


def _parse_ascii_stl(path):
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
            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f'Incomplete triangle near line {i + 1}')
            continue
        i += 1
    return triangles, issues


def _parse_binary_stl(path):
    issues = []
    triangles = []
    with open(path, 'rb') as fh:
        header = fh.read(80)
        if len(header) < 80:
            return [], ['File too short for binary STL header']
        raw = fh.read(4)
        if len(raw) < 4:
            return [], ['File too short for binary STL triangle count']
        tri_count = struct.unpack('<I', raw)[0]
        for idx in range(tri_count):
            rec = fh.read(50)
            if len(rec) < 50:
                issues.append(f'Triangle record {idx} truncated')
                break
            vals = struct.unpack('<12fH', rec)
            declared_normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((declared_normal, [v1, v2, v3]))
    return triangles, issues


def validate_stl(path):
    triangles, issues = (_parse_binary_stl(path) if _is_binary_stl(path) else _parse_ascii_stl(path))
    if not triangles:
        issues.append('No valid faces found')
    edge_count = {}
    inconsistent_normals = 0
    degenerate = 0
    for declared_normal, verts in triangles:
        v1, v2, v3 = verts
        cross = _cross(_sub(v2, v1), _sub(v3, v1))
        cross_mag = _mag(cross)
        if cross_mag <= EPS:
            degenerate += 1
        else:
            decl_mag = _mag(declared_normal)
            if decl_mag > EPS and _dot(cross, declared_normal) < 0:
                inconsistent_normals += 1
        rounded = [_round_vertex(v) for v in verts]
        for j in range(3):
            edge = tuple(sorted((rounded[j], rounded[(j + 1) % 3])))
            edge_count[edge] = edge_count.get(edge, 0) + 1
    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if non_manifold_edges:
        issues.append(f'Mesh has {non_manifold_edges} non-manifold edge(s)')
    if degenerate:
        issues.append(f'Mesh has {degenerate} degenerate triangle(s)')
    if inconsistent_normals:
        issues.append(f'Mesh has {inconsistent_normals} facet(s) with inverted/inconsistent declared normals')
    surface_area_in2 = sum(_triangle_area(*verts) for _, verts in triangles)
    surface_area_mm2 = round(surface_area_in2 * AREA_SCALE, 6)
    return {
        'valid': len(issues) == 0,
        'surface_area_mm2': surface_area_mm2,
        'issues': issues,
    }


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'surface_area_mm2': 0.0, 'issues': ['usage: validator.py <file.stl>']}))
        sys.exit(1)
    print(json.dumps(validate_stl(sys.argv[1])))
