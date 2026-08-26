"""
Horizon Shadow Evaluation Framework — Research-only evaluation of inactive horizons.

Reads completed horizon shadow trades (hshadow_ prefix, created by live_scanner Phase 4C.3)
and produces observations, reports, and activation readiness assessments.

ARCHITECTURE:
    Live Scanner (existing)
        → creates hshadow_ shadow trades for ALL eligible horizons
        → ShadowTradeEngine resolves them via bar evaluation
        → persisted to logs/shadow_trades/{SYMBOL}/{DATE}.jsonl

    This module (NEW):
        → reads persisted shadow trade results
        → filters by horizon (from trade_id suffix or strategy field)
        → builds HorizonObservation per horizon
        → generates research reports + activation readiness

THIS MODULE DOES NOT:
    - Create shadow trades (already done in live_scanner)
    - Modify execution behaviour
    - Enable inactive horizons
    - Place broker orders
    - Modify HorizonExecutionAuthority
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.horizon.research_contract import (
    HorizonObservation,
    HorizonResearchContract,
    ValidationStatus,
    compare_contract_to_observation,
    get_active_contract,
    ACTIVE_CONTRACT_VERSION,
)
from core.horizon.research_report import (
    HorizonResearchReport,
    OverallStatus,
    generate_horizon_report,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW TRADE RESULT MODEL (research-only representation)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HorizonShadowResult:
    """
    Completed shadow trade result for one horizon.

    Populated from persisted shadow trade records (logs/shadow_trades/).
    Not connected to broker execution.
    """
    shadow_id: str
    source_opportunity_id: str
    symbol: str
    direction: str
    horizon: str
    entry_price: float
    hypothetical_stop_loss: float
    hypothetical_take_profit: float
    entry_time: float
    exit_time: float
    exit_price: float
    realised_r: float
    max_favourable_excursion: float
    max_adverse_excursion: float
    close_reason: str
    profile_version: str
    bars_held: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "shadow_id": self.shadow_id,
            "source_opportunity_id": self.source_opportunity_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "horizon": self.horizon,
            "entry_price": self.entry_price,
            "hypothetical_stop_loss": self.hypothetical_stop_loss,
            "hypothetical_take_profit": self.hypothetical_take_profit,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "realised_r": round(self.realised_r, 4),
            "max_favourable_excursion": round(self.max_favourable_excursion, 6),
            "max_adverse_excursion": round(self.max_adverse_excursion, 6),
            "close_reason": self.close_reason,
            "profile_version": self.profile_version,
            "bars_held": self.bars_held,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVATION READINESS
# ═══════════════════════════════════════════════════════════════════════════════

class ActivationReadiness(str, Enum):
    """Whether a horizon is ready for live activation."""
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    CONTINUE_SHADOW = "CONTINUE_SHADOW"
    NOT_RECOMMENDED = "NOT_RECOMMENDED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class ActivationReport:
    """Assessment of whether a horizon is ready to enable."""
    horizon: str
    profile_version: str
    sample_size: int
    readiness: ActivationReadiness
    observed_rr: float = 0.0
    observed_win_rate: float = 0.0
    observed_profit_factor: float = 0.0
    observed_expectancy: float = 0.0
    expected_rr: float = 0.0
    expected_win_rate: float = 0.0
    recommendation: str = ""
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "profile_version": self.profile_version,
            "sample_size": self.sample_size,
            "readiness": self.readiness.value,
            "observed_rr": round(self.observed_rr, 4),
            "observed_win_rate": round(self.observed_win_rate, 4),
            "observed_profit_factor": round(self.observed_profit_factor, 3),
            "observed_expectancy": round(self.observed_expectancy, 4),
            "expected_rr": round(self.expected_rr, 4),
            "expected_win_rate": round(self.expected_win_rate, 4),
            "recommendation": self.recommendation,
            "generated_at": self.generated_at,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_LOGS_DIR = "logs"
_SHADOW_SUBDIR = "shadow_trades"


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_horizon_shadow_results(
    horizon: str | None = None,
) -> list[HorizonShadowResult]:
    """
    Load completed horizon shadow trade results from persistence.

    Filters to hshadow_ prefix trades (horizon research shadows).
    Optionally filters by specific horizon.

    Returns list of HorizonShadowResult (completed trades only).
    """
    shadow_dir = _get_project_root() / _DEFAULT_LOGS_DIR / _SHADOW_SUBDIR
    if not shadow_dir.exists():
        return []

    results: list[HorizonShadowResult] = []

    # Shadow trades are stored per symbol subdirectory
    for sym_dir in sorted(shadow_dir.iterdir()):
        if not sym_dir.is_dir():
            # Flat file (date-partitioned without symbol)
            if sym_dir.suffix == ".jsonl":
                results.extend(_parse_shadow_file(sym_dir, horizon))
            continue
        for jsonl_file in sorted(sym_dir.glob("*.jsonl")):
            results.extend(_parse_shadow_file(jsonl_file, horizon))

    logger.info(
        "[HORIZON_SHADOW] loaded %d shadow results%s",
        len(results),
        f" (horizon={horizon})" if horizon else "",
    )
    return results


def _parse_shadow_file(path: Path, horizon_filter: str | None) -> list[HorizonShadowResult]:
    """Parse a single JSONL file for horizon shadow results."""
    results: list[HorizonShadowResult] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Filter to horizon shadows (hshadow_ prefix)
                trade_id = record.get("trade_id", "")
                if not trade_id.startswith("hshadow_"):
                    continue

                # Extract horizon from trade_id suffix or strategy field
                _horizon = _extract_horizon(record)
                if not _horizon:
                    continue
                if horizon_filter and _horizon != horizon_filter:
                    continue

                # Only completed shadows
                if not record.get("exit_reason"):
                    continue

                result = _build_shadow_result(record, _horizon)
                if result:
                    results.append(result)
    except Exception as e:
        logger.debug("[HORIZON_SHADOW] error reading %s: %s", path.name, e)
    return results


def _extract_horizon(record: dict[str, Any]) -> str:
    """Extract horizon name from shadow trade record."""
    # trade_id format: hshadow_{cycle}_{symbol}_{HORIZON}
    trade_id = record.get("trade_id", "")
    parts = trade_id.split("_")
    if len(parts) >= 4:
        candidate = parts[-1].upper()
        if candidate in ("SCALP", "INTRADAY", "EXTENDED"):
            return candidate

    # Fallback: parse from strategy field (format: "STRATEGY_HORIZON")
    strategy = record.get("strategy", "")
    for h in ("SCALP", "INTRADAY", "EXTENDED"):
        if strategy.upper().endswith(f"_{h}"):
            return h

    return ""


def _build_shadow_result(record: dict[str, Any], horizon: str) -> HorizonShadowResult | None:
    """Convert raw shadow record dict to HorizonShadowResult."""
    try:
        _version = ACTIVE_CONTRACT_VERSION.get(horizon, f"{horizon}_RESEARCH_V1")
        return HorizonShadowResult(
            shadow_id=record.get("trade_id", ""),
            source_opportunity_id=(record.get("identity") or {}).get("canonical_opportunity_id")
            or record.get("canonical_opportunity_id", ""),
            symbol=record.get("symbol", ""),
            direction=record.get("direction", ""),
            horizon=horizon,
            entry_price=float(record.get("entry_price", 0)),
            hypothetical_stop_loss=float(record.get("stop_loss", 0)),
            hypothetical_take_profit=float(record.get("take_profit", 0)),
            entry_time=float(record.get("entry_time", 0)),
            exit_time=float(record.get("exit_time", 0)),
            exit_price=float(record.get("exit_price", 0)),
            realised_r=float(record.get("pnl_r_multiple", 0)),
            max_favourable_excursion=float(record.get("mfe_r", 0)),
            max_adverse_excursion=float(record.get("mae_r", 0)),
            close_reason=record.get("exit_reason", ""),
            profile_version=_version,
            bars_held=int(record.get("bars_held", 0)),
        )
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATION BUILDER (from shadow results)
# ═══════════════════════════════════════════════════════════════════════════════

def build_shadow_observation(
    results: list[HorizonShadowResult],
    horizon: str,
) -> HorizonObservation:
    """
    Build a HorizonObservation from completed shadow trade results.

    Uses R-multiples directly (shadow trades already store pnl_r_multiple).
    """
    if not results:
        _version = ACTIVE_CONTRACT_VERSION.get(horizon, f"{horizon}_RESEARCH_V1")
        return HorizonObservation(
            horizon=horizon,
            profile_version=_version,
            sample_size=0,
        )

    _version = results[0].profile_version
    _r_values: list[float] = []
    _hold_minutes: list[float] = []
    _mfe_r: list[float] = []
    _mae_r: list[float] = []
    _exit_reasons: dict[str, int] = {}
    _wins = 0
    _losses = 0
    _gross_win_r = 0.0
    _gross_loss_r = 0.0

    for r in results:
        _r_values.append(r.realised_r)
        if r.realised_r > 0:
            _wins += 1
            _gross_win_r += r.realised_r
        else:
            _losses += 1
            _gross_loss_r += abs(r.realised_r)

        # Hold duration
        if r.exit_time > r.entry_time:
            _hold_minutes.append((r.exit_time - r.entry_time) / 60.0)

        # Excursion (already in R-multiples from shadow engine)
        if r.max_favourable_excursion > 0:
            _mfe_r.append(r.max_favourable_excursion)
        if r.max_adverse_excursion > 0:
            _mae_r.append(r.max_adverse_excursion)

        _reason = r.close_reason or "unknown"
        _exit_reasons[_reason] = _exit_reasons.get(_reason, 0) + 1

    _sample = len(results)
    _total_decided = _wins + _losses
    _win_rate = _wins / _total_decided if _total_decided > 0 else 0.0
    _avg_r = statistics.mean(_r_values) if _r_values else 0.0
    _hold_avg = statistics.mean(_hold_minutes) if _hold_minutes else 0.0
    _hold_med = statistics.median(_hold_minutes) if _hold_minutes else 0.0
    _pf = _gross_win_r / _gross_loss_r if _gross_loss_r > 0 else (999.0 if _gross_win_r > 0 else 0.0)

    _avg_win_r = _gross_win_r / _wins if _wins > 0 else 0.0
    _avg_loss_r = _gross_loss_r / _losses if _losses > 0 else 0.0
    _expectancy = (_win_rate * _avg_win_r) - ((1 - _win_rate) * _avg_loss_r)

    # Convert R-multiple excursions to approximate pips (use average risk ~10 pips as proxy)
    _pip_proxy = 10.0  # Approximate pips per R for observation display
    _mfe_avg = statistics.mean(_mfe_r) * _pip_proxy if _mfe_r else 0.0
    _mae_avg = statistics.mean(_mae_r) * _pip_proxy if _mae_r else 0.0

    return HorizonObservation(
        horizon=horizon,
        profile_version=_version,
        sample_size=_sample,
        observed_move_average_pips=round(abs(_avg_r) * _pip_proxy, 2),
        observed_move_median_pips=round(statistics.median([abs(v) for v in _r_values]) * _pip_proxy, 2) if _r_values else 0.0,
        observed_hold_average_minutes=round(_hold_avg, 1),
        observed_hold_median_minutes=round(_hold_med, 1),
        observed_rr=round(_avg_r, 4),
        observed_win_rate=round(_win_rate, 4),
        observed_profit_factor=round(_pf, 3),
        observed_expectancy=round(_expectancy, 4),
        observed_mae_pips=round(_mae_avg, 2),
        observed_mfe_pips=round(_mfe_avg, 2),
        exit_reasons=_exit_reasons,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ACTIVATION READINESS ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════════════

_MIN_ACTIVATION_SAMPLE = 50
_MIN_POSITIVE_EXPECTANCY = 0.0


def assess_activation_readiness(
    horizon: str,
    observation: HorizonObservation,
    *,
    min_sample: int = _MIN_ACTIVATION_SAMPLE,
) -> ActivationReport:
    """
    Assess whether a horizon is ready for live activation based on shadow data.

    Rules:
        - < min_sample trades → INSUFFICIENT_DATA
        - Negative expectancy → NOT_RECOMMENDED
        - Positive expectancy + win_rate > 30% → READY_FOR_REVIEW
        - Otherwise → CONTINUE_SHADOW

    Never automatically enables. Only produces recommendation.
    """
    contract = get_active_contract(horizon)
    _version = ACTIVE_CONTRACT_VERSION.get(horizon, f"{horizon}_RESEARCH_V1")
    _expected_rr = contract.expected_rr if contract else 0.0
    _expected_wr = contract.expected_win_rate if contract else 0.0

    if observation.sample_size < min_sample:
        return ActivationReport(
            horizon=horizon,
            profile_version=_version,
            sample_size=observation.sample_size,
            readiness=ActivationReadiness.INSUFFICIENT_DATA,
            expected_rr=_expected_rr,
            expected_win_rate=_expected_wr,
            recommendation=f"Need {min_sample}+ shadow trades. Currently have {observation.sample_size}.",
        )

    _obs_exp = observation.observed_expectancy
    _obs_wr = observation.observed_win_rate
    _obs_rr = observation.observed_rr
    _obs_pf = observation.observed_profit_factor

    if _obs_exp <= _MIN_POSITIVE_EXPECTANCY:
        return ActivationReport(
            horizon=horizon,
            profile_version=_version,
            sample_size=observation.sample_size,
            readiness=ActivationReadiness.NOT_RECOMMENDED,
            observed_rr=_obs_rr,
            observed_win_rate=_obs_wr,
            observed_profit_factor=_obs_pf,
            observed_expectancy=_obs_exp,
            expected_rr=_expected_rr,
            expected_win_rate=_expected_wr,
            recommendation=f"Negative expectancy ({_obs_exp:.4f}). Not recommended for activation.",
        )

    if _obs_wr >= 0.30 and _obs_exp > 0:
        return ActivationReport(
            horizon=horizon,
            profile_version=_version,
            sample_size=observation.sample_size,
            readiness=ActivationReadiness.READY_FOR_REVIEW,
            observed_rr=_obs_rr,
            observed_win_rate=_obs_wr,
            observed_profit_factor=_obs_pf,
            observed_expectancy=_obs_exp,
            expected_rr=_expected_rr,
            expected_win_rate=_expected_wr,
            recommendation=f"Positive expectancy ({_obs_exp:.4f}), win rate {_obs_wr:.1%}. Candidate for activation review.",
        )

    return ActivationReport(
        horizon=horizon,
        profile_version=_version,
        sample_size=observation.sample_size,
        readiness=ActivationReadiness.CONTINUE_SHADOW,
        observed_rr=_obs_rr,
        observed_win_rate=_obs_wr,
        observed_profit_factor=_obs_pf,
        observed_expectancy=_obs_exp,
        expected_rr=_expected_rr,
        expected_win_rate=_expected_wr,
        recommendation=f"Positive expectancy but win rate below threshold ({_obs_wr:.1%}). Continue shadow testing.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FULL SHADOW EVALUATION PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_shadow_evaluation(
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Run the complete shadow evaluation pipeline for all horizons.

    Steps:
        1. Load horizon shadow results from persistence.
        2. Build observations per horizon.
        3. Generate research reports.
        4. Assess activation readiness.
        5. Persist results.

    Returns dict with reports and activation assessments.
    """
    _now = datetime.now(timezone.utc)
    all_results = load_horizon_shadow_results()

    horizon_data: dict[str, Any] = {}

    for horizon in ("SCALP", "INTRADAY", "EXTENDED"):
        _filtered = [r for r in all_results if r.horizon == horizon]
        _obs = build_shadow_observation(_filtered, horizon)

        # Research report
        _contract = get_active_contract(horizon)
        if _contract:
            _report = generate_horizon_report(_contract, _obs)
        else:
            _report = HorizonResearchReport(
                horizon=horizon,
                contract_version="UNKNOWN",
                observation_sample_size=0,
                overall_status=OverallStatus.INSUFFICIENT_DATA,
            )

        # Activation readiness
        _activation = assess_activation_readiness(horizon, _obs)

        horizon_data[horizon] = {
            "observation": _obs.to_dict(),
            "report": _report.to_dict(),
            "activation": _activation.to_dict(),
        }

    result = {
        "experiment_name": "horizon_shadow_evaluation",
        "question_id": "HORIZON_SHADOW_PERFORMANCE",
        "question": "How do inactive horizons perform in shadow mode?",
        "generated_at": _now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_source": "shadow_trades",
        "data_path": "logs/shadow_trades/{SYMBOL}/{DATE}.jsonl",
        "total_shadow_results": len(all_results),
        "horizons": horizon_data,
    }

    if persist:
        _persist_shadow_report(result, _now)

    return result


def _persist_shadow_report(report: dict[str, Any], timestamp: datetime) -> Path | None:
    """Persist shadow evaluation report following research_reports/ pattern."""
    try:
        reports_dir = _get_project_root() / "research_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        filename = f"horizon_shadow_eval_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = reports_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        logger.info("[HORIZON_SHADOW] report persisted: %s", filepath.name)
        return filepath
    except Exception as e:
        logger.warning("[HORIZON_SHADOW] failed to persist: %s", e)
        return None
