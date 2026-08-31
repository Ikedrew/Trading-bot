"""Verify ineligible horizon is persisted (range regime rejects EXTENDED)."""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, '.')

import core.persistence.horizon_candidates_writer as hcw
hcw._LOCAL_DIR = tempfile.mkdtemp()

from core.horizon.horizon_classifier import classify_horizons

# Range regime — EXTENDED requires TRENDING, so it's ineligible
result = classify_horizons(
    strategy_type="REVERSAL",
    strategy_confidence=0.6,
    h4_regime="RANGE",
    h4_regime_confidence=0.7,
    h1_direction="NEUTRAL",
    h1_bos_confirmed=False,
    htf_alignment=0.5,
    h4_alignment=0.4,
    market_quality=0.5,
    chop_clarity=0.5,
    volatility_quality=0.5,
    pattern="TEST_PATTERN",
    direction="SELL",
)

records = hcw.build_horizon_candidate_records(
    assessments=result.assessments,
    selected_horizon="",  # legacy path — no V10 selection
    symbol="GBPUSD",
    bar_time=1784800000.0,
    lineage={"canonical_opportunity_id": "GBPUSD*1784800000*TEST_PATTERN", "cycle_id": 7},
)
hcw.persist_horizon_candidates(candidates=records)

files = list(Path(hcw._LOCAL_DIR).rglob("*.jsonl"))
lines = files[0].read_text().strip().split("\n")
print(f"Records: {len(lines)} (must be 3 — ineligible NOT discarded)")
for line in lines:
    rec = json.loads(line)
    print(f"  {rec['horizon']:9s} eligible={rec['eligible']!s:5s} "
          f"conf={rec['confidence']:.4f} status={rec['selection_status']}")
print()
ext = [json.loads(l) for l in lines if json.loads(l)["horizon"] == "EXTENDED"][0]
print("INELIGIBLE EXTENDED RECORD:")
print(json.dumps(ext, indent=2))
