"""Account-safe prop-risk telemetry — position / open-risk telemetry (Block 2B).

This module owns the ONE canonical, account-scoped representation of live open
positions and open monetary risk. It answers, for exactly one trading account:
which positions are open, which broker ticket owns each one, on which symbol,
in which direction, at what volume/price, which SL/TP are attached, how far the
stop is from the relevant risk price, exactly how much money is at risk if that
stop executes, what floating P&L the broker reports, and the aggregate open
risk for the account — with an explicit status for every exact/unavailable/
invalid/stale value.

DESIGN RULES (Block 2B)
-----------------------
1. EXACT POSITION IDENTITY. A position is (account_id, position_ticket) inside
   one broker/server/login namespace — the same ownership boundary already
   owned by ``core.position_ownership.position_identity_key``. Symbol, magic,
   comment, strategy, correlation_id and trade_id are METADATA, never identity.
   The same numeric ticket on two accounts is two distinct positions.
2. ZERO IS NOT "UNAVAILABLE". ``positions_get()`` returning ``()`` is a COMPLETE
   observation with zero open positions. ``None`` or a raised exception is
   UNAVAILABLE and is never converted into an empty portfolio.
3. NO STOP IS NOT ZERO RISK. A position with no valid protective SL is
   UNPROTECTED with ``monetary_risk_to_sl = None``. Its risk is unbounded, not
   small. An aggregate over such a portfolio is risk-incomplete and must not
   publish an authoritative ``total_open_risk``.
4. NO INVENTED VALUES. Observed broker data is preserved verbatim — an
   out-of-direction SL is recorded as observed and marked INVALID, never
   clamped, swapped or dropped. Volume is never normalised.
5. BROKER-NATIVE MONEY FIRST. Monetary loss at the stop is computed with the
   broker's own ``order_calc_profit`` when available. The only permitted
   fallback is the exact tick-size/tick-value identity using that account's
   broker specs. "pips x a guessed pip value" is never used.
6. ACCOUNT CURRENCY ONLY. ``risk_currency`` is the account currency taken from
   the exact Block 2A snapshot. No FX conversion is guessed in 2B.
7. DETERMINISTIC IDENTITY. ``position_snapshot_id`` is a pure function of
   (account identity, ticket, observation instant, source) and
   ``observation_id`` of (account identity, observation instant, source). No
   UUID, no wall clock and no monetary float takes part, so an exact replay is
   idempotent and a different observation instant is a new snapshot.
8. DURABLE. Local append-only JSONL + fsync, then the certified Block 1
   canonical handoff (outbox -> worker -> ACK). No best-effort S3 mirror.
9. PURELY OBSERVATIONAL. Nothing here places, modifies or closes a trade, and
   nothing here changes a trading decision.

Daily loss, max drawdown, high-water/peak equity, prop breach rules and
correlation exposure are Block 2C+ concerns and are deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from core.risk.account_snapshot import (
    AccountIdentity,
    AccountIdentityError,
    AccountSnapshot,
    to_epoch_ms,
    to_iso_utc,
    utc_now,
)

logger = logging.getLogger(__name__)

# ─── PRODUCTION V1 DATASET CONTRACT ───────────────────────────────────────────
DATASET = "position_snapshots"
SCHEMA_VERSION = "position_snapshots_v1"
DEFAULT_LOCAL_DIR = "logs/position_snapshots"

OPEN_RISK_DATASET = "account_open_risk"
OPEN_RISK_SCHEMA_VERSION = "account_open_risk_v1"
DEFAULT_OPEN_RISK_DIR = "logs/account_open_risk"

# ─── FRESHNESS (telemetry quality, NOT a prop rule) ───────────────────────────
# Position freshness is INDEPENDENT of the Block 2A account-snapshot threshold:
# open positions and stops change far more often than balance/equity, and a
# consumer must be able to see each freshness state separately.
DEFAULT_FRESHNESS_ENV = "POSITION_SNAPSHOT_FRESHNESS_SECONDS"
DEFAULT_FRESHNESS_SECONDS = 30.0

# ─── CADENCE (configurable; never an uncontrolled thread) ────────────────────
DEFAULT_INTERVAL_ENV = "POSITION_SNAPSHOT_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 15.0

# ─── BOKER CALCULATION TOLERANCE ─────────────────────────────────────────────
# Guards the broker's own float32/float64 accumulation only. Never used to
# correct or replace a reported value.
CONSISTENCY_TOLERANCE_ABS = 0.01
CONSISTENCY_TOLERANCE_REL = 1e-6

MT5_POSITION_TYPE_BUY = 0
MT5_POSITION_TYPE_SELL = 1

class PositionStatus(str, Enum):
    """Explicit, non-ambiguous data-quality status of one position snapshot.

    ``STALE`` is never persisted: it is a read-time evaluation of age against a
    configured freshness threshold (use :meth:`PositionSnapshot.evaluate`).
    """

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    STALE = "STALE"
    UNPROTECTED = "UNPROTECTED"


class PositionSide(str, Enum):
    """Broker position direction. ``type`` 0 is BUY, 1 is SELL in MT5."""

    BUY = "BUY"
    SELL = "SELL"


class PositionSnapshotError(RuntimeError):
    """Base error for the position snapshot contract."""


class PositionSetUnavailable(PositionSnapshotError):
    """The position source could not be read for this account."""


class PositionSnapshotNotFound(PositionSnapshotError):
    """No position snapshot / position-set observation exists for the request.

    Raised instead of ever returning another account's state or a zero value.
    """


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str, allow_nan=False)


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def freshness_threshold_ms(env: Mapping[str, str] | None = None) -> int:
    """Configurable position freshness threshold. Never a hard-coded prop rule."""
    source = os.environ if env is None else env
    raw = str(source.get(DEFAULT_FRESHNESS_ENV, "") or "").strip()
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = DEFAULT_FRESHNESS_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = DEFAULT_FRESHNESS_SECONDS
    return int(round(seconds * 1000))


def snapshot_interval_ms(env: Mapping[str, str] | None = None) -> int:
    """Configurable position observation cadence in ms."""
    source = os.environ if env is None else env
    raw = str(source.get(DEFAULT_INTERVAL_ENV, "") or "").strip()
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = DEFAULT_INTERVAL_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = DEFAULT_INTERVAL_SECONDS
    return int(round(seconds * 1000))


# ═══════════════════════════════════════════════════════════════════════════
# EXACT POSITION IDENTITY
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PositionIdentity:
    """Deterministic identity of exactly one open position on exactly one account.

    A broker ticket alone is NOT a sufficient position identity — the same
    numeric ticket on two accounts is two different positions. The ownership
    boundary is ``(account_id, position_ticket)`` within one
    (broker, server, login) namespace, mirroring
    ``core.position_ownership.position_identity_key``.
    """

    account: AccountIdentity
    position_ticket: int

    def __post_init__(self) -> None:
        if not isinstance(self.account, AccountIdentity):
            raise AccountIdentityError("EXACT_ACCOUNT_IDENTITY_REQUIRED")
        ticket = self.position_ticket
        if isinstance(ticket, bool) or not isinstance(ticket, int):
            raise PositionSnapshotError("POSITION_TICKET_MUST_BE_INTEGER")
        if ticket <= 0:
            raise PositionSnapshotError("POSITION_TICKET_MUST_BE_POSITIVE")

    @property
    def account_id(self) -> str:
        return self.account.account_id

    def ownership_key(self) -> tuple[str, int]:
        """The account-safe ownership key: (account_id, position_ticket)."""
        return (self.account_id, int(self.position_ticket))


def _validate_identity(account: AccountIdentity) -> None:
    if not isinstance(account, AccountIdentity):
        raise AccountIdentityError("EXACT_ACCOUNT_IDENTITY_REQUIRED")


def _validate_observation_ms(observed_at_utc_ms: int) -> int:
    if isinstance(observed_at_utc_ms, bool) or not isinstance(observed_at_utc_ms, int):
        raise ValueError("OBSERVATION_MS_MUST_BE_INTEGER")
    if observed_at_utc_ms <= 0:
        raise ValueError("OBSERVATION_MS_MUST_BE_POSITIVE")
    return int(observed_at_utc_ms)


def _validate_source(source: str) -> str:
    label = str(source or "").strip()
    if not label:
        raise ValueError("OBSERVATION_SOURCE_REQUIRED")
    return label


def _observation_digest_parts(
    account: AccountIdentity, observed_at_utc_ms: int, source: str,
) -> dict[str, Any]:
    _validate_identity(account)
    return {
        "account_id": account.account_id,
        "broker": account.broker,
        "server": account.server,
        "login": account.login,
        "observed_at_utc_ms": _validate_observation_ms(observed_at_utc_ms),
        "source": _validate_source(source),
    }


def derive_observation_id(
    account: AccountIdentity, observed_at_utc_ms: int, source: str,
) -> str:
    """Deterministic observation-cycle identity shared by one account's records.

    The account state snapshot, the position-set observation, every per-position
    row and the open-risk aggregate produced in ONE observation cycle share this
    id. Monetary values never take part in it.
    """
    digest = _sha256_hex({"kind": "observation",
                          **_observation_digest_parts(account, observed_at_utc_ms, source)})
    return f"obs_{digest[:32]}"


def derive_position_snapshot_id(
    account: AccountIdentity, position_ticket: int,
    observed_at_utc_ms: int, source: str,
) -> str:
    """Deterministic identity of ONE logical position observation.

    ``(account_id, position_ticket, observed_at_utc_ms)`` — a repeated exact
    observation at the same instant is idempotent; a different observation time
    is a new snapshot. Monetary risk is never part of identity.
    """
    ticket = position_ticket
    if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
        raise PositionSnapshotError("POSITION_TICKET_MUST_BE_POSITIVE")
    digest = _sha256_hex({
        "kind": "position_snapshot",
        **_observation_digest_parts(account, observed_at_utc_ms, source),
        "position_ticket": int(ticket),
    })
    return f"psnap_{digest[:32]}"


def derive_open_risk_id(
    account: AccountIdentity, observed_at_utc_ms: int, source: str,
) -> str:
    """Deterministic identity of ONE account open-risk aggregate observation."""
    digest = _sha256_hex({"kind": "open_risk",
                          **_observation_digest_parts(account, observed_at_utc_ms, source)})
    return f"orsk_{digest[:32]}"


def derive_position_set_id(
    account: AccountIdentity, observed_at_utc_ms: int, source: str,
) -> str:
    """Deterministic identity of ONE position-SET boundary observation.

    The set boundary shares the ``position_snapshots`` dataset with the per-
    position rows, so it needs its own governed identity: without one, its
    canonical handoff would be rejected for a missing identity field and the
    absence/closure proof would never reach the outbox.
    """
    digest = _sha256_hex({"kind": "position_set",
                          **_observation_digest_parts(account, observed_at_utc_ms, source)})
    return f"pset_{digest[:32]}"


# ═══════════════════════════════════════════════════════════════════════════
# VALUE VALIDATION — fail closed, never substitute a false zero
# ═══════════════════════════════════════════════════════════════════════════


def _coerce_float(
    value: Any, field_name: str, unavailable: list[str], invalid: list[str],
) -> float | None:
    """Return a finite float, or ``None`` with an explicit quality reason.

    ``None`` in, ``None`` out as *unavailable*. A non-numeric, boolean, NaN or
    infinite value is *invalid*. A legitimate ``0.0`` is preserved verbatim.
    """
    if value is None:
        unavailable.append(field_name)
        return None
    if isinstance(value, bool):
        invalid.append(field_name)
        return None
    if not isinstance(value, (int, float)):
        invalid.append(field_name)
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        invalid.append(field_name)
        return None
    return number


def _coerce_int(
    value: Any, field_name: str, unavailable: list[str], invalid: list[str],
) -> int | None:
    if value is None:
        unavailable.append(field_name)
        return None
    if isinstance(value, bool):
        invalid.append(field_name)
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value) or not float(value).is_integer():
            invalid.append(field_name)
            return None
        return int(value)
    invalid.append(field_name)
    return None


def _coerce_str(
    value: Any, field_name: str, unavailable: list[str], invalid: list[str],
) -> str | None:
    if value is None:
        unavailable.append(field_name)
        return None
    if not isinstance(value, str):
        invalid.append(field_name)
        return None
    text = value.strip()
    if not text:
        unavailable.append(field_name)
        return None
    return text


def _read_field(info: Mapping[str, Any], *names: str) -> Any:
    """Return the first present attribute/key among ``names`` (MT5 naming)."""
    for name in names:
        if isinstance(info, Mapping):
            if name in info:
                return info[name]
        elif hasattr(info, name):
            return getattr(info, name)
    return None


def _volume_defect(
    volume: float | None,
    volume_min: float | None,
    volume_max: float | None,
    volume_step: float | None,
    invalid: list[str],
) -> str | None:
    """Validate an OBSERVED position volume against broker specs.

    The volume is never normalised, rounded or repaired. A negative, zero, NaN or
    otherwise impossible volume for an active position is an explicit defect.
    """
    if volume is None:
        return None
    if volume <= 0:
        invalid.append("volume")
        return "POSITION_VOLUME_NOT_POSITIVE"
    if volume_min is not None and volume_min > 0 and volume < volume_min:
        invalid.append("volume")
        return "POSITION_VOLUME_BELOW_BROKER_MIN"
    if volume_max is not None and volume_max > 0 and volume > volume_max:
        invalid.append("volume")
        return "POSITION_VOLUME_ABOVE_BROKER_MAX"
    if volume_step is not None and volume_step > 0:
        # MT5 lot grids are anchored at zero. Only a gross misalignment is a
        # defect; broker float noise must not be reported as corruption.
        steps = volume / volume_step
        if abs(steps - round(steps)) > 1e-6:
            invalid.append("volume")
            return "POSITION_VOLUME_NOT_ON_BROKER_STEP"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# POSITION SET OBSERVATION — the absence/closure boundary
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PositionSetObservation:
    """One account-scoped observation of the COMPLETE set of open tickets.

    An append-only history alone cannot prove that a formerly open ticket is
    still open, nor that a ticket which stopped appearing is closed. This record
    is the explicit boundary: "at observation T, account X had exactly these N
    open position tickets", with ``position_ticket`` listed in full.

    ZERO IS A REAL OBSERVATION. ``()`` from the source is
    ``position_set_complete=True`` with ``open_position_count == 0``. A source
    failure (``None`` / exception) is ``position_set_complete=False`` with an
    explicit ``source_error`` and is NEVER converted into an empty portfolio.
    """

    account_id: str
    broker: str
    server: str
    login: int

    position_snapshot_id: str
    observation_id: str
    observed_at_utc: str
    observed_at_utc_ms: int
    source: str

    position_set_complete: bool
    open_position_count: int | None = None
    position_tickets: tuple[int, ...] = ()
    status: PositionStatus = PositionStatus.COMPLETE
    source_error: str | None = None
    consistency_ok: bool | None = None
    consistency_detail: str | None = None

    @property
    def identity(self) -> AccountIdentity:
        return AccountIdentity(
            account_id=self.account_id, broker=self.broker,
            server=self.server, login=self.login,
        )

    def age_ms(self, *, now_ms: int) -> int | None:
        if self.observed_at_utc_ms <= 0:
            return None
        return max(0, int(now_ms) - int(self.observed_at_utc_ms))

    def is_stale(self, *, now_ms: int, threshold_ms: int) -> bool:
        if threshold_ms <= 0:
            raise ValueError("FRESHNESS_THRESHOLD_MUST_BE_POSITIVE")
        age = self.age_ms(now_ms=now_ms)
        return age is None or age > threshold_ms

    def contains(self, ticket: int) -> bool:
        """True only if the ticket was PROVEN open at this observation."""
        return self.position_set_complete and int(ticket) in self.position_tickets

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "POSITION_SET",
            "account_id": self.account_id,
            "broker": self.broker,
            "server": self.server,
            "login": self.login,
            "position_snapshot_id": self.position_snapshot_id,
            "observation_id": self.observation_id,
            "observed_at_utc": self.observed_at_utc,
            "observed_at_utc_ms": self.observed_at_utc_ms,
            "source": self.source,
            "position_set_complete": self.position_set_complete,
            "open_position_count": self.open_position_count,
            "position_tickets": list(self.position_tickets),
            "status": self.status.value,
            "source_error": self.source_error,
            "consistency_ok": self.consistency_ok,
            "consistency_detail": self.consistency_detail,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PositionSetObservation":
        return cls(
            account_id=str(payload["account_id"]),
            broker=str(payload["broker"]),
            server=str(payload["server"]),
            login=int(payload["login"]),
            position_snapshot_id=str(payload["position_snapshot_id"]),
            observation_id=str(payload["observation_id"]),
            observed_at_utc=str(payload["observed_at_utc"]),
            observed_at_utc_ms=int(payload["observed_at_utc_ms"]),
            source=str(payload["source"]),
            position_set_complete=bool(payload["position_set_complete"]),
            open_position_count=(
                None if payload.get("open_position_count") is None
                else int(payload["open_position_count"])
            ),
            position_tickets=tuple(
                int(t) for t in (payload.get("position_tickets") or ())),
            status=PositionStatus(payload["status"]),
            source_error=payload.get("source_error"),
            consistency_ok=payload.get("consistency_ok"),
            consistency_detail=payload.get("consistency_detail"),
        )


# ═══════════════════════════════════════════════════════════════════════════
# TYPED POSITION SNAPSHOT RECORD
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PositionSnapshot:
    """One canonical, account-safe observation of exactly one open position.

    VALUE SEMANTICS
    ---------------
    side            MT5 ``position.type``: 0 -> BUY, 1 -> SELL. Anything else is
                    INVALID; the direction is never guessed.
    volume          MT5 ``position.volume`` — the CURRENT open volume, so a
                    partial close is observed as a smaller volume on the SAME
                    ticket (a new observation, not a new position identity).
                    Preserved verbatim; never normalised or repaired.
    open_price      MT5 ``position.price_open`` (the position's own entry).
    current_price   MT5 ``position.price`` — the broker's current close-out
                    price for the position (Bid for BUY, Ask for SELL).
    sl / tp         MT5 ``position.sl`` / ``position.tp`` verbatim. ``0`` means
                    "no stop attached" and yields ``has_stop=False``; the
                    observed value is still preserved.
    floating_pnl    MT5 ``position.profit`` — the broker's authoritative
                    per-position floating P&L in ACCOUNT CURRENCY. Positive,
                    negative and an exact zero are all preserved, and
                    unavailable is distinct from zero.
    monetary_risk_to_sl
                    Expected monetary LOSS (a non-negative number) in ACCOUNT
                    CURRENCY if the attached stop executes. ``None`` means the
                    risk is UNKNOWN — never zero. An open position without a
                    valid protective stop is UNPROTECTED and unbounded.
    expected_pnl_at_sl
                    Signed account-currency P&L implied by executing the stop.
                    Negative -> a loss (this is the risk); >= 0 -> breakeven or
                    locked profit. Preserved so profit-locking geometry is never
                    hidden behind a clamped zero.
    risk_price      The price the stop distance is measured FROM: the broker's
                    current close-out price for a live position. Documented
                    explicitly so open-price and close-out-price conventions are
                    never silently mixed.
    """

    # ── identity ──────────────────────────────────────────────────────────
    account_id: str
    broker: str
    server: str
    login: int
    position_snapshot_id: str
    observation_id: str
    position_ticket: int
    canonical_symbol: str
    broker_symbol: str

    # ── observation ───────────────────────────────────────────────────────
    observed_at_utc: str
    observed_at_utc_ms: int
    source: str

    # ── position ──────────────────────────────────────────────────────────
    side: PositionSide
    volume: float | None = None
    open_price: float | None = None
    current_price: float | None = None
    sl: float | None = None
    tp: float | None = None

    # ── broker metadata (recorded only when the broker reports it) ────────
    magic: int | None = None
    comment: str | None = None
    position_time: int | None = None
    position_time_ms: int | None = None
    order_ticket: int | None = None

    # ── risk geometry ─────────────────────────────────────────────────────
    risk_price: float | None = None
    stop_distance_price: float | None = None
    stop_distance_points: float | None = None
    stop_distance_pips: float | None = None
    has_stop: bool = False
    stop_side_valid: bool | None = None

    # ── money (ACCOUNT CURRENCY; None == unknown, never a false zero) ─────
    floating_pnl: float | None = None
    monetary_risk_to_sl: float | None = None
    expected_pnl_at_sl: float | None = None
    risk_currency: str | None = None
    risk_calculation: str = "UNAVAILABLE"

    # ── instrument / broker spec ──────────────────────────────────────────
    digits: int | None = None
    point: float | None = None
    tick_size: float | None = None
    tick_value: float | None = None
    tick_value_profit: float | None = None
    tick_value_loss: float | None = None
    contract_size: float | None = None
    volume_min: float | None = None
    volume_max: float | None = None
    volume_step: float | None = None
    trade_mode: int | None = None
    trade_calc_mode: int | None = None
    pip_size: float | None = None

    # ── quality ───────────────────────────────────────────────────────────
    status: PositionStatus = PositionStatus.UNAVAILABLE
    unavailable_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()
    source_error: str | None = None

    @property
    def identity(self) -> AccountIdentity:
        return AccountIdentity(
            account_id=self.account_id, broker=self.broker,
            server=self.server, login=self.login,
        )

    @property
    def position_identity(self) -> PositionIdentity:
        return PositionIdentity(self.identity, self.position_ticket)

    @property
    def ownership_key(self) -> tuple[str, int]:
        """Account-safe ownership key. The same ticket on B is a different key."""
        return (self.account_id, int(self.position_ticket))

    @property
    def is_protected(self) -> bool:
        """A position is protected only with a valid protective stop attached."""
        return bool(self.has_stop and self.stop_side_valid)

    @property
    def has_known_risk(self) -> bool:
        return self.monetary_risk_to_sl is not None

    def age_ms(self, *, now_ms: int) -> int | None:
        """Age since the BROKER observation, never since persistence."""
        if self.observed_at_utc_ms <= 0:
            return None
        return max(0, int(now_ms) - int(self.observed_at_utc_ms))

    def is_stale(self, *, now_ms: int, threshold_ms: int) -> bool:
        if threshold_ms <= 0:
            raise ValueError("FRESHNESS_THRESHOLD_MUST_BE_POSITIVE")
        age = self.age_ms(now_ms=now_ms)
        return age is None or age > threshold_ms

    def evaluate(self, *, now_ms: int, threshold_ms: int) -> "PositionSnapshot":
        """Return a copy whose status reflects read-time freshness."""
        if self.is_stale(now_ms=now_ms, threshold_ms=threshold_ms):
            return replace(self, status=PositionStatus.STALE)
        return self

    def to_dict(self) -> dict[str, Any]:
        """Canonical persistable payload.

        ``age_ms``/``stale`` are deliberately NOT persisted: they are read-time
        evaluations against a live clock, not observation facts.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "POSITION",
            "account_id": self.account_id,
            "broker": self.broker,
            "server": self.server,
            "login": self.login,
            "position_snapshot_id": self.position_snapshot_id,
            "observation_id": self.observation_id,
            "position_ticket": self.position_ticket,
            "canonical_symbol": self.canonical_symbol,
            "broker_symbol": self.broker_symbol,
            "observed_at_utc": self.observed_at_utc,
            "observed_at_utc_ms": self.observed_at_utc_ms,
            "source": self.source,
            "side": self.side.value,
            "volume": self.volume,
            "open_price": self.open_price,
            "current_price": self.current_price,
            "sl": self.sl,
            "tp": self.tp,
            "magic": self.magic,
            "comment": self.comment,
            "position_time": self.position_time,
            "position_time_ms": self.position_time_ms,
            "order_ticket": self.order_ticket,
            "risk_price": self.risk_price,
            "stop_distance_price": self.stop_distance_price,
            "stop_distance_points": self.stop_distance_points,
            "stop_distance_pips": self.stop_distance_pips,
            "has_stop": self.has_stop,
            "stop_side_valid": self.stop_side_valid,
            "floating_pnl": self.floating_pnl,
            "monetary_risk_to_sl": self.monetary_risk_to_sl,
            "expected_pnl_at_sl": self.expected_pnl_at_sl,
            "risk_currency": self.risk_currency,
            "risk_calculation": self.risk_calculation,
            "digits": self.digits,
            "point": self.point,
            "tick_size": self.tick_size,
            "tick_value": self.tick_value,
            "tick_value_profit": self.tick_value_profit,
            "tick_value_loss": self.tick_value_loss,
            "contract_size": self.contract_size,
            "volume_min": self.volume_min,
            "volume_max": self.volume_max,
            "volume_step": self.volume_step,
            "trade_mode": self.trade_mode,
            "trade_calc_mode": self.trade_calc_mode,
            "pip_size": self.pip_size,
            "status": self.status.value,
            "unavailable_fields": list(self.unavailable_fields),
            "invalid_fields": list(self.invalid_fields),
            "source_error": self.source_error,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PositionSnapshot":
        """Rehydrate a persisted position with no value reinterpretation."""
        return cls(
            account_id=str(payload["account_id"]),
            broker=str(payload["broker"]),
            server=str(payload["server"]),
            login=int(payload["login"]),
            position_snapshot_id=str(payload["position_snapshot_id"]),
            observation_id=str(payload["observation_id"]),
            position_ticket=int(payload["position_ticket"]),
            canonical_symbol=str(payload["canonical_symbol"]),
            broker_symbol=str(payload["broker_symbol"]),
            observed_at_utc=str(payload["observed_at_utc"]),
            observed_at_utc_ms=int(payload["observed_at_utc_ms"]),
            source=str(payload["source"]),
            side=PositionSide(payload["side"]),
            volume=payload.get("volume"),
            open_price=payload.get("open_price"),
            current_price=payload.get("current_price"),
            sl=payload.get("sl"),
            tp=payload.get("tp"),
            magic=payload.get("magic"),
            comment=payload.get("comment"),
            position_time=payload.get("position_time"),
            position_time_ms=payload.get("position_time_ms"),
            order_ticket=payload.get("order_ticket"),
            risk_price=payload.get("risk_price"),
            stop_distance_price=payload.get("stop_distance_price"),
            stop_distance_points=payload.get("stop_distance_points"),
            stop_distance_pips=payload.get("stop_distance_pips"),
            has_stop=bool(payload.get("has_stop")),
            stop_side_valid=payload.get("stop_side_valid"),
            floating_pnl=payload.get("floating_pnl"),
            monetary_risk_to_sl=payload.get("monetary_risk_to_sl"),
            expected_pnl_at_sl=payload.get("expected_pnl_at_sl"),
            risk_currency=payload.get("risk_currency"),
            risk_calculation=str(
                payload.get("risk_calculation") or "UNAVAILABLE"),
            digits=payload.get("digits"),
            point=payload.get("point"),
            tick_size=payload.get("tick_size"),
            tick_value=payload.get("tick_value"),
            tick_value_profit=payload.get("tick_value_profit"),
            tick_value_loss=payload.get("tick_value_loss"),
            contract_size=payload.get("contract_size"),
            volume_min=payload.get("volume_min"),
            volume_max=payload.get("volume_max"),
            volume_step=payload.get("volume_step"),
            trade_mode=payload.get("trade_mode"),
            trade_calc_mode=payload.get("trade_calc_mode"),
            pip_size=payload.get("pip_size"),
            status=PositionStatus(payload["status"]),
            unavailable_fields=tuple(payload.get("unavailable_fields") or ()),
            invalid_fields=tuple(payload.get("invalid_fields") or ()),
            source_error=payload.get("source_error"),
        )


# ═══════════════════════════════════════════════════════════════════════════
# POSITION STATUS CONTRACT
# ═══════════════════════════════════════════════════════════════════════════
#
# COMPLETE     identity, side, volume, open price, current price, SL/TP and the
#              monetary risk to the stop are all present, valid and consistent.
# PARTIAL      the position exists and is readable, but at least one non-critical
#              field (a broker spec, the broker profit calculation) is missing,
#              so risk may be unavailable. Never silently zero.
# UNAVAILABLE  the position source could not be read at all for this account.
# INVALID      a field is present-but-malformed (NaN/inf/non-numeric/bool), the
#              volume is impossible, or the observed SL is on the WRONG SIDE of
#              the risk price. Observed values are preserved verbatim and
#              ``monetary_risk_to_sl`` is None. Nothing is clamped or repaired.
# STALE        read-time only: the observation age exceeds the configured
#              freshness threshold. Never persisted as a status.
# UNPROTECTED  an open position exists but NO valid protective SL is attached.
#              Its risk is unbounded: ``monetary_risk_to_sl`` is None and the
#              account aggregate can never publish an authoritative total.


# ═══════════════════════════════════════════════════════════════════════════
# POSITION SOURCE BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════


class PositionSource(Protocol):
    """Injectable, ACCOUNT-SCOPED position source.

    A source instance is bound to exactly one account. Unit tests provide a
    fake; production provides :class:`Mt5PositionSource`. No caller of this
    module ever touches ``mt5.positions_get`` directly, so one account's
    observation can never accidentally read whichever global MT5 session
    happened to initialise last.
    """

    def read_positions(self) -> Sequence[Any] | None:
        """Return this account's open positions, or ``None`` if unreadable.

        An empty tuple/list is a VALID observation of zero open positions. A
        ``None`` return (or a raised :class:`PositionSetUnavailable`) is a
        source FAILURE and must never be read as "no positions".
        """


class SymbolSpecSource(Protocol):
    """Injectable, account-scoped broker symbol-spec source."""

    def read_symbol_spec(self, broker_symbol: str) -> Mapping[str, Any] | None:
        """Return this account's broker spec for ``broker_symbol``, or ``None``."""


class ProfitCalculator(Protocol):
    """Injectable broker-native monetary calculation boundary."""

    def calc_profit(
        self, *, side: str, broker_symbol: str, volume: float,
        open_price: float, close_price: float,
    ) -> float | None:
        """Account-currency P&L for closing at ``close_price``, or ``None``."""


class Mt5PositionSource:
    """Live MT5 position adapter for ONE pinned account.

    This is the ONLY place in the module that touches ``mt5.positions_get``.
    Identity is verified BEFORE and AFTER the read so a mid-read account switch
    can never be attributed to the requested account.
    """

    source_name = "MT5_POSITIONS_GET"

    def __init__(self, account: AccountIdentity, *, timeout: float | None = None) -> None:
        _validate_identity(account)
        self._account = account
        self._timeout = timeout

    @property
    def account(self) -> AccountIdentity:
        return self._account

    def _verify_identity(self) -> None:
        import MetaTrader5 as mt5

        info = mt5.account_info()
        if info is None:
            raise PositionSetUnavailable("MT5_ACCOUNT_INFO_UNAVAILABLE")
        login = _read_field(info, "login")
        server = str(_read_field(info, "server") or "")
        if login is None or not server:
            raise PositionSetUnavailable("MT5_ACCOUNT_IDENTITY_UNVERIFIABLE")
        try:
            login = int(login)
        except (TypeError, ValueError) as exc:
            raise PositionSetUnavailable(
                "MT5_ACCOUNT_IDENTITY_MALFORMED") from exc
        if (login != self._account.login
                or server.casefold() != self._account.server.casefold()):
            raise PositionSetUnavailable("MT5_ACCOUNT_IDENTITY_MISMATCH")

    def read_positions(self) -> Sequence[Any] | None:
        try:
            import MetaTrader5 as mt5
            from core.mt5_timeout import mt5_call
        except ImportError as exc:  # MT5 absent (CI / test environment)
            raise PositionSetUnavailable("MT5_MODULE_UNAVAILABLE") from exc
        call = mt5_call
        if self._timeout is not None:
            call = lambda func: mt5_call(func, timeout=self._timeout)  # noqa: E731
        self._verify_identity()
        try:
            positions = call(mt5.positions_get)
        except Exception as exc:
            raise PositionSetUnavailable(
                f"MT5_POSITIONS_GET_FAILED:{type(exc).__name__}") from exc
        self._verify_identity()
        return positions


class Mt5SymbolSpecSource:
    """Live MT5 symbol-spec adapter for ONE pinned account."""

    SPEC_FIELDS = (
        "digits", "point", "trade_tick_size", "trade_tick_value",
        "trade_tick_value_profit", "trade_tick_value_loss",
        "trade_contract_size", "volume_min", "volume_max", "volume_step",
        "trade_mode", "trade_calc_mode",
    )

    def read_symbol_spec(self, broker_symbol: str) -> Mapping[str, Any] | None:
        try:
            import MetaTrader5 as mt5
            from core.mt5_timeout import mt5_call
        except ImportError as exc:
            raise PositionSetUnavailable("MT5_MODULE_UNAVAILABLE") from exc
        info = mt5_call(mt5.symbol_info, broker_symbol)
        if info is None:
            return None
        return {field: getattr(info, field, None) for field in self.SPEC_FIELDS}


class Mt5OrderCalcProfit:
    """Broker-native ``order_calc_profit`` adapter.

    MT5 computes this in the ACCOUNT DEPOSIT CURRENCY, which is exactly the
    quantity prop-risk needs. It handles FX, metals, indices and CFDs with the
    broker's own contract specs, so it is preferred over any local formula.
    """

    def calc_profit(
        self, *, side: str, broker_symbol: str, volume: float,
        open_price: float, close_price: float,
    ) -> float | None:
        try:
            import MetaTrader5 as mt5
            from core.mt5_timeout import mt5_call
        except ImportError as exc:
            raise PositionSetUnavailable("MT5_MODULE_UNAVAILABLE") from exc
        order_type = (
            mt5.ORDER_TYPE_BUY if str(side).upper() == "BUY"
            else mt5.ORDER_TYPE_SELL
        )
        try:
            return mt5_call(mt5.order_calc_profit, order_type, broker_symbol,
                            float(volume), float(open_price), float(close_price))
        except Exception as exc:
            raise PositionSetUnavailable(
                f"MT5_ORDER_CALC_PROFIT_FAILED:{type(exc).__name__}") from exc


# ═══════════════════════════════════════════════════════════════════════════
# MONETARY RISK CALCULATION (§7, §31, §32, §33)
# ═══════════════════════════════════════════════════════════════════════════
#
# PRIORITY (highest first; nothing below is a guess):
#   1. BROKER-NATIVE  mt5.order_calc_profit(symbol, side, volume, open, close)
#                     evaluated at the ATTACHED STOP PRICE. MT5 returns this in
#                     ACCOUNT DEPOSIT CURRENCY using the broker's own contract
#                     spec, so FX, XAUUSD and indices are all covered without
#                     any local assumption.
#   2. EXACT TICK     (close - open) / trade_tick_size * trade_tick_value *
#                     volume, where trade_tick_value is the ACCOUNT-CURRENCY
#                     value of one minimum price increment for one lot. This is
#                     the broker's own valuation identity, not a pip formula.
#   3. NOTHING        risk stays None and the status degrades. Never guessed.
#
# SIDE / PRICE CONVENTION (one documented contract, never mixed):
#   The broker-native calculation closes the position AT THE STOP PRICE with
#   the position's own side, so:
#     BUY  -> P&L(open -> sl) is negative when sl is below the open price.
#     SELL -> P&L(open -> sl) is negative when sl is above the open price.
#   That signed result is `expected_pnl_at_sl`. `monetary_risk_to_sl` is then
#   max(0, -expected_pnl_at_sl): a breakeven or profit-locking stop yields a
#   valid non-negative ZERO RISK, and the signed value is preserved separately
#   so locked-profit geometry is never hidden.
#
# STOP DIRECTION is validated separately against the CURRENT CLOSE-OUT price
# (`position.price`: Bid for BUY, Ask for SELL) because a broker stop is
# triggered against the live market, not against the historical entry. An
# out-of-direction SL is preserved verbatim and yields risk = None.


def _tick_value_exact_pnl(
    *, side: str, open_price: float, close_price: float, volume: float,
    tick_size: float | None, tick_value: float | None,
    tick_value_profit: float | None, tick_value_loss: float | None,
) -> float | None:
    """Exact broker tick valuation of closing at ``close_price``.

    ``(close - open) / tick_size`` is the signed tick displacement; the tick
    value is the account-currency value of one tick for one lot. MT5 exposes
    separate profit/loss tick values for asymmetric instruments, so the correct
    one is selected by the SIGN of the displacement — never by direction.
    """
    if tick_size is None or tick_size <= 0 or volume is None or volume <= 0:
        return None
    ticks = (float(close_price) - float(open_price)) / float(tick_size)
    if not math.isfinite(ticks):
        return None
    if ticks >= 0:
        value = tick_value_profit if tick_value_profit else tick_value
    else:
        value = tick_value_loss if tick_value_loss else tick_value
    if value is None or value <= 0:
        return None
    result = ticks * float(value) * float(volume)
    if not math.isfinite(result):
        return None
    # A BUY gains when price rises, a SELL gains when price falls. The tick value
    # is always a magnitude, so the side fixes the sign.
    signed = result if str(side).upper() == "BUY" else -result
    return float(signed)


def _compute_risk_at_stop(
    *, side: str, broker_symbol: str, volume: float | None,
    open_price: float | None, sl: float | None,
    calculator: ProfitCalculator | None,
    tick_size: float | None, tick_value: float | None,
    tick_value_profit: float | None, tick_value_loss: float | None,
) -> tuple[float | None, float | None, str]:
    """Return ``(expected_pnl_at_sl, monetary_risk_to_sl, method)``.

    ``monetary_risk_to_sl`` is ``None`` whenever the value is not provable — it
    is NEVER a false zero.
    """
    if volume is None or volume <= 0 or open_price is None or open_price <= 0:
        return None, None, "UNAVAILABLE_MISSING_VOLUME_OR_OPEN_PRICE"
    if sl is None or sl <= 0:
        return None, None, "UNPROTECTED_NO_STOP"

    expected: float | None = None
    method = "UNAVAILABLE"
    if calculator is not None:
        try:
            expected = calculator.calc_profit(
                side=side, broker_symbol=broker_symbol, volume=float(volume),
                open_price=float(open_price), close_price=float(sl),
            )
        except PositionSetUnavailable:
            expected = None
        except Exception as exc:  # never let a broker IPC fault escape
            logger.warning(
                "[POSITION_RISK] broker profit calculation failed symbol=%s "
                "error=%s", broker_symbol, type(exc).__name__)
            expected = None
        if expected is not None:
            if not isinstance(expected, (int, float)) or isinstance(expected, bool) \
                    or not math.isfinite(float(expected)):
                expected = None
            else:
                expected = float(expected)
                method = "BROKER_ORDER_CALC_PROFIT"

    if expected is None:
        expected = _tick_value_exact_pnl(
            side=side, open_price=float(open_price), close_price=float(sl),
            volume=float(volume), tick_size=tick_size, tick_value=tick_value,
            tick_value_profit=tick_value_profit, tick_value_loss=tick_value_loss,
        )
        if expected is not None:
            method = "BROKER_TICK_VALUE_EXACT"

    if expected is None:
        return None, None, "UNAVAILABLE_NO_EXACT_MONETARY_PATH"
    # A breakeven / profit-locking stop is a VALID non-negative risk.
    risk = max(0.0, -float(expected))
    return expected, risk, method


# ═══════════════════════════════════════════════════════════════════════════
# POSITION PRODUCER — per-position capture
# ═══════════════════════════════════════════════════════════════════════════


def _read_spec(
    spec_source: SymbolSpecSource | None, broker_symbol: str,
    unavailable: list[str],
) -> dict[str, Any]:
    """Read this account's broker spec for one broker symbol.

    The existing broker portability/spec layer is the ONLY spec truth source;
    nothing here re-derives digits, point, tick size/value or contract size.
    """
    if spec_source is None:
        unavailable.append("broker_spec")
        return {}
    try:
        spec = spec_source.read_symbol_spec(broker_symbol)
    except Exception as exc:
        unavailable.append("broker_spec")
        logger.warning("[POSITION_SPEC] spec read failed symbol=%s error=%s",
                       broker_symbol, type(exc).__name__)
        return {}
    if spec is None:
        unavailable.append("broker_spec")
        return {}
    return dict(spec)


def capture_position_snapshot(
    account: AccountIdentity,
    row: Mapping[str, Any],
    *,
    observed_at_utc: str,
    observed_at_utc_ms: int,
    observation_id: str,
    source_name: str,
    canonical_symbol: str,
    spec_source: SymbolSpecSource | None = None,
    calculator: ProfitCalculator | None = None,
    risk_currency: str | None = None,
) -> PositionSnapshot:
    """Build one typed, account-safe position snapshot from ONE broker row.

    Read-only telemetry: this function never places, modifies or closes a trade
    and never raises for a broker-data problem — a defect degrades the status
    and records the reason instead.
    """
    _validate_identity(account)
    if not isinstance(row, Mapping):
        raise PositionSnapshotError("POSITION_ROW_MAPPING_REQUIRED")
    observed_ms = _validate_observation_ms(observed_at_utc_ms)
    label = _validate_source(source_name)
    if not str(canonical_symbol or "").strip():
        raise PositionSnapshotError("CANONICAL_SYMBOL_REQUIRED")

    unavailable: list[str] = []
    invalid: list[str] = []

    ticket = _coerce_int(_read_field(row, "ticket", "position_ticket"),
                         "position_ticket", unavailable, invalid)
    broker_symbol = _coerce_str(_read_field(row, "symbol"), "broker_symbol",
                                unavailable, invalid) or ""

    # ── SIDE: MT5 type 0 = BUY, 1 = SELL. Never guessed. ──────────────────
    raw_type = _read_field(row, "type", "side")
    side: PositionSide | None = None
    if raw_type is None:
        unavailable.append("side")
    elif isinstance(raw_type, str) and not isinstance(raw_type, bool):
        text = raw_type.strip().upper()
        if text in ("BUY", "SELL"):
            side = PositionSide(text)
        else:
            invalid.append("side")
    elif isinstance(raw_type, int) and not isinstance(raw_type, bool):
        if raw_type == MT5_POSITION_TYPE_BUY:
            side = PositionSide.BUY
        elif raw_type == MT5_POSITION_TYPE_SELL:
            side = PositionSide.SELL
        else:
            invalid.append("side")
    else:
        invalid.append("side")

    volume = _coerce_float(
        _read_field(row, "volume", "volume_current"), "volume", unavailable, invalid)
    open_price = _coerce_float(
        _read_field(row, "price_open", "open_price"), "open_price", unavailable, invalid)
    current_price = _coerce_float(
        _read_field(row, "price", "price_current", "current_price"),
        "current_price", unavailable, invalid)
    sl_raw = _coerce_float(_read_field(row, "sl", "stop_loss"), "sl", unavailable, invalid)
    tp_raw = _coerce_float(_read_field(row, "tp", "take_profit"), "tp",
                           unavailable, invalid)
    magic = _coerce_int(_read_field(row, "magic"), "magic", unavailable, invalid)
    comment = _coerce_str(_read_field(row, "comment"), "comment", unavailable, invalid)
    position_time = _coerce_int(
        _read_field(row, "time", "position_time"), "position_time",
        unavailable, invalid)
    position_time_ms = _coerce_int(
        _read_field(row, "time_msc", "position_time_ms"), "position_time_ms",
        unavailable, invalid)
    order_ticket = _coerce_int(
        _read_field(row, "order", "order_ticket"), "order_ticket",
        unavailable, invalid)
    # Broker-reported per-position floating P&L: authoritative, in ACCOUNT
    # CURRENCY. Positive, negative and exact zero are all preserved; only a
    # genuinely absent value is unavailable.
    floating_pnl = _coerce_float(
        _read_field(row, "profit", "floating_pnl"), "floating_pnl",
        unavailable, invalid)

    spec = _read_spec(spec_source, broker_symbol, unavailable)
    digits = _coerce_int(spec.get("digits"), "digits", unavailable, invalid)
    point = _coerce_float(spec.get("point"), "point", unavailable, invalid)
    tick_size = _coerce_float(spec.get("trade_tick_size"), "tick_size", unavailable, invalid)
    tick_value = _coerce_float(
        spec.get("trade_tick_value"), "tick_value", unavailable, invalid)
    tick_value_profit = _coerce_float(
        spec.get("trade_tick_value_profit"), "tick_value_profit", unavailable, invalid)
    tick_value_loss = _coerce_float(
        spec.get("trade_tick_value_loss"), "tick_value_loss", unavailable, invalid)
    contract_size = _coerce_float(
        spec.get("trade_contract_size"), "contract_size", unavailable, invalid)
    volume_min = _coerce_float(spec.get("volume_min"), "volume_min", unavailable, invalid)
    volume_max = _coerce_float(spec.get("volume_max"), "volume_max", unavailable, invalid)
    volume_step = _coerce_float(spec.get("volume_step"), "volume_step", unavailable, invalid)
    trade_mode = _coerce_int(spec.get("trade_mode"), "trade_mode", unavailable, invalid)
    trade_calc_mode = _coerce_int(
        spec.get("trade_calc_mode"), "trade_calc_mode", unavailable, invalid)
    pip_size = _coerce_float(spec.get("pip_size"), "pip_size", unavailable, invalid)

    # ── OBSERVED volume is validated, never normalised (§11) ───────────────
    _volume_defect(volume, volume_min, volume_max, volume_step, invalid)

    # ── RISK GEOMETRY: stop side is judged against the CURRENT CLOSE-OUT
    #    price (Bid for BUY, Ask for SELL) because a broker stop triggers on
    #    the live market. The open price is NOT substituted silently. ───────
    has_stop = bool(sl_raw is not None and sl_raw > 0)
    risk_price = current_price
    stop_side_valid: bool | None = None
    stop_distance_price: float | None = None
    stop_distance_points: float | None = None
    stop_distance_pips: float | None = None

    if has_stop and risk_price is not None and risk_price > 0 and side is not None:
        stop_side_valid = (
            sl_raw < risk_price if side is PositionSide.BUY
            else sl_raw > risk_price
        )
        stop_distance_price = abs(float(risk_price) - float(sl_raw))
        if point is not None and point > 0:
            stop_distance_points = stop_distance_price / float(point)
        if pip_size is not None and pip_size > 0:
            stop_distance_pips = stop_distance_price / float(pip_size)
    elif has_stop and (risk_price is None or risk_price <= 0 or side is None):
        unavailable.append("risk_price")

    # ── MONETARY RISK (account currency; None means UNKNOWN, never 0) ─────
    expected_pnl_at_sl: float | None = None
    monetary_risk: float | None = None
    risk_calculation = "UNAVAILABLE"
    if side is not None and not invalid:
        expected_pnl_at_sl, monetary_risk, risk_calculation = _compute_risk_at_stop(
            side=side.value, broker_symbol=broker_symbol, volume=volume,
            open_price=open_price, sl=sl_raw, calculator=calculator,
            tick_size=tick_size, tick_value=tick_value,
            tick_value_profit=tick_value_profit, tick_value_loss=tick_value_loss,
        )

    # An out-of-direction SL is preserved verbatim and yields NO risk value.
    if stop_side_valid is False:
        expected_pnl_at_sl = None
        monetary_risk = None
        risk_calculation = "INVALID_STOP_DIRECTION"
        invalid.append("stop_side")

    # ── STATUS: fail closed, never a false zero ───────────────────────────
    if invalid:
        status = PositionStatus.INVALID
    elif ticket is None or ticket <= 0:
        status = PositionStatus.INVALID
    elif not has_stop:
        # An open position with no protective stop is UNPROTECTED, not zero risk.
        status = PositionStatus.UNPROTECTED
    elif monetary_risk is None:
        status = PositionStatus.PARTIAL
    else:
        status = PositionStatus.COMPLETE

    return PositionSnapshot(
        account_id=account.account_id, broker=account.broker,
        server=account.server, login=account.login,
        position_snapshot_id=(
            derive_position_snapshot_id(account, ticket, observed_ms, label)
            if ticket is not None and ticket > 0 else ""),
        observation_id=observation_id,
        position_ticket=ticket if ticket is not None else 0,
        canonical_symbol=str(canonical_symbol).strip(),
        broker_symbol=broker_symbol,
        observed_at_utc=observed_at_utc,
        observed_at_utc_ms=observed_ms,
        source=label,
        side=side if side is not None else PositionSide.BUY,
        volume=volume, open_price=open_price, current_price=current_price,
        sl=sl_raw, tp=tp_raw,
        magic=magic, comment=comment, position_time=position_time,
        position_time_ms=position_time_ms, order_ticket=order_ticket,
        risk_price=risk_price, stop_distance_price=stop_distance_price,
        stop_distance_points=stop_distance_points,
        stop_distance_pips=stop_distance_pips,
        has_stop=has_stop, stop_side_valid=stop_side_valid,
        floating_pnl=floating_pnl,
        monetary_risk_to_sl=monetary_risk,
        expected_pnl_at_sl=expected_pnl_at_sl,
        risk_currency=risk_currency,
        risk_calculation=risk_calculation,
        digits=digits, point=point, tick_size=tick_size, tick_value=tick_value,
        tick_value_profit=tick_value_profit, tick_value_loss=tick_value_loss,
        contract_size=contract_size, volume_min=volume_min, volume_max=volume_max,
        volume_step=volume_step, trade_mode=trade_mode,
        trade_calc_mode=trade_calc_mode, pip_size=pip_size,
        status=status,
        unavailable_fields=tuple(sorted(set(unavailable))),
        invalid_fields=tuple(sorted(set(invalid))),
        source_error=None,
    )


# ═══════════════════════════════════════════════════════════════════════════
# ACCOUNT OPEN-RISK AGGREGATE (§20, §21, §22, §24)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AccountOpenRiskSnapshot:
    """One account's aggregate open monetary risk for ONE observation cycle.

    AGGREGATE SEMANTICS — NEVER UNDERSTATE RISK
    -------------------------------------------
    ``known_open_risk`` is the sum of the risks that are actually provable.
    ``total_open_risk`` is the AUTHORITATIVE total and is published ONLY when
    ``risk_complete`` is true. If ANY open position has unknown or unbounded
    risk then ``risk_complete`` is False and ``total_open_risk`` is ``None`` —
    never the sum of the known subset, which would silently understate exposure.
    ``known_open_risk`` remains available so an operator can still see the floor.
    """

    account_id: str
    broker: str
    server: str
    login: int

    open_risk_id: str
    observation_id: str
    account_snapshot_id: str | None
    observed_at_utc: str
    observed_at_utc_ms: int
    source: str

    open_position_count: int | None = None
    protected_position_count: int | None = None
    unprotected_position_count: int | None = None
    unknown_risk_position_count: int | None = None

    known_open_risk: float | None = None
    total_open_risk: float | None = None
    floating_pnl_total: float | None = None
    currency: str | None = None

    status: PositionStatus = PositionStatus.COMPLETE
    risk_complete: bool = False
    position_set_complete: bool = False
    # The exact open-ticket set proven at this observation. This is the
    # closure/absence boundary: a ticket absent here is NOT open, and it must
    # never be inferred merely from not having appeared recently.
    position_tickets: tuple[int, ...] = ()
    unavailable_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()
    source_error: str | None = None
    consistency_ok: bool | None = None
    consistency_detail: str | None = None

    @property
    def identity(self) -> AccountIdentity:
        return AccountIdentity(
            account_id=self.account_id, broker=self.broker,
            server=self.server, login=self.login,
        )

    @property
    def has_authoritative_total(self) -> bool:
        """True only when a complete set of provable risks was aggregated."""
        return bool(self.risk_complete and self.total_open_risk is not None)

    def age_ms(self, *, now_ms: int) -> int | None:
        if self.observed_at_utc_ms <= 0:
            return None
        return max(0, int(now_ms) - int(self.observed_at_utc_ms))

    def is_stale(self, *, now_ms: int, threshold_ms: int) -> bool:
        if threshold_ms <= 0:
            raise ValueError("FRESHNESS_THRESHOLD_MUST_BE_POSITIVE")
        age = self.age_ms(now_ms=now_ms)
        return age is None or age > threshold_ms

    def evaluate(self, *, now_ms: int, threshold_ms: int) -> "AccountOpenRiskSnapshot":
        if self.is_stale(now_ms=now_ms, threshold_ms=threshold_ms):
            return replace(self, status=PositionStatus.STALE)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": OPEN_RISK_SCHEMA_VERSION,
            "record_kind": "ACCOUNT_OPEN_RISK",
            "account_id": self.account_id,
            "broker": self.broker,
            "server": self.server,
            "login": self.login,
            "open_risk_id": self.open_risk_id,
            "observation_id": self.observation_id,
            "account_snapshot_id": self.account_snapshot_id,
            "observed_at_utc": self.observed_at_utc,
            "observed_at_utc_ms": self.observed_at_utc_ms,
            "source": self.source,
            "open_position_count": self.open_position_count,
            "protected_position_count": self.protected_position_count,
            "unprotected_position_count": self.unprotected_position_count,
            "unknown_risk_position_count": self.unknown_risk_position_count,
            "known_open_risk": self.known_open_risk,
            "total_open_risk": self.total_open_risk,
            "floating_pnl_total": self.floating_pnl_total,
            "currency": self.currency,
            "status": self.status.value,
            "risk_complete": self.risk_complete,
            "position_set_complete": self.position_set_complete,
            "position_tickets": list(self.position_tickets),
            "unavailable_fields": list(self.unavailable_fields),
            "invalid_fields": list(self.invalid_fields),
            "source_error": self.source_error,
            "consistency_ok": self.consistency_ok,
            "consistency_detail": self.consistency_detail,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AccountOpenRiskSnapshot":
        return cls(
            account_id=str(payload["account_id"]),
            broker=str(payload["broker"]),
            server=str(payload["server"]),
            login=int(payload["login"]),
            open_risk_id=str(payload["open_risk_id"]),
            observation_id=str(payload["observation_id"]),
            account_snapshot_id=payload.get("account_snapshot_id"),
            observed_at_utc=str(payload["observed_at_utc"]),
            observed_at_utc_ms=int(payload["observed_at_utc_ms"]),
            source=str(payload["source"]),
            open_position_count=payload.get("open_position_count"),
            protected_position_count=payload.get("protected_position_count"),
            unprotected_position_count=payload.get("unprotected_position_count"),
            unknown_risk_position_count=payload.get("unknown_risk_position_count"),
            known_open_risk=payload.get("known_open_risk"),
            total_open_risk=payload.get("total_open_risk"),
            floating_pnl_total=payload.get("floating_pnl_total"),
            currency=payload.get("currency"),
            status=PositionStatus(payload["status"]),
            risk_complete=bool(payload["risk_complete"]),
            position_set_complete=bool(payload["position_set_complete"]),
            position_tickets=tuple(
                int(t) for t in (payload.get("position_tickets") or ())),
            unavailable_fields=tuple(payload.get("unavailable_fields") or ()),
            invalid_fields=tuple(payload.get("invalid_fields") or ()),
            source_error=payload.get("source_error"),
            consistency_ok=payload.get("consistency_ok"),
            consistency_detail=payload.get("consistency_detail"),
        )


def _unavailable_position_set(
    account: AccountIdentity, observed_ms: int, observed_iso: str,
    source: str, observation_id: str, error: str,
) -> PositionSetObservation:
    """Explicit UNAVAILABLE set record — never an empty portfolio."""
    return PositionSetObservation(
        account_id=account.account_id, broker=account.broker,
        server=account.server, login=account.login,
        position_snapshot_id=derive_position_set_id(
            account, observed_ms, source),
        observation_id=observation_id, observed_at_utc=observed_iso,
        observed_at_utc_ms=observed_ms, source=source,
        position_set_complete=False, open_position_count=None,
        position_tickets=(), status=PositionStatus.UNAVAILABLE,
        source_error=error or "POSITION_SOURCE_UNAVAILABLE",
        consistency_ok=None, consistency_detail=None,
    )


def _unavailable_open_risk(
    account: AccountIdentity, observed_ms: int, observed_iso: str,
    source: str, observation_id: str, error: str,
    account_snapshot_id: str | None, currency: str | None,
) -> AccountOpenRiskSnapshot:
    """Explicit UNAVAILABLE aggregate — never "zero risk"."""
    return AccountOpenRiskSnapshot(
        account_id=account.account_id, broker=account.broker,
        server=account.server, login=account.login,
        open_risk_id=derive_open_risk_id(account, observed_ms, source),
        observation_id=observation_id,
        account_snapshot_id=account_snapshot_id,
        observed_at_utc=observed_iso, observed_at_utc_ms=observed_ms, source=source,
        open_position_count=None, protected_position_count=None,
        unprotected_position_count=None, unknown_risk_position_count=None,
        known_open_risk=None, total_open_risk=None, floating_pnl_total=None,
        currency=currency, status=PositionStatus.UNAVAILABLE,
        risk_complete=False, position_set_complete=False, position_tickets=(),
        unavailable_fields=("open_position_set",), invalid_fields=(),
        source_error=error or "POSITION_SOURCE_UNAVAILABLE",
        consistency_ok=None, consistency_detail=None,
    )


def _consistency_defects(
    observation: PositionSetObservation,
    positions: Sequence[PositionSnapshot],
    *,
    account_id: str,
    open_count: int,
    protected: int,
    unprotected: int,
    unknown_risk: int,
) -> list[str]:
    """Bounded cross-record consistency checks. Any failure fails closed."""
    defects: list[str] = []
    if open_count != len(positions):
        defects.append(f"OPEN_COUNT_MISMATCH:{open_count}!={len(positions)}")
    if protected + unprotected != open_count:
        defects.append(
            f"PROTECTED_UNPROTECTED_MISMATCH:{protected}+{unprotected}!={open_count}")
    if (open_count - unknown_risk) + unknown_risk != open_count:
        defects.append("RISK_COUNT_MISMATCH")
    if any(snapshot.account_id != account_id for snapshot in positions):
        defects.append("ACCOUNT_ID_MISMATCH_IN_POSITION_ROWS")
    if any(snapshot.observation_id != observation.observation_id
           for snapshot in positions):
        defects.append("OBSERVATION_ID_MISMATCH_IN_POSITION_ROWS")
    tickets = [snapshot.position_ticket for snapshot in positions]
    if len(set(tickets)) != len(tickets):
        defects.append("DUPLICATE_POSITION_TICKET")
    if observation.position_set_complete and set(tickets) != set(
            observation.position_tickets):
        defects.append("POSITION_SET_TICKET_MISMATCH")
    return defects


def aggregate_open_risk(
    account: AccountIdentity,
    observation: PositionSetObservation,
    positions: Sequence[PositionSnapshot],
    *,
    currency: str | None = None,
    account_snapshot_id: str | None = None,
) -> AccountOpenRiskSnapshot:
    """Aggregate ONE complete position-set observation into account open risk.

    The aggregate never understates risk: an authoritative ``total_open_risk``
    exists only when every open position's risk is provable.
    """
    _validate_identity(account)
    if not isinstance(observation, PositionSetObservation):
        raise PositionSnapshotError("POSITION_SET_OBSERVATION_REQUIRED")
    if observation.account_id != account.account_id:
        raise AccountIdentityError("OBSERVATION_ACCOUNT_MISMATCH")

    rows = tuple(positions)
    unknown: list[str] = []
    invalid: list[str] = []
    if observation.source_error:
        unknown.append("open_position_set")

    protected = sum(1 for snapshot in rows if snapshot.is_protected)
    unprotected = len(rows) - protected
    unknown_risk = sum(1 for snapshot in rows if not snapshot.has_known_risk)
    open_count = len(rows)

    known_open_risk = sum(
        snapshot.monetary_risk_to_sl for snapshot in rows
        if snapshot.monetary_risk_to_sl is not None)
    floating_values = [snapshot.floating_pnl for snapshot in rows]
    floating_total = (
        sum(value for value in floating_values if value is not None)
        if all(value is not None for value in floating_values) else None
    )

    # An AUTHORITATIVE total requires a complete set AND a provable risk for
    # EVERY open position. Otherwise the total is withheld, never approximated.
    risk_complete = bool(
        observation.position_set_complete
        and open_count > 0 and unknown_risk == 0
    )
    # A provably EMPTY complete set is a legitimate, complete zero-risk account.
    empty_complete = bool(observation.position_set_complete and open_count == 0)
    total_open_risk = (
        float(known_open_risk) if (risk_complete or empty_complete) else None
    )

    defects = _consistency_defects(
        observation, rows, account_id=account.account_id,
        open_count=open_count, protected=protected,
        unprotected=unprotected, unknown_risk=unknown_risk,
    )
    if defects:
        invalid.extend(defects)
        # Fail closed: a self-inconsistent aggregate publishes NO money at all.
        known_open_risk = None
        total_open_risk = None
        floating_total = None
        risk_complete = False

    if not observation.position_set_complete:
        status = PositionStatus.UNAVAILABLE
    elif defects:
        status = PositionStatus.INVALID
    elif unprotected:
        # An open, unprotected position leaves the account risk incomplete.
        status = PositionStatus.UNPROTECTED
    elif unknown_risk:
        status = PositionStatus.PARTIAL
    else:
        status = PositionStatus.COMPLETE

    return AccountOpenRiskSnapshot(
        account_id=account.account_id, broker=account.broker,
        server=account.server, login=account.login,
        open_risk_id=derive_open_risk_id(
            account, observation.observed_at_utc_ms, observation.source),
        observation_id=observation.observation_id,
        account_snapshot_id=account_snapshot_id,
        observed_at_utc=observation.observed_at_utc,
        observed_at_utc_ms=observation.observed_at_utc_ms,
        source=observation.source,
        open_position_count=open_count,
        protected_position_count=protected,
        unprotected_position_count=unprotected,
        unknown_risk_position_count=unknown_risk,
        known_open_risk=known_open_risk,
        total_open_risk=total_open_risk,
        floating_pnl_total=floating_total,
        currency=currency,
        status=status,
        risk_complete=risk_complete or empty_complete,
        position_set_complete=observation.position_set_complete,
        position_tickets=observation.position_tickets,
        unavailable_fields=tuple(sorted(set(unknown))),
        invalid_fields=tuple(sorted(set(invalid))),
        source_error=observation.source_error,
        consistency_ok=(not defects) if observation.position_set_complete else None,
        consistency_detail=(";".join(defects) if defects else None),
    )


# ═══════════════════════════════════════════════════════════════════════════
# DURABLE PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════
#
# Local durable write (JSONL + fsync)  ->  certified pre-write handoff
#   -> canonical outbox  ->  worker  ->  ACK
#
# There is deliberately NO best-effort S3 mirror here. The local JSONL append is
# canonical local truth; S3 is reached only through the Block 1 outbox.
#
# Both datasets are ACCOUNT-SCOPED and DATE-partitioned (never symbol-scoped):
# a position set is a property of one account at one instant, not of a symbol.


def _position_local_path(base_dir: str | Path, record: Any) -> Path:
    date = record.observed_at_utc[:10]
    return Path(base_dir) / date / f"{record.account_id}.jsonl"


def _open_risk_local_path(base_dir: str | Path, record: Any) -> Path:
    date = record.observed_at_utc[:10]
    return Path(base_dir) / date / f"{record.account_id}.jsonl"


def _append_durable_jsonl(
    *, dataset: str, payload: Mapping[str, Any], line: str,
    partition_date: str, path: Path, outbox: Any | None,
) -> None:
    """Shared certified write: handoff journal FIRST, then local fsync.

    The certified pre-write handoff MUST be journalled before the local append
    so a crash can never leave durable local evidence with no recovery intent.
    """
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
        event_id=f"account-risk:{dataset}:{identity_token}",
        lifecycle_stage="ACCOUNT_RISK_OBSERVATION",
        dataset=dataset,
        identity=identity,
        timestamp=str(payload.get("observed_at_utc") or ""),
        producer="core.risk.position_snapshot._append_durable_jsonl",
        trigger="ACCOUNT_RISK_LOCAL_FSYNC",
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


def persist_position_snapshot(
    snapshot: PositionSnapshot,
    *,
    base_dir: str | Path = DEFAULT_LOCAL_DIR,
    outbox: Any | None = None,
) -> bool:
    """Durably persist ONE position observation through the canonical path."""
    if not isinstance(snapshot, PositionSnapshot):
        raise PositionSnapshotError("POSITION_SNAPSHOT_REQUIRED")
    payload = snapshot.to_dict()
    _append_durable_jsonl(
        dataset=DATASET, payload=payload, line=_canonical_json(payload),
        partition_date=snapshot.observed_at_utc[:10],
        path=_position_local_path(base_dir, snapshot), outbox=outbox,
    )
    return True


def persist_position_set(
    observation: PositionSetObservation,
    *,
    base_dir: str | Path = DEFAULT_LOCAL_DIR,
    outbox: Any | None = None,
) -> bool:
    """Durably persist ONE position-set observation (the closure boundary)."""
    if not isinstance(observation, PositionSetObservation):
        raise PositionSnapshotError("POSITION_SET_OBSERVATION_REQUIRED")
    payload = observation.to_dict()
    _append_durable_jsonl(
        dataset=DATASET, payload=payload, line=_canonical_json(payload),
        partition_date=observation.observed_at_utc[:10],
        path=_position_local_path(base_dir, observation), outbox=outbox,
    )
    return True


def persist_open_risk(
    aggregate: AccountOpenRiskSnapshot,
    *,
    base_dir: str | Path = DEFAULT_OPEN_RISK_DIR,
    outbox: Any | None = None,
) -> bool:
    """Durably persist ONE account open-risk aggregate."""
    if not isinstance(aggregate, AccountOpenRiskSnapshot):
        raise PositionSnapshotError("OPEN_RISK_SNAPSHOT_REQUIRED")
    payload = aggregate.to_dict()
    _append_durable_jsonl(
        dataset=OPEN_RISK_DATASET, payload=payload, line=_canonical_json(payload),
        partition_date=aggregate.observed_at_utc[:10],
        path=_open_risk_local_path(base_dir, aggregate), outbox=outbox,
    )
    return True


# ═══════════════════════════════════════════════════════════════════════════
# OBSERVATION CYCLE (§23)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PositionObservationCycle:
    """Everything ONE observation cycle produced for ONE exact account.

    All four record groups share the same ``observation_id``:
      account snapshot (2A linkage) + position set + position rows + open risk.
    """

    account: AccountIdentity
    observation_id: str
    observed_at_utc: str
    observed_at_utc_ms: int
    position_set: PositionSetObservation
    positions: tuple[PositionSnapshot, ...]
    open_risk: AccountOpenRiskSnapshot

    @property
    def account_id(self) -> str:
        return self.account.account_id


def _resolve_canonical_symbol(
    broker_symbol: str, resolver: Any,
) -> str | None:
    """Map a broker symbol onto its canonical upstream name.

    Canonical naming is owned upstream (the existing symbol resolution layer).
    This never invents a mapping: an unresolvable broker symbol stays ``None`` so
    the canonical identity is explicitly unavailable rather than fabricated.
    """
    if not str(broker_symbol or "").strip():
        return None
    if resolver is None:
        return str(broker_symbol).strip()
    try:
        canonical = resolver(broker_symbol)
    except Exception as exc:
        logger.warning(
            "[POSITION_SYMBOL] canonical resolution failed symbol=%s error=%s",
            broker_symbol, type(exc).__name__)
        return None
    text = str(canonical or "").strip()
    return text or None


def observe_positions(
    account: AccountIdentity,
    source: PositionSource,
    *,
    clock: Callable[[], datetime] = utc_now,
    source_name: str = "MT5_POSITIONS_GET",
    spec_source: SymbolSpecSource | None = None,
    calculator: ProfitCalculator | None = None,
    canonical_resolver: Any = None,
    currency: str | None = None,
    account_snapshot_id: str | None = None,
) -> PositionObservationCycle:
    """Observe ONE exact account's positions, position set and open risk.

    Fails closed and never raises for a broker problem. An unreadable source
    yields an explicit UNAVAILABLE position set and an UNAVAILABLE aggregate —
    never an empty portfolio and never a zero-risk account.
    """
    _validate_identity(account)
    if source is None:
        raise ValueError("POSITION_SOURCE_REQUIRED")
    label = _validate_source(source_name)

    observed = clock()
    observed_ms = to_epoch_ms(observed)
    observed_iso = to_iso_utc(observed)
    observation_id = derive_observation_id(account, observed_ms, label)

    # ── READ THE POSITION SET (failure is NOT an empty portfolio) ─────────
    source_error: str | None = None
    rows: Sequence[Any] | None
    try:
        rows = source.read_positions()
    except PositionSetUnavailable as exc:
        rows, source_error = None, str(exc) or "POSITION_SOURCE_UNAVAILABLE"
    except Exception as exc:
        rows, source_error = None, f"POSITION_SOURCE_ERROR:{type(exc).__name__}"

    if rows is None:
        observation = _unavailable_position_set(
            account, observed_ms, observed_iso, label, observation_id,
            source_error or "POSITION_SOURCE_UNAVAILABLE",
        )
        aggregate = _unavailable_open_risk(
            account, observed_ms, observed_iso, label, observation_id,
            source_error or "POSITION_SOURCE_UNAVAILABLE",
            account_snapshot_id, currency,
        )
        return PositionObservationCycle(
            account=account, observation_id=observation_id,
            observed_at_utc=observed_iso, observed_at_utc_ms=observed_ms,
            position_set=observation, positions=(), open_risk=aggregate,
        )

    if isinstance(rows, (str, bytes, Mapping)):
        # A scalar/None-ish payload is not a position set; fail closed.
        observation = _unavailable_position_set(
            account, observed_ms, observed_iso, label, observation_id,
            "POSITION_SET_MALFORMED",
        )
        aggregate = _unavailable_open_risk(
            account, observed_ms, observed_iso, label, observation_id,
            "POSITION_SET_MALFORMED", account_snapshot_id, currency,
        )
        return PositionObservationCycle(
            account=account, observation_id=observation_id,
            observed_at_utc=observed_iso, observed_at_utc_ms=observed_ms,
            position_set=observation, positions=(), open_risk=aggregate,
        )

    # ── BUILD ONE TYPED SNAPSHOT PER OBSERVED POSITION ─────────────────────
    snapshots: list[PositionSnapshot] = []
    tickets: list[int] = []
    for row in rows:
        if not isinstance(row, Mapping):
            # An unreadable row cannot be given an exact identity; the set is
            # still complete but the row count no longer matches what we can
            # prove, so the aggregate's consistency check fails closed later.
            logger.warning("[POSITION_ROW] non-mapping position row ignored")
            continue
        broker_symbol = _read_field(row, "symbol") or ""
        canonical = _resolve_canonical_symbol(str(broker_symbol), canonical_resolver)
        snapshot = capture_position_snapshot(
            account, row,
            observed_at_utc=observed_iso, observed_at_utc_ms=observed_ms,
            observation_id=observation_id, source_name=label,
            canonical_symbol=canonical or f"UNRESOLVED:{broker_symbol}",
            spec_source=spec_source, calculator=calculator, risk_currency=currency,
        )
        snapshots.append(snapshot)
        if snapshot.position_ticket > 0:
            tickets.append(snapshot.position_ticket)

    observation = PositionSetObservation(
        account_id=account.account_id, broker=account.broker,
        server=account.server, login=account.login,
        position_snapshot_id=derive_position_set_id(account, observed_ms, label),
        observation_id=observation_id, observed_at_utc=observed_iso,
        observed_at_utc_ms=observed_ms, source=label,
        position_set_complete=True, open_position_count=len(tickets),
        position_tickets=tuple(sorted(tickets)),
        status=PositionStatus.COMPLETE, source_error=None,
        consistency_ok=True, consistency_detail=None,
    )
    aggregate = aggregate_open_risk(
        account, observation, snapshots, currency=currency,
        account_snapshot_id=account_snapshot_id,
    )
    return PositionObservationCycle(
        account=account, observation_id=observation_id,
        observed_at_utc=observed_iso, observed_at_utc_ms=observed_ms,
        position_set=observation, positions=tuple(snapshots), open_risk=aggregate,
    )


class PositionSnapshotProducer:
    """Focused producer/service for position and open-risk telemetry.

    Owns the read -> validate -> timestamp -> persist path so downstream Block 2
    consumers never call raw ``mt5.positions_get`` themselves. The producer is
    strictly READ-ONLY: it never places, modifies or closes a trade.
    """

    def __init__(
        self,
        source: PositionSource,
        *,
        base_dir: str | Path = DEFAULT_LOCAL_DIR,
        open_risk_dir: str | Path = DEFAULT_OPEN_RISK_DIR,
        clock: Callable[[], datetime] = utc_now,
        source_name: str = "MT5_POSITIONS_GET",
        spec_source: SymbolSpecSource | None = None,
        calculator: ProfitCalculator | None = None,
        canonical_resolver: Any = None,
        outbox: Any | None = None,
        persist: bool = True,
    ) -> None:
        self._source = source
        self._base_dir = base_dir
        self._open_risk_dir = open_risk_dir
        self._clock = clock
        self._source_name = source_name
        self._spec_source = spec_source
        self._calculator = calculator
        self._canonical_resolver = canonical_resolver
        self._outbox = outbox
        self._persist = persist

    def observe(
        self, account: AccountIdentity, *, currency: str | None = None,
        account_snapshot_id: str | None = None,
        source: PositionSource | None = None,
    ) -> PositionObservationCycle:
        """Observe one exact account and durably persist its telemetry.

        ``source`` overrides the producer's default read boundary for this one
        call. A multi-account fan-out MUST pass each account's own source:
        reading account B through account A's boundary would attribute one
        account's positions to the other.
        """
        cycle = observe_positions(
            account, self._source if source is None else source,
            clock=self._clock,
            source_name=self._source_name, spec_source=self._spec_source,
            calculator=self._calculator,
            canonical_resolver=self._canonical_resolver,
            currency=currency, account_snapshot_id=account_snapshot_id,
        )
        if self._persist:
            persist_position_set(
                cycle.position_set, base_dir=self._base_dir, outbox=self._outbox)
            for snapshot in cycle.positions:
                persist_position_snapshot(
                    snapshot, base_dir=self._base_dir, outbox=self._outbox)
            persist_open_risk(
                cycle.open_risk, base_dir=self._open_risk_dir, outbox=self._outbox)
        return cycle


# ═══════════════════════════════════════════════════════════════════════════
# POSITION STORE / LATEST INDEX  (convenience only)
# ═══════════════════════════════════════════════════════════════════════════
#
# The canonical append-only JSONL evidence remains authoritative. This index is
# a rebuildable convenience: it never mutates history, and it is reconstructed
# from the local snapshots if it is missing or corrupt.
#
# CURRENT-STATE RECONSTRUCTION is driven by the POSITION SET, never by recency.
# A ticket is "currently open" only when the latest COMPLETE position-set
# observation for that account explicitly lists it. A ticket that merely has not
# appeared recently is NOT open; that is how a closed position stops being
# returned as current instead of lingering forever in an append-only history.


class PositionSnapshotStore:
    """Exact-account position/open-risk lookup for Block 2 consumers.

    Guarantees:
      * exact ``account_id`` lookup only — never falls back to another account;
      * ``current_positions`` is governed by the latest complete position-set
        observation, so closed positions are never returned as open;
      * stale state is exposed, not hidden;
      * raises :class:`PositionSnapshotNotFound` rather than returning zeros.
    """

    def __init__(
        self,
        *,
        base_dir: str | Path = DEFAULT_LOCAL_DIR,
        open_risk_dir: str | Path = DEFAULT_OPEN_RISK_DIR,
        threshold_ms: int | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._open_risk_dir = Path(open_risk_dir)
        self._threshold_ms = (
            freshness_threshold_ms() if threshold_ms is None else int(threshold_ms)
        )
        if self._threshold_ms <= 0:
            raise ValueError("FRESHNESS_THRESHOLD_MUST_BE_POSITIVE")
        self._latest_position: dict[tuple[str, int], PositionSnapshot] = {}
        self._latest_set: dict[str, PositionSetObservation] = {}
        self._open_risk: dict[str, AccountOpenRiskSnapshot] = {}
        self.rebuild()

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
                                "[POSITION_STORE_INDEX] corrupt line in %s", path)
                            continue
                        if not isinstance(record, dict):
                            continue
                        yield path, record
            except OSError:
                logger.warning("[POSITION_STORE_INDEX] unreadable %s", path)

    def rebuild(self) -> None:
        """Reconstruct the indexes from the canonical local evidence."""
        latest_position: dict[tuple[str, int], PositionSnapshot] = {}
        latest_set: dict[str, PositionSetObservation] = {}
        for _path, record in self._iter_persisted(self._base_dir):
            try:
                kind = str(record.get("record_kind") or "POSITION")
                if kind == "POSITION_SET":
                    observation = PositionSetObservation.from_dict(record)
                    current = latest_set.get(observation.account_id)
                    if current is None or (
                            observation.observed_at_utc_ms
                            >= current.observed_at_utc_ms):
                        latest_set[observation.account_id] = observation
                else:
                    snapshot = PositionSnapshot.from_dict(record)
                    key = snapshot.ownership_key
                    current = latest_position.get(key)
                    if current is None or (
                            snapshot.observed_at_utc_ms
                            >= current.observed_at_utc_ms):
                        latest_position[key] = snapshot
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "[POSITION_STORE_INDEX] unusable position record in %s", _path)

        open_risk: dict[str, AccountOpenRiskSnapshot] = {}
        for _path, record in self._iter_persisted(self._open_risk_dir):
            try:
                aggregate = AccountOpenRiskSnapshot.from_dict(record)
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "[POSITION_STORE_INDEX] unusable open-risk record in %s", _path)
                continue
            current = open_risk.get(aggregate.account_id)
            if current is None or (
                    aggregate.observed_at_utc_ms >= current.observed_at_utc_ms):
                open_risk[aggregate.account_id] = aggregate

        self._latest_position = latest_position
        self._latest_set = latest_set
        self._open_risk = open_risk

    def _account_key(self, account_id: str) -> str:
        key = str(account_id or "").strip()
        if not key:
            raise PositionSnapshotNotFound("ACCOUNT_ID_REQUIRED")
        return key

    @property
    def threshold_ms(self) -> int:
        return self._threshold_ms

    def account_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._latest_set))

    def latest_position_set(
        self, account_id: str, *, now_ms: int | None = None,
    ) -> PositionSetObservation:
        """Latest position-set observation for EXACTLY ``account_id``."""
        key = self._account_key(account_id)
        observation = self._latest_set.get(key)
        if observation is None:
            self.rebuild()
            observation = self._latest_set.get(key)
        if observation is None:
            raise PositionSnapshotNotFound(f"NO_POSITION_SET:{key}")
        return observation

    def latest_position(
        self, account_id: str, ticket: int, *, now_ms: int | None = None,
    ) -> PositionSnapshot:
        """Latest observation of EXACTLY (account_id, position_ticket)."""
        key = self._account_key(account_id)
        try:
            ticket = int(ticket)
        except (TypeError, ValueError) as exc:
            raise PositionSnapshotNotFound("POSITION_TICKET_INVALID") from exc
        snapshot = self._latest_position.get((key, ticket))
        if snapshot is None:
            self.rebuild()
            snapshot = self._latest_position.get((key, ticket))
        if snapshot is None:
            raise PositionSnapshotNotFound(f"NO_POSITION:{key}:{ticket}")
        if now_ms is None:
            return snapshot
        return snapshot.evaluate(now_ms=int(now_ms), threshold_ms=self._threshold_ms)

    def current_positions(
        self, account_id: str, *, now_ms: int | None = None,
    ) -> tuple[PositionSnapshot, ...]:
        """Positions PROVEN open by the latest COMPLETE position-set observation.

        A ticket absent from that set is NOT returned, so a position closed on
        the broker side is never reported as still open. An INCOMPLETE set (a
        source failure) raises instead of falling back to stale rows.
        """
        observation = self.latest_position_set(account_id)
        if not observation.position_set_complete:
            raise PositionSnapshotNotFound(
                f"POSITION_SET_INCOMPLETE:{observation.account_id}:"
                f"{observation.source_error or 'UNKNOWN'}")
        rows: list[PositionSnapshot] = []
        for ticket in observation.position_tickets:
            try:
                snapshot = self.latest_position(
                    observation.account_id, ticket, now_ms=now_ms)
            except PositionSnapshotNotFound:
                continue
            rows.append(snapshot)
        return tuple(rows)

    def open_tickets(self, account_id: str) -> tuple[int, ...]:
        observation = self.latest_position_set(account_id)
        if not observation.position_set_complete:
            raise PositionSnapshotNotFound(
                f"POSITION_SET_INCOMPLETE:{observation.account_id}")
        return observation.position_tickets

    def latest_open_risk(
        self, account_id: str, *, now_ms: int | None = None,
    ) -> AccountOpenRiskSnapshot:
        """Latest open-risk aggregate for EXACTLY ``account_id``."""
        key = self._account_key(account_id)
        aggregate = self._open_risk.get(key)
        if aggregate is None:
            self.rebuild()
            aggregate = self._open_risk.get(key)
        if aggregate is None:
            raise PositionSnapshotNotFound(f"NO_OPEN_RISK:{key}")
        if now_ms is None:
            return aggregate
        return aggregate.evaluate(now_ms=int(now_ms), threshold_ms=self._threshold_ms)

    def record_cycle(self, cycle: PositionObservationCycle) -> None:
        """Refresh the in-memory indexes after a durable write."""
        account = cycle.account_id
        current_set = self._latest_set.get(account)
        if current_set is None or (
                cycle.observed_at_utc_ms >= current_set.observed_at_utc_ms):
            self._latest_set[account] = cycle.position_set
        for snapshot in cycle.positions:
            key = snapshot.ownership_key
            current = self._latest_position.get(key)
            if current is None or (
                    snapshot.observed_at_utc_ms >= current.observed_at_utc_ms):
                self._latest_position[key] = snapshot
        current_risk = self._open_risk.get(account)
        if current_risk is None or (
                cycle.open_risk.observed_at_utc_ms
                >= current_risk.observed_at_utc_ms):
            self._open_risk[account] = cycle.open_risk

    # ── observability ────────────────────────────────────────────────────
    def health(self, *, now_ms: int) -> dict[str, Any]:
        """Basic open-risk telemetry health. No dashboard, no network."""
        accounts: dict[str, Any] = {}
        for account_id, aggregate in sorted(self._open_risk.items()):
            evaluated = aggregate.evaluate(
                now_ms=int(now_ms), threshold_ms=self._threshold_ms)
            accounts[account_id] = {
                "status": evaluated.status.value,
                "observed_at_utc": evaluated.observed_at_utc,
                "age_ms": evaluated.age_ms(now_ms=int(now_ms)),
                "open_position_count": evaluated.open_position_count,
                "protected_position_count": evaluated.protected_position_count,
                "unprotected_position_count": evaluated.unprotected_position_count,
                "unknown_risk_position_count": evaluated.unknown_risk_position_count,
                "known_open_risk": evaluated.known_open_risk,
                "total_open_risk": evaluated.total_open_risk,
                "risk_complete": evaluated.risk_complete,
                "position_set_complete": evaluated.position_set_complete,
                "floating_pnl_total": evaluated.floating_pnl_total,
                "currency": evaluated.currency,
                "source_error": evaluated.source_error,
            }
        return {
            "accounts_observed": len(accounts),
            "freshness_threshold_ms": self._threshold_ms,
            "accounts": accounts,
            "source_error_count": sum(
                1 for row in accounts.values() if row["source_error"]),
            "accounts_with_authoritative_total": sum(
                1 for row in accounts.values()
                if row["total_open_risk"] is not None and row["risk_complete"]),
            "accounts_with_unprotected_positions": sum(
                1 for row in accounts.values()
                if (row["unprotected_position_count"] or 0) > 0),
            "accounts_with_unknown_risk": sum(
                1 for row in accounts.values()
                if (row["unknown_risk_position_count"] or 0) > 0),
        }


