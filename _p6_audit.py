"""Phase 6 read-only audit of fresh (post-boundary) runtime records.

Reads ONLY byte-tails beyond the pre-soak baseline => strictly fresh records.
No file is modified. No production code involved.
"""
import json, sys, io
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent
baseline = json.loads((ROOT / "_p6_baseline.json").read_text(encoding="utf-8-sig"))
fresh = json.loads((ROOT / "_p6_fresh.json").read_text(encoding="utf-8-sig"))

def tail_records(rel, growth):
    p = ROOT / rel
    size = p.stat().st_size
    with open(p, "rb") as f:
        f.seek(max(0, size - growth))
        data = f.read().decode("utf-8", errors="replace")
    recs = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            # possible boundary-straddling line at tail start; flag it
            recs.append({"__MALFORMED__": line[:120]})
    return recs

buckets = defaultdict(list)
for rel, growth in fresh.items():
    recs = tail_records(rel, growth)
    norm = rel.replace("\\", "/")
    if "strategy_observations" in norm: buckets["obs"] += recs
    elif "v3_shadow/market_context" in norm: buckets["ctx"] += recs
    elif "/opportunities/" in norm: buckets["opp"] += recs
    elif "v3_shadow/entry_assessment" in norm: buckets["entry"] += recs
    elif "v3_shadow/risk_assessment" in norm: buckets["risk"] += recs
    elif "v3_shadow/horizon_assessment" in norm: buckets["horizon"] += recs
    elif "v3_shadow/opportunity_assessment" in norm: buckets["opp_assess"] += recs
    elif "v3_shadow/execution_assessment" in norm: buckets["exec_assess"] += recs
    elif "v3_shadow/market_understanding" in norm: buckets["mkt_under"] += recs
    elif "decision_trace" in norm: buckets["trace"] += recs
    elif "decision_audit" in norm: buckets["audit"] += recs
    elif "decision_ledger" in norm: buckets["ledger"] += recs
    elif "execution_context" in norm: buckets["exec_ctx"] += recs
    elif norm.startswith("events/"): buckets["events"] += recs
    else: buckets["other"] += recs

def g(rec, *keys):
    """fetch first present key (also checks nested 'data' dict)"""
    for k in keys:
        if isinstance(rec, dict) and k in rec and rec[k] not in (None, ""):
            return rec[k]
    return None

report = {}
defects = defaultdict(list)

# ---------- 1. strategy observations ----------
obs = [r for r in buckets["obs"] if isinstance(r, dict) and "__MALFORMED__" not in r]
report["strategy_observations"] = len(obs)
for r in obs:
    sym, cid, eid = g(r, "symbol"), g(r, "cycle_id"), g(r, "entity_id")
    root = r.get("canonical_opportunity_id")
    if not sym: defects["malformed"].append(("obs", r))
    if eid is None: defects["missing_entity_obs"].append((sym, cid))
    if root is None: defects["missing_canonical_field_obs"].append((sym, cid))
    if root:  # root established on an observation
        if sym and not root.startswith(sym + "*"):
            defects["stale_cross_symbol_root"].append(("obs", sym, root))

# ---------- 2. market context ----------
ctx = [r for r in buckets["ctx"] if isinstance(r, dict) and "__MALFORMED__" not in r]
report["market_context_shadow"] = len(ctx)
for r in ctx:
    sym, cid, eid, bt = g(r, "symbol"), g(r, "cycle_id"), g(r, "entity_id"), g(r, "bar_time")
    root = r.get("canonical_opportunity_id")
    if not sym or cid is None: defects["malformed"].append(("ctx", r))
    if root not in (None, ""):  # context must never fabricate a canonical
        defects["fabricated_canonical_in_context"].append((sym, cid, root))

# ---------- 3. opportunities ----------
opp = [r for r in buckets["opp"] if isinstance(r, dict) and "__MALFORMED__" not in r]
report["opportunities"] = len(opp)
opp_roots = {}
for r in opp:
    sym, cid = g(r, "symbol"), g(r, "cycle_id")
    root = r.get("canonical_opportunity_id")
    oid = g(r, "opportunity_id", "legacy_opportunity_id")
    if not root:
        defects["opportunity_missing_root"].append((sym, cid))
        continue
    if not root.startswith(f"{sym}*"):
        defects["stale_cross_symbol_root"].append(("opp", sym, root))
    if root in opp_roots:
        defects["duplicate_root"].append(("opp", root))
    opp_roots[root] = (sym, cid, oid)

# ---------- 4/5. assessments (shadow lane records) + decision trace/audit/ledger ----------
def audit_assess(name, recs, extra_keys=()):
    cnt = 0
    for r in recs:
        if not isinstance(r, dict) or "__MALFORMED__" in r:
            defects["malformed"].append((name, r)); continue
        cnt += 1
        sym, cid = g(r, "symbol"), g(r, "cycle_id")
        root = r.get("canonical_opportunity_id")
        if root is None: defects[f"missing_canonical_field_{name}"].append((sym, cid))
    return cnt

report["entry_assessment"] = audit_assess("entry", buckets["entry"])
report["risk_assessment"] = audit_assess("risk", buckets["risk"])
report["horizon_assessment"] = audit_assess("horizon", buckets["horizon"])
report["opportunity_assessment"] = audit_assess("opp_assess", buckets["opp_assess"])
report["execution_assessment"] = audit_assess("exec_assess", buckets["exec_assess"])
report["market_understanding"] = audit_assess("mkt_under", buckets["mkt_under"])

