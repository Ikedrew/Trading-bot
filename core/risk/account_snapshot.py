"""Account-safe prop-risk telemetry — canonical runtime account snapshot.

This module owns the ONE canonical, account-scoped representation of live MT5
account state used by prop-risk telemetry. It answers, for exactly one trading
account: balance, equity, floating P&L, margin, free margin, margin level,
account currency, leverage, broker/server/login ownership, observation time and
data quality.

DESIGN RULES (Block 2A)
-----------------------
1. EXACT ACCOUNT IDENTITY. Every snapshot is bound to one
   :class:`AccountIdentity` = (account_id, broker namespace, server, login).
   A symbol, a broker name alone, a bare login or a terminal path can NEVER
   identify an account.
2. FAIL CLOSED. An unavailable broker value is ``None`` plus a named entry in
   ``unavailable_fields``. A malformed / non-finite value is ``None`` plus a
   named entry in ``invalid_fields`` and forces ``INVALID``. A legitimate
   numeric zero is preserved verbatim and is never confused with "unavailable".
3. NO INVENTED VALUES. Nothing is recomputed unless the MT5 semantics are exact
   (see ``floating_pnl`` below) and no contradiction is ever "corrected".
4. DETERMINISTIC IDENTITY. ``snapshot_id`` is a pure function of
   (account identity, observation instant, source). No UUID, no wall clock, no
   monetary float takes part in it, so exact replay is idempotent.
5. DURABLE. Local append-only JSONL + fsync, then the certified Block 1
   canonical handoff (outbox -> worker -> ACK). There is no best-effort S3
   mirror here.
6. PURELY OBSERVATIONAL. Nothing in this module changes a trading decision.

This block defines the contract only. Daily loss, max drawdown, peak equity,
open-risk aggregation, correlation exposure and prop-rule enforcement are Block
2B+ concerns and are deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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

logger = logging.getLogger(__name__)

# ─── PRODUCTION V1 DATASET CONTRACT ───────────────────────────────────────────
DATASET = "account_snapshots"
SCHEMA_VERSION = "account_snapshots_v1"
DEFAULT_LOCAL_DIR = "logs/account_snapshots"

# ─── FRESHNESS (telemetry quality, NOT a prop rule) ───────────────────────────
DEFAULT_FRESHNESS_ENV = "ACCOUNT_SNAPSHOT_FRESHNESS_SECONDS"
DEFAULT_FRESHNESS_SECONDS = 60.0

# ─── BROKER-SOURCE CONSISTENCY TOLERANCE ─────────────────────────────────────
# Guards only against float32/float64 accumulation in the broker's own
# arithmetic. Never used to correct a reported value.
CONSISTENCY_TOLERANCE_ABS = 0.01
CONSISTENCY_TOLERANCE_REL = 1e-6

REQUIRED_COMPLETE_FIELDS = ("balance", "equity", "currency")


class AccountSnapshotStatus(str, Enum):
    """Explicit, non-ambiguous data-quality status of one account snapshot.

    ``STALE`` is never persisted: it is a read-time evaluation of age against a
    configured freshness threshold. Use :meth:`AccountSnapshot.evaluate`.
    """

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    STALE = "STALE"


class AccountSnapshotError(RuntimeError):
    """Base error for the account snapshot contract."""


class AccountIdentityError(AccountSnapshotError):
    """The supplied identity cannot name exactly one trading account."""


class AccountSnapshotNotFound(AccountSnapshotError):
    """No snapshot exists for the requested account_id.

    Raised instead of ever returning another account's state or a zero value.
    """


def utc_now() -> datetime:
    """Default injectable clock. Tests must inject their own; never sleep."""
    return datetime.now(timezone.utc)


def to_epoch_ms(moment: datetime) -> int:
    if moment.tzinfo is None:
        raise ValueError("OBSERVATION_TIMEZONE_REQUIRED")
    return int(round(moment.astimezone(timezone.utc).timestamp() * 1000))


def to_iso_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("OBSERVATION_TIMEZONE_REQUIRED")
    return moment.astimezone(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str, allow_nan=False)


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def freshness_threshold_ms(env: Mapping[str, str] | None = None) -> int:
    """Configurable freshness threshold. Never a hard-coded prop rule."""
    source = os.environ if env is None else env
    raw = str(source.get(DEFAULT_FRESHNESS_ENV, "") or "").strip()
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = DEFAULT_FRESHNESS_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        seconds = DEFAULT_FRESHNESS_SECONDS
    return int(round(seconds * 1000))


# ─── CADENCE (configurable; never an uncontrolled thread) ────────────────────
DEFAULT_INTERVAL_ENV = "ACCOUNT_SNAPSHOT_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 60.0


def snapshot_interval_ms(env: Mapping[str, str] | None = None) -> int:
    """Configurable snapshot cadence in ms. Only a fallback default is fixed."""
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
# EXACT ACCOUNT IDENTITY
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AccountIdentity:
    """Deterministic, restart-stable identity of exactly one trading account.

    An account is identified by the broker/terminal namespace plus server plus
    login. It is NEVER identified by symbol, broker name alone, a login without
    its server namespace, or a terminal path alone.

    This mirrors the existing canonical ``AccountConfig.identity`` tuple
    ``(account_id, broker, server, login)`` owned by ``core.accounts.config``;
    this dataclass is the account-safe transport form used by snapshots.
    """

    account_id: str
    broker: str
    server: str
    login: int

    def __post_init__(self) -> None:
        for name in ("account_id", "broker", "server"):
            if not str(getattr(self, name) or "").strip():
                raise AccountIdentityError(f"ACCOUNT_IDENTITY_FIELD_REQUIRED:{name}")
        if isinstance(self.login, bool) or not isinstance(self.login, int):
            raise AccountIdentityError("ACCOUNT_IDENTITY_LOGIN_INVALID")
        if self.login <= 0:
            raise AccountIdentityError("ACCOUNT_IDENTITY_LOGIN_INVALID")

    @property
    def identity(self) -> tuple[str, str, str, int]:
        """The canonical identity tuple shared with ``AccountConfig.identity``."""
        return (self.account_id, self.broker, self.server, self.login)

    @classmethod
    def from_account_config(cls, config: Any) -> "AccountIdentity":
        """Build from any object exposing the canonical identity fields."""
        return cls(
            account_id=str(getattr(config, "account_id", "") or ""),
            broker=str(getattr(config, "broker", "") or ""),
            server=str(getattr(config, "server", "") or ""),
            login=getattr(config, "login", None),
        )

    def matches_account_config(self, config: Any) -> bool:
        """True only when this identity names exactly the configured account."""
        return self.identity == tuple(getattr(config, "identity", ()) or ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "broker": self.broker,
            "server": self.server,
            "login": self.login,
        }


def derive_snapshot_id(
    identity: AccountIdentity, observed_at_utc_ms: int, source: str,
) -> str:
    """Deterministic snapshot identity — same account + instant + source = same id.

    Built from the exact account identity, the observation instant and the
    observation source. Monetary floats and random UUIDs are deliberately
    excluded so exact replay is idempotent and two accounts can never collide.
    """
    if isinstance(observed_at_utc_ms, bool) or not isinstance(observed_at_utc_ms, int):
        raise ValueError("OBSERVATION_INSTANT_MUST_BE_INT_MS")
    if observed_at_utc_ms <= 0:
        raise ValueError("OBSERVATION_INSTANT_MUST_BE_POSITIVE")
    source_label = str(source or "").strip()
    if not source_label:
        raise ValueError("OBSERVATION_SOURCE_REQUIRED")
    digest = _sha256_hex({
        "account_id": identity.account_id,
        "broker": identity.broker,
        "server": identity.server,
        "login": identity.login,
        "observed_at_utc_ms": observed_at_utc_ms,
        "source": source_label,
    })
    return f"asnap_{digest[:32]}"


# ═══════════════════════════════════════════════════════════════════════════
# VALUE VALIDATION — fail closed, never substitute a false zero
# ═══════════════════════════════════════════════════════════════════════════


def _coerce_float(
    value: Any, field_name: str,
    unavailable: list[str], invalid: list[str],
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
    value: Any, field_name: str,
    unavailable: list[str], invalid: list[str],
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


def _coerce_bool(
    value: Any, field_name: str,
    unavailable: list[str], invalid: list[str],
) -> bool | None:
    if value is None:
        unavailable.append(field_name)
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    invalid.append(field_name)
    return None


def _coerce_currency(
    value: Any, unavailable: list[str], invalid: list[str],
) -> str | None:
    """Account base/deposit currency exactly as the broker reports it.

    Block 2A never converts currency. An empty/absent currency is a hard quality
    defect because monetary prop rules cannot be evaluated without it.
    """
    if value is None:
        unavailable.append("currency")
        return None
    if not isinstance(value, str):
        invalid.append("currency")
        return None
    text = value.strip()
    if not text:
        unavailable.append("currency")
        return None
    return text


# ═══════════════════════════════════════════════════════════════════════════
# TYPED ACCOUNT SNAPSHOT RECORD
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AccountSnapshot:
    """One canonical, account-safe observation of exactly one trading account.

    VALUE SEMANTICS (source and transformation are exact for every field)
    ---------------------------------------------------------------------
    balance        MT5 ``account_info().balance`` — closed balance. It already
                   contains realised P&L, deposits, withdrawals, commissions and
                   swaps. Never adjusted here.
    equity         MT5 ``account_info().equity`` — current account equity.
    floating_pnl   MT5 ``account_info().profit`` — the broker's authoritative
                   floating (unrealised) P&L of open positions, denominated in
                   account currency. It is NOT realised P&L and is never mixed
                   with deposits/withdrawals. When ``profit`` is absent but
                   equity, balance AND credit are all present, the exact MT5
                   identity ``equity == balance + credit + profit`` is used to
                   derive it and ``floating_pnl_source`` records that. Without
                   ``credit`` the derivation is not exact, so the field stays
                   unavailable rather than guessed.
    margin         MT5 ``account_info().margin`` — currently used margin. A
                   legitimate ``0.0`` means no open positions.
    free_margin    MT5 ``account_info().margin_free`` — available margin.
    margin_level   MT5 ``account_info().margin_level`` — broker-reported margin
                   level (%). Never recomputed; MT5 reports ``0.0`` when margin
                   is zero, which is preserved verbatim.
    leverage       MT5 ``account_info().leverage`` — broker-reported leverage.
    currency       MT5 ``account_info().currency`` — account base/deposit
                   currency. Never assumed, never converted.
    """

    # ── identity ──────────────────────────────────────────────────────────
    account_id: str
    broker: str
    server: str
    login: int

    # ── observation ───────────────────────────────────────────────────────
    snapshot_id: str
    observed_at_utc: str
    observed_at_utc_ms: int
    source: str

    # ── account state (None == unavailable/invalid, never a false zero) ────
    balance: float | None = None
    equity: float | None = None
    floating_pnl: float | None = None
    margin: float | None = None
    free_margin: float | None = None
    margin_level: float | None = None
    leverage: int | None = None
    currency: str | None = None
    credit: float | None = None
    floating_pnl_source: str = "MT5_ACCOUNT_INFO_PROFIT"

    # ── optional MT5 state (recorded only when the broker reports it) ─────
    trade_allowed: bool | None = None
    trade_expert: bool | None = None
    trade_mode: int | None = None
    margin_mode: int | None = None
    stopout_mode: int | None = None
    stopout_call: float | None = None
    stopout_so: float | None = None

    # ── quality ───────────────────────────────────────────────────────────
    status: AccountSnapshotStatus = AccountSnapshotStatus.UNAVAILABLE
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
    def is_complete(self) -> bool:
        return self.status is AccountSnapshotStatus.COMPLETE

    def age_ms(self, *, now_ms: int) -> int | None:
        """Age since the BROKER observation, never since persistence."""
        if self.observed_at_utc_ms <= 0:
            return None
        return max(0, int(now_ms) - int(self.observed_at_utc_ms))

    def is_stale(self, *, now_ms: int, threshold_ms: int) -> bool:
        """Freshness is telemetry quality only — never a prop compliance rule."""
        if threshold_ms <= 0:
            raise ValueError("FRESHNESS_THRESHOLD_MUST_BE_POSITIVE")
        age = self.age_ms(now_ms=now_ms)
        return age is None or age > threshold_ms

    def evaluate(self, *, now_ms: int, threshold_ms: int) -> "AccountSnapshot":
        """Return a copy whose status reflects read-time freshness.

        A fresh COMPLETE snapshot stays COMPLETE. An aged one becomes STALE so
        no consumer can mistake durable history for current state.
        """
        if self.is_stale(now_ms=now_ms, threshold_ms=threshold_ms):
            return replace(self, status=AccountSnapshotStatus.STALE)
        return self

    def to_dict(self) -> dict[str, Any]:
        """Canonical persistable payload.

        ``age_ms``/``stale`` are deliberately NOT persisted: they are read-time
        evaluations against a live clock, not observation facts.
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "account_id": self.account_id,
            "broker": self.broker,
            "server": self.server,
            "login": self.login,
            "snapshot_id": self.snapshot_id,
            "observed_at_utc": self.observed_at_utc,
            "observed_at_utc_ms": self.observed_at_utc_ms,
            "source": self.source,
            "balance": self.balance,
            "equity": self.equity,
            "floating_pnl": self.floating_pnl,
            "floating_pnl_source": self.floating_pnl_source,
            "margin": self.margin,
            "free_margin": self.free_margin,
            "margin_level": self.margin_level,
            "leverage": self.leverage,
            "currency": self.currency,
            "credit": self.credit,
            "trade_allowed": self.trade_allowed,
            "trade_expert": self.trade_expert,
            "trade_mode": self.trade_mode,
            "margin_mode": self.margin_mode,
            "stopout_mode": self.stopout_mode,
            "stopout_call": self.stopout_call,
            "stopout_so": self.stopout_so,
            "status": self.status.value,
            "unavailable_fields": list(self.unavailable_fields),
            "invalid_fields": list(self.invalid_fields),
            "source_error": self.source_error,
            "consistency_ok": self.consistency_ok,
            "consistency_detail": self.consistency_detail,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AccountSnapshot":
        """Rehydrate a persisted snapshot with no value reinterpretation."""
        return cls(
            account_id=str(payload["account_id"]),
            broker=str(payload["broker"]),
            server=str(payload["server"]),
            login=int(payload["login"]),
            snapshot_id=str(payload["snapshot_id"]),
            observed_at_utc=str(payload["observed_at_utc"]),
            observed_at_utc_ms=int(payload["observed_at_utc_ms"]),
            source=str(payload["source"]),
            balance=payload.get("balance"),
            equity=payload.get("equity"),
            floating_pnl=payload.get("floating_pnl"),
            floating_pnl_source=str(
                payload.get("floating_pnl_source") or "MT5_ACCOUNT_INFO_PROFIT"),
            margin=payload.get("margin"),
            free_margin=payload.get("free_margin"),
            margin_level=payload.get("margin_level"),
            leverage=payload.get("leverage"),
            currency=payload.get("currency"),
            credit=payload.get("credit"),
            trade_allowed=payload.get("trade_allowed"),
            trade_expert=payload.get("trade_expert"),
            trade_mode=payload.get("trade_mode"),
            margin_mode=payload.get("margin_mode"),
            stopout_mode=payload.get("stopout_mode"),
            stopout_call=payload.get("stopout_call"),
            stopout_so=payload.get("stopout_so"),
            status=AccountSnapshotStatus(payload["status"]),
            unavailable_fields=tuple(payload.get("unavailable_fields") or ()),
            invalid_fields=tuple(payload.get("invalid_fields") or ()),
            source_error=payload.get("source_error"),
            consistency_ok=payload.get("consistency_ok"),
            consistency_detail=payload.get("consistency_detail"),
        )

    def missing_required_fields(self) -> tuple[str, ...]:
        """Required-for-COMPLETE fields that are not usable in this snapshot."""
        return tuple(
            name for name in REQUIRED_COMPLETE_FIELDS
            if getattr(self, name, None) is None
        )