# ═══════════════════════════════════════════════════════════════════════════
# ACCOUNT-SAFE OBSERVATION CYCLE — links 2A account state to 2B positions
# ═══════════════════════════════════════════════════════════════════════════
#
# Position state can change without any bot-initiated order: manual trades,
# broker-side stop execution, SL modification, partial closes and external
# terminal actions. Execution callbacks alone therefore cannot prove current
# state, so periodic observation is required and is the reason this cycle is a
# first-class runtime hook rather than a side effect of order execution.


def observe_account_risk(
    account: AccountIdentity,
    source: PositionSource,
    *,
    account_snapshot: AccountSnapshot | None = None,
    account_producer: Any = None,
    clock: Callable[[], datetime] = utc_now,
    source_name: str = "MT5_POSITIONS_GET",
    spec_source: SymbolSpecSource | None = None,
    calculator: ProfitCalculator | None = None,
    canonical_resolver: Any = None,
    outbox: Any | None = None,
    base_dir: str | Path = DEFAULT_LOCAL_DIR,
    open_risk_dir: str | Path = DEFAULT_OPEN_RISK_DIR,
    persist: bool = True,
) -> tuple[AccountSnapshot | None, PositionObservationCycle]:
    """Run ONE account-risk telemetry cycle for one exact account.

    The Block 2A account snapshot is captured (or accepted) FIRST so the
    open-risk aggregate can be linked to it by EXACT ``account_snapshot_id``
    rather than by timestamp proximity, and so ``currency`` is the account's own
    currency. One clock reading produces one shared ``observation_id``.
    """
    _validate_identity(account)

    captured: AccountSnapshot | None = account_snapshot
    if captured is None and account_producer is not None:
        captured = account_producer.observe(account)
    if captured is not None:
        if captured.account_id != account.account_id:
            raise AccountIdentityError("ACCOUNT_SNAPSHOT_IDENTITY_MISMATCH")
        currency = captured.currency
        account_snapshot_id = captured.snapshot_id
    else:
        currency = None
        account_snapshot_id = None

    producer = PositionSnapshotProducer(
        source, base_dir=base_dir, open_risk_dir=open_risk_dir, clock=clock,
        source_name=source_name, spec_source=spec_source, calculator=calculator,
        canonical_resolver=canonical_resolver, outbox=outbox, persist=persist,
    )
    cycle = producer.observe(
        account, currency=currency, account_snapshot_id=account_snapshot_id)
    return captured, cycle