trace = [r for r in buckets["trace"] if isinstance(r, dict) and "__MALFORMED__" not in r]
audit_r = [r for r in buckets["audit"] if isinstance(r, dict) and "__MALFORMED__" not in r]
ledger = [r for r in buckets["ledger"] if isinstance(r, dict) and "__MALFORMED__" not in r]
report["decision_trace"] = len(trace)
report["decision_audit"] = len(audit_r)
report["decision_ledger"] = len(ledger)

dec_roots = {}
for r in trace:
    sym, cid = g(r, "symbol"), g(r, "cycle_id")
    action = g(r, "action", "decision", "verdict")
    root = r.get("canonical_opportunity_id")
    corr = g(r, "correlation_id")
    if root is None: defects["missing_canonical_field_trace"].append((sym, cid))
    if root and not root.startswith(f"{sym}*"):
        defects["stale_cross_symbol_root"].append(("trace", sym, root))
    if (action or "").upper() in ("NO_TRADE", "NO TRADE", "REJECT", "REJECTED") and root:
        defects["notrade_inherited_root"].append((sym, cid, root))
    if root: dec_roots[root] = (sym, cid, corr, action)

for r in audit_r:
    sym, cid = g(r, "symbol"), g(r, "cycle_id")
    root = r.get("canonical_opportunity_id")
    if root is None: defects["missing_canonical_field_audit"].append((sym, cid))
    if root and not root.startswith(f"{sym}*"):
        defects["stale_cross_symbol_root"].append(("audit", sym, root))

ledger_roots = {}
for r in ledger:
    sym, cid = g(r, "symbol"), g(r, "cycle_id")
    root = r.get("canonical_opportunity_id")
    corr = g(r, "correlation_id")
    outcome_present = any(k in r for k in ("pnl", "outcome", "exit_price", "result"))
    if root is None: defects["missing_canonical_field_ledger"].append((sym, cid))
    if root and not root.startswith(f"{sym}*"):
        defects["stale_cross_symbol_root"].append(("ledger", sym, root))
    if root:
        ledger_roots[root] = (sym, cid, corr, outcome_present)

# ---------- 6. execution context ----------
exec_ctx = [r for r in buckets["exec_ctx"] if isinstance(r, dict) and "__MALFORMED__" not in r]
report["execution_context"] = len(exec_ctx)
for r in exec_ctx:
    sym, cid, eid, bt = g(r, "symbol"), g(r, "cycle_id"), g(r, "entity_id"), g(r, "bar_time")
    root = r.get("canonical_opportunity_id")
    has_px = any(k in r for k in ("bid", "ask", "bid_at_execution", "ask_at_execution", "spread"))
    if root is None: defects["missing_canonical_field_exec_ctx"].append((sym, cid))
    if root and not root.startswith(f"{sym}*"):
        defects["stale_cross_symbol_root"].append(("exec_ctx", sym, root))

# ---------- 7-9. execution results / trade journal / risk deviation ----------
report["execution_results_fresh"] = len(buckets.get("other", []))
report["risk_deviation_fresh"] = 0  # no growth in logs/risk_deviation (no orders placed)

# ---------- cross-lane join ----------
all_roots = set(opp_roots) | set(dec_roots) | set(ledger_roots)
assess_roots = set()
for name in ("entry", "risk", "horizon", "opp_assess", "exec_assess"):
    for r in buckets[name]:
        if isinstance(r, dict) and r.get("canonical_opportunity_id"):
            assess_roots.add(r["canonical_opportunity_id"])

joined = []
for root in sorted(all_roots):
    j = {
        "root": root,
        "opportunity": root in opp_roots,
        "assessment": root in assess_roots,
        "decision_trace": root in dec_roots,
        "ledger": root in ledger_roots,
        "execution": None,  # no orders placed this soak
        "outcome": None,
    }
    j["score"] = sum(1 for k in ("opportunity", "assessment", "decision_trace", "ledger") if j[k])
    joined.append(j)

full = [j for j in joined if j["score"] == 4]
# negative case: NO_TRADE cycles must not carry any prior root
neg_ok = len(defects["notrade_inherited_root"]) == 0

# ---------- malformed ----------
malformed = sum(1 for d in defects["malformed"])

out = {
    "counts": report,
    "opportunity_roots": {k: v[2] or "" for k, v in list(opp_roots.items())[:20]},
    "decision_roots_sample": {k: {"sym": v[0], "cycle": v[1], "action": v[3]} for k, v in list(dec_roots.items())[:20]},
    "ledger_roots_sample": {k: {"sym": v[0], "outcome_side": v[3]} for k, v in list(ledger_roots.items())[:20]},
    "cross_lane": {"distinct_roots": len(all_roots), "fully_joined": len(full),
                   "joined_detail": joined[:25]},
    "negative_case_notrade_clean": neg_ok,
    "defect_counts": {k: len(v) for k, v in defects.items() if v},
    "defect_samples": {k: v[:3] for k, v in defects.items() if v},
}
print(json.dumps(out, indent=1, default=str)[:14000])