# ═══════════════════════════════════════════════════════════════════════════
# STATUS CONTRACT
# ═══════════════════════════════════════════════════════════════════════════
#
# INVALID      any field present-but-malformed (NaN/inf/non-numeric/bool), or a
#              broker-reported contradiction between equity/balance/credit/
#              floating P&L. Never silently corrected.
# UNAVAILABLE  the source could not be read at all (no account_info, connection
#              failure, identity mismatch). Explicit source_error is recorded.
# PARTIAL      the source responded but at least one required field
#              (balance / equity / currency) is missing.
# COMPLETE     account identity, balance, equity and currency are all present
#              and valid, and the observation is internally consistent.
#              margin / free_margin / margin_level / leverage are recorded when
#              available and their absence is reported in unavailable_fields,
#              but they are not required for COMPLETE — they qualify
#              independently for the later margin-based prop rules.
# STALE        read-time only: the observation age exceeds the configured
#              freshness threshold. Never persisted as a status.


# ═══════════════════════════════════════════════════════════════════════════
# MT5 ADAPTER BOUNDARY
# ═══════════════════════════════════════════════════════════════════════════


class AccountInfoSource(Protocol):
    """Injectable account-state source.

    Unit tests provide a fake; production provides :class:`Mt5AccountInfoSource`.
    No caller of this module ever touches ``mt5.account_info()`` directly.
    """

    def read_account_info(self) -> Mapping[str, Any] | None:
        """Return the broker's account-info mapping, or ``None`` if unreadable."""
        ...


