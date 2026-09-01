"""
Phase 5 — Capture Contract Tests.

Proves the ACTUAL capture behavior of the real production components
(no mocked reimplementation):

  1. MarketContextBuilder receives and MarketContextPersistence persists:
     symbol, cycle_id, entity_id, bar_time + existing market-context fields.
  2. Canonical-opportunity TIMING rule:
       - before the opportunity engine establishes a canonical root,
         canonical_opportunity_id stays "" (never fabricated by the
         market-context layer);
       - once the engine mints the root, downstream records carry that
         exact root verbatim.
  3. create_opportunity() carries a caller-supplied canonical root VERBATIM.
  4. DecisionRecorder.init_cycle() preserves the supplied root.
  5. The decision-audit persistence boundary (ledger record kwargs) carries
     the same root.
  6. Deterministic ID_A / ID_B prove two opportunities cannot
     cross-contaminate one another.
  7. Negative case: ID_A cannot leak into the next cycle when no canonical
     opportunity exists.

Constraints honored: tmp dirs + in-memory doubles only. No MT5, no orders,
no bot launch, no historical/production data touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.decision_ledger import DecisionOutcome
from core.identity.canonical import make_canonical_opportunity_id
from core.market_context.builder import MarketContextBuilder
from core.market_context.persistence import MarketContextPersistence
from core.opportunity.factory import create_opportunity
from core.runtime.decision_recorder import DecisionRecorder
from strategy.signals import Side, Signal

SYMBOL = "EURUSD_SB"
BAR_TIME = 1784800000
CYCLE_ID = 7
ID_A = make_canonical_opportunity_id(symbol=SYMBOL, bar_time=BAR_TIME, pattern="TWEEZER_TOP")
ID_B = make_canonical_opportunity_id(symbol=SYMBOL, bar_time=BAR_TIME + 300, pattern="HAMMER")


# ─── in-memory doubles ────────────────────────────────────────────────────────

class _FakePersistence:
    """In-memory double of MarketContextPersistence capturing persist() calls."""

    def __init__(self):
        self.calls: list[dict] = []

    def persist(self, context_dict, *, entity_id="", correlation_id="", bar_time=None):
        self.calls.append(
            {
                "context_dict": dict(context_dict),
                "entity_id": entity_id,
                "correlation_id": correlation_id,
                "bar_time": bar_time,
            }
        )


class _FakeLedger:
    """In-memory double of the decision ledger capturing record() kwargs."""

    def __init__(self):
        self.records: list[dict] = []

    def record(self, **kwargs):
        self.records.append(kwargs)


def _make_signal(pattern: str = "TWEEZER_TOP", bar_time: int = BAR_TIME) -> Signal:
    return Signal(pattern=pattern, side=Side.SELL, bar_index=5, bar_time=bar_time, confidence=0.9)


def _init_cycle(recorder, cycle_id, snapshot, root=...):
    kwargs = dict(
        symbol=SYMBOL, cycle_id=cycle_id, regime="TRENDING",
        context_snapshot_id=snapshot, drawdown_pct=0.0, daily_loss_pct=0.0,
    )
    if root is not ...:
        kwargs["canonical_opportunity_id"] = root
    return recorder.init_cycle(**kwargs)


# ─── 1. market-context capture + persistence boundary ────────────────────────

class TestMarketContextCapture:
    def test_builder_captures_symbol_cycle_entity_bar_time(self):
        fake = _FakePersistence()
        builder = MarketContextBuilder(symbol=SYMBOL, persistence=fake)
        ctx = builder.build(cycle_id=CYCLE_ID, current_time_s=float(BAR_TIME))

        assert ctx.symbol == SYMBOL
        assert ctx.cycle_id == CYCLE_ID
        assert ctx.entity_id == f"{SYMBOL}_{BAR_TIME}"
        assert ctx.bar_time == float(BAR_TIME)

    def test_persisted_record_carries_identity_and_market_fields(self):
        fake = _FakePersistence()
        builder = MarketContextBuilder(symbol=SYMBOL, persistence=fake)
        builder.build(cycle_id=CYCLE_ID, current_time_s=float(BAR_TIME))

        assert fake.calls, "material first build must be persisted"
        rec = fake.calls[0]["context_dict"]

        # identity fields
        assert rec["symbol"] == SYMBOL
        assert rec["cycle_id"] == CYCLE_ID
        assert rec["entity_id"] == f"{SYMBOL}_{BAR_TIME}"
        assert rec["bar_time"] == float(BAR_TIME)
        # existing market-context fields survive the capture boundary.
        # (schema_version is stamped by the REAL persistence layer and is
        # asserted in test_local_jsonl_persistence_writes_captured_record;
        # this in-memory double sits above that stamp point.)
        for key in ("regime", "phase", "direction", "h4", "h1", "m15", "m5"):
            assert key in rec

    def test_local_jsonl_persistence_writes_captured_record(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # never touch real logs/
        MarketContextPersistence().persist(
            {"symbol": SYMBOL, "cycle_id": CYCLE_ID},
            entity_id=f"{SYMBOL}_{BAR_TIME}",
            bar_time=BAR_TIME,
            correlation_id="COR-TEST-0001",
        )
        files = list((Path("logs/market_context") / SYMBOL).glob("*.jsonl"))
        assert files, "JSONL row must exist under logs/market_context/<symbol>/"
        rec = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
        assert rec["symbol"] == SYMBOL
        assert rec["cycle_id"] == CYCLE_ID
        assert rec["entity_id"] == f"{SYMBOL}_{BAR_TIME}"
        assert rec["bar_time"] == BAR_TIME
        assert rec["correlation_id"] == "COR-TEST-0001"
        assert rec["schema_version"] == "market_context_v1"


# ─── 2. canonical timing rule ─────────────────────────────────────────────────

class TestCanonicalTiming:
    def test_market_context_layer_never_fabricates_canonical_root(self):
        """Pre-engine capture: canonical root must NOT exist yet, must not be minted."""
        fake = _FakePersistence()
        builder = MarketContextBuilder(symbol=SYMBOL, persistence=fake)
        ctx = builder.build(cycle_id=CYCLE_ID, current_time_s=float(BAR_TIME))

        assert getattr(ctx, "canonical_opportunity_id", "") == ""
        assert fake.calls, "first build must persist"
        # the persistence layer may carry through an empty root but must
        # never CREATE one
        assert fake.calls[0]["context_dict"].get("canonical_opportunity_id", "") == ""

    def test_after_engine_mints_root_downstream_carries_exact_root(self):
        """Post-mint: opportunity -> decision -> ledger all carry the exact root."""
        root = make_canonical_opportunity_id(
            symbol=SYMBOL, bar_time=BAR_TIME, pattern="TWEEZER_TOP"
        )
        assert root == ID_A

        opp = create_opportunity(
            signal=_make_signal(), symbol=SYMBOL, cycle_id=CYCLE_ID,
            canonical_opportunity_id=root,
        )
        assert opp.canonical_opportunity_id == root

        ledger = _FakeLedger()
        recorder = DecisionRecorder(ledger)
        _init_cycle(recorder, CYCLE_ID, "SNAP-1", root=opp.canonical_opportunity_id)
        recorder.decision["decision"] = DecisionOutcome.NO_TRADE
        recorder.decision["reason"] = "phase5_test"
        recorder.finalize(cycle_start=0.0)

        assert len(ledger.records) == 1
        assert ledger.records[0]["canonical_opportunity_id"] == root


# ─── 3. opportunity factory verbatim passthrough ──────────────────────────────

class TestOpportunityVerbatimRoot:
    def test_caller_supplied_root_passes_verbatim(self):
        opp = create_opportunity(
            signal=_make_signal(), symbol=SYMBOL, cycle_id=CYCLE_ID,
            canonical_opportunity_id=ID_A,
        )
        # verbatim: the caller-supplied string is exactly what lands on the
        # record — never re-derived, never mutated
        assert opp.canonical_opportunity_id == ID_A

    def test_without_caller_root_factory_mints_deterministic_root(self):
        opp = create_opportunity(signal=_make_signal(), symbol=SYMBOL, cycle_id=CYCLE_ID)
        assert opp.canonical_opportunity_id == ID_A
        assert opp.opportunity_id == ID_A


# ─── 4. decision recorder preserves supplied root ─────────────────────────────

class TestDecisionRecorderRoot:
    def test_init_cycle_preserves_supplied_root(self):
        recorder = DecisionRecorder(_FakeLedger())
        d = _init_cycle(recorder, CYCLE_ID, "SNAP-1", root=ID_A)
        assert d["canonical_opportunity_id"] == ID_A

    def test_finalize_persists_same_root_to_ledger(self):
        ledger = _FakeLedger()
        recorder = DecisionRecorder(ledger)
        _init_cycle(recorder, CYCLE_ID, "SNAP-2", root=ID_A)
        recorder.decision["entity_id"] = f"{SYMBOL}_{BAR_TIME}"
        recorder.decision["decision"] = DecisionOutcome.NO_TRADE
        recorder.decision["reason"] = "phase5_test"
        recorder.finalize(cycle_start=0.0)

        assert ledger.records[0]["canonical_opportunity_id"] == ID_A
        assert ledger.records[0]["entity_id"] == f"{SYMBOL}_{BAR_TIME}"


# ─── 5. no cross-contamination between two opportunities ──────────────────────

class TestNoCrossContamination:
    def test_two_opportunities_keep_their_own_roots(self):
        opp_a = create_opportunity(
            signal=_make_signal(pattern="TWEEZER_TOP", bar_time=BAR_TIME),
            symbol=SYMBOL, cycle_id=CYCLE_ID, canonical_opportunity_id=ID_A,
        )
        opp_b = create_opportunity(
            signal=_make_signal(pattern="HAMMER", bar_time=BAR_TIME + 300),
            symbol=SYMBOL, cycle_id=CYCLE_ID + 1, canonical_opportunity_id=ID_B,
        )
        assert opp_a.canonical_opportunity_id == ID_A
        assert opp_b.canonical_opportunity_id == ID_B
        assert opp_a.canonical_opportunity_id != opp_b.canonical_opportunity_id

    def test_decision_cycles_keep_their_own_roots(self):
        ledger = _FakeLedger()
        recorder = DecisionRecorder(ledger)

        for cid, snap, root in ((CYCLE_ID, "SNAP-A", ID_A), (CYCLE_ID + 1, "SNAP-B", ID_B)):
            _init_cycle(recorder, cid, snap, root=root)
            recorder.decision["decision"] = DecisionOutcome.NO_TRADE
            recorder.decision["reason"] = "phase5_cycle"
            recorder.finalize(cycle_start=0.0)

        assert [r["canonical_opportunity_id"] for r in ledger.records] == [ID_A, ID_B]


# ─── 6. negative case: no opportunity in next cycle ───────────────────────────

class TestNoLeakWithoutOpportunity:
    def test_no_canonical_root_next_cycle_stays_empty(self):
        """ID_A must not leak into a cycle where the engine found no opportunity."""
        ledger = _FakeLedger()
        recorder = DecisionRecorder(ledger)

        # cycle N: engine established ID_A
        _init_cycle(recorder, CYCLE_ID, "SNAP-A", root=ID_A)
        recorder.decision["decision"] = DecisionOutcome.NO_TRADE
        recorder.decision["reason"] = "with_opportunity"
        recorder.finalize(cycle_start=0.0)

        # cycle N+1: NO opportunity -> caller supplies nothing
        _init_cycle(recorder, CYCLE_ID + 1, "SNAP-B")
        assert recorder.decision["canonical_opportunity_id"] == ""

        recorder.decision["decision"] = DecisionOutcome.NO_TRADE
        recorder.decision["reason"] = "no_signal"
        recorder.finalize(cycle_start=0.0)

        assert ledger.records[-1]["canonical_opportunity_id"] == ""
        assert all(r["canonical_opportunity_id"] != ID_A for r in ledger.records[1:])

    def test_market_context_after_root_still_not_fabricated(self):
        """The market-context layer never back-fills an earlier root on later cycles."""
        fake = _FakePersistence()
        builder = MarketContextBuilder(symbol=SYMBOL, persistence=fake)
        builder.build(cycle_id=CYCLE_ID, current_time_s=float(BAR_TIME))
        # engine has minted ID_A in between — the context builder must not
        # adopt or fabricate it on a subsequent build
        ctx2 = builder.build(cycle_id=CYCLE_ID + 1, current_time_s=float(BAR_TIME + 300))
        assert getattr(ctx2, "canonical_opportunity_id", "") == ""


