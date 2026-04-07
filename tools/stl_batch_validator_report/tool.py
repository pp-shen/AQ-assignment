"""stl_batch_validator_report.py — Batch STL validator with manufacturing report.

Scans a directory of ASCII STL files and produces:
  - Console report (valid / invalid with reasons)
  - validation_report.json

Checks performed per file:
  1. Manifold closure (open edges)
  2. Inverted face normals (winding-order cross-product vs declared normal)
  3. Degenerate (zero-area) triangles

Fixes the hidden inverted-normal bug in stl_validator_provided.py.

Usage:
    python stl_batch_validator_report.py [stl_directory]
    Default directory: stl_files/
"""
import os
import sys
import json
import math
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


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
    mag = _magnitude(v)
    if mag < 1e-10:
        return None
    return (v[0] / mag, v[1] / mag, v[2] / mag)

def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

def _triangle_area(v1, v2, v3):
    return 0.5 * _magnitude(_cross(_sub(v2, v1), _sub(v3, v1)))


def parse_stl(path: str) -> dict:
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

    if not triangles:
        issues.append("No valid faces found")

    # Manifold check: every edge must appear exactly twice
    edge_count: dict = {}
    for _, verts in triangles:
        rounded = [tuple(round(x, 6) for x in v) for v in verts]
        for j in range(3):
            edge = tuple(sorted([rounded[j], rounded[(j + 1) % 3]]))
            edge_count[edge] = edge_count.get(edge, 0) + 1

    open_edges = sum(1 for cnt in edge_count.values() if cnt == 1)
    if open_edges > 0:
        issues.append(f"Mesh has {open_edges} open edge(s) - not a closed solid")

    # Degenerate triangle and inverted normal checks
    degenerate_count = 0
    inverted_count = 0
    for declared_normal, verts in triangles:
        v1, v2, v3 = verts
        cross = _cross(_sub(v2, v1), _sub(v3, v1))
        area = 0.5 * _magnitude(cross)

        if area < 1e-10:
            degenerate_count += 1
            continue

        computed_normal = _normalize(cross)
        if computed_normal is None:
            degenerate_count += 1
            continue

        declared_mag = _magnitude(declared_normal)
        if declared_mag < 1e-10:
            continue  # zero declared normal — skip direction check
        declared_unit = _normalize(declared_normal)

        if _dot(computed_normal, declared_unit) < -0.5:
            inverted_count += 1

    if degenerate_count > 0:
        issues.append(f"{degenerate_count} degenerate (zero-area) triangle(s) found")
    if inverted_count > 0:
        issues.append(f"{inverted_count} triangle(s) have inverted normals (winding-order mismatch)")

    surface_area = sum(_triangle_area(*verts) for _, verts in triangles)

    return {
        "valid": len(issues) == 0,
        "triangle_count": len(triangles),
        "surface_area": round(surface_area, 6),
        "issues": issues,
    }


def run_batch(stl_dir: str = "stl_files") -> list:
    files = sorted(f for f in os.listdir(stl_dir) if f.endswith(".stl"))
    results = []
    for fname in files:
        path = os.path.join(stl_dir, fname)
        result = parse_stl(path)
        result["file"] = fname
        results.append(result)
    return results


def print_report(results: list):
    valid_count = sum(1 for r in results if r["valid"])
    invalid_count = len(results) - valid_count

    print("=" * 72)
    print("  STL MANUFACTURING VALIDATION REPORT")
    print("  Validator: stl_batch_validator_report")
    print(f"  Files checked : {len(results)}")
    print(f"  VALID         : {valid_count}")
    print(f"  INVALID       : {invalid_count}")
    print("=" * 72)

    print("\n--- VALID FILES ---")
    for r in results:
        if r["valid"]:
            print(f"  [PASS] {r['file']:<45}  triangles={r['triangle_count']:4d}  area={r['surface_area']:.4f}")

    print("\n--- INVALID FILES (must be corrected before manufacturing) ---")
    for r in results:
        if not r["valid"]:
            print(f"\n  [FAIL] {r['file']}")
            print(f"         Triangles    : {r['triangle_count']}")
            print(f"         Surface area : {r['surface_area']:.4f}")
            print(f"         Issues ({len(r['issues'])})  :")
            for issue in r["issues"]:
                print(f"           * {issue}")

    print("\n" + "=" * 72)
    print("SUMMARY TABLE")
    print(f"  {'File':<45} {'Status':>8} {'Triangles':>10}   Issues")
    print("  " + "-" * 70)
    for r in results:
        status = "PASS" if r["valid"] else "FAIL"
        issue_str = "; ".join(r["issues"]) if r["issues"] else "none"
        if len(issue_str) > 55:
            issue_str = issue_str[:52] + "..."
        print(f"  {r['file']:<45} {status:>8} {r['triangle_count']:>10}   {issue_str}")
    print("=" * 72)


if __name__ == "__main__":
    stl_dir = sys.argv[1] if len(sys.argv) > 1 else "stl_files"
    results = run_batch(stl_dir)
    print_report(results)
    report_path = "validation_report.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull JSON report written to: {report_path}")
