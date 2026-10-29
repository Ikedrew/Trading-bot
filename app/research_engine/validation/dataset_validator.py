"""
Research Dataset Validator — Pre-experiment data quality check.

Inspects a dataset (list of record dicts) and produces a ResearchValidationResult
describing coverage, suitability, and warnings.

Usage:
    from research_engine.validation import validate_dataset

    records = load_shadow_trades()
    result = validate_dataset(records, dataset_name="shadow_trades_2026_07_26")

    if not result.suitable_for_htf_research:
        print("WARNING:", result.warnings)

Does NOT modify records. Does NOT affect trading logic. Pure inspection.
"""

from __future__ import annotations

from typing import Any

from research_engine.validation.validation_models import (
    CoverageMetric,
    DataSource,
    ResearchValidationResult,
    ValidationThresholds,
)


# ─── DEFAULT THRESHOLDS ──────────────────────────────────────────────────────

_DEFAULT_THRESHOLDS = ValidationThresholds()


# ─── FIELD PRESENCE HELPERS ───────────────────────────────────────────────────

_UNKNOWN_VALUES = frozenset({"UNKNOWN", "unknown", "", None})
_NEUTRAL_VALUES = frozenset({"NEUTRAL", "neutral", "NONE", "none", ""})


def _count_field(
    records: list[dict[str, Any]],
    field_path: str | tuple[str, ...],
    unknown_values: frozenset = _UNKNOWN_VALUES,
) -> CoverageMetric:
    """
    Count populated, total, and unknown occurrences of a field.

    field_path can be:
        - A single key: "regime"
        - A nested path: ("simulation_environment", "htf_snapshot", "timeframe_bias", "H4", "regime")
        - A tuple of alternative keys: checked in order, first match wins

    Values in unknown_values are counted as populated-but-unknown.
    """
    total = len(records)
    populated = 0
    unknown = 0

    for record in records:
        value = _extract_field(record, field_path)
        if value is not None:
            populated += 1
            if value in unknown_values or (isinstance(value, str) and value.strip() == ""):
                unknown += 1

    field_name = field_path if isinstance(field_path, str) else ".".join(field_path) if isinstance(field_path, tuple) else str(field_path)
    return CoverageMetric(
        field_name=field_name,
        populated_count=populated,
        total_count=total,
        unknown_count=unknown,
    )


def _extract_field(record: dict[str, Any], field_path: str | tuple[str, ...]) -> Any:
    """Extract a value from a record by key or nested path."""
    if isinstance(field_path, str):
        return record.get(field_path)

    # Nested path traversal
    current = record
    for key in field_path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


# ─── DATA SOURCE DETECTION ────────────────────────────────────────────────────

def _detect_source(records: list[dict[str, Any]], dataset_name: str) -> DataSource:
    """
    Detect dataset origin from record structure and naming.

    Heuristics:
        - "shadow" in name or schema_version contains "shadow" → SHADOW
        - "trade_truth" in name or schema contains "trade_truth" → TRADE_TRUTH
        - "test" in name or timestamps are synthetic (< 2020) → TEST
        - Records have execution fill data → LIVE
        - Otherwise → REPLAY (most common for replay-mode output)
    """
    name_lower = dataset_name.lower()

    if "test" in name_lower:
        return DataSource.TEST

    if "trade_truth" in name_lower:
        return DataSource.TRADE_TRUTH

    if "shadow" in name_lower:
        return DataSource.SHADOW

    # Check schema_version in records
    if records:
        sample = records[0]
        schema = str(sample.get("schema_version", ""))
        if "shadow" in schema:
            return DataSource.SHADOW
        if "trade_truth" in schema:
            return DataSource.TRADE_TRUTH

    # Check for synthetic timestamps (test data often uses ts < 2020)
    synthetic_count = 0
    check_count = min(10, len(records))
    for r in records[:check_count]:
        ts = r.get("timestamp_utc") or r.get("entry_time") or r.get("bar_time", 0)
        if isinstance(ts, (int, float)) and 0 < ts < 1577836800:  # < 2020-01-01
            synthetic_count += 1
    if check_count > 0 and synthetic_count / check_count > 0.5:
        return DataSource.TEST

    # Check for live execution evidence
    if records:
        sample = records[0]
        if sample.get("execution") and sample["execution"].get("slippage_entry") is not None:
            return DataSource.LIVE

    # Default: replay (most common scenario)
    return DataSource.REPLAY


