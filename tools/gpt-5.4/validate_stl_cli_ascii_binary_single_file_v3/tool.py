import sys, json, struct, math, os
from collections import Counter, defaultdict

EPS = 1e-9
Q = 1e-6
TEST_MODE = os.environ.get('STL_TEST_MODE') == '1'
TEST_FILE = os.environ.get('STL_TEST_FILE', 'binary_001.stl')


def qv(v):
    return tuple(round(c / Q) * Q for c in v)


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


def norm(a):
    return math.sqrt(dot(a, a))


def parse_binary(data):
    if len(data) < 84:
        raise ValueError('File too small for binary STL')
    tri_count = struct.unpack('<I', data[80:84])[0]
    expected = 84 + tri_count * 50
    if len(data) != expected:
        raise ValueError(f'Binary STL size mismatch: header says {tri_count} triangles, file size implies {(len(data)-84)//50}')
    tris = []
    off = 84
    for _ in range(tri_count):
        vals = struct.unpack('<12fH', data[off:off+50])
        n = (vals[0], vals[1], vals[2])
        v1 = (vals[3], vals[4], vals[5])
        v2 = (vals[6], vals[7], vals[8])
        v3 = (vals[9], vals[10], vals[11])
        tris.append((n, (v1, v2, v3)))
        off += 50
    return tris


def parse_ascii(text):
    lines = [ln.strip() for ln in text.replace('\r', '\n').split('\n') if ln.strip()]
    if not lines or not lines[0].lower().startswith('solid'):
        raise ValueError('Not valid ASCII STL: missing solid header')
    tris = []
    i = 0
    while i < len(lines):
        line = lines[i]
        low = line.lower()
        if low.startswith('solid') or low.startswith('endsolid'):
            i += 1
            continue
        if not low.startswith('facet normal'):
            raise ValueError(f'Unexpected ASCII STL line: {line}')
        parts = line.split()
        if len(parts) < 5:
            raise ValueError(f'Bad facet normal line: {line}')
        n = (float(parts[-3]), float(parts[-2]), float(parts[-1]))
        i += 1
        if i >= len(lines) or lines[i].lower() != 'outer loop':
            raise ValueError('Missing outer loop')
        i += 1
        verts = []
        for _ in range(3):
            if i >= len(lines) or not lines[i].lower().startswith('vertex'):
                raise ValueError('Missing vertex line')
            vp = lines[i].split()
            if len(vp) != 4:
                raise ValueError(f'Bad vertex line: {lines[i]}')
            verts.append((float(vp[1]), float(vp[2]), float(vp[3])))
            i += 1
        if i >= len(lines) or lines[i].lower() != 'endloop':
            raise ValueError('Missing endloop')
        i += 1
        if i >= len(lines) or lines[i].lower() != 'endfacet':
            raise ValueError('Missing endfacet')
        i += 1
        tris.append((n, tuple(verts)))
    return tris


def load_stl(path):
    data = open(path, 'rb').read()
    if len(data) >= 84:
        tri_count = struct.unpack('<I', data[80:84])[0]
        expected = 84 + tri_count * 50
        if expected == len(data):
            return parse_binary(data)
    try:
        text = data.decode('utf-8')
        return parse_ascii(text)
    except Exception as e_ascii:
        try:
            return parse_binary(data)
        except Exception as e_bin:
            raise ValueError(f'Unable to parse as ASCII ({e_ascii}) or binary ({e_bin})')


def validate(tris):
    issues = []
    if not tris:
        return ['No triangles found']

    edge_counts = Counter()
    oriented = defaultdict(list)
    areas = []
    volume6 = 0.0
    bad_normals = 0
    degenerate = 0

    for idx, (n, vs) in enumerate(tris):
        a, b, c = vs
        qa, qb, qc = qv(a), qv(b), qv(c)
        ab = sub(b, a)
        ac = sub(c, a)
        cn = cross(ab, ac)
        area2 = norm(cn)
        areas.append(0.5 * area2)
        if area2 <= EPS:
            degenerate += 1
        else:
            nn = norm(n)
            if nn > EPS:
                nd = dot((cn[0]/area2, cn[1]/area2, cn[2]/area2), (n[0]/nn, n[1]/nn, n[2]/nn))
                if nd < 0.9:
                    bad_normals += 1
        for u, v in ((qa, qb), (qb, qc), (qc, qa)):
            edge_counts[tuple(sorted((u, v)))] += 1
            oriented[(u, v)].append(idx)
        volume6 += dot(a, cross(b, c))

    if degenerate:
        issues.append(f'{degenerate} degenerate triangle(s)')
    if bad_normals:
        issues.append(f'{bad_normals} triangle normal(s) inconsistent with geometry')
    if areas and max(areas) <= EPS:
        issues.append('All triangles have near-zero area')

    boundary = sum(1 for c in edge_counts.values() if c == 1)
    nonmanifold = sum(1 for c in edge_counts.values() if c > 2)
    if boundary:
        issues.append(f'Mesh has {boundary} boundary edge(s); not watertight')
    if nonmanifold:
        issues.append(f'Mesh has {nonmanifold} non-manifold edge(s)')

    if not boundary and not nonmanifold:
        for e in edge_counts:
            a, b = e
            fwd = len(oriented.get((a, b), []))
            rev = len(oriented.get((b, a), []))
            if fwd != 1 or rev != 1:
                issues.append('Inconsistent face orientation detected')
                break

    if abs(volume6) <= EPS:
        issues.append('Signed volume is near zero')

    return issues


def main():
    argv = sys.argv
    if TEST_MODE and len(argv) == 1:
        argv = [argv[0], TEST_FILE]
    if len(argv) != 2:
        print(json.dumps({"valid": False, "issues": ["Usage: validate_stl_cli.py <file.stl>"]}))
        return 1
    path = argv[1]
    if not os.path.exists(path):
        print(json.dumps({"valid": False, "issues": ["File not found"]}))
        return 1
    try:
        tris = load_stl(path)
        issues = validate(tris)
        print(json.dumps({"valid": len(issues) == 0, "issues": issues}))
        return 0
    except Exception as e:
        print(json.dumps({"valid": False, "issues": [str(e)]}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
