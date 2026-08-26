import os, json
bp = r"C:\Users\ikues\Trading bot build\core\runtime\bar_provider.py"
src = open(bp, encoding="utf-8").read().splitlines()
bad = [(i, l.rstrip()[:78]) for i, l in enumerate(src, 1) if "print" in l and any(ord(c) > 127 for c in l)]
full = "\n".join(src)
cfg = open(r"C:\Users\ikues\Trading bot build\core\config.py", encoding="utf-8").read()
term_line = next((ln.strip() for ln in cfg.splitlines() if "MT5_TERMINAL_PATH =" in ln), "NOT FOUND")
res = {
    "bar_provider_non_ascii_print_lines": bad if bad else "NONE",
    "no_raw_arrow_token": "\u2192" not in full,
    "warn_text_present": "[WARN] MT5 FEED MAY BE FROZEN" in full,
    "warning_emoji_gone": "\u26a0" not in full,
    "config_shadow_gate_false": "SHADOW_RUNTIME_V2_ENABLED = False" in cfg,
    "config_legacy_shadow_false": "ENABLE_LEGACY_SHADOW_PIPELINE = False" in cfg,
    "config_mt5_terminal_path": term_line,
}
open(r"C:\Users\ikues\verify_report.txt", "w").write(json.dumps(res, ensure_ascii=True, indent=2))
print("WROTE", os.path.exists(r"C:\Users\ikues\verify_report.txt"))
