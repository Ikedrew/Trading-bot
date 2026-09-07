"""
Wave 3 live proof — run S2/S3/S4/HORIZON-1/STRAT-1 against real canonical S3.

Read-only toward production. Prints honest status per question.
"""
import sys

sys.path.insert(0, ".")

from research_engine.experiments.selection_research import (
    _load_simulation_population,
    run_s2, run_s3, run_s4, run_horizon1, run_strat1,
)


def main() -> int:
    print("=" * 74)
    print("WAVE 3 LIVE PROOF - canonical S3 evidence")
    print("=" * 74)

    pop = _load_simulation_population()
    n = len(pop)
    primaries = sum(1 for r in pop if r["shadow_type"] == "PRIMARY_HORIZON_SIMULATION")
    alternatives = sum(1 for r in pop if r["shadow_type"] == "HORIZON_ALTERNATIVE")
    with_r = sum(1 for r in pop if r["pnl_r"] is not None)
    print(f"shadow simulation population : {n}")
    print(f"  primary-horizon records    : {primaries}")
    print(f"  horizon-alternative records: {alternatives}")
    print(f"  with realised R            : {with_r}")
    print()

    for name, fn, kw in (
        ("S2", run_s2, {}),
        ("S3", run_s3, {}),
        ("S4", run_s4, {}),
        ("HORIZON-1", run_horizon1, {}),
        ("STRAT-1", run_strat1, {}),
    ):
        report = fn(**kw)
        status = report.get("status")
        rec = report.get("recommendation")
        conf = report.get("confidence")
        sample = report.get("dataset", {}).get("sample_size")
        print(f"{name:10s} status={status:20s} n={sample:<6} conf={conf:<18} rec={rec}")
        if status == "COMPLETE" and name in ("S2", "HORIZON-1", "STRAT-1"):
            overall = report["overall"]
            if name == "S2" and overall.get("spread_assessment"):
                print(f"           spread: {overall['spread_assessment']}")
            if name == "HORIZON-1":
                print(f"           best/tied/worse: "
                      f"{overall['selected_best']}/{overall['selected_tied_best']}"
                      f"/{overall['selected_underperformed_alternative']}  "
                      f"mean_delta_r={overall['mean_selected_minus_best_alternative_r']}")
            if name == "STRAT-1":
                print(f"           rho={overall['spearman_rho']}  "
                      f"monotonicity={overall['monotonicity']}")
    print()
    print("READ-ONLY: no S3 writes, no lifecycle mutation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
