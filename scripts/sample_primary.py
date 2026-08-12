import json
from pathlib import Path

shadow_dir = Path("logs/shadow_trades")
samples = []
for sym_dir in sorted(shadow_dir.iterdir()):
    if not sym_dir.is_dir(): continue
    for f in sorted(sym_dir.glob("*.jsonl")):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line: continue
            rec = json.loads(line)
            tid = rec.get("identity",{}).get("trade_id","")
            if tid.startswith("shadow_") and not tid.startswith("hshadow_"):
                samples.append(f"{tid[:45]}|eid={rec.get('identity',{}).get('entity_id','')[:20]}|cor={rec.get('identity',{}).get('correlation_id','')[:35]}|strat={rec.get('identity',{}).get('strategy_id','')[:15]}")
                if len(samples) >= 8: break
        if len(samples) >= 8: break
    if len(samples) >= 8: break

Path("scripts/primary_samples.txt").write_text("\n".join(samples), encoding="utf-8")