# ─── H4 REGIME COVERAGE ──────────────────────────────────────────────────────

def _check_h4_regime(records: list[dict[str, Any]]) -> CoverageMetric:
    """
    Check H4 regime coverage across multiple record formats.

    Supports:
        - decision_trace format: record["regime"] with record["regime_source"]
        - shadow_trade format: record["simulation_environment"]["htf_snapshot"]["timeframe_bias"]["H4"]["regime"]
        - opportunity_assessment format: record["regime"]
    """
    total = len(records)
    populated = 0
    unknown = 0

    for r in records:
        # Try direct regime field
        regime = r.get("regime") or r.get("activation_regime")

        # Try nested shadow trade format
        if regime is None:
            env = r.get("simulation_environment") or {}
            htf = env.get("htf_snapshot") or {}
            tf_bias = htf.get("timeframe_bias") or {}
            h4 = tf_bias.get("H4") or {}
            regime = h4.get("regime")

        if regime is not None:
            populated += 1
            if regime in ("UNKNOWN", "unknown", "", "TRANSITIONAL"):
                # TRANSITIONAL from M5 fallback often means "regime unknown at H4 level"
                # Check if regime_source indicates M5 fallback
                source = r.get("regime_source", "")
                if regime == "UNKNOWN" or (regime == "TRANSITIONAL" and source == "M5_CLASSIFIER"):
                    unknown += 1

    return CoverageMetric(
        field_name="h4_regime",
        populated_count=populated,
        total_count=total,
        unknown_count=unknown,
    )


# ─── H1 BIAS COVERAGE ────────────────────────────────────────────────────────

def _check_h1_bias(records: list[dict[str, Any]]) -> CoverageMetric:
    """Check H1 directional bias coverage."""
    total = len(records)
    populated = 0
    unknown = 0

    for r in records:
        # Direct field
        bias = r.get("h1_bias") or r.get("bias")

        # Shadow trade nested
        if bias is None:
            env = r.get("simulation_environment") or {}
            htf = env.get("htf_snapshot") or {}
            tf_bias = htf.get("timeframe_bias") or {}
            h1 = tf_bias.get("H1") or {}
            bias = h1.get("bias")

        if bias is not None:
            populated += 1
            if bias in ("NEUTRAL", "neutral", "UNKNOWN", "unknown", ""):
                unknown += 1

    return CoverageMetric(
        field_name="h1_bias",
        populated_count=populated,
        total_count=total,
        unknown_count=unknown,
    )


# ─── MARKET PHASE COVERAGE ───────────────────────────────────────────────────

def _check_market_phase(records: list[dict[str, Any]]) -> CoverageMetric:
    """Check market_phase coverage."""
    total = len(records)
    populated = 0
    unknown = 0

    for r in records:
        phase = r.get("market_phase")

        # Shadow trade decision_snapshot
        if phase is None:
            ds = r.get("decision_snapshot") or {}
            phase = ds.get("market_phase")

        if phase is not None and phase != "":
            populated += 1
            if phase in ("UNKNOWN", "unknown"):
                unknown += 1

    return CoverageMetric(
        field_name="market_phase",
        populated_count=populated,
        total_count=total,
        unknown_count=unknown,
    )


# ─── PATTERN COVERAGE ────────────────────────────────────────────────────────

def _check_pattern(records: list[dict[str, Any]]) -> CoverageMetric:
    """Check pattern name coverage."""
    total = len(records)
    populated = 0
    unknown = 0

    for r in records:
        pattern = r.get("pattern") or r.get("pattern_name")

        # Shadow trade
        if pattern is None:
            ds = r.get("decision_snapshot") or {}
            pattern = ds.get("pattern")

        if pattern is not None and pattern != "":
            populated += 1
            if pattern in ("UNKNOWN", "unknown"):
                unknown += 1

    return CoverageMetric(
        field_name="pattern",
        populated_count=populated,
        total_count=total,
        unknown_count=unknown,
    )


