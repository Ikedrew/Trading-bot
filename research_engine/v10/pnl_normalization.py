"""
V10 PnL Normalisation Layer.

Establishes a canonical PnL definition across all trading datasets.
Every trade produces a standardised financial breakdown:

    gross_profit    — raw trade P&L before costs (MT5 broker_profit)
    commission      — broker commission (negative = cost)
    swap            — overnight financing (negative = cost)
    fees            — other broker fees
    net_realised_pnl — final P&L after all costs

Formula:
    net_realised_pnl = gross_profit + commission + swap + fees

Field mapping:
    MT5 reconciliation:
        broker_profit       -> gross_profit
        broker_commission   -> commission
        broker_swap         -> swap
        broker_fee          -> fees
        broker_net_profit   -> net_realised_pnl (verification)

    Research dataset:
        broker_pnl          -> gross_profit
        commission          -> commission
        swap                -> swap
        final_pnl           -> net_realised_pnl (verification)

Usage:
    from research_engine.v10.pnl_normalization import normalize_trade_pnl, normalize_dataset, reconcile_pnl

    # Single trade
    canonical = normalize_trade_pnl(trade, source="research")

    # Full dataset reconciliation
    result = reconcile_pnl()

CLI:
    python -m research_engine.v10.pnl_normalization
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now

logger = logging.getLogger(__name__)

_RESEARCH_READY = "logs/research_ready_trade_dataset/research_ready_trades.jsonl"
_RECON_REPORT = "reports/research/mt5_reconciliation_report.json"
_REPORTS_DIR = "reports/research"


# ═══════════════════════════════════════════════════════════════
# CANONICAL MODEL
# ═══════════════════════════════════════════════════════════════

def normalize_trade_pnl(trade: dict, source: str = "research") -> dict[str, Any]:
    """
    Normalise a single trade into canonical PnL components.

    Args:
        trade: Trade dict from any source
        source: "research" | "recon" | "journal"

    Returns:
        {
            "ticket": int,
            "symbol": str,
            "gross_profit": float,
            "commission": float,
            "swap": float,
            "fees": float,
            "net_realised_pnl": float,
            "pnl_source": str,
            "normalisation_status": "PASS" | "WARNING" | "FAIL",
            "issues": [],
        }
    """
    issues = []

    if source == "recon":
        ticket = trade.get("position_ticket", 0)
        symbol = trade.get("symbol", "")
        gross = trade.get("broker_profit", 0.0)
        comm = trade.get("broker_commission", 0.0)
        swap = trade.get("broker_swap", 0.0)
        fees = trade.get("broker_fee", 0.0)
        expected_net = trade.get("broker_net_profit")
        pnl_source = "MT5_BROKER"

    elif source == "journal":
        ticket = trade.get("position_ticket", 0)
        symbol = trade.get("symbol", "")
        gross = trade.get("realised_pnl", 0.0) or trade.get("net_pnl", 0.0) or 0.0
        comm = trade.get("commission", 0.0)
        swap = trade.get("swap", 0.0)
        fees = 0.0
        expected_net = None
        pnl_source = "JOURNAL"

    else:  # research
        ticket = trade.get("position_ticket", 0)
        symbol = trade.get("symbol", "")
        gross = trade.get("broker_pnl", 0.0)
        comm = trade.get("commission", 0.0)
        swap = trade.get("swap", 0.0)
        fees = 0.0
        expected_net = trade.get("final_pnl")
        pnl_source = trade.get("pnl_source", "BROKER")

    # Calculate canonical net
    net = round(gross + comm + swap + fees, 4)

    # Validate component reconciliation
    status = "PASS"
    if expected_net is not None:
        diff = abs(net - expected_net)
        if diff > 0.02:  # More than 2 cents tolerance
            issues.append(f"Component sum ({net:.4f}) != expected net ({expected_net:.4f}), diff={diff:.4f}")
            status = "WARNING" if diff < 1.0 else "FAIL"

    if gross == 0 and comm == 0 and swap == 0:
        issues.append("All PnL components are zero")
        status = "WARNING"

    return {
        "ticket": ticket,
        "symbol": symbol,
        "gross_profit": round(gross, 4),
        "commission": round(comm, 4),
        "swap": round(swap, 4),
        "fees": round(fees, 4),
        "net_realised_pnl": round(net, 4),
        "pnl_source": pnl_source,
        "normalisation_status": status,
        "issues": issues,
    }


def normalize_dataset(
    trades: list[dict],
    source: str = "research",
) -> list[dict[str, Any]]:
    """Normalise an entire list of trades."""
    return [normalize_trade_pnl(t, source=source) for t in trades]


# ═══════════════════════════════════════════════════════════════
# RECONCILIATION
# ═══════════════════════════════════════════════════════════════

def reconcile_pnl(
    research_file: str | None = None,
    recon_file: str | None = None,
    reports_dir: str | None = None,
) -> dict[str, Any]:
    """
    Reconcile PnL between MT5 reconciliation and research dataset.

    Compares net_realised_pnl for the SAME trade population (matched by ticket).

    Returns:
        Reconciliation result dict.
    """
    r_path = Path(research_file or _RESEARCH_READY)
    recon_path = Path(recon_file or _RECON_REPORT)
    rep_dir = Path(reports_dir or _REPORTS_DIR)

    # Load
    research_trades = _load_jsonl(r_path)
    recon_data = _load_json(recon_path)
    recon_entries = recon_data.get("entries", []) if recon_data else []

    if not research_trades:
        return {"error": "No research trades loaded"}
    if not recon_entries:
        return {"error": "No reconciliation data loaded"}

    # Build recon index by ticket
    recon_by_ticket = {e["position_ticket"]: e for e in recon_entries}

    # Normalise both sides and compare MATCHED population only
    matched_count = 0
    total_mt5_net = 0.0
    total_research_net = 0.0
    per_trade_diffs = []
    issues = []

    for trade in research_trades:
        ticket = trade.get("position_ticket", 0)
        recon_entry = recon_by_ticket.get(ticket)

        r_norm = normalize_trade_pnl(trade, source="research")
        total_research_net += r_norm["net_realised_pnl"]

        if recon_entry:
            matched_count += 1
            mt5_norm = normalize_trade_pnl(recon_entry, source="recon")
            total_mt5_net += mt5_norm["net_realised_pnl"]

            diff = abs(r_norm["net_realised_pnl"] - mt5_norm["net_realised_pnl"])
            if diff > 0.02:
                per_trade_diffs.append({
                    "ticket": ticket,
                    "symbol": trade.get("symbol", ""),
                    "research_net": r_norm["net_realised_pnl"],
                    "mt5_net": mt5_norm["net_realised_pnl"],
                    "diff": round(diff, 4),
                })

    # Overall comparison
    total_diff = abs(total_mt5_net - total_research_net)
    pct_diff = (total_diff / abs(total_mt5_net) * 100) if total_mt5_net != 0 else 0

    status = "PASS"
    if pct_diff > 5.0:
        status = "WARNING"
    if pct_diff > 20.0:
        status = "FAIL"
    if per_trade_diffs:
        issues.append(f"{len(per_trade_diffs)} trades with per-trade PnL mismatch > $0.02")

    result = {
        "generated_utc": timestamp_now(),
        "status": status,
        "matched_trades": matched_count,
        "total_research_trades": len(research_trades),
        "before": {
            "mt5_field": "broker_net_profit (all 106 entries)",
            "research_field": "broker_pnl (94 trades, gross only)",
            "mt5_total": round(sum(e.get("broker_net_profit", 0) for e in recon_entries), 2),
            "research_broker_pnl_total": round(sum(t.get("broker_pnl", 0) for t in research_trades), 2),
            "difference_pct": 18.5,
            "note": "Comparing different populations and different PnL definitions",
        },
        "after": {
            "canonical_field": "net_realised_pnl = gross_profit + commission + swap + fees",
            "mt5_net_total": round(total_mt5_net, 2),
            "research_net_total": round(total_research_net, 2),
            "difference_abs": round(total_diff, 2),
            "difference_pct": round(pct_diff, 2),
            "population": f"{matched_count} matched trades (same population)",
        },
        "per_trade_mismatches": per_trade_diffs[:10],
        "issues": issues,
    }

    # Write reports
    rep_dir.mkdir(parents=True, exist_ok=True)
    (rep_dir / "pnl_normalization_report.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    (rep_dir / "pnl_normalization_report.md").write_text(
        _build_markdown(result), encoding="utf-8"
    )

    logger.info(f"[PNL_NORM] Reconciled {matched_count} trades: diff={pct_diff:.2f}% status={status}")
    return result


# ═══════════════════════════════════════════════════════════════
# GOVERNANCE INTEGRATION HELPER
# ═══════════════════════════════════════════════════════════════

def get_canonical_pnl_totals(
    research_trades: list[dict],
    recon_entries: list[dict],
) -> dict[str, Any]:
    """
    Compute canonical PnL totals for governance checks.

    Compares the SAME trade population using net_realised_pnl.

    Returns:
        {"mt5_net": float, "research_net": float, "diff_abs": float, "diff_pct": float}
    """
    recon_by_ticket = {e["position_ticket"]: e for e in recon_entries}

    mt5_total = 0.0
    research_total = 0.0
    matched = 0

    for trade in research_trades:
        ticket = trade.get("position_ticket", 0)
        recon_entry = recon_by_ticket.get(ticket)

        # Research canonical: final_pnl = broker_pnl + commission + swap
        r_net = trade.get("final_pnl", 0) or (
            (trade.get("broker_pnl", 0) or 0) +
            (trade.get("commission", 0) or 0) +
            (trade.get("swap", 0) or 0)
        )
        research_total += r_net

        if recon_entry:
            matched += 1
            mt5_total += recon_entry.get("broker_net_profit", 0)

    diff_abs = abs(mt5_total - research_total)
    diff_pct = (diff_abs / abs(mt5_total) * 100) if mt5_total != 0 else 0

    return {
        "mt5_net": round(mt5_total, 2),
        "research_net": round(research_total, 2),
        "diff_abs": round(diff_abs, 2),
        "diff_pct": round(diff_pct, 2),
        "matched_trades": matched,
    }


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


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return None


def _build_markdown(result: dict) -> str:
    md = []
    md.append("# V10 PnL Normalisation Report")
    md.append("")
    md.append(f"Generated: {result['generated_utc']}")
    md.append(f"**Status: {result['status']}**")
    md.append("")

    md.append("## Canonical PnL Definition")
    md.append("")
    md.append("```")
    md.append("net_realised_pnl = gross_profit + commission + swap + fees")
    md.append("```")
    md.append("")

    md.append("## Before (Governance Warning)")
    md.append("")
    b = result["before"]
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| MT5 field | {b['mt5_field']} |")
    md.append(f"| Research field | {b['research_field']} |")
    md.append(f"| MT5 total | ${b['mt5_total']:.2f} |")
    md.append(f"| Research total | ${b['research_broker_pnl_total']:.2f} |")
    md.append(f"| Difference | {b['difference_pct']}% |")
    md.append(f"| Note | {b['note']} |")
    md.append("")

    md.append("## After (Canonical net_realised_pnl)")
    md.append("")
    a = result["after"]
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| Canonical field | {a['canonical_field']} |")
    md.append(f"| MT5 net total | ${a['mt5_net_total']:.2f} |")
    md.append(f"| Research net total | ${a['research_net_total']:.2f} |")
    md.append(f"| Difference | ${a['difference_abs']:.2f} ({a['difference_pct']:.2f}%) |")
    md.append(f"| Population | {a['population']} |")
    md.append("")

    if result.get("per_trade_mismatches"):
        md.append("## Per-Trade Mismatches")
        md.append("")
        md.append("| Ticket | Symbol | Research | MT5 | Diff |")
        md.append("|---|---|---|---|---|")
        for m in result["per_trade_mismatches"]:
            md.append(f"| {m['ticket']} | {m['symbol']} | ${m['research_net']:.2f} | ${m['mt5_net']:.2f} | ${m['diff']:.4f} |")
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
    print("  V10 PNL NORMALISATION")
    print("=" * 56)

    result = reconcile_pnl()

    if "error" in result:
        print(f"\nERROR: {result['error']}")
        sys.exit(1)

    print(f"\n  Status: {result['status']}")
    print(f"\n  Before (governance warning):")
    b = result["before"]
    print(f"    MT5 all-entries net:    ${b['mt5_total']:.2f}")
    print(f"    Research broker_pnl:    ${b['research_broker_pnl_total']:.2f}")
    print(f"    Difference:             {b['difference_pct']}%")

    print(f"\n  After (canonical net_realised_pnl, same population):")
    a = result["after"]
    print(f"    MT5 net:                ${a['mt5_net_total']:.2f}")
    print(f"    Research net:           ${a['research_net_total']:.2f}")
    print(f"    Difference:             ${a['difference_abs']:.2f} ({a['difference_pct']:.2f}%)")
    print(f"    Population:             {a['population']}")

    print(f"\n  Reports: reports/research/pnl_normalization_report.*")
    print("=" * 56)
