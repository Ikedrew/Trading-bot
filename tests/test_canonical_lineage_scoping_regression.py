"""
Focused regression — cross-symbol / cross-bar canonical lineage leakage is
IMPOSSIBLE through the live scanner scoping.

Origin: live_scanner kept `_canonical_opp_id` bound from a previous symbol/bar;
the historical ``"_canonical_opp_id" in dir()`` guards then forwarded a STALE
canonical lineage into Shadow opens and execution records. Fix under test:

    core/runtime/live_scanner.py — "Canonical lineage freshness guard"
        `_canonical_opp_id = ""` reset per symbol/bar BEFORE any downstream
        consumer, re-derived per pattern in the "CANONICAL LINEAGE" block.

Layers proven here:
    1. Source guard (AST): in live_scanner.py every READ of
       `_canonical_opp_id` is dominated by a RESET-to-empty statement — a
       stale binding can never legally survive into a consumer expression.
    2. Behavioural harness: the exact fixed scoping sequence drives REAL
       identity minting (`make_canonical_opportunity_id`), the REAL decision
       persistence boundary (`DecisionRecorder` -> ledger row), and the REAL
       Shadow branch adapter (`handle_live_opportunity_shadow`) with an
       in-memory shadow-runtime double.
           - Case A: opportunity followed by NO opportunity → second cycle
             must NOT inherit ID_A anywhere downstream.
           - Case B: two opportunities → ID_A and ID_B remain distinct and
             each cycle persists ONLY its own root.
           - Case C: Shadow lifecycle inherits the CURRENT canonical id;
             an empty ("lineage not established") id refuses to open.
    3. Leak-detection control: a deliberately un-reset scope DOES leak —
     proving this suite can detect the original bug shape if it regresses.

All objects are mocks/in-memory. No bot start-up, no Shadow enablement,
no MT5 connection, no trades, no log modification.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.identity.canonical import make_canonical_opportunity_id

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCANNER_PATH = PROJECT_ROOT / "core" / "runtime" / "live_scanner.py"

BAR_T1 = 1784800000          # one closed M5 bar
BAR_T2 = 1784800300          # the next closed M5 bar
ID_A = make_canonical_opportunity_id(symbol="EURUSD", bar_time=BAR_T1, pattern="TWEEZER_TOP")
ID_B = make_canonical_opportunity_id(symbol="GBPUSD", bar_time=BAR_T2, pattern="HAMMER")


# ═══ Layer 1 — source guard (AST; live_scanner is NOT imported) ═══════════════

def _scanner_reset_and_load_lines() -> tuple[list[int], list[int]]:
    """Line numbers of `_canonical_opp_id` reset-assignments vs reads."""
    tree = ast.parse(SCANNER_PATH.read_text(encoding="utf-8-sig"))
    resets: list[int] = []
    loads: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_canonical_opp_id" in targets and isinstance(node.value, ast.Constant):
                assert node.value.value == "", "reset must bind the empty string"
                resets.append(node.lineno)
        elif isinstance(node, ast.Name) and node.id == "_canonical_opp_id":
            if isinstance(node.ctx, ast.Load):
                loads.append(node.lineno)
    return resets, loads


class TestSourceGuardNoStaleBindingPossible:
    def test_every_read_is_dominated_by_a_reset(self):
        """
        Anti-leak invariant: no READ of _canonical_opp_id may occur before the
        LAST reset-to-empty statement. Positional dominance over the flat
        execution order of the scanner body means any value read downstream
        was either freshly reset ("" = lineage not established) or minted THIS
        symbol/bar. If the freshness guard is deleted or moved below a
        consumer, this fails.
        """
        resets, loads = _scanner_reset_and_load_lines()
        assert loads, "scanner lost its canonical lineage consumers?"
        # The per-pattern derivation reset AND the per-symbol-bar guard both exist
        assert len(resets) >= 2, (
            "expected BOTH the per-symbol-bar freshness guard and the "
            "per-pattern re-derivation reset to be present"
        )
        worst_read = min(loads)
        last_reset = max(resets)
        assert last_reset < worst_read, (
            f"stale-binding risk: a read of _canonical_opp_id at line "
            f"{worst_read} is not dominated by the last reset at {last_reset}"
        )

    def test_freshness_guard_precedes_decision_recorder_init_cycle(self):
        """The guard must fire BEFORE init_cycle creates the fresh decision."""
        src = SCANNER_PATH.read_text(encoding="utf-8-sig")
        lines = src.splitlines()
        resets, _ = _scanner_reset_and_load_lines()
        init_cycle_line = next(
            i for i, l in enumerate(lines, 1) if "_decision_recorder.init_cycle(" in l
        )
        guard = min(resets)
        assert guard < init_cycle_line


# ═══ Layer 2/3 — behavioural harness ══════════════════════════════════════════

class _FakeLedger:
    """In-memory stand-in for the decision-ledger sink."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, **kwargs) -> None:
        self.rows.append(kwargs)