class AccountSourceUnavailable(AccountSnapshotError):
    """The underlying account source could not be read."""


class AccountSourceIdentityMismatch(AccountSourceUnavailable):
    """The source reported a different account than the one requested."""


class Mt5AccountInfoSource:
    """Live MT5 account-info adapter.

    Identity is verified BEFORE and AFTER the read so a mid-read account switch
    can never be attributed to the requested account.
    """

    source_name = "MT5_ACCOUNT_INFO"

    def __init__(self, *, timeout: float | None = None) -> None:
        self._timeout = timeout

    def read_account_info(self) -> Mapping[str, Any] | None:
        try:
            import MetaTrader5 as mt5
            from core.mt5_timeout import mt5_call
        except ImportError as exc:  # MT5 absent (CI / test environment)
            raise AccountSourceUnavailable("MT5_MODULE_UNAVAILABLE") from exc
        call = mt5_call
        if self._timeout is not None:
            call = lambda func: mt5_call(func, timeout=self._timeout)  # noqa: E731
        try:
            return call(mt5.account_info)
        except Exception as exc:  # broker/terminal IPC failure
            raise AccountSourceUnavailable(
                f"MT5_ACCOUNT_INFO_FAILED:{type(exc).__name__}") from exc


def _read_field(info: Mapping[str, Any], *names: str) -> Any:
    """Return the first present attribute/key among ``names`` (MT5 naming)."""
    for name in names:
        if isinstance(info, Mapping):
            if name in info:
                return info[name]
        elif hasattr(info, name):
            return getattr(info, name)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# PRODUCER
