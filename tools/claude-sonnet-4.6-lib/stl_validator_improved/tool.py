"""stl_validator_improved.py — Improved STL validation tool.

Fixes bugs found in the provided validator:
1. Normal consistency threshold bug: provided tool uses < -0.99 (only catches
   nearly perfectly inverted normals). Fixed to < 0 to catch any inverted normal.
2. Adds non-manifold edge detection (edges shared by more than 2 triangles).
3. Adds degenerate triangle detection.
4. Supports both ASCII and binary STL files.
5. Computes volume using divergence theorem (for manufacturing feasibility).
6. Reports bounding box dimensions.
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


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _magnitude(v):
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _normalize(v):
    m = _magnitude(v)
    if m < 1e-10:
        return v
    return (v[0] / m, v[1] / m, v[2] / m)


def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def _signed_volume_contribution(v1, v2, v3):
    """Signed volume contribution of a triangle to the total volume."""
    return _dot(v1, _cross(v2, v3)) / 6.0


def _is_binary_stl(path: str) -> bool:
    """Heuristic: binary STL files start with an 80-byte header, then a 4-byte triangle count."""
    try:
        with open(path, 'rb') as fh:
            header = fh.read(80)
            if len(header) < 80:
                return False
            # If header contains 'solid' at start, could be ASCII
            if header.lstrip().startswith(b'solid'):
                # Check if rest of file has vertex/facet keywords (ASCII)
                fh.seek(0)
                sample = fh.read(256).decode('utf-8', errors='replace')
                if 'facet' in sample or 'vertex' in sample:
                    return False
            count_bytes = fh.read(4)
            if len(count_bytes) < 4:
                return False
            count = struct.unpack('<I', count_bytes)[0]
            # Binary STL: 80 + 4 + count * 50 bytes
            fh.seek(0, 2)
            file_size = fh.tell()
            expected_size = 80 + 4 + count * 50
            return file_size == expected_size
    except Exception:
        return False


def parse_binary_stl(path: str):
    """Parse a binary STL file. Returns list of (normal, [v1, v2, v3])."""
    triangles = []
    issues = []
    with open(path, 'rb') as fh:
        fh.read(80)  # skip header
        count_bytes = fh.read(4)
        count = struct.unpack('<I', count_bytes)[0]
        for i in range(count):
            data = fh.read(50)
            if len(data) < 50:
                issues.append(f"Truncated binary data at triangle {i}")
                break
            vals = struct.unpack('<12fH', data)
            normal = (vals[0], vals[1], vals[2])
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append((normal, [v1, v2, v3]))
    return triangles, issues


def parse_ascii_stl(path: str):
    """Parse an ASCII STL file. Returns list of (normal, [v1, v2, v3])."""
    issues = []
    triangles = []

    with open(path, "r", errors="replace") as fh:
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


def validate_stl(path: str) -> dict:
    issues = []

    # Parse the STL file
    if _is_binary_stl(path):
        file_format = 'binary'
        triangles, parse_issues = parse_binary_stl(path)
    else:
        file_format = 'ascii'
        triangles, parse_issues = parse_ascii_stl(path)

    issues.extend(parse_issues)

    if not triangles:
        issues.append("No valid faces found")
        return {
            "valid": False,
            "file_format": file_format,
            "triangle_count": 0,
            "surface_area": 0.0,
            "volume": 0.0,
            "bounding_box": None,
            "issues": issues,
        }

    # Degenerate triangle check
    degenerate_count = 0
    for _, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"Mesh has {degenerate_count} degenerate (zero-area) triangle(s)")

    # Manifold check: every edge must be shared by exactly 2 triangles.
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
        issues.append(f"Mesh has {non_manifold_edges} non-manifold edge(s) (shared by >2 triangles)")

    # Normal consistency check — FIXED: threshold should be < 0 (any inverted normal)
    # The provided tool used < -0.99 which only catches nearly perfectly inverted normals.
    inverted_normals = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # degenerate triangle, skip
        winding_normal = _normalize(cross)
        dn_mag = _magnitude(declared_normal)
        if dn_mag < 1e-10:
            continue  # zero declared normal, skip
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < 0:  # FIXED: was < -0.99 in provided tool
            inverted_normals += 1

    if inverted_normals > 0:
        issues.append(
            f"{inverted_normals} face(s) have normals inverted relative to winding order"
        )

    # Surface area
    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    # Volume (signed, using divergence theorem)
    volume = sum(_signed_volume_contribution(*verts) for _, verts in triangles)
    volume = abs(volume)

    # Bounding box
    all_verts = [v for _, verts in triangles for v in verts]
    xs = [v[0] for v in all_verts]
    ys = [v[1] for v in all_verts]
    zs = [v[2] for v in all_verts]
    bounding_box = {
        "x": {"min": min(xs), "max": max(xs)},
        "y": {"min": min(ys), "max": max(ys)},
        "z": {"min": min(zs), "max": max(zs)},
    }

    return {
        "valid": len(issues) == 0,
        "file_format": file_format,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "volume": round(volume, 6),
        "bounding_box": bounding_box,
        "issues": issues,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: stl_validator_improved.py <file.stl>"}))
        sys.exit(1)
    result = validate_stl(sys.argv[1])
    print(json.dumps(result, indent=2))