class _PerCycleIdentityScope:
    """
    Mirror of the FIXED scanner scoping sequence for one symbol/bar iteration
    (core/runtime/live_scanner.py):
        begin_symbol_bar()      <-> 'Canonical lineage freshness guard'
        establish_lineage()     <-> 'CANONICAL LINEAGE' block
    Kept deliberately in lockstep with those blocks; Layer 1 pins the real file.
    """

    def __init__(self) -> None:
        # Simulates the stray binding a leaked global/function-scoped var would
        # carry across iterations when the guard is missing.
        self._canonical_opp_id = "<never-legitimately-set>"

    def begin_symbol_bar(self) -> None:
        self._canonical_opp_id = ""

    def establish_lineage(
        self, *, cycle_decision: dict, new_result: dict, symbol: str,
        bar_time: int, pattern: str,
    ) -> str:
        cid = ""
        try:
            cid = make_canonical_opportunity_id(
                symbol=symbol, bar_time=bar_time, pattern=pattern
            )
        except Exception:
            cid = ""
        if cid:
            cycle_decision["canonical_opportunity_id"] = cid
        new_result["canonical_opportunity_id"] = cid
        self._canonical_opp_id = cid
        return cid


class _LeakyScope(_PerCycleIdentityScope):
    """CONTROL: reproduces the PRE-FIX bug — no per-cycle reset."""

    def begin_symbol_bar(self) -> None:
        pass  # intentionally missing the freshness guard


class _BoundaryHarness:
    """Drives one full scanner-style cycle into the real persistence points."""

    def __init__(self) -> None:
        self.ledger = _FakeLedger()
        from core.runtime.decision_recorder import DecisionRecorder
        self.recorder = DecisionRecorder(self.ledger)

    def run_cycle(
        self, *, scope, symbol: str, cycle_id: int, bar_time: int,
        pattern: str | None, context_snapshot_id: str,
    ) -> dict:
        # Mirrors the scanner's per-symbol-bar entry sequence: the freshness
        # guard fires BEFORE anything else consumes identity state.
        scope.begin_symbol_bar()
        cycle_decision = self.recorder.init_cycle(
            symbol=symbol,
            cycle_id=cycle_id,
            regime="test",
            context_snapshot_id=context_snapshot_id,
            drawdown_pct=0.0,
            daily_loss_pct=0.0,
        )
        new_result: dict = {"action": "NO_TRADE", "pattern": pattern or ""}
        if pattern:
            scope.establish_lineage(
                cycle_decision=cycle_decision,
                new_result=new_result,
                symbol=symbol,
                bar_time=bar_time,
                pattern=pattern,
            )
        else:
            # No pattern -> lineage not established; whatever the scope still
            # holds is what the historical 'in dir()' guards would forward.
            new_result["canonical_opportunity_id"] = scope._canonical_opp_id
        cycle_decision["decision"] = "NO_TRADE"
        cycle_decision["reason"] = f"testing:{symbol}:{cycle_id}"
        cycle_decision["entity_id"] = f"{symbol}_{bar_time}" if pattern else ""
        self.recorder.finalize(cycle_start=time.time())
        return {
            "cycle_decision": dict(cycle_decision),
            "engine_result": dict(new_result),
            "scope_value": scope._canonical_opp_id,
        }


