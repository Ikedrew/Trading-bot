"""
Focused tests for the legacy ``shadow_runtime`` recovery-quarantine logging fix.

Requirement under test (``ShadowRuntime.recover`` in core/shadow/runtime.py):

    A persisted legacy OPEN that carries no ``record_lineage`` is STILL
    quarantined (fail-closed) and never resumed, but the per-record
    ``[SHADOW_RUNTIME_RECOVERY_QUARANTINE]`` error is suppressed for that one
    repeated case (a pre-governance stream can hold tens of thousands of them)
    and replaced by ONE aggregate summary line emitted after the recovery pass:

        [SHADOW_RUNTIME_RECOVERY_QUARANTINE_SUMMARY] legacy_unpinned_open_count=<n>

Every other distinct quarantine (e.g. an ``ObservabilityContractError`` for an
incompatible pinned lineage) must still log per record, governed OPENs must
still resume normally, and recovery must never rewrite persisted evidence.

Nothing here writes outside ``tmp_path``; no historical file is modified.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.shadow.persistence import ShadowEventWriter, load_events  # noqa: E402
from core.shadow.runtime import ShadowRuntime  # noqa: E402

SYMBOL = "EURUSD"
#: M5-grid aligned so it satisfies the ROOT-03 bar-grid rule.
BASE = (1_784_800_000 // 300) * 300
ROOT_ID = "EURUSD*1784800000*TWEEZER_TOP"

#: The exact fail-closed reason the high-volume legacy case produces.
UNPINNED_REASON = (
    "RECOVERED_LIFECYCLE_UNPINNED:shadow_runtime:"
    "no record_lineage on the persisted OPEN")


# ─── helpers ─────────────────────────────────────────────────────────────────


def _ctx(**overrides):
    """A governed opportunity context (mirrors the serialization-guard tests)."""
    ctx = {
        "canonical_opportunity_id": ROOT_ID,
        "observation_id": "obs-1",
        "entity_id": "EURUSD_1784800000",
        "symbol": SYMBOL,
        "cycle_id": 42,
        "bar_time_utc": BASE,
        "direction": "SELL",
        "pattern": "TWEEZER_TOP",
        "strategy": "REVERSAL",
        "score": 0.61,
        "regime": "RANGE",
        "h4_regime": "RANGE",
        "h1_bias": "BEARISH",
        "market_phase": "PULLBACK",
        "market_phase_confidence": 0.55,
        "bid": 1.10000,
        "ask": 1.10002,
        "structure": {
            "m5_candle_high": 1.10050,
            "m5_candle_low": 1.09980,
            "m15_nearest_support": None,
            "m15_nearest_resistance": 1.10100,
            "h1_last_swing_high": None,
            "h1_last_swing_low": None,
        },
        "eligible_horizons": ["SCALP"],
        "horizon_assessments": [
            {"horizon": "SCALP", "confidence": 0.6, "reasoning": "tight",
             "eligible": True},
        ],
        "v10_action": "ENTER_SHORT",
        "v10_rejection_stage": "",
        "v10_selected_horizon": "SCALP",
    }
    ctx.update(overrides)
    return ctx


def _legacy_open_line(trade_id: str) -> str:
    """One persisted pre-governance OPEN — deliberately WITHOUT record_lineage."""
    record = {
        "event_type": "OPEN",
        "schema_version": "shadow_runtime_v1",
        "event_id": f"{trade_id}:OPEN",
        "shadow_trade_id": trade_id,
        "canonical_opportunity_id": ROOT_ID,
        "observation_id": "obs-legacy",
        "symbol": SYMBOL,
        "horizon": "SCALP",
        "entry_market_time_utc_epoch_s": BASE,
        "lifecycle_initial": {},
        "simulation_assumptions": {"timeout_bars": 9},
        "construction": {
            "direction": "SELL", "entry_price": 1.1,
            "stop_loss": 1.2, "take_profit": 1.0,
        },
    }
    assert "record_lineage" not in record
    return json.dumps(record, separators=(",", ":"))


def _write_legacy_stream(base: Path, count: int) -> Path:
    path = base / SYMBOL / "2026-07-20.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [_legacy_open_line(f"nshadow_legacy_{i}") for i in range(count)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ─── 1–4 + 7: legacy unpinned OPENs ──────────────────────────────────────────


def test_legacy_unpinned_opens_all_quarantined_with_single_summary(tmp_path, caplog):
    """Many legacy OPENs -> all quarantined, none resumed, ONE summary, exact
    count, and zero writes to the persisted evidence."""
    n = 50
    _write_legacy_stream(tmp_path, n)
    before = _digest_tree(tmp_path)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        rt = ShadowRuntime(writer=ShadowEventWriter(str(tmp_path)))

    # (1) every legacy OPEN is quarantined, with the exact fail-closed reason.
    quarantined = rt.quarantined_ids()
    assert len(quarantined) == n
    assert set(quarantined) == {f"nshadow_legacy_{i}" for i in range(n)}
    assert set(quarantined.values()) == {UNPINNED_REASON}

    # quarantined_ids() still returns a defensive copy (contents preserved).
    snapshot = rt.quarantined_ids()
    snapshot.clear()
    assert len(rt.quarantined_ids()) == n

    # (2) none are resumed into _active.
    assert rt.active_ids() == []
    assert rt._active == {}

    # (3) exactly one aggregate summary; NO per-record error flood.
    summaries = _summary_records(caplog)
    assert len(summaries) == 1
    assert _per_record_records(caplog) == []

    # (4) the count is correct.
    assert f"legacy_unpinned_open_count={n}" in summaries[0].getMessage()

    # (7) recovery rewrote nothing on disk; the legacy records are untouched.
    assert _digest_tree(tmp_path) == before
    replayed = load_events(str(tmp_path))
    assert len(replayed) == n
    assert all(rec.get("record_lineage") is None for rec in replayed)


def test_no_summary_when_there_is_no_legacy_backlog(tmp_path, caplog):
    """A clean (empty) recovery emits neither a summary nor a per-record error."""
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        rt = ShadowRuntime(writer=ShadowEventWriter(str(tmp_path)))
    assert rt.active_ids() == []
    assert rt.quarantined_ids() == {}
    assert _summary_records(caplog) == []
    assert _per_record_records(caplog) == []


# ─── 5: a different quarantine still logs normally ───────────────────────────


def test_incompatible_lineage_quarantine_still_logs_per_record(tmp_path, caplog):
    """A distinct ObservabilityContractError quarantine is NOT suppressed."""
    # Setup: build a governed stream, then (test setup only) corrupt the
    # persisted OPEN's pinned lineage so recovery takes the contract-error path.
    ShadowRuntime(writer=ShadowEventWriter(str(tmp_path))).handle_opportunity(_ctx())
    path = next(Path(tmp_path).rglob("*.jsonl"))
    rewritten = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("event_type") == "OPEN":
            rec["record_lineage"]["schema_generation"] = 1
        rewritten.append(json.dumps(rec, separators=(",", ":")))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    before = _digest_tree(tmp_path)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        rt = ShadowRuntime(writer=ShadowEventWriter(str(tmp_path)))

    assert rt.active_ids() == []
    reasons = list(rt.quarantined_ids().values())
    assert len(reasons) == 1
    assert "LIFECYCLE_LINEAGE_INCOMPATIBLE" in reasons[0]

    per_record = _per_record_records(caplog)
    assert len(per_record) == 1
    assert "LIFECYCLE_LINEAGE_INCOMPATIBLE" in per_record[0].getMessage()
    # No legacy unpinned case occurred -> no aggregate summary.
    assert _summary_records(caplog) == []
    assert _digest_tree(tmp_path) == before


# ─── 6: governed OPENs still recover normally ────────────────────────────────


def test_governed_open_with_valid_lineage_recovers_normally(tmp_path, caplog):
    """An OPEN with a valid pinned record_lineage resumes, with no quarantine."""
    first = ShadowRuntime(writer=ShadowEventWriter(str(tmp_path)))
    first.handle_opportunity(_ctx())
    tid = first.active_ids()[0]
    before = _digest_tree(tmp_path)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        resumed = ShadowRuntime(writer=ShadowEventWriter(str(tmp_path)))

    assert resumed.active_ids() == [tid]
    assert resumed.quarantined_ids() == {}
    assert _summary_records(caplog) == []
    assert _per_record_records(caplog) == []
    assert _digest_tree(tmp_path) == before



def _digest_tree(base: Path) -> dict[str, str]:
    """Content hash of every file under ``base`` (proves nothing was rewritten)."""
    return {
        str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


def _summary_records(caplog):
    return [r for r in caplog.records if SUMMARY_TOKEN in r.getMessage()]


def _per_record_records(caplog):
    return [r for r in caplog.records if PER_RECORD_TOKEN in r.getMessage()]


LOGGER_NAME = "core.shadow.runtime"
SUMMARY_TOKEN = "SHADOW_RUNTIME_RECOVERY_QUARANTINE_SUMMARY"
PER_RECORD_TOKEN = "[SHADOW_RUNTIME_RECOVERY_QUARANTINE]"