# ═══════════════════════════════════════════════════════════════════════════


def _consistency_check(
    balance: float | None, equity: float | None,
    credit: float | None, floating: float | None,
) -> tuple[bool | None, str | None]:
    """Bounded plausibility check of the exact MT5 equity identity.

    MT5 guarantees ``equity == balance + credit + profit``. Tiny float
    differences are tolerated; an obvious contradiction marks the snapshot
    quality degraded/invalid. No value is ever corrected.
    """
    if balance is None or equity is None or floating is None:
        return None, None
    if credit is None:
        return None, "CREDIT_UNAVAILABLE_IDENTITY_UNVERIFIABLE"
    expected = balance + credit + floating
    difference = abs(equity - expected)
    tolerance = max(
        CONSISTENCY_TOLERANCE_ABS, abs(expected) * CONSISTENCY_TOLERANCE_REL,
    )
    if difference <= tolerance:
        return True, None
    return False, (
        f"EQUITY_IDENTITY_CONTRADICTION:expected={expected!r}:"
        f"reported={equity!r}:delta={difference!r}"
    )


def _unavailable_snapshot(
    identity: AccountIdentity, observed_ms: int, observed_iso: str,
    source: str, error: str,
) -> AccountSnapshot:
    """Explicit UNAVAILABLE record — never a zero-valued fake account."""
    return AccountSnapshot(
        account_id=identity.account_id, broker=identity.broker,
        server=identity.server, login=identity.login,
        snapshot_id=derive_snapshot_id(identity, observed_ms, source),
        observed_at_utc=observed_iso, observed_at_utc_ms=observed_ms,
        source=source,
        status=AccountSnapshotStatus.UNAVAILABLE,
        unavailable_fields=(
            "balance", "equity", "floating_pnl", "margin", "free_margin",
            "margin_level", "leverage", "currency", "credit",
        ),
        source_error=error,
    )


