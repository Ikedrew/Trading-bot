"""
Market Context Builder — Produces unified MarketContext per cycle.

Reads existing TimeframeCache (HTFContext) and EngineState to produce
a single frozen MarketContext. Does NOT call MT5. Does NOT influence decisions.

Phase 1: Observational only — builds and optionally persists.
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import replace
from typing import Any

from core.market_context.models import (
    Direction,
    H1Summary,
    H4Summary,
    M15Summary,
    M5Summary,
    MacroSummary,
    MonthlySummary,
    WeeklySummary,
    DailySummary,
    MarketContext,
    Phase,
    Regime,
)
from core.market_context.change_detector import ChangeDetector
from core.market_context.conflict_resolver import ConflictResolver
from core.market_context.persistence import MarketContextPersistence

logger = logging.getLogger(__name__)


class MarketContextBuilder:
    """
    Per-symbol builder that produces MarketContext each cycle.

    Lifecycle:
        1. Created once per symbol at scanner init
        2. build() called once per M5 cycle
        3. Returns frozen MarketContext
        4. Persists on material change only
    """

    def __init__(
        self,
        symbol: str,
        persistence: MarketContextPersistence | None = None,
    ) -> None:
        self._symbol = symbol
        self._persistence = persistence or MarketContextPersistence()
        self._change_detector = ChangeDetector()
        self._conflict_resolver = ConflictResolver()
        self._previous: MarketContext | None = None

    @property
    def previous_context(self) -> MarketContext | None:
        """Last produced MarketContext (for diagnostics)."""
        return self._previous

    def build(
        self,
        *,
        htf_context: Any = None,
        candles: Any = None,
        closed_i: int = 0,
        engine_state: Any = None,
        cycle_id: int = 0,
        current_time_s: float = 0.0,
        current_price: float = 0.0,
    ) -> MarketContext:
        """
        Produce one MarketContext for this cycle.

        Never raises — returns neutral context on any failure.
        Never modifies engine_state or candles.
        """
        try:
            return self._build_impl(
                htf_context=htf_context,
                engine_state=engine_state,
                cycle_id=cycle_id,
                current_time_s=current_time_s,
            )
        except Exception as exc:
            logger.debug("[MARKET_CONTEXT_BUILD_FAIL] symbol=%s error=%s", self._symbol, exc)
            return self._neutral_context(cycle_id, current_time_s)

    def _build_impl(
        self,
        *,
        htf_context: Any,
        engine_state: Any,
        cycle_id: int,
        current_time_s: float,
    ) -> MarketContext:
        """Internal build logic. May raise — caught by build()."""

        # ─── EXTRACT MACRO (MN/W1/D1) ────────────────────────────────
        macro = self._extract_macro(htf_context)

        # ─── EXTRACT H4 ──────────────────────────────────────────────
        h4 = self._extract_h4(htf_context)

        # ─── EXTRACT H1 ──────────────────────────────────────────────
        h1 = self._extract_h1(htf_context)

        # ─── EXTRACT M15 ─────────────────────────────────────────────
        m15 = self._extract_m15(htf_context)

        # ─── EXTRACT M5 ──────────────────────────────────────────────
        m5 = self._extract_m5(engine_state)

        # ─── RESOLVE DIRECTION ────────────────────────────────────────
        direction, dir_conf, conflict, conflict_desc, resolution = (
            self._conflict_resolver.resolve(h4, h1, m15, m5)
        )

        # ─── CLASSIFY REGIME ──────────────────────────────────────────
        regime, regime_conf = self._classify_regime(h4)

        # ─── CLASSIFY PHASE ──────────────────────────────────────────
        phase, phase_conf = self._classify_phase(h1, m5)

        # ─── COMPUTE TRADABILITY ──────────────────────────────────────
        tradability = self._compute_tradability(h4, h1, m15, m5)

        # ─── COMPUTE ALIGNMENT ────────────────────────────────────────
        alignment = self._compute_alignment(h4, h1, m5)

        # ─── BUILD CONTEXT ────────────────────────────────────────────
        ctx = MarketContext(
            symbol=self._symbol,
            cycle_id=cycle_id,
            timestamp_utc=current_time_s,
            direction=direction,
            direction_confidence=dir_conf,
            regime=regime,
            regime_confidence=regime_conf,
            phase=phase,
            phase_confidence=phase_conf,
            tradability_score=tradability,
            alignment_score=alignment,
            macro=macro,
            h4=h4,
            h1=h1,
            m15=m15,
            m5=m5,
            conflict_detected=conflict,
            conflict_description=conflict_desc,
            resolution_method=resolution,
        )

        # ─── CHANGE DETECTION + PERSISTENCE ──────────────────────────
        is_material = self._change_detector.is_material(ctx, self._previous)
        change_reason = ""
        if is_material:
            change_reason = self._change_detector.describe_change(ctx, self._previous)

        ctx = replace(ctx, is_material_change=is_material, change_reason=change_reason)

        # Persist on material change
        if is_material:
            try:
                from core import config as _cfg
                if getattr(_cfg, "MARKET_CONTEXT_PERSISTENCE_ENABLED", True):
                    self._persistence.persist(ctx.to_dict())
            except Exception:
                pass  # Persistence failure must never affect runtime

        self._previous = ctx
        return ctx

    # ─── EXTRACTION HELPERS ───────────────────────────────────────────────────

    def _extract_macro(self, htf_context: Any) -> MacroSummary:
        """Extract macro (MN/W1/D1) summary from HTFContext.macro. Returns empty on failure."""
        if htf_context is None:
            return MacroSummary()
        macro_snap = getattr(htf_context, "macro", None)
        if macro_snap is None:
            return MacroSummary()
        return MacroSummary(
            monthly=MonthlySummary(
                trend=getattr(macro_snap, "monthly_trend", "") or "",
                trend_strength=getattr(macro_snap, "monthly_trend_strength", 0.0),
                classification=getattr(macro_snap, "monthly_classification", "") or "",
            ),
            weekly=WeeklySummary(
                trend=getattr(macro_snap, "weekly_trend", "") or "",
                trend_strength=getattr(macro_snap, "weekly_trend_strength", 0.0),
                swing_high=getattr(macro_snap, "weekly_swing_high", 0.0),
                swing_low=getattr(macro_snap, "weekly_swing_low", 0.0),
                bos_level=getattr(macro_snap, "weekly_bos_level", 0.0),
                range_position=getattr(macro_snap, "weekly_range_position", 0.0),
            ),
            daily=DailySummary(
                bias=getattr(macro_snap, "daily_bias", "") or "",
                bias_strength=getattr(macro_snap, "daily_bias_strength", 0.0),
                swing_high=getattr(macro_snap, "daily_swing_high", 0.0),
                swing_low=getattr(macro_snap, "daily_swing_low", 0.0),
                range_position=getattr(macro_snap, "daily_range_position", 0.0),
                atr_ratio=getattr(macro_snap, "daily_atr_ratio", 1.0),
            ),
        )

    def _extract_h4(self, htf_context: Any) -> H4Summary:
        """Extract H4 summary from HTFContext. Returns neutral on failure."""
        if htf_context is None:
            return H4Summary()
        regime_snap = getattr(htf_context, "regime", None)
        if regime_snap is None:
            return H4Summary()
        classification = getattr(regime_snap, "classification", None)
        regime_str = classification.value if classification and hasattr(classification, "value") else "TRANSITIONAL"
        return H4Summary(
            regime=regime_str,
            confidence=getattr(regime_snap, "confidence", 0.0),
            trend_bias=getattr(regime_snap, "trend_bias", "NEUTRAL") or "NEUTRAL",
            trend_strength=getattr(regime_snap, "trend_strength", 0.0),
            atr_ratio=getattr(regime_snap, "atr_ratio", 1.0),
        )

    def _extract_h1(self, htf_context: Any) -> H1Summary:
        """Extract H1 summary from HTFContext. Returns neutral on failure."""
        if htf_context is None:
            return H1Summary()
        bias_snap = getattr(htf_context, "bias", None)
        if bias_snap is None:
            return H1Summary()
        direction = getattr(bias_snap, "direction", None)
        dir_str = direction.value if direction and hasattr(direction, "value") else "NEUTRAL"
        return H1Summary(
            direction=dir_str,
            confidence=getattr(bias_snap, "confidence", 0.0),
            swing_structure=getattr(bias_snap, "swing_structure", "MIXED") or "MIXED",
            ema_position=getattr(bias_snap, "ema_position", 0.0),
            bos_confirmed=getattr(bias_snap, "bos_confirmed", False),
            bos_direction=getattr(bias_snap, "bos_direction", "") or "",
            bos_level=float(getattr(bias_snap, "bos_level", 0.0) or 0.0),
            swing_high=float(getattr(bias_snap, "last_swing_high", 0.0) or 0.0),
            swing_low=float(getattr(bias_snap, "last_swing_low", 0.0) or 0.0),
        )

    def _extract_m15(self, htf_context: Any) -> M15Summary:
        """Extract M15 summary from HTFContext. Returns neutral on failure."""
        if htf_context is None:
            return M15Summary()
        struct_snap = getattr(htf_context, "structure", None)
        if struct_snap is None:
            return M15Summary()
        return M15Summary(
            quality_score=getattr(struct_snap, "quality_score", 0.0),
            at_key_level=getattr(struct_snap, "at_key_level", False),
            order_block_present=getattr(struct_snap, "order_block_present", False),
            nearest_support=getattr(struct_snap, "nearest_support", 0.0),
            nearest_resistance=getattr(struct_snap, "nearest_resistance", 0.0),
            swing_high=float(getattr(struct_snap, "nearest_resistance", 0.0) or 0.0),
            swing_low=float(getattr(struct_snap, "nearest_support", 0.0) or 0.0),
        )

    def _extract_m5(self, engine_state: Any) -> M5Summary:
        """
        Extract M5 execution context from EngineState.

        M5 is the EXECUTION timeframe. It only describes:
        - Current trigger readiness (bias FSM state)
        - Execution timing (confirmation strength)
        - Micro-regime (diagnostic, not authoritative)

        It does NOT determine: regime, structure, phase, setup quality.
        """
        if engine_state is None:
            return M5Summary()
        bias = getattr(engine_state, "current_bias", None)
        bias_dir = "NEUTRAL"
        if bias is not None:
            bias_dir = bias.value if hasattr(bias, "value") else str(bias)
        bias_phase = getattr(engine_state, "bias_phase", "EXPIRED") or "EXPIRED"
        return M5Summary(
            bias_phase=bias_phase,
            bias_strength=getattr(engine_state, "bias_strength", 0.0),
            bias_direction=bias_dir,
            regime_state=getattr(engine_state, "regime_state", "RANGING") or "RANGING",
            trigger_ready=(bias_phase == "CONFIRMED"),
            confirmation_strength="",  # Populated during engine evaluation, not at context build time
        )

    # ─── CLASSIFICATION HELPERS ───────────────────────────────────────────────

    def _classify_regime(self, h4: H4Summary) -> tuple[Regime, float]:
        """Derive unified regime from H4 classification."""
        r = h4.regime.upper()
        if "TRENDING" in r:
            return Regime.TRENDING, h4.confidence
        if r == "RANGING":
            return Regime.RANGING, h4.confidence
        if r == "VOLATILE":
            return Regime.TRANSITIONAL, h4.confidence * 0.8
        return Regime.TRANSITIONAL, max(0.3, h4.confidence)

    def _classify_phase(self, h1: H1Summary, m5: M5Summary) -> tuple[Phase, float]:
        """
        Classify structural phase from H1 data ONLY.

        Authority: H1 owns structural phase (M3B).
        Uses: h1.swing_structure, h1.bos_confirmed, h1.bos_direction,
              h1.direction, h1.confidence.

        M5 bias_phase is NOT used for structural phase determination.
        M5 remains responsible for entry timing only.

        Phase model:
            IMPULSE:       Clear directional structure (HH_HL or LH_LL) + BOS confirmed
            PULLBACK:      Directional structure present but no BOS (retracement within trend)
            CONSOLIDATION: Mixed structure, no clear direction
            EXHAUSTION:    Directional structure present but confidence fading
            REVERSAL:      BOS confirmed against prior structure direction
        """
        structure = h1.swing_structure.upper()
        bos = h1.bos_confirmed
        bos_dir = h1.bos_direction.upper()
        h1_dir = h1.direction.upper()
        confidence = h1.confidence

        # ─── IMPULSE: clear structure + BOS in same direction ─────────
        if structure == "HH_HL" and bos and bos_dir == "BULLISH" and confidence > 0.4:
            return Phase.IMPULSE, min(1.0, confidence * 1.1)
        if structure == "LH_LL" and bos and bos_dir == "BEARISH" and confidence > 0.4:
            return Phase.IMPULSE, min(1.0, confidence * 1.1)

        # ─── REVERSAL: BOS against the prevailing structure ───────────
        if bos and structure == "HH_HL" and bos_dir == "BEARISH":
            return Phase.REVERSAL, 0.6
        if bos and structure == "LH_LL" and bos_dir == "BULLISH":
            return Phase.REVERSAL, 0.6

        # ─── PULLBACK: directional structure but no BOS ───────────────
        if structure in ("HH_HL", "LH_LL") and not bos and confidence > 0.3:
            return Phase.PULLBACK, confidence * 0.8

        # ─── EXHAUSTION: directional structure fading (low confidence) ─
        if structure in ("HH_HL", "LH_LL") and confidence <= 0.3:
            return Phase.EXHAUSTION, 0.4

        # ─── CONSOLIDATION: no clear directional structure ────────────
        return Phase.CONSOLIDATION, max(0.2, 0.4 - confidence)

    def _compute_tradability(self, h4: H4Summary, h1: H1Summary, m15: M15Summary, m5: M5Summary) -> float:
        """Compute 0.0–1.0 tradability score. Higher = more tradeable."""
        score = 0.0
        # H4 non-volatile regime bonus
        if "VOLATILE" not in h4.regime.upper():
            score += 0.25
        # H1 clear direction bonus
        if h1.direction != "NEUTRAL" and h1.confidence > 0.3:
            score += 0.25
        # M15 quality bonus
        score += m15.quality_score * 0.25
        # M5 confirmed bias bonus
        if m5.bias_phase in ("CONFIRMED", "CONFIRMING"):
            score += 0.25
        return min(1.0, score)

    def _compute_alignment(self, h4: H4Summary, h1: H1Summary, m5: M5Summary) -> float:
        """Compute cross-TF agreement score. 1.0 = full alignment."""
        h4_dir = h4.trend_bias.upper()
        h1_dir = h1.direction.upper()
        m5_dir = m5.bias_direction.upper()

        # Normalize m5 BUY/SELL to BULLISH/BEARISH
        if m5_dir == "BUY":
            m5_dir = "BULLISH"
        elif m5_dir == "SELL":
            m5_dir = "BEARISH"

        agreement = 0.0
        comparisons = 0

        # H4 vs H1
        if h4_dir != "NEUTRAL" and h1_dir != "NEUTRAL":
            agreement += 1.0 if h4_dir == h1_dir else 0.0
            comparisons += 1
        # H1 vs M5
        if h1_dir != "NEUTRAL" and m5_dir != "NEUTRAL":
            agreement += 1.0 if h1_dir == m5_dir else 0.0
            comparisons += 1
        # H4 vs M5
        if h4_dir != "NEUTRAL" and m5_dir != "NEUTRAL":
            agreement += 1.0 if h4_dir == m5_dir else 0.0
            comparisons += 1

        if comparisons == 0:
            return 0.5  # No directional signals to compare
        return round(agreement / comparisons, 4)

    def _neutral_context(self, cycle_id: int, current_time_s: float) -> MarketContext:
        """Return a neutral/safe MarketContext on failure."""
        return MarketContext(
            symbol=self._symbol,
            cycle_id=cycle_id,
            timestamp_utc=current_time_s,
        )