@pytest.fixture
def shadow_calls(monkeypatch):
    """In-memory shadow runtime double behind the REAL branch adapter."""
    seen: list[dict] = []

    class _RuntimeDouble:
        def handle_opportunity(self, ctx: dict) -> None:
            seen.append(ctx)

    import core.shadow.runtime as rt_mod
    monkeypatch.setattr(rt_mod, "get_shadow_runtime", lambda: _RuntimeDouble())
    return seen


def _call_real_shadow_adapter(
    *, canonical_opportunity_id: str, symbol: str, bar_time: int,
) -> None:
    """Invoke the REAL integration adapter used by live_scanner's gated branch."""
    from core.shadow.integration import handle_live_opportunity_shadow

    candle = SimpleNamespace(high=1.2345, low=1.2000)
    handle_live_opportunity_shadow(
        symbol=symbol,
        cycle_id=1,
        closed_time=int(bar_time),
        candles=[candle],
        closed_i=0,
        bid=1.2300,
        ask=1.2310,
        htf_context=None,
        new_result={
            "action": "NO_TRADE",
            "pattern": "TWEEZER_TOP",
            "side": "SELL",
            "score": 0.7,
        },
        horizon_result=None,
        canonical_opportunity_id=canonical_opportunity_id,
        entity_id=f"{symbol}_{bar_time}",
    )


class TestCaseA_NoOpportunityDoesNotInherit:
    def test_no_inheritance_of_previous_canonical_anywhere(self):
        harness = _BoundaryHarness()
        scope = _PerCycleIdentityScope()

        first = harness.run_cycle(
            scope=scope, symbol="EURUSD", cycle_id=1, bar_time=BAR_T1,
            pattern="TWEEZER_TOP", context_snapshot_id="CTX-A1",
        )
        assert first["scope_value"] == ID_A

        # Next evaluation: NO opportunity (pre-pattern gate block territory)
        second = harness.run_cycle(
            scope=scope, symbol="GBPUSD", cycle_id=2, bar_time=BAR_T2,
            pattern=None, context_snapshot_id="CTX-A2",
        )
        # -- scope layer --
        assert second["scope_value"] == ""
        # -- engine-result layer --
        assert second["engine_result"]["canonical_opportunity_id"] == ""
        assert ID_A not in second["engine_result"].values()
        # -- downstream PERSISTENCE boundary (decision ledger rows) --
        assert len(harness.ledger.rows) == 2
        row_a, row_b = harness.ledger.rows
        assert row_a["canonical_opportunity_id"] == ID_A
        assert row_b["canonical_opportunity_id"] == ""

    def test_control_leaky_scope_DOES_leak_proving_detector_sensitivity(self):
        """Without the freshness reset, ID_A leaks — the failure this guards."""
        harness = _BoundaryHarness()
        leaky = _LeakyScope()
        harness.run_cycle(
            scope=leaky, symbol="EURUSD", cycle_id=1, bar_time=BAR_T1,
            pattern="TWEEZER_TOP", context_snapshot_id="CTX-L1",
        )
        leaked = harness.run_cycle(
            scope=leaky, symbol="GBPUSD", cycle_id=2, bar_time=BAR_T2,
            pattern=None, context_snapshot_id="CTX-L2",
        )
        # The bug shape lives in the raw variable / consumer-forwarded layers
        # (what 'in dir()' guards would forward to shadow & execution), not in
        # the decision row — init_cycle's fresh dict masks THAT boundary.
        assert leaked["scope_value"] == ID_A                                 # stray binding survives
        assert leaked["engine_result"]["canonical_opportunity_id"] == ID_A   # forwarded downstream!


