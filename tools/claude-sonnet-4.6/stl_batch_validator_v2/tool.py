"""Batch STL validator using the best available checks from the tool library.
Uses stl_validator_improved_v3 logic: ASCII + binary support, manifold,
non-manifold edges, degenerate triangles, duplicate faces, inverted normals,
binary file-size consistency.
Writes validation_report.json.
"""
import json
import math
import os
import struct


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


def _is_binary_stl(path):
    with open(path, 'rb') as f:
        header = f.read(80)
    try:
        if header.lstrip()[:5].decode('ascii', errors='replace').lower().startswith('solid'):
            with open(path, 'rb') as f:
                f.read(80)
                data = f.read(4)
            if len(data) < 4:
                return False
            num_tris = struct.unpack('<I', data)[0]
            expected_size = 80 + 4 + num_tris * 50
            actual_size = os.path.getsize(path)
            if actual_size == expected_size:
                return True
            return False
        else:
            return True
    except Exception:
        return True


def parse_binary_stl(path):
    issues = []
    triangles = []
    file_size = os.path.getsize(path)
    with open(path, 'rb') as f:
        f.read(80)
        count_data = f.read(4)
        if len(count_data) < 4:
            issues.append("Binary STL: truncated triangle count")
            return triangles, issues
        num_tris = struct.unpack('<I', count_data)[0]
        expected_size = 80 + 4 + num_tris * 50
        if file_size != expected_size:
            issues.append(
                f"Binary STL file size mismatch: expected {expected_size} bytes "
                f"for {num_tris} triangles, got {file_size} bytes"
            )
        for idx in range(num_tris):
            chunk = f.read(50)
            if len(chunk) < 50:
                issues.append(f"Binary STL: truncated data at triangle {idx}")
                break
            vals = struct.unpack('<12fH', chunk)
            normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((normal, [v1, v2, v3]))
    return triangles, issues


def parse_ascii_stl(path):
    issues = []
    triangles = []
    with open(path, 'r', errors='replace') as fh:
        lines = fh.readlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("facet normal"):
            parts = line.split()
            try:
                declared_normal = (float(parts[2]), float(parts[3]), float(parts[4]))
            except (IndexError, ValueError):
                issues.append(f"Malformed facet normal at line {i + 1}")
                i += 1
                continue
            vertices = []
            i += 1
            if i < len(lines) and lines[i].strip() == "outer loop":
                i += 1
                for _ in range(3):
                    if i < len(lines):
                        vline = lines[i].strip()
                        if vline.startswith("vertex"):
                            vparts = vline.split()
                            try:
                                vertices.append(
                                    (float(vparts[1]), float(vparts[2]), float(vparts[3]))
                                )
                            except (IndexError, ValueError):
                                issues.append(f"Malformed vertex at line {i + 1}")
                        i += 1
            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f"Incomplete triangle near line {i + 1}")
            continue
        i += 1
    return triangles, issues


def validate_stl(path):
    issues = []
    try:
        if _is_binary_stl(path):
            triangles, parse_issues = parse_binary_stl(path)
        else:
            triangles, parse_issues = parse_ascii_stl(path)
    except Exception as e:
        return {"valid": False, "issues": [f"Parse error: {e}"], "reason": "parse_error"}
    issues.extend(parse_issues)
    if not triangles:
        issues.append("No valid faces found")
        return {"valid": False, "issues": issues, "reason": "no_faces"}
    degenerate_count = 0
    for _, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate triangle(s) (zero area)")
    face_set = set()
    duplicate_count = 0
    for _, verts in triangles:
        key = frozenset(tuple(round(x, 6) for x in v) for v in verts)
        if key in face_set:
            duplicate_count += 1
        else:
            face_set.add(key)
    if duplicate_count > 0:
        issues.append(f"{duplicate_count} duplicate face(s)")
    edge_count = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1
    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges > 0:
        issues.append(f"{open_edges} open edge(s) — mesh is not a closed solid")
    if non_manifold_edges > 0:
        issues.append(f"{non_manifold_edges} non-manifold edge(s) (shared by >2 faces)")
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
        if similarity < -0.5:
            inverted_count += 1
    if inverted_count > 0:
        issues.append(f"{inverted_count} face(s) with inverted normals (declared vs winding order)")
    reason = None
    if issues:
        combined = " ".join(issues).lower()
        if "inverted" in combined:
            reason = "inverted_normals"
        elif "open edge" in combined:
            reason = "open_edges"
        elif "non-manifold" in combined:
            reason = "non_manifold_edges"
        elif "degenerate" in combined:
            reason = "degenerate_triangles"
        elif "duplicate" in combined:
            reason = "duplicate_faces"
        elif "file size" in combined or "truncated" in combined:
            reason = "binary_file_corrupt"
        elif "no valid faces" in combined:
            reason = "no_faces"
        else:
            reason = "malformed_geometry"
    return {"valid": len(issues) == 0, "triangle_count": len(triangles), "issues": issues, "reason": reason}


def main():
    results = []
    for i in range(1, 21):
        fname = f"supplier_{i:03d}.stl"
        result = validate_stl(fname)
        entry = {"file": fname, "valid": result["valid"]}
        if not result["valid"]:
            entry["reason"] = result["reason"]
        results.append(entry)
    report = {"results": results}
    with open("validation_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("Report written to validation_report.json")


if __name__ == "__main__":
    main()
