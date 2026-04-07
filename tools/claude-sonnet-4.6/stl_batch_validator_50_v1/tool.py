"""Batch STL validator for up to 50 supplier STL files.
Supports both ASCII and binary STL. Checks:
- Manifold integrity (open edges, non-manifold edges)
- Degenerate triangles
- Duplicate faces
- Inverted normals (declared vs winding order, threshold -0.5)
- Binary file-size consistency
Writes validation_report.json.
"""
import json
import math
import os
import struct


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])

def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])

def _dot(a, b):
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]

def _magnitude(v):
    return math.sqrt(v[0]**2+v[1]**2+v[2]**2)

def _normalize(v):
    m = _magnitude(v)
    if m < 1e-10:
        return v
    return (v[0]/m, v[1]/m, v[2]/m)


def _is_binary_stl(path):
    with open(path, 'rb') as f:
        header = f.read(80)
        if len(header) < 80:
            return False
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            return False
        tri_count = struct.unpack('<I', count_bytes)[0]
        expected_size = 80 + 4 + tri_count * 50
        actual_size = os.path.getsize(path)
        if actual_size == expected_size:
            return True
        try:
            text_start = header.decode('ascii', errors='replace').strip()
            if text_start.startswith('solid'):
                return False
        except Exception:
            pass
        return True


def parse_binary_stl(path):
    issues = []
    triangles = []
    file_size = os.path.getsize(path)
    with open(path, 'rb') as f:
        f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            return {"valid": False, "issues": ["Truncated binary STL"]}
        tri_count = struct.unpack('<I', count_bytes)[0]
        expected_size = 80 + 4 + tri_count * 50
        if file_size != expected_size:
            issues.append(f"Binary file size mismatch: expected {expected_size}, got {file_size}")
        for i in range(tri_count):
            data = f.read(50)
            if len(data) < 50:
                issues.append(f"Truncated triangle data at triangle {i}")
                break
            vals = struct.unpack('<12fH', data)
            normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((normal, [v1, v2, v3]))
    return _validate_triangles(triangles, issues)


def parse_ascii_stl(path):
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
                issues.append(f"Malformed facet normal at line {i+1}")
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
                                issues.append(f"Malformed vertex at line {i+1}")
                        i += 1
            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f"Incomplete triangle near line {i+1}")
            continue
        i += 1
    return _validate_triangles(triangles, issues)


def _validate_triangles(triangles, issues):
    if not triangles:
        issues.append("no_valid_faces")
        return {"valid": False, "issues": issues}
    edge_count = {}
    seen_faces = set()
    for _, verts in triangles:
        rounded = tuple(tuple(round(x, 6) for x in v) for v in verts)
        face_key = tuple(sorted(rounded))
        if face_key in seen_faces:
            issues.append("duplicate_faces")
        seen_faces.add(face_key)
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j+1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1
    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    if open_edges > 0:
        issues.append(f"open_edges:{open_edges}")
    if non_manifold_edges > 0:
        issues.append(f"non_manifold_edges:{non_manifold_edges}")
    degenerate_count = 0
    inverted_count = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        mag = _magnitude(cross)
        if mag < 1e-10:
            degenerate_count += 1
            continue
        winding_normal = _normalize(cross)
        dn_mag = _magnitude(declared_normal)
        if dn_mag < 1e-10:
            continue
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < -0.5:
            inverted_count += 1
    if degenerate_count > 0:
        issues.append(f"degenerate_triangles:{degenerate_count}")
    if inverted_count > 0:
        issues.append(f"inverted_normals:{inverted_count}")
    return {"valid": len(issues) == 0, "triangle_count": len(triangles), "issues": issues}


def validate_stl(path):
    try:
        if _is_binary_stl(path):
            return parse_binary_stl(path)
        else:
            return parse_ascii_stl(path)
    except Exception as e:
        return {"valid": False, "issues": [f"exception:{str(e)}"]}


def classify_reason(issues):
    combined = ' '.join(issues)
    if 'inverted_normals' in combined: return 'inverted_normals'
    if 'open_edges' in combined: return 'open_edges'
    if 'non_manifold' in combined: return 'non_manifold_edges'
    if 'degenerate_triangles' in combined: return 'degenerate_triangles'
    if 'duplicate_faces' in combined: return 'duplicate_faces'
    if 'no_valid_faces' in combined: return 'no_valid_faces'
    if 'Binary file size' in combined: return 'binary_size_mismatch'
    if 'Truncated' in combined: return 'truncated_file'
    return issues[0] if issues else 'unknown'


def main(n_files=50, prefix='supplier', output='validation_report.json'):
    results = []
    for n in range(1, n_files+1):
        fname = f"{prefix}_{n:03d}.stl"
        result = validate_stl(fname)
        entry = {"file": fname, "valid": result["valid"]}
        if not result["valid"]:
            entry["reason"] = classify_reason(result.get("issues", []))
        results.append(entry)
        status = "OK" if result["valid"] else f"FAIL ({entry.get('reason', '?')})"
        print(f"{fname}: {status}")
    with open(output, 'w') as f:
        json.dump({"results": results}, f, indent=2)
    print(f"\nDone. {sum(1 for r in results if r['valid'])}/{n_files} files valid.")
    print(f"Report written to {output}")


if __name__ == '__main__':
    main()