class TestCaseB_TwoOpportunitiesStayDistinct:
    def test_ids_distinct_each_boundary_carries_only_its_own_root(self):
        harness = _BoundaryHarness()
        scope = _PerCycleIdentityScope()

        opp_a = harness.run_cycle(
            scope=scope, symbol="EURUSD", cycle_id=10, bar_time=BAR_T1,
            pattern="TWEEZER_TOP", context_snapshot_id="CTX-B1",
        )
        opp_b = harness.run_cycle(
            scope=scope, symbol="GBPUSD", cycle_id=11, bar_time=BAR_T2,
            pattern="HAMMER", context_snapshot_id="CTX-B2",
        )

        assert opp_a["scope_value"] == ID_A
        assert opp_b["scope_value"] == ID_B
        assert ID_A != ID_B

        row_a, row_b = harness.ledger.rows
        assert row_a["canonical_opportunity_id"] == ID_A
        assert row_b["canonical_opportunity_id"] == ID_B
        assert row_b["canonical_opportunity_id"] != ID_A
        assert row_a["canonical_opportunity_id"] != ID_B

        assert opp_a["engine_result"]["canonical_opportunity_id"] == ID_A
        assert opp_b["engine_result"]["canonical_opportunity_id"] == ID_B


class TestCaseC_ShadowInheritsCurrentCanonicalOnly:
    def test_shadow_lifecycle_receives_current_root_never_a_prior_one(
        self, shadow_calls,
    ):
        harness = _BoundaryHarness()
        scope = _PerCycleIdentityScope()

        # Cycle 1: opportunity A -> shadow must carry EXACTLY ID_A
        harness.run_cycle(
            scope=scope, symbol="EURUSD", cycle_id=20, bar_time=BAR_T1,
            pattern="TWEEZER_TOP", context_snapshot_id="CTX-C1",
        )
        _call_real_shadow_adapter(
            canonical_opportunity_id=scope._canonical_opp_id,
            symbol="EURUSD",
            bar_time=BAR_T1,
        )
        assert [c["canonical_opportunity_id"] for c in shadow_calls] == [ID_A]

        # Cycle 2: NO opportunity -> adapter invoked with the reset (empty) id.
        harness.run_cycle(
            scope=scope, symbol="GBPUSD", cycle_id=21, bar_time=BAR_T2,
            pattern=None, context_snapshot_id="CTX-C2",
        )
        _call_real_shadow_adapter(
            canonical_opportunity_id=scope._canonical_opp_id,
            symbol="GBPUSD",
            bar_time=BAR_T2,
        )
        # Rule 17: empty canonical root => NO simulation may open, and the
        # prior ID_A must NOT have been inherited by any call.
        assert [c["canonical_opportunity_id"] for c in shadow_calls] == [ID_A]

        # Cycle 3: opportunity B -> next shadow carries EXACTLY ID_B
        harness.run_cycle(
            scope=scope, symbol="GBPUSD", cycle_id=22, bar_time=BAR_T2,
            pattern="HAMMER", context_snapshot_id="CTX-C3",
        )
        _call_real_shadow_adapter(
            canonical_opportunity_id=scope._canonical_opp_id,
            symbol="GBPUSD",
            bar_time=BAR_T2,
        )
        opened_roots = [c["canonical_opportunity_id"] for c in shadow_calls]
        assert opened_roots == [ID_A, ID_B]
        # Identity of the last context is fully B's, never blended with A's:
        assert shadow_calls[-1]["symbol"] == "GBPUSD"
        assert shadow_calls[-1]["entity_id"] == f"GBPUSD_{BAR_T2}"
        assert shadow_calls[-1]["canonical_opportunity_id"] == ID_B