def capture_account_snapshot(
    identity: AccountIdentity,
    source: AccountInfoSource,
    *,
    clock: Callable[[], datetime] = utc_now,
    source_name: str = "MT5_ACCOUNT_INFO",
) -> AccountSnapshot:
    """Read exact MT5 account state for ONE account and build a typed snapshot.

    Fails closed and never raises for a broker problem: an unreadable source
    yields an explicit UNAVAILABLE snapshot. An unusable identity is a
    programming error and raises :class:`AccountIdentityError`.
    """
    if not isinstance(identity, AccountIdentity):
        raise AccountIdentityError("EXACT_ACCOUNT_IDENTITY_REQUIRED")
    if source is None:
        raise ValueError("ACCOUNT_INFO_SOURCE_REQUIRED")

    observed = clock()
    observed_ms = to_epoch_ms(observed)
    observed_iso = to_iso_utc(observed)

    try:
        info = source.read_account_info()
    except AccountSourceUnavailable as exc:
        return _unavailable_snapshot(
            identity, observed_ms, observed_iso, source_name, str(exc) or
            "ACCOUNT_SOURCE_UNAVAILABLE",
        )
    except Exception as exc:
        return _unavailable_snapshot(
            identity, observed_ms, observed_iso, source_name,
            f"ACCOUNT_SOURCE_ERROR:{type(exc).__name__}",
        )

    if info is None:
        return _unavailable_snapshot(
            identity, observed_ms, observed_iso, source_name,
            "ACCOUNT_INFO_UNAVAILABLE",
        )

    # ── EXACT identity binding: the read must belong to the requested account.
    reported_login = _read_field(info, "login")
    reported_server = str(_read_field(info, "server") or "")
    if reported_login is None or not reported_server:
        return _unavailable_snapshot(
            identity, observed_ms, observed_iso, source_name,
            "ACCOUNT_INFO_IDENTITY_UNVERIFIABLE",
        )
    try:
        reported_login = int(reported_login)
    except (TypeError, ValueError):
        return _unavailable_snapshot(
            identity, observed_ms, observed_iso, source_name,
            "ACCOUNT_INFO_IDENTITY_MALFORMED",
        )
    if (reported_login != identity.login
            or reported_server.casefold() != identity.server.casefold()):
        return _unavailable_snapshot(
            identity, observed_ms, observed_iso, source_name,
            "ACCOUNT_INFO_IDENTITY_MISMATCH",
        )

    unavailable: list[str] = []
    invalid: list[str] = []

    balance = _coerce_float(_read_field(info, "balance"), "balance", unavailable, invalid)
    equity = _coerce_float(_read_field(info, "equity"), "equity", unavailable, invalid)
    margin = _coerce_float(_read_field(info, "margin"), "margin", unavailable, invalid)
    free_margin = _coerce_float(
        _read_field(info, "margin_free", "free_margin"), "free_margin",
        unavailable, invalid,
    )
    margin_level = _coerce_float(
        _read_field(info, "margin_level"), "margin_level", unavailable, invalid,
    )
    leverage = _coerce_int(
        _read_field(info, "leverage"), "leverage", unavailable, invalid,
    )
    currency = _coerce_currency(_read_field(info, "currency"), unavailable, invalid)
    credit = _coerce_float(_read_field(info, "credit"), "credit", unavailable, invalid)

    # Floating P&L: the broker's authoritative `profit` field wins. Only the
    # exact MT5 equity identity is used as a fallback, and only when credit is
    # also known — otherwise the value is unavailable, never guessed.
    raw_profit = _read_field(info, "profit")
    floating = _coerce_float(raw_profit, "floating_pnl", unavailable, invalid)
    floating_source = "MT5_ACCOUNT_INFO_PROFIT"
    if floating is None and raw_profit is None:
        floating_source = "UNAVAILABLE"
    if floating is None and raw_profit is None and "floating_pnl" in unavailable:
        if None not in (balance, equity, credit):
            floating = equity - balance - credit
            unavailable.remove("floating_pnl")
            floating_source = "DERIVED_EQUITY_MINUS_BALANCE_MINUS_CREDIT"

    trade_allowed = _coerce_bool(
        _read_field(info, "trade_allowed"), "trade_allowed", unavailable, invalid)
    trade_expert = _coerce_bool(
        _read_field(info, "trade_expert"), "trade_expert", unavailable, invalid)
    trade_mode = _coerce_int(
        _read_field(info, "trade_mode"), "trade_mode", unavailable, invalid)
    margin_mode = _coerce_int(
        _read_field(info, "margin_mode"), "margin_mode", unavailable, invalid)
    stopout_mode = _coerce_int(
        _read_field(info, "stopout_mode", "stopout_level"),
        "stopout_mode", unavailable, invalid)
    stopout_call = _coerce_float(
        _read_field(info, "stopout_call", "margin_so_call"),
        "stopout_call", unavailable, invalid)
    stopout_so = _coerce_float(
        _read_field(info, "stopout_so", "margin_so_so"),
        "stopout_so", unavailable, invalid)

    consistency_ok, consistency_detail = _consistency_check(
        balance, equity, credit, floating,
    )

    # ── STATUS ASSIGNMENT (explicit, never a bare boolean) ─────────────────
    if invalid:
        status = AccountSnapshotStatus.INVALID
    elif consistency_ok is False:
        status = AccountSnapshotStatus.INVALID
    elif None in (balance, equity, currency):
        status = AccountSnapshotStatus.PARTIAL
    else:
        status = AccountSnapshotStatus.COMPLETE

    if status is AccountSnapshotStatus.INVALID and consistency_ok is False:
        invalid = sorted({*invalid, "equity_identity_consistency"})

    return AccountSnapshot(
        account_id=identity.account_id,
        broker=identity.broker,
        server=identity.server,
        login=identity.login,
        snapshot_id=derive_snapshot_id(identity, observed_ms, source_name),
        observed_at_utc=observed_iso,
        observed_at_utc_ms=observed_ms,
        source=source_name,
        balance=balance,
        equity=equity,
        floating_pnl=floating,
        floating_pnl_source=floating_source,
        margin=margin,
        free_margin=free_margin,
        margin_level=margin_level,
        leverage=leverage,
        currency=currency,
        credit=credit,
        trade_allowed=trade_allowed,
        trade_expert=trade_expert,
        trade_mode=trade_mode,
        margin_mode=margin_mode,
        stopout_mode=stopout_mode,
        stopout_call=stopout_call,
        stopout_so=stopout_so,
        status=status,
        unavailable_fields=tuple(sorted(set(unavailable))),
        invalid_fields=tuple(sorted(set(invalid))),
        source_error=None,
        consistency_ok=consistency_ok,
        consistency_detail=consistency_detail,
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


def _local_path(base_dir: str | Path, snapshot: AccountSnapshot) -> Path:
    """Account snapshots are account-scoped and date-partitioned (never symbol)."""
    date = snapshot.observed_at_utc[:10]
    return Path(base_dir) / date / f"{snapshot.account_id}.jsonl"


def persist_account_snapshot(
    snapshot: AccountSnapshot,
    *,
    base_dir: str | Path = DEFAULT_LOCAL_DIR,
    outbox: Any | None = None,
) -> bool:
    """Durably persist one account snapshot through the canonical delivery path.

    Returns True only when the local fsync succeeded. The canonical handoff is
    attempted after the local write; a handoff failure propagates to the caller
    so an operator can observe it, exactly as other governed writers behave.
    """
    if not isinstance(snapshot, AccountSnapshot):
        raise AccountSnapshotError("ACCOUNT_SNAPSHOT_REQUIRED")
    payload = snapshot.to_dict()
    partition_date = snapshot.observed_at_utc[:10]
    line = _canonical_json(payload)
    path = _local_path(base_dir, snapshot)
    path.parent.mkdir(parents=True, exist_ok=True)

    from core.lifecycle_evidence_obligations import (
        create_dataset_obligation,
        obligation_ledger,
        record_producer_outcome,
    )
    ledger = obligation_ledger()
    obligation = create_dataset_obligation(
        ledger,
        event_id=f"account-state:{snapshot.account_id}:{snapshot.snapshot_id}",
        lifecycle_stage="ACCOUNT_STATE_OBSERVATION",
        dataset=DATASET,
        identity={
            "account_id": snapshot.account_id,
            "snapshot_id": snapshot.snapshot_id,
        },
        timestamp=snapshot.observed_at_utc,
        producer="core.risk.account_snapshot.persist_account_snapshot",
        trigger="ACCOUNT_STATE_LOCAL_FSYNC",
    )

    from core.canonical_delivery import (
        enqueue_canonical_delivery,
        try_prepare_local_jsonl_handoffs,
    )
    # Certified pre-write handoff MUST be journalled before the local append.
    try_prepare_local_jsonl_handoffs(
        dataset=DATASET, content=line + "\n", symbol="",
        partition_date=partition_date, local_path=path, outbox=outbox,
        lifecycle_obligation_id=obligation.obligation_id,
    )
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)

    record_producer_outcome(
        ledger, obligation, succeeded=True,
        observed_record_id=snapshot.snapshot_id,
        provenance={"persistence_scope": "LOCAL_FSYNC"},
    )

    enqueue_canonical_delivery(
        dataset=DATASET, payload=payload, symbol="",
        partition_date=partition_date, outbox=outbox,
        lifecycle_obligation_id=obligation.obligation_id,
    )
    return True


