"""
Inspect shadow record structure to find correct time field for matching.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, ".")

from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
from research_engine.v10.universes.models import Population

builder = ShadowOutcomeUniverseBuilder()
builder.build()
shadows = builder.get_population(Population.PRIMARY_V10_SHADOW)

# Print first 3 shadow records fully to understand field structure
print(f"Total V10_PRIMARY shadows: {len(shadows)}")
print()
for i, s in enumerate(shadows[:3]):
    print(f"=== SHADOW {i+1} ===")
    for k, v in sorted(s.items()):
        print(f"  {k}: {v}")
    print()

# Check time-related fields
time_fields = set()
for s in shadows[:20]:
    for k in s.keys():
        if "time" in k.lower() or "date" in k.lower() or "ts" in k.lower() or "timestamp" in k.lower():
            time_fields.add(k)

print(f"Time-related fields found: {sorted(time_fields)}")
print()

# Check the actual values of each time field
for tf in sorted(time_fields):
    vals = [s.get(tf) for s in shadows[:5] if s.get(tf)]
    print(f"  {tf}: {vals[:3]}")
