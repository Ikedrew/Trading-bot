"""READ-ONLY live smoke — Research Engine AWS credential resolution (Step 3).

Proves that setting ONLY RESEARCH_AWS_PROFILE (NOT AWS_PROFILE) authenticates
the Research Engine S3 data source to account 179512357189 and reads real
evidence (trade_truth / decision_trace / shadow_runtime) plus canonical shadow
ingestion through the shared layer. No writes, no AWS_PROFILE dependency.

Run:  $env:RESEARCH_AWS_PROFILE="trading-bot-new"; python _smoke_research_auth_tmp.py
"""

from __future__ import annotations

import json
import os

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        FAILURES.append(name)


# ── environment hygiene: ONLY the Research Engine config may drive this ──
check("AWS_PROFILE unset (not required)", not os.environ.get("AWS_PROFILE"))
check(
    "RESEARCH_AWS_PROFILE set",
    os.environ.get("RESEARCH_AWS_PROFILE") == "trading-bot-new",
    f"(got {os.environ.get('RESEARCH_AWS_PROFILE')!r})",
)

from research_engine.data_access.s3_source import (  # noqa: E402
    S3ResearchDataSource,
)

src = S3ResearchDataSource()
check(
    "source.research_profile == trading-bot-new",
    src.research_profile == "trading-bot-new",
    f"(got {src.research_profile!r})",
)
check("canonical bucket trading-bot-v10-data", src.bucket == "trading-bot-v10-data",
      f"(got {src.bucket!r})")
check("canonical region eu-west-2", src._region == "eu-west-2", f"(got {src._region!r})")

# ── account identity via the same explicit profile ──────────────────────
import boto3  # noqa: E402

sess = boto3.Session(profile_name=src.research_profile, region_name=src._region)
ident = sess.client("sts", region_name=src._region).get_caller_identity()
check("identity account == 179512357189", ident["Account"] == "179512357189",
      f"(got {ident['Account']} arn={ident['Arn']})")

# ── real dataset reads through the sanctioned layer ─────────────────────
truth = src.read_dataset("trade_truth")
trace = src.read_dataset("decision_trace")
check("trade_truth loaded", len(truth) > 0, f"(n={len(truth)})")
check("decision_trace loaded", len(trace) > 0, f"(n={len(trace)})")

# ── canonical shadow ingestion still builds real nshadow_* lifecycles ───
from research_engine.data_access.shadow_runtime_ingestion import (  # noqa: E402
    ingest_completed_shadow_trades,
)

shadows = ingest_completed_shadow_trades()
nshadow = [
    r for r in shadows
    if str(r.get("identity", {}).get("shadow_trade_id", "")).startswith("nshadow_")
]
check("shadow_runtime_v1 completed lifecycles", len(shadows) > 0,
      f"(completed={len(shadows)})")
check(">= 2 real nshadow_* lifecycles", len(nshadow) >= 2, f"(n={len(nshadow)})")
if nshadow:
    latest = nshadow[-1]
    print("SAMPLE latest nshadow record:", json.dumps(latest)[:700])

print()
if FAILURES:
    print(f"SMOKE RESULT: FAIL ({len(FAILURES)} checks failed): {FAILURES}")
    raise SystemExit(1)
print("SMOKE RESULT: PASS — research authenticated via RESEARCH_AWS_PROFILE only")