class AccountSnapshotProducer:
    """Focused producer/service for account snapshots.

    Owns the read -> validate -> timestamp -> persist path so that downstream
    Block 2 consumers never call raw ``mt5.account_info()`` themselves.
    """

    def __init__(
        self,
        source: AccountInfoSource,
        *,
        base_dir: str | Path = DEFAULT_LOCAL_DIR,
        clock: Callable[[], datetime] = utc_now,
        source_name: str = "MT5_ACCOUNT_INFO",
        outbox: Any | None = None,
        persist: bool = True,
    ) -> None:
        self._source = source
        self._base_dir = base_dir
        self._clock = clock
        self._source_name = source_name
        self._outbox = outbox
        self._persist = persist

    def observe(self, identity: AccountIdentity) -> AccountSnapshot:
        """Capture one exact account snapshot and persist it durably."""
        snapshot = capture_account_snapshot(
            identity, self._source, clock=self._clock,
            source_name=self._source_name,
        )
        if self._persist:
            persist_account_snapshot(
                snapshot, base_dir=self._base_dir, outbox=self._outbox)
        return snapshot

    def observe_all(
        self, identities: "list[AccountIdentity] | tuple[AccountIdentity, ...]",
    ) -> tuple[AccountSnapshot, ...]:
        """Capture many accounts. One account's failure never affects another."""
        return tuple(self.observe(identity) for identity in identities)


