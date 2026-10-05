"""Account-safe prop-risk telemetry -- portfolio / correlation exposure (Block 2C).

This module owns the ONE canonical, account-scoped representation of how an
account's open risk is CONCENTRATED: by canonical symbol, by direction, and by
governed correlation cluster. It sits strictly ON TOP of Block 2B
(:mod:`core.risk.position_snapshot`) and never reads raw MT5 state itself.

DESIGN RULES (Block 2C)
-----------------------
1. 2B IS THE ONLY TRUTH SOURCE. Portfolio exposure consumes ONE exact
   :class:`~core.risk.position_snapshot.PositionObservationCycle`. It never
   calls ``mt5.positions_get`` and never reconstructs positions independently.
2. EXACT OBSERVATION LINKAGE. Every record binds to the exact 2B
   ``observation_id``, ``open_risk_id`` and 2A ``account_snapshot_id``. Records
   are NEVER joined by timestamp proximity and mismatched observation cycles are
   NEVER combined.
3. EXACT ACCOUNT SCOPE. One record per exact ``account_id``. There is no
   cross-account fallback, and one account's risk can never satisfy another.
4. UNKNOWN RISK IS NOT ZERO RISK. If ANY member position has unknown or
   unbounded monetary risk, the known floor is still reported, the authoritative
   total is WITHHELD (``None``), and every percentage that depends on that
   denominator is ``None`` -- never a percentage against a known-risk floor.
5. NO HEDGE CREDIT. A correlation cluster identifies CONCENTRATION, not
   diversification. ``cluster_risk = sum(member monetary risk)`` always. Two
   opposite-direction positions in one cluster yield their FULL gross sum; no
   netting, no diversification discount, ever.
6. GROSS AND NET ARE DIFFERENT THINGS. Long and short risk are persisted
   separately at portfolio, symbol and cluster grain. Netting them into one
   smaller number is prohibited.
7. STATIC, GOVERNED CORRELATION. Correlation semantics come from
   :mod:`core.risk.symbol_correlation` and are explicitly STATIC, versioned and
   non-empirical. A symbol the model does not describe gets a deterministic
   UNCLASSIFIED singleton cluster -- never dropped, never merged.
8. MODEL FAILURE IS EXPLICIT. An unavailable or invalid correlation model
   yields an explicit status, never a silently empty or zero cluster set.
9. COMPLETENESS IS INHERITED. If the 2B position set is incomplete, the
   portfolio is PARTIAL/UNAVAILABLE, its total risk is not authoritative and its
   correlation exposure is not complete. The visible subset is never treated as
   the whole account.
10. DERIVED FRESHNESS NEVER EXCEEDS ITS SOURCE. Portfolio exposure inherits
    the 2B observation instant; it cannot appear fresher than the observation
    it was derived from.
11. PERCENTAGES REQUIRE AUTHORITATIVE DENOMINATORS. ``risk_pct_equity`` uses
    the exact linked 2A equity (never balance). ``risk_pct_total_open_risk``
    requires ``total_open_risk``. Otherwise both are ``None``.
12. NO CROSS-CURRENCY SUMMING. Monetary risk stays in each account's own
    account currency. Cross-account aggregation exposes explicit currency
    buckets and never guesses an FX conversion.

Prop-firm breach enforcement, daily loss rules, max drawdown, equity
high-water, challenge pass/fail and automated closing are Block 2D+ and are
deliberately absent. This block is TELEMETRY ONLY.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from core.risk.account_snapshot import (
    AccountIdentity,
    AccountIdentityError,
    AccountSnapshot,
    AccountSnapshotNotFound,
)
from core.risk.position_snapshot import (
    AccountOpenRiskSnapshot,
    PositionObservationCycle,
    PositionSetObservation,
    PositionSide,
    PositionSnapshot,
    PositionSnapshotError,
    PositionSnapshotNotFound,
    PositionStatus,
    freshness_threshold_ms,
    to_epoch_ms,
    to_iso_utc,
    utc_now,
)
from core.risk.symbol_correlation import (
    CorrelationModelInvalid,
    SymbolCorrelationModel,
    UNCLASSIFIED_PREFIX,
    default_symbol_correlation_model,
)

logger = logging.getLogger(__name__)

# -- DATASETS ------------------------------------------------------------------

#: Grain A -- one row per ACCOUNT PORTFOLIO OBSERVATION.
DATASET = "portfolio_exposure"
SCHEMA_VERSION = "portfolio_exposure_v1"
DEFAULT_LOCAL_DIR = "logs/portfolio_exposure"

#: Grain B -- one row per CORRELATION CLUSTER per account observation.
CLUSTER_DATASET = "correlation_exposure"
CLUSTER_SCHEMA_VERSION = "correlation_exposure_v1"
DEFAULT_CLUSTER_DIR = "logs/correlation_exposure"

#: Grain C -- one row per explicitly requested CROSS-ACCOUNT aggregate.
CROSS_ACCOUNT_DATASET = "cross_account_portfolio_exposure"
CROSS_ACCOUNT_SCHEMA_VERSION = "cross_account_portfolio_exposure_v1"
DEFAULT_CROSS_ACCOUNT_DIR = "logs/cross_account_portfolio_exposure"

#: Portfolio freshness is INHERITED from 2B and can never exceed it.
DEFAULT_FRESHNESS_ENV = "PORTFOLIO_EXPOSURE_FRESHNESS_SECONDS"
DEFAULT_FRESHNESS_SECONDS = 30.0
class PortfolioExposureError(RuntimeError):
    """Base error for the portfolio exposure contract."""


class PortfolioExposureNotFound(PortfolioExposureError):
    """No portfolio exposure exists for the requested exact account/symbol.

    Raised instead of ever returning another account's state or a zero value.
    """


class CorrelationModelUnavailable(PortfolioExposureError):
    """No correlation model could be supplied for this observation."""


class PortfolioStatus(str, Enum):
    """Explicit data-quality status of one portfolio exposure observation.

    ``STALE`` is never persisted: it is a read-time evaluation of age against
    the inherited 2B freshness threshold (use ``evaluate``).
    """

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    STALE = "STALE"


class PortfolioFailureReason(str, Enum):
    """Distinguishable failure semantics. Never collapsed into zero exposure."""

    NONE = "NONE"
    NO_POSITIONS = "NO_POSITIONS"
    POSITION_SOURCE_UNAVAILABLE = "POSITION_SOURCE_UNAVAILABLE"
    POSITION_SET_INCOMPLETE = "POSITION_SET_INCOMPLETE"
    RISK_INCOMPLETE = "RISK_INCOMPLETE"
    CORRELATION_MODEL_UNAVAILABLE = "CORRELATION_MODEL_UNAVAILABLE"
    CORRELATION_MODEL_INVALID = "CORRELATION_MODEL_INVALID"
    ACCOUNT_SNAPSHOT_UNAVAILABLE = "ACCOUNT_SNAPSHOT_UNAVAILABLE"
    EQUITY_UNAVAILABLE = "EQUITY_UNAVAILABLE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    ACCOUNT_IDENTITY_MISMATCH = "ACCOUNT_IDENTITY_MISMATCH"
    OBSERVATION_LINKAGE_MISMATCH = "OBSERVATION_LINKAGE_MISMATCH"
    CROSS_ACCOUNT_CURRENCY_DIVERGENCE = "CROSS_ACCOUNT_CURRENCY_DIVERGENCE"
    STALE_SOURCE = "STALE_SOURCE"


class Direction(str, Enum):
    """Directional bucket. Netting is an indicator, never a risk total."""

    LONG = "LONG"
    SHORT = "SHORT"
    MIXED = "MIXED"
    NONE = "NONE"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str, allow_nan=False)


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _round(value: float | None, digits: int = 8) -> float | None:
    """Deterministic rounding for persisted monetary values only."""
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(float(value), digits)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    """Ratio ONLY when the denominator is a real, authoritative, positive total.

    A known-risk FLOOR is never an authoritative denominator, so a ``None`` or
    non-positive denominator always yields ``None`` -- never a percentage
    computed against a floor and labelled authoritative.
    """
    if numerator is None or denominator is None:
        return None
    if not math.isfinite(denominator) or denominator <= 0.0:
        return None
    return round(float(numerator) / float(denominator), 10)


def freshness_threshold_ms(env: Mapping[str, str] | None = None) -> int:
    """Configurable portfolio freshness threshold. Inherits the 2B default."""
    source = os.environ if env is None else env
    raw = str(source.get(DEFAULT_FRESHNESS_ENV, "") or "").strip()
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = DEFAULT_FRESHNESS_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = DEFAULT_FRESHNESS_SECONDS
    return int(round(seconds * 1000))
    ACCOUNT_SNAPSHOT_UNAVAILABLE = "ACCOUNT_SNAPSHOT_UNAVAILABLE"
    EQUITY_UNAVAILABLE = "EQUITY_UNAVAILABLE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    ACCOUNT_IDENTITY_MISMATCH = "ACCOUNT_IDENTITY_MISMATCH"
    OBSERVATION_LINKAGE_MISMATCH = "OBSERVATION_LINKAGE_MISMATCH"
    CROSS_ACCOUNT_CURRENCY_DIVERGENCE = "CROSS_ACCOUNT_CURRENCY_DIVERGENCE"
    STALE_SOURCE = "STALE_SOURCE"


class Direction(str, Enum):
    """Directional bucket. Netting is an indicator, never a risk total."""

    LONG = "LONG"
    SHORT = "SHORT"
    MIXED = "MIXED"
    NONE = "NONE"
# ═════════════════════════════════════════════════════════════════════════════
# EXACT DETERMINISTIC IDENTITY
#
# Identity is a pure function of the exact account + the exact 2B observation
# lineage + the exact correlation model version. No UUID, no wall clock and no
# monetary float participates, so an exact replay is idempotent and a changed
# model version is a NEW immutable record (historical rows keep their own
# version forever).
# ═════════════════════════════════════════════════════════════════════════════


def _observation_digest_parts(
    account: AccountIdentity, open_risk: AccountOpenRiskSnapshot,
    model: SymbolCorrelationModel | None,
) -> dict[str, Any]:
    return {
        "account_id": account.account_id,
        "broker": account.broker,
        "server": account.server,
        "login": int(account.login),
        "observation_id": open_risk.observation_id,
        "observed_at_utc_ms": int(open_risk.observed_at_utc_ms),
        "open_risk_id": open_risk.open_risk_id,
        "account_snapshot_id": open_risk.account_snapshot_id,
        "source": open_risk.source,
        "correlation_model_id": model.model_id if model is not None else None,
        "correlation_model_version": model.model_version if model is not None else None,
    }


def derive_portfolio_exposure_id(
    account: AccountIdentity,
    open_risk: AccountOpenRiskSnapshot,
    model: SymbolCorrelationModel | None,
) -> str:
    """Deterministic identity of ONE account portfolio observation.

    The correlation model version is PART OF identity: changing the model
    produces a new portfolio record rather than silently reinterpreting the
    historical one.
    """
    digest = _sha256_hex({
        "kind": "portfolio_exposure",
        **_observation_digest_parts(account, open_risk, model),
    })
    return f"pexp_{digest[:32]}"


def derive_cluster_exposure_id(
    account: AccountIdentity,
    open_risk: AccountOpenRiskSnapshot,
    model: SymbolCorrelationModel | None,
    cluster_id: str,
) -> str:
    """Deterministic identity of ONE cluster row inside one observation."""
    digest = _sha256_hex({
        "kind": "cluster_exposure",
        **_observation_digest_parts(account, open_risk, model),
        "cluster_id": str(cluster_id),
    })
    return f"cexp_{digest[:32]}"


def derive_cross_account_exposure_id(
    account_ids: Sequence[str],
    observation_ids: Sequence[str],
    model: SymbolCorrelationModel | None,
) -> str:
    """Deterministic identity of ONE explicit cross-account aggregate.

    A cross-account record is a DIFFERENT GRAIN from per-account exposure and
    therefore has its own identity namespace (``xacc_``).
    """
    digest = _sha256_hex({
        "kind": "cross_account_portfolio_exposure",
        "account_ids": sorted(str(a) for a in account_ids),
        "observation_ids": sorted(str(o) for o in observation_ids),
        "correlation_model_id": model.model_id if model is not None else None,
        "correlation_model_version": model.model_version if model is not None else None,
    })
    return f"xacc_{digest[:32]}"
# ═════════════════════════════════════════════════════════════════════════════
# SYMBOL CONCENTRATION RECORD
#
# MULTIPLE POSITIONS ON THE SAME CANONICAL SYMBOL belong to ONE symbol bucket
# and their risks are summed exactly once each.
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SymbolExposure:
    """Per-canonical-symbol concentration for ONE account observation.

    ``known_risk`` is the provable floor. ``total_risk`` is authoritative ONLY
    when ``risk_complete`` is true. ``risk_pct_*`` fields are ``None`` whenever
    their denominator is not authoritative.
    """

    portfolio_exposure_id: str
    account_id: str
    observation_id: str
    open_risk_snapshot_id: str
    canonical_symbol: str
    cluster_id: str
    cluster_model_id: str | None
    cluster_model_version: str | None

    position_count: int
    position_tickets: tuple[int, ...] = ()

    known_risk: float | None = None
    total_risk: float | None = None
    risk_complete: bool = False
    unknown_risk_position_count: int = 0

    long_risk: float | None = None
    short_risk: float | None = None
    long_position_count: int = 0
    short_position_count: int = 0
    direction: Direction = Direction.NONE

    risk_pct_total_open_risk: float | None = None
    risk_pct_equity: float | None = None

    currency: str | None = None
    classified: bool = True
    status: PortfolioStatus = PortfolioStatus.COMPLETE

    @property
    def has_authoritative_total(self) -> bool:
        return bool(self.risk_complete and self.total_risk is not None)

    @property
    def gross_known_risk(self) -> float | None:
        """Sum of the long and short known floors (never netted)."""
        if self.long_risk is None and self.short_risk is None:
            return None
        return (self.long_risk or 0.0) + (self.short_risk or 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "SYMBOL_EXPOSURE",
            "portfolio_exposure_id": self.portfolio_exposure_id,
            "account_id": self.account_id,
            "observation_id": self.observation_id,
            "open_risk_snapshot_id": self.open_risk_snapshot_id,
            "canonical_symbol": self.canonical_symbol,
            "cluster_id": self.cluster_id,
            "cluster_model_id": self.cluster_model_id,
            "cluster_model_version": self.cluster_model_version,
            "position_count": self.position_count,
            "position_tickets": list(self.position_tickets),
            "known_risk": _round(self.known_risk),
            "total_risk": _round(self.total_risk),
            "risk_complete": self.risk_complete,
            "unknown_risk_position_count": self.unknown_risk_position_count,
            "long_risk": _round(self.long_risk),
            "short_risk": _round(self.short_risk),
            "long_position_count": self.long_position_count,
            "short_position_count": self.short_position_count,
            "direction": self.direction.value,
            "risk_pct_total_open_risk": self.risk_pct_total_open_risk,
            "risk_pct_equity": self.risk_pct_equity,
            "currency": self.currency,
            "classified": self.classified,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SymbolExposure":
        return cls(
            portfolio_exposure_id=str(payload["portfolio_exposure_id"]),
            account_id=str(payload["account_id"]),
            observation_id=str(payload["observation_id"]),
            open_risk_snapshot_id=str(payload["open_risk_snapshot_id"]),
            canonical_symbol=str(payload["canonical_symbol"]),
            cluster_id=str(payload["cluster_id"]),
            cluster_model_id=payload.get("cluster_model_id"),
            cluster_model_version=payload.get("cluster_model_version"),
            position_count=int(payload["position_count"]),
            position_tickets=tuple(int(t) for t in (payload.get("position_tickets") or ())),
            known_risk=payload.get("known_risk"),
            total_risk=payload.get("total_risk"),
            risk_complete=bool(payload.get("risk_complete", False)),
            unknown_risk_position_count=int(
                payload.get("unknown_risk_position_count") or 0),
            long_risk=payload.get("long_risk"),
            short_risk=payload.get("short_risk"),
            long_position_count=int(payload.get("long_position_count") or 0),
            short_position_count=int(payload.get("short_position_count") or 0),
            direction=Direction(payload.get("direction") or "NONE"),
            risk_pct_total_open_risk=payload.get("risk_pct_total_open_risk"),
            risk_pct_equity=payload.get("risk_pct_equity"),
            currency=payload.get("currency"),
            classified=bool(payload.get("classified", True)),
            status=PortfolioStatus(payload.get("status") or "COMPLETE"),
        )
# ═════════════════════════════════════════════════════════════════════════════
# CORRELATION CLUSTER EXPOSURE RECORD  (grain B)
#
# CLUSTER SEMANTICS -- CONCENTRATION, NOT HEDGING CREDIT
# ----------------------------------------------------
# cluster_risk = SUM of member monetary risk. Two members held in OPPOSING
# directions preserve BOTH gross risks: EURUSD BUY 100 + GBPUSD SELL 120 in one
# cluster is 220, never 20. A direction mix is reported as a labelled
# indicator; it never reduces the cluster total.
#
# NO DOUBLE COUNTING: each member POSITION contributes exactly once, so two
# positions on the same canonical symbol both count, and no ticket is summed
# twice inside one cluster.
#
# DETERMINISM: same positions + same model version -> same membership, because
# membership is a pure function of the governed canonical-symbol map.
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CorrelationClusterExposure:
    """One governed correlation cluster's exposure inside ONE account observation."""

    cluster_exposure_id: str
    portfolio_exposure_id: str
    cluster_id: str
    cluster_model_id: str | None
    cluster_model_version: str | None

    account_id: str
    observation_id: str
    open_risk_snapshot_id: str

    member_symbols: tuple[str, ...] = ()
    classified: bool = True
    position_tickets: tuple[int, ...] = ()
    member_position_count: int = 0

    known_cluster_risk: float | None = None
    total_cluster_risk: float | None = None
    cluster_risk_complete: bool = False
    unknown_risk_position_count: int = 0

    long_risk: float | None = None
    short_risk: float | None = None
    long_position_count: int = 0
    short_position_count: int = 0
    direction: Direction = Direction.NONE

    #: LABELLED indicator only. It is NOT a portfolio risk reduction and MUST
    #: never be used as hedge credit.
    net_known_directional_risk: float | None = None

    concentration_pct: float | None = None
    cluster_risk_pct_total_open_risk: float | None = None
    cluster_risk_pct_equity: float | None = None

    currency: str | None = None
    hedging_credit_applied: bool = False
    status: PortfolioStatus = PortfolioStatus.COMPLETE

    @property
    def has_authoritative_total(self) -> bool:
        return bool(self.cluster_risk_complete and self.total_cluster_risk is not None)

    @property
    def member_symbol_count(self) -> int:
        return len(self.member_symbols)

    def contains_ticket(self, ticket: int) -> bool:
        return int(ticket) in self.position_tickets

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CLUSTER_SCHEMA_VERSION,
            "record_kind": "CORRELATION_CLUSTER_EXPOSURE",
            "cluster_exposure_id": self.cluster_exposure_id,
            "portfolio_exposure_id": self.portfolio_exposure_id,
            "cluster_id": self.cluster_id,
            "cluster_model_id": self.cluster_model_id,
            "cluster_model_version": self.cluster_model_version,
            "account_id": self.account_id,
            "observation_id": self.observation_id,
            "open_risk_snapshot_id": self.open_risk_snapshot_id,
            "member_symbols": list(self.member_symbols),
            "classified": self.classified,
            "position_tickets": list(self.position_tickets),
            "member_position_count": self.member_position_count,
            "known_cluster_risk": _round(self.known_cluster_risk),
            "total_cluster_risk": _round(self.total_cluster_risk),
            "cluster_risk_complete": self.cluster_risk_complete,
            "unknown_risk_position_count": self.unknown_risk_position_count,
            "long_risk": _round(self.long_risk),
            "short_risk": _round(self.short_risk),
            "long_position_count": self.long_position_count,
            "short_position_count": self.short_position_count,
            "direction": self.direction.value,
            "net_known_directional_risk": _round(self.net_known_directional_risk),
            "concentration_pct": self.concentration_pct,
            "cluster_risk_pct_total_open_risk": self.cluster_risk_pct_total_open_risk,
            "cluster_risk_pct_equity": self.cluster_risk_pct_equity,
            "currency": self.currency,
            "hedging_credit_applied": self.hedging_credit_applied,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CorrelationClusterExposure":
        return cls(
            cluster_exposure_id=str(payload["cluster_exposure_id"]),
            portfolio_exposure_id=str(payload["portfolio_exposure_id"]),
            cluster_id=str(payload["cluster_id"]),
            cluster_model_id=payload.get("cluster_model_id"),
            cluster_model_version=payload.get("cluster_model_version"),
            account_id=str(payload["account_id"]),
            observation_id=str(payload["observation_id"]),
            open_risk_snapshot_id=str(payload["open_risk_snapshot_id"]),
            member_symbols=tuple(payload.get("member_symbols") or ()),
            classified=bool(payload.get("classified", True)),
            position_tickets=tuple(int(t) for t in (payload.get("position_tickets") or ())),
            member_position_count=int(payload.get("member_position_count") or 0),
            known_cluster_risk=payload.get("known_cluster_risk"),
            total_cluster_risk=payload.get("total_cluster_risk"),
            cluster_risk_complete=bool(payload.get("cluster_risk_complete", False)),
            unknown_risk_position_count=int(
                payload.get("unknown_risk_position_count") or 0),
            long_risk=payload.get("long_risk"),
            short_risk=payload.get("short_risk"),
            long_position_count=int(payload.get("long_position_count") or 0),
            short_position_count=int(payload.get("short_position_count") or 0),
            direction=Direction(payload.get("direction") or "NONE"),
            net_known_directional_risk=payload.get("net_known_directional_risk"),
            concentration_pct=payload.get("concentration_pct"),
            cluster_risk_pct_total_open_risk=payload.get(
                "cluster_risk_pct_total_open_risk"),
            cluster_risk_pct_equity=payload.get("cluster_risk_pct_equity"),
            currency=payload.get("currency"),
            hedging_credit_applied=bool(payload.get("hedging_credit_applied", False)),
            status=PortfolioStatus(payload.get("status") or "COMPLETE"),
        )
