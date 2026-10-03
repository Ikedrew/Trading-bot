"""Test-only fake candidate adapter (Block 1 fixture)."""

from __future__ import annotations

from core.shadow.candidate_models import TreatmentBar, TreatmentResult


class FakeBarCountAdapter:
    """Deterministic trivial adapter: counts bars, terminals after N."""

    CANDIDATE_ID = "FAKE_CANDIDATE"
    POLICY_ID = "FAKE_POLICY_V1"
    TREATMENT_HASH = "fake_treatment_hash_v1"

    def __init__(self, terminal_after_bars: int = 3) -> None:
        self._n = int(terminal_after_bars)

    def initialize(self, *, entry_geometry: dict) -> dict:
        return {"bars_seen": 0, "terminal_after": self._n}

    def on_bar(self, *, entry_geometry: dict, risk_distance: float,
               direction: str, bar: TreatmentBar,
               prior_state: dict) -> TreatmentResult:
        seen = int(dict(prior_state or {}).get("bars_seen", 0)) + 1
        state = {"bars_seen": seen,
                 "terminal_after": int(dict(prior_state or {}).get(
                     "terminal_after", self._n))}
        if seen >= int(state["terminal_after"]):
            return TreatmentResult(treatment_state=state, terminal=True,
                                   exit_reason="fake_bar_count",
                                   exit_price=float(bar.bar_close),
                                   diagnostic="FAKE_TERMINAL")
        return TreatmentResult(treatment_state=state, terminal=False,
                               diagnostic="FAKE_PROGRESS")
