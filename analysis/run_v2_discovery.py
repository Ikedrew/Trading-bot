"""Execute V2 Discovery Engine against CURRENT epoch dataset."""

import json
from pathlib import Path

# Load dataset
with open("analysis/artifacts/v2_discovery_dataset.json") as f:
    records = json.load(f)

print(f"Loaded {len(records)} records")
print()

# Run full discovery
from research_engine.v2_discovery.discovery_report import run_full_discovery, save_report

report = run_full_discovery(records, min_sample=20, spread_cost_r=0.48)

# ═══════════════════════════════════════════════════════════════════
# CQ1 RESULTS
# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("CQ1 — INDIVIDUAL FEATURE PREDICTIVE VALUE")
print("=" * 70)
print(f"Features analysed: {report.cq1_features_analysed}")
print(f"Significant features: {report.cq1_significant_features}")
print(f"Conclusion: {report.cq1_conclusion}")
print()

print("Top 10 features by best cost-adjusted EV:")
for i, f in enumerate(report.cq1_top_features[:10], 1):
    feat = f["feature"]
    cat = f["best_category"]
    ev = f["best_ev"]
    n = f["total_sample"]
    pred = f["predictive"]
    print(f"  {i:2d}. {feat:25s} | best={cat:15s} | EV={ev:+.4f}R | n={n:4d} | predictive={pred}")

print()

# ═══════════════════════════════════════════════════════════════════
# CQ2 RESULTS
# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("CQ2 — CONTEXT COMBINATIONS")
print("=" * 70)
print(f"Hypotheses tested: {report.cq2_hypotheses_tested}")
print(f"Validated out-of-sample: {report.cq2_validated_combinations}")
print(f"Best combination: {report.cq2_best_combination}")
print(f"Best validated EV: {report.cq2_best_ev:.4f}R")
print(f"Conclusion: {report.cq2_conclusion}")
print()

# ═══════════════════════════════════════════════════════════════════
# CQ3 RESULTS
# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("CQ3 — ENVIRONMENT CLASSIFICATION")
print("=" * 70)
print(f"Favourable environments: {report.cq3_favourable_environments}")
print(f"Unfavourable environments: {report.cq3_unfavourable_environments}")
print(f"Conclusion: {report.cq3_conclusion}")
print()
if report.cq3_best_environments:
    print("Best environments:")
    for env in report.cq3_best_environments[:5]:
        dim = env["dimension"]
        state = env["state"]
        ev = env["cost_adjusted_ev"]
        wr = env["win_rate"]
        n = env["sample_size"]
        p = env["p_value"]
        print(f"  {dim:20s} = {state:15s} | EV={ev:+.4f}R | WR={wr:.1%} | n={n:3d} | p={p:.4f}")
print()
if report.cq3_worst_environments:
    print("Worst environments:")
    for env in report.cq3_worst_environments[:5]:
        dim = env["dimension"]
        state = env["state"]
        ev = env["cost_adjusted_ev"]
        wr = env["win_rate"]
        n = env["sample_size"]
        print(f"  {dim:20s} = {state:15s} | EV={ev:+.4f}R | WR={wr:.1%} | n={n:3d}")
print()

# ═══════════════════════════════════════════════════════════════════
# CQ4 RESULTS
# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("CQ4 — PROBABILITY MODEL")
print("=" * 70)
print(f"Model accuracy: {report.cq4_model_accuracy:.1%}")
print(f"Baseline accuracy: {report.cq4_baseline_accuracy:.1%}")
print(f"Model useful: {report.cq4_model_useful}")
print(f"Brier score: {report.cq4_brier_score:.4f}")
print(f"Mean calibration error: {report.cq4_mean_calibration_error:.4f}")
print(f"Conclusion: {report.cq4_conclusion}")
print()
if report.cq4_top_features:
    print("Feature importance (leave-one-out):")
    for fi in report.cq4_top_features[:5]:
        feat = fi["feature"]
        imp = fi["importance"]
        print(f"  {feat:25s} | importance={imp:+.4f}")
print()

# ═══════════════════════════════════════════════════════════════════
# OVERALL CONCLUSION
# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("OVERALL CONCLUSION")
print("=" * 70)
if report.conclusion:
    print(f"Outcome: {report.conclusion.outcome}")
    print(f"Confidence: {report.conclusion.confidence}")
    print(f"Summary: {report.conclusion.summary}")
    print()
    print("Evidence:")
    for e in report.conclusion.evidence:
        print(f"  - {e}")
    print()
    print("Next steps:")
    for s in report.conclusion.next_steps:
        print(f"  - {s}")

# Save report
filepath = save_report(report)
print(f"\nReport saved: {filepath}")