# ═════════════════════════════════════════════════════════════════════════════
# PORTFOLIO EXPOSURE RECORD  (grain A -- one row per account observation)
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PortfolioExposure:
    """One account's portfolio and correlation concentration for ONE observation.

    IDENTITY LINKAGE (never joined by timestamp)
    ---------------------------------------------
    ``observation_id``          exact Block 2B observation cycle
    ``open_risk_snapshot_id``   exact Block 2B ``open_risk_id``
    ``account_snapshot_id``     exact Block 2A ``snapshot_id`` (or ``None``)

    COMPLETENESS
    ------------
    ``known_open_risk`` is the provable floor and is ALWAYS exposed.
    ``total_open_risk`` is authoritative only when ``risk_complete`` is true;
    otherwise it is ``None``. Percentage fields that depend on the total are
    ``None`` in that case -- never a percentage against the floor.
    """

    portfolio_exposure_id: str

    # -- identity ----------------------------------------------------------
    account_id: str
    broker: str
    server: str
    login: int
    observation_id: str
    account_snapshot_id: str | None
    open_risk_snapshot_id: str
    observed_at_utc: str
    observed_at_utc_ms: int
    source: str

    # -- portfolio counts ---------------------------------------------------
    open_position_count: int = 0
    symbols_open: int = 0
    long_position_count: int = 0
    short_position_count: int = 0

    # -- risk ----------------------------------------------------------------
    known_open_risk: float | None = None
    total_open_risk: float | None = None
    risk_complete: bool = False
    gross_risk: float | None = None
    long_risk: float | None = None
    short_risk: float | None = None
    #: LABELLED indicator only. Never a hedge credit and never a risk total.
    net_known_directional_risk: float | None = None
    currency: str | None = None

    # -- concentration -------------------------------------------------------
    largest_symbol_risk: float | None = None
    largest_symbol: str | None = None
    largest_symbol_risk_pct: float | None = None
    largest_direction_risk: float | None = None
    largest_direction: Direction = Direction.NONE
    largest_direction_risk_pct: float | None = None
    top_n_concentration: tuple[tuple[str, float | None], ...] = ()
    unclassified_symbol_count: int = 0

    # -- correlation ---------------------------------------------------------
    correlation_model_id: str | None = None
    correlation_model_version: str | None = None
    correlation_model_key: str | None = None
    correlation_model_is_empirical: bool = False
    cluster_count: int = 0
    correlated_cluster_risk: float | None = None
    max_cluster_risk: float | None = None
    max_cluster_id: str | None = None
    max_cluster_risk_pct: float | None = None
    correlation_complete: bool = False
    unclassified_cluster_count: int = 0

    # -- equity-linked percentages ------------------------------------------
    equity: float | None = None
    risk_pct_equity: float | None = None

    # -- quality --------------------------------------------------------------
    status: PortfolioStatus = PortfolioStatus.UNAVAILABLE
    failure_reason: PortfolioFailureReason = PortfolioFailureReason.NONE
    position_set_complete: bool = False
    unavailable_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()
    source_error: str | None = None
    #: Per-canonical-symbol detail of THIS observation, in the same row so a
    #: portfolio record is self-contained (still one row per observation).
    symbol_exposure: tuple[SymbolExposure, ...] = ()

    @property
    def identity(self) -> AccountIdentity:
        return AccountIdentity(
            account_id=self.account_id, broker=self.broker,
            server=self.server, login=self.login,
        )

    @property
    def is_empty(self) -> bool:
        return self.open_position_count == 0

    @property
    def has_authoritative_total(self) -> bool:
        return bool(self.risk_complete and self.total_open_risk is not None)

    def age_ms(self, *, now_ms: int) -> int | None:
        """Age of the SOURCE observation; portfolio is never fresher than it."""
        if self.observed_at_utc_ms <= 0:
            return None
        return max(0, int(now_ms) - int(self.observed_at_utc_ms))

    def is_stale(self, *, now_ms: int, threshold_ms: int) -> bool:
        if threshold_ms <= 0:
            raise ValueError("FRESHNESS_THRESHOLD_MUST_BE_POSITIVE")
        age = self.age_ms(now_ms=now_ms)
        return age is None or age > threshold_ms

    def evaluate(self, *, now_ms: int, threshold_ms: int) -> "PortfolioExposure":
        """Read-time freshness evaluation. ``STALE`` is never persisted."""
        if self.is_stale(now_ms=now_ms, threshold_ms=threshold_ms):
            return replace(
                self, status=PortfolioStatus.STALE,
                failure_reason=PortfolioFailureReason.STALE_SOURCE,
            )
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "PORTFOLIO_EXPOSURE",
            "portfolio_exposure_id": self.portfolio_exposure_id,
            "account_id": self.account_id,
            "broker": self.broker,
            "server": self.server,
            "login": self.login,
            "observation_id": self.observation_id,
            "account_snapshot_id": self.account_snapshot_id,
            "open_risk_snapshot_id": self.open_risk_snapshot_id,
            "observed_at_utc": self.observed_at_utc,
            "observed_at_utc_ms": self.observed_at_utc_ms,
            "source": self.source,
            "open_position_count": self.open_position_count,
            "symbols_open": self.symbols_open,
            "long_position_count": self.long_position_count,
            "short_position_count": self.short_position_count,
            "known_open_risk": _round(self.known_open_risk),
            "total_open_risk": _round(self.total_open_risk),
            "risk_complete": self.risk_complete,
            "gross_risk": _round(self.gross_risk),
            "long_risk": _round(self.long_risk),
            "short_risk": _round(self.short_risk),
            "net_known_directional_risk": _round(self.net_known_directional_risk),
            "currency": self.currency,
            "largest_symbol_risk": _round(self.largest_symbol_risk),
            "largest_symbol": self.largest_symbol,
            "largest_symbol_risk_pct": self.largest_symbol_risk_pct,
            "largest_direction_risk": _round(self.largest_direction_risk),
            "largest_direction": self.largest_direction.value,
            "largest_direction_risk_pct": self.largest_direction_risk_pct,
            "top_n_concentration": [
                [symbol, _round(risk)]
                for symbol, risk in self.top_n_concentration
            ],
            "unclassified_symbol_count": self.unclassified_symbol_count,
            "correlation_model_id": self.correlation_model_id,
            "correlation_model_version": self.correlation_model_version,
            "correlation_model_key": self.correlation_model_key,
            "correlation_model_is_empirical": self.correlation_model_is_empirical,
            "cluster_count": self.cluster_count,
            "correlated_cluster_risk": _round(self.correlated_cluster_risk),
            "max_cluster_risk": _round(self.max_cluster_risk),
            "max_cluster_id": self.max_cluster_id,
            "max_cluster_risk_pct": self.max_cluster_risk_pct,
            "correlation_complete": self.correlation_complete,
            "unclassified_cluster_count": self.unclassified_cluster_count,
            "equity": _round(self.equity),
            "risk_pct_equity": self.risk_pct_equity,
            "status": self.status.value,
            "failure_reason": self.failure_reason.value,
            "position_set_complete": self.position_set_complete,
            "unavailable_fields": list(self.unavailable_fields),
            "invalid_fields": list(self.invalid_fields),
            "source_error": self.source_error,
            # Grain A also carries its own per-symbol detail so one portfolio row
            # is self-contained and provably ONE observation cycle.
            "symbol_exposure": [row.to_dict() for row in self.symbol_exposure],
        }
    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PortfolioExposure":
        return cls(
            portfolio_exposure_id=str(payload["portfolio_exposure_id"]),
            account_id=str(payload["account_id"]),
            broker=str(payload["broker"]),
            server=str(payload["server"]),
            login=int(payload["login"]),
            observation_id=str(payload["observation_id"]),
            account_snapshot_id=payload.get("account_snapshot_id"),
            open_risk_snapshot_id=str(payload["open_risk_snapshot_id"]),
            observed_at_utc=str(payload["observed_at_utc"]),
            observed_at_utc_ms=int(payload["observed_at_utc_ms"]),
            source=str(payload["source"]),
            open_position_count=int(payload.get("open_position_count") or 0),
            symbols_open=int(payload.get("symbols_open") or 0),
            long_position_count=int(payload.get("long_position_count") or 0),
            short_position_count=int(payload.get("short_position_count") or 0),
            known_open_risk=payload.get("known_open_risk"),
            total_open_risk=payload.get("total_open_risk"),
            risk_complete=bool(payload.get("risk_complete", False)),
            gross_risk=payload.get("gross_risk"),
            long_risk=payload.get("long_risk"),
            short_risk=payload.get("short_risk"),
            net_known_directional_risk=payload.get("net_known_directional_risk"),
            currency=payload.get("currency"),
            largest_symbol_risk=payload.get("largest_symbol_risk"),
            largest_symbol=payload.get("largest_symbol"),
            largest_symbol_risk_pct=payload.get("largest_symbol_risk_pct"),
            largest_direction_risk=payload.get("largest_direction_risk"),
            largest_direction=Direction(payload.get("largest_direction") or "NONE"),
            largest_direction_risk_pct=payload.get("largest_direction_risk_pct"),
            top_n_concentration=tuple(
                (str(item[0]), item[1])
                for item in (payload.get("top_n_concentration") or ())
            ),
            unclassified_symbol_count=int(payload.get("unclassified_symbol_count") or 0),
            correlation_model_id=payload.get("correlation_model_id"),
            correlation_model_version=payload.get("correlation_model_version"),
            correlation_model_key=payload.get("correlation_model_key"),
            correlation_model_is_empirical=bool(
                payload.get("correlation_model_is_empirical", False)),
            cluster_count=int(payload.get("cluster_count") or 0),
            correlated_cluster_risk=payload.get("correlated_cluster_risk"),
            max_cluster_risk=payload.get("max_cluster_risk"),
            max_cluster_id=payload.get("max_cluster_id"),
            max_cluster_risk_pct=payload.get("max_cluster_risk_pct"),
            correlation_complete=bool(payload.get("correlation_complete", False)),
            unclassified_cluster_count=int(
                payload.get("unclassified_cluster_count") or 0),
            equity=payload.get("equity"),
            risk_pct_equity=payload.get("risk_pct_equity"),
            status=PortfolioStatus(payload.get("status") or "UNAVAILABLE"),
            failure_reason=PortfolioFailureReason(
                payload.get("failure_reason") or "NONE"),
            position_set_complete=bool(payload.get("position_set_complete", False)),
            unavailable_fields=tuple(payload.get("unavailable_fields") or ()),
            invalid_fields=tuple(payload.get("invalid_fields") or ()),
            source_error=payload.get("source_error"),
            symbol_exposure=tuple(
                SymbolExposure.from_dict(item)
                for item in (payload.get("symbol_exposure") or ())
            ),
        )