# ─── OUTCOME COVERAGE ────────────────────────────────────────────────────────

def _check_outcome(records: list[dict[str, Any]]) -> CoverageMetric:
    """Check whether records have trade outcome data (R-multiple)."""
    total = len(records)
    populated = 0

    for r in records:
        # Shadow trade
        outcome = r.get("simulated_outcome") or {}
        r_mult = outcome.get("pnl_r_multiple")

        # Trade truth
        if r_mult is None:
            outcome2 = r.get("outcome") or {}
            r_mult = outcome2.get("r_multiple_realised")

        # Direct field
        if r_mult is None:
            r_mult = r.get("r") or r.get("r_multiple")

        if r_mult is not None:
            populated += 1

    return CoverageMetric(
        field_name="outcome_r_multiple",
        populated_count=populated,
        total_count=total,
        unknown_count=0,
    )


# ─── LINEAGE COVERAGE ─────────────────────────────────────────────────────────

def _check_lineage(records: list[dict[str, Any]]) -> CoverageMetric:
    """
    Check decision lineage coverage (entity_id or correlation_id).

    A record has lineage if it contains a non-empty entity_id OR a valid
    correlation_id that can link back to a DecisionTrace.
    """
    total = len(records)
    populated = 0
    unknown = 0

    for r in records:
        # Check entity_id (primary join key)
        entity_id = r.get("entity_id")

        # Shadow trade nested identity
        if entity_id is None:
            ident = r.get("identity") or {}
            entity_id = ident.get("entity_id")

        # Check correlation_id as fallback
        correlation_id = r.get("correlation_id")
        if correlation_id is None:
            ident = r.get("identity") or {}
            correlation_id = ident.get("correlation_id")

        has_entity = entity_id is not None and entity_id != ""
        has_correlation = (
            correlation_id is not None
            and correlation_id != ""
            and not correlation_id.startswith("HORIZON-")  # HORIZON- prefix is not a valid spine ID
        )

        if has_entity or has_correlation:
            populated += 1
        elif correlation_id and correlation_id.startswith("HORIZON-"):
            # Has a correlation_id but it's the non-joinable HORIZON format
            populated += 1
            unknown += 1  # Count as "populated but not joinable"

    return CoverageMetric(
        field_name="decision_lineage",
        populated_count=populated,
        total_count=total,
        unknown_count=unknown,
    )


# ─── STRATEGY COVERAGE ────────────────────────────────────────────────────────

_VALID_STRATEGIES = frozenset({"REVERSAL", "CONTINUATION", "FALSE_BREAK"})
_COMBINED_PATTERN = frozenset({"_SCALP", "_INTRADAY", "_EXTENDED"})


def _check_strategy(records: list[dict[str, Any]]) -> tuple[CoverageMetric, int]:
    """
    Check strategy identity coverage and detect combined strategy_horizon contamination.

    Returns (CoverageMetric, contaminated_count).
    contaminated_count = records where strategy contains horizon suffix (e.g. 'NONE_SCALP').
    """
    total = len(records)
    populated = 0
    unknown = 0
    contaminated = 0

    for r in records:
        # Direct field
        strategy = r.get("strategy") or r.get("selected_strategy")

        # Shadow trade identity
        if strategy is None:
            ident = r.get("identity") or {}
            strategy = ident.get("strategy_id")

        # Shadow trade decision_snapshot
        if strategy is None:
            ds = r.get("decision_snapshot") or {}
            strategy = ds.get("strategy")

        if strategy is not None and strategy != "":
            populated += 1
            # Detect contamination (combined strategy_horizon)
            if any(suffix in strategy for suffix in _COMBINED_PATTERN):
                contaminated += 1
                unknown += 1  # Treat contaminated as "not cleanly usable"
            elif strategy not in _VALID_STRATEGIES and strategy not in ("NONE", "None", ""):
                unknown += 1  # Unknown strategy value

    return CoverageMetric(
        field_name="strategy",
        populated_count=populated,
        total_count=total,
        unknown_count=unknown,
    ), contaminated


# ─── HORIZON COVERAGE ─────────────────────────────────────────────────────────

