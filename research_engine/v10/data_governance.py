"""
V10 Layer 0 — Data Governance.

Validates integrity, consistency, and completeness of all trading datasets
before research execution. No experiment should run unless the underlying
data passes governance checks.

Usage:
    from research_engine.v10.data_governance import DataGovernanceValidator, research_data_allowed

    validator = DataGovernanceValidator()
    result = validator.validate()

    if not research_data_allowed():
        stop_research_run()

CLI:
    python -m research_engine.v10.data_governance
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# PATH CONSTANTS
# ═══════════════════════════════════════════════════════════════

_JOURNAL_DIR = "logs/trade_journal"
_RESEARCH_READY = "logs/research_ready_trade_dataset/research_ready_trades.jsonl"
_RESEARCH_ENRICHED = "logs/research_ready_trade_dataset/research_ready_trades_enriched.jsonl"
_EXCLUDED_FILE = "logs/research_ready_trade_dataset/excluded_trades.jsonl"
_RECON_REPORT = "reports/research/mt5_reconciliation_report.json"
_DECISION_TRACE_DIR = "logs/decision_trace"
_EXECUTION_RESULTS_DIR = "logs/execution_results"
_REPORTS_DIR = "reports/research"

# ═══════════════════════════════════════════════════════════════
# THRESHOLDS
# ═══════════════════════════════════════════════════════════════

_PNL_TOLERANCE_PCT = 5.0        # % difference before WARNING
_PNL_TOLERANCE_FAIL_PCT = 20.0  # % difference before FAIL
_DECISION_COVERAGE_WARN = 0.80  # Below 80% = WARNING
_DECISION_COVERAGE_FAIL = 0.50  # Below 50% = FAIL


# ═══════════════════════════════════════════════════════════════
# STATUS ENUM
# ═══════════════════════════════════════════════════════════════

class GovernanceStatus:
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


# ═══════════════════════════════════════════════════════════════
# VALIDATOR CLASS
# ═══════════════════════════════════════════════════════════════

class DataGovernanceValidator:
    """
    Data Governance validation engine.

    Verifies integrity, consistency, and completeness of all trading
    datasets before research execution.
    """

    def __init__(
        self,
        journal_dir: str | None = None,
        research_file: str | None = None,
        excluded_file: str | None = None,
        recon_file: str | None = None,
        decision_trace_dir: str | None = None,
        execution_results_dir: str | None = None,
        reports_dir: str | None = None,
    ):
        self._journal_dir = Path(journal_dir or _JOURNAL_DIR)
        self._research_file = Path(research_file or _RESEARCH_READY)
        self._excluded_file = Path(excluded_file or _EXCLUDED_FILE)
        self._recon_file = Path(recon_file or _RECON_REPORT)
        self._dt_dir = Path(decision_trace_dir or _DECISION_TRACE_DIR)
        self._exec_dir = Path(execution_results_dir or _EXECUTION_RESULTS_DIR)
        self._reports_dir = Path(reports_dir or _REPORTS_DIR)

        # Loaded data (lazy)
        self._journal_trades: list[dict] | None = None
        self._research_trades: list[dict] | None = None
        self._excluded_trades: list[dict] | None = None
        self._recon_data: dict | None = None
        self._recon_entries: list[dict] | None = None

    # ═══════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════

    def validate(self) -> dict[str, Any]:
        """
        Run all governance checks and produce the final report.

        Returns:
            {
                "data_trust": "PASS" | "WARNING" | "FAIL",
                "checks": {...},
                "timestamp": str,
                "dataset_version": str,
            }
        """
        self._load_all()

        checks = {
            "trade_counts": self.check_trade_counts(),
            "pnl_reconciliation": self.check_pnl(),
            "identity_validation": self.check_identity(),
            "field_completeness": self.check_fields(),
            "decision_coverage": self.check_decision_coverage(),
        }

        # Determine overall trust
        statuses = [c["status"] for c in checks.values()]
        if GovernanceStatus.FAIL in statuses:
            data_trust = GovernanceStatus.FAIL
        elif GovernanceStatus.WARNING in statuses:
            data_trust = GovernanceStatus.WARNING
        else:
            data_trust = GovernanceStatus.PASS

        report = {
            "data_trust": data_trust,
            "checks": checks,
            "timestamp": timestamp_now(),
            "dataset_version": self._get_dataset_version(),
            "summary": {
                "journal_trades": len(self._journal_trades or []),
                "research_trades": len(self._research_trades or []),
                "excluded_trades": len(self._excluded_trades or []),
                "mt5_matched": self._recon_data.get("matched", 0) if self._recon_data else 0,
            },
        }

        self.generate_report(report)
        return report

    def check_trade_counts(self) -> dict[str, Any]:
        """
        Check 1: Trade count validation.

        MT5 matched == Journal == Research + Excluded
        """
        journal_count = len(self._journal_trades or [])
        research_count = len(self._research_trades or [])
        excluded_count = len(self._excluded_trades or [])
        mt5_matched = self._recon_data.get("matched", 0) if self._recon_data else 0

        research_plus_excluded = research_count + excluded_count

        issues = []
        status = GovernanceStatus.PASS

        if mt5_matched > 0 and mt5_matched != journal_count:
            issues.append(f"MT5 matched ({mt5_matched}) != journal ({journal_count})")
            status = GovernanceStatus.FAIL

        if journal_count != research_plus_excluded:
            issues.append(
                f"Journal ({journal_count}) != research+excluded ({research_plus_excluded})"
            )
            status = GovernanceStatus.FAIL

        if journal_count == 0:
            issues.append("No journal trades found")
            status = GovernanceStatus.FAIL

        return {
            "mt5_trades": mt5_matched,
            "journal_trades": journal_count,
            "research_trades": research_count,
            "excluded_trades": excluded_count,
            "research_plus_excluded": research_plus_excluded,
            "issues": issues,
            "status": status,
        }

    def check_pnl(self) -> dict[str, Any]:
        """
        Check 2: PnL reconciliation.

        Uses canonical net_realised_pnl (gross + commission + swap + fees)
        and compares the SAME trade population between MT5 and research.
        """
        if not self._recon_entries:
            return {"status": GovernanceStatus.WARNING, "issues": ["No reconciliation data available"]}

        from research_engine.v10.pnl_normalization import get_canonical_pnl_totals

        totals = get_canonical_pnl_totals(
            self._research_trades or [],
            self._recon_entries,
        )

        mt5_pnl = totals["mt5_net"]
        research_pnl = totals["research_net"]
        diff_abs = totals["diff_abs"]
        diff_pct = totals["diff_pct"]

        issues = []
        status = GovernanceStatus.PASS

        if diff_pct > _PNL_TOLERANCE_FAIL_PCT:
            issues.append(f"PnL difference {diff_pct:.1f}% exceeds FAIL threshold ({_PNL_TOLERANCE_FAIL_PCT}%)")
            status = GovernanceStatus.FAIL
        elif diff_pct > _PNL_TOLERANCE_PCT:
            issues.append(f"PnL difference {diff_pct:.1f}% exceeds WARNING threshold ({_PNL_TOLERANCE_PCT}%)")
            status = GovernanceStatus.WARNING

        return {
            "mt5_pnl": mt5_pnl,
            "research_pnl": research_pnl,
            "matched_trades": totals["matched_trades"],
            "difference_abs": diff_abs,
            "difference_pct": diff_pct,
            "canonical_field": "net_realised_pnl (gross + commission + swap + fees)",
            "issues": issues,
            "status": status,
        }

    def check_identity(self) -> dict[str, Any]:
        """
        Check 3: Trade identity validation.

        Every trade must have a unique identity.
        No duplicates. No orphans.
        """
        research_tickets = [t.get("position_ticket") for t in (self._research_trades or [])]
        journal_tickets = [t.get("position_ticket") for t in (self._journal_trades or [])]
        excluded_tickets = [t.get("position_ticket") for t in (self._excluded_trades or [])]

        # Missing tickets (None or 0)
        missing_tickets = [t for t in research_tickets if not t]

        # Duplicates in research
        seen = set()
        duplicates = []
        for ticket in research_tickets:
            if ticket and ticket in seen:
                duplicates.append(ticket)
            if ticket:
                seen.add(ticket)

        # Unmatched: in research but not in journal
        journal_set = set(t for t in journal_tickets if t)
        research_set = set(t for t in research_tickets if t)
        excluded_set = set(t for t in excluded_tickets if t)
        all_derived = research_set | excluded_set

        unmatched = research_set - journal_set

        issues = []
        status = GovernanceStatus.PASS

        if missing_tickets:
            issues.append(f"{len(missing_tickets)} trades with missing position_ticket")
            status = GovernanceStatus.FAIL

        if duplicates:
            issues.append(f"{len(duplicates)} duplicate position_tickets")
            status = GovernanceStatus.FAIL

        if unmatched:
            issues.append(f"{len(unmatched)} research trades not in journal")
            status = GovernanceStatus.FAIL

        return {
            "missing_ticket_count": len(missing_tickets),
            "duplicate_ticket_count": len(duplicates),
            "unmatched_position_count": len(unmatched),
            "total_research_identities": len(research_tickets),
            "issues": issues,
            "status": status,
        }

    def check_fields(self) -> dict[str, Any]:
        """
        Check 4: Field completeness validation.

        Required research fields must be present.
        Uses enriched dataset when available (dt_* fields map to canonical names).
        """
        required_fields = {
            "symbol": "symbol",
            "direction": "direction",
            "entry_time": "entry_time",
            "exit_time": "exit_time",
            "entry_price": "entry_price",
            "exit_price": "exit_price",
            "stop_loss": "stop_loss",
            "broker_pnl": "broker_pnl",
            "realised_r": "realised_r",
            "exit_reason": "exit_reason_validated",
        }

        # Desired fields — check enriched dt_* fields as authoritative source
        # For strategy: dt_strategy OR dt_v10_strategy_family OR pattern (V1 trades use pattern as strategy)
        desired_fields = {
            "strategy": ["dt_strategy", "dt_v10_strategy_family", "dt_pattern", "pattern", "strategy"],
            "regime": ["dt_v10_regime", "dt_regime", "regime"],
            "score": ["dt_score_strategy", "dt_score_neutral", "score"],
            "correlation_id": ["correlation_id"],
            "pattern": ["dt_pattern", "pattern"],
        }

        # Use enriched dataset if available, else base research
        enriched_path = Path(str(self._research_file).replace(
            "research_ready_trades.jsonl", "research_ready_trades_enriched.jsonl"
        ))
        if enriched_path.exists():
            check_trades = self._load_jsonl_file(enriched_path)
            source_used = "enriched"
        else:
            check_trades = self._research_trades or []
            source_used = "base"

        missing_required: dict[str, int] = {}
        missing_desired: dict[str, int] = {}

        for t in check_trades:
            for label, field in required_fields.items():
                val = t.get(field)
                if val is None or val == "":
                    missing_required[label] = missing_required.get(label, 0) + 1

            for label, field_candidates in desired_fields.items():
                # Check any of the candidate fields for a value
                found = False
                for field in field_candidates:
                    val = t.get(field)
                    if val is not None and val != "" and val != 0:
                        found = True
                        break
                if not found:
                    missing_desired[label] = missing_desired.get(label, 0) + 1

        issues = []
        status = GovernanceStatus.PASS

        # Any required field missing = FAIL
        critical_missing = {k: v for k, v in missing_required.items()
                           if k in ("symbol", "direction", "entry_time", "exit_time", "entry_price")}
        if critical_missing:
            issues.append(f"Critical fields missing: {critical_missing}")
            status = GovernanceStatus.FAIL
        elif missing_required:
            issues.append(f"Required fields with gaps: {missing_required}")
            status = GovernanceStatus.WARNING

        if missing_desired:
            if status == GovernanceStatus.PASS:
                status = GovernanceStatus.WARNING
            issues.append(f"Desired fields with gaps: {missing_desired}")

        return {
            "missing_required": missing_required,
            "missing_desired": missing_desired,
            "total_trades_checked": len(check_trades),
            "source_used": source_used,
            "issues": issues,
            "status": status,
        }

    def check_decision_coverage(self) -> dict[str, Any]:
        """
        Check 5: Decision trace coverage.

        What percentage of research trades have a matching decision trace?
        """
        if not self._research_trades:
            return {"status": GovernanceStatus.FAIL, "issues": ["No research trades"]}

        # Load EXECUTE decisions
        execute_decisions = self._load_execute_decisions()
        total = len(self._research_trades)

        # Build index by (symbol, cycle_id)
        dt_by_sym_cycle: set[tuple[str, int]] = set()
        dt_by_entity: set[str] = set()
        for d in execute_decisions:
            sym = d.get("symbol", "")
            cycle = d.get("cycle_id", 0)
            if sym and cycle:
                dt_by_sym_cycle.add((sym, cycle))
            eid = d.get("entity_id", "")
            if eid:
                dt_by_entity.add(eid)

        # Match research trades to decisions
        matched = 0
        unmatched_trades = []

        for t in self._research_trades:
            cor_id = t.get("correlation_id", "")
            symbol = t.get("symbol", "")
            found = False

            # Remediation Stage 8: current-epoch lineage joins on the explicit
            # canonical_opportunity_id — regex parsing is historical-only.
            canonical = (
                (t.get("identity") or {}).get("canonical_opportunity_id")
                or t.get("canonical_opportunity_id", "")
            )
            if canonical:
                # Canonical IDs are deterministic from (symbol, bar_time,
                # pattern); the entity-level index provides the bridge to
                # legacy decision-trace keys without any string parsing.
                bar_time = canonical.split("*")[1] if "*" in canonical else ""
                if bar_time and f"{symbol}_{bar_time}" in dt_by_entity:
                    found = True

            # Extract cycle_id from correlation_id — HISTORICAL FALLBACK ONLY
            if not found and cor_id:
                m = re.match(r"COR-\d{8}-(\d+)-([A-Z0-9]+)-", cor_id)
                if m:
                    cycle_id = int(m.group(1))
                    cor_symbol = m.group(2)
                    if (cor_symbol, cycle_id) in dt_by_sym_cycle:
                        found = True

            # Fallback: entity_id proximity
            if not found and t.get("entry_time"):
                rounded = int(t["entry_time"] // 300) * 300
                entity_key = f"{symbol}_{rounded}"
                if entity_key in dt_by_entity:
                    found = True

            if found:
                matched += 1
            else:
                unmatched_trades.append(t.get("trade_id", "?"))

        coverage = matched / total if total > 0 else 0

        issues = []
        status = GovernanceStatus.PASS

        if coverage < _DECISION_COVERAGE_FAIL:
            issues.append(f"Decision coverage {coverage:.0%} below FAIL threshold ({_DECISION_COVERAGE_FAIL:.0%})")
            status = GovernanceStatus.FAIL
        elif coverage < _DECISION_COVERAGE_WARN:
            issues.append(f"Decision coverage {coverage:.0%} below WARNING threshold ({_DECISION_COVERAGE_WARN:.0%})")
            status = GovernanceStatus.WARNING

        return {
            "trades_total": total,
            "with_decision_trace": matched,
            "without_decision_trace": total - matched,
            "coverage": f"{coverage:.0%}",
            "coverage_pct": round(coverage * 100, 1),
            "decision_traces_loaded": len(execute_decisions),
            "unmatched_trade_ids": unmatched_trades[:10],  # First 10 only
            "issues": issues,
            "status": status,
        }

    def generate_report(self, report: dict[str, Any]) -> tuple[str, str]:
        """Write governance report as JSON + Markdown."""
        self._reports_dir.mkdir(parents=True, exist_ok=True)

        json_path = self._reports_dir / "database_health_report.json"
        json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        md_path = self._reports_dir / "database_health_report.md"
        md_path.write_text(self._build_markdown(report), encoding="utf-8")

        return str(json_path), str(md_path)

    # ═══════════════════════════════════════════════════════════
    # INTERNAL DATA LOADING
    # ═══════════════════════════════════════════════════════════

    def _load_all(self) -> None:
        """Load all data sources."""
        self._journal_trades = self._load_jsonl_dir(self._journal_dir)
        self._research_trades = self._load_jsonl_file(self._research_file)
        self._excluded_trades = self._load_jsonl_file(self._excluded_file)
        self._recon_data = self._load_json(self._recon_file)
        self._recon_entries = self._recon_data.get("entries", []) if self._recon_data else []

        logger.info(
            f"[DATA_GOVERNANCE] Loaded: journal={len(self._journal_trades)}, "
            f"research={len(self._research_trades)}, excluded={len(self._excluded_trades)}, "
            f"recon_entries={len(self._recon_entries)}"
        )

    def _load_jsonl_dir(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        trades = []
        for f in sorted(path.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return trades

    def _load_jsonl_file(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        trades = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return trades

    def _load_json(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            return None

    def _load_execute_decisions(self) -> list[dict]:
        """Load EXECUTE decisions from decision trace."""
        if not self._dt_dir.exists():
            return []
        decisions = []
        for f in self._dt_dir.rglob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip() or '"EXECUTE"' not in line:
                    continue
                try:
                    d = json.loads(line)
                    if d.get("action") == "EXECUTE":
                        decisions.append(d)
                except json.JSONDecodeError:
                    pass
        return decisions

    def _get_dataset_version(self) -> str:
        """Generate a version string from the research file modification time."""
        if self._research_file.exists():
            mtime = self._research_file.stat().st_mtime
            return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d_%H%M")
        return "unknown"

    # ═══════════════════════════════════════════════════════════
    # REPORT FORMATTING
    # ═══════════════════════════════════════════════════════════

    def _build_markdown(self, report: dict) -> str:
        md = []
        md.append("# V10 Data Governance Report")
        md.append("")
        md.append(f"Generated: {report['timestamp']}")
        md.append(f"Dataset version: {report['dataset_version']}")
        md.append(f"**DATA TRUST: {report['data_trust']}**")
        md.append("")

        # Summary
        s = report.get("summary", {})
        md.append("## Summary")
        md.append("")
        md.append(f"| Source | Count |")
        md.append(f"|---|---|")
        md.append(f"| MT5 matched | {s.get('mt5_matched', 0)} |")
        md.append(f"| Journal trades | {s.get('journal_trades', 0)} |")
        md.append(f"| Research trades | {s.get('research_trades', 0)} |")
        md.append(f"| Excluded trades | {s.get('excluded_trades', 0)} |")
        md.append("")

        # Each check
        for check_name, check in report.get("checks", {}).items():
            status = check.get("status", "?")
            icon = {"PASS": "PASS", "WARNING": "WARN", "FAIL": "FAIL"}.get(status, "?")
            md.append(f"## {check_name.replace('_', ' ').title()} [{icon}]")
            md.append("")

            # Render key metrics
            skip_keys = {"status", "issues"}
            for k, v in check.items():
                if k in skip_keys:
                    continue
                if isinstance(v, dict):
                    md.append(f"**{k}:**")
                    for kk, vv in v.items():
                        md.append(f"  - {kk}: {vv}")
                elif isinstance(v, list) and k != "unmatched_trade_ids":
                    for item in v:
                        md.append(f"  - {item}")
                else:
                    md.append(f"- {k}: {v}")

            if check.get("issues"):
                md.append("")
                md.append("**Issues:**")
                for issue in check["issues"]:
                    md.append(f"- {issue}")
            md.append("")

        md.append("---")
        return "\n".join(md)


# ═══════════════════════════════════════════════════════════════
# GATE FUNCTION
# ═══════════════════════════════════════════════════════════════

def research_data_allowed(
    journal_dir: str | None = None,
    research_file: str | None = None,
    recon_file: str | None = None,
) -> bool:
    """
    Gate function: returns True only when DATA_TRUST == PASS.

    Usage:
        if not research_data_allowed():
            stop_research_run()
    """
    validator = DataGovernanceValidator(
        journal_dir=journal_dir,
        research_file=research_file,
        recon_file=recon_file,
    )
    result = validator.validate()
    return result["data_trust"] == GovernanceStatus.PASS


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    print("=" * 56)
    print("  V10 DATA GOVERNANCE VALIDATION")
    print("=" * 56)

    validator = DataGovernanceValidator()
    result = validator.validate()

    trust = result["data_trust"]
    print(f"\n  DATA TRUST: {trust}")
    print(f"  Dataset version: {result['dataset_version']}")
    print(f"  Timestamp: {result['timestamp']}")

    print(f"\n  Checks:")
    for name, check in result["checks"].items():
        status = check["status"]
        print(f"    {name:<25s}: {status}")
        for issue in check.get("issues", []):
            print(f"      -> {issue}")

    s = result["summary"]
    print(f"\n  Dataset:")
    print(f"    Journal: {s['journal_trades']}")
    print(f"    Research: {s['research_trades']}")
    print(f"    Excluded: {s['excluded_trades']}")
    print(f"    MT5 matched: {s['mt5_matched']}")

    print(f"\n  Reports: reports/research/database_health_report.*")
    print("=" * 56)

    sys.exit(0 if trust != GovernanceStatus.FAIL else 1)
