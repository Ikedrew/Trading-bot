"""
Execution Sizing Quality — Governed MARKET entry-reference sizing overlay.

Single governed evidence-quality mechanism for the historical MARKET
sizing defect (sized off strategy/reference_entry instead of executable
broker fill)::

    distortion = |fill - SL| / |reference_entry - SL|

Reuses the existing data_quality pattern: read-only, deterministic,
computed overlay. Never mutates raw records. Complements (not replaces)
research_engine.data_quality.classifier (epoch overlay).

Eligibility invariant: price-R stays eligible; monetary/volume need CLEAN.
Missing/unknown quality fails closed for monetary/volume.
Frozen audit thresholds: CLEAN 0.80-1.25; SUSPECT 0.667-1.50; else AFFECTED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
from typing import Any


class ExecutionSizingQuality(str, Enum):
    CLEAN = "CLEAN"
    SUSPECT = "SUSPECT"
    AFFECTED = "AFFECTED"
    UNKNOWN = "UNKNOWN"


class EvidencePurpose(str, Enum):
    PRICE_R = "PRICE_R"
    MONETARY_RISK = "MONETARY_RISK"
    VOLUME = "VOLUME"


REASON_MARKET_ENTRY_REFERENCE_SIZING_DEFECT = (
    "MARKET_ENTRY_REFERENCE_SIZING_DEFECT"
)
REASON_SUSPECT_SIZING_GEOMETRY = "SUSPECT_SIZING_GEOMETRY"
REASON_UNKNOWN_SIZING_QUALITY = "UNKNOWN_SIZING_QUALITY"
REASON_CLEAN_SIZING = "CLEAN_SIZING"

CLEAN_MIN = 0.80
CLEAN_MAX = 1.25
SUSPECT_MIN = 0.667
SUSPECT_MAX = 1.50

_WORST_ORDER = (
    ExecutionSizingQuality.AFFECTED,
    ExecutionSizingQuality.SUSPECT,
    ExecutionSizingQuality.UNKNOWN,
    ExecutionSizingQuality.CLEAN,
)


def _quality_rank(quality: ExecutionSizingQuality) -> int:
    return _WORST_ORDER.index(quality)


def _num(value: Any) -> float | None:
    try:
        if value is None or value is True or value is False:
            return None
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def extract_fill_price(record: dict[str, Any]) -> float | None:
    direct = _num(record.get("fill_price"))
    if direct:
        return direct
    fill = record.get("fill")
    if isinstance(fill, dict):
        nested = _num(fill.get("price"))
        if nested:
            return nested
    return None


def extract_reference_entry(record: dict[str, Any]) -> float | None:
    direct = _num(record.get("entry_reference"))
    if direct:
        return direct
    request = record.get("request")
    if isinstance(request, dict):
        nested = _num(request.get("entry_reference"))
        if nested:
            return nested
    return None


def extract_submitted_sl(record: dict[str, Any]) -> float | None:
    direct = _num(record.get("sl"))
    if direct:
        return direct
    for section in ("request", "submission"):
        block = record.get(section)
        if isinstance(block, dict):
            nested = _num(block.get("sl"))
            if nested:
                return nested
    return None


def sizing_distortion(
    *,
    fill_price: float | None,
    reference_entry: float | None,
    submitted_sl: float | None,
) -> float | None:
    fill = _num(fill_price)
    ref = _num(reference_entry)
    sl = _num(submitted_sl)
    if fill is None or ref is None or sl is None:
        return None
    denom = abs(ref - sl)
    if denom <= 0:
        return None
    return abs(fill - sl) / denom


def classify_distortion(distortion: float | None) -> ExecutionSizingQuality:
    value = _num(distortion)
    if value is None:
        return ExecutionSizingQuality.UNKNOWN
    if CLEAN_MIN <= value <= CLEAN_MAX:
        return ExecutionSizingQuality.CLEAN
    if SUSPECT_MIN <= value <= SUSPECT_MAX:
        return ExecutionSizingQuality.SUSPECT
    return ExecutionSizingQuality.AFFECTED


def is_primary_fill_record(record: dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("comment") == "protection_verification":
        return False
    if not record.get("result_ok", False):
        return False
    if not record.get("deal"):
        return False
    return True


def fill_key(record: dict[str, Any]) -> tuple[str, str, str]:
    deal = record.get("deal")
    response = record.get("response") or {}
    deal_id = deal if deal else (
        response.get("deal_id") if isinstance(response, dict) else None
    )
    return (
        str(record.get("correlation_id", "") or ""),
        str(deal_id or ""),
        str(record.get("account_id", "") or ""),
    )


@dataclass(frozen=True)
class FillSizingAssessment:
    key: tuple[str, str, str]
    correlation_id: str
    deal_id: str
    account_id: str
    fill_price: float | None
    reference_entry: float | None
    submitted_sl: float | None
    distortion: float | None
    quality: ExecutionSizingQuality
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": list(self.key),
            "correlation_id": self.correlation_id,
            "deal_id": self.deal_id,
            "account_id": self.account_id,
            "fill_price": self.fill_price,
            "reference_entry": self.reference_entry,
            "submitted_sl": self.submitted_sl,
            "distortion": self.distortion,
            "quality": self.quality.value,
            "reason": self.reason,
        }


def _reason_for(quality: ExecutionSizingQuality) -> str:
    if quality is ExecutionSizingQuality.CLEAN:
        return REASON_CLEAN_SIZING
    if quality is ExecutionSizingQuality.AFFECTED:
        return REASON_MARKET_ENTRY_REFERENCE_SIZING_DEFECT
    if quality is ExecutionSizingQuality.SUSPECT:
        return REASON_SUSPECT_SIZING_GEOMETRY
    return REASON_UNKNOWN_SIZING_QUALITY


def classify_fill(record: dict[str, Any]) -> FillSizingAssessment:
    key = fill_key(record)
    fill_price = extract_fill_price(record)
    reference_entry = extract_reference_entry(record)
    submitted_sl = extract_submitted_sl(record)
    distortion = sizing_distortion(
        fill_price=fill_price,
        reference_entry=reference_entry,
        submitted_sl=submitted_sl,
    )
    quality = classify_distortion(distortion)
    return FillSizingAssessment(
        key=key,
        correlation_id=key[0],
        deal_id=key[1],
        account_id=key[2],
        fill_price=fill_price,
        reference_entry=reference_entry,
        submitted_sl=submitted_sl,
        distortion=distortion,
        quality=quality,
        reason=_reason_for(quality),
    )


def build_fill_index(
    execution_results: list[dict[str, Any]],
) -> dict[tuple[str, str, str], FillSizingAssessment]:
    index: dict[tuple[str, str, str], FillSizingAssessment] = {}
    for record in execution_results or []:
        if not is_primary_fill_record(record):
            continue
        assessment = classify_fill(record)
        if not assessment.correlation_id or not assessment.deal_id:
            continue
        prior = index.get(assessment.key)
        if prior is None:
            index[assessment.key] = assessment
            continue
        if (_quality_rank(assessment.quality), json.dumps(assessment.to_dict(), sort_keys=True)) < (
            _quality_rank(prior.quality), json.dumps(prior.to_dict(), sort_keys=True)
        ):
            index[assessment.key] = assessment
    return dict(sorted(index.items()))


@dataclass
class TradeSizingEligibility:
    correlation_id: str
    quality: ExecutionSizingQuality
    reason: str
    fills: int = 0
    distortion_min: float | None = None
    distortion_max: float | None = None
    price_r_eligible: bool = True
    monetary_risk_eligible: bool = False
    volume_analysis_eligible: bool = False
    details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "execution_sizing_quality": self.quality.value,
            "reason": self.reason,
            "fills": self.fills,
            "distortion_min": self.distortion_min,
            "distortion_max": self.distortion_max,
            "price_r_eligible": self.price_r_eligible,
            "monetary_risk_eligible": self.monetary_risk_eligible,
            "volume_analysis_eligible": self.volume_analysis_eligible,
        }


def _worst_quality(
    assessments: list[FillSizingAssessment],
) -> ExecutionSizingQuality:
    worst = ExecutionSizingQuality.CLEAN
    for assessment in assessments:
        if _quality_rank(assessment.quality) < _quality_rank(worst):
            worst = assessment.quality
    return worst


def build_trade_eligibility(
    execution_results: list[dict[str, Any]],
) -> dict[str, TradeSizingEligibility]:
    fills = build_fill_index(execution_results)
    by_trade: dict[str, list[FillSizingAssessment]] = {}
    for assessment in fills.values():
        by_trade.setdefault(assessment.correlation_id, []).append(assessment)
    out: dict[str, TradeSizingEligibility] = {}
    for correlation_id in sorted(by_trade):
        assessments = by_trade[correlation_id]
        quality = _worst_quality(assessments)
        distortions = [
            a.distortion for a in assessments if a.distortion is not None
        ]
        out[correlation_id] = TradeSizingEligibility(
            correlation_id=correlation_id,
            quality=quality,
            reason=_reason_for(quality),
            fills=len(assessments),
            distortion_min=min(distortions) if distortions else None,
            distortion_max=max(distortions) if distortions else None,
            price_r_eligible=True,
            monetary_risk_eligible=quality is ExecutionSizingQuality.CLEAN,
            volume_analysis_eligible=quality is ExecutionSizingQuality.CLEAN,
            details=[a.to_dict() for a in assessments],
        )
    return out


def eligibility_for_trade(
    correlation_id: str,
    eligibility: dict[str, TradeSizingEligibility] | None,
) -> TradeSizingEligibility:
    if eligibility:
        found = eligibility.get(correlation_id or "")
        if found is not None:
            return found
    return TradeSizingEligibility(
        correlation_id=correlation_id or "",
        quality=ExecutionSizingQuality.UNKNOWN,
        reason=REASON_UNKNOWN_SIZING_QUALITY,
        fills=0,
        price_r_eligible=True,
        monetary_risk_eligible=False,
        volume_analysis_eligible=False,
    )


def _correlation_of(record: dict[str, Any]) -> str:
    corr = str(record.get("correlation_id", "") or "")
    if corr:
        return corr
    identity = record.get("identity")
    if isinstance(identity, dict):
        return str(identity.get("correlation_id", "") or "")
    return ""


def _looks_like_shadow(record: dict[str, Any]) -> bool:
    if record.get("is_shadow"):
        return True
    shadow_type = str(record.get("shadow_type", "") or "")
    if shadow_type.startswith("CANDIDATE_") or shadow_type in (
        "PRIMARY_HORIZON_SIMULATION",
        "HORIZON_ALTERNATIVE",
    ):
        return True
    provenance = record.get("provenance")
    if isinstance(provenance, dict) and provenance.get("population") == "SHADOW":
        return True
    identity = record.get("identity")
    if isinstance(identity, dict) and str(
        identity.get("shadow_trade_id", "") or ""
    ).startswith("nshadow_"):
        return True
    return False


def is_eligible(
    record_or_correlation: dict[str, Any] | str,
    purpose: EvidencePurpose | str,
    eligibility: dict[str, TradeSizingEligibility] | None = None,
    *,
    is_shadow: bool = False,
) -> bool:
    want = purpose.value if isinstance(purpose, EvidencePurpose) else str(purpose)
    if want not in {p.value for p in EvidencePurpose}:
        return False
    if is_shadow:
        return True
    if isinstance(record_or_correlation, dict):
        if _looks_like_shadow(record_or_correlation):
            return True
        correlation_id = _correlation_of(record_or_correlation)
        if eligibility is None:
            # Derived overlays may travel with a record through projections.
            # Flags alone cannot turn UNKNOWN evidence into CLEAN evidence.
            metadata = record_or_correlation.get("sizing_quality") or record_or_correlation
            if want == EvidencePurpose.PRICE_R.value:
                return True
            return isinstance(metadata, dict) and metadata.get(
                "execution_sizing_quality"
            ) == ExecutionSizingQuality.CLEAN.value
    else:
        correlation_id = str(record_or_correlation or "")
    want = purpose.value if isinstance(purpose, EvidencePurpose) else str(purpose)
    entry = eligibility_for_trade(correlation_id, eligibility)
    if want == EvidencePurpose.PRICE_R.value:
        return entry.price_r_eligible
    if want == EvidencePurpose.MONETARY_RISK.value:
        return entry.monetary_risk_eligible
    if want == EvidencePurpose.VOLUME.value:
        return entry.volume_analysis_eligible
    return False


# Field ownership used by research projections and purpose-aware queries.
MONETARY_FIELDS = frozenset({
    "pnl", "live_pnl", "final_pnl", "broker_pnl", "pnl_realised",
    "net_realised_pnl", "net_profit", "gross_profit", "commission", "swap",
    "fees", "risk_amount", "risk_cash", "monetary_risk", "risk_percentage",
    "risk_percent", "risk_pct", "account_risk_pct", "effective_risk_pct",
    "dt_risk_pct", "exposure",
})
VOLUME_FIELDS = frozenset({"volume", "volume_executed", "requested_volume", "position_size"})


def eligible_for_fields(record: dict[str, Any], fields) -> bool:
    """Gate only the purposes a query requires; R-only queries remain usable."""
    names = {name.rsplit(".", 1)[-1] for name in fields}
    evidence = record.get("execution", record)
    if names & MONETARY_FIELDS and not is_eligible(evidence, EvidencePurpose.MONETARY_RISK):
        return False
    if names & VOLUME_FIELDS and not is_eligible(evidence, EvidencePurpose.VOLUME):
        return False
    return True


def governed_record(record: dict[str, Any]) -> dict[str, Any]:
    """Copy a research projection, withholding unsafe sizing-derived values.

    Raw broker accounting remains in its source record. None means excluded,
    never zero profit/volume. This overlay grants no other quality guarantees.
    """
    result = dict(record)
    for purpose, fields in ((EvidencePurpose.MONETARY_RISK, MONETARY_FIELDS),
                            (EvidencePurpose.VOLUME, VOLUME_FIELDS)):
        if not is_eligible(record, purpose):
            for name in fields & result.keys():
                result[name] = None
    return result


def require_monetary_eligible(
    correlation_id: str,
    eligibility: dict[str, TradeSizingEligibility] | None,
) -> TradeSizingEligibility:
    entry = eligibility_for_trade(correlation_id, eligibility)
    if not entry.monetary_risk_eligible or not entry.volume_analysis_eligible:
        raise ValueError(
            f"monetary_sizing_excluded: correlation_id={correlation_id!r} "
            f"quality={entry.quality.value} reason={entry.reason}"
        )
    return entry


def annotate_record(
    record: dict[str, Any],
    eligibility: dict[str, TradeSizingEligibility] | None,
    *,
    correlation_id: str = "",
) -> dict[str, Any]:
    corr = correlation_id or _correlation_of(record)
    entry = eligibility_for_trade(corr, eligibility)
    copy = dict(record)
    copy["sizing_quality"] = entry.to_dict()
    return copy


def filter_price_r_eligible(
    records: list[dict[str, Any]],
    eligibility: dict[str, TradeSizingEligibility] | None,
) -> list[dict[str, Any]]:
    return [
        r for r in (records or [])
        if is_eligible(r, EvidencePurpose.PRICE_R, eligibility)
    ]


def filter_monetary_eligible(
    records: list[dict[str, Any]],
    eligibility: dict[str, TradeSizingEligibility] | None,
) -> list[dict[str, Any]]:
    return [
        r
        for r in (records or [])
        if is_eligible(r, EvidencePurpose.MONETARY_RISK, eligibility)
        and is_eligible(r, EvidencePurpose.VOLUME, eligibility)
    ]


def reconciliation_counts(
    execution_results: list[dict[str, Any]],
) -> dict[str, int]:
    counts = {"CLEAN": 0, "SUSPECT": 0, "AFFECTED": 0, "UNKNOWN": 0}
    for assessment in build_fill_index(execution_results).values():
        counts[assessment.quality.value] += 1
    counts["total"] = sum(
        counts[k] for k in ("CLEAN", "SUSPECT", "AFFECTED", "UNKNOWN")
    )
    return counts



