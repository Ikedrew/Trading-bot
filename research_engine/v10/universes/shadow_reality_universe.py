"""
Shadow Reality Universe Builder — Pairs shadow predictions with realised outcomes.

Joins V10_PRIMARY EXECUTE shadow observations to closed trade journal entries
via correlation_id, producing ShadowRealityComparison records that measure
shadow-vs-reality correspondence.

SOURCE DATA:
    logs/shadow_trades/**/*.jsonl     (shadow predictions)
    logs/trade_journal/**/*.jsonl     (realised outcomes)
    logs/execution_results/**/*.jsonl (optional: execution slippage enrichment)

JOIN KEY:
    correlation_id (format: "COR-{date}-{cycle}-{symbol}-{hash}")

AUTHORITATIVE SHADOW POPULATION:
    schema_version == "shadow_trades_v1"
    AND identity.shadow_type == "V10_PRIMARY"
    AND identity.v10_action == "EXECUTE"
    AND identity.correlation_id starts with "COR-"

SEMANTIC RULE:
    delta_r = shadow_r - realised_gross_r
    This is an observed difference. It does NOT establish causation.

This module is:
    - READ ONLY (never modifies source data)
    - RESEARCH-SIDE (no production imports)
    - DETERMINISTIC (same inputs → same outputs)
    - REPRODUCIBLE (rebuilt from source logs at any time)

This module NEVER:
    - Modifies production trading
    - Calls MT5Execution or broker
    - Changes live configuration
    - Interprets delta_r as causal
    - Calibrates or corrects shadow R
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from research_engine.v10.universes.shadow_reality_models import (
    ComparisonStatus,
    GEOMETRY_RELATIVE_TOLERANCE,
    ShadowRealityComparison,
    ShadowRealityCoverageReport,
)
from research_engine.v10.universes.base import UniverseBuilder
from research_engine.v10.universes.models import Population, Universe

logger = logging.getLogger(__name__)

_SHADOW_DIR = Path("logs/shadow_trades")
_JOURNAL_DIR = Path("logs/trade_journal")
_EXEC_RESULTS_DIR = Path("logs/execution_results")

# Exit reason semantic mapping: shadow → journal equivalents
_EXIT_REASON_MAP = {
    "stop_loss": "stop_loss",
    "take_profit": "take_profit",
    "max_bars_timeout": "time_exit",
}


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class ShadowRealityUniverseBuilder(UniverseBuilder):
    """
    Builds the Shadow↔Reality comparison universe from persisted log data.

    Usage:
        builder = ShadowRealityUniverseBuilder()
        builder.load()
        comparisons = builder.build()
        report = builder.get_coverage_report()
        matched = builder.get_population(ComparisonStatus.MATCHED)
    """

    def __init__(
        self,
        shadow_dir: str | Path | None = None,
        journal_dir: str | Path | None = None,
        exec_results_dir: str | Path | None = None,
    ):
        super().__init__()
        self._shadow_dir = Path(shadow_dir) if shadow_dir else _SHADOW_DIR
        self._journal_dir = Path(journal_dir) if journal_dir else _JOURNAL_DIR
        self._exec_dir = Path(exec_results_dir) if exec_results_dir else _EXEC_RESULTS_DIR

        # Raw loaded data
        self._raw_shadows: list[dict[str, Any]] = []
        self._raw_journal: list[dict[str, Any]] = []
        self._raw_exec_results: list[dict[str, Any]] = []

        # Built results
        self._comparisons: list[ShadowRealityComparison] = []
        self._report = ShadowRealityCoverageReport()
        self._built = False

    @property
    def universe_type(self) -> Universe:
        return Universe.SHADOW_REALITY

    @property
    def comparisons(self) -> list[ShadowRealityComparison]:
        if not self._built:
            self.build()
        return self._comparisons

    # ─── PUBLIC API ───────────────────────────────────────────────────────────

    def load(self) -> int:
        """Load all source data from disk. Returns total raw records loaded."""
        self._raw_shadows = _load_jsonl_dir(self._shadow_dir)
        self._raw_journal = _load_jsonl_dir(self._journal_dir)
        self._raw_exec_results = _load_jsonl_dir(self._exec_dir)
        return len(self._raw_shadows) + len(self._raw_journal) + len(self._raw_exec_results)

    def build(self) -> list[ShadowRealityComparison]:
        """
        Execute the complete join pipeline.

        Returns list of all ShadowRealityComparison records (all statuses).
        """
        if not self._raw_shadows and not self._raw_journal:
            self.load()

        report = ShadowRealityCoverageReport()
        report.total_shadow_records = len(self._raw_shadows)
        report.total_journal_records = len(self._raw_journal)

        # ─── STEP 1: Filter authoritative shadow population ───────────
        authoritative, legacy_count, no_cor_count, malformed, schema_mismatch = (
            _filter_authoritative_shadows(self._raw_shadows)
        )
        report.legacy_shadows = legacy_count
        report.shadows_without_correlation_id = no_cor_count
        report.excluded_malformed = malformed
        report.excluded_schema_mismatch = schema_mismatch

        # Count V10_PRIMARY by action (for reporting)
        v10p_execute = []
        v10p_no_trade = 0
        for s in self._raw_shadows:
            if s.get("schema_version") != "shadow_trades_v1":
                continue
            identity = s.get("identity", {})
            if identity.get("shadow_type") != "V10_PRIMARY":
                continue
            if identity.get("v10_action") == "NO_TRADE":
                v10p_no_trade += 1

        report.authoritative_v10_primary_execute = len(authoritative)
        report.authoritative_v10_primary_no_trade = v10p_no_trade

        # ─── STEP 2: Index journal by correlation_id ──────────────────
        journal_by_cor, journal_duplicates = _index_journal(self._raw_journal)
        report.journal_with_correlation_id = len(journal_by_cor) + journal_duplicates
        report.duplicate_journal_correlation_ids = journal_duplicates

        # ─── STEP 3: Index execution results (optional enrichment) ────
        exec_by_cor = _index_execution_results(self._raw_exec_results)

        # ─── STEP 4: Check for duplicate shadow correlation_ids ───────
        shadow_cor_counts = Counter(
            s.get("identity", {}).get("correlation_id", "") for s in authoritative
        )
        duplicate_shadow_cors = {k for k, v in shadow_cor_counts.items() if v > 1}
        report.duplicate_shadow_correlation_ids = len(duplicate_shadow_cors)

        # ─── STEP 5: Build shadow index (deduplicated) ────────────────
        shadow_by_cor: dict[str, dict] = {}
        for s in authoritative:
            cor = s.get("identity", {}).get("correlation_id", "")
            if cor in duplicate_shadow_cors:
                continue  # Will be classified AMBIGUOUS
            shadow_by_cor[cor] = s

        # ─── STEP 6: Perform join ────────────────────────────────────
        comparisons: list[ShadowRealityComparison] = []

        # Track which journal cors are consumed
        journal_cors_consumed: set[str] = set()

        # Process authoritative shadows
        for s in authoritative:
            cor = s.get("identity", {}).get("correlation_id", "")

            # AMBIGUOUS: duplicate shadow correlation_id
            if cor in duplicate_shadow_cors:
                comparisons.append(ShadowRealityComparison(
                    correlation_id=cor,
                    symbol=s.get("identity", {}).get("symbol", ""),
                    comparison_status=ComparisonStatus.AMBIGUOUS,
                ))
                report.ambiguous += 1
                continue

            # Check journal
            if cor not in journal_by_cor:
                comparisons.append(_build_shadow_only(s))
                report.shadow_only += 1
                continue

            journal_rec = journal_by_cor[cor]
            journal_cors_consumed.add(cor)

            # Check for duplicate journal (already filtered above, but safety)
            exec_rec = exec_by_cor.get(cor)

            # Build comparison
            comparison = _build_comparison(s, journal_rec, exec_rec)
            comparisons.append(comparison)

            # Classify into report
            status = comparison.comparison_status
            if status == ComparisonStatus.MATCHED:
                report.matched += 1
            elif status == ComparisonStatus.IDENTITY_MISMATCH:
                report.identity_mismatch += 1
            elif status == ComparisonStatus.GEOMETRY_DIVERGED:
                report.geometry_diverged += 1
            elif status == ComparisonStatus.GEOMETRY_INVALID:
                report.geometry_invalid += 1

        # Process journal-only (real trades without authoritative shadow)
        for cor, j_rec in journal_by_cor.items():
            if cor not in journal_cors_consumed:
                comparisons.append(ShadowRealityComparison(
                    correlation_id=cor,
                    symbol=j_rec.get("symbol", ""),
                    direction=j_rec.get("direction", ""),
                    real_entry_price=j_rec.get("entry_price", 0.0),
                    real_exit_price=j_rec.get("exit_price", 0.0),
                    real_initial_sl=j_rec.get("initial_sl", 0.0),
                    real_initial_tp=j_rec.get("initial_tp", 0.0),
                    real_exit_reason=j_rec.get("close_reason", ""),
                    real_duration_seconds=j_rec.get("duration_seconds", 0.0),
                    realised_net_pnl=j_rec.get("net_pnl", 0.0),
                    commission=j_rec.get("commission", 0.0),
                    swap=j_rec.get("swap", 0.0),
                    pattern=j_rec.get("pattern_name", ""),
                    trade_horizon=j_rec.get("trade_horizon", ""),
                    comparison_status=ComparisonStatus.REAL_ONLY,
                ))
                report.real_only += 1

        # Compute derived rates
        if report.authoritative_v10_primary_execute > 0:
            report.match_rate = report.matched / report.authoritative_v10_primary_execute
        if report.total_journal_records > 0:
            report.journal_coverage = (
                report.journal_with_correlation_id / report.total_journal_records
            )

        self._comparisons = comparisons
        self._report = report
        self._built = True

        # ─── BASE CONTRACT: populate _records and _metadata ───────────
        self._records = [c.to_dict() for c in comparisons
                         if c.comparison_status == ComparisonStatus.MATCHED]

        source_files = tuple(
            str(p) for p in sorted(self._shadow_dir.rglob("*.jsonl"))[:3]
        ) + tuple(
            str(p) for p in sorted(self._journal_dir.rglob("*.jsonl"))[:3]
        ) if self._shadow_dir.exists() else ()

        self._metadata = self._generate_metadata(
            records=self._records,
            source_files=source_files,
            populations=(
                Population.SR_ALL.value,
                Population.SR_MATCHED.value,
                Population.SR_SHADOW_ONLY.value,
                Population.SR_REAL_ONLY.value,
            ),
            exclusions={
                "legacy_shadows": report.legacy_shadows,
                "shadows_without_correlation_id": report.shadows_without_correlation_id,
                "identity_mismatch": report.identity_mismatch,
                "geometry_diverged": report.geometry_diverged,
                "geometry_invalid": report.geometry_invalid,
                "ambiguous": report.ambiguous,
            },
        )

        logger.info(
            "[SHADOW_REALITY] Built %d comparisons: matched=%d shadow_only=%d "
            "real_only=%d ambiguous=%d identity_mismatch=%d geometry_diverged=%d "
            "geometry_invalid=%d",
            len(comparisons), report.matched, report.shadow_only,
            report.real_only, report.ambiguous, report.identity_mismatch,
            report.geometry_diverged, report.geometry_invalid,
        )

        return comparisons

    def get_population(self, status_or_pop) -> list[ShadowRealityComparison]:
        """Get comparisons filtered by status or Population enum."""
        from research_engine.v10.universes.models import Population as Pop
        # Support both ComparisonStatus strings and Population enum values
        if isinstance(status_or_pop, Pop):
            pop_map = {
                Pop.SR_ALL: None,
                Pop.SR_MATCHED: ComparisonStatus.MATCHED,
                Pop.SR_SHADOW_ONLY: ComparisonStatus.SHADOW_ONLY,
                Pop.SR_REAL_ONLY: ComparisonStatus.REAL_ONLY,
            }
            status_filter = pop_map.get(status_or_pop)
            if status_filter is None and status_or_pop == Pop.SR_ALL:
                return list(self.comparisons)
            if status_filter:
                return [c for c in self.comparisons if c.comparison_status == status_filter]
            return list(self.comparisons)
        # String-based status filter (original API)
        return [c for c in self.comparisons if c.comparison_status == status_or_pop]

    def get_coverage_report(self) -> ShadowRealityCoverageReport:
        """Get the coverage/statistics report."""
        if not self._built:
            self.build()
        return self._report

    def get_statistics(self) -> dict[str, Any]:
        """Summary statistics for matched comparisons."""
        matched = self.get_population(ComparisonStatus.MATCHED)
        if not matched:
            return {"n": 0}

        import statistics
        deltas = [c.delta_r for c in matched]
        return {
            "n": len(matched),
            "mean_delta_r": round(statistics.mean(deltas), 4),
            "median_delta_r": round(statistics.median(deltas), 4),
            "std_delta_r": round(statistics.stdev(deltas), 4) if len(deltas) > 1 else 0.0,
            "min_delta_r": round(min(deltas), 4),
            "max_delta_r": round(max(deltas), 4),
            "shadow_better_count": sum(1 for d in deltas if d > 0),
            "real_better_count": sum(1 for d in deltas if d < 0),
            "exit_reason_match_rate": (
                sum(1 for c in matched if c.exit_reason_match) / len(matched)
            ),
            "geometry_match_rate": (
                sum(1 for c in matched if c.geometry_match) / len(matched)
            ),
            "mean_entry_slippage": round(
                statistics.mean(c.entry_slippage for c in matched), 8
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL: LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def _load_jsonl_dir(directory: Path) -> list[dict[str, Any]]:
    """Load all .jsonl files from a directory tree. Never raises."""
    records: list[dict[str, Any]] = []
    if not directory.exists():
        return records
    for f in sorted(directory.rglob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        pass
        except Exception:
            pass
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL: FILTERING
# ═══════════════════════════════════════════════════════════════════════════════

def _filter_authoritative_shadows(
    raw: list[dict[str, Any]],
) -> tuple[list[dict], int, int, int, int]:
    """
    Filter to the authoritative comparison population.

    Returns:
        (authoritative, legacy_count, no_cor_count, malformed_count, schema_mismatch_count)
    """
    authoritative: list[dict] = []
    legacy_count = 0
    no_cor_count = 0
    malformed_count = 0
    schema_mismatch_count = 0

    for rec in raw:
        # Schema check
        if not isinstance(rec, dict):
            malformed_count += 1
            continue
        if rec.get("schema_version") != "shadow_trades_v1":
            schema_mismatch_count += 1
            continue

        identity = rec.get("identity")
        if not isinstance(identity, dict):
            malformed_count += 1
            continue

        cor = identity.get("correlation_id", "") or ""

        # No correlation_id
        if not cor:
            no_cor_count += 1
            continue

        # Legacy format (V10SHADOW-* prefix)
        if not cor.startswith("COR-"):
            legacy_count += 1
            continue

        # Must be V10_PRIMARY
        if identity.get("shadow_type") != "V10_PRIMARY":
            # Not authoritative for comparison (HORIZON_ALTERNATIVE, legacy empty, etc.)
            continue

        # Must be EXECUTE action
        if identity.get("v10_action") != "EXECUTE":
            continue

        authoritative.append(rec)

    return authoritative, legacy_count, no_cor_count, malformed_count, schema_mismatch_count


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL: INDEXING
# ═══════════════════════════════════════════════════════════════════════════════

def _index_journal(raw: list[dict[str, Any]]) -> tuple[dict[str, dict], int]:
    """
    Index journal records by correlation_id.

    Returns (index, duplicate_count).
    Duplicates are excluded from the index entirely.
    """
    counts: Counter = Counter()
    by_cor: dict[str, dict] = {}

    for rec in raw:
        if not isinstance(rec, dict):
            continue
        cor = rec.get("correlation_id", "") or ""
        if not cor:
            continue
        counts[cor] += 1
        by_cor[cor] = rec

    # Remove duplicates
    duplicates = 0
    for cor, count in counts.items():
        if count > 1:
            by_cor.pop(cor, None)
            duplicates += 1

    return by_cor, duplicates


def _index_execution_results(raw: list[dict[str, Any]]) -> dict[str, dict]:
    """Index execution results by correlation_id (for slippage enrichment)."""
    index: dict[str, dict] = {}
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        cor = rec.get("correlation_id", "") or ""
        if cor and rec.get("result_ok") is True:
            index[cor] = rec
    return index


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL: COMPARISON CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════════

def _build_shadow_only(shadow: dict) -> ShadowRealityComparison:
    """Build a SHADOW_ONLY observation (no journal match)."""
    identity = shadow.get("identity", {})
    snap = shadow.get("decision_snapshot", {})
    outcome = shadow.get("simulated_outcome", {})

    return ShadowRealityComparison(
        correlation_id=identity.get("correlation_id", ""),
        entity_id=identity.get("entity_id", "") or "",
        symbol=identity.get("symbol", ""),
        direction=snap.get("direction", ""),
        shadow_entry_price=snap.get("entry_intent_price", 0.0),
        shadow_exit_price=outcome.get("exit_price", 0.0),
        shadow_sl=snap.get("stop_loss_intent", 0.0),
        shadow_tp=snap.get("take_profit_intent", 0.0),
        shadow_r=outcome.get("pnl_r_multiple", 0.0),
        shadow_exit_reason=outcome.get("exit_reason", ""),
        shadow_bars_held=outcome.get("bars_held", 0),
        shadow_mfe_r=outcome.get("mfe_r", 0.0),
        shadow_mae_r=outcome.get("mae_r", 0.0),
        pattern=snap.get("pattern", ""),
        trade_horizon=snap.get("trade_horizon", ""),
        spread_at_entry=snap.get("spread_at_entry", 0.0),
        timestamp_decision_utc=snap.get("timestamp_decision_utc", 0.0),
        comparison_status=ComparisonStatus.SHADOW_ONLY,
    )


def _build_comparison(
    shadow: dict,
    journal: dict,
    exec_result: dict | None,
) -> ShadowRealityComparison:
    """Build a full comparison from a shadow/journal pair."""
    identity = shadow.get("identity", {})
    snap = shadow.get("decision_snapshot", {})
    outcome = shadow.get("simulated_outcome", {})

    cor = identity.get("correlation_id", "")
    shadow_symbol = identity.get("symbol", "")
    shadow_direction = snap.get("direction", "")

    journal_symbol = journal.get("symbol", "")
    journal_direction = journal.get("direction", "")

    # ─── IDENTITY VALIDATION ─────────────────────────────────────────
    if shadow_symbol != journal_symbol or shadow_direction != journal_direction:
        return ShadowRealityComparison(
            correlation_id=cor,
            symbol=shadow_symbol,
            direction=shadow_direction,
            comparison_status=ComparisonStatus.IDENTITY_MISMATCH,
        )

    # ─── EXTRACT FIELDS ──────────────────────────────────────────────
    shadow_entry = snap.get("entry_intent_price", 0.0)
    shadow_exit = outcome.get("exit_price", 0.0)
    shadow_sl = snap.get("stop_loss_intent", 0.0)
    shadow_tp = snap.get("take_profit_intent", 0.0)
    shadow_r = outcome.get("pnl_r_multiple", 0.0)

    real_entry = journal.get("entry_price", 0.0)
    real_exit = journal.get("exit_price", 0.0)
    real_sl = journal.get("initial_sl", 0.0)
    real_tp = journal.get("initial_tp", 0.0)

    # ─── GEOMETRY VALIDATION ─────────────────────────────────────────
    risk_distance = abs(real_entry - real_sl)
    if risk_distance <= 0:
        return ShadowRealityComparison(
            correlation_id=cor,
            entity_id=identity.get("entity_id", "") or "",
            symbol=shadow_symbol,
            direction=shadow_direction,
            shadow_entry_price=shadow_entry,
            shadow_sl=shadow_sl,
            real_entry_price=real_entry,
            real_initial_sl=real_sl,
            comparison_status=ComparisonStatus.GEOMETRY_INVALID,
        )

    # ─── COMPUTE REALISED GROSS R ────────────────────────────────────
    if journal_direction == "BUY":
        realised_gross_r = (real_exit - real_entry) / risk_distance
    else:
        realised_gross_r = (real_entry - real_exit) / risk_distance

    # ─── COMPUTE DELTA R ─────────────────────────────────────────────
    delta_r = shadow_r - realised_gross_r

    # ─── ENTRY SLIPPAGE ──────────────────────────────────────────────
    entry_slippage = real_entry - shadow_entry

    # ─── EXECUTION SLIPPAGE (optional enrichment) ────────────────────
    execution_slippage = None
    if exec_result is not None:
        exec_slip = exec_result.get("slippage")
        if exec_slip is not None:
            execution_slippage = float(exec_slip)

    # ─── EXIT REASON MAPPING ─────────────────────────────────────────
    shadow_exit_reason = outcome.get("exit_reason", "")
    real_exit_reason = journal.get("close_reason", "")
    expected_real_reason = _EXIT_REASON_MAP.get(shadow_exit_reason, "")
    exit_reason_match = (expected_real_reason == real_exit_reason)

    # ─── GEOMETRY MATCH ──────────────────────────────────────────────
    tolerance = max(abs(shadow_entry) * GEOMETRY_RELATIVE_TOLERANCE, 1e-8)
    sl_match = abs(shadow_sl - real_sl) <= tolerance
    tp_match = abs(shadow_tp - real_tp) <= tolerance
    geometry_match = sl_match and tp_match

    # ─── DETERMINE STATUS ────────────────────────────────────────────
    if not geometry_match:
        status = ComparisonStatus.GEOMETRY_DIVERGED
    else:
        status = ComparisonStatus.MATCHED

    # ─── BUILD COMPARISON ────────────────────────────────────────────
    return ShadowRealityComparison(
        # Identity
        correlation_id=cor,
        entity_id=identity.get("entity_id", "") or "",
        symbol=shadow_symbol,
        direction=shadow_direction,
        # Shadow
        shadow_entry_price=shadow_entry,
        shadow_exit_price=shadow_exit,
        shadow_sl=shadow_sl,
        shadow_tp=shadow_tp,
        shadow_r=round(shadow_r, 6),
        shadow_exit_reason=shadow_exit_reason,
        shadow_bars_held=outcome.get("bars_held", 0),
        shadow_mfe_r=outcome.get("mfe_r", 0.0),
        shadow_mae_r=outcome.get("mae_r", 0.0),
        # Real
        real_entry_price=real_entry,
        real_exit_price=real_exit,
        real_initial_sl=real_sl,
        real_initial_tp=real_tp,
        realised_gross_r=round(realised_gross_r, 6),
        realised_net_pnl=journal.get("net_pnl", 0.0),
        real_exit_reason=real_exit_reason,
        real_duration_seconds=journal.get("duration_seconds", 0.0),
        real_max_favourable_price=journal.get("max_favourable_price", 0.0),
        commission=journal.get("commission", 0.0),
        swap=journal.get("swap", 0.0),
        # Comparison
        delta_r=round(delta_r, 6),
        entry_slippage=round(entry_slippage, 8),
        execution_slippage=execution_slippage,
        exit_reason_match=exit_reason_match,
        geometry_match=geometry_match,
        # Context
        pattern=snap.get("pattern", ""),
        trade_horizon=snap.get("trade_horizon", "") or journal.get("trade_horizon", ""),
        spread_at_entry=snap.get("spread_at_entry", 0.0),
        timestamp_decision_utc=snap.get("timestamp_decision_utc", 0.0),
        # Status
        comparison_status=status,
    )