# ═══════════════════════════════════════════════════════════════════════════
# SNAPSHOT STORE / LATEST INDEX  (convenience only)
# ═══════════════════════════════════════════════════════════════════════════
#
# The canonical append-only JSONL evidence remains authoritative. This index is
# a rebuildable convenience: it never mutates history, and it is reconstructed
# from the local snapshots if it is missing or corrupt.


class AccountSnapshotStore:
    """Exact-account latest-snapshot lookup for Block 2 consumers.

    Guarantees:
      * exact ``account_id`` lookup only — never falls back to another account;
      * latest valid observation by broker observation instant;
      * explicit status / staleness / timestamp exposure;
      * raises :class:`AccountSnapshotNotFound` rather than returning zeros.
    """

    def __init__(
        self,
        *,
        base_dir: str | Path = DEFAULT_LOCAL_DIR,
        threshold_ms: int | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._threshold_ms = (
            freshness_threshold_ms() if threshold_ms is None else int(threshold_ms)
        )
        if self._threshold_ms <= 0:
            raise ValueError("FRESHNESS_THRESHOLD_MUST_BE_POSITIVE")
        self._index: dict[str, AccountSnapshot] = {}
        self._rebuild()

    def _iter_persisted(self):
        if not self._base_dir.is_dir():
            return
        for path in sorted(self._base_dir.rglob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning(
                                "[ACCOUNT_SNAPSHOT_INDEX] corrupt line in %s", path)
                            continue
                        if not isinstance(record, dict):
                            continue
                        try:
                            yield AccountSnapshot.from_dict(record)
                        except (KeyError, TypeError, ValueError):
                            logger.warning(
                                "[ACCOUNT_SNAPSHOT_INDEX] unusable record in %s", path)
            except OSError:
                logger.warning("[ACCOUNT_SNAPSHOT_INDEX] unreadable %s", path)

    def _rebuild(self) -> None:
        """Rebuild the latest index from the canonical local snapshots."""
        rebuilt: dict[str, AccountSnapshot] = {}
        for snapshot in self._iter_persisted():
            current = rebuilt.get(snapshot.account_id)
            if current is None or snapshot.observed_at_utc_ms > current.observed_at_utc_ms:
                rebuilt[snapshot.account_id] = snapshot
        self._index = rebuilt

    def rebuild(self) -> None:
        """Reconstruct the latest index from canonical evidence."""
        self._rebuild()

    def latest(self, account_id: str, *, now_ms: int | None = None) -> AccountSnapshot:
        """Return the latest snapshot for EXACTLY ``account_id``.

        Raises :class:`AccountSnapshotNotFound` when that account has never been
        observed. It never substitutes another account and never returns zeros.
        """
        key = str(account_id or "").strip()
        if not key:
            raise AccountSnapshotNotFound("ACCOUNT_ID_REQUIRED")
        snapshot = self._index.get(key)
        if snapshot is None:
            self._rebuild()
            snapshot = self._index.get(key)
        if snapshot is None:
            raise AccountSnapshotNotFound(f"NO_ACCOUNT_SNAPSHOT:{key}")
        if now_ms is None:
            return snapshot
        return snapshot.evaluate(now_ms=int(now_ms), threshold_ms=self._threshold_ms)

    def latest_all(self, *, now_ms: int | None = None) -> dict[str, AccountSnapshot]:
        return {
            account_id: self.latest(account_id, now_ms=now_ms)
            for account_id in sorted(self._index)
        }

    def account_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._index))

    def record(self, snapshot: AccountSnapshot) -> None:
        """Refresh the in-memory index after a durable write."""
        current = self._index.get(snapshot.account_id)
        if current is None or snapshot.observed_at_utc_ms >= current.observed_at_utc_ms:
            self._index[snapshot.account_id] = snapshot

    @property
    def threshold_ms(self) -> int:
        return self._threshold_ms

    # ── observability ────────────────────────────────────────────────────
    def health(self, *, now_ms: int) -> dict[str, Any]:
        """Basic account snapshot health. No dashboard, no network."""
        accounts: dict[str, Any] = {}
        unavailable = invalid = partial = stale = source_errors = 0
        last_success_ms: int | None = None
        for account_id, snapshot in sorted(self._index.items()):
            evaluated = snapshot.evaluate(
                now_ms=int(now_ms), threshold_ms=self._threshold_ms)
            accounts[account_id] = {
                "status": evaluated.status.value,
                "observed_at_utc": evaluated.observed_at_utc,
                "age_ms": evaluated.age_ms(now_ms=int(now_ms)),
                "currency": evaluated.currency,
                "unavailable_field_count": len(evaluated.unavailable_fields),
                "invalid_field_count": len(evaluated.invalid_fields),
                "source_error": evaluated.source_error,
            }
            if evaluated.status is AccountSnapshotStatus.UNAVAILABLE:
                unavailable += 1
            elif evaluated.status is AccountSnapshotStatus.INVALID:
                invalid += 1
            elif evaluated.status is AccountSnapshotStatus.PARTIAL:
                partial += 1
            elif evaluated.status is AccountSnapshotStatus.STALE:
                stale += 1
            if evaluated.source_error:
                source_errors += 1
            if evaluated.status is not AccountSnapshotStatus.UNAVAILABLE and (
                last_success_ms is None
                or evaluated.observed_at_utc_ms > last_success_ms
            ):
                last_success_ms = evaluated.observed_at_utc_ms
        return {
            "accounts_observed": len(accounts),
            "freshness_threshold_ms": self._threshold_ms,
            "counts": {
                "complete": sum(
                    1 for a in accounts.values() if a["status"] == "COMPLETE"),
                "partial": partial,
                "unavailable": unavailable,
                "invalid": invalid,
                "stale": stale,
            },
            "source_error_count": source_errors,
            "last_success_observed_at_utc_ms": last_success_ms,
            "accounts": accounts,
        }