# ═════════════════════════════════════════════════════════════════════════════
# AGGREGATION
#
# The ONLY input is ONE exact Block 2B observation cycle. Raw MT5 state is never
# queried here; 2B is the truth source.
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PortfolioExposureCycle:
    """Everything ONE telemetry cycle produced for ONE exact account.

    Grain A (one portfolio row) and grain B (one row per cluster) share the same
    ``observation_id`` and the same ``portfolio_exposure_id``.
    """

    account: AccountIdentity
    observation_id: str
    observed_at_utc: str
    observed_at_utc_ms: int
    portfolio: PortfolioExposure
    symbols: tuple[SymbolExposure, ...]
    clusters: tuple[CorrelationClusterExposure, ...]

    @property
    def account_id(self) -> str:
        return self.account.account_id


def _direction_of(long_count: int, short_count: int) -> Direction:
    if long_count and short_count:
        return Direction.MIXED
    if long_count:
        return Direction.LONG
    if short_count:
        return Direction.SHORT
    return Direction.NONE


def _equity_for_pct(
    account: AccountIdentity,
    open_risk: AccountOpenRiskSnapshot,
    account_snapshot: AccountSnapshot | None,
) -> tuple[float | None, PortfolioFailureReason]:
    """Exact linked 2A equity, or ``None``. Balance is NEVER a fallback."""
    if account_snapshot is None:
        return None, PortfolioFailureReason.ACCOUNT_SNAPSHOT_UNAVAILABLE
    if account_snapshot.account_id != account.account_id:
        raise AccountIdentityError("ACCOUNT_SNAPSHOT_IDENTITY_MISMATCH")
    # EXACT lineage: the 2A snapshot must be the one 2B linked to. Timestamp
    # proximity is explicitly forbidden.
    if open_risk.account_snapshot_id is not None and (
            account_snapshot.snapshot_id != open_risk.account_snapshot_id):
        raise PortfolioExposureError("ACCOUNT_SNAPSHOT_OBSERVATION_LINKAGE_MISMATCH")
    equity = account_snapshot.equity
    if equity is None or not math.isfinite(equity) or equity <= 0.0:
        return None, PortfolioFailureReason.EQUITY_UNAVAILABLE
    return float(equity), PortfolioFailureReason.NONE


