import os, time
LOGS = r"C:\Users\ikues\Trading bot build\logs"
cut = time.mktime((2026,8,26,20,0,0,0,0,0))
for d in ("trade_journal", "trade_truth", "research_shadow_trades", "risk_deviation", "protections"):
    p = os.path.join(LOGS, d)
    print(d, "EXISTS" if os.path.isdir(p) else "NO", end="  ")
    if os.path.isdir(p):
        fresh = []
        for _r, _dd, fs in os.walk(p):
            for f in fs:
                fp = os.path.join(_r, f)
                if os.path.getmtime(fp) > cut:
                    fresh.append(f)
        print("fresh-last2d:", fresh[:10], "count", len(fresh))
    else:
        print()
print("done")