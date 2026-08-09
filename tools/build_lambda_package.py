"""
Build Lambda deployment package for V10 Research Engine.

Creates: build/v10-research-lambda.zip

Includes:
    research_engine/v10/  (all modules)
    research_engine/__init__.py

Excludes:
    tests/, .git/, __pycache__/, logs/, reports/, data/,
    .venv/, .env, large datasets, MT5 terminals

Run:
    python tools/build_lambda_package.py
"""

import os
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
BUILD_DIR = ROOT / "build"
ZIP_NAME = "v10-research-lambda.zip"

# Directories to include in the package
INCLUDE_DIRS = [
    "research_engine/v10",
    "research_engine/__init__.py",
]

# Files/patterns to exclude
EXCLUDE_PATTERNS = {
    "__pycache__",
    ".pyc",
    ".pyo",
    ".git",
    ".env",
    "tests",
    ".hypothesis",
    ".pytest_cache",
}

# Specific large files to exclude
EXCLUDE_FILES = {
    "research_universe.jsonl",
    "research_ready_trades.jsonl",
    "research_ready_trades_enriched.jsonl",
}


def build():
    print("=" * 60)
    print("  V10 RESEARCH LAMBDA — BUILD PACKAGE")
    print("=" * 60)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BUILD_DIR / ZIP_NAME

    if zip_path.exists():
        zip_path.unlink()

    file_count = 0
    total_size = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add lambda_handler at root
        handler_path = ROOT / "research_engine" / "v10" / "lambda_handler.py"
        if handler_path.exists():
            zf.write(handler_path, "lambda_handler.py")
            file_count += 1

        # Add research_engine package
        pkg_root = ROOT / "research_engine"

        # research_engine/__init__.py
        init_file = pkg_root / "__init__.py"
        if init_file.exists():
            zf.write(init_file, "research_engine/__init__.py")
            file_count += 1

        # research_engine/v10/ (recursive)
        v10_dir = pkg_root / "v10"
        for filepath in sorted(v10_dir.rglob("*")):
            if filepath.is_dir():
                continue
            if _should_exclude(filepath):
                continue

            arcname = f"research_engine/v10/{filepath.relative_to(v10_dir)}"
            arcname = arcname.replace("\\", "/")
            zf.write(filepath, arcname)
            file_count += 1
            total_size += filepath.stat().st_size

    zip_size = zip_path.stat().st_size
    print(f"\n  Output: {zip_path}")
    print(f"  Files: {file_count}")
    print(f"  Source size: {total_size / 1024:.1f} KB")
    print(f"  ZIP size: {zip_size / 1024:.1f} KB")
    print("=" * 60)
    return str(zip_path)


def _should_exclude(filepath: Path) -> bool:
    parts = str(filepath)
    for pattern in EXCLUDE_PATTERNS:
        if pattern in parts:
            return True
    if filepath.name in EXCLUDE_FILES:
        return True
    if not filepath.suffix == ".py":
        return True  # Only include .py files
    return False


if __name__ == "__main__":
    build()