def _linkage_defects(
    account: AccountIdentity,
    cycle_position_set: PositionSetObservation,
    positions: Sequence[PositionSnapshot],
    open_risk: AccountOpenRiskSnapshot,
) -> list[str]:
    """Bounded cross-record consistency. Any defect fails closed."""
    defects: list[str] = []
    if cycle_position_set.account_id != account.account_id:
        defects.append("ACCOUNT_ID_MISMATCH_IN_POSITION_SET")
    if open_risk.account_id != account.account_id:
        defects.append("ACCOUNT_ID_MISMATCH_IN_OPEN_RISK")
    if any(p.account_id != account.account_id for p in positions):
        defects.append("ACCOUNT_ID_MISMATCH_IN_POSITION_ROWS")
    if any(p.observation_id != open_risk.observation_id for p in positions):
        defects.append("OBSERVATION_ID_MISMATCH_IN_POSITION_ROWS")
    if cycle_position_set.observation_id != open_risk.observation_id:
        defects.append("OBSERVATION_ID_MISMATCH_IN_POSITION_SET")
    tickets = [p.position_ticket for p in positions]
    if len(set(tickets)) != len(tickets):
        defects.append("DUPLICATE_POSITION_TICKET")
    if open_risk.position_set_complete and set(tickets) != set(
            open_risk.position_tickets):
        defects.append("OPEN_RISK_TICKET_MISMATCH")
    return defects
def aggregate_portfolio_exposure(
    account: AccountIdentity,
    cycle: PositionObservationCycle,
    *,
    model: SymbolCorrelationModel | None = None,
    account_snapshot: AccountSnapshot | None = None,
) -> PortfolioExposureCycle:
    """Build ONE exact account portfolio + correlation exposure observation.

    Never under-reports. The known floor is always published; the authoritative
    total and every dependent percentage are withheld whenever the 2B position
    set or any member risk is incomplete.
    """
    if not isinstance(cycle, PositionObservationCycle):
        raise PortfolioExposureError("POSITION_OBSERVATION_CYCLE_REQUIRED")
    if cycle.account_id != account.account_id:
        raise AccountIdentityError("PORTFOLIO_ACCOUNT_IDENTITY_MISMATCH")

    open_risk = cycle.open_risk
    positions = tuple(cycle.positions)
    set_complete = bool(cycle.position_set.position_set_complete)
    risk_complete = bool(open_risk.risk_complete)

    defects = _linkage_defects(account, cycle.position_set, positions, open_risk)
    model_error: str | None = None
    if model is None:
        model_error = "CORRELATION_MODEL_UNAVAILABLE"
    else:
        try:
            model.to_dict()
        except CorrelationModelInvalid as exc:  # pragma: no cover - defensive
            model_error = f"CORRELATION_MODEL_INVALID:{exc}"

    portfolio_id = derive_portfolio_exposure_id(account, open_risk, model)

    # -- EQUITY LINKAGE (exact 2A snapshot, never balance) --------------------
    equity: float | None = None
    equity_reason = PortfolioFailureReason.NONE
    try:
        equity, equity_reason = _equity_for_pct(account, open_risk, account_snapshot)
    except PortfolioExposureError:
        raise
    equity_known = equity is not None

    # -- SYMBOL BUCKETS -------------------------------------------------------
    # Multiple positions on the SAME canonical symbol land in ONE bucket; each
    # position's risk is summed exactly once.
    symbol_rows: dict[str, list[PositionSnapshot]] = {}
    for snapshot in positions:
        symbol_rows.setdefault(snapshot.canonical_symbol, []).append(snapshot)

    symbol_exposures: list[SymbolExposure] = []
    for symbol in sorted(symbol_rows):
        rows = sorted(symbol_rows[symbol], key=lambda p: p.position_ticket)
        cluster_id = (
            model.cluster_for_symbol(symbol) if model is not None
            else f"{UNCLASSIFIED_PREFIX}{symbol}"
        )
        classified = bool(model is not None and model.is_classified(symbol))
        known_values = [
            p.monetary_risk_to_sl for p in rows if p.monetary_risk_to_sl is not None
        ]
        long_rows = [p for p in rows if p.side is PositionSide.BUY]
        short_rows = [p for p in rows if p.side is PositionSide.SELL]
        long_values = [
            p.monetary_risk_to_sl for p in long_rows
            if p.monetary_risk_to_sl is not None
        ]
        short_values = [
            p.monetary_risk_to_sl for p in short_rows
            if p.monetary_risk_to_sl is not None
        ]
        unknown_risk = sum(1 for p in rows if not p.has_known_risk)
        # A symbol total is authoritative only when the whole account set is
        # complete AND every position in this bucket has provable risk.
        symbol_complete = bool(set_complete and unknown_risk == 0 and not defects)
        known_risk = float(sum(known_values)) if known_values else (
            0.0 if (symbol_complete and not rows) else None)
        symbol_exposures.append(SymbolExposure(
            portfolio_exposure_id=portfolio_id,
            account_id=account.account_id,
            observation_id=open_risk.observation_id,
            open_risk_snapshot_id=open_risk.open_risk_id,
            canonical_symbol=symbol,
            cluster_id=cluster_id,
            cluster_model_id=model.model_id if model is not None else None,
            cluster_model_version=model.model_version if model is not None else None,
            position_count=len(rows),
            position_tickets=tuple(p.position_ticket for p in rows),
            known_risk=_round(known_risk),
            total_risk=_round(known_risk) if symbol_complete else None,
            risk_complete=symbol_complete,
            unknown_risk_position_count=unknown_risk,
            long_risk=_round(float(sum(long_values)) if long_values else 0.0),
            short_risk=_round(float(sum(short_values)) if short_values else 0.0),
            long_position_count=len(long_rows),
            short_position_count=len(short_rows),
            direction=_direction_of(len(long_rows), len(short_rows)),
            risk_pct_total_open_risk=None,   # filled below (needs portfolio total)
            risk_pct_equity=None,            # filled below (needs equity)
            currency=open_risk.currency,
            classified=classified,
            status=(PortfolioStatus.COMPLETE if symbol_complete
                    else PortfolioStatus.PARTIAL),
        ))