_VALID_HORIZONS = frozenset({"SCALP", "INTRADAY", "EXTENDED"})


def _check_horizon(records: list[dict[str, Any]]) -> CoverageMetric:
    """Check trade_horizon coverage."""
    total = len(records)
    populated = 0
    unknown = 0

    for r in records:
        horizon = r.get("trade_horizon")

        # Shadow trade decision_snapshot
        if horizon is None:
            ds = r.get("decision_snapshot") or {}
            horizon = ds.get("trade_horizon")

        if horizon is not None and horizon != "":
            populated += 1
            if horizon not in _VALID_HORIZONS:
                unknown += 1

    return CoverageMetric(
        field_name="trade_horizon",
        populated_count=populated,
        total_count=total,
        unknown_count=unknown,
    )


# ─── REQUIRED FIELD VALIDATION ────────────────────────────────────────────────

def _check_required_fields(
    records: list[dict[str, Any]],
    required_fields: list[str] | None = None,
) -> list[str]:
    """
    Check which required fields are missing from ALL records.

    Returns list of field names that are absent from every record.
    A field is considered "present" if at least one record has it.
    """
    if not required_fields or not records:
        return []

    missing = []
    for field_name in required_fields:
        found = False
        for r in records:
            if r.get(field_name) is not None:
                found = True
                break
            # Check nested in decision_snapshot (shadow trades)
            ds = r.get("decision_snapshot") or {}
            if ds.get(field_name) is not None:
                found = True
                break
        if not found:
            missing.append(field_name)
    return missing


# ─── MAIN VALIDATOR ───────────────────────────────────────────────────────────

