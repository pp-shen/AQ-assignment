import json
import math
import os
import struct
import sys

INCH_TO_MM = 25.4
IN2_TO_MM2 = INCH_TO_MM * INCH_TO_MM
EPS = 1e-10
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
    if m < EPS:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _triangle_area(v1, v2, v3):
    return 0.5 * _mag(_cross(_sub(v2, v1), _sub(v3, v1)))


def _detect_binary(path):
    size = os.path.getsize(path)
    if size < 84:
        return False
    with open(path, 'rb') as f:
        header = f.read(84)
    if len(header) < 84:
        return False
    tri_count = struct.unpack('<I', header[80:84])[0]
    expected = 84 + tri_count * 50
    if expected == size:
        return True
    return False


def _parse_ascii(path, issues):
    triangles = []
    with open(path, 'r', errors='replace') as fh:
        lines = fh.readlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('facet normal'):
            parts = line.split()
            try:
                declared = (float(parts[2]), float(parts[3]), float(parts[4]))
            except (IndexError, ValueError):
                issues.append(f'Malformed facet normal at line {i + 1}')
                i += 1
                continue
            i += 1
            if i >= len(lines) or lines[i].strip() != 'outer loop':
                issues.append(f"Missing 'outer loop' after facet at line {i}")
                continue
            i += 1
            verts = []
            for _ in range(3):
                if i >= len(lines):
                    break
                vline = lines[i].strip()
                if not vline.startswith('vertex'):
                    issues.append(f'Malformed vertex at line {i + 1}')
                    i += 1
                    continue
                parts = vline.split()
                try:
                    verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except (IndexError, ValueError):
                    issues.append(f'Malformed vertex at line {i + 1}')
                i += 1
            if i >= len(lines) or lines[i].strip() != 'endloop':
                issues.append(f"Missing 'endloop' near line {i + 1}")
            else:
                i += 1
            if i >= len(lines) or lines[i].strip() != 'endfacet':
                issues.append(f"Missing 'endfacet' near line {i + 1}")
            else:
                i += 1
            if len(verts) == 3:
                triangles.append((declared, verts))
            else:
                issues.append(f'Incomplete triangle near line {i + 1}')
            continue
        i += 1
    return triangles


def _parse_binary(path, issues):
    triangles = []
    with open(path, 'rb') as fh:
        data = fh.read()
    if len(data) < 84:
        issues.append('Binary STL too short')
        return triangles
    tri_count = struct.unpack('<I', data[80:84])[0]
    expected = 84 + tri_count * 50
    if expected != len(data):
        issues.append('Binary STL size does not match triangle count in header')
        tri_count = max(0, (len(data) - 84) // 50)
    offset = 84
    for idx in range(tri_count):
        if offset + 50 > len(data):
            issues.append(f'Unexpected EOF reading triangle {idx}')
            break
        vals = struct.unpack('<12fH', data[offset:offset + 50])
        declared = (vals[0], vals[1], vals[2])
        verts = [
            (vals[3], vals[4], vals[5]),
            (vals[6], vals[7], vals[8]),
            (vals[9], vals[10], vals[11]),
        ]
        triangles.append((declared, verts))
        offset += 50
    return triangles


def validate(path):
    issues = []
    try:
        is_binary = _detect_binary(path)
        triangles = _parse_binary(path, issues) if is_binary else _parse_ascii(path, issues)
    except FileNotFoundError:
        return {'valid': False, 'surface_area_mm2': 0.0, 'issues': ['File not found']}
    except Exception as e:
        return {'valid': False, 'surface_area_mm2': 0.0, 'issues': [f'Parse error: {e}']}

    if not triangles:
        issues.append('No valid faces found')

    edge_count = {}
    degenerate = 0
    for declared, verts in triangles:
        area = _triangle_area(*verts)
        if area < EPS:
            degenerate += 1
        rounded = [tuple(round(c, ROUND_DP) for c in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted((rounded[j], rounded[(j + 1) % 3])))
            edge_count[edge] = edge_count.get(edge, 0) + 1
        cross = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        if _mag(cross) >= EPS and _mag(declared) >= EPS:
            similarity = _dot(_norm(declared), _norm(cross))
            if similarity < -0.99:
                issues.append(f'Face normal is inverted relative to winding order (similarity={similarity:.3f})')

    if degenerate:
        issues.append(f'Mesh has {degenerate} degenerate triangle(s)')

    open_edges = sum(1 for c in edge_count.values() if c == 1)
    non_manifold = sum(1 for c in edge_count.values() if c > 2)
    if open_edges:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')
    if non_manifold:
        issues.append(f'Mesh has {non_manifold} non-manifold edge(s)')

    surface_area_in2 = sum(_triangle_area(*verts) for _, verts in triangles)
    surface_area_mm2 = round(surface_area_in2 * IN2_TO_MM2, 6)

    return {
        'valid': len(issues) == 0,
        'surface_area_mm2': surface_area_mm2,
        'issues': issues,
    }


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'surface_area_mm2': 0.0, 'issues': ['usage: unit_aware_stl_validator_cli.py <file.stl>']}))
        sys.exit(1)
    print(json.dumps(validate(sys.argv[1])))