# ═══════════════════════════════════════════════════════════════════════════
# PERIODIC CADENCE
# ═══════════════════════════════════════════════════════════════════════════
#
# Snapshots must be available at meaningful risk decision points. Beyond the
# explicit producer hooks (startup, before a risk decision, after a fill), a
# configurable heartbeat keeps account state fresh in long-running processes.
#
# The heartbeat owns AT MOST ONE daemon thread, is started explicitly, is
# idempotent, and schedules from the PREVIOUS DUE TIME so a slow cycle never
# accumulates drift. It waits on an Event, so shutdown is immediate.


class AccountSnapshotHeartbeat:
    """Single-threaded, monotonic, configurable periodic account observation."""

    def __init__(
        self,
        producer: AccountSnapshotProducer,
        store: AccountSnapshotStore,
        *,
        identities: "Sequence[AccountIdentity]",
        interval_ms: int | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if interval_ms is None:
            interval_ms = snapshot_interval_ms()
        interval_ms = int(interval_ms)
        if interval_ms <= 0:
            raise ValueError("SNAPSHOT_INTERVAL_MUST_BE_POSITIVE")
        self._producer = producer
        self._store = store
        self._identities = tuple(identities)
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

    def tick(self) -> tuple[AccountSnapshot, ...]:
        """One bounded observation cycle across every configured account."""
        results = self._producer.observe_all(self._identities)
        for snapshot in results:
            self._store.record(snapshot)
        return results

    def start(self) -> None:
        """Start the heartbeat. Idempotent: never spawns a second thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="account_snapshot_heartbeat", daemon=True)
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
                logger.exception("[ACCOUNT_SNAPSHOT_HEARTBEAT] cycle failed")


__all__ = [
    "DATASET",
    "DEFAULT_LOCAL_DIR",
    "REQUIRED_COMPLETE_FIELDS",
    "SCHEMA_VERSION",
    "AccountIdentity",
    "AccountIdentityError",
    "AccountInfoSource",
    "AccountSnapshot",
    "AccountSnapshotError",
    "AccountSnapshotHeartbeat",
    "AccountSnapshotNotFound",
    "AccountSnapshotProducer",
    "AccountSnapshotStatus",
    "AccountSnapshotStore",
    "AccountSourceIdentityMismatch",
    "AccountSourceUnavailable",
    "Mt5AccountInfoSource",
    "capture_account_snapshot",
    "derive_snapshot_id",
    "freshness_threshold_ms",
    "persist_account_snapshot",
    "snapshot_interval_ms",
    "to_epoch_ms",
    "to_iso_utc",
    "utc_now",
]
