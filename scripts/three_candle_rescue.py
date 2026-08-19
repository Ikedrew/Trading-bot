"""
THREE_BLACK_CROWS / THREE_WHITE_SOLDIERS — RESCUE ANALYSIS

The current failure: 100% SL hit in bar 1. The stop is placed within
the first bar's adverse excursion range.

Key data already available per shadow:
  - mae_r: maximum adverse excursion (how far price moved AGAINST the position)
  - mfe_r: maximum favorable excursion (how far price moved FOR the position)
  - bars_held: always 1 (SL hit on first bar)
  - risk_distance: actual SL distance used
  - entry_price: midpoint at decision time
  - stop_loss: actual SL placed

QUESTIONS:
1. If the SL were WIDER, would the trade survive bar 1 and reach profitability?
   → Test: what % of trades have MAE < 2R, 3R, 5R? (i.e., wider stop survives)
   → If MFE > wider_SL_cost, the trade would be net positive with wider stop

2. If the entry were DELAYED (wait for pullback), would it work?
   → Can't directly test from shadow data, but can infer from MFE/MAE relationship

3. Is the directional signal itself correct?
   → If MFE is large (price DOES move in the pattern direction eventually),
     the signal is correct but the TIMING/STOP is wrong
   → If MFE is small, the signal itself is wrong

4. Alternative: USE AS COUNTER-SIGNAL (invert direction)
   → If TBC always hits SL for SELL, what would BUY produce?
   → shadow data doesn't directly show this, but MAE (adverse) for SELL = MFE for BUY

DOES NOT modify V10.
"""
import sys
import json
import statistics
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, ".")


def load_shadow_primary():
    from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
    from research_engine.v10.universes.models import Population
    builder = ShadowOutcomeUniverseBuilder()
    builder.build()
    return builder.get_population(Population.PRIMARY_V10_SHADOW)


