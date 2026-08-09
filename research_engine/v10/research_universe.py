"""
V10 Research Universe Builder.

Creates a single canonical research dataset where every trade is a complete
research event combining:
    - Trade execution truth (PnL, prices, lifecycle)
    - Decision context (strategy, score, confidence, risk)
    - Market context (regime, session, volatility, trend)
    - Strategy observations (family, pattern, conditions)
    - Data quality metadata (anomaly, governance, completeness)

Output:
    data/research/research_universe.jsonl

Usage:
    from research_engine.v10.research_universe import build_research_universe
    result = build_research_universe()

CLI:
    python -m research_engine.v10.research_universe
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.data_governance import DataGovernanceValidator, GovernanceStatus
from research_engine.v10.pnl_normalization import normalize_trade_pnl

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# PATH CONSTANTS
# ═══════════════════════════════════════════════════════════════

_ENRICHED_FILE = "logs/research_ready_trade_dataset/research_ready_trades_enriched.jsonl"
_BASE_FILE = "logs/research_ready_trade_dataset/research_ready_trades.jsonl"
_RECON_REPORT = "reports/research/mt5_reconciliation_report.json"
_OUTPUT_DIR = "data/research"
_OUTPUT_FILE = "data/research/research_universe.jsonl"
_REPORTS_DIR = "reports/research"

# Session classification (UTC hour ranges)
_SESSIONS = {
    "ASIAN": (22, 7),      # 22:00-07:00 UTC
    "LONDON": (7, 15),     # 07:00-15:00 UTC
    "NEW_YORK": (12, 21),  # 12:00-21:00 UTC
}


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

def build_research_universe(
    enriched_file: str | None = None,
    base_file: str | None = None,
    recon_file: str | None = None,
    output_file: str | None = None,
    reports_dir: str | None = None,
    skip_governance: bool = False,
) -> dict[str, Any]:
    """
    Build the research universe from all validated sources.

    Steps:
        1. Run governance gate (unless skipped)
        2. Load enriched trades (falls back to base if enriched unavailable)
        3. Load reconciliation data for canonical PnL
        4. Build structured research events
        5. Validate each record
        6. Write output + report

    Returns:
        Build summary dict.
    """
    enriched_path = Path(enriched_file or _ENRICHED_FILE)
    base_path = Path(base_file or _BASE_FILE)
    recon_path = Path(recon_file or _RECON_REPORT)
    out_path = Path(output_file or _OUTPUT_FILE)
    rep_dir = Path(reports_dir or _REPORTS_DIR)

    # ─── GOVERNANCE GATE ──────────────────────────────────────
    governance_status = "SKIPPED"
    if not skip_governance:
        validator = DataGovernanceValidator()
        gov_result = validator.validate()
        governance_status = gov_result["data_trust"]

        if governance_status == GovernanceStatus.FAIL:
            logger.error("[RESEARCH_UNIVERSE] Governance FAIL — aborting build")
            return {
                "error": "Governance check FAILED — research universe not built",
                "governance_status": governance_status,
            }
        if governance_status == GovernanceStatus.WARNING:
            logger.warning("[RESEARCH_UNIVERSE] Governance WARNING — proceeding with flag")

    # ─── LOAD DATA ────────────────────────────────────────────
    # Prefer enriched dataset (has decision context joined)
    if enriched_path.exists():
        trades = _load_jsonl(enriched_path)
        source_used = "enriched"
    elif base_path.exists():
        trades = _load_jsonl(base_path)
        source_used = "base"
    else:
        return {"error": "No research dataset found"}

    # Load reconciliation for canonical PnL components
    recon_data = _load_json(recon_path)
    recon_by_ticket = {}
    if recon_data:
        for e in recon_data.get("entries", []):
            recon_by_ticket[e["position_ticket"]] = e

    logger.info(f"[RESEARCH_UNIVERSE] Loaded {len(trades)} trades from {source_used}")

    # ─── BUILD UNIVERSE ───────────────────────────────────────
    universe = []
    coverage = {
        "decision": 0,
        "market": 0,
        "strategy": 0,
        "complete": 0,
        "incomplete": 0,
    }

    seen_ids = set()

    for trade in trades:
        trade_id = trade.get("trade_id", "")

        # Reject duplicates
        if trade_id in seen_ids:
            continue
        seen_ids.add(trade_id)

        # Build structured event
        event = _build_event(trade, recon_by_ticket, governance_status)
        universe.append(event)

        # Track coverage
        if event["decision"]["strategy"] or event["decision"]["score"]:
            coverage["decision"] += 1
        if event["market"]["regime"]:
            coverage["market"] += 1
        if event["strategy"]["family"] or event["strategy"]["pattern"]:
            coverage["strategy"] += 1
        if event["quality"]["data_completeness"] == "COMPLETE":
            coverage["complete"] += 1
        else:
            coverage["incomplete"] += 1

    # ─── WRITE OUTPUT ─────────────────────────────────────────
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, default=str) for e in universe]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    logger.info(f"[RESEARCH_UNIVERSE] Built {len(universe)} research events -> {out_path}")

    # ─── REPORT ───────────────────────────────────────────────
    total = len(universe)
    report = {
        "generated_utc": timestamp_now(),
        "source_used": source_used,
        "governance_status": governance_status,
        "total_trades": total,
        "coverage": {
            "decision_matched": coverage["decision"],
            "decision_pct": round(100 * coverage["decision"] / max(total, 1), 1),
            "market_matched": coverage["market"],
            "market_pct": round(100 * coverage["market"] / max(total, 1), 1),
            "strategy_matched": coverage["strategy"],
            "strategy_pct": round(100 * coverage["strategy"] / max(total, 1), 1),
            "complete_events": coverage["complete"],
            "complete_pct": round(100 * coverage["complete"] / max(total, 1), 1),
            "incomplete_events": coverage["incomplete"],
        },
        "output_file": str(out_path),
    }

    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / "research_universe_report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (rep_dir / "research_universe_report.md").write_text(
        _build_markdown(report), encoding="utf-8"
    )

    return report


# ═══════════════════════════════════════════════════════════════
# EVENT BUILDER
# ═══════════════════════════════════════════════════════════════

def _build_event(trade: dict, recon_by_ticket: dict, governance_status: str) -> dict[str, Any]:
    """Build a single structured research event from a trade."""
    ticket = trade.get("position_ticket", 0)

    # ─── EXECUTION ────────────────────────────────────────────
    # Canonical PnL from normalisation
    recon_entry = recon_by_ticket.get(ticket)
    if recon_entry:
        pnl_norm = normalize_trade_pnl(recon_entry, source="recon")
    else:
        pnl_norm = normalize_trade_pnl(trade, source="research")

    r_multiple = trade.get("realised_r", 0)

    execution = {
        "ticket": ticket,
        "symbol": trade.get("symbol", ""),
        "direction": trade.get("direction", ""),
        "entry_price": trade.get("entry_price", 0),
        "exit_price": trade.get("exit_price", 0),
        "entry_time": trade.get("entry_time", 0),
        "exit_time": trade.get("exit_time", 0),
        "stop_loss": trade.get("stop_loss", 0),
        "take_profit": trade.get("take_profit", 0),
        "gross_profit": pnl_norm["gross_profit"],
        "commission": pnl_norm["commission"],
        "swap": pnl_norm["swap"],
        "net_realised_pnl": pnl_norm["net_realised_pnl"],
        "r_multiple": r_multiple,
        "volume": trade.get("volume", 0),
        "duration_seconds": trade.get("duration_seconds", 0),
        "exit_reason": trade.get("exit_reason_validated", ""),
    }

    # ─── DECISION ─────────────────────────────────────────────
    strategy = (
        trade.get("dt_strategy")
        or trade.get("dt_v10_strategy_family")
        or trade.get("strategy")
        or ""
    )
    score = trade.get("dt_score_strategy") or trade.get("dt_score_neutral") or trade.get("score") or 0
    confidence = (
        trade.get("dt_strategy_confidence")
        or trade.get("dt_v10_strategy_confidence")
        or trade.get("strategy_confidence")
        or 0
    )

    decision = {
        "strategy": strategy,
        "score": score,
        "confidence": confidence,
        "decision_type": trade.get("dt_match_method", ""),
        "decision_timestamp": trade.get("entry_time", 0),
        "components": trade.get("dt_components", {}),
        "weakest_component": trade.get("dt_weakest_component", ""),
        "ev": trade.get("dt_ev"),
        "p_success": trade.get("dt_p_success"),
    }

    # ─── MARKET ───────────────────────────────────────────────
    regime = (
        trade.get("dt_v10_regime")
        or trade.get("dt_regime")
        or trade.get("regime")
        or ""
    )
    session = _classify_session(trade.get("entry_time", 0))
    volatility = trade.get("dt_v10_volatility") or ""
    h1_direction = trade.get("dt_h1_direction") or ""
    htf_bias = trade.get("dt_directional_bias") or trade.get("dt_h4_trend") or ""

    market = {
        "regime": regime,
        "session": session,
        "volatility": volatility,
        "trend_state": h1_direction,
        "higher_timeframe_bias": htf_bias,
        "h4_phase": trade.get("dt_h4_phase", ""),
        "h1_clarity": trade.get("dt_h1_clarity", 0),
    }

    # ─── STRATEGY ─────────────────────────────────────────────
    family = trade.get("dt_v10_strategy_family") or trade.get("dt_strategy") or ""
    pattern = trade.get("dt_pattern") or trade.get("pattern") or ""

    strategy_block = {
        "family": family,
        "pattern": pattern,
        "conditions_met": _count_conditions(trade),
        "strategy_confidence": confidence,
        "opportunity_quality": trade.get("dt_opportunity_quality", 0),
        "opportunity_type": trade.get("dt_opportunity_type", ""),
    }

    # ─── QUALITY ──────────────────────────────────────────────
    anomaly_status = trade.get("anomaly_status", "NORMAL")
    is_anomaly = anomaly_status == "FLAGGED"

    # Determine completeness
    has_decision = bool(strategy or score)
    has_market = bool(regime)
    has_strategy = bool(family or pattern)
    has_pnl = pnl_norm["net_realised_pnl"] != 0 or trade.get("exit_reason_validated") == "BREAKEVEN"

    completeness = "COMPLETE" if (has_decision and has_market and has_strategy) else "INCOMPLETE"
    missing = []
    if not has_decision:
        missing.append("decision")
    if not has_market:
        missing.append("market")
    if not has_strategy:
        missing.append("strategy")

    quality = {
        "anomaly": is_anomaly,
        "anomaly_reasons": trade.get("anomaly_reasons", []),
        "governance_status": governance_status,
        "data_completeness": completeness,
        "missing": missing,
        "join_method": trade.get("dt_match_method", "none"),
        "pnl_source": pnl_norm["pnl_source"],
    }

    return {
        "trade_id": trade.get("trade_id", ""),
        "execution": execution,
        "decision": decision,
        "market": market,
        "strategy": strategy_block,
        "quality": quality,
    }


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _classify_session(entry_time: float) -> str:
    """Classify trade session from UTC entry timestamp."""
    if not entry_time or entry_time <= 0:
        return ""
    try:
        dt = datetime.fromtimestamp(entry_time, tz=timezone.utc)
        hour = dt.hour

        # Overlap detection (London+NY overlap 12-15 UTC)
        in_london = 7 <= hour < 15
        in_ny = 12 <= hour < 21
        in_asian = hour >= 22 or hour < 7

        if in_london and in_ny:
            return "LONDON_NY_OVERLAP"
        elif in_london:
            return "LONDON"
        elif in_ny:
            return "NEW_YORK"
        elif in_asian:
            return "ASIAN"
        return "OFF_SESSION"
    except (OSError, ValueError):
        return ""


def _count_conditions(trade: dict) -> int:
    """Count how many scoring components passed threshold (>0.5)."""
    components = trade.get("dt_components", {})
    if not components:
        return 0
    return sum(1 for v in components.values() if isinstance(v, (int, float)) and v > 0.5)


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


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None


def _build_markdown(report: dict) -> str:
    md = []
    md.append("# V10 Research Universe Report")
    md.append("")
    md.append(f"Generated: {report['generated_utc']}")
    md.append(f"Source: {report['source_used']}")
    md.append(f"Governance: {report['governance_status']}")
    md.append("")
    md.append("## Coverage")
    md.append("")
    c = report["coverage"]
    md.append(f"| Layer | Matched | Coverage |")
    md.append(f"|---|---|---|")
    md.append(f"| Decision | {c['decision_matched']}/{report['total_trades']} | {c['decision_pct']}% |")
    md.append(f"| Market | {c['market_matched']}/{report['total_trades']} | {c['market_pct']}% |")
    md.append(f"| Strategy | {c['strategy_matched']}/{report['total_trades']} | {c['strategy_pct']}% |")
    md.append(f"| **Complete events** | **{c['complete_events']}/{report['total_trades']}** | **{c['complete_pct']}%** |")
    md.append(f"| Incomplete events | {c['incomplete_events']} | |")
    md.append("")
    md.append(f"Output: `{report['output_file']}`")
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
    print("  V10 RESEARCH UNIVERSE BUILDER")
    print("=" * 56)

    result = build_research_universe()

    if "error" in result:
        print(f"\n  ERROR: {result['error']}")
        sys.exit(1)

    c = result["coverage"]
    print(f"\n  Governance: {result['governance_status']}")
    print(f"  Source: {result['source_used']}")
    print(f"  Total events: {result['total_trades']}")
    print(f"\n  Coverage:")
    print(f"    Decision:  {c['decision_matched']}/{result['total_trades']} ({c['decision_pct']}%)")
    print(f"    Market:    {c['market_matched']}/{result['total_trades']} ({c['market_pct']}%)")
    print(f"    Strategy:  {c['strategy_matched']}/{result['total_trades']} ({c['strategy_pct']}%)")
    print(f"    Complete:  {c['complete_events']}/{result['total_trades']} ({c['complete_pct']}%)")
    print(f"\n  Output: {result['output_file']}")
    print(f"  Report: reports/research/research_universe_report.*")
    print("=" * 56)