# -- DIRECTION BUCKETS (gross, never netted) ---------------------------------
    long_positions = [p for p in positions if p.side is PositionSide.BUY]
    short_positions = [p for p in positions if p.side is PositionSide.SELL]
    long_known = float(sum(
        p.monetary_risk_to_sl for p in long_positions
        if p.monetary_risk_to_sl is not None))
    short_known = float(sum(
        p.monetary_risk_to_sl for p in short_positions
        if p.monetary_risk_to_sl is not None))
    gross_known = long_known + short_known

    # -- CLUSTERS (grain B) ---------------------------------------------------
    # NO HEDGE CREDIT: cluster risk is always the SUM of member risks, even when
    # members are held in opposing directions.
    clusters: list[CorrelationClusterExposure] = []
    if model is not None:
        grouped: dict[str, list[PositionSnapshot]] = {}
        for snapshot in positions:
            grouped.setdefault(
                model.cluster_for_symbol(snapshot.canonical_symbol), []).append(snapshot)
        for cluster_id in sorted(grouped):
            rows = sorted(grouped[cluster_id], key=lambda p: p.position_ticket)
            members = tuple(sorted({p.canonical_symbol for p in rows}))
            known_values = [
                p.monetary_risk_to_sl for p in rows if p.monetary_risk_to_sl is not None
            ]
            long_rows = [p for p in rows if p.side is PositionSide.BUY]
            short_rows = [p for p in rows if p.side is PositionSide.SELL]
            unknown_risk = sum(1 for p in rows if not p.has_known_risk)
            cluster_complete = bool(set_complete and unknown_risk == 0 and not defects)
            cluster_long = float(sum(
                p.monetary_risk_to_sl for p in long_rows
                if p.monetary_risk_to_sl is not None))
            cluster_short = float(sum(
                p.monetary_risk_to_sl for p in short_rows
                if p.monetary_risk_to_sl is not None))
            known_cluster = float(sum(known_values)) if known_values else 0.0
            clusters.append(CorrelationClusterExposure(
                cluster_exposure_id=derive_cluster_exposure_id(
                    account, open_risk, model, cluster_id),
                portfolio_exposure_id=portfolio_id,
                cluster_id=cluster_id,
                cluster_model_id=model.model_id,
                cluster_model_version=model.model_version,
                account_id=account.account_id,
                observation_id=open_risk.observation_id,
                open_risk_snapshot_id=open_risk.open_risk_id,
                member_symbols=members,
                classified=not cluster_id.startswith(UNCLASSIFIED_PREFIX),
                position_tickets=tuple(p.position_ticket for p in rows),
                member_position_count=len(rows),
                known_cluster_risk=_round(known_cluster),
                total_cluster_risk=(
                    _round(known_cluster) if cluster_complete else None),
                cluster_risk_complete=cluster_complete,
                unknown_risk_position_count=unknown_risk,
                long_risk=_round(cluster_long),
                short_risk=_round(cluster_short),
                long_position_count=len(long_rows),
                short_position_count=len(short_rows),
                direction=_direction_of(len(long_rows), len(short_rows)),
                # LABELLED indicator. Never subtracted from any total.
                net_known_directional_risk=_round(cluster_long - cluster_short),
                concentration_pct=None,           # filled below
                cluster_risk_pct_total_open_risk=None,
                cluster_risk_pct_equity=None,
                currency=open_risk.currency,
                hedging_credit_applied=False,
                status=(PortfolioStatus.COMPLETE if cluster_complete
                        else PortfolioStatus.PARTIAL),
            ))

    # -- PORTFOLIO RISK -------------------------------------------------------
    # A provably EMPTY, COMPLETE set is a legitimate complete zero-risk account.
    empty_complete = bool(set_complete and not positions)
    portfolio_total = (
        float(open_risk.total_open_risk)
        if open_risk.total_open_risk is not None and risk_complete
        else (0.0 if empty_complete else None)
    )
    portfolio_known = (
        float(open_risk.known_open_risk)
        if open_risk.known_open_risk is not None and not defects
        else (0.0 if empty_complete and not defects else None)
    )
    if defects:
        # Fail closed: a self-inconsistent input publishes NO money at all.
        portfolio_total = None
        portfolio_known = None
        risk_complete = False
# -- CONCENTRATION ---------------------------------------------------------
    ranked_symbols = sorted(
        (s for s in symbol_exposures if s.known_risk is not None),
        key=lambda s: (-float(s.known_risk), s.canonical_symbol),
    )
    largest_symbol_row = ranked_symbols[0] if ranked_symbols else None
    largest_symbol_risk = (
        float(largest_symbol_row.known_risk) if largest_symbol_row is not None else None)
    if largest_symbol_row is None and empty_complete:
        largest_symbol_risk = 0.0

    # Percentages need an AUTHORITATIVE denominator. The known floor is NOT one.
    largest_symbol_pct = _ratio(largest_symbol_risk, portfolio_total)

    top_n = tuple(
        (row.canonical_symbol, row.known_risk) for row in ranked_symbols[:5])

    largest_direction = (
        Direction.LONG if long_known > short_known
        else (Direction.SHORT if short_known > long_known
              else (_direction_of(len(long_positions), len(short_positions))))
    )
    largest_direction_risk = max(long_known, short_known) if positions else (
        0.0 if empty_complete else None)
    largest_direction_pct = _ratio(largest_direction_risk, portfolio_total)

    # -- CORRELATION SUMMARY -------------------------------------------------
    unclassified_symbols = sum(1 for s in symbol_exposures if not s.classified)
    unclassified_clusters = sum(
        1 for c in clusters if not c.classified)
    max_cluster: CorrelationClusterExposure | None = None
    for cluster in clusters:
        if max_cluster is None or (
            cluster.known_cluster_risk or 0.0
        ) > (max_cluster.known_cluster_risk or 0.0):
            max_cluster = cluster
    max_cluster_risk = (
        float(max_cluster.known_cluster_risk) if max_cluster is not None
        else (0.0 if empty_complete else None))
    max_cluster_pct = _ratio(max_cluster_risk, portfolio_total)
    # "Correlated risk" = gross known risk held inside MULTI-SYMBOL clusters:
    # positions that are separate tickets but one concentrated directional theme.
    correlated_cluster_risk = float(sum(
        c.known_cluster_risk or 0.0 for c in clusters
        if len(c.member_symbols) > 1
    )) if clusters else (0.0 if empty_complete else None)
    correlation_complete = bool(
        model is not None and set_complete and not defects)

    # -- BACK-FILL PERCENTAGES ON CHILD ROWS --------------------------------
    symbol_exposures = [
        replace(
            row,
            risk_pct_total_open_risk=(
                _ratio(row.known_risk, portfolio_total) if row.risk_complete else None),
            risk_pct_equity=(
                _ratio(row.known_risk, equity) if row.risk_complete else None),
        )
        for row in symbol_exposures
    ]
    clusters = [
        replace(
            cluster,
            concentration_pct=(
                _ratio(cluster.known_cluster_risk, portfolio_total)
                if cluster.cluster_risk_complete else None),
            cluster_risk_pct_total_open_risk=(
                _ratio(cluster.known_cluster_risk, portfolio_total)
                if cluster.cluster_risk_complete else None),
            cluster_risk_pct_equity=(
                _ratio(cluster.known_cluster_risk, equity)
                if cluster.cluster_risk_complete else None),
        )
        for cluster in clusters
    ]

    # -- STATUS / FAILURE SEMANTICS -----------------------------------------
    unavailable: list[str] = []
    invalid: list[str] = []
    if defects:
        invalid.extend(defects)
        status = PortfolioStatus.INVALID
        reason = PortfolioFailureReason.OBSERVATION_LINKAGE_MISMATCH
    elif not set_complete:
        status = PortfolioStatus.UNAVAILABLE
        reason = (
            PortfolioFailureReason.POSITION_SOURCE_UNAVAILABLE
            if open_risk.source_error
            else PortfolioFailureReason.POSITION_SET_INCOMPLETE
        )
        unavailable.append("open_position_set")
    elif model is None:
        status = PortfolioStatus.PARTIAL
        reason = PortfolioFailureReason.CORRELATION_MODEL_UNAVAILABLE
    elif model_error is not None:
        status = PortfolioStatus.PARTIAL
        reason = PortfolioFailureReason.CORRELATION_MODEL_INVALID
    elif not risk_complete:
        status = PortfolioStatus.PARTIAL
        reason = PortfolioFailureReason.RISK_INCOMPLETE
    else:
        # A valid zero-position account is COMPLETE, never "unavailable".
        status = PortfolioStatus.COMPLETE
        reason = (
            PortfolioFailureReason.NO_POSITIONS if empty_complete
            else PortfolioFailureReason.NONE
        )
    if open_risk.source_error:
        unavailable.append("source_error")
    if not equity_known:
        unavailable.append("equity")
    portfolio = PortfolioExposure(
        portfolio_exposure_id=portfolio_id,
        account_id=account.account_id,
        broker=account.broker,
        server=account.server,
        login=int(account.login),
        observation_id=open_risk.observation_id,
        account_snapshot_id=open_risk.account_snapshot_id,
        open_risk_snapshot_id=open_risk.open_risk_id,
        observed_at_utc=open_risk.observed_at_utc,
        observed_at_utc_ms=int(open_risk.observed_at_utc_ms),
        source=open_risk.source,
        open_position_count=len(positions),
        symbols_open=len(symbol_rows),
        long_position_count=len(long_positions),
        short_position_count=len(short_positions),
        known_open_risk=_round(portfolio_known),
        total_open_risk=_round(portfolio_total),
        risk_complete=bool(risk_complete and portfolio_total is not None),
        gross_risk=_round(gross_known if positions else (
            0.0 if empty_complete else None)),
        long_risk=_round(long_known if positions else (0.0 if empty_complete else None)),
        short_risk=_round(short_known if positions else (
            0.0 if empty_complete else None)),
        net_known_directional_risk=_round(
            (long_known - short_known) if positions else (
                0.0 if empty_complete else None)),
        currency=open_risk.currency,
        largest_symbol_risk=_round(largest_symbol_risk),
        largest_symbol=(largest_symbol_row.canonical_symbol
                        if largest_symbol_row is not None else None),
        largest_symbol_risk_pct=largest_symbol_pct,
        largest_direction_risk=_round(largest_direction_risk),
        largest_direction=largest_direction,
        largest_direction_risk_pct=largest_direction_pct,
        top_n_concentration=top_n,
        unclassified_symbol_count=unclassified_symbols,
        correlation_model_id=model.model_id if model is not None else None,
        correlation_model_version=model.model_version if model is not None else None,
        correlation_model_key=model.model_key if model is not None else None,
        correlation_model_is_empirical=bool(model.is_empirical) if model else False,
        cluster_count=len(clusters),
        correlated_cluster_risk=_round(correlated_cluster_risk),
        max_cluster_risk=_round(max_cluster_risk),
        max_cluster_id=(max_cluster.cluster_id if max_cluster is not None else None),
        max_cluster_risk_pct=max_cluster_pct,
        correlation_complete=correlation_complete,
        unclassified_cluster_count=unclassified_clusters,
        equity=_round(equity),
        risk_pct_equity=_ratio(portfolio_total, equity),
        status=status,
        failure_reason=reason,
        position_set_complete=set_complete,
        unavailable_fields=tuple(sorted(set(unavailable))),
        invalid_fields=tuple(sorted(set(invalid))),
        source_error=open_risk.source_error,
        symbol_exposure=tuple(symbol_exposures),
    )
    return PortfolioExposureCycle(
        account=account,
        observation_id=open_risk.observation_id,
        observed_at_utc=open_risk.observed_at_utc,
        observed_at_utc_ms=int(open_risk.observed_at_utc_ms),
        portfolio=portfolio,
        symbols=tuple(symbol_exposures),
        clusters=tuple(clusters),
    )