# ═══════════════════════════════════════════════════════════════════════════
# PERIODIC CADENCE
# ═══════════════════════════════════════════════════════════════════════════
#
# Positions must be observed at meaningful runtime points: startup, before a
# risk decision/order send, after a fill, after a close, and on a configurable
# heartbeat for everything that changes without our involvement.
#
# The heartbeat owns AT MOST ONE daemon thread, is started explicitly, is
# idempotent, and schedules from the PREVIOUS DUE TIME so a slow cycle never
# accumulates drift. It waits on an Event, so shutdown is immediate. No test
# ever sleeps: every clock is injected.


class PositionSnapshotHeartbeat:
    """Single-threaded, monotonic, configurable periodic position observation."""

    def __init__(
        self,
        producer: PositionSnapshotProducer,
        store: PositionSnapshotStore,
        *,
        sources: Mapping[str, PositionSource],
        identities: "Sequence[AccountIdentity]",
        interval_ms: int | None = None,
        currencies: Mapping[str, str | None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if interval_ms is None:
            interval_ms = snapshot_interval_ms()
        interval_ms = int(interval_ms)
        if interval_ms <= 0:
            raise ValueError("SNAPSHOT_INTERVAL_MUST_BE_POSITIVE")
        self._producer = producer
        self._store = store
        self._sources = dict(sources)
        self._identities = tuple(identities)
        self._currencies = dict(currencies or {})
        self._interval_ms = interval_ms
        self._monotonic = monotonic or time.monotonic
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_due = self._monotonic() + (self._interval_ms / 1000.0)

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def next_due_from(self, previous_due: float) -> float:
        """Next due time derived from the previous due time (no drift)."""
        return float(previous_due) + (self._interval_ms / 1000.0)

    def next_due_monotonic(self) -> float:
        return self._next_due

    def tick(self) -> tuple[PositionObservationCycle, ...]:
        """One bounded observation cycle across every configured account."""
        results: list[PositionObservationCycle] = []
        for account in self._identities:
            source = self._sources.get(account.account_id)
            if source is None:
                raise PositionSnapshotError(
                    f"NO_POSITION_SOURCE:{account.account_id}")
            cycle = self._producer.observe(
                account, currency=self._currencies.get(account.account_id),
                source=source)
            self._store.record_cycle(cycle)
            results.append(cycle)
        return tuple(results)

    def start(self) -> None:
        """Start the heartbeat. Idempotent: never spawns a second thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="position_snapshot_heartbeat", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._next_due = self.next_due_from(self._next_due)
            remaining = self._next_due - self._monotonic()
            if remaining > 0 and self._stop.wait(remaining):
                return
            if self._stop.is_set():
                return
            try:
                self.tick()
            except Exception:  # telemetry must never kill the runtime
                logger.exception("[POSITION_SNAPSHOT_HEARTBEAT] cycle failed")


__all__ = [
    "DATASET",
    "DEFAULT_FRESHNESS_SECONDS",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_LOCAL_DIR",
    "DEFAULT_OPEN_RISK_DIR",
    "OPEN_RISK_DATASET",
    "OPEN_RISK_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "AccountOpenRiskSnapshot",
    "Mt5OrderCalcProfit",
    "Mt5PositionSource",
    "Mt5SymbolSpecSource",
    "PositionIdentity",
    "PositionObservationCycle",
    "PositionSetObservation",
    "PositionSetUnavailable",
    "PositionSide",
    "PositionSnapshot",
    "PositionSnapshotError",
    "PositionSnapshotHeartbeat",
    "PositionSnapshotNotFound",
    "PositionSnapshotProducer",
    "PositionSnapshotStore",
    "PositionSource",
    "PositionStatus",
    "ProfitCalculator",
    "SymbolSpecSource",
    "aggregate_open_risk",
    "capture_position_snapshot",
    "derive_open_risk_id",
    "derive_observation_id",
    "derive_position_snapshot_id",
    "freshness_threshold_ms",
    "observe_account_risk",
    "observe_positions",
    "persist_open_risk",
    "persist_position_set",
    "persist_position_snapshot",
    "snapshot_interval_ms",
    "to_epoch_ms",
    "to_iso_utc",
    "utc_now",
]
