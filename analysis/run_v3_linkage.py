"""Run V3 outcome linkage and report results."""
from core.research.v3_outcome_linker import link_v3_outcomes

report = link_v3_outcomes(persist=True)
s = report.summary()

print("V3 OUTCOME LINKAGE RESULTS")
print("=" * 40)
for k, v in s.items():
    print(f"  {k}: {v}")

print()
linked = [r for r in report.linked_records if r.get("_linkage", {}).get("linked")]
print(f"Records with outcomes: {len(linked)}")

if linked:
    outcomes = [r["outcome_raw_r"] for r in linked if r.get("outcome_raw_r") is not None]
    if outcomes:
        wins = sum(1 for o in outcomes if o > 0)
        print(f"Win rate: {wins/len(outcomes)*100:.1f}%")
        print(f"Mean R: {sum(outcomes)/len(outcomes):.4f}")

    # Show a few samples
    print()
    print("Sample linked records:")
    for r in linked[:3]:
        sym = r.get("symbol", "")
        rp = r.get("h1_range_position", 0)
        eq_h = r.get("equal_highs_above", False)
        eq_l = r.get("equal_lows_below", False)
        out_r = r.get("outcome_raw_r", None)
        exit_r = r.get("outcome_exit_reason", "")
        method = r.get("_linkage", {}).get("match_method", "")
        print(f"  {sym} | range_pos={rp:.3f} | eq_highs={eq_h} | eq_lows={eq_l} | R={out_r} | exit={exit_r} | method={method}")
