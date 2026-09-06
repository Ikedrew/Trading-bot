"""
V10 Decision Trace Enrichment Layer.

Joins completed research trades with their decision lifecycle events.
Does NOT modify trade records — creates a new enriched output file.

Matching priority:
    1. correlation_id (exact match between trade and decision trace/execution)
    2. entity_id (symbol_timestamp pattern)
    3. symbol + cycle_id (extracted from correlation_id)
    4. symbol + timestamp proximity (fallback)

Output:
    logs/research_ready_trade_dataset/research_ready_trades_enriched.jsonl
    reports/research/decision_enrichment_report.md
    reports/research/decision_enrichment_report.json

Usage:
    from research_engine.v10.decision_enrichment import enrich_trades
    result = enrich_trades()

CLI:
    python -m research_engine.v10.decision_enrichment
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RESEARCH_READY = "logs/research_ready_trade_dataset/research_ready_trades.jsonl"
_DECISION_TRACE_DIR = "logs/decision_trace"
_EXECUTION_RESULTS_DIR = "logs/execution_results"
_OUTPUT_FILE = "logs/research_ready_trade_dataset/research_ready_trades_enriched.jsonl"
_REPORTS_DIR = "reports/research"

# Fields to extract from decision trace EXECUTE records
_DT_FIELDS = {
    "selected_strategy": "dt_strategy",
    "strategy_confidence": "dt_strategy_confidence",
    "score_neutral": "dt_score_neutral",
    "score_strategy": "dt_score_strategy",
    "score_delta": "dt_score_delta",
    "regime": "dt_regime",
    "regime_confidence": "dt_regime_confidence",
    "regime_source": "dt_regime_source",
    "market_state": "dt_market_state",
    "market_phase": "dt_market_phase",
    "htf_alignment": "dt_htf_alignment",
    "h4_alignment": "dt_h4_alignment",
    "trend_alignment_source": "dt_trend_source",
    "trend_alignment_confidence": "dt_trend_confidence",
    "confirmation_score": "dt_confirmation_score",
    "ev": "dt_ev",
    "p_success": "dt_p_success",
    "rr_effective": "dt_rr_effective",
    "trade_horizon": "dt_trade_horizon",
    "pattern_name": "dt_pattern",
    "weakest_component": "dt_weakest_component",
    "weakest_value": "dt_weakest_value",
    "engine_version": "dt_engine_version",
}

# Fields from V10 nested structures
_V10_FIELDS = {
    "v10_market_state.regime.regime": "dt_v10_regime",
    "v10_market_state.regime.regime_confidence": "dt_v10_regime_confidence",
    "v10_market_state.regime.volatility_state": "dt_v10_volatility",
    "v10_market_state.h1.dominant_trend": "dt_h1_direction",
    "v10_market_state.h1.structural_clarity": "dt_h1_clarity",
    "v10_market_state.h4.trend": "dt_h4_trend",
    "v10_market_state.h4.market_phase": "dt_h4_phase",
    "v10_opportunity.state": "dt_opportunity_state",
    "v10_opportunity.directional_bias": "dt_directional_bias",
    "v10_opportunity.opportunity_type": "dt_opportunity_type",
    "v10_opportunity.overall_quality": "dt_opportunity_quality",
    "v10_opportunity.location_score": "dt_location_score",
    "v10_opportunity.structure_score": "dt_structure_score",
    "v10_opportunity.behaviour_score": "dt_behaviour_score",
    "v10_opportunity.formation_score": "dt_formation_score",
    "v10_strategy.family": "dt_v10_strategy_family",
    "v10_strategy.confidence": "dt_v10_strategy_confidence",
    "v10_strategy.direction": "dt_v10_strategy_direction",
    "v10_horizon.type": "dt_horizon_type",
    "v10_horizon.duration_minutes": "dt_horizon_duration",
    "v10_entry.method": "dt_entry_method",
    "v10_entry.status": "dt_entry_status",
    "v10_risk.approved": "dt_risk_approved",
    "v10_risk.risk_percentage": "dt_risk_pct",
    "v10_execution.approved": "dt_execution_approved",
    "v10_execution.order_type": "dt_order_type",
}


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

def enrich_trades(
    source_file: str | None = None,
    output_file: str | None = None,
    reports_dir: str | None = None,
    decision_trace_dir: str | None = None,
    execution_results_dir: str | None = None,
) -> dict[str, Any]:
    """
    Enrich research-ready trades with decision trace context.

    Does NOT modify the source file. Creates a separate enriched output.

    Production evidence (decision_trace, execution_results) is read from the
    canonical S3 datasets via the shared research data-access layer.
    ``decision_trace_dir`` / ``execution_results_dir`` are explicit OFFLINE
    FIXTURE overrides (test/local replay) and are never a production fallback.

    Returns:
        Enrichment summary dict.
    """
    src = Path(source_file or _RESEARCH_READY)
    out = Path(output_file or _OUTPUT_FILE)
    rep = Path(reports_dir or _REPORTS_DIR)

    # Load trades
    trades = _load_jsonl(src)
    if not trades:
        return {"error": "No trades loaded"}

    logger.info(f"[ENRICHMENT] Loaded {len(trades)} trades from {src}")

    # Load decision traces (EXECUTE only)
    dt_execute = _load_decision_traces(dt_dir=decision_trace_dir)
    logger.info(f"[ENRICHMENT] Loaded {len(dt_execute)} EXECUTE decision traces")

    # Load execution results
    exec_results = _load_execution_results(exec_dir=execution_results_dir)
    logger.info(f"[ENRICHMENT] Loaded {len(exec_results)} execution results")

    # Build indices
    dt_by_cor = {}       # correlation_id -> decision
    dt_by_entity = {}    # entity_id -> decision
    dt_by_sym_cycle = {} # (symbol, cycle_id) -> decision
    dt_by_sym_time = {}  # (symbol, rounded_timestamp) -> decision

    for d in dt_execute:
        cor = d.get("correlation_id", "")
        if cor:
            dt_by_cor[cor] = d
        eid = d.get("entity_id", "")
        if eid:
            dt_by_entity[eid] = d
        sym = d.get("symbol", "")
        cycle = d.get("cycle_id", 0)
        if sym and cycle:
            dt_by_sym_cycle[(sym, cycle)] = d
        ts = d.get("timestamp_utc", "")
        if sym and ts:
            # Round to minute for proximity matching
            try:
                dt_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                dt_by_sym_time[(sym, int(dt_ts // 300) * 300)] = d
            except (ValueError, TypeError):
                pass

    # Build execution result index
    exec_by_cor = {}
    for e in exec_results:
        cor = e.get("correlation_id", "")
        if cor and e.get("result_ok"):
            exec_by_cor[cor] = e

    # ─── MATCH AND ENRICH ─────────────────────────────────────
    matched = 0
    unmatched = 0
    match_methods = {"correlation_id": 0, "v10_correlation": 0, "entity_id": 0,
                     "sym_cycle": 0, "sym_time": 0, "unmatched": 0}
    missing_fields_count: dict[str, int] = {}

    for trade in trades:
        cor_id = trade.get("correlation_id", "")
        symbol = trade.get("symbol", "")
        entry_time = trade.get("entry_time", 0)
        decision = None
        method = "unmatched"

        # Method 0 (remediation Stage 8): explicit canonical lineage field —
        # no string reconstruction for current-epoch records.
        canonical = (
            (trade.get("identity") or {}).get("canonical_opportunity_id")
            or trade.get("canonical_opportunity_id", "")
        )
        if canonical and canonical in dt_by_cor:
            decision = dt_by_cor[canonical]
            method = "canonical_opportunity_id"

        # Method 1: Direct correlation_id match on decision trace
        if cor_id and cor_id in dt_by_cor:
            decision = dt_by_cor[cor_id]
            method = "correlation_id"

        # Method 2: V10 correlation format (v10_{symbol}_{entity_ts}_{cycle})
        if not decision and cor_id:
            # Try to build v10 correlation from COR format
            # COR-20260804-10400-AUDUSD-E5A2 -> cycle_id=10400, symbol=AUDUSD
            m = re.match(r"COR-(\d{8})-(\d+)-([A-Z0-9]+)-", cor_id)
            if m:
                cycle_id = int(m.group(2))
                cor_symbol = m.group(3)
                # Try sym+cycle
                key = (cor_symbol, cycle_id)
                if key in dt_by_sym_cycle:
                    decision = dt_by_sym_cycle[key]
                    method = "sym_cycle"
                else:
                    # Try v10 format correlation
                    # v10_AUDUSD_1785846600_10400
                    for v10_cor, d in dt_by_cor.items():
                        if v10_cor.startswith(f"v10_{cor_symbol}_") and v10_cor.endswith(f"_{cycle_id}"):
                            decision = d
                            method = "v10_correlation"
                            break

        # Method 3: entity_id match
        if not decision and symbol and entry_time:
            # Build entity_id pattern: SYMBOL_TIMESTAMP (rounded to 5min)
            rounded_ts = int(entry_time // 300) * 300
            entity_key = f"{symbol}_{rounded_ts}"
            if entity_key in dt_by_entity:
                decision = dt_by_entity[entity_key]
                method = "entity_id"

        # Method 4: Symbol + timestamp proximity
        if not decision and symbol and entry_time:
            rounded_ts = int(entry_time // 300) * 300
            for offset in [0, -300, 300, -600, 600]:
                key = (symbol, rounded_ts + offset)
                if key in dt_by_sym_time:
                    decision = dt_by_sym_time[key]
                    method = "sym_time"
                    break

        # Apply enrichment
        if decision:
            matched += 1
            match_methods[method] += 1
            _apply_enrichment(trade, decision)
        else:
            unmatched += 1
            match_methods["unmatched"] += 1
            # Set empty enrichment fields
            for target_field in _DT_FIELDS.values():
                trade[target_field] = None
            for target_field in _V10_FIELDS.values():
                trade[target_field] = None

        trade["dt_match_method"] = method
        trade["dt_matched"] = decision is not None

        # Track missing enrichment fields
        for target_field in list(_DT_FIELDS.values()) + list(_V10_FIELDS.values()):
            if trade.get(target_field) is None:
                missing_fields_count[target_field] = missing_fields_count.get(target_field, 0) + 1

    # ─── WRITE OUTPUT ─────────────────────────────────────────
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(t, default=str) for t in trades]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info(f"[ENRICHMENT] Complete: {matched} matched, {unmatched} unmatched")

    # ─── BUILD REPORT ─────────────────────────────────────────
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_file": str(src),
        "output_file": str(out),
        "total_trades": len(trades),
        "matched": matched,
        "unmatched": unmatched,
        "match_rate": round(matched / max(len(trades), 1), 4),
        "match_methods": match_methods,
        "decision_traces_loaded": len(dt_execute),
        "execution_results_loaded": len(exec_results),
        "missing_fields": {
            k: v for k, v in sorted(missing_fields_count.items(), key=lambda x: -x[1])
            if v > 0
        },
        "unmatched_trades": [
            {
                "trade_id": t.get("trade_id"),
                "symbol": t.get("symbol"),
                "correlation_id": t.get("correlation_id"),
                "entry_time": t.get("entry_time"),
            }
            for t in trades if not t.get("dt_matched")
        ],
    }

    rep.mkdir(parents=True, exist_ok=True)
    (rep / "decision_enrichment_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (rep / "decision_enrichment_report.md").write_text(
        _build_markdown(report), encoding="utf-8"
    )

    return report


# ═══════════════════════════════════════════════════════════════
# INTERNAL
# ═══════════════════════════════════════════════════════════════

def _load_jsonl(path: Path) -> list[dict]:
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


def _load_decision_traces(dt_dir: str | Path | None = None) -> list[dict]:
    """Load all EXECUTE decisions from decision trace.

    Authoritative source: S3 dataset ``decision_trace`` via the shared research
    data-access layer. ``dt_dir`` is an explicit OFFLINE FIXTURE override
    (test/local replay) — never a production fallback.
    """
    if dt_dir is not None:
        base = Path(dt_dir)
        if not base.exists():
            return []
        execute_decisions = []
        for f in base.rglob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                if '"EXECUTE"' not in line:
                    continue  # Fast filter before JSON parse
                try:
                    d = json.loads(line)
                    if d.get("action") == "EXECUTE":
                        execute_decisions.append(d)
                except json.JSONDecodeError:
                    pass
        return execute_decisions

    from research_engine.data_access.s3_source import get_default_source

    return [
        d for d in get_default_source().read_dataset("decision_trace")
        if d.get("action") == "EXECUTE"
    ]


def _load_execution_results(exec_dir: str | Path | None = None) -> list[dict]:
    """Load successful execution results.

    Authoritative source: S3 dataset ``execution_results`` via the shared
    research data-access layer. ``exec_dir`` is an explicit OFFLINE FIXTURE
    override (test/local replay) — never a production fallback.
    """
    if exec_dir is not None:
        base = Path(exec_dir)
        if not base.exists():
            return []
        results = []
        for f in base.rglob("*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    if e.get("result_ok") and e.get("correlation_id"):
                        results.append(e)
                except json.JSONDecodeError:
                    pass
        return results

    from research_engine.data_access.s3_source import get_default_source

    return [
        e for e in get_default_source().read_dataset("execution_results")
        if e.get("result_ok") and e.get("correlation_id")
    ]


def _get_nested(d: dict, dotpath: str) -> Any:
    """Get a value from a nested dict using dot notation."""
    parts = dotpath.split(".")
    current = d
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _apply_enrichment(trade: dict, decision: dict) -> None:
    """Apply decision trace fields to trade dict."""
    # Flat fields
    for src_field, target_field in _DT_FIELDS.items():
        val = decision.get(src_field)
        trade[target_field] = val

    # Nested V10 fields
    for dotpath, target_field in _V10_FIELDS.items():
        val = _get_nested(decision, dotpath)
        trade[target_field] = val

    # Components dict (keep as nested structure)
    components = decision.get("components")
    if components:
        trade["dt_components"] = components

    # V10 opportunity reasoning
    opp = decision.get("v10_opportunity", {})
    if opp:
        trade["dt_opportunity_reasoning"] = opp.get("reasoning", [])

    # V10 strategy reasoning
    strat = decision.get("v10_strategy", {})
    if strat:
        trade["dt_strategy_reasoning"] = strat.get("reasoning", [])


def _build_markdown(report: dict) -> str:
    md = []
    md.append("# V10 Decision Enrichment Report")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Source: {report['source_file']}")
    md.append(f"Output: {report['output_file']}")
    md.append("")

    md.append("## Summary")
    md.append("")
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| Total trades | {report['total_trades']} |")
    md.append(f"| Matched | {report['matched']} |")
    md.append(f"| Unmatched | {report['unmatched']} |")
    md.append(f"| Match rate | {report['match_rate']:.0%} |")
    md.append(f"| Decision traces loaded | {report['decision_traces_loaded']} |")
    md.append(f"| Execution results loaded | {report['execution_results_loaded']} |")

    md.append("")
    md.append("## Match Methods")
    md.append("")
    md.append("| Method | Count |")
    md.append("|---|---|")
    for method, count in sorted(report["match_methods"].items(), key=lambda x: -x[1]):
        md.append(f"| {method} | {count} |")

    if report.get("missing_fields"):
        md.append("")
        md.append("## Missing Fields (top 10)")
        md.append("")
        md.append("| Field | Missing Count |")
        md.append("|---|---|")
        for field, count in list(report["missing_fields"].items())[:10]:
            md.append(f"| {field} | {count} |")

    if report.get("unmatched_trades"):
        md.append("")
        md.append("## Unmatched Trades")
        md.append("")
        md.append("| Trade ID | Symbol | Correlation ID |")
        md.append("|---|---|---|")
        for t in report["unmatched_trades"]:
            md.append(f"| {t['trade_id']} | {t['symbol']} | {t.get('correlation_id', '')} |")

    md.append("")
    md.append("---")
    return "\n".join(md)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    print("=" * 56)
    print("  V10 DECISION TRACE ENRICHMENT")
    print("=" * 56)

    result = enrich_trades()

    if "error" in result:
        print(f"\nERROR: {result['error']}")
        sys.exit(1)

    print(f"\n  Total trades: {result['total_trades']}")
    print(f"  Matched: {result['matched']} ({result['match_rate']:.0%})")
    print(f"  Unmatched: {result['unmatched']}")
    print(f"\n  Match methods:")
    for method, count in sorted(result["match_methods"].items(), key=lambda x: -x[1]):
        if count > 0:
            print(f"    {method}: {count}")
    print(f"\n  Decision traces loaded: {result['decision_traces_loaded']}")
    print(f"  Execution results loaded: {result['execution_results_loaded']}")
    print(f"\n  Output: {result['output_file']}")
    print(f"  Report: reports/research/decision_enrichment_report.*")
    print("=" * 56)
