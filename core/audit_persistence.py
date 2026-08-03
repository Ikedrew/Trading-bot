"""
Persistence Layer Audit — Full S3 bucket integrity inspection.

Read-only inspection engine. No writes, deletes, or mutations.
Audits the S3 bucket against the expected storage contract and identifies:
    1. Missing root prefixes
    2. Symbol coverage gaps
    3. Timestamp anomalies
    4. Schema integrity violations
    5. Test fixture contamination
    6. Temporal sanity issues
    7. Promotion funnel drop-off

Operates on LOCAL persistence mirrors (logs/) with optional S3 scan.

Usage:
    from core.audit_persistence import run_persistence_audit

    report = run_persistence_audit()
    print(report["critical_findings"])
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_S3_BUCKET = "v10-engine"

REQUIRED_BUCKET_PREFIXES = {
    "events",
    "trade_truth",
    "trade_truth_graph",
    "shadow_trades",
    "edge_attribution",
    "edge_optimisation",
    "strategy_compiler",
}

# Local mirror directories (map to S3 prefixes)
_LOCAL_MIRRORS = {
    "events": "events",
    "trade_truth": "logs/trade_truth",
    "trade_truth_graph": "logs/trade_truth_graph",
    "shadow_trades": "logs/shadow_trades",
    "edge_attribution": "logs/edge_attribution",
    "edge_optimisation": "logs/edge_optimisation",
    "strategy_compiler": "logs/strategy_compiler",
}

# Schema fields required for trade_truth_v2
_REQUIRED_TRUTH_FIELDS = [
    "schema_version", "trade_id", "symbol",
    "timestamps.entry_time", "timestamps.exit_time",
    "prices.entry_price", "prices.exit_price",
    "position.direction",
    "outcome.r_multiple",
]

# Test fixture detection patterns
_FIXTURE_PATTERNS = [
    "today_trade", "survived_1", "survived_2", "test_", "pos_1", "pos_2",
    "pos_100", "pos_dup", "target_trade", "graph_test_", "lifecycle_test_",
]
_FIXTURE_PRICES = {1.1, 1.095, 1.105, 1.11}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ROOT PREFIX AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def _audit_prefixes() -> dict[str, Any]:
    """Check which required prefixes exist locally."""
    present = set()
    missing = set()
    unexpected: set[str] = set()

    for prefix, local_path in _LOCAL_MIRRORS.items():
        if Path(local_path).exists():
            present.add(prefix)
        else:
            missing.add(prefix)

    # Check for unexpected directories in logs/
    logs_path = Path("logs")
    if logs_path.exists():
        for d in logs_path.iterdir():
            if d.is_dir():
                name = d.name
                if name not in _LOCAL_MIRRORS.values() and name not in {"decision_audit", "state", "trade_journal"}:
                    unexpected.add(name)

    return {
        "present": sorted(present),
        "missing": sorted(missing),
        "unexpected": sorted(unexpected),
        "coverage_pct": round(len(present) / len(REQUIRED_BUCKET_PREFIXES) * 100, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SYMBOL COVERAGE AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def _audit_symbol_coverage() -> dict[str, Any]:
    """Enumerate symbols per layer and detect coverage gaps."""
    layer_symbols: dict[str, set[str]] = {}

    for prefix, local_path in _LOCAL_MIRRORS.items():
        path = Path(local_path)
        symbols: set[str] = set()
        if path.exists():
            for item in path.iterdir():
                if item.is_dir() and "_SB" in item.name:
                    symbols.add(item.name)
            # Also check JSONL filenames for symbol prefixes
            for f in path.rglob("*.jsonl"):
                parts = f.relative_to(path).parts
                if len(parts) >= 2 and "_SB" in parts[0]:
                    symbols.add(parts[0])
        layer_symbols[prefix] = symbols

    # Reference: symbols from events layer
    reference_symbols = layer_symbols.get("events", set())

    # Find gaps
    missing_coverage: dict[str, list[str]] = {}
    for prefix, symbols in layer_symbols.items():
        if prefix == "events":
            continue
        missing = sorted(reference_symbols - symbols) if reference_symbols else []
        if missing:
            missing_coverage[prefix] = missing

    return {
        "per_layer": {k: {"count": len(v), "symbols": sorted(v)} for k, v in layer_symbols.items()},
        "reference_symbols": sorted(reference_symbols),
        "missing_promotion_coverage": missing_coverage,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PARTITION DATE AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def _audit_partitions() -> dict[str, Any]:
    """Detect timestamp anomalies in date partitions."""
    anomalies: list[dict[str, str]] = []

    for prefix, local_path in _LOCAL_MIRRORS.items():
        path = Path(local_path)
        if not path.exists():
            continue
        for f in path.rglob("*.jsonl"):
            date_part = f.stem  # e.g., "2026-07-03" or "curated_2026-07-03"
            # Extract date from filename
            for segment in date_part.split("_"):
                if len(segment) == 10 and segment[4] == "-" and segment[7] == "-":
                    try:
                        d = datetime.strptime(segment, "%Y-%m-%d")
                        if d.year == 1970:
                            anomalies.append({"prefix": prefix, "file": str(f), "issue": "epoch_date_1970"})
                        elif d > datetime.now() + __import__("datetime").timedelta(days=7):
                            anomalies.append({"prefix": prefix, "file": str(f), "issue": "future_date"})
                    except ValueError:
                        anomalies.append({"prefix": prefix, "file": str(f), "issue": "invalid_date_format"})

    return {
        "anomalies_found": len(anomalies),
        "anomalies": anomalies[:20],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SCHEMA INTEGRITY AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def _get_nested(d: dict, path: str) -> Any:
    """Get nested dict value by dot-path."""
    parts = path.split(".")
    current = d
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _audit_schema_integrity() -> dict[str, Any]:
    """Validate trade_truth records against required schema."""
    violations: list[dict[str, Any]] = []
    total_records = 0
    valid_records = 0

    # Scan trade_truth and trade_truth_graph
    for prefix in ("trade_truth", "trade_truth_graph", "shadow_trades"):
        local_path = _LOCAL_MIRRORS.get(prefix)
        if not local_path:
            continue
        path = Path(local_path)
        if not path.exists():
            continue

        for f in path.rglob("*.jsonl"):
            with open(f, "r", encoding="utf-8") as fh:
                for line_num, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        violations.append({"prefix": prefix, "file": str(f), "line": line_num, "issue": "json_parse_error"})
                        continue

                    total_records += 1
                    issues = []

                    # Check required fields
                    for field in _REQUIRED_TRUTH_FIELDS:
                        val = _get_nested(record, field)
                        if val is None:
                            issues.append(f"missing:{field}")

                    # Check quality
                    if record.get("trade_id") == "":
                        issues.append("empty_trade_id")
                    if _get_nested(record, "outcome.r_multiple") == 0.0 and _get_nested(record, "prices.exit_price") != _get_nested(record, "prices.entry_price"):
                        issues.append("suspicious_zero_r")

                    if issues:
                        violations.append({
                            "prefix": prefix, "file": f.name, "line": line_num,
                            "trade_id": record.get("trade_id", "?"),
                            "issues": issues,
                        })
                    else:
                        valid_records += 1

    return {
        "total_records_scanned": total_records,
        "valid_records": valid_records,
        "violations_count": len(violations),
        "violations": violations[:30],
        "integrity_pct": round(valid_records / max(total_records, 1) * 100, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TEST FIXTURE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def _audit_fixtures() -> dict[str, Any]:
    """Detect likely test/synthetic data contamination."""
    suspicious: list[dict[str, Any]] = []

    for prefix in ("trade_truth", "trade_truth_graph", "shadow_trades"):
        local_path = _LOCAL_MIRRORS.get(prefix)
        if not local_path:
            continue
        path = Path(local_path)
        if not path.exists():
            continue

        for f in path.rglob("*.jsonl"):
            with open(f, "r", encoding="utf-8") as fh:
                for line_num, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    trade_id = str(record.get("trade_id", ""))
                    reasons = []

                    # Pattern match on trade_id
                    for pat in _FIXTURE_PATTERNS:
                        if pat in trade_id.lower():
                            reasons.append(f"fixture_pattern:{pat}")
                            break

                    # Static prices
                    entry = _get_nested(record, "prices.entry_price")
                    if entry in _FIXTURE_PRICES:
                        reasons.append(f"static_price:{entry}")

                    # Duplicated timestamps (June 2024 epoch)
                    entry_time = _get_nested(record, "timestamps.entry_time")
                    if entry_time and isinstance(entry_time, (int, float)):
                        if 1717000000 <= entry_time <= 1717500000:
                            reasons.append("hardcoded_june_2024_timestamp")

                    if reasons:
                        suspicious.append({
                            "prefix": prefix, "file": f.name, "line": line_num,
                            "trade_id": trade_id, "reasons": reasons,
                        })

    return {
        "suspicious_count": len(suspicious),
        "suspicious_records": suspicious[:20],
        "contamination_risk": "HIGH" if len(suspicious) > 10 else "MEDIUM" if len(suspicious) > 0 else "NONE",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TEMPORAL SANITY AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def _audit_temporal() -> dict[str, Any]:
    """Validate temporal consistency of trade records."""
    anomalies: list[dict[str, Any]] = []
    total = 0

    for prefix in ("trade_truth", "trade_truth_graph", "shadow_trades"):
        local_path = _LOCAL_MIRRORS.get(prefix)
        if not local_path:
            continue
        path = Path(local_path)
        if not path.exists():
            continue

        for f in path.rglob("*.jsonl"):
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    total += 1
                    entry_time = _get_nested(record, "timestamps.entry_time") or 0
                    exit_time = _get_nested(record, "timestamps.exit_time") or 0
                    trade_id = record.get("trade_id", "?")

                    issues = []

                    if exit_time < entry_time and exit_time > 0:
                        issues.append("exit_before_entry")

                    if entry_time > 0 and exit_time > 0:
                        duration_min = (exit_time - entry_time) / 60
                        if duration_min > 10080:  # 7 days
                            issues.append(f"unrealistic_duration:{duration_min:.0f}min")
                        if duration_min < 0:
                            issues.append("negative_duration")

                    # Static timestamp detection
                    if entry_time in (0, 1717400000, 1000, 2000, 3000):
                        issues.append(f"hardcoded_entry_time:{entry_time}")

                    if issues:
                        anomalies.append({
                            "prefix": prefix, "trade_id": trade_id,
                            "entry_time": entry_time, "exit_time": exit_time,
                            "issues": issues,
                        })

    return {
        "total_records_checked": total,
        "anomalies_found": len(anomalies),
        "anomalies": anomalies[:20],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 7. PROMOTION FUNNEL AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def _audit_promotion_funnel() -> dict[str, Any]:
    """Compute record counts per layer and detect dead layers."""
    layer_counts: dict[str, dict[str, int]] = {}
    layer_order = ["events", "trade_truth", "trade_truth_graph", "shadow_trades", "edge_attribution", "edge_optimisation", "strategy_compiler"]

    for prefix in layer_order:
        local_path = _LOCAL_MIRRORS.get(prefix)
        if not local_path:
            layer_counts[prefix] = {"objects": 0, "records": 0, "symbols": 0}
            continue
        path = Path(local_path)
        if not path.exists():
            layer_counts[prefix] = {"objects": 0, "records": 0, "symbols": 0}
            continue

        objects = list(path.rglob("*.jsonl"))
        records = 0
        symbols: set[str] = set()
        for f in objects:
            with open(f, "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        records += 1
            # Extract symbol from path
            parts = f.relative_to(path).parts
            if len(parts) >= 2 and "_SB" in parts[0]:
                symbols.add(parts[0])

        layer_counts[prefix] = {"objects": len(objects), "records": records, "symbols": len(symbols)}

    # Dead layers
    dead_layers = [p for p, c in layer_counts.items() if c["records"] == 0]

    # Promotion ratios
    ratios: dict[str, str] = {}
    for i in range(1, len(layer_order)):
        prev = layer_order[i - 1]
        curr = layer_order[i]
        prev_count = layer_counts[prev]["records"]
        curr_count = layer_counts[curr]["records"]
        if prev_count > 0:
            ratios[f"{prev}->{curr}"] = f"{curr_count}/{prev_count} ({round(curr_count/prev_count*100, 1)}%)"
        else:
            ratios[f"{prev}->{curr}"] = "0/0"

    return {
        "per_layer": layer_counts,
        "dead_layers": dead_layers,
        "promotion_ratios": ratios,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def run_persistence_audit() -> dict[str, Any]:
    """
    Run full persistence layer audit. Read-only — no mutations.

    Returns structured report with severity-classified findings.
    """
    prefix_audit = _audit_prefixes()
    symbol_coverage = _audit_symbol_coverage()
    partition_audit = _audit_partitions()
    schema_audit = _audit_schema_integrity()
    fixture_audit = _audit_fixtures()
    temporal_audit = _audit_temporal()
    funnel_audit = _audit_promotion_funnel()

    # Classify critical findings
    critical: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    # Prefix gaps
    if prefix_audit["missing"]:
        critical.append(f"MISSING PREFIXES: {prefix_audit['missing']}")

    # Dead layers
    if funnel_audit["dead_layers"]:
        warnings.append(f"DEAD LAYERS (zero records): {funnel_audit['dead_layers']}")

    # Schema violations
    if schema_audit["violations_count"] > 0:
        warnings.append(f"SCHEMA VIOLATIONS: {schema_audit['violations_count']} records")

    # Fixture contamination
    if fixture_audit["contamination_risk"] == "HIGH":
        critical.append(f"TEST FIXTURE CONTAMINATION: {fixture_audit['suspicious_count']} records")
    elif fixture_audit["contamination_risk"] == "MEDIUM":
        warnings.append(f"Possible test data: {fixture_audit['suspicious_count']} suspicious records")

    # Temporal anomalies
    if temporal_audit["anomalies_found"] > 0:
        warnings.append(f"TEMPORAL ANOMALIES: {temporal_audit['anomalies_found']} records")

    # Partition anomalies
    if partition_audit["anomalies_found"] > 0:
        info.append(f"Partition date anomalies: {partition_audit['anomalies_found']}")

    # Symbol gaps
    if symbol_coverage["missing_promotion_coverage"]:
        info.append(f"Symbol promotion gaps: {symbol_coverage['missing_promotion_coverage']}")

    # Bucket health score
    issues_total = len(critical) * 3 + len(warnings) * 2 + len(info)
    health_score = max(0, 100 - issues_total * 10)

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bucket": _S3_BUCKET,
            "audit_mode": "local_mirror",
        },
        "bucket_health": {
            "score": health_score,
            "grade": "A" if health_score >= 80 else "B" if health_score >= 60 else "C" if health_score >= 40 else "D",
        },
        "prefix_audit": prefix_audit,
        "symbol_coverage": symbol_coverage,
        "partition_audit": partition_audit,
        "schema_integrity": schema_audit,
        "temporal_integrity": temporal_audit,
        "fixture_contamination": fixture_audit,
        "promotion_funnel": funnel_audit,
        "critical_findings": critical,
        "warnings": warnings,
        "info": info,
    }


def print_audit(report: dict[str, Any]) -> None:
    """Print human-readable audit summary."""
    health = report.get("bucket_health", {})
    prefix = report.get("prefix_audit", {})
    funnel = report.get("promotion_funnel", {})

    print()
    print("=" * 60)
    print("  PERSISTENCE LAYER AUDIT")
    print("=" * 60)
    print(f"  Bucket: {report.get('metadata', {}).get('bucket', '?')}")
    print(f"  Health: {health.get('score', 0)}/100 (Grade {health.get('grade', '?')})")
    print()

    print("─── PREFIX COVERAGE ────────────────────────────────────────────")
    print(f"  Present:    {prefix.get('present', [])}")
    print(f"  Missing:    {prefix.get('missing', [])}")
    print(f"  Coverage:   {prefix.get('coverage_pct', 0)}%")
    print()

    print("─── PROMOTION FUNNEL ───────────────────────────────────────────")
    for layer, counts in funnel.get("per_layer", {}).items():
        status = "✓" if counts["records"] > 0 else "✗"
        print(f"  {status} {layer:<22} objects={counts['objects']:>4} records={counts['records']:>6} symbols={counts['symbols']}")
    print()
    if funnel.get("dead_layers"):
        print(f"  ⚠ Dead layers: {funnel['dead_layers']}")
        print()

    # Findings
    for finding in report.get("critical_findings", []):
        print(f"  🔴 CRITICAL: {finding}")
    for finding in report.get("warnings", []):
        print(f"  🟡 WARNING:  {finding}")
    for finding in report.get("info", []):
        print(f"  🔵 INFO:     {finding}")
    print()
    print("=" * 60)


def export_audit(report: dict[str, Any], path: str = "analysis/reports/persistence_audit.json") -> str:
    """Export audit report."""
    filepath = Path(path)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    return str(filepath)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    report = run_persistence_audit()
    print_audit(report)
    output = sys.argv[1] if len(sys.argv) > 1 else "analysis/reports/persistence_audit.json"
    export_audit(report, output)
    print(f"  Report saved to: {output}")
