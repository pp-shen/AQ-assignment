"""stl_validator_binary.py - STL validator supporting both ASCII and binary formats.

Checks:
  - File format detection (binary vs ASCII)
  - Correct face count in binary files
  - Manifold integrity (every edge shared by exactly 2 triangles)
  - No degenerate triangles (zero area)
  - No duplicate faces
  - Declared face normals consistent with vertex winding order
  - Mesh produces a closed solid (no open edges)

Usage: python stl_validator_binary.py <file.stl>
Output: JSON object {"valid": true/false, "issues": [...]}
"""
import json
import math
import struct
import sys


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _magnitude(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _normalize(v):
    m = _magnitude(v)
    if m == 0:
        return (0.0, 0.0, 0.0)
    return (v[0] / m, v[1] / m, v[2] / m)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def _is_binary_stl(path: str) -> bool:
    """Detect if file is binary STL.
    Binary STL: 80-byte header + 4-byte uint32 triangle count + 50 bytes per triangle.
    If the file starts with 'solid' it might still be binary (bad practice), so
    we use size-based heuristic.
    """
    import os
    file_size = os.path.getsize(path)
    # Minimum binary STL size: 80 (header) + 4 (count) = 84 bytes for 0 triangles
    if file_size < 84:
        # Could be a very small ASCII file
        with open(path, 'r', errors='replace') as f:
            first = f.read(256).lstrip()
        return not first.lower().startswith('solid')
    
    with open(path, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
    
    if len(count_bytes) < 4:
        return False
    
    num_triangles = struct.unpack('<I', count_bytes)[0]
    expected_size = 84 + num_triangles * 50
    
    # If size matches binary expectation, it's binary
    if expected_size == file_size:
        return True
    
    # If header starts with 'solid', treat as ASCII
    try:
        header_text = header.decode('ascii', errors='strict').lstrip()
        if header_text.lower().startswith('solid'):
            return False
    except Exception:
        pass
    
    # Otherwise assume binary
    return True


def parse_binary_stl(path: str) -> tuple:
    """Parse binary STL. Returns (triangles, issues) where
    triangles = list of (declared_normal, [v1, v2, v3]).
    """
    import os
    issues = []
    triangles = []
    
    file_size = os.path.getsize(path)
    
    with open(path, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            issues.append("File too short to contain binary STL header")
            return triangles, issues
        
        num_triangles = struct.unpack('<I', count_bytes)[0]
        expected_size = 84 + num_triangles * 50
        
        if expected_size != file_size:
            issues.append(
                f"Binary STL size mismatch: header claims {num_triangles} triangles "
                f"(expected {expected_size} bytes) but file is {file_size} bytes"
            )
            # Use actual available data
            actual_triangles = (file_size - 84) // 50
            num_triangles = actual_triangles
        
        for idx in range(num_triangles):
            chunk = f.read(50)
            if len(chunk) < 50:
                issues.append(f"Unexpected end of file at triangle {idx}")
                break
            
            values = struct.unpack('<12fH', chunk)
            nx, ny, nz = values[0], values[1], values[2]
            v1 = (values[3], values[4], values[5])
            v2 = (values[6], values[7], values[8])
            v3 = (values[9], values[10], values[11])
            # attribute_byte_count = values[12]  # ignored
            
            declared_normal = (nx, ny, nz)
            triangles.append((declared_normal, [v1, v2, v3]))
    
    return triangles, issues


def parse_ascii_stl(path: str) -> tuple:
    """Parse ASCII STL. Returns (triangles, issues)."""
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
                issues.append(f"Malformed facet normal at line {i + 1}")
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


def validate_triangles(triangles: list, issues: list) -> dict:
    """Run all geometric checks on parsed triangles."""
    NORMAL_ANGLE_THRESHOLD = 0.01  # radians (~0.57 degrees)
    AREA_THRESHOLD = 1e-10
    
    if not triangles:
        issues.append("No valid faces found")
        surface_area = 0.0
    else:
        surface_area = sum(_triangle_area(*verts) for _, verts in triangles)
    
    degenerate_count = 0
    inverted_normal_count = 0
    
    face_set = set()
    duplicate_count = 0
    
    for tri_idx, (declared_normal, verts) in enumerate(triangles):
        v1, v2, v3 = verts
        
        # Degenerate triangle check
        area = _triangle_area(v1, v2, v3)
        if area < AREA_THRESHOLD:
            degenerate_count += 1
            continue
        
        # Duplicate face check
        rounded = tuple(sorted([tuple(round(x, 6) for x in v) for v in verts]))
        if rounded in face_set:
            duplicate_count += 1
        else:
            face_set.add(rounded)
        
        # Normal consistency check
        computed_normal = _cross(_sub(v2, v1), _sub(v3, v1))
        computed_unit = _normalize(computed_normal)
        
        declared_mag = _magnitude(declared_normal)
        if declared_mag < 1e-10:
            # Zero declared normal - common in some binary files, skip check
            pass
        else:
            declared_unit = _normalize(declared_normal)
            cos_angle = max(-1.0, min(1.0, _dot(declared_unit, computed_unit)))
            angle = math.acos(cos_angle)
            if angle > NORMAL_ANGLE_THRESHOLD:
                inverted_normal_count += 1
    
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate triangle(s) with zero or near-zero area")
    
    if duplicate_count > 0:
        issues.append(f"{duplicate_count} duplicate face(s) found")
    
    if inverted_normal_count > 0:
        issues.append(
            f"{inverted_normal_count} face(s) have declared normals inconsistent with "
            f"vertex winding order (inverted normals)"
        )
    
    # Manifold check: every edge must be shared by exactly 2 triangles
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1
    
    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    non_manifold_edges = sum(1 for cnt in edge_count.values() if cnt > 2)
    
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) — not a closed solid")
    
    if non_manifold_edges > 0:
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s) (shared by >2 faces)")
    
    return {
        "valid": len(issues) == 0,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }


def validate_stl(path: str) -> dict:
    """Main entry point: detect format, parse, validate."""
    issues = []
    
    try:
        is_binary = _is_binary_stl(path)
    except FileNotFoundError:
        return {"valid": False, "issues": [f"File not found: {path}"]}
    except Exception as e:
        return {"valid": False, "issues": [f"Error reading file: {e}"]}
    
    if is_binary:
        triangles, parse_issues = parse_binary_stl(path)
        format_name = "binary"
    else:
        triangles, parse_issues = parse_ascii_stl(path)
        format_name = "ASCII"
    
    issues.extend(parse_issues)
    result = validate_triangles(triangles, issues)
    result["format"] = format_name
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_binary.py <file.stl>"}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result))
