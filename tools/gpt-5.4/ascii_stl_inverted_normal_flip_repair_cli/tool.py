import json
import math
import os
import re
import sys

FLOAT_FMT = ".6g"
FACET_RE = re.compile(r'^(\s*)facet\s+normal\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)(\s*)$', re.IGNORECASE)
VERTEX_RE = re.compile(r'^\s*vertex\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s*$', re.IGNORECASE)
OUTER_LOOP_RE = re.compile(r'^\s*outer\s+loop\s*$', re.IGNORECASE)
ENDLOOP_RE = re.compile(r'^\s*endloop\s*$', re.IGNORECASE)
ENDFACET_RE = re.compile(r'^\s*endfacet\s*$', re.IGNORECASE)


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


def _normalize(v):
    m = _mag(v)
    if m < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _format_float(x):
    if abs(x) < 5e-15:
        x = 0.0
    return format(x, FLOAT_FMT)


def _format_normal_line(indent, normal, trailing):
    return f"{indent}facet normal {_format_float(normal[0])} {_format_float(normal[1])} {_format_float(normal[2])}{trailing}\n"


def parse_ascii_stl(lines):
    issues = []
    facets = []
    i = 0
    while i < len(lines):
        m = FACET_RE.match(lines[i].rstrip('\n'))
        if not m:
            i += 1
            continue
        indent, nx, ny, nz, trailing = m.groups()
        try:
            declared = (float(nx), float(ny), float(nz))
        except ValueError:
            issues.append(f"Malformed facet normal at line {i+1}")
            i += 1
            continue
        facet_idx = i
        i += 1
        if i >= len(lines) or not OUTER_LOOP_RE.match(lines[i].rstrip('\n')):
            issues.append(f"Missing outer loop after facet at line {facet_idx+1}")
            continue
        i += 1
        vertices = []
        for _ in range(3):
            if i >= len(lines):
                break
            vm = VERTEX_RE.match(lines[i].rstrip('\n'))
            if not vm:
                break
            try:
                vertex = (float(vm.group(1)), float(vm.group(2)), float(vm.group(3)))
            except ValueError:
                issues.append(f"Malformed vertex at line {i+1}")
                break
            vertices.append(vertex)
            i += 1
        if len(vertices) != 3:
            issues.append(f"Incomplete triangle near line {facet_idx+1}")
            continue
        if i >= len(lines) or not ENDLOOP_RE.match(lines[i].rstrip('\n')):
            issues.append(f"Missing endloop for facet at line {facet_idx+1}")
            continue
        i += 1
        if i >= len(lines) or not ENDFACET_RE.match(lines[i].rstrip('\n')):
            issues.append(f"Missing endfacet for facet at line {facet_idx+1}")
            continue
        i += 1
        facets.append({
            "facet_line": facet_idx,
            "indent": indent,
            "trailing": trailing,
            "declared": declared,
            "vertices": vertices,
        })
    if not facets:
        issues.append("No valid faces found")
    return facets, issues


def validate_facets(facets, parse_issues):
    issues = list(parse_issues)
    edge_count = {}
    for facet in facets:
        verts = facet["vertices"]
        rounded = [tuple(round(c, 6) for c in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted((rounded[j], rounded[(j + 1) % 3])))
            edge_count[edge] = edge_count.get(edge, 0) + 1
    open_edges = sum(1 for c in edge_count.values() if c == 1)
    non_manifold = sum(1 for c in edge_count.values() if c > 2)
    if open_edges:
        issues.append(f"Mesh has {open_edges} open edge(s) — not a closed solid")
    if non_manifold:
        issues.append(f"Mesh has {non_manifold} non-manifold edge(s)")
    for idx, facet in enumerate(facets, start=1):
        v1, v2, v3 = facet["vertices"]
        cross = _cross(_sub(v2, v1), _sub(v3, v1))
        if _mag(cross) < 1e-10:
            issues.append(f"Degenerate triangle at facet {idx}")
            continue
        winding = _normalize(cross)
        declared_raw = facet["declared"]
        if _mag(declared_raw) < 1e-10:
            issues.append(f"Zero-length declared normal at facet {idx}")
            continue
        declared = _normalize(declared_raw)
        similarity = _dot(declared, winding)
        if similarity < 0.99:
            issues.append(f"Declared normal inconsistent with winding at facet {idx} (similarity={similarity:.3f})")
    return {"valid": len(issues) == 0, "issues": issues}


def repair_file(path):
    if not os.path.exists(path):
        return {"valid": False, "repaired": 0, "issues": ["File not found"]}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    facets, parse_issues = parse_ascii_stl(lines)
    repaired = 0
    for facet in facets:
        v1, v2, v3 = facet["vertices"]
        cross = _cross(_sub(v2, v1), _sub(v3, v1))
        if _mag(cross) < 1e-10:
            continue
        winding = _normalize(cross)
        declared = facet["declared"]
        if _mag(declared) < 1e-10:
            lines[facet["facet_line"]] = _format_normal_line(facet["indent"], winding, facet["trailing"])
            facet["declared"] = winding
            repaired += 1
            continue
        similarity = _dot(_normalize(declared), winding)
        if similarity < -0.99:
            flipped = tuple(-c for c in declared)
            lines[facet["facet_line"]] = _format_normal_line(facet["indent"], flipped, facet["trailing"])
            facet["declared"] = flipped
            repaired += 1
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)
    validation = validate_facets(facets, parse_issues)
    return {"valid": validation["valid"], "repaired": repaired, "issues": validation["issues"]}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(json.dumps({"valid": False, "repaired": 0, "issues": ["usage: repair_inverted_normals.py <file.stl>"]}))
        sys.exit(1)
    print(json.dumps(repair_file(sys.argv[1])))