# ═════════════════════════════════════════════════════════════════════════════
# DURABLE PERSISTENCE  (local fsync -> certified Block 1 canonical handoff)
#
# Three grains, THREE datasets, three local directories:
#   portfolio_exposure          grain A  logs/portfolio_exposure/<date>/<acct>.jsonl
#   correlation_exposure        grain B  logs/correlation_exposure/<date>/<acct>.jsonl
#   cross_account_portfolio_... grain C  logs/cross_account_portfolio_exposure/...
#
# There is deliberately NO direct S3 write. Local JSONL is canonical local truth;
# S3 is reached only through the Block 1 outbox.
# ═════════════════════════════════════════════════════════════════════════════


def _local_path(base_dir: str | Path, observed_at_utc: str, account_id: str) -> Path:
    return Path(base_dir) / observed_at_utc[:10] / f"{account_id}.jsonl"


def _cross_account_local_path(
    base_dir: str | Path, observed_at_utc: str,
) -> Path:
    return Path(base_dir) / observed_at_utc[:10] / "cross_account.jsonl"


def _append_durable_jsonl(
    *, dataset: str, payload: Mapping[str, Any], line: str,
    partition_date: str, path: Path, outbox: Any | None,
) -> None:
    """Shared certified write: handoff journal FIRST, then local fsync."""
    from core.lifecycle_evidence_obligations import (
        EXACT_IDENTITY_FIELDS,
        create_dataset_obligation,
        obligation_ledger,
        record_producer_outcome,
    )
    identity = {name: payload.get(name) for name in EXACT_IDENTITY_FIELDS[dataset]}
    identity_token = ":".join(str(identity[name]) for name in EXACT_IDENTITY_FIELDS[dataset])
    ledger = obligation_ledger()
    obligation = create_dataset_obligation(
        ledger,
        event_id=f"portfolio-risk:{dataset}:{identity_token}",
        lifecycle_stage="PORTFOLIO_RISK_OBSERVATION",
        dataset=dataset,
        identity=identity,
        timestamp=str(payload.get("observed_at_utc") or ""),
        producer="core.risk.portfolio_exposure._append_durable_jsonl",
        trigger="PORTFOLIO_RISK_LOCAL_FSYNC",
    )

    from core.canonical_delivery import (
        enqueue_canonical_delivery,
        try_prepare_local_jsonl_handoffs,
    )
    try_prepare_local_jsonl_handoffs(
        dataset=dataset, content=line + "\n", symbol="",
        partition_date=partition_date, local_path=path, outbox=outbox,
        lifecycle_obligation_id=obligation.obligation_id,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    record_producer_outcome(
        ledger, obligation, succeeded=True,
        observed_record_id=identity_token,
        provenance={"persistence_scope": "LOCAL_FSYNC"},
    )
    enqueue_canonical_delivery(
        dataset=dataset, payload=payload, symbol="",
        partition_date=partition_date, outbox=outbox,
        lifecycle_obligation_id=obligation.obligation_id,
    )


def persist_portfolio_exposure(
    cycle: PortfolioExposureCycle, *,
    base_dir: str | Path = DEFAULT_LOCAL_DIR,
    cluster_dir: str | Path = DEFAULT_CLUSTER_DIR,
    outbox: Any | None = None,
) -> bool:
    """Durably persist ONE account portfolio observation AND its cluster rows.

    Both writes share the SAME ``portfolio_exposure_id`` and ``observation_id``,
    so the two grains are provably one observation cycle.
    """
    if not isinstance(cycle, PortfolioExposureCycle):
        raise PortfolioExposureError("PORTFOLIO_EXPOSURE_CYCLE_REQUIRED")
    portfolio = cycle.portfolio
    payload = portfolio.to_dict()
    _append_durable_jsonl(
        dataset=DATASET, payload=payload, line=_canonical_json(payload),
        partition_date=portfolio.observed_at_utc[:10],
        path=_local_path(base_dir, portfolio.observed_at_utc, portfolio.account_id),
        outbox=outbox,
    )
    for cluster in cycle.clusters:
        cluster_payload = cluster.to_dict()
        _append_durable_jsonl(
            dataset=CLUSTER_DATASET, payload=cluster_payload,
            line=_canonical_json(cluster_payload),
            partition_date=portfolio.observed_at_utc[:10],
            path=_local_path(
                cluster_dir, portfolio.observed_at_utc, portfolio.account_id),
            outbox=outbox,
        )
    return True


def persist_cross_account_exposure(
    aggregate: "CrossAccountPortfolioExposure", *,
    base_dir: str | Path = DEFAULT_CROSS_ACCOUNT_DIR,
    outbox: Any | None = None,
) -> bool:
    """Durably persist ONE explicitly requested cross-account aggregate."""
    payload = aggregate.to_dict()
    _append_durable_jsonl(
        dataset=CROSS_ACCOUNT_DATASET, payload=payload,
        line=_canonical_json(payload),
        partition_date=aggregate.observed_at_utc[:10],
        path=_cross_account_local_path(base_dir, aggregate.observed_at_utc),
        outbox=outbox,
    )
    return True
# ═════════════════════════════════════════════════════════════════════════════
# ACCOUNT-SAFE SNAPSHOT PRODUCER
#
# Portfolio exposure is derived in the SAME telemetry cycle as 2B. It never
# queries raw MT5 state: it consumes the exact 2B cycle that 2A/2B just produced
# under one shared observation_id. No independent polling, no extra thread.
# ═════════════════════════════════════════════════════════════════════════════


class PortfolioExposureProducer:
    """Derives and durably persists portfolio/correlation exposure for one account.

    Read-only telemetry: it places, modifies and closes nothing.
    """

    def __init__(
        self,
        *,
        base_dir: str | Path = DEFAULT_LOCAL_DIR,
        cluster_dir: str | Path = DEFAULT_CLUSTER_DIR,
        model: SymbolCorrelationModel | None = None,
        outbox: Any | None = None,
        persist: bool = True,
    ) -> None:
        self._base_dir = base_dir
        self._cluster_dir = cluster_dir
        self._model = model
        self._outbox = outbox
        self._persist = persist

    @property
    def model(self) -> SymbolCorrelationModel | None:
        return self._model

    def derive(
        self,
        cycle: PositionObservationCycle,
        *,
        account_snapshot: AccountSnapshot | None = None,
        model: SymbolCorrelationModel | None = None,
    ) -> PortfolioExposureCycle:
        """Pure derivation -- no persistence, no clock, no broker access."""
        return aggregate_portfolio_exposure(
            cycle.account, cycle,
            model=self._model if model is None else model,
            account_snapshot=account_snapshot,
        )

    def observe(
        self,
        cycle: PositionObservationCycle,
        *,
        account_snapshot: AccountSnapshot | None = None,
        model: SymbolCorrelationModel | None = None,
    ) -> PortfolioExposureCycle:
        """Derive from ONE exact 2B cycle and durably persist the result."""
        derived = self.derive(
            cycle, account_snapshot=account_snapshot, model=model)
        if self._persist:
            persist_portfolio_exposure(
                derived, base_dir=self._base_dir,
                cluster_dir=self._cluster_dir, outbox=self._outbox)
        return derived


def observe_account_portfolio(
    account: AccountIdentity,
    cycle: PositionObservationCycle,
    *,
    model: SymbolCorrelationModel | None = None,
    account_snapshot: AccountSnapshot | None = None,
    base_dir: str | Path = DEFAULT_LOCAL_DIR,
    cluster_dir: str | Path = DEFAULT_CLUSTER_DIR,
    outbox: Any | None = None,
    persist: bool = True,
) -> PortfolioExposureCycle:
    """Run Block 2C for ONE exact account inside ONE exact 2B observation cycle.

    The returned cycle shares the 2B ``observation_id`` and the 2A
    ``account_snapshot_id``; no timestamp-nearest join is ever performed.
    """
    producer = PortfolioExposureProducer(
        base_dir=base_dir, cluster_dir=cluster_dir, model=model,
        outbox=outbox, persist=persist)
    return producer.observe(cycle, account_snapshot=account_snapshot)
# ═════════════════════════════════════════════════════════════════════════════
# STORE / QUERY API  (rebuildable convenience index over append-only evidence)
#
# Guarantees:
#   * EXACT account lookup only -- never falls back to another account;
#   * the latest observation is chosen by observation instant, NEVER by
#     reconstructing from an unrelated timestamp or by proximity joining;
#   * stale / incomplete state is EXPOSED, not hidden;
#   * raises PortfolioExposureNotFound rather than returning zeros.
# ═════════════════════════════════════════════════════════════════════════════


class PortfolioExposureStore:
    """Exact-account portfolio / correlation exposure lookup for Block 2D+."""

    def __init__(
        self,
        *,
        base_dir: str | Path = DEFAULT_LOCAL_DIR,
        cluster_dir: str | Path = DEFAULT_CLUSTER_DIR,
        threshold_ms: int | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._cluster_dir = Path(cluster_dir)
        self._threshold_ms = (
            freshness_threshold_ms() if threshold_ms is None else int(threshold_ms)
        )
        if self._threshold_ms <= 0:
            raise ValueError("FRESHNESS_THRESHOLD_MUST_BE_POSITIVE")
        self._latest: dict[str, PortfolioExposure] = {}
        self._symbols: dict[str, tuple[SymbolExposure, ...]] = {}
        self._clusters: dict[str, tuple[CorrelationClusterExposure, ...]] = {}
        self.rebuild()

    @property
    def threshold_ms(self) -> int:
        return self._threshold_ms

    def _iter_persisted(self, base_dir: Path):
        if not base_dir.is_dir():
            return
        for path in sorted(base_dir.rglob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning(
                                "[PORTFOLIO_STORE_INDEX] corrupt line in %s", path)
                            continue
                        if isinstance(record, dict):
                            yield path, record
            except OSError:
                logger.warning("[PORTFOLIO_STORE_INDEX] unreadable %s", path)

    def rebuild(self) -> None:
        """Reconstruct the indexes from the canonical append-only evidence."""
        latest: dict[str, PortfolioExposure] = {}
        symbols: dict[str, tuple[SymbolExposure, ...]] = {}
        for _path, record in self._iter_persisted(self._base_dir):
            try:
                portfolio = PortfolioExposure.from_dict(record)
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "[PORTFOLIO_STORE_INDEX] unusable portfolio record in %s", _path)
                continue
            current = latest.get(portfolio.account_id)
            if current is None or (
                    portfolio.observed_at_utc_ms >= current.observed_at_utc_ms):
                latest[portfolio.account_id] = portfolio
                symbols[portfolio.account_id] = tuple(
                    sorted(portfolio.symbol_exposure,
                           key=lambda r: r.canonical_symbol))
        self._latest = latest
        self._symbols = symbols
        self._clusters = self._rebuild_clusters(latest)
    def _rebuild_clusters(
        self, latest: Mapping[str, PortfolioExposure],
    ) -> dict[str, tuple[CorrelationClusterExposure, ...]]:
        clusters: dict[str, tuple[CorrelationClusterExposure, ...]] = {}
        for _path, record in self._iter_persisted(self._cluster_dir):
            try:
                cluster = CorrelationClusterExposure.from_dict(record)
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "[PORTFOLIO_STORE_INDEX] unusable cluster record in %s", _path)
                continue
            clusters.setdefault(cluster.account_id, ())
            clusters[cluster.account_id] = (
                cluster,) + clusters[cluster.account_id]
        return {
            account: tuple(sorted(
                (row for row in rows
                 if latest.get(account) is not None
                 and row.portfolio_exposure_id == latest[account].portfolio_exposure_id),
                key=lambda r: r.cluster_id))
            for account, rows in clusters.items()
        }

    def _account_key(self, account_id: str) -> str:
        key = str(account_id or "").strip()
        if not key:
            raise PortfolioExposureNotFound("ACCOUNT_ID_REQUIRED")
        return key

    def account_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._latest))

    def latest_portfolio_exposure(
        self, account_id: str, *, now_ms: int | None = None,
    ) -> PortfolioExposure:
        """Latest portfolio exposure for EXACTLY ``account_id``."""
        key = self._account_key(account_id)
        portfolio = self._latest.get(key)
        if portfolio is None:
            self.rebuild()
            portfolio = self._latest.get(key)
        if portfolio is None:
            raise PortfolioExposureNotFound(f"NO_PORTFOLIO_EXPOSURE:{key}")
        if now_ms is None:
            return portfolio
        return portfolio.evaluate(now_ms=int(now_ms), threshold_ms=self._threshold_ms)

    def latest_symbol_exposure(
        self, account_id: str, canonical_symbol: str, *,
        now_ms: int | None = None,
    ) -> SymbolExposure:
        """Latest symbol exposure for EXACTLY (account_id, canonical_symbol)."""
        key = self._account_key(account_id)
        symbol = str(canonical_symbol or "").strip()
        if not symbol:
            raise PortfolioExposureNotFound("CANONICAL_SYMBOL_REQUIRED")
        for row in self.latest_symbol_exposures(key):
            if row.canonical_symbol == symbol:
                return row
        raise PortfolioExposureNotFound(f"NO_SYMBOL_EXPOSURE:{key}:{symbol}")

    def latest_symbol_exposures(
        self, account_id: str, *, now_ms: int | None = None,
    ) -> tuple[SymbolExposure, ...]:
        key = self._account_key(account_id)
        return tuple(sorted(self._symbols.get(key, ()),
                            key=lambda r: r.canonical_symbol))

    def latest_cluster_exposures(
        self, account_id: str, *, now_ms: int | None = None,
    ) -> tuple[CorrelationClusterExposure, ...]:
        """Every cluster exposure of EXACTLY ``account_id``, sorted by cluster id."""
        key = self._account_key(account_id)
        return tuple(sorted(self._clusters.get(key, ()),
                            key=lambda r: r.cluster_id))

    def latest_cluster_exposure(
        self, account_id: str, cluster_id: str, *,
        now_ms: int | None = None,
    ) -> CorrelationClusterExposure:
        key = self._account_key(account_id)
        wanted = str(cluster_id or "").strip()
        for row in self.latest_cluster_exposures(key):
            if row.cluster_id == wanted:
                return row
        raise PortfolioExposureNotFound(f"NO_CLUSTER_EXPOSURE:{key}:{wanted}")

    def record_cycle(self, cycle: PortfolioExposureCycle) -> None:
        """Refresh the in-memory indexes after a durable write."""
        account = cycle.account_id
        current = self._latest.get(account)
        if current is None or (
                cycle.portfolio.observed_at_utc_ms >= current.observed_at_utc_ms):
            self._latest[account] = cycle.portfolio
            self._symbols[account] = tuple(
                sorted(cycle.portfolio.symbol_exposure,
                       key=lambda r: r.canonical_symbol))
            self._clusters[account] = tuple(
                sorted(cycle.clusters, key=lambda r: r.cluster_id))
