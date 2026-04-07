"""stl_validator_improved.py - Validates both ASCII and binary STL files.

Checks:
  - Binary vs ASCII detection
  - Triangle count
  - Manifold integrity (every edge shared by exactly 2 triangles)
  - Non-manifold edges (edge shared by >2 triangles)
  - Degenerate triangles (zero area)
  - Normal consistency (fixed threshold bug: uses < 0 not < -0.99)
  - Surface area, volume, bounding box

Usage: python stl_validator_improved.py <file.stl>
Output: JSON {"valid": true/false, "issues": [...]}
"""
import json
import math
import struct
import sys


# ── Vector math helpers ──────────────────────────────────────────────────────

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


def _triangle_signed_volume(v1, v2, v3):
    """Signed volume contribution for divergence theorem (used for total volume)."""
    return _dot(v1, _cross(v2, v3)) / 6.0


# ── Format detection ─────────────────────────────────────────────────────────

def _is_binary_stl(path: str) -> bool:
    """Return True if the file looks like a binary STL.

    Binary STL: 80-byte header + 4-byte uint32 triangle count + 50 bytes/triangle.
    ASCII STL starts with 'solid' (possibly with whitespace).
    """
    with open(path, "rb") as fh:
        header = fh.read(80)
        if len(header) < 80:
            return False  # too small to be valid binary
        count_bytes = fh.read(4)
        if len(count_bytes) < 4:
            return False
        count = struct.unpack("<I", count_bytes)[0]
        # Check that file size matches expected binary size
        import os
        expected_size = 80 + 4 + count * 50
        actual_size = os.path.getsize(path)
        if actual_size == expected_size and count > 0:
            return True
        # Also check: if header starts with 'solid', it might be ASCII
        # but some binary files also start with 'solid', so size check is primary
        try:
            header_text = header.decode("ascii", errors="strict")
            if header_text.strip().startswith("solid"):
                # Could be ASCII — verify by reading more
                pass
        except UnicodeDecodeError:
            return True  # non-ASCII bytes → must be binary
        # Fall back: if file content after header has non-printable bytes, binary
        with open(path, "rb") as fh2:
            raw = fh2.read()
        # Check for null bytes which are common in binary STL floats
        if b"\x00" in raw[80:]:
            return True
        return False


# ── Parsers ──────────────────────────────────────────────────────────────────

def _parse_binary(path: str):
    """Parse binary STL. Returns (triangles, issues) where
    triangles = list of (declared_normal, [v1, v2, v3])."""
    issues = []
    triangles = []

    with open(path, "rb") as fh:
        header = fh.read(80)  # skip header
        count_bytes = fh.read(4)
        if len(count_bytes) < 4:
            issues.append("Binary STL: file too short to read triangle count")
            return triangles, issues
        count = struct.unpack("<I", count_bytes)[0]

        for idx in range(count):
            chunk = fh.read(50)
            if len(chunk) < 50:
                issues.append(f"Binary STL: truncated data at triangle {idx + 1}")
                break
            vals = struct.unpack("<12fH", chunk)  # 12 floats + 1 uint16 attr
            nx, ny, nz = vals[0], vals[1], vals[2]
            v1 = (vals[3], vals[4], vals[5])
            v2 = (vals[6], vals[7], vals[8])
            v3 = (vals[9], vals[10], vals[11])
            triangles.append(((nx, ny, nz), [v1, v2, v3]))

    return triangles, issues


def _parse_ascii(path: str):
    """Parse ASCII STL. Returns (triangles, issues)."""
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
                                    (
                                        float(vparts[1]),
                                        float(vparts[2]),
                                        float(vparts[3]),
                                    )
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


# ── Main validation ──────────────────────────────────────────────────────────

def validate_stl(path: str) -> dict:
    issues = []

    # Detect format
    try:
        binary = _is_binary_stl(path)
    except OSError as e:
        return {"valid": False, "issues": [f"Cannot open file: {e}"]}

    # Parse
    if binary:
        triangles, parse_issues = _parse_binary(path)
    else:
        triangles, parse_issues = _parse_ascii(path)

    issues.extend(parse_issues)

    if not triangles:
        issues.append("No valid faces found")
        return {
            "valid": False,
            "triangle_count": 0,
            "surface_area": 0.0,
            "issues": issues,
        }

    # ── Degenerate triangle check ────────────────────────────────────────────
    degenerate_count = 0
    for idx, (_, verts) in enumerate(triangles):
        area = _triangle_area(*verts)
        if area < 1e-10:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")

    # ── Manifold / non-manifold edge check ───────────────────────────────────
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

    # ── Normal consistency check (FIXED: threshold < 0, not < -0.99) ─────────
    inverted_count = 0
    for declared_normal, verts in triangles:
        edge1 = _sub(verts[1], verts[0])
        edge2 = _sub(verts[2], verts[0])
        cross = _cross(edge1, edge2)
        if _magnitude(cross) < 1e-10:
            continue  # degenerate triangle, skip
        winding_normal = _normalize(cross)
        dn_mag = _magnitude(declared_normal)
        if dn_mag < 1e-10:
            continue  # zero normal declared — skip
        similarity = _dot(_normalize(declared_normal), winding_normal)
        if similarity < 0:
            inverted_count += 1

    if inverted_count > 0:
        issues.append(
            f"{inverted_count} face normal(s) are inverted relative to winding order"
        )

    # ── Surface area & volume ────────────────────────────────────────────────
    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)
    volume = abs(sum(_triangle_signed_volume(*verts) for _, verts in triangles))

    # ── Bounding box ─────────────────────────────────────────────────────────
    all_verts = [v for _, verts in triangles for v in verts]
    xs = [v[0] for v in all_verts]
    ys = [v[1] for v in all_verts]
    zs = [v[2] for v in all_verts]
    bounding_box = {
        "x": [round(min(xs), 6), round(max(xs), 6)],
        "y": [round(min(ys), 6), round(max(ys), 6)],
        "z": [round(min(zs), 6), round(max(zs), 6)],
    }

    return {
        "valid": len(issues) == 0,
        "format": "binary" if binary else "ascii",
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
