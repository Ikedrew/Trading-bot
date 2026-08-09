import json
from pathlib import Path
r = json.loads(Path("reports/research/mt5_reconciliation_report.json").read_text(encoding="utf-8"))
nfx = r["non_fx_recovery"]
print("NON-FX RECOVERY:")
for d in nfx["details"]:
    bp = f"${d['broker_profit']:.2f}" if d["broker_profit"] is not None else "N/A"
    print(f"  {d['symbol']:8s} {d['trade_id']:18s} journal=${d['journal_pnl']:>14.2f}  broker={bp:>10}  {d['match_method']}  {d['confidence']}")
print()
print("BY ASSET CLASS:")
for k, v in r["by_asset_class"].items():
    print(f"  {k}: total={v['total']} matched={v['matched']} profit=${v.get('broker_profit',0):.2f}")
print()
print("BY SYMBOL:")
for k, v in sorted(r["by_symbol"].items(), key=lambda x: -x[1]["total"]):
    print(f"  {k:8s}: total={v['total']} matched={v['matched']} broker_profit=${v.get('broker_profit',0):.2f}")
