import json
import math
import os
from collections import defaultdict

EPS = 1e-6


def sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])


def cross(a, b):
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )


def dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]


def mag(v):
    return math.sqrt(dot(v, v))


def norm(v):
    m = mag(v)
    if m <= EPS:
        return (0.0, 0.0, 0.0)
    return (v[0]/m, v[1]/m, v[2]/m)


def parse_ascii_stl(path):
    triangles = []
    issues = []
    with open(path, 'r', errors='replace') as f:
        lines = f.readlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith('facet normal'):
            parts = line.split()
            try:
                declared = (float(parts[2]), float(parts[3]), float(parts[4]))
            except Exception:
                issues.append('malformed')
                i += 1
                continue
            vertices = []
            i += 1
            if i < len(lines) and lines[i].strip() == 'outer loop':
                i += 1
                for _ in range(3):
                    if i < len(lines) and lines[i].strip().startswith('vertex'):
                        vp = lines[i].strip().split()
                        try:
                            vertices.append((float(vp[1]), float(vp[2]), float(vp[3])))
                        except Exception:
                            issues.append('malformed')
                        i += 1
            if len(vertices) == 3:
                triangles.append((declared, vertices))
            else:
                issues.append('incomplete_triangle')
            continue
        i += 1
    return triangles, issues


def validate(path):
    tris, issues = parse_ascii_stl(path)
    if not tris:
        return False, 'no_faces'

    edge_count = defaultdict(int)
    has_degenerate = False
    inverted = False

    for declared, verts in tris:
        v1, v2, v3 = verts
        cp = cross(sub(v2, v1), sub(v3, v1))
        if mag(cp) <= EPS:
            has_degenerate = True
        else:
            nd = norm(declared)
            nc = norm(cp)
            if mag(nd) > EPS and dot(nd, nc) < 1 - 1e-4:
                inverted = True
        rounded = [tuple(round(c, 6) for c in v) for v in verts]
        for j in range(3):
            e = tuple(sorted((rounded[j], rounded[(j+1)%3])))
            edge_count[e] += 1

    if has_degenerate:
        return False, 'degenerate_triangle'
    if any(c == 1 for c in edge_count.values()):
        return False, 'open_edges'
    if any(c > 2 for c in edge_count.values()):
        return False, 'non_manifold'
    if inverted:
        return False, 'inverted_normals'
    return True, None


def main():
    results = []
    for i in range(1, 21):
        fname = f'supplier_{i:03d}.stl'
        valid, reason = validate(fname)
        entry = {'file': fname, 'valid': valid}
        if not valid:
            entry['reason'] = reason
        results.append(entry)
    with open('validation_report.json', 'w') as f:
        json.dump({'results': results}, f)
    print(json.dumps({'results': results}, indent=2))


if __name__ == '__main__':
    main()