# -- observability -------------------------------------------------------
    def health(self, *, now_ms: int) -> dict[str, Any]:
        """Per-account portfolio observability. No dashboard, no network."""
        accounts: dict[str, Any] = {}
        last_success_ms: int | None = None
        for account_id, portfolio in sorted(self._latest.items()):
            evaluated = portfolio.evaluate(
                now_ms=int(now_ms), threshold_ms=self._threshold_ms)
            accounts[account_id] = {
                "status": evaluated.status.value,
                "failure_reason": evaluated.failure_reason.value,
                "source_observation_age_ms": evaluated.age_ms(now_ms=int(now_ms)),
                "observed_at_utc": evaluated.observed_at_utc,
                "open_position_count": evaluated.open_position_count,
                "known_open_risk": evaluated.known_open_risk,
                "total_open_risk": evaluated.total_open_risk,
                "largest_symbol": evaluated.largest_symbol,
                "largest_symbol_risk": evaluated.largest_symbol_risk,
                "largest_direction": evaluated.largest_direction.value,
                "max_cluster_id": evaluated.max_cluster_id,
                "max_cluster_risk": evaluated.max_cluster_risk,
                "unclassified_symbol_count": evaluated.unclassified_symbol_count,
                "correlation_model_key": evaluated.correlation_model_key,
            }
            if evaluated.status is not PortfolioStatus.UNAVAILABLE and (
                last_success_ms is None
                or evaluated.observed_at_utc_ms > last_success_ms
            ):
                last_success_ms = evaluated.observed_at_utc_ms
        return {
            "accounts_observed": len(accounts),
            "freshness_threshold_ms": self._threshold_ms,
            "last_success_observed_at_utc_ms": last_success_ms,
            "accounts": accounts,
        }