def validate_dataset(
    records: list[dict[str, Any]],
    *,
    dataset_name: str = "unnamed",
    thresholds: ValidationThresholds | None = None,
    required_fields: list[str] | None = None,
) -> ResearchValidationResult:
    """
    Validate a research dataset for suitability before experiment execution.

    Args:
        records: List of record dicts (shadow trades, decision traces, etc.)
        dataset_name: Human-readable identifier for the dataset
        thresholds: Optional custom thresholds (defaults to 80% coverage)
        required_fields: Optional list of fields the experiment requires

    Returns:
        ResearchValidationResult with coverage metrics, suitability flags, and warnings.

    Never raises. Returns a result even for empty datasets.
    """
    t = thresholds or _DEFAULT_THRESHOLDS
    warnings: list[str] = []

    # ─── EMPTY DATASET ────────────────────────────────────────────────
    if not records:
        return ResearchValidationResult(
            dataset_name=dataset_name,
            source=DataSource.UNKNOWN,
            total_records=0,
            h4_regime_coverage=CoverageMetric("h4_regime", 0, 0),
            h1_bias_coverage=CoverageMetric("h1_bias", 0, 0),
            market_phase_coverage=CoverageMetric("market_phase", 0, 0),
            pattern_coverage=CoverageMetric("pattern", 0, 0),
            outcome_coverage=CoverageMetric("outcome_r_multiple", 0, 0),
            lineage_coverage=CoverageMetric("decision_lineage", 0, 0),
            strategy_coverage=CoverageMetric("strategy", 0, 0),
            horizon_coverage=CoverageMetric("trade_horizon", 0, 0),
            suitable_for_htf_research=False,
            suitable_for_phase_research=False,
            suitable_for_pattern_research=False,
            suitable_for_execution_research=False,
            warnings=("Dataset is empty",),
            validation_passed=False,
        )

    # ─── SOURCE DETECTION ─────────────────────────────────────────────
    source = _detect_source(records, dataset_name)

    # ─── COVERAGE CHECKS ──────────────────────────────────────────────
    h4_regime = _check_h4_regime(records)
    h1_bias = _check_h1_bias(records)
    market_phase = _check_market_phase(records)
    pattern = _check_pattern(records)
    outcome = _check_outcome(records)
    lineage = _check_lineage(records)
    strategy, strategy_contaminated = _check_strategy(records)
    horizon = _check_horizon(records)

    # ─── SUITABILITY DETERMINATION ────────────────────────────────────
    n = len(records)

    # HTF research: needs H4 regime + H1 bias + sufficient sample
    suitable_htf = (
        h4_regime.coverage_pct >= t.htf_regime_min_coverage
        and h1_bias.coverage_pct >= t.h1_bias_min_coverage
        and n >= t.min_sample_size
    )

    # Phase research: needs market_phase + sufficient sample
    suitable_phase = (
        market_phase.coverage_pct >= t.market_phase_min_coverage
        and n >= t.min_sample_size
    )

    # Pattern research: needs pattern + outcome + sufficient sample
    suitable_pattern = (
        pattern.coverage_pct >= 0.5  # At least 50% of records have patterns
        and outcome.coverage_pct >= 0.5
        and n >= t.min_sample_size
    )

    # Execution research: needs outcome data from live/trade_truth source
    suitable_execution = (
        source in (DataSource.LIVE, DataSource.TRADE_TRUTH)
        and outcome.coverage_pct >= 0.8
        and n >= t.min_sample_size
    )

    # ─── WARNINGS ─────────────────────────────────────────────────────
    if n < t.min_sample_size:
        warnings.append(f"Sample size ({n}) below minimum ({t.min_sample_size})")

    if h4_regime.coverage_pct < t.htf_regime_min_coverage:
        pct = round(h4_regime.coverage_pct * 100, 1)
        warnings.append(f"H4 regime coverage {pct}% below {t.htf_regime_min_coverage*100:.0f}% threshold — HTF research unreliable")

    if h1_bias.coverage_pct < t.h1_bias_min_coverage:
        pct = round(h1_bias.coverage_pct * 100, 1)
        warnings.append(f"H1 bias coverage {pct}% below {t.h1_bias_min_coverage*100:.0f}% threshold — directional analysis unreliable")

    if market_phase.coverage_pct < t.market_phase_min_coverage:
        pct = round(market_phase.coverage_pct * 100, 1)
        warnings.append(f"Market phase coverage {pct}% below {t.market_phase_min_coverage*100:.0f}% threshold — phase research unavailable")

    if source == DataSource.REPLAY:
        warnings.append("Data source is REPLAY — MarketContext may not be fully populated")

    if source == DataSource.TEST:
        warnings.append("Data source is TEST — results are not representative of live conditions")

    if lineage.coverage_pct < 0.5:
        pct = round(lineage.coverage_pct * 100, 1)
        warnings.append(f"Decision lineage coverage {pct}% — research requiring decision context may be invalid")

    if strategy_contaminated > 0:
        pct = round(strategy_contaminated / n * 100, 1)
        warnings.append(f"Strategy/horizon contamination detected: {strategy_contaminated} records ({pct}%) have combined strategy_horizon format")

    if strategy.coverage_pct < 0.5:
        pct = round(strategy.coverage_pct * 100, 1)
        warnings.append(f"Clean strategy coverage {pct}% — strategy-based research may be unreliable")

    if horizon.coverage_pct < 0.5:
        pct = round(horizon.coverage_pct * 100, 1)
        warnings.append(f"Horizon coverage {pct}% — horizon-specific research unavailable")

    # ─── REQUIRED FIELDS CHECK ────────────────────────────────────────
    missing = _check_required_fields(records, required_fields)
    if missing:
        warnings.append(f"Required fields missing: {', '.join(missing)}")

    # ─── VALIDATION PASSED ────────────────────────────────────────────
    # Passes if minimum sample size is met and no required fields are missing
    validation_passed = n >= t.min_sample_size and len(missing) == 0

    return ResearchValidationResult(
        dataset_name=dataset_name,
        source=source,
        total_records=n,
        h4_regime_coverage=h4_regime,
        h1_bias_coverage=h1_bias,
        market_phase_coverage=market_phase,
        pattern_coverage=pattern,
        outcome_coverage=outcome,
        lineage_coverage=lineage,
        strategy_coverage=strategy,
        horizon_coverage=horizon,
        strategy_contaminated=strategy_contaminated,
        suitable_for_htf_research=suitable_htf,
        suitable_for_phase_research=suitable_phase,
        suitable_for_pattern_research=suitable_pattern,
        suitable_for_execution_research=suitable_execution,
        warnings=tuple(warnings),
        missing_fields=tuple(missing),
        validation_passed=validation_passed,
    )
