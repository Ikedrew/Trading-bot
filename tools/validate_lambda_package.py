"""
Validate the Lambda deployment package.

Checks:
    - lambda_handler.py exists at root
    - research_engine package exists
    - required modules present
    - tests excluded
    - large datasets excluded
    - MT5/broker modules excluded
    - credentials absent

Run:
    python tools/validate_lambda_package.py
"""

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
ZIP_PATH = ROOT / "build" / "v10-research-lambda.zip"

REQUIRED_FILES = [
    "lambda_handler.py",
    "research_engine/__init__.py",
    "research_engine/v10/__init__.py",
    "research_engine/v10/operations/__init__.py",
    "research_engine/v10/operations/router.py",
    "research_engine/v10/research_intelligence/__init__.py",
    "research_engine/v10/campaigns/__init__.py",
    "research_engine/v10/research_governance/__init__.py",
    "research_engine/v10/validation_lab/__init__.py",
    "research_engine/v10/shadow/__init__.py",
    "research_engine/v10/candidates/__init__.py",
    "research_engine/v10/baselines/__init__.py",
    "research_engine/v10/optimisation/__init__.py",
    "research_engine/v10/domains/__init__.py",
    "research_engine/v10/segmentation_engine.py",
    "research_engine/v10/base.py",
]

MUST_NOT_CONTAIN = [
    "test_",
    ".env",
    "research_universe.jsonl",
    "research_ready_trades.jsonl",
    "mt5_execution",
    "MetaTrader5",
    "__pycache__",
]


def validate():
    print("=" * 60)
    print("  V10 LAMBDA PACKAGE VALIDATION")
    print("=" * 60)

    if not ZIP_PATH.exists():
        print(f"\n  ERROR: {ZIP_PATH} not found. Run build_lambda_package.py first.")
        return {"status": "FAIL", "reason": "ZIP not found"}

    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        names = zf.namelist()

    issues = []
    checks = {}

    # Check required files
    missing = [f for f in REQUIRED_FILES if f not in names]
    checks["required_files"] = "PASS" if not missing else "FAIL"
    if missing:
        issues.append(f"Missing required files: {missing}")

    # Check excluded content
    banned_found = []
    for name in names:
        for banned in MUST_NOT_CONTAIN:
            if banned in name:
                banned_found.append(f"{banned} in {name}")
    checks["excluded_content"] = "PASS" if not banned_found else "FAIL"
    if banned_found:
        issues.append(f"Banned content found: {banned_found[:5]}")

    # Check no credentials
    cred_patterns = [".env", "credentials", "secret", "aws_access"]
    creds_found = [n for n in names if any(p in n.lower() for p in cred_patterns)]
    checks["no_credentials"] = "PASS" if not creds_found else "FAIL"
    if creds_found:
        issues.append(f"Credential files found: {creds_found}")

    # Size check
    zip_size = ZIP_PATH.stat().st_size
    checks["size_reasonable"] = "PASS" if zip_size < 10_000_000 else "WARNING"

    # Overall
    all_pass = all(v == "PASS" for v in checks.values())
    status = "PASS" if all_pass else "FAIL"

    print(f"\n  ZIP: {ZIP_PATH.name} ({zip_size / 1024:.1f} KB)")
    print(f"  Files in package: {len(names)}")
    print(f"\n  Checks:")
    for check, result in checks.items():
        print(f"    {check:<25s}: {result}")
    if issues:
        print(f"\n  Issues:")
        for issue in issues:
            print(f"    - {issue}")
    print(f"\n  OVERALL: {status}")
    print("=" * 60)

    # Save report
    report = {
        "status": status,
        "zip_path": str(ZIP_PATH),
        "zip_size_bytes": zip_size,
        "file_count": len(names),
        "checks": checks,
        "issues": issues,
        "missing_required": missing,
        "banned_found": banned_found[:10],
    }
    rep_dir = ROOT / "reports" / "research"
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / "lambda_package_validation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (rep_dir / "lambda_package_validation.md").write_text(
        f"# Lambda Package Validation\n\n**Status: {status}**\n\n"
        f"Files: {len(names)} | Size: {zip_size/1024:.1f} KB\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in checks.items())
        + "\n\n---", encoding="utf-8"
    )

    return report


if __name__ == "__main__":
    result = validate()
    sys.exit(0 if result["status"] == "PASS" else 1)
