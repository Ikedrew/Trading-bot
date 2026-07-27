"""Run all implemented research experiments and generate dashboard."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_engine.experiments.component_reward import run as run_q1
from research_engine.experiments.shadow_validation import run as run_q16
from research_engine.experiments.expected_value import run as run_q19
from research_engine.experiments.score_calibration import run as run_q20
from research_engine.experiments.research_runner import run_all as run_q2_q25
from research_engine.report_builder import generate_dashboard

print("Running Q1...")
r1 = run_q1()
print(f"  Q1: {r1.confidence} | best={r1.best_predictor}")

print("Running Q16...")
r16 = run_q16()
print(f"  Q16: matched={r16.matched_trades} | {r16.confidence}")

print("Running Q19...")
r19 = run_q19()
print(f"  Q19: EV={r19.expected_value:+.4f}R | {r19.edge_classification}")

print("Running Q20...")
r20 = run_q20()
print(f"  Q20: {r20.get('recommendation', '?')}")

print("Running Q2-Q15, Q17-Q18, Q21-Q25...")
results = run_q2_q25()
for qid, info in sorted(results.items()):
    print(f"  {qid}: {info['status']} (n={info.get('sample', '?')})")

print("\nGenerating dashboard...")
d = generate_dashboard()
print(f"\nDashboard: {d['summary']}")
print("\nAll questions:")
for qid in sorted(d["questions"].keys(), key=lambda x: int(x[1:])):
    q = d["questions"][qid]
    print(f"  {qid}: {q['status']:<14s} | {q.get('recommendation', '')}")

