"""
Lambda Dependency & Isolation Audit.

Traces all imports from lambda_handler.py and reports:
    - Required source files
    - Required Python packages
    - Broker/MT5 dependencies (MUST BE ZERO)
    - Optional development files

Run:
    python tools/audit_lambda_deps.py
"""

import ast
import json
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

ENTRY = ROOT / "research_engine" / "v10" / "lambda_handler.py"
RESEARCH_PKG = ROOT / "research_engine"

# Banned imports for Lambda isolation
BANNED_MODULES = {
    "MetaTrader5", "mt5",
    "execution.mt5_execution",
    "core.mt5_timeout",
    "core.runtime.live_scanner",
}
BANNED_KEYWORDS = {"order_send", "position_open", "mt5.initialize"}


def trace_imports(entry_file: Path, base_dir: Path) -> dict:
    """Trace all imports reachable from entry file."""
    visited = set()
    internal_files = []
    external_packages = set()
    banned_found = []

    def _visit(filepath: Path):
        if filepath in visited:
            return
        visited.add(filepath)

        if not filepath.exists():
            return

        try:
            source = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _process_import(alias.name, filepath)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    _process_import(node.module, filepath)

    def _process_import(module_name: str, from_file: Path):
        # Check banned
        for banned in BANNED_MODULES:
            if banned in module_name:
                banned_found.append({"module": module_name, "from": str(from_file.relative_to(ROOT))})

        # Resolve internal
        parts = module_name.replace(".", "/")
        candidates = [
            base_dir.parent / f"{parts}.py",
            base_dir.parent / parts / "__init__.py",
        ]
        for candidate in candidates:
            if candidate.exists() and candidate not in visited:
                rel = str(candidate.relative_to(ROOT))
                internal_files.append(rel)
                _visit(candidate)
                return

        # External package
        top_level = module_name.split(".")[0]
        stdlib = {"os", "sys", "json", "pathlib", "time", "logging", "statistics",
                  "hashlib", "re", "abc", "enum", "dataclasses", "collections",
                  "datetime", "typing", "importlib", "inspect", "ast", "shutil"}
        if top_level not in stdlib and top_level != "research_engine":
            external_packages.add(top_level)

    _visit(entry_file)

    # Also trace through all research_engine/v10 files reachable
    for f in sorted((ROOT / "research_engine" / "v10").rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        _visit(f)

    return {
        "entry_point": str(entry_file.relative_to(ROOT)),
        "internal_files": sorted(set(internal_files)),
        "internal_count": len(set(internal_files)),
        "external_packages": sorted(external_packages),
        "banned_imports": banned_found,
        "isolation_status": "PASS" if not banned_found else "FAIL",
    }


def main():
    print("=" * 60)
    print("  LAMBDA DEPENDENCY & ISOLATION AUDIT")
    print("=" * 60)

    result = trace_imports(ENTRY, RESEARCH_PKG)

    print(f"\n  Entry: {result['entry_point']}")
    print(f"  Internal files: {result['internal_count']}")
    print(f"  External packages: {result['external_packages']}")
    print(f"  Banned imports found: {len(result['banned_imports'])}")
    print(f"  Isolation: {result['isolation_status']}")

    if result["banned_imports"]:
        print("\n  BANNED IMPORTS:")
        for b in result["banned_imports"]:
            print(f"    {b['module']} (from {b['from']})")

    # Save reports
    rep_dir = ROOT / "reports" / "research"
    rep_dir.mkdir(parents=True, exist_ok=True)

    (rep_dir / "lambda_dependency_audit.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )

    md = []
    md.append("# Lambda Dependency Audit")
    md.append("")
    md.append(f"Entry: `{result['entry_point']}`")
    md.append(f"Internal files: {result['internal_count']}")
    md.append(f"External packages: {', '.join(result['external_packages']) or 'None (stdlib only)'}")
    md.append(f"**Isolation: {result['isolation_status']}**")
    if result["banned_imports"]:
        md.append("\n## Banned Imports Found")
        for b in result["banned_imports"]:
            md.append(f"- `{b['module']}` in `{b['from']}`")
    md.append("\n---")
    (rep_dir / "lambda_dependency_audit.md").write_text("\n".join(md), encoding="utf-8")

    # Isolation report (same data, different perspective)
    isolation = {
        "isolation_status": result["isolation_status"],
        "banned_imports": result["banned_imports"],
        "external_packages": result["external_packages"],
        "note": "Lambda research path: S3 data -> Lambda -> Research Engine -> Reports -> S3",
    }
    (rep_dir / "lambda_isolation_audit.json").write_text(
        json.dumps(isolation, indent=2), encoding="utf-8"
    )
    (rep_dir / "lambda_isolation_audit.md").write_text(
        f"# Lambda Isolation Audit\n\n**Status: {isolation['isolation_status']}**\n\n"
        f"Banned imports: {len(isolation['banned_imports'])}\n"
        f"External packages: {', '.join(isolation['external_packages']) or 'None'}\n\n---",
        encoding="utf-8",
    )

    print(f"\n  Reports: reports/research/lambda_*_audit.*")
    print("=" * 60)
    return 0 if result["isolation_status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
