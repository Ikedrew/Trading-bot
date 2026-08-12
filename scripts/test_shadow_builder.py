"""Quick validation that ShadowOutcomeUniverseBuilder loads and builds."""
import sys
from pathlib import Path
sys.path.insert(0, ".")

from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
from research_engine.v10.universes.models import Population

out = []

builder = ShadowOutcomeUniverseBuilder()
count = builder.load()
out.append(f"Loaded: {count} raw records")

records = builder.build()
out.append(f"Built: {len(records)} normalised records")

meta = builder.metadata
out.append(f"Metadata: {meta.record_count} records, hash={meta.content_hash[:12]}")
out.append(f"Exclusions: {meta.exclusions}")

# Population checks
for pop in [
    Population.ALL_SHADOW_OUTCOMES,
    Population.SHADOW_WINS,
    Population.SHADOW_LOSSES,
    Population.PRIMARY_V10_SHADOW,
    Population.HORIZON_SCALP,
    Population.HORIZON_INTRADAY,
    Population.HORIZON_EXTENDED,
    Population.SHADOW_TP_HIT,
    Population.SHADOW_SL_HIT,
    Population.SHADOW_TIMEOUT,
]:
    pop_records = builder.get_population(pop)
    out.append(f"  {pop.value}: {len(pop_records)}")

# Sample record
if records:
    r = records[0]
    out.append(f"\nSample record fields: {list(r.keys())}")
    out.append(f"  shadow_type={r.get('shadow_type')}")
    out.append(f"  entity_id={r.get('entity_id','')[:25]}")
    out.append(f"  r_multiple={r.get('r_multiple')}")
    out.append(f"  evidence_source={r.get('evidence_source')}")

# Lineage stats
with_eid = sum(1 for r in records if r.get("has_entity_id"))
out.append(f"\nLineage: {with_eid}/{len(records)} have entity_id ({with_eid*100//max(len(records),1)}%)")
out.append("\nDONE — ShadowOutcomeUniverseBuilder operational")

Path("scripts/shadow_builder_result.txt").write_text("\n".join(out), encoding="utf-8")
print("OUTPUT WRITTEN")
