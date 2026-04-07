"""Batch validation of supplier STL files using the improved validator logic."""
import json
import math
import os
import glob
import sys

# Force UTF-8 output
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# ── geometry helpers ──────────────────────────────────────────────────────────

def _cross(a, b):
    return (
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0],
    )

def _sub(a, b):
    return (a[0]-b[0], a[1]-b[1], a[2]-b[2])

def _dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]

def _magnitude(v):
    return math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)

def _normalize(v):
    m = _magnitude(v)
    if m == 0:
        return (0.0, 0.0, 0.0)
    return (v[0]/m, v[1]/m, v[2]/m)

def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))

# ── parser & validator ────────────────────────────────────────────────────────

def validate_stl(path: str) -> dict:
    issues = []
    triangles = []  # list of (declared_normal, [v1, v2, v3])

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
                issues.append(f"Malformed facet normal at line {i+1}")
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
                                vertices.append((
                                    float(vparts[1]),
                                    float(vparts[2]),
                                    float(vparts[3])
                                ))
                            except (IndexError, ValueError):
                                issues.append(f"Malformed vertex at line {i+1}")
                        i += 1

            if len(vertices) == 3:
                triangles.append((declared_normal, vertices))
            else:
                issues.append(f"Incomplete triangle near line {i+1}")
            continue

        i += 1

    if not triangles:
        issues.append("No valid faces found")
        return {
            "valid": False,
            "triangle_count": 0,
            "surface_area": 0.0,
            "issues": issues,
        }

    # ── degenerate triangle check ─────────────────────────────────────────────
    degenerate_count = 0
    for _, verts in triangles:
        area = _triangle_area(*verts)
        if area < 1e-12:
            degenerate_count += 1
    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")

    # ── manifold check ────────────────────────────────────────────────────────
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j+1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) - not a closed solid")

    # ── inverted normal check (winding-order cross-product) ───────────────────
    inverted_count = 0
    for declared_normal, verts in triangles:
        v1, v2, v3 = verts
        computed = _cross(_sub(v2, v1), _sub(v3, v1))
        mag = _magnitude(computed)
        if mag < 1e-12:
            continue  # degenerate -- already flagged
        computed_n = _normalize(computed)
        declared_n = _normalize(declared_normal)
        # dot < -0.5 -> more than 90 degrees apart -> inverted
        if _dot(computed_n, declared_n) < -0.5:
            inverted_count += 1
    if inverted_count > 0:
        issues.append(f"{inverted_count} face(s) have inverted normals (winding-order mismatch)")

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }

# ── batch run ─────────────────────────────────────────────────────────────────

def main():
    stl_files = sorted(glob.glob("supplier_*.stl"))
    if not stl_files:
        print("No supplier STL files found.")
        return

    results = {}
    invalid_files = []

    for stl_path in stl_files:
        result = validate_stl(stl_path)
        results[stl_path] = result
        if not result["valid"]:
            invalid_files.append(stl_path)

    # ── console report ────────────────────────────────────────────────────────
    print("=" * 70)
    print("  MANUFACTURING VALIDATION REPORT")
    print(f"  Files checked : {len(stl_files)}")
    print(f"  Files VALID   : {len(stl_files) - len(invalid_files)}")
    print(f"  Files INVALID : {len(invalid_files)}")
    print("=" * 70)

    for path in stl_files:
        r = results[path]
        status = "PASS" if r["valid"] else "FAIL"
        print(f"\n[{status}] {path}")
        print(f"       Triangles    : {r['triangle_count']}")
        print(f"       Surface area : {r['surface_area']}")
        if not r["valid"]:
            for issue in r["issues"]:
                print(f"       ISSUE: {issue}")

    print("\n" + "=" * 70)
    if invalid_files:
        print("  SUMMARY OF INVALID FILES:")
        for path in invalid_files:
            print(f"  * {path}")
            for issue in results[path]["issues"]:
                print(f"      - {issue}")
    else:
        print("  All files passed validation.")
    print("=" * 70)

    # ── write JSON report ─────────────────────────────────────────────────────
    with open("validation_report.json", "w", encoding="utf-8") as fh:
        json.dump({
            "summary": {
                "total": len(stl_files),
                "valid": len(stl_files) - len(invalid_files),
                "invalid": len(invalid_files),
                "invalid_files": invalid_files,
            },
            "details": results,
        }, fh, indent=2)
    print("\nFull results written to validation_report.json")

if __name__ == "__main__":
    main()