# ═════════════════════════════════════════════════════════════════════════════
# CROSS-ACCOUNT AGGREGATION  (grain C -- an EXPLICIT, DIFFERENT grain)
#
# Cross-account records are NEVER silently merged into per-account rows. They
# carry their own record_kind, their own identity namespace (``xacc_``) and
# their own dataset.
#
# CURRENCY RULE: monetary risk stays in each account's own account currency.
# Without an explicit, governed FX conversion ``combined_known_risk`` and
# ``combined_total_risk`` are ``None`` and the per-currency buckets carry the
# truth. NO conversion is ever guessed.
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CrossAccountCurrencyBucket:
    """Monetary risk held by accounts sharing ONE account currency."""

    currency: str
    account_ids: tuple[str, ...]
    known_risk: float | None
    total_risk: float | None
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "account_ids": list(self.account_ids),
            "known_risk": _round(self.known_risk),
            "total_risk": _round(self.total_risk),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class CrossAccountPortfolioExposure:
    """An explicitly requested aggregate across SEVERAL exact accounts.

    This is a DIFFERENT GRAIN from :class:`PortfolioExposure`; the two must never
    share a dataset row type without an exact grain distinction.
    """

    cross_account_exposure_id: str
    account_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    observed_at_utc: str
    observed_at_utc_ms: int

    correlation_model_id: str | None = None
    correlation_model_version: str | None = None
    correlation_model_key: str | None = None

    currency_buckets: tuple[CrossAccountCurrencyBucket, ...] = ()
    #: ``None`` whenever participating accounts do NOT all share one currency.
    combined_known_risk: float | None = None
    combined_total_risk: float | None = None
    combined_risk_complete: bool = False
    combined_correlated_cluster_risk: float | None = None
    combined_max_cluster_risk: float | None = None
    combined_max_cluster_id: str | None = None

    account_count: int = 0
    complete_account_ids: tuple[str, ...] = ()
    incomplete_account_ids: tuple[str, ...] = ()
    missing_account_ids: tuple[str, ...] = ()
    status: PortfolioStatus = PortfolioStatus.UNAVAILABLE
    failure_reason: PortfolioFailureReason = PortfolioFailureReason.NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CROSS_ACCOUNT_SCHEMA_VERSION,
            "record_kind": "CROSS_ACCOUNT_PORTFOLIO_EXPOSURE",
            "cross_account_exposure_id": self.cross_account_exposure_id,
            "account_ids": list(self.account_ids),
            "observation_ids": list(self.observation_ids),
            "observed_at_utc": self.observed_at_utc,
            "observed_at_utc_ms": self.observed_at_utc_ms,
            "correlation_model_id": self.correlation_model_id,
            "correlation_model_version": self.correlation_model_version,
            "correlation_model_key": self.correlation_model_key,
            "currency_buckets": [b.to_dict() for b in self.currency_buckets],
            "combined_known_risk": _round(self.combined_known_risk),
            "combined_total_risk": _round(self.combined_total_risk),
            "combined_risk_complete": self.combined_risk_complete,
            "combined_correlated_cluster_risk": _round(
                self.combined_correlated_cluster_risk),
            "combined_max_cluster_risk": _round(self.combined_max_cluster_risk),
            "combined_max_cluster_id": self.combined_max_cluster_id,
            "account_count": self.account_count,
            "complete_account_ids": list(self.complete_account_ids),
            "incomplete_account_ids": list(self.incomplete_account_ids),
            "missing_account_ids": list(self.missing_account_ids),
            "status": self.status.value,
            "failure_reason": self.failure_reason.value,
        }
def aggregate_cross_account_exposure(
    store: PortfolioExposureStore,
    account_ids: Sequence[str],
    *,
    model: SymbolCorrelationModel | None = None,
    requested: bool = True,
) -> CrossAccountPortfolioExposure:
    """Aggregate SEVERAL exact accounts into ONE explicitly labelled record.

    Cross-account aggregation is OPT-IN and explicit. It is never a fallback and
    never substitutes one account's state for another's: an account with no
    observation is listed in ``missing_account_ids``, not silently filled.
    """
    if not requested:
        raise PortfolioExposureError("CROSS_ACCOUNT_AGGREGATION_NOT_REQUESTED")
    keys = tuple(sorted({str(a or "").strip()
                         for a in account_ids if str(a or "").strip()}))
    if not keys:
        raise PortfolioExposureNotFound("CROSS_ACCOUNT_IDS_REQUIRED")

    portfolios: dict[str, PortfolioExposure] = {}
    missing: list[str] = []
    for key in keys:
        try:
            portfolios[key] = store.latest_portfolio_exposure(key)
        except PortfolioExposureNotFound:
            missing.append(key)

    ordered = [portfolios[key] for key in keys if key in portfolios]
    if not ordered:
        raise PortfolioExposureNotFound(
            f"NO_CROSS_ACCOUNT_PORTFOLIOS:{','.join(missing)}")

    # Every participating account must share ONE correlation model version.
    model_keys = {
        (p.correlation_model_id, p.correlation_model_version) for p in ordered
    }
    if len(model_keys) > 1:
        raise PortfolioExposureError(
            "CROSS_ACCOUNT_CORRELATION_MODEL_VERSION_MISMATCH:"
            + ";".join(sorted(f"{a}@{b}" for a, b in model_keys)))
    model_id, model_version = next(iter(model_keys))

    buckets: list[CrossAccountCurrencyBucket] = []
    by_currency: dict[str, list[PortfolioExposure]] = {}
    for portfolio in ordered:
        by_currency.setdefault(
            str(portfolio.currency or "UNKNOWN"), []).append(portfolio)
    for currency in sorted(by_currency):
        rows = by_currency[currency]
        known_values = [p.known_open_risk for p in rows
                        if p.known_open_risk is not None]
        total_values = [p.total_open_risk for p in rows
                        if p.total_open_risk is not None]
        all_complete = bool(rows) and all(p.has_authoritative_total for p in rows)
        buckets.append(CrossAccountCurrencyBucket(
            currency=currency,
            account_ids=tuple(sorted(p.account_id for p in rows)),
            known_risk=(float(sum(known_values)) if known_values else None),
            total_risk=(float(sum(total_values))
                        if (all_complete and total_values) else None),
            complete=all_complete,
        ))

    single_currency = len(buckets) == 1
    complete_ids = tuple(sorted(
        p.account_id for p in ordered if p.has_authoritative_total))
    incomplete_ids = tuple(sorted(
        p.account_id for p in ordered if not p.has_authoritative_total))
    all_complete = bool(ordered) and not incomplete_ids
    # A combined MONETARY total is published only when EVERY requested account
    # actually participated AND they all share one currency. A missing account
    # would otherwise make "combined" mean "only the ones that happened to work".
    combinable = bool(single_currency and not missing and all_complete)
    combined_known = buckets[0].known_risk if combinable else None
    combined_total = buckets[0].total_risk if combinable else None
    combined_correlated = (
        float(sum(p.correlated_cluster_risk or 0.0 for p in ordered))
        if combinable else None)
    max_rows = [p for p in ordered if p.max_cluster_risk is not None]
    top = (max(max_rows, key=lambda p: float(p.max_cluster_risk))
           if (combinable and max_rows) else None)
    combined_max = float(top.max_cluster_risk) if top is not None else None
    combined_max_id = top.max_cluster_id if top is not None else None

    if missing:
        status = PortfolioStatus.PARTIAL
        reason = PortfolioFailureReason.POSITION_SOURCE_UNAVAILABLE
    elif not single_currency:
        status = PortfolioStatus.PARTIAL
        reason = PortfolioFailureReason.CROSS_ACCOUNT_CURRENCY_DIVERGENCE
    elif not all_complete:
        status = PortfolioStatus.PARTIAL
        reason = PortfolioFailureReason.RISK_INCOMPLETE
    else:
        status = PortfolioStatus.COMPLETE
        reason = PortfolioFailureReason.NONE

    newest = max(ordered, key=lambda p: p.observed_at_utc_ms)
    return CrossAccountPortfolioExposure(
        cross_account_exposure_id=derive_cross_account_exposure_id(
            keys, [p.observation_id for p in ordered], model),
        account_ids=keys,
        observation_ids=tuple(sorted(p.observation_id for p in ordered)),
        observed_at_utc=newest.observed_at_utc,
        observed_at_utc_ms=newest.observed_at_utc_ms,
        correlation_model_id=model_id,
        correlation_model_version=model_version,
        correlation_model_key=(f"{model_id}@{model_version}" if model_id else None),
        currency_buckets=tuple(buckets),
        combined_known_risk=combined_known,
        combined_total_risk=combined_total,
        combined_risk_complete=bool(single_currency and all_complete),
        combined_correlated_cluster_risk=combined_correlated,
        combined_max_cluster_risk=combined_max,
        combined_max_cluster_id=combined_max_id,
        account_count=len(ordered),
        complete_account_ids=complete_ids,
        incomplete_account_ids=incomplete_ids,
        missing_account_ids=tuple(sorted(missing)),
        status=status,
        failure_reason=reason,
    )

    return CrossAccountPortfolioExposure(
cross_account_exposure_id=derive_cross_account_exposure_id(
            keys, [p.observation_id for p in ordered], model),
        account_ids=keys,
        observation_ids=tuple(sorted(p.observation_id for p in ordered)),
        observed_at_utc=newest.observed_at_utc,
        observed_at_utc_ms=newest.observed_at_utc_ms,
        correlation_model_id=model_id,
        correlation_model_version=model_version,
        correlation_model_key=(f"{model_id}@{model_version}" if model_id else None),
        currency_buckets=tuple(buckets),
        combined_known_risk=combined_known,
        combined_total_risk=combined_total,
        combined_risk_complete=bool(combinable),
        combined_correlated_cluster_risk=combined_correlated,
        combined_max_cluster_risk=combined_max,
        combined_max_cluster_id=combined_max_id,
        account_count=len(ordered),
        complete_account_ids=complete_ids,
        incomplete_account_ids=incomplete_ids,
        missing_account_ids=tuple(sorted(missing)),
        status=status,
        failure_reason=reason,
    )
__all__ = [
    "CLUSTER_DATASET",
    "CLUSTER_SCHEMA_VERSION",
    "CROSS_ACCOUNT_DATASET",
    "CROSS_ACCOUNT_SCHEMA_VERSION",
    "CorrelationClusterExposure",
    "CorrelationModelUnavailable",
    "CrossAccountCurrencyBucket",
    "CrossAccountPortfolioExposure",
    "DATASET",
    "DEFAULT_CLUSTER_DIR",
    "DEFAULT_CROSS_ACCOUNT_DIR",
    "DEFAULT_FRESHNESS_ENV",
    "DEFAULT_FRESHNESS_SECONDS",
    "DEFAULT_LOCAL_DIR",
    "Direction",
    "PortfolioExposure",
    "PortfolioExposureCycle",
    "PortfolioExposureError",
    "PortfolioExposureNotFound",
    "PortfolioExposureProducer",
    "PortfolioExposureStore",
    "PortfolioFailureReason",
    "PortfolioStatus",
    "SCHEMA_VERSION",
    "SymbolExposure",
    "aggregate_cross_account_exposure",
    "aggregate_portfolio_exposure",
    "derive_cluster_exposure_id",
    "derive_cross_account_exposure_id",
    "derive_portfolio_exposure_id",
    "freshness_threshold_ms",
    "observe_account_portfolio",
    "persist_cross_account_exposure",
    "persist_portfolio_exposure",
]
