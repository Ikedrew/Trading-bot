#!/usr/bin/env python
"""Phase 5: dump exact signatures needed for regression tests (read-only)."""
import inspect, sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"C:\Users\ikues\Trading bot build"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.market_context import builder as mcb
from core.market_context import persistence as mcp
from core.market_context.models import MarketContext
from core.opportunity.factory import create_opportunity
from core.identity.canonical import make_canonical_opportunity_id
from core.runtime.decision_recorder import DecisionRecorder
from core.decision_audit import persist_decision_audit

print("BUILDER classes:", [n for n, _ in inspect.getmembers(mcb, inspect.isclass)])
try:
    print("BUILDER INIT:", inspect.signature(mcb.MarketContextBuilder.__init__))
    print("BUILD:", inspect.signature(mcb.MarketContextBuilder.build))
except Exception as e:
    print("builder sig err:", e)
print()
print("MC functions:", [n for n, _ in inspect.getmembers(mcp, inspect.isfunction)])
for fn in ["persist_market_context"]:
    f = getattr(mcp, fn, None)
    if f:
        print(f"PERSIST_MC {fn}:", inspect.signature(f))
print()
print("MC dataclass fields:", [f.name for f in MarketContext.__dataclass_fields__.values()] if hasattr(MarketContext, "__dataclass_fields__") else "n/a")
print()
print("CREATE_OPP:", inspect.signature(create_opportunity))
print("MAKE_CANON:", inspect.signature(make_canonical_opportunity_id))
print()
print("RECORDER INIT:", inspect.signature(DecisionRecorder.__init__))
print("INIT_CYCLE:", inspect.signature(DecisionRecorder.init_cycle))
print("RECORDER methods:", [n for n, _ in inspect.getmembers(DecisionRecorder, inspect.isfunction)])
print()
print("PERSIST_AUDIT:", inspect.signature(persist_decision_audit))
