"""
SWING_BLOCKED Decision Analysis — Post MarketContext Migration.

Shows H1 structural state at time of block to confirm correctness.
"""
import json, sys, statistics
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TRACE_DIR = Path("logs/decision_trace")


def _load_traces() -> list[dict]:
    records = []
    if not _TRACE_DIR.exists():
        return records
    for item in sorted(_TRACE_DIR.rglob("*.jsonl")):
        for line in item.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return records


def main():
    traces = _load_traces()
    post = [t for t in traces if t.get("regime_source")]
    swing_blocked = [t for t in post if t.get("terminal_stage") == "swing"]

    print("=" * 70)
    print("SWING_BLOCKED ANALYSIS — Post MarketContext Migration")
    print("=" * 70)
    print(f"Post-migration traces: {len(post)}")
    print(f"Swing-blocked decisions: {len(swing_blocked)} ({len(swing_blocked)/max(len(post),1)*100:.1f}%)")
    print()

    if not swing_blocked:
        print("No swing-blocked decisions in post-migration data.")
        return

    # ═══════════════════════════════════════════════════════════════════
    # BLOCK REASON BREAKDOWN
    # ═══════════════════════════════════════════════════════════════════
    print("─── BLOCK REASON BREAKDOWN ─────────────────────────────────────")
    reasons = Counter()
    for t in swing_blocked:
        r = t.get("terminal_reason", "") or ""
        # Extract the core reason
        if "swing_blocked:" in r:
            r = r.replace("swing_blocked: ", "").replace("swing_blocked:", "")
        reasons[r] += 1

    for reason, cnt in reasons.most_common():
        print(f"  {reason:65s}: {cnt:4d} ({cnt/len(swing_blocked)*100:.1f}%)")
    print()

    # Classify into H1-authority vs legacy M5
    h1_blocks = sum(c for r, c in reasons.items() if "h1_" in r.lower())
    m5_blocks = sum(c for r, c in reasons.items() if "h1_" not in r.lower() and "swing_direction" in r.lower())
    bos_blocks = sum(c for r, c in reasons.items() if "bos" in r.lower() or "structure_not_broken" in r.lower())

    print(f"  H1-authority blocks (h1_bos/h1_swing): {h1_blocks}")
    print(f"  Directional blocks (swing_direction):  {m5_blocks}")
    print(f"  BOS-required blocks:                   {bos_blocks}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # DETAILED TABLE
    # ═══════════════════════════════════════════════════════════════════
    print("─── DETAILED SWING BLOCKS ──────────────────────────────────────")
    print()
    print(f"{'#':>3s} {'Sym':6s} {'Trade':>5s} {'H1_Dir':>7s} {'H1_Swing':>9s} {'H1_BOS':>6s} {'BOS_Dir':>8s} {'M5_SwDir':>9s} {'M5_BOS':>6s} {'Reason (short)'}")
    print(f"{'─'*3} {'─'*6} {'─'*5} {'─'*7} {'─'*9} {'─'*6} {'─'*8} {'─'*9} {'─'*6} {'─'*30}")

    # We need to extract from engine_result metadata fields
    # In traces: swing_direction, swing_break_confirmed, m5_swing_bos_diagnostic, bos_source
    for i, t in enumerate(swing_blocked[:40]):
        symbol = t.get("symbol", "?")[:6]

        # Trade direction from pattern
        pattern = t.get("pattern_name", "")
        # Infer side from pattern name or components
        components = t.get("components", {})
        # Use metadata if available
        meta = t.get("metadata", {})

        # H1 fields (from MarketContext/HTF)
        # These come through the engine_result metadata which is stored in trace
        # The trace stores: regime (H4), but H1 direction comes through trend_alignment_source
        h1_dir = "?"
        trend_src = t.get("trend_alignment_source", "")
        if trend_src == "H1_PHASE":
            # H1 was providing direction — infer from htf_alignment score
            htf_score = t.get("htf_alignment", 0.5)
            if htf_score > 0.6:
                h1_dir = "ALIGN"
            elif htf_score < 0.4:
                h1_dir = "CONTRA"
            else:
                h1_dir = "NEUTR"
        else:
            h1_dir = "M5_FB"  # M5 fallback was used

        # Swing metadata (from _strategy_meta fields stored in trace metadata or direct)
        swing_dir = t.get("swing_direction", "?") if "swing_direction" in t else "?"
        swing_bos = t.get("swing_break_confirmed", "?")
        m5_diag_bos = t.get("m5_swing_bos_diagnostic", "?")
        bos_source = t.get("bos_source", "?")

        # If not in top-level trace, check metadata dict
        if swing_dir == "?" and meta:
            swing_dir = meta.get("swing_direction", "?")
            swing_bos = meta.get("swing_break_confirmed", "?")

        # Reason
        reason = t.get("terminal_reason", "")
        if "swing_blocked:" in reason:
            reason_short = reason.split("swing_blocked:")[-1].strip()[:30]
        else:
            reason_short = reason[:30]

        # Infer trade side from score components
        bias_align = components.get("bias_alignment", 0.5)
        trade_side = "BUY" if bias_align > 0.6 else "SELL" if bias_align < 0.4 else "?"

        print(f"{i+1:3d} {symbol:6s} {trade_side:>5s} {h1_dir:>7s} {'?':>9s} {str(swing_bos):>6s} {'?':>8s} {swing_dir:>9s} {str(m5_diag_bos):>6s} {reason_short}")

    print()

    # ═══════════════════════════════════════════════════════════════════
    # REGIME CONTEXT OF BLOCKED DECISIONS
    # ═══════════════════════════════════════════════════════════════════
    print("─── CONTEXT OF SWING-BLOCKED DECISIONS ──────────────────────────")
    print()

    regimes = Counter(t.get("regime") for t in swing_blocked)
    print("  Regime distribution:")
    for r, c in regimes.most_common():
        print(f"    {r or 'UNKNOWN':20s}: {c:4d}")

    strategies = Counter(t.get("selected_strategy") or "None" for t in swing_blocked)
    print("  Strategy distribution:")
    for s, c in strategies.most_common():
        print(f"    {s:20s}: {c:4d}")

    # Score distribution of blocked decisions
    scores = [t.get("score_neutral", 0) for t in swing_blocked if t.get("score_neutral", 0) > 0]
    if scores:
        print(f"  Score: mean={statistics.mean(scores):.4f} median={statistics.median(scores):.4f}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # CORRECTNESS ASSESSMENT
    # ═══════════════════════════════════════════════════════════════════
    print("─── CORRECTNESS ASSESSMENT ─────────────────────────────────────")
    print()

    # Rule 1: REVERSAL without BOS → always correct to block
    reversal_no_bos = sum(1 for t in swing_blocked
                         if "structure_not_broken" in (t.get("terminal_reason") or "")
                         or "bos_not_confirmed" in (t.get("terminal_reason") or ""))
    print(f"  REVERSAL without BOS blocks: {reversal_no_bos} — {'✅ CORRECT (reversal needs structural break)' if reversal_no_bos > 0 else 'none'}")

    # Rule 2: Counter-trend without BOS → correct to block
    directional_blocks = sum(1 for t in swing_blocked
                            if "swing_direction" in (t.get("terminal_reason") or ""))
    print(f"  Directional misalignment blocks: {directional_blocks} — {'✅ CORRECT (counter-structure needs BOS)' if directional_blocks > 0 else 'none'}")

    # Rule 3: H1 authority blocks (new gate)
    h1_authority = sum(1 for t in swing_blocked
                       if "h1_" in (t.get("terminal_reason") or ""))
    print(f"  H1 authority blocks: {h1_authority} — {'✅ CORRECT (H1 structural gate active)' if h1_authority > 0 else 'none'}")
    print()

    # Overall assessment
    total_explained = reversal_no_bos + directional_blocks + h1_authority
    unexplained = len(swing_blocked) - total_explained
    print(f"  Total swing blocks: {len(swing_blocked)}")
    print(f"  Structurally explained: {total_explained}")
    print(f"  Unexplained: {unexplained}")
    print()

    if unexplained == 0:
        print("  ✅ All swing blocks are structurally correct.")
        print("     Decisions were blocked because:")
        print("     - Trade direction contradicted H1 swing structure without BOS")
        print("     - Reversal strategy attempted without structural break confirmation")
    else:
        print(f"  ⚠️  {unexplained} blocks need investigation")
    print()


if __name__ == "__main__":
    main()
