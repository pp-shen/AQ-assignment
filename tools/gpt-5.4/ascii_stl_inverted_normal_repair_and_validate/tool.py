import json
import math
import sys


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
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _normalize(v):
    m = _mag(v)
    if m == 0:
        return None
    return (v[0] / m, v[1] / m, v[2] / m)


def _fmt(x):
    return format(x, '.15g')


def _parse_vertex(line):
    parts = line.strip().split()
    if len(parts) != 4 or parts[0] != 'vertex':
        raise ValueError('Malformed vertex line')
    return (float(parts[1]), float(parts[2]), float(parts[3]))


def _parse_normal(line):
    parts = line.strip().split()
    if len(parts) != 5 or parts[0] != 'facet' or parts[1] != 'normal':
        raise ValueError('Malformed facet normal line')
    return (float(parts[2]), float(parts[3]), float(parts[4]))


def repair_ascii_stl_in_place(path):
    issues = []
    repaired = 0

    try:
        with open(path, 'r', errors='replace') as fh:
            lines = fh.readlines()
    except Exception as e:
        return {'valid': False, 'repaired': 0, 'issues': [f'Failed to read file: {e}']}

    new_lines = list(lines)
    i = 0
    facet_count = 0

    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped.startswith('facet normal'):
            i += 1
            continue

        facet_line_index = i
        facet_count += 1
        try:
            declared = _parse_normal(lines[i])
        except Exception:
            issues.append(f'Malformed facet normal at line {i + 1}')
            i += 1
            continue

        if i + 6 >= len(lines):
            issues.append(f'Incomplete triangle near line {i + 1}')
            break

        try:
            if lines[i + 1].strip() != 'outer loop':
                raise ValueError('Missing outer loop')
            v1 = _parse_vertex(lines[i + 2])
            v2 = _parse_vertex(lines[i + 3])
            v3 = _parse_vertex(lines[i + 4])
            if lines[i + 5].strip() != 'endloop':
                raise ValueError('Missing endloop')
            if lines[i + 6].strip() != 'endfacet':
                raise ValueError('Missing endfacet')
        except Exception as e:
            issues.append(f'Malformed triangle near line {i + 1}: {e}')
            i += 1
            continue

        computed = _cross(_sub(v2, v1), _sub(v3, v1))
        comp_n = _normalize(computed)
        dec_n = _normalize(declared)

        if comp_n is None:
            issues.append(f'Degenerate triangle at facet starting line {i + 1}')
        else:
            if dec_n is None:
                issues.append(f'Zero declared normal at facet starting line {i + 1}; replaced')
                repaired += 1
                indent = lines[facet_line_index][: len(lines[facet_line_index]) - len(lines[facet_line_index].lstrip())]
                new_lines[facet_line_index] = f"{indent}facet normal {_fmt(comp_n[0])} {_fmt(comp_n[1])} {_fmt(comp_n[2])}\n"
            else:
                if _dot(dec_n, comp_n) < 0:
                    repaired += 1
                    indent = lines[facet_line_index][: len(lines[facet_line_index]) - len(lines[facet_line_index].lstrip())]
                    new_lines[facet_line_index] = f"{indent}facet normal {_fmt(comp_n[0])} {_fmt(comp_n[1])} {_fmt(comp_n[2])}\n"

        i += 7

    if facet_count == 0:
        issues.append('No valid faces found')

    try:
        with open(path, 'w', newline='') as fh:
            fh.writelines(new_lines)
    except Exception as e:
        return {'valid': False, 'repaired': repaired, 'issues': issues + [f'Failed to write file: {e}']}

    validation = validate_ascii_stl(path)
    return {
        'valid': validation['valid'],
        'repaired': repaired,
        'issues': validation['issues'],
    }


def validate_ascii_stl(path):
    issues = []
    triangles = []

    try:
        with open(path, 'r', errors='replace') as fh:
            lines = fh.readlines()
    except Exception as e:
        return {'valid': False, 'issues': [f'Failed to read file for validation: {e}']}

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('facet normal'):
            try:
                declared = _parse_normal(lines[i])
            except Exception:
                issues.append(f'Malformed facet normal at line {i + 1}')
                i += 1
                continue

            try:
                if i + 6 >= len(lines):
                    raise ValueError('Incomplete triangle')
                if lines[i + 1].strip() != 'outer loop':
                    raise ValueError('Missing outer loop')
                v1 = _parse_vertex(lines[i + 2])
                v2 = _parse_vertex(lines[i + 3])
                v3 = _parse_vertex(lines[i + 4])
                if lines[i + 5].strip() != 'endloop':
                    raise ValueError('Missing endloop')
                if lines[i + 6].strip() != 'endfacet':
                    raise ValueError('Missing endfacet')
                triangles.append((declared, [v1, v2, v3]))
                i += 7
                continue
            except Exception as e:
                issues.append(f'Malformed triangle near line {i + 1}: {e}')
        i += 1

    if not triangles:
        issues.append('No valid faces found')

    edge_count = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted((rounded[j], rounded[(j + 1) % 3])))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    if open_edges > 0:
        issues.append(f'Mesh has {open_edges} open edge(s) — not a closed solid')

    for idx, (declared, verts) in enumerate(triangles, start=1):
        computed = _cross(_sub(verts[1], verts[0]), _sub(verts[2], verts[0]))
        comp_n = _normalize(computed)
        dec_n = _normalize(declared)
        if comp_n is None:
            issues.append(f'Degenerate triangle at facet {idx}')
            continue
        if dec_n is None:
            issues.append(f'Zero declared normal at facet {idx}')
            continue
        if _dot(dec_n, comp_n) < 1 - 1e-6:
            issues.append(f'Inconsistent declared normal at facet {idx}')

    return {'valid': len(issues) == 0, 'issues': issues}


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(json.dumps({'valid': False, 'repaired': 0, 'issues': ['usage: repair_inverted_normals.py <file.stl>']}))
        sys.exit(1)
    result = repair_ascii_stl_in_place(sys.argv[1])
    print(json.dumps(result))