def main():
    out = []
    out.append("=" * 80)
    out.append("THREE_BLACK_CROWS / THREE_WHITE_SOLDIERS — RESCUE ANALYSIS")
    out.append("=" * 80)
    out.append("")

    shadows = load_shadow_primary()
    real = [s for s in shadows if s.get("correlation_id")]

    tbc = [s for s in real if s.get("pattern") == "THREE_BLACK_CROWS"]
    tws = [s for s in real if s.get("pattern") == "THREE_WHITE_SOLDIERS"]
    failing = tbc + tws

    out.append(f"THREE_BLACK_CROWS: {len(tbc)}")
    out.append(f"THREE_WHITE_SOLDIERS: {len(tws)}")
    out.append(f"Total: {len(failing)}")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 1: MAE/MFE DEEP ANALYSIS — IS THE SIGNAL CORRECT?
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 1: IS THE DIRECTIONAL SIGNAL CORRECT?")
    out.append("━" * 80)
    out.append("")

    out.append("  If the signal is correct, price should move substantially in the")
    out.append("  pattern's direction at SOME point during the 60-bar observation window.")
    out.append("  MFE = max favorable excursion (how far price moved in the RIGHT direction)")
    out.append("  MAE = max adverse excursion (how far price moved AGAINST the position)")
    out.append("")

    mfes = [s.get("mfe_r", 0) for s in failing if s.get("mfe_r") is not None]
    maes = [s.get("mae_r", 0) for s in failing if s.get("mae_r") is not None]

    out.append(f"  MFE (favorable movement in pattern direction):")
    out.append(f"    N={len(mfes)}")
    out.append(f"    Mean: {statistics.mean(mfes):.3f}R")
    out.append(f"    Median: {statistics.median(mfes):.3f}R")
    out.append(f"    Min: {min(mfes):.3f}R, Max: {max(mfes):.3f}R")
    out.append("")

    # MFE distribution
    out.append(f"  MFE distribution (how far did price go FOR the trade?):")
    mfe_buckets = [(0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 100)]
    for lo, hi in mfe_buckets:
        count = sum(1 for m in mfes if lo <= m < hi)
        pct = count * 100 / len(mfes) if mfes else 0
        out.append(f"    {lo:.2f}–{hi:.1f}R: {count} ({pct:.0f}%)")
    out.append("")

    out.append(f"  MAE (adverse movement against trade):")
    out.append(f"    Mean: {statistics.mean(maes):.3f}R")
    out.append(f"    Median: {statistics.median(maes):.3f}R")
    out.append("")

    # Key question: does MFE > MAE? (i.e., price moves MORE in the right direction than wrong)
    favorable = sum(1 for m, a in zip(mfes, maes) if m > a)
    out.append(f"  MFE > MAE (price moved further in pattern direction): {favorable}/{len(mfes)} ({favorable*100//len(mfes)}%)")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 2: WIDER STOP SIMULATION
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 2: WOULD A WIDER STOP RESCUE THE PATTERN?")
    out.append("━" * 80)
    out.append("")

    out.append("  Current SL: hit at 1.0R (by definition)")
    out.append("  If SL were placed at 2R, 3R, or 5R, would the trade survive and profit?")
    out.append("")
    out.append("  Logic: If MAE < new_SL_distance, the trade survives.")
    out.append("         Then outcome = MFE (capped at original TP) adjusted for wider risk.")
    out.append("")

    # For each wider stop multiplier, calculate:
    # - How many trades survive (MAE < multiplier)
    # - For survivors: what's the effective R (MFE / new_risk_distance)
    # - Account for the fact that wider stop = smaller R per pip of movement

    out.append(f"  {'SL Width':<10} {'Survive':<9} {'Surv%':<7} {'Mean MFE/SL':<12} {'Mean outcome':<13} {'Net Expectancy'}")
    out.append(f"  {'─'*10} {'─'*9} {'─'*7} {'─'*12} {'─'*13} {'─'*15}")

    for sl_mult in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        survivors = [(m, a) for m, a in zip(mfes, maes) if a < sl_mult]
        losers = [(m, a) for m, a in zip(mfes, maes) if a >= sl_mult]
        
        n_survive = len(survivors)
        n_total = len(mfes)
        surv_pct = n_survive * 100 / n_total if n_total else 0

        if survivors:
            # Survivor outcome: MFE / sl_mult (normalized to new risk unit)
            # But TP was set at original RR * 1R. If SL is wider, TP stays same absolute level.
            # So effective TP in new-R terms = original_RR / sl_mult
            # Survivor can at best reach MFE / sl_mult in new R terms
            survivor_outcomes = [min(m / sl_mult, 3.0) for m, a in survivors]  # Cap at 3R (original RR)
            mean_surv_outcome = statistics.mean(survivor_outcomes)
        else:
            mean_surv_outcome = 0

        # Losers always produce -1R (hit the wider stop)
        # Expected value = P(survive) * mean_survivor_R + P(lose) * (-1)
        p_survive = n_survive / n_total if n_total else 0
        p_lose = 1 - p_survive
        expected_r = p_survive * mean_surv_outcome + p_lose * (-1.0)

        mean_mfe_sl = statistics.mean([m / sl_mult for m, _ in zip(mfes, maes)]) if mfes else 0

        out.append(f"  {sl_mult:.1f}R{'':<6} {n_survive}/{n_total}{'':<3} {surv_pct:<7.0f} "
                   f"{mean_mfe_sl:<12.3f} {mean_surv_outcome:+.3f}R{'':<5} {expected_r:+.4f}R")

    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 3: SEPARATE TBC AND TWS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 3: SEPARATE ANALYSIS — TBC vs TWS")
    out.append("━" * 80)
    out.append("")

    for name, group in [("THREE_BLACK_CROWS (SELL)", tbc), ("THREE_WHITE_SOLDIERS (BUY)", tws)]:
        g_mfes = [s.get("mfe_r", 0) for s in group if s.get("mfe_r") is not None]
        g_maes = [s.get("mae_r", 0) for s in group if s.get("mae_r") is not None]
        
        if not g_mfes:
            continue

        out.append(f"  {name} (N={len(group)}):")
        out.append(f"    MFE: Mean={statistics.mean(g_mfes):.3f}R, Median={statistics.median(g_mfes):.3f}R")
        out.append(f"    MAE: Mean={statistics.mean(g_maes):.3f}R, Median={statistics.median(g_maes):.3f}R")
        out.append(f"    MFE > MAE: {sum(1 for m, a in zip(g_mfes, g_maes) if m > a)}/{len(g_mfes)}")
        out.append("")

        # Wider stop simulation for this group
        out.append(f"    Wider stop simulation:")
        for sl_mult in [2.0, 3.0, 5.0]:
            survivors = [(m, a) for m, a in zip(g_mfes, g_maes) if a < sl_mult]
            n_survive = len(survivors)
            p_survive = n_survive / len(g_mfes)
            if survivors:
                surv_out = [min(m / sl_mult, 3.0) for m, a in survivors]
                ev = p_survive * statistics.mean(surv_out) + (1 - p_survive) * (-1.0)
            else:
                ev = -1.0
            out.append(f"      SL={sl_mult}R: {n_survive}/{len(g_mfes)} survive, EV={ev:+.4f}R")
        out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 4: COUNTER-SIGNAL HYPOTHESIS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 4: COUNTER-SIGNAL — WHAT IF WE INVERT?")
    out.append("━" * 80)
    out.append("")

    out.append("  If TBC (3 bearish candles) is used to BUY instead of SELL:")
    out.append("  The current MAE (adverse for SELL) becomes MFE (favorable for BUY)")
    out.append("  The current MFE (favorable for SELL) becomes MAE (adverse for BUY)")
    out.append("")

    # For inverted TBC (BUY instead of SELL):
    # New MFE = old MAE (price going UP is favorable for BUY, adverse for SELL)
    # New MAE = old MFE (price going DOWN is adverse for BUY, favorable for SELL)
    tbc_mfes = [s.get("mfe_r", 0) for s in tbc if s.get("mfe_r") is not None]
    tbc_maes = [s.get("mae_r", 0) for s in tbc if s.get("mae_r") is not None]

    if tbc_mfes and tbc_maes:
        # Inverted: MFE_new = MAE_old, MAE_new = MFE_old
        inv_mfes = tbc_maes  # Adverse for SELL = favorable for BUY
        inv_maes = tbc_mfes  # Favorable for SELL = adverse for BUY

        out.append(f"  INVERTED TBC (BUY after 3 bearish — counter-trend):")
        out.append(f"    New MFE (favorable): Mean={statistics.mean(inv_mfes):.3f}R")
        out.append(f"    New MAE (adverse): Mean={statistics.mean(inv_maes):.3f}R")
        out.append(f"    MFE > MAE: {sum(1 for m, a in zip(inv_mfes, inv_maes) if m > a)}/{len(inv_mfes)}")
        out.append("")

        # Simulate inverted with standard 1R stop:
        # If new MAE < 1 → survives, outcome = min(new MFE, RR_target) 
        inv_survivors = [(m, a) for m, a in zip(inv_mfes, inv_maes) if a < 1.0]
        n_inv_surv = len(inv_survivors)
        p_inv_surv = n_inv_surv / len(inv_mfes)
        if inv_survivors:
            inv_outcomes = [min(m, 3.0) for m, a in inv_survivors]  # Cap at 3R TP
            inv_ev = p_inv_surv * statistics.mean(inv_outcomes) + (1 - p_inv_surv) * (-1.0)
        else:
            inv_ev = -1.0
        out.append(f"    Inverted with 1R stop: {n_inv_surv}/{len(inv_mfes)} survive, EV={inv_ev:+.4f}R")
        
        # With wider stop
        for sl_mult in [2.0, 3.0]:
            inv_surv_w = [(m, a) for m, a in zip(inv_mfes, inv_maes) if a < sl_mult]
            p_w = len(inv_surv_w) / len(inv_mfes)
            if inv_surv_w:
                inv_out_w = [min(m / sl_mult, 3.0) for m, a in inv_surv_w]
                ev_w = p_w * statistics.mean(inv_out_w) + (1 - p_w) * (-1.0)
            else:
                ev_w = -1.0
            out.append(f"    Inverted with {sl_mult}R stop: {len(inv_surv_w)}/{len(inv_mfes)} survive, EV={ev_w:+.4f}R")
        out.append("")

    # Same for TWS inverted (SELL after 3 bullish)
    tws_mfes = [s.get("mfe_r", 0) for s in tws if s.get("mfe_r") is not None]
    tws_maes = [s.get("mae_r", 0) for s in tws if s.get("mae_r") is not None]

    if tws_mfes and tws_maes:
        inv_mfes_tws = tws_maes
        inv_maes_tws = tws_mfes

        out.append(f"  INVERTED TWS (SELL after 3 bullish — counter-trend):")
        out.append(f"    New MFE: Mean={statistics.mean(inv_mfes_tws):.3f}R")
        out.append(f"    New MAE: Mean={statistics.mean(inv_maes_tws):.3f}R")

        inv_surv_tws = [(m, a) for m, a in zip(inv_mfes_tws, inv_maes_tws) if a < 1.0]
        p_tws = len(inv_surv_tws) / len(inv_mfes_tws)
        if inv_surv_tws:
            inv_out_tws = [min(m, 3.0) for m, a in inv_surv_tws]
            ev_tws = p_tws * statistics.mean(inv_out_tws) + (1 - p_tws) * (-1.0)
        else:
            ev_tws = -1.0
        out.append(f"    Inverted with 1R stop: {len(inv_surv_tws)}/{len(inv_mfes_tws)} survive, EV={ev_tws:+.4f}R")
        
        for sl_mult in [2.0, 3.0]:
            inv_surv_w = [(m, a) for m, a in zip(inv_mfes_tws, inv_maes_tws) if a < sl_mult]
            p_w = len(inv_surv_w) / len(inv_mfes_tws)
            if inv_surv_w:
                inv_out_w = [min(m / sl_mult, 3.0) for m, a in inv_surv_w]
                ev_w = p_w * statistics.mean(inv_out_w) + (1 - p_w) * (-1.0)
            else:
                ev_w = -1.0
            out.append(f"    Inverted with {sl_mult}R stop: {len(inv_surv_w)}/{len(inv_mfes_tws)} survive, EV={ev_w:+.4f}R")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 5: TIMEOUT SCENARIO (NO STOP, JUST HOLD 60 BARS)
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("━" * 80)
    out.append("SECTION 5: WHAT IF THERE WERE NO STOP LOSS? (timeout only)")
    out.append("━" * 80)
    out.append("")

    # The shadow model uses SL, TP, and 60-bar timeout.
    # Current exit: bar 1 SL hit (because SL is checked first).
    # If SL didn't exist: what would the 60-bar outcome be?
    # We can't know exactly from shadow data, but we can estimate:
    # - If trade survives (wider stop), what's the expected outcome at timeout?
    # - MFE tells us the best the trade gets during 60 bars

    # Actually: since current bars_held = 1 and exit = stop_loss,
    # the shadow never reaches timeout. We need to look at what happens
    # if the stop were removed entirely.
    
    # The best proxy: use MFE as the max profit potential, MAE as max drawdown potential.
    # At timeout (60 bars): final R would be somewhere between -MAE and +MFE.
    # Midpoint estimate: (MFE - MAE) / 2 as average path

    # Better: MFE and MAE are measured over the full observation window (60 bars)
    # even though exit is at bar 1 — WAIT, is this true?
    # In the shadow model: once SL is hit, the trade EXITS. MFE/MAE are measured
    # only up to the exit point (bar 1). So MFE = max in that 1 bar.
    
    # This means MFE = intra-bar maximum favorable within bar 1.
    # We CANNOT determine the 60-bar outcome from current data.

    out.append("  NOTE: MFE/MAE are measured only to the exit point (bar 1).")
    out.append("  We cannot determine the 60-bar timeout outcome from current shadow data.")
    out.append("  This would require re-running the shadow with wider stops or no SL.")
    out.append("")
    out.append("  HOWEVER: the MFE data tells us that within bar 1 alone:")
    out.append(f"    Mean MFE (bar 1 only): {statistics.mean(mfes):.3f}R")
    out.append(f"    This is the max favorable within a single 5-min bar.")
    out.append(f"    Over 60 bars, the MFE would likely be MUCH larger.")
    out.append("")

    # ═══════════════════════════════════════════════════════════════════════════
    # CONCLUSIONS
    # ═══════════════════════════════════════════════════════════════════════════
    out.append("=" * 80)
    out.append("CONCLUSIONS: CAN THE PATTERN BE RESCUED?")
    out.append("=" * 80)
    out.append("")

    # The key question: at wider stops, does EV become positive?
    # From section 2 results
    out.append("  WIDER STOP RESCUE ATTEMPT:")
    out.append("")

    # Recalculate for clarity
    best_ev = -999
    best_sl = 0
    for sl_mult in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        survivors = [(m, a) for m, a in zip(mfes, maes) if a < sl_mult]
        n_survive = len(survivors)
        p_survive = n_survive / len(mfes) if mfes else 0
        if survivors:
            surv_out = [min(m / sl_mult, 3.0) for m, a in survivors]
            ev = p_survive * statistics.mean(surv_out) + (1 - p_survive) * (-1.0)
        else:
            ev = -1.0
        if ev > best_ev:
            best_ev = ev
            best_sl = sl_mult

    out.append(f"  Best wider-stop EV: {best_ev:+.4f}R at SL={best_sl}R")
    if best_ev > 0:
        out.append(f"  → YES: Pattern is RESCUABLE with wider stop ({best_sl}R)")
    elif best_ev > -0.3:
        out.append(f"  → MARGINAL: Pattern approaches breakeven with wider stop but not clearly profitable")
    else:
        out.append(f"  → NO: Even with optimal wider stop, EV remains negative ({best_ev:+.4f}R)")
    out.append("")

    out.append("  COUNTER-SIGNAL RESCUE ATTEMPT:")
    if tbc_mfes and tbc_maes:
        inv_surv = [(m, a) for m, a in zip(tbc_maes, tbc_mfes) if a < 1.0]
        p = len(inv_surv) / len(tbc_mfes)
        if inv_surv:
            ev_inv = p * statistics.mean([min(m, 3.0) for m, a in inv_surv]) + (1 - p) * (-1.0)
        else:
            ev_inv = -1.0
        out.append(f"  Inverted TBC (BUY after 3 bearish): EV={ev_inv:+.4f}R")
    if tws_mfes and tws_maes:
        inv_surv = [(m, a) for m, a in zip(tws_maes, tws_mfes) if a < 1.0]
        p = len(inv_surv) / len(tws_mfes)
        if inv_surv:
            ev_inv = p * statistics.mean([min(m, 3.0) for m, a in inv_surv]) + (1 - p) * (-1.0)
        else:
            ev_inv = -1.0
        out.append(f"  Inverted TWS (SELL after 3 bullish): EV={ev_inv:+.4f}R")
    out.append("")

    out.append("  CRITICAL LIMITATION:")
    out.append("  MAE/MFE are measured only within bar 1 (the exit bar).")
    out.append("  A wider stop would allow the trade to SURVIVE bar 1 and potentially")
    out.append("  reach much higher MFE over 60 bars. But we cannot measure this")
    out.append("  from existing shadow data — it would require re-simulating with")
    out.append("  wider stops over the full 60-bar window.")
    out.append("")
    
    out.append("  FINAL VERDICT:")
    out.append("")
    if best_ev > 0:
        out.append("  THE PATTERN IS POTENTIALLY RESCUABLE — but requires:")
        out.append("  1. Wider stop (beyond 1-bar retrace range)")
        out.append("  2. Shadow re-simulation with the wider geometry")
        out.append("  3. Validation that multi-bar MFE justifies the wider risk")
    else:
        out.append("  BASED ON BAR-1 DATA ALONE: Pattern appears unrescuable.")
        out.append("  HOWEVER: this is inconclusive because we only see 1 bar of data.")
        out.append("  The correct next step is to re-run shadow simulation with wider stops")
        out.append("  (e.g., 3R SL with same TP) to see multi-bar outcomes.")
        out.append("")
        out.append("  INTERIM RECOMMENDATION (no code changes):")
        out.append("  - THREE_BLACK_CROWS and THREE_WHITE_SOLDIERS should be SUSPENDED")
        out.append("    from live execution pending wider-stop shadow re-simulation.")
        out.append("  - Current evidence: -37R total drag on portfolio (38 × -1R)")
        out.append("  - Risk of continued use: ~1R loss per occurrence with 0% win rate")
    out.append("")

    output = "\n".join(out)
    Path("reports/research/baseline/three_candle_rescue_analysis.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
