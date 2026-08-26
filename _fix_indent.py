import re
p = r"C:\Users\ikues\Trading bot build\core\runtime\bar_provider.py"
lines = open(p, encoding="utf-8").read().splitlines(keepends=True)
fixed = []
for ln in lines:
    if "[FEED BLOCKED]" in ln and "print(f\"" in ln:
        ln = "                " + ln.lstrip()
    if "[BAR SLOW]" in ln and "print(f\"" in ln:
        ln = "                " + ln.lstrip()
    fixed.append(ln)
open(p, "w", encoding="utf-8").write("".join(fixed))
print("FIXED lines")
