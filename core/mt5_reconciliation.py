"""
MT5 Broker PnL Reconciliation — Extracts authoritative PnL from MT5 deal history.

Matches trade journal records to MT5 deals by position_ticket.
Produces a reconciliation report without modifying existing datasets.

Usage:
    from core.mt5_reconciliation import reconcile_all, ReconciliationResult
    result = reconcile_all()
    # result.report contains structured audit
    # result.matched contains per-trade broker data

Run standalone:
    python -m core.mt5_reconciliation
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_JOURNAL_DIR = "logs/trade_journal"
_OUTPUT_DIR = "reports/research"


# ═══════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════

@dataclass
class MT5DealRecord:
    """One MT5 deal extracted from history."""
    deal_ticket: int = 0
    order_ticket: int = 0
    position_id: int = 0
    symbol: str = ""
    deal_type: int = 0  # 0=BUY, 1=SELL
    entry_type: int = 0  # 0=IN, 1=OUT, 2=INOUT, 3=OUT_BY
    volume: float = 0.0
    price: float = 0.0
    time_unix: float = 0.0
    profit: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    fee: float = 0.0
    comment: str = ""
    magic: int = 0
    reason: int = 0
    external_id: str = ""


@dataclass
class ReconciliationEntry:
    """One matched journal trade with broker data."""
    trade_id: str = ""
    symbol: str = ""
    position_ticket: int = 0
    journal_entry_time: float = 0.0
    journal_pnl: float = 0.0
    # MT5 data
    mt5_matched: bool = False
    match_method: str = ""  # POSITION_ID, UNMATCHED, AMBIGUOUS
    match_confidence: str = ""  # HIGH, MEDIUM, LOW, NONE
    # Broker financials
    broker_profit: float = 0.0
    broker_commission: float = 0.0
    broker_swap: float = 0.0
    broker_fee: float = 0.0
    broker_net_profit: float = 0.0
    # Aggregated deals
    deal_count: int = 0
    deals: list = field(default_factory=list)
    # Mismatch
    pnl_mismatch: float = 0.0
    pnl_mismatch_ratio: float = 0.0


@dataclass
class ReconciliationResult:
    """Complete reconciliation output."""
    generated_utc: str = ""
    journal_count: int = 0
    mt5_deals_count: int = 0
    matched: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    match_rate: float = 0.0
    entries: list = field(default_factory=list)
    by_symbol: dict = field(default_factory=dict)
    by_asset_class: dict = field(default_factory=dict)
    non_fx_recovery: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# MT5 DEAL HISTORY EXTRACTION
# ═══════════════════════════════════════════════════════════════

def extract_mt5_deals(
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    magic: int = 0,
) -> list[MT5DealRecord]:
    """
    Extract all closed deals from MT5 history.

    Args:
        from_date: Start date (default: 2020-01-01)
        to_date: End date (default: 2030-01-01)
        magic: Filter by magic number (0 = all)

    Returns:
        List of MT5DealRecord
    """
    try:
        import MetaTrader5 as mt5
        from core.mt5_timeout import mt5_call
    except ImportError:
        logger.warning("[MT5_RECON] MetaTrader5 not available — cannot extract deals")
        return []

    if from_date is None:
        from_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
    if to_date is None:
        to_date = datetime(2030, 1, 1, tzinfo=timezone.utc)

    # Ensure MT5 is initialized
    if not mt5.initialize():
        logger.warning("[MT5_RECON] MT5 initialization failed")
        return []

    deals = mt5_call(mt5.history_deals_get, from_date, to_date)
    if deals is None:
        logger.warning("[MT5_RECON] No deals returned from MT5")
        return []

    records = []
    for deal in deals:
        # Filter by magic if specified
        if magic > 0 and int(deal.magic) != magic:
            continue

        records.append(MT5DealRecord(
            deal_ticket=int(deal.ticket),
            order_ticket=int(deal.order),
            position_id=int(deal.position_id),
            symbol=str(deal.symbol),
            deal_type=int(deal.type),
            entry_type=int(deal.entry),
            volume=float(deal.volume),
            price=float(deal.price),
            time_unix=float(deal.time),
            profit=float(deal.profit),
            commission=float(deal.commission),
            swap=float(deal.swap),
            fee=float(deal.fee) if hasattr(deal, "fee") else 0.0,
            comment=str(deal.comment) if deal.comment else "",
            magic=int(deal.magic),
            reason=int(deal.reason) if hasattr(deal, "reason") else 0,
            external_id=str(deal.external_id) if hasattr(deal, "external_id") else "",
        ))

    logger.info(f"[MT5_RECON] Extracted {len(records)} deals from MT5 history")
    return records


# ═══════════════════════════════════════════════════════════════
# MATCHING ENGINE
# ═══════════════════════════════════════════════════════════════

def match_journal_to_mt5(
    journal_trades: list[dict],
    mt5_deals: list[MT5DealRecord],
) -> list[ReconciliationEntry]:
    """
    Match journal trades to MT5 deals.

    Matching strategy (in priority order):
    1. position_ticket == deal.position_id (strongest — used by trade management)
    2. position_ticket == deal.deal_ticket (fallback — some brokers)
    3. Ambiguous if multiple positions match

    A single position can have multiple deals (entry + exit + partial).
    We aggregate all EXIT deals for a position to get total broker PnL.
    """
    # Index MT5 deals by position_id (group all deals per position)
    deals_by_position: dict[int, list[MT5DealRecord]] = {}
    for deal in mt5_deals:
        deals_by_position.setdefault(deal.position_id, []).append(deal)

    # Also index by deal_ticket for fallback matching
    deals_by_ticket: dict[int, MT5DealRecord] = {d.deal_ticket: d for d in mt5_deals}

    results = []
    for trade in journal_trades:
        ticket = trade.get("position_ticket", 0)
        entry = ReconciliationEntry(
            trade_id=trade.get("trade_id", ""),
            symbol=trade.get("symbol", ""),
            position_ticket=ticket,
            journal_entry_time=trade.get("entry_time", 0),
            journal_pnl=trade.get("net_pnl", 0),
        )

        if ticket <= 0:
            entry.match_method = "NO_TICKET"
            entry.match_confidence = "NONE"
            results.append(entry)
            continue

        # Method 1: Match by position_id
        position_deals = deals_by_position.get(ticket, [])
        if position_deals:
            _aggregate_deals(entry, position_deals, "POSITION_ID", "HIGH")
            results.append(entry)
            continue

        # Method 2: Match by deal_ticket directly
        direct_deal = deals_by_ticket.get(ticket)
        if direct_deal:
            # Found as deal_ticket — get all deals for that position
            pos_deals = deals_by_position.get(direct_deal.position_id, [direct_deal])
            _aggregate_deals(entry, pos_deals, "DEAL_TICKET", "HIGH")
            results.append(entry)
            continue

        # Method 3: Search by symbol + approximate time + volume
        # (controlled fallback with strict matching)
        symbol = trade.get("symbol", "")
        direction = trade.get("direction", "")
        volume = trade.get("final_volume", 0)
        entry_time = trade.get("entry_time", 0)

        candidates = []
        for pos_id, pos_deals in deals_by_position.items():
            # Check symbol match
            if not any(d.symbol == symbol for d in pos_deals):
                continue
            # Check time proximity (within 60 seconds of entry)
            entry_deals = [d for d in pos_deals if d.entry_type == 0]  # DEAL_ENTRY_IN
            for ed in entry_deals:
                if abs(ed.time_unix - entry_time) < 60 and abs(ed.volume - volume) < 0.01:
                    candidates.append((pos_id, pos_deals))
                    break

        if len(candidates) == 1:
            _aggregate_deals(entry, candidates[0][1], "TIMESTAMP_SYMBOL", "LOW")
        elif len(candidates) > 1:
            entry.match_method = "AMBIGUOUS"
            entry.match_confidence = "NONE"
        else:
            entry.match_method = "UNMATCHED"
            entry.match_confidence = "NONE"

        results.append(entry)

    return results


def _aggregate_deals(entry: ReconciliationEntry, deals: list[MT5DealRecord], method: str, confidence: str) -> None:
    """Aggregate MT5 deal financials for a matched position."""
    entry.mt5_matched = True
    entry.match_method = method
    entry.match_confidence = confidence

    # Sum EXIT deals (entry_type == 1 means DEAL_ENTRY_OUT)
    exit_deals = [d for d in deals if d.entry_type == 1]
    if not exit_deals:
        # Fallback: use all deals with non-zero profit
        exit_deals = [d for d in deals if d.profit != 0]

    total_profit = sum(d.profit for d in exit_deals)
    total_commission = sum(d.commission for d in deals)  # Commission on all deals
    total_swap = sum(d.swap for d in deals)
    total_fee = sum(d.fee for d in deals)

    entry.broker_profit = round(total_profit, 4)
    entry.broker_commission = round(total_commission, 4)
    entry.broker_swap = round(total_swap, 4)
    entry.broker_fee = round(total_fee, 4)
    entry.broker_net_profit = round(total_profit + total_commission + total_swap + total_fee, 4)
    entry.deal_count = len(deals)
    entry.deals = [d.deal_ticket for d in deals]

    # Mismatch calculation
    if entry.journal_pnl != 0:
        entry.pnl_mismatch = round(entry.broker_net_profit - entry.journal_pnl, 4)
        entry.pnl_mismatch_ratio = round(
            abs(entry.pnl_mismatch / entry.journal_pnl), 2
        ) if entry.journal_pnl != 0 else 0


# ═══════════════════════════════════════════════════════════════
# RECONCILIATION ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

def reconcile_all(
    journal_dir: str | None = None,
    magic: int = 713001,
) -> ReconciliationResult:
    """
    Run full reconciliation: load journal, extract MT5, match, report.

    Args:
        journal_dir: Override journal directory
        magic: MT5 magic number to filter (default: 713001)

    Returns:
        ReconciliationResult with complete audit data
    """
    # Load journal trades
    j_path = Path(journal_dir or _JOURNAL_DIR)
    journal_trades = []
    for f in sorted(j_path.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    journal_trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # Extract MT5 deals
    mt5_deals = extract_mt5_deals(magic=magic)

    # Match
    entries = match_journal_to_mt5(journal_trades, mt5_deals)

    # Aggregate results
    matched = sum(1 for e in entries if e.mt5_matched)
    unmatched = sum(1 for e in entries if e.match_method == "UNMATCHED")
    ambiguous = sum(1 for e in entries if e.match_method == "AMBIGUOUS")

    # By symbol
    from core.instrument_utils import get_instrument_class
    by_symbol: dict[str, dict] = {}
    by_asset: dict[str, dict] = {}

    for e in entries:
        sym = e.symbol
        if sym not in by_symbol:
            by_symbol[sym] = {"total": 0, "matched": 0, "unmatched": 0,
                              "broker_profit": 0, "commission": 0, "swap": 0}
        by_symbol[sym]["total"] += 1
        if e.mt5_matched:
            by_symbol[sym]["matched"] += 1
            by_symbol[sym]["broker_profit"] += e.broker_profit
            by_symbol[sym]["commission"] += e.broker_commission
            by_symbol[sym]["swap"] += e.broker_swap
        else:
            by_symbol[sym]["unmatched"] += 1

        # By asset class
        asset = get_instrument_class(sym).value
        if asset not in by_asset:
            by_asset[asset] = {"total": 0, "matched": 0, "unmatched": 0, "broker_profit": 0}
        by_asset[asset]["total"] += 1
        if e.mt5_matched:
            by_asset[asset]["matched"] += 1
            by_asset[asset]["broker_profit"] += e.broker_net_profit

    # Non-FX recovery
    non_fx_symbols = {"XAUUSD", "US500", "NAS100", "US30", "GER40"}
    non_fx_entries = [e for e in entries if e.symbol in non_fx_symbols]
    non_fx_recovery = {
        "total": len(non_fx_entries),
        "matched": sum(1 for e in non_fx_entries if e.mt5_matched),
        "unmatched": sum(1 for e in non_fx_entries if not e.mt5_matched),
        "details": [
            {
                "trade_id": e.trade_id,
                "symbol": e.symbol,
                "ticket": e.position_ticket,
                "journal_pnl": e.journal_pnl,
                "broker_profit": e.broker_net_profit if e.mt5_matched else None,
                "match_method": e.match_method,
                "confidence": e.match_confidence,
                "mismatch_ratio": e.pnl_mismatch_ratio,
            }
            for e in non_fx_entries
        ],
    }

    result = ReconciliationResult(
        generated_utc=datetime.now(timezone.utc).isoformat(),
        journal_count=len(journal_trades),
        mt5_deals_count=len(mt5_deals),
        matched=matched,
        unmatched=unmatched,
        ambiguous=ambiguous,
        match_rate=round(matched / max(len(entries), 1), 4),
        entries=[asdict(e) for e in entries],
        by_symbol={k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in by_symbol.items()},
        by_asset_class=by_asset,
        non_fx_recovery=non_fx_recovery,
    )

    return result


def save_reconciliation_report(result: ReconciliationResult, output_dir: str | None = None) -> tuple[str, str]:
    """Save reconciliation report as JSON + MD."""
    out = Path(output_dir or _OUTPUT_DIR)
    out.mkdir(parents=True, exist_ok=True)

    # JSON (full)
    json_path = out / "mt5_reconciliation_report.json"
    report_dict = asdict(result)
    json_path.write_text(json.dumps(report_dict, indent=2, default=str), encoding="utf-8")

    # Markdown
    md = _build_markdown(result)
    md_path = out / "mt5_reconciliation_report.md"
    md_path.write_text(md, encoding="utf-8")

    return str(json_path), str(md_path)


def _build_markdown(result: ReconciliationResult) -> str:
    md = []
    md.append("# MT5 Broker PnL Reconciliation Report")
    md.append("")
    md.append(f"Generated: {result.generated_utc}")
    md.append("")
    md.append("## Overall")
    md.append("")
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| Journal trades | {result.journal_count} |")
    md.append(f"| MT5 deals extracted | {result.mt5_deals_count} |")
    md.append(f"| Matched | {result.matched} |")
    md.append(f"| Unmatched | {result.unmatched} |")
    md.append(f"| Ambiguous | {result.ambiguous} |")
    md.append(f"| Match rate | {result.match_rate:.0%} |")

    md.append("")
    md.append("## By Asset Class")
    md.append("")
    md.append("| Class | Total | Matched | Unmatched | Broker Profit |")
    md.append("|---|---|---|---|---|")
    for cls, stats in sorted(result.by_asset_class.items()):
        md.append(f"| {cls} | {stats['total']} | {stats['matched']} | "
                  f"{stats.get('unmatched', 0)} | ${stats.get('broker_profit', 0):.2f} |")

    md.append("")
    md.append("## By Symbol")
    md.append("")
    md.append("| Symbol | Total | Matched | Unmatched | Broker Profit |")
    md.append("|---|---|---|---|---|")
    for sym, stats in sorted(result.by_symbol.items(), key=lambda x: -x[1]["total"]):
        md.append(f"| {sym} | {stats['total']} | {stats['matched']} | "
                  f"{stats['unmatched']} | ${stats.get('broker_profit', 0):.2f} |")

    md.append("")
    md.append("## Non-FX Recovery")
    md.append("")
    nfx = result.non_fx_recovery
    md.append(f"Total non-FX: {nfx.get('total', 0)} | Matched: {nfx.get('matched', 0)} | Unmatched: {nfx.get('unmatched', 0)}")
    md.append("")
    if nfx.get("details"):
        md.append("| Trade | Symbol | Ticket | Journal PnL | Broker Profit | Method | Confidence |")
        md.append("|---|---|---|---|---|---|---|")
        for d in nfx["details"]:
            bp = f"${d['broker_profit']:.2f}" if d["broker_profit"] is not None else "—"
            md.append(f"| {d['trade_id']} | {d['symbol']} | {d['ticket']} | "
                      f"${d['journal_pnl']:.2f} | {bp} | {d['match_method']} | {d['confidence']} |")

    md.append("")
    md.append("## Existing Research Dataset")
    md.append("")
    md.append("**Changed: NO** — this is a discovery/audit only")
    md.append("")
    md.append("---")
    return "\n".join(md)


# ═══════════════════════════════════════════════════════════════
# STANDALONE EXECUTION
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    logging.basicConfig(level=logging.INFO)
    print("Running MT5 reconciliation...")

    result = reconcile_all()
    json_path, md_path = save_reconciliation_report(result)

    print(f"\nMT5 RECONCILIATION COMPLETE")
    print(f"  Journal trades: {result.journal_count}")
    print(f"  MT5 deals: {result.mt5_deals_count}")
    print(f"  Matched: {result.matched}")
    print(f"  Unmatched: {result.unmatched}")
    print(f"  Ambiguous: {result.ambiguous}")
    print(f"  Match rate: {result.match_rate:.0%}")
    print(f"\n  Non-FX recovery:")
    nfx = result.non_fx_recovery
    print(f"    Total: {nfx.get('total', 0)}")
    print(f"    Matched: {nfx.get('matched', 0)}")
    print(f"    Unmatched: {nfx.get('unmatched', 0)}")
    print(f"\n  Reports: {json_path}")
    print(f"           {md_path}")
    print(f"\n  Existing research dataset changed: NO")
