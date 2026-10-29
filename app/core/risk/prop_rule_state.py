"""Prop rule STATE authority and historical state derivation (Block 3B).

This module owns the DURABLE STATE a canonical prop rule needs before it can be
evaluated truthfully:

* the immutable initial account anchor,
* one immutable start-of-day anchor per (account, rule timezone, rule day),
* monotonic balance/equity high-water state,
* an authoritative realised daily P&L ledger with separated cost components,
* durable trading-day history and inactivity state,
* the typed account evaluation state assembled from all of the above.

WHAT THIS MODULE IS NOT
-----------------------
It is NOT a rule evaluator (see :mod:`core.risk.prop_rule_evaluator`) and it is
NOT an enforcement surface. It never blocks a trade, never closes a position,
never touches a kill switch, a runtime guard or a strategy, and it never imports
MetaTrader5. It reads injected evidence objects only.

WHY NOT THE LEGACY STATE
------------------------
``core/daily_reset.py`` and ``risk/daily_loss_guard.py`` derive the trading day
from a UTC hour and hold no initial anchor; ``risk/drawdown_guard.py`` tracks a
single equity peak. Those are live guard surfaces with different semantics. This
module is a separate, rule-day-explicit authority and deliberately neither reuses
nor modifies them.

TIME IS INJECTED, NEVER READ
----------------------------
Every function that needs "now" takes an explicit timezone-aware UTC instant.
There is no ``datetime.now()`` and no ``time.time()`` anywhere in this module, so
reconstruction after a restart is exact and replay is deterministic.

DURABILITY MODEL (Block 3B decision)
------------------------------------
Five state families are defined, but they are NOT five Production V1 datasets.
They are operational durable state, written as append-only local JSONL:

=========================  ==========================  ==================
family                     mutability                  classification
=========================  ==========================  ==================
initial_anchor             immutable, write-once       operational (A)
daily_anchor               immutable, one per rule day operational (A)
high_water                 derived from observations   operational (A)
trade_event                immutable, append-only      operational (A)
trading_day                immutable, one per rule day operational (A)
=========================  ==========================  ==================

Aggregates (daily P&L, high-water, trading days) are always RECOMPUTED from the
append-only evidence rather than stored as authority, so a late-arriving closed
trade updates the correct rule day deterministically and no historical evidence
is ever overwritten. None of these families enters the canonical research /
Production V1 namespace: they are per-account operational state with a different
lifecycle, and nothing in this block justifies widening that namespace.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, fields as dataclass_fields, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence, runtime_checkable
from zoneinfo import ZoneInfo

UTC = timezone.utc

#: Tolerance used ONLY when cross-checking two independent measures of the same
#: quantity (e.g. ledger-derived P&L against a balance delta). Never used to
#: correct a reported value.
CROSS_CHECK_TOLERANCE = 0.01


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# ERRORS Ã¢â‚¬â€ every one of them fails closed
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class PropRuleStateError(RuntimeError):
    """Base error for the Block 3B prop rule state authority."""


class InitialAnchorConflict(PropRuleStateError):
    """Two incompatible "initial" anchors were presented for one account.

    Raised instead of picking one by timestamp. Treating a later, wrong snapshot
    as "initial" is exactly the silent drift this block forbids.
    """


class ConflictingDuplicateEvent(PropRuleStateError):
    """The same source event identity arrived twice with different content."""


class TradingDayRecordConflict(PropRuleStateError):
    """Two different trading-day records were recorded for one rule day."""


class RuleDayUnavailable(PropRuleStateError):
    """The rule day could not be determined from the supplied semantics."""


class StateStoreError(PropRuleStateError):
    """The durable state store could not be read or written."""


class NoRulePackInForce(PropRuleStateError):
    """No rule pack was in force at the requested instant."""


class AmbiguousRulePackInForce(PropRuleStateError):
    """More than one rule pack claims to have been in force.

    Overlapping effective windows are never resolved by "nearest match" or by
    version ordering: the ambiguity is a governance defect and fails closed.
    """



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# ACCOUNT IDENTITY Ã¢â‚¬â€ the key every piece of state is scoped by
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@dataclass(frozen=True, order=True)
class AccountKey:
    """Exact identity of one trading account.

    Two accounts that merely share a broker, a login or a symbol are different
    accounts and can never share state. The slug is a filesystem-safe rendering
    of the FULL identity, so a per-account store can never collide.
    """

    account_id: str
    broker: str
    server: str
    login: int

    def __post_init__(self) -> None:
        for name in ("account_id", "broker", "server"):
            text = str(getattr(self, name) or "").strip()
            if not text:
                raise PropRuleStateError(f"ACCOUNT_KEY_FIELD_REQUIRED:{name}")
            object.__setattr__(self, name, text)
        if isinstance(self.login, bool) or not isinstance(self.login, int):
            raise PropRuleStateError("ACCOUNT_KEY_LOGIN_INVALID")
        if self.login <= 0:
            raise PropRuleStateError("ACCOUNT_KEY_LOGIN_INVALID")

    @property
    def slug(self) -> str:
        raw = f"{self.account_id}|{self.broker}|{self.server}|{self.login}"
        return "acct_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @property
    def identity(self) -> tuple[str, str, str, int]:
        return (self.account_id, self.broker, self.server, self.login)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "broker": self.broker,
            "server": self.server,
            "login": self.login,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AccountKey":
        return cls(
            account_id=str(payload["account_id"]),
            broker=str(payload["broker"]),
            server=str(payload["server"]),
            login=int(payload["login"]),
        )

    @classmethod
    def from_block2(cls, identity: Any) -> "AccountKey":
        """Build from a Block 2 ``AccountIdentity`` (or any equivalent)."""
        return cls(
            account_id=str(identity.account_id),
            broker=str(identity.broker),
            server=str(identity.server),
            login=int(identity.login),
        )


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# CANONICAL ENCODING Ã¢â‚¬â€ deterministic identity for every state record
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def _require_finite(value: float | None, code: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PropRuleStateError(f"{code}_MUST_BE_NUMERIC")
    number = float(value)
    if not math.isfinite(number):
        raise PropRuleStateError(f"{code}_MUST_BE_FINITE")
    return round(number, 8) + 0.0


def _require_aware(moment: datetime, code: str) -> datetime:
    if not isinstance(moment, datetime):
        raise PropRuleStateError(f"{code}_MUST_BE_DATETIME")
    if moment.tzinfo is None:
        raise PropRuleStateError(f"{code}_MUST_BE_TIMEZONE_AWARE")
    return moment.astimezone(UTC)


def _encode(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise PropRuleStateError("NAIVE_DATETIME_NOT_ALLOWED")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PropRuleStateError("NON_FINITE_STATE_VALUE")
        return round(value, 8) + 0.0
    if isinstance(value, AccountKey):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in sorted(value.items(), key=lambda i: str(i[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(str(i) for i in value) if isinstance(value, (set, frozenset)) else list(value)
        return [_encode(i) for i in items]
    if hasattr(value, "__dataclass_fields__"):
        # ``dataclasses.fields`` yields Field objects, not names.
        return {
            (f.name if hasattr(f, "name") else f): _encode(getattr(value, f.name if hasattr(f, "name") else f))
            for f in dataclass_fields(value)
        }
    if value is None or isinstance(value, (str, int)):
        return value
    raise PropRuleStateError(f"UNSERIALISABLE_STATE_VALUE:{type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(_encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def to_epoch_ms(moment: datetime) -> int:
    return int(_require_aware(moment, "EPOCH_MOMENT").timestamp() * 1000)



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# RULE DAY SEMANTICS Ã¢â‚¬â€ reset clock interpreted in the rule pack's timezone
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@dataclass(frozen=True)
class RuleDayDefinition:
    """Exact day-boundary semantics of one rule, in the rule pack's timezone.

    ``reset_time`` is a LOCAL WALL CLOCK in ``timezone_name``. A 17:00
    America/New_York reset means 17:00 New York time whatever the UTC offset
    happens to be on the day in question, which is why each boundary is
    recomputed per rule day instead of using a fixed UTC offset.
    """

    timezone_name: str
    reset_time: time = time(0, 0)

    def __post_init__(self) -> None:
        name = str(self.timezone_name or "").strip()
        if not name:
            raise RuleDayUnavailable("RULE_DAY_TIMEZONE_REQUIRED")
        if name.upper() in {"UTC", "Z"}:
            name = "UTC"
        else:
            try:
                ZoneInfo(name)
            except Exception as exc:  # any zone failure is fatal here
                raise RuleDayUnavailable(f"UNKNOWN_RULE_TIMEZONE:{name}") from exc
        if not isinstance(self.reset_time, time) or self.reset_time.tzinfo is not None:
            raise RuleDayUnavailable("RULE_DAY_RESET_TIME_MUST_BE_NAIVE_LOCAL_TIME")
        object.__setattr__(self, "timezone_name", name)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def key(self) -> str:
        return f"{self.timezone_name}@{self.reset_time.strftime('%H:%M:%S')}"

    def day_start_utc(self, rule_day: date) -> datetime:
        """The exact UTC instant this rule day begins.

        The boundary is built from the LOCAL wall clock on ``rule_day`` and
        localised with the offset actually in force on that date, so a 17:00 New
        York reset is 22:00Z in winter and 21:00Z in summer.
        """
        if not isinstance(rule_day, date):
            raise RuleDayUnavailable("RULE_DAY_MUST_BE_DATE")
        local = datetime.combine(rule_day, self.reset_time)
        # A nonexistent local time (spring-forward gap) resolves to a real
        # instant rather than raising; that resolved instant is authoritative.
        return local.replace(tzinfo=self.zone).astimezone(UTC)

    def rule_day_for(self, moment_utc: datetime) -> date:
        """Which rule day an instant belongs to, in THIS timezone.

        The only supported way to obtain a rule date. Host-machine local time and
        UTC calendar days are never used.
        """
        moment = _require_aware(moment_utc, "RULE_DAY_MOMENT")
        local = moment.astimezone(self.zone)
        start_today = datetime.combine(local.date(), self.reset_time)
        # Before today's reset instant -> the day that began at yesterday's reset.
        if local.replace(tzinfo=None) < start_today:
            return local.date() - timedelta(days=1)
        return local.date()

    def day_end_utc(self, rule_day: date) -> datetime:
        """The exact UTC instant this rule day ends (= the next day's start)."""
        return self.day_start_utc(rule_day + timedelta(days=1))

    def contains(self, moment_utc: datetime) -> bool:
        moment = _require_aware(moment_utc, "RULE_DAY_MOMENT")
        day = self.rule_day_for(moment)
        return self.day_start_utc(day) <= moment < self.day_end_utc(day)

    def iter_rule_days(self, start_utc: datetime, end_utc: datetime) -> tuple[date, ...]:
        """Every rule day touched by ``[start_utc, end_utc)``, ascending.

        Days are enumerated, so a DST transition that shortens or lengthens a day
        cannot produce a duplicate day or skip one.
        """
        start = _require_aware(start_utc, "REPLAY_START")
        end = _require_aware(end_utc, "REPLAY_END")
        if end < start:
            raise RuleDayUnavailable("REPLAY_END_BEFORE_START")
        first = self.rule_day_for(start)
        last = self.rule_day_for(end - timedelta(microseconds=1)) if end > start else first
        days: list[date] = []
        cursor = first
        for _ in range(4000):  # bounded so a pathological range cannot spin
            if cursor > last:
                break
            days.append(cursor)
            cursor = cursor + timedelta(days=1)
        return tuple(days)


#: How a start-of-day anchor value was obtained. ``EXACT_BOUNDARY`` means an
#: authoritative snapshot exists AT the boundary instant. ``FIRST_VALID_AFTER``
#: means the first authoritative observation at/after the boundary, and is always
#: labelled so a reader can tell the two apart.
ANCHOR_BASES: frozenset[str] = frozenset({"EXACT_BOUNDARY", "FIRST_VALID_AFTER"})



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# INITIAL ACCOUNT ANCHOR Ã¢â‚¬â€ immutable, write-once, never recalculated
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@dataclass(frozen=True)
class InitialAccountAnchor:
    """The ONE immutable "starting point" of a challenge for one account.

    WHY IMMUTABLE
    -------------
    "Initial balance" is the denominator of the static drawdown and the profit
    target. If it could drift, every historical drawdown number would silently
    change. It is therefore established once from an explicit source snapshot and
    never recomputed from a later balance.

    WHY FAIL CLOSED ON CONFLICT
    ---------------------------
    If two sources present different initial values, choosing by timestamp would
    let a later, wrong snapshot silently rewrite history. That is
    :class:`InitialAnchorConflict`.
    """

    account: AccountKey
    account_currency: str
    initial_balance: float
    initial_equity: float
    observed_at_utc: datetime
    source_snapshot_id: str
    source_provenance: str
    rule_pack_id: str | None = None
    anchor_id: str = ""
    evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_currency", self._normalise_currency(self.account_currency))
        balance = _require_finite(self.initial_balance, "INITIAL_BALANCE")
        equity = _require_finite(self.initial_equity, "INITIAL_EQUITY")
        if balance is None or equity is None:
            raise PropRuleStateError("INITIAL_ANCHOR_REQUIRES_BALANCE_AND_EQUITY")
        if balance <= 0 or equity <= 0:
            raise PropRuleStateError("INITIAL_ANCHOR_MUST_BE_POSITIVE")
        object.__setattr__(self, "initial_balance", balance)
        object.__setattr__(self, "initial_equity", equity)
        object.__setattr__(
            self, "observed_at_utc", _require_aware(self.observed_at_utc, "ANCHOR_OBSERVED_AT")
        )
        for name in ("source_snapshot_id", "source_provenance"):
            text = str(getattr(self, name) or "").strip()
            if not text:
                raise PropRuleStateError(f"INITIAL_ANCHOR_REQUIRES:{name}")
            object.__setattr__(self, name, text)
        if not self.anchor_id:
            object.__setattr__(self, "anchor_id", self.derive_anchor_id())

    @staticmethod
    def _normalise_currency(currency: str) -> str:
        text = str(currency or "").strip().upper()
        if not text:
            raise PropRuleStateError("INITIAL_ANCHOR_REQUIRES_ACCOUNT_CURRENCY")
        return text

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "account": self.account.to_dict(),
            "account_currency": self.account_currency,
            "initial_balance": self.initial_balance,
            "initial_equity": self.initial_equity,
        }

    def derive_anchor_id(self) -> str:
        return "pia_" + content_hash(self._identity_payload())[:32]

    def same_values_as(self, other: "InitialAccountAnchor") -> bool:
        """Semantic equality of the ANCHOR, ignoring provenance.

        Provenance may legitimately differ (the same starting balance can be cited
        twice); the VALUES may not.
        """
        return (
            self.account.identity == other.account.identity
            and self.account_currency == other.account_currency
            and self.initial_balance == other.initial_balance
            and self.initial_equity == other.initial_equity
        )

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InitialAccountAnchor":
        return cls(
            account=AccountKey.from_dict(payload["account"]),
            account_currency=payload["account_currency"],
            initial_balance=payload["initial_balance"],
            initial_equity=payload["initial_equity"],
            observed_at_utc=datetime.fromisoformat(payload["observed_at_utc"]),
            source_snapshot_id=payload["source_snapshot_id"],
            source_provenance=payload["source_provenance"],
            rule_pack_id=payload.get("rule_pack_id"),
            anchor_id=payload.get("anchor_id", ""),
            evidence=payload.get("evidence"),
        )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# START-OF-DAY ANCHOR Ã¢â‚¬â€ exactly one per (account, timezone, rule day)
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@dataclass(frozen=True)
class DailyAccountAnchor:
    """Immutable start-of-day reference for one rule day.

    The key is (account, rule timezone, rule day). Two accounts, two timezones or
    two rule days can never share an anchor, so a 00:00 New York anchor and a
    17:00 London anchor for the same account are genuinely different objects.

    An anchor is NEVER invented. If no authoritative snapshot exists at the
    boundary, no anchor is created and the dependent evaluation stays
    INDETERMINATE. The optional ``FIRST_VALID_AFTER`` basis may only be used when
    the caller explicitly asks for it, and it is always labelled as such.
    """

    account: AccountKey
    account_currency: str
    rule_day: date
    timezone_name: str
    rule_day_definition: str
    start_of_day_balance: float
    start_of_day_equity: float
    anchor_at_utc: datetime
    source_snapshot_id: str
    source_provenance: str
    start_of_day_floating_pnl: float | None = None
    rule_pack_id: str | None = None
    anchor_id: str = ""
    anchor_basis: str = "EXACT_BOUNDARY"

    def __post_init__(self) -> None:
        currency = str(self.account_currency or "").strip().upper()
        if not currency:
            raise PropRuleStateError("DAILY_ANCHOR_REQUIRES_ACCOUNT_CURRENCY")
        object.__setattr__(self, "account_currency", currency)
        if not isinstance(self.rule_day, date):
            raise PropRuleStateError("DAILY_ANCHOR_REQUIRES_RULE_DAY")
        balance = _require_finite(self.start_of_day_balance, "START_OF_DAY_BALANCE")
        equity = _require_finite(self.start_of_day_equity, "START_OF_DAY_EQUITY")
        if balance is None or equity is None:
            raise PropRuleStateError("DAILY_ANCHOR_REQUIRES_BALANCE_AND_EQUITY")
        object.__setattr__(self, "start_of_day_balance", balance)
        object.__setattr__(self, "start_of_day_equity", equity)
        object.__setattr__(
            self, "start_of_day_floating_pnl",
            _require_finite(self.start_of_day_floating_pnl, "START_OF_DAY_FLOATING"),
        )
        object.__setattr__(self, "anchor_at_utc", _require_aware(self.anchor_at_utc, "ANCHOR_AT"))
        for name in ("timezone_name", "rule_day_definition", "source_snapshot_id", "source_provenance"):
            text = str(getattr(self, name) or "").strip()
            if not text:
                raise PropRuleStateError(f"DAILY_ANCHOR_REQUIRES:{name}")
            object.__setattr__(self, name, text)
        if self.anchor_basis not in ANCHOR_BASES:
            raise PropRuleStateError(f"DAILY_ANCHOR_UNKNOWN_BASIS:{self.anchor_basis}")
        if not self.anchor_id:
            object.__setattr__(self, "anchor_id", self.derive_anchor_id())

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "account": self.account.to_dict(),
            "account_currency": self.account_currency,
            "rule_day": self.rule_day,
            "timezone_name": self.timezone_name,
            "rule_day_definition": self.rule_day_definition,
            "start_of_day_balance": self.start_of_day_balance,
            "start_of_day_equity": self.start_of_day_equity,
        }

    def derive_anchor_id(self) -> str:
        return "daa_" + content_hash(self._identity_payload())[:32]

    def same_values_as(self, other: "DailyAccountAnchor") -> bool:
        return (
            self.account.identity == other.account.identity
            and self.rule_day == other.rule_day
            and self.timezone_name == other.timezone_name
            and self.account_currency == other.account_currency
            and self.start_of_day_balance == other.start_of_day_balance
            and self.start_of_day_equity == other.start_of_day_equity
        )

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DailyAccountAnchor":
        return cls(
            account=AccountKey.from_dict(payload["account"]),
            account_currency=payload["account_currency"],
            rule_day=date.fromisoformat(payload["rule_day"]),
            timezone_name=payload["timezone_name"],
            rule_day_definition=payload["rule_day_definition"],
            start_of_day_balance=payload["start_of_day_balance"],
            start_of_day_equity=payload["start_of_day_equity"],
            anchor_at_utc=datetime.fromisoformat(payload["anchor_at_utc"]),
            source_snapshot_id=payload["source_snapshot_id"],
            source_provenance=payload["source_provenance"],
            start_of_day_floating_pnl=payload.get("start_of_day_floating_pnl"),
            rule_pack_id=payload.get("rule_pack_id"),
            anchor_id=payload.get("anchor_id", ""),
            anchor_basis=payload.get("anchor_basis", "EXACT_BOUNDARY"),
        )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# HIGH-WATER STATE Ã¢â‚¬â€ balance and equity tracked SEPARATELY, monotonically
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class HighWaterBasis(str, Enum):
    """WHICH quantity the high-water mark tracks.

    Balance and equity are never the same number and are never blended. A
    trailing drawdown on equity is a different rule from one on balance, and a
    single "peak" number cannot honestly represent both.
    """

    BALANCE = "BALANCE"
    EQUITY = "EQUITY"


class HighWaterGapKind(str, Enum):
    """Declared uncertainty in a high-water projection.

    A MISSING observation must never reduce the high-water mark, and an unseen
    period must never be assumed to have had no higher equity. When the evidence
    has a hole the gap is recorded explicitly, so the state exposes lower-bound
    semantics instead of claiming certainty.
    """

    NONE = "NONE"
    #: No valid observation yet, so the high-water is only a lower bound.
    NO_OBSERVATIONS = "NO_OBSERVATIONS"
    #: Observations exist but a gap in coverage was detected.
    COVERAGE_GAP = "COVERAGE_GAP"
    #: One or more observations were rejected as invalid.
    INVALID_OBSERVATIONS = "INVALID_OBSERVATIONS"


@dataclass(frozen=True)
class HighWaterMark:
    """One monotone high-water value with the evidence that set it."""

    basis: HighWaterBasis
    value: float | None
    high_at_utc: datetime | None
    source_snapshot_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_finite(self.value, "HIGH_WATER_VALUE"))
        if self.value is not None and self.value <= 0:
            raise PropRuleStateError("HIGH_WATER_VALUE_MUST_BE_POSITIVE")
        if self.high_at_utc is not None:
            object.__setattr__(
                self, "high_at_utc", _require_aware(self.high_at_utc, "HIGH_WATER_AT")
            )
        if self.value is not None and self.high_at_utc is None:
            raise PropRuleStateError("HIGH_WATER_VALUE_REQUIRES_INSTANT")

    @property
    def is_established(self) -> bool:
        return self.value is not None

    def advanced_by(self, candidate: float) -> bool:
        """Whether a candidate observation would ADVANCE this mark.

        Lower or equal observations never move the mark, and never move it
        backwards. This is what makes reconstruction exact.
        """
        number = _require_finite(candidate, "HIGH_WATER_CANDIDATE")
        if number is None:
            return False
        if self.value is None:
            return True
        return number > self.value

    def advance(
        self, candidate: float, moment_utc: datetime, source_snapshot_id: str
    ) -> "HighWaterMark":
        """Return an advanced mark, or ``self`` unchanged when not a new high."""
        if not self.advanced_by(candidate):
            return self
        return HighWaterMark(
            basis=self.basis,
            value=candidate,
            high_at_utc=_require_aware(moment_utc, "HIGH_WATER_AT"),
            source_snapshot_id=str(source_snapshot_id or "").strip() or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})



@dataclass(frozen=True)
class HighWaterState:
    """Durable high-water projection for one account, reconstructed on demand.

    ``balance_mark`` and ``equity_mark`` are SEPARATE. ``observation_count`` and
    ``last_observed_at_utc`` make coverage auditable, and ``gap`` makes
    uncertainty explicit instead of silently claiming the unseen period was
    unprofitable.
    """

    account: AccountKey
    account_currency: str
    balance_mark: HighWaterMark
    equity_mark: HighWaterMark
    observation_count: int = 0
    rejected_observation_count: int = 0
    last_observed_at_utc: datetime | None = None
    gap: HighWaterGapKind = HighWaterGapKind.NONE
    gap_detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.balance_mark, HighWaterMark) or self.balance_mark.basis is not HighWaterBasis.BALANCE:
            raise PropRuleStateError("HIGH_WATER_BALANCE_MARK_REQUIRED")
        if not isinstance(self.equity_mark, HighWaterMark) or self.equity_mark.basis is not HighWaterBasis.EQUITY:
            raise PropRuleStateError("HIGH_WATER_EQUITY_MARK_REQUIRED")
        if self.observation_count < 0 or self.rejected_observation_count < 0:
            raise PropRuleStateError("HIGH_WATER_COUNTS_MUST_BE_NON_NEGATIVE")
        if self.last_observed_at_utc is not None:
            object.__setattr__(
                self, "last_observed_at_utc",
                _require_aware(self.last_observed_at_utc, "HIGH_WATER_LAST_OBSERVED"),
            )
        if self.observation_count == 0 and self.gap is HighWaterGapKind.NONE:
            object.__setattr__(self, "gap", HighWaterGapKind.NO_OBSERVATIONS)
            object.__setattr__(self, "gap_detail", self.gap_detail or "NO_VALID_OBSERVATION_RECORDED")

    @classmethod
    def empty(cls, account: AccountKey, account_currency: str) -> "HighWaterState":
        return cls(
            account=account,
            account_currency=str(account_currency or "").strip().upper(),
            balance_mark=HighWaterMark(HighWaterBasis.BALANCE, None, None, None),
            equity_mark=HighWaterMark(HighWaterBasis.EQUITY, None, None, None),
        )

    @property
    def high_water_balance(self) -> float | None:
        return self.balance_mark.value

    @property
    def high_water_equity(self) -> float | None:
        return self.equity_mark.value

    @property
    def is_complete(self) -> bool:
        """Whether both marks rest on real observations with no known gap."""
        return (
            self.observation_count > 0
            and self.balance_mark.is_established
            and self.equity_mark.is_established
            and self.gap is HighWaterGapKind.NONE
        )

    def advance(
        self,
        *,
        balance: float | None,
        equity: float | None,
        observed_at_utc: datetime,
        source_snapshot_id: str,
    ) -> "HighWaterState":
        """Fold one authoritative observation into the projection.

        Monotonic by construction. A lower value never lowers the mark, and a
        missing observation is recorded as coverage, never as a decline.
        """
        moment = _require_aware(observed_at_utc, "HIGH_WATER_OBSERVED_AT")
        snapshot_id = str(source_snapshot_id or "").strip()
        if not snapshot_id:
            raise PropRuleStateError("HIGH_WATER_REQUIRES_SOURCE_SNAPSHOT_ID")
        balance_value = _require_finite(balance, "HIGH_WATER_BALANCE")
        equity_value = _require_finite(equity, "HIGH_WATER_EQUITY")
        rejected = 0
        if balance_value is None:
            rejected += 1
        if equity_value is None:
            rejected += 1
        new_balance = self.balance_mark
        new_equity = self.equity_mark
        if balance_value is not None and balance_value > 0:
            new_balance = self.balance_mark.advance(balance_value, moment, snapshot_id)
        if equity_value is not None and equity_value > 0:
            new_equity = self.equity_mark.advance(equity_value, moment, snapshot_id)
        gap = self.gap
        detail = self.gap_detail
        if balance_value is None or equity_value is None:
            gap = HighWaterGapKind.INVALID_OBSERVATIONS
            detail = "OBSERVATION_MISSING_BALANCE_OR_EQUITY"
        elif gap is HighWaterGapKind.NO_OBSERVATIONS:
            gap = HighWaterGapKind.NONE
            detail = ""
        return replace(
            self,
            balance_mark=new_balance,
            equity_mark=new_equity,
            observation_count=self.observation_count + 1,
            rejected_observation_count=self.rejected_observation_count + rejected,
            last_observed_at_utc=moment,
            gap=gap,
            gap_detail=detail,
        )

    def note_coverage_gap(self, detail: str) -> "HighWaterState":
        """Record that evidence is missing over a period.

        The marks are left untouched: a missing observation can never reduce a
        high-water value, so the mark remains a LOWER BOUND and the state declares
        the uncertainty rather than assuming the gap was flat.
        """
        text = str(detail or "COVERAGE_GAP")
        if not self.balance_mark.is_established and not self.equity_mark.is_established:
            return replace(self, gap=HighWaterGapKind.NO_OBSERVATIONS, gap_detail=text)
        return replace(self, gap=HighWaterGapKind.COVERAGE_GAP, gap_detail=text)

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# CLOSED TRADE EVIDENCE Ã¢â‚¬â€ append-only, account-scoped, deduplicated
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class CloseEventKind(str, Enum):
    """How a source described a close. Never collapsed into one value."""

    FULL_CLOSE = "FULL_CLOSE"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    #: The source did not say. Recorded honestly rather than assumed.
    UNSPECIFIED = "UNSPECIFIED"


@dataclass(frozen=True)
class ClosedTradeEvent:
    """ONE authoritative closed-trade event for ONE account.

    DEDUPLICATION IDENTITY
    ---------------------
    ``event_id`` is derived from the FULL account identity plus the source's own
    close-deal identity. Consequences this guarantees:

    * the same event ingested twice counts ONCE;
    * two accounts reporting the same ticket stay DISTINCT (account is hashed in);
    * two PARTIAL closes of one position are TWO events, because the source gives
      each close deal its own identity.

    COST COMPONENTS ARE SEPARATE
    ----------------------------
    ``commission``, ``swap`` and ``fees`` are each independently optional. A
    missing component is ``None``, never ``0.0``: a rule that includes commission
    cannot silently treat "unknown" as "free".
    """

    account: AccountKey
    account_currency: str
    source: str
    source_trade_id: str
    position_ticket: int | None
    symbol: str
    closed_at_utc: datetime
    gross_realised_pnl: float
    volume: float | None = None
    close_kind: CloseEventKind = CloseEventKind.UNSPECIFIED
    commission: float | None = None
    swap: float | None = None
    fees: float | None = None
    duration_seconds: float | None = None
    event_id: str = ""
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        currency = str(self.account_currency or "").strip().upper()
        if not currency:
            raise PropRuleStateError("TRADE_EVENT_REQUIRES_ACCOUNT_CURRENCY")
        object.__setattr__(self, "account_currency", currency)
        for name in ("source", "source_trade_id"):
            text = str(getattr(self, name) or "").strip()
            if not text:
                raise PropRuleStateError(f"TRADE_EVENT_REQUIRES:{name}")
            object.__setattr__(self, name, text)
        object.__setattr__(self, "symbol", str(self.symbol or "").strip())
        object.__setattr__(
            self, "closed_at_utc", _require_aware(self.closed_at_utc, "TRADE_EVENT_CLOSED_AT")
        )
        gross = _require_finite(self.gross_realised_pnl, "TRADE_EVENT_GROSS")
        if gross is None:
            raise PropRuleStateError("TRADE_EVENT_REQUIRES_GROSS_PNL")
        object.__setattr__(self, "gross_realised_pnl", gross)
        for name in ("commission", "swap", "fees", "volume", "duration_seconds"):
            object.__setattr__(self, name, _require_finite(getattr(self, name), f"TRADE_EVENT_{name}"))
        if not isinstance(self.close_kind, CloseEventKind):
            raise PropRuleStateError("TRADE_EVENT_CLOSE_KIND_INVALID")
        if self.event_id:
            if self.event_id != self.derive_event_id():
                raise PropRuleStateError("TRADE_EVENT_ID_MISMATCH")
        else:
            object.__setattr__(self, "event_id", self.derive_event_id())

    def derive_event_id(self) -> str:
        """Deterministic, ACCOUNT-SCOPED identity of this close event."""
        payload = {
            "account": self.account.to_dict(),
            "source": self.source,
            "source_trade_id": self.source_trade_id,
            "position_ticket": self.position_ticket,
            "closed_at_utc": self.closed_at_utc,
            "close_kind": self.close_kind.value,
            "volume": self.volume,
        }
        return "cte_" + content_hash(payload)[:32]

    @property
    def is_full_close(self) -> bool:
        return self.close_kind is CloseEventKind.FULL_CLOSE

    def _cost_payload(self) -> dict[str, Any]:
        return {
            "gross_realised_pnl": self.gross_realised_pnl,
            "commission": self.commission,
            "swap": self.swap,
            "fees": self.fees,
        }

    def same_values_as(self, other: "ClosedTradeEvent") -> bool:
        """Whether two events with one identity carry the same economics.

        Re-ingesting the identical event is a no-op. Re-ingesting the SAME
        identity with DIFFERENT amounts is a source conflict, not an update.
        """
        return self.event_id == other.event_id and self._cost_payload() == other._cost_payload()

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ClosedTradeEvent":
        return cls(
            account=AccountKey.from_dict(payload["account"]),
            account_currency=payload["account_currency"],
            source=payload["source"],
            source_trade_id=payload["source_trade_id"],
            position_ticket=payload.get("position_ticket"),
            symbol=payload.get("symbol", ""),
            closed_at_utc=datetime.fromisoformat(payload["closed_at_utc"]),
            gross_realised_pnl=payload["gross_realised_pnl"],
            volume=payload.get("volume"),
            close_kind=CloseEventKind(payload.get("close_kind", CloseEventKind.UNSPECIFIED.value)),
            commission=payload.get("commission"),
            swap=payload.get("swap"),
            fees=payload.get("fees"),
            duration_seconds=payload.get("duration_seconds"),
            event_id=payload.get("event_id", ""),
            evidence_ref=payload.get("evidence_ref"),
        )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# DAILY REALISED P&L LEDGER Ã¢â‚¬â€ derived deterministically from close evidence
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@dataclass(frozen=True)
class DailyPnlLedgerEntry:
    """Authoritative realised P&L for ONE account on ONE rule day.

    Components are kept SEPARATE (gross, commission, swap, fees) and each carries
    its own completeness flag, because a rule may include some and exclude others.
    ``net_realised_pnl`` is published only when every component is known.

    This is a PROJECTION: it is always recomputed from the append-only close
    events, so a late-arriving trade updates the correct rule day and never
    rewrites or deletes an earlier day.
    """

    account: AccountKey
    account_currency: str
    rule_day: date
    timezone_name: str
    rule_day_definition: str

    gross_realised_pnl: float = 0.0
    commission: float | None = 0.0
    swap: float | None = 0.0
    fees: float | None = 0.0
    net_realised_pnl: float | None = 0.0

    trade_count: int = 0
    full_close_count: int = 0
    partial_close_count: int = 0
    total_volume: float | None = 0.0

    source_trade_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    first_close_at_utc: datetime | None = None
    last_close_at_utc: datetime | None = None

    #: Component names whose value could not be established from the evidence.
    unknown_components: tuple[str, ...] = ()
    rule_pack_id: str | None = None
    ledger_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.rule_day, date):
            raise PropRuleStateError("LEDGER_REQUIRES_RULE_DAY")
        object.__setattr__(self, "source_trade_ids", tuple(self.source_trade_ids))
        object.__setattr__(self, "event_ids", tuple(self.event_ids))
        object.__setattr__(self, "symbols", tuple(self.symbols))
        object.__setattr__(self, "unknown_components", tuple(sorted(self.unknown_components)))
        gross = _require_finite(self.gross_realised_pnl, "LEDGER_GROSS")
        object.__setattr__(self, "gross_realised_pnl", 0.0 if gross is None else gross)
        for name in ("commission", "swap", "fees", "total_volume"):
            object.__setattr__(self, name, _require_finite(getattr(self, name), f"LEDGER_{name}"))
        if self.trade_count < 0:
            raise PropRuleStateError("LEDGER_TRADE_COUNT_MUST_BE_NON_NEGATIVE")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise PropRuleStateError("LEDGER_DUPLICATE_EVENT_IDS")
        if not self.ledger_id:
            object.__setattr__(self, "ledger_id", self.derive_ledger_id())

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "account": self.account.to_dict(),
            "account_currency": self.account_currency,
            "rule_day": self.rule_day,
            "timezone_name": self.timezone_name,
            "rule_day_definition": self.rule_day_definition,
            "gross_realised_pnl": self.gross_realised_pnl,
            "commission": self.commission,
            "swap": self.swap,
            "fees": self.fees,
            "trade_count": self.trade_count,
            "event_ids": list(self.event_ids),
        }

    def derive_ledger_id(self) -> str:
        return "dpl_" + content_hash(self._identity_payload())[:32]

    @property
    def is_complete(self) -> bool:
        """Whether every cost component is known, so net P&L is authoritative."""
        return not self.unknown_components and self.net_realised_pnl is not None

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DailyPnlLedgerEntry":
        first_close = payload.get("first_close_at_utc")
        last_close = payload.get("last_close_at_utc")
        return cls(
            account=AccountKey.from_dict(payload["account"]),
            account_currency=payload["account_currency"],
            rule_day=date.fromisoformat(payload["rule_day"]),
            timezone_name=payload["timezone_name"],
            rule_day_definition=payload["rule_day_definition"],
            gross_realised_pnl=payload.get("gross_realised_pnl", 0.0),
            commission=payload.get("commission"),
            swap=payload.get("swap"),
            fees=payload.get("fees"),
            net_realised_pnl=payload.get("net_realised_pnl"),
            trade_count=int(payload.get("trade_count", 0)),
            full_close_count=int(payload.get("full_close_count", 0)),
            partial_close_count=int(payload.get("partial_close_count", 0)),
            total_volume=payload.get("total_volume"),
            source_trade_ids=tuple(payload.get("source_trade_ids") or ()),
            event_ids=tuple(payload.get("event_ids") or ()),
            symbols=tuple(payload.get("symbols") or ()),
            first_close_at_utc=datetime.fromisoformat(first_close) if first_close else None,
            last_close_at_utc=datetime.fromisoformat(last_close) if last_close else None,
            unknown_components=tuple(payload.get("unknown_components") or ()),
            rule_pack_id=payload.get("rule_pack_id"),
            ledger_id=payload.get("ledger_id", ""),
        )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# TRADING DAY HISTORY + INACTIVITY Ã¢â‚¬â€ rule-day based, never wall-clock based
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class TradingDayCriterion(str, Enum):
    """How a rule decides that a day QUALIFIED.

    The 3A ``TradingDayRule.qualifying_criterion`` string is mapped onto these
    explicit criteria. An unrecognised or absent criterion is NEVER guessed: it
    yields :attr:`TradingDayCriterion.UNSPECIFIED`, and the evaluation that
    depends on it stays INDETERMINATE.
    """

    ANY_CLOSED_TRADE = "ANY_CLOSED_TRADE"
    MIN_CLOSED_TRADES = "MIN_CLOSED_TRADES"
    MIN_TOTAL_VOLUME = "MIN_TOTAL_VOLUME"
    MIN_TOTAL_DURATION = "MIN_TOTAL_DURATION"
    MIN_REALISED_PNL = "MIN_REALISED_PNL"
    #: The source did not state a criterion.
    UNSPECIFIED = "UNSPECIFIED"


#: String forms accepted from a 3A rule's ``qualifying_criterion`` field.
CRITERION_ALIASES: Mapping[str, TradingDayCriterion] = {
    "ANY_CLOSED_TRADE": TradingDayCriterion.ANY_CLOSED_TRADE,
    "ANY_TRADE": TradingDayCriterion.ANY_CLOSED_TRADE,
    "CLOSED_TRADE": TradingDayCriterion.ANY_CLOSED_TRADE,
    "MIN_CLOSED_TRADES": TradingDayCriterion.MIN_CLOSED_TRADES,
    "MINIMUM_CLOSED_TRADES": TradingDayCriterion.MIN_CLOSED_TRADES,
    "MIN_TOTAL_VOLUME": TradingDayCriterion.MIN_TOTAL_VOLUME,
    "MIN_LOT": TradingDayCriterion.MIN_TOTAL_VOLUME,
    "MIN_LOTS": TradingDayCriterion.MIN_TOTAL_VOLUME,
    "MIN_TOTAL_DURATION": TradingDayCriterion.MIN_TOTAL_DURATION,
    "MIN_DURATION": TradingDayCriterion.MIN_TOTAL_DURATION,
    "MIN_REALISED_PNL": TradingDayCriterion.MIN_REALISED_PNL,
    "MIN_PROFIT": TradingDayCriterion.MIN_REALISED_PNL,
}


def resolve_criterion(raw: str | None) -> TradingDayCriterion:
    """Map a declared criterion string onto the explicit vocabulary."""
    if raw is None:
        return TradingDayCriterion.UNSPECIFIED
    text = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
    if not text:
        return TradingDayCriterion.UNSPECIFIED
    return CRITERION_ALIASES.get(text, TradingDayCriterion.UNSPECIFIED)


#: Duration forms accepted from a 3A ``InactivityRule.max_inactive_duration``.
_DURATION_UNITS: Mapping[str, int] = {"D": 1, "DAY": 1, "DAYS": 1, "W": 7}


def parse_inactive_days(raw: str | None) -> int | None:
    """Parse an inactivity window such as ``"3d"`` / ``"2 DAYS"``.

    Returns ``None`` when the window is absent or cannot be parsed exactly, which
    keeps the dependent evaluation INDETERMINATE rather than defaulting to a
    number.
    """
    if raw is None:
        return None
    text = str(raw).strip().upper().replace(" ", "")
    if not text:
        return None
    digits = ""
    index = 0
    while index < len(text) and (text[index].isdigit() or text[index] == "."):
        digits += text[index]
        index += 1
    unit = text[index:]
    if not digits or unit not in _DURATION_UNITS:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    if value < 0:
        return None
    return int(round(value * _DURATION_UNITS[unit]))


@dataclass(frozen=True)
class TradingDayRecord:
    """One rule day's trading evidence, immutable once recorded.

    The day is the RULE day in the rule pack's timezone, not a host calendar day.
    All raw evidence is retained so a qualifying criterion can be applied later
    without re-reading any source.
    """

    account: AccountKey
    account_currency: str
    rule_day: date
    timezone_name: str
    rule_day_definition: str
    closed_trade_count: int
    total_volume: float | None
    total_duration_seconds: float | None
    gross_realised_pnl: float
    net_realised_pnl: float | None
    has_closed_trade: bool
    day_start_utc: datetime
    day_end_utc: datetime
    record_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.rule_day, date):
            raise PropRuleStateError("TRADING_DAY_REQUIRES_RULE_DAY")
        if self.closed_trade_count < 0:
            raise PropRuleStateError("TRADING_DAY_COUNT_MUST_BE_NON_NEGATIVE")
        for name in ("total_volume", "total_duration_seconds", "gross_realised_pnl", "net_realised_pnl"):
            object.__setattr__(self, name, _require_finite(getattr(self, name), f"TRADING_DAY_{name}"))
        object.__setattr__(self, "day_start_utc", _require_aware(self.day_start_utc, "TRADING_DAY_START"))
        object.__setattr__(self, "day_end_utc", _require_aware(self.day_end_utc, "TRADING_DAY_END"))
        if self.day_end_utc <= self.day_start_utc:
            raise PropRuleStateError("TRADING_DAY_WINDOW_NOT_POSITIVE")
        if self.has_closed_trade != (self.closed_trade_count > 0):
            raise PropRuleStateError("TRADING_DAY_TRADE_FLAG_INCONSISTENT")
        if not self.record_id:
            object.__setattr__(self, "record_id", self.derive_record_id())

    def derive_record_id(self) -> str:
        payload = {
            "account": self.account.to_dict(),
            "rule_day": self.rule_day,
            "timezone_name": self.timezone_name,
            "rule_day_definition": self.rule_day_definition,
            "closed_trade_count": self.closed_trade_count,
            "total_volume": self.total_volume,
            "total_duration_seconds": self.total_duration_seconds,
            "gross_realised_pnl": self.gross_realised_pnl,
            "net_realised_pnl": self.net_realised_pnl,
        }
        return "tdr_" + content_hash(payload)[:32]


    def qualifies(
        self,
        criterion: TradingDayCriterion,
        *,
        minimum_closed_trades: int | None = None,
        minimum_volume: float | None = None,
        minimum_duration_seconds: float | None = None,
        minimum_pnl: float | None = None,
    ) -> bool | None:
        """Whether this day qualifies. ``None`` when the criterion is unknown.

        ``None`` is NOT ``False``: an unspecified criterion must leave the
        evaluation INDETERMINATE rather than silently counting the day as failed.
        """
        if criterion is TradingDayCriterion.UNSPECIFIED:
            return None
        if criterion is TradingDayCriterion.ANY_CLOSED_TRADE:
            return self.has_closed_trade
        if criterion is TradingDayCriterion.MIN_CLOSED_TRADES:
            if minimum_closed_trades is None:
                return None
            return self.closed_trade_count >= int(minimum_closed_trades)
        if criterion is TradingDayCriterion.MIN_TOTAL_VOLUME:
            if minimum_volume is None or self.total_volume is None:
                return None
            return self.total_volume >= float(minimum_volume)
        if criterion is TradingDayCriterion.MIN_TOTAL_DURATION:
            if minimum_duration_seconds is None or self.total_duration_seconds is None:
                return None
            return self.total_duration_seconds >= float(minimum_duration_seconds)
        if criterion is TradingDayCriterion.MIN_REALISED_PNL:
            if minimum_pnl is None or self.net_realised_pnl is None:
                return None
            return self.net_realised_pnl >= float(minimum_pnl)
        return None

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TradingDayRecord":
        return cls(
            account=AccountKey.from_dict(payload["account"]),
            account_currency=payload["account_currency"],
            rule_day=date.fromisoformat(payload["rule_day"]),
            timezone_name=payload["timezone_name"],
            rule_day_definition=payload["rule_day_definition"],
            closed_trade_count=int(payload["closed_trade_count"]),
            total_volume=payload.get("total_volume"),
            total_duration_seconds=payload.get("total_duration_seconds"),
            gross_realised_pnl=payload.get("gross_realised_pnl", 0.0),
            net_realised_pnl=payload.get("net_realised_pnl"),
            has_closed_trade=bool(payload.get("has_closed_trade", False)),
            day_start_utc=datetime.fromisoformat(payload["day_start_utc"]),
            day_end_utc=datetime.fromisoformat(payload["day_end_utc"]),
            record_id=payload.get("record_id", ""),
        )



@dataclass(frozen=True)
class TradingDayHistory:
    """Durable, account-scoped, rule-day-scoped trading-day history.

    Counts are ALWAYS derived from the stored records by applying the rule's
    qualifying criterion. Nothing is incremented by a live loop, so a restart
    cannot lose or double-count a day.
    """

    account: AccountKey
    account_currency: str
    timezone_name: str
    rule_day_definition: str
    records: tuple[TradingDayRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(sorted(self.records, key=lambda r: (r.rule_day, r.record_id))))
        seen: set[date] = set()
        for record in self.records:
            if record.account.identity != self.account.identity:
                raise PropRuleStateError("TRADING_DAY_HISTORY_ACCOUNT_MISMATCH")
            if record.timezone_name != self.timezone_name:
                raise PropRuleStateError("TRADING_DAY_HISTORY_TIMEZONE_MISMATCH")
            if record.rule_day in seen:
                raise TradingDayRecordConflict(f"DUPLICATE_TRADING_DAY_RECORD:{record.rule_day.isoformat()}")
            seen.add(record.rule_day)

    @property
    def observed_days(self) -> tuple[date, ...]:
        return tuple(record.rule_day for record in self.records)

    def record_for(self, rule_day: date) -> TradingDayRecord | None:
        for record in self.records:
            if record.rule_day == rule_day:
                return record
        return None

    def with_record(self, record: TradingDayRecord) -> "TradingDayHistory":
        """Return a new history including ``record``.

        Re-recording an IDENTICAL day is idempotent; recording a DIFFERENT day
        under the same identity is a conflict and fails closed.
        """
        existing = self.record_for(record.rule_day)
        if existing is not None:
            if existing.record_id == record.record_id:
                return self
            raise TradingDayRecordConflict(f"TRADING_DAY_RECORD_MISMATCH:{record.rule_day.isoformat()}")
        return TradingDayHistory(
            account=self.account,
            account_currency=self.account_currency,
            timezone_name=self.timezone_name,
            rule_day_definition=self.rule_day_definition,
            records=self.records + (record,),
        )

    def count_qualifying(
        self, criterion: TradingDayCriterion, *, up_to_rule_day: date | None = None, **thresholds: Any
    ) -> int | None:
        """How many days qualify. ``None`` when the criterion is unspecified."""
        if criterion is TradingDayCriterion.UNSPECIFIED:
            return None
        total = 0
        for record in self.records:
            if up_to_rule_day is not None and record.rule_day > up_to_rule_day:
                continue
            if record.qualifies(criterion, **thresholds) is True:
                total += 1
        return total

    def last_qualifying_day(self, criterion: TradingDayCriterion, **thresholds: Any) -> date | None:
        if criterion is TradingDayCriterion.UNSPECIFIED:
            return None
        latest: date | None = None
        for record in self.records:
            if record.qualifies(criterion, **thresholds) is True:
                latest = record.rule_day if latest is None else max(latest, record.rule_day)
        return latest

    def daily_profit_series(self, *, up_to_rule_day: date | None = None) -> tuple[tuple[date, float], ...]:
        """Authoritative (rule day, net realised P&L) series for consistency.

        Days whose net P&L is unknown are OMITTED rather than assumed zero, so a
        consistency ratio is never computed on a partially-known series.
        """
        series: list[tuple[date, float]] = []
        for record in self.records:
            if up_to_rule_day is not None and record.rule_day > up_to_rule_day:
                continue
            if record.net_realised_pnl is None:
                continue
            series.append((record.rule_day, record.net_realised_pnl))
        return tuple(series)

    def to_dict(self) -> dict[str, Any]:
        return _encode(
            {
                "account": self.account.to_dict(),
                "account_currency": self.account_currency,
                "timezone_name": self.timezone_name,
                "rule_day_definition": self.rule_day_definition,
                "records": [record.to_dict() for record in self.records],
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TradingDayHistory":
        return cls(
            account=AccountKey.from_dict(payload["account"]),
            account_currency=payload["account_currency"],
            timezone_name=payload["timezone_name"],
            rule_day_definition=payload["rule_day_definition"],
            records=tuple(TradingDayRecord.from_dict(r) for r in payload.get("records") or ()),
        )



@dataclass(frozen=True)
class InactivityState:
    """Inactivity measured in the basis the rule DECLARES.

    ``basis`` is ``CALENDAR_DAYS`` or ``TRADING_DAYS``. On a trading-day basis
    weekends are NOT counted, because only RECORDED days are walked. Nothing is
    assumed: a rule that does not state its basis yields ``basis=None`` and an
    INDETERMINATE evaluation.
    """

    account: AccountKey
    account_currency: str
    timezone_name: str
    criterion: TradingDayCriterion
    last_qualifying_day: date | None
    current_rule_day: date
    inactivity_days: int | None
    basis: str | None
    completeness: str = "COMPLETE"
    detail: str = ""

    def __post_init__(self) -> None:
        if self.basis is not None and self.basis not in {"CALENDAR_DAYS", "TRADING_DAYS"}:
            raise PropRuleStateError(f"INACTIVITY_BASIS_UNKNOWN:{self.basis}")
        if self.inactivity_days is not None and self.inactivity_days < 0:
            raise PropRuleStateError("INACTIVITY_DAYS_MUST_BE_NON_NEGATIVE")

    @property
    def is_evaluable(self) -> bool:
        return (
            self.basis is not None
            and self.criterion is not TradingDayCriterion.UNSPECIFIED
            and self.inactivity_days is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})


def derive_inactivity_state(
    *,
    account: AccountKey,
    account_currency: str,
    history: TradingDayHistory,
    definition: RuleDayDefinition,
    current_rule_day: date,
    criterion: TradingDayCriterion,
    basis: str | None,
    **thresholds: Any,
) -> InactivityState:
    """Derive inactivity from durable history only.

    ``basis=None`` or an unspecified criterion yields ``inactivity_days=None``
    and ``is_evaluable == False``; nothing is defaulted.
    """
    currency = str(account_currency or "").strip().upper()
    common = dict(
        account=account, account_currency=currency,
        timezone_name=definition.timezone_name, criterion=criterion,
        current_rule_day=current_rule_day,
    )
    if basis not in {"CALENDAR_DAYS", "TRADING_DAYS"}:
        return InactivityState(
            **common, last_qualifying_day=None, inactivity_days=None, basis=None,
            completeness="INDETERMINATE", detail="INACTIVITY_BASIS_NOT_DECLARED",
        )
    if criterion is TradingDayCriterion.UNSPECIFIED:
        return InactivityState(
            **common, last_qualifying_day=None, inactivity_days=None, basis=basis,
            completeness="INDETERMINATE",
            detail="INACTIVITY_QUALIFYING_CRITERION_UNSPECIFIED",
        )
    last = history.last_qualifying_day(criterion, **thresholds)
    if basis == "TRADING_DAYS":
        if last is None:
            # Measured over RECORDED days: zero when none qualify, which is
            # still indeterminate when no days were recorded at all.
            return InactivityState(
                **common, last_qualifying_day=None, inactivity_days=0, basis=basis,
                completeness="COMPLETE" if history.records else "INDETERMINATE",
                detail="NO_QUALIFYING_TRADING_DAY_RECORDED",
            )
        walked = [r.rule_day for r in history.records if last <= r.rule_day <= current_rule_day]
        inactive = max(0, len(walked) - 1)
    else:
        if last is None:
            return InactivityState(
                **common, last_qualifying_day=None, inactivity_days=None, basis=basis,
                completeness="INDETERMINATE", detail="NO_QUALIFYING_DAY_RECORDED",
            )
        inactive = max(0, (current_rule_day - last).days)
    return InactivityState(
        **common, last_qualifying_day=last, inactivity_days=inactive, basis=basis,
        completeness="COMPLETE", detail="DERIVED_FROM_DURABLE_HISTORY",
    )


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# EXTERNAL SOURCES Ã¢â‚¬â€ declared protocols, never fabricated data
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


@runtime_checkable
class EconomicCalendarSource(Protocol):
    """A real economic-calendar provider.

    Block 3B ships NO implementation. News evaluation therefore stays
    INDETERMINATE until a genuine provider is supplied; no timestamp is ever
    hardcoded and no synthetic calendar is accepted as evidence.
    """

    def events_in_window(self, start_utc: datetime, end_utc: datetime) -> Sequence[Any]:
        """Return the events in ``[start_utc, end_utc)``, or raise."""
        ...


@runtime_checkable
class MarketSessionSource(Protocol):
    """A real exchange/session calendar provider.

    Required before weekend/overnight hold state can be evaluated. Market close
    times are NEVER inferred from a symbol name.
    """

    def is_market_open(self, symbol: str, at_utc: datetime) -> bool | None:
        """``True``/``False``, or ``None`` when the calendar cannot answer."""
        ...



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# ACCOUNT EVALUATION STATE Ã¢â‚¬â€ the typed state a rule is evaluated against
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


class StateStatus(str, Enum):
    """Overall quality of the assembled evaluation state."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    #: A required anchor or ledger is absent, so dependent rules are INDETERMINATE.
    UNAVAILABLE = "UNAVAILABLE"
    #: Two sources disagree. Nothing is evaluated against it.
    CONFLICT = "CONFLICT"
    STALE = "STALE"


@dataclass(frozen=True)
class AccountEvaluationState:
    """Typed, account-scoped state for evaluating canonical prop rules.

    NOT A DICT. Every field is named and typed, and a field that could not be
    established is ``None`` and listed in ``unavailable_fields`` -- never a
    guessed zero. A rule reading a ``None`` field must return INDETERMINATE.

    LINEAGE
    -------
    ``rule_pack_id`` and ``evaluation_id`` bind the state to the exact pack and
    the exact evaluation it was assembled for, so a result can always explain
    which state produced it.
    """

    # Ã¢â€â‚¬Ã¢â€â‚¬ identity Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    account: AccountKey
    account_currency: str
    # Ã¢â€â‚¬Ã¢â€â‚¬ lineage Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    rule_pack_id: str | None
    evaluation_id: str | None
    observed_at_utc: datetime
    observed_at_utc_ms: int
    # Ã¢â€â‚¬Ã¢â€â‚¬ rule day Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    rule_day: date
    rule_timezone: str
    rule_day_definition: str
    # Ã¢â€â‚¬Ã¢â€â‚¬ account references Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    initial_anchor: InitialAccountAnchor | None = None
    initial_balance: float | None = None
    initial_equity: float | None = None
    daily_anchor: DailyAccountAnchor | None = None
    start_of_day_balance: float | None = None
    start_of_day_equity: float | None = None
    current_balance: float | None = None
    current_equity: float | None = None
    high_water: HighWaterState | None = None
    high_water_balance: float | None = None
    high_water_equity: float | None = None
    # Ã¢â€â‚¬Ã¢â€â‚¬ P&L Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    daily_ledger: DailyPnlLedgerEntry | None = None
    realised_pnl_today: float | None = None
    floating_pnl: float | None = None
    commissions_today: float | None = None
    swap_today: float | None = None
    fees_today: float | None = None
    net_daily_pnl: float | None = None
    # Ã¢â€â‚¬Ã¢â€â‚¬ trading history Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    trading_day_history: TradingDayHistory | None = None
    last_trade_date: date | None = None
    # Ã¢â€â‚¬Ã¢â€â‚¬ position / risk (from exact Block 2 telemetry) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    total_open_risk: float | None = None
    position_count: int | None = None
    open_position_tickets: tuple[int, ...] = ()
    largest_position_risk: float | None = None
    correlated_risk: float | None = None
    largest_symbol_risk: float | None = None
    largest_symbol: str | None = None
    directional_risk: float | None = None
    largest_direction: str | None = None
    largest_lot_size: float | None = None
    # Ã¢â€â‚¬Ã¢â€â‚¬ Block 2 lineage Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    account_snapshot_id: str | None = None
    open_risk_snapshot_id: str | None = None
    portfolio_exposure_id: str | None = None
    # Ã¢â€â‚¬Ã¢â€â‚¬ quality Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    status: StateStatus = StateStatus.UNAVAILABLE
    unavailable_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()
    source_error: str | None = None
    stale: bool = False
    conflict_detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_currency", str(self.account_currency or "").strip().upper())
        object.__setattr__(self, "observed_at_utc", _require_aware(self.observed_at_utc, "STATE_OBSERVED_AT"))
        object.__setattr__(self, "unavailable_fields", tuple(sorted(self.unavailable_fields)))
        object.__setattr__(self, "invalid_fields", tuple(sorted(self.invalid_fields)))
        object.__setattr__(self, "open_position_tickets", tuple(int(t) for t in self.open_position_tickets))
        if not isinstance(self.rule_day, date):
            raise PropRuleStateError("STATE_REQUIRES_RULE_DAY")
        if self.status is StateStatus.UNAVAILABLE and not self.unavailable_fields:
            object.__setattr__(self, "unavailable_fields", ("STATE_ASSEMBLY_UNKNOWN",))

    def has(self, field_name: str) -> bool:
        """Whether a named field holds an established value."""
        return getattr(self, field_name, None) is not None

    def is_evaluable_for(self, field_names: Sequence[str]) -> bool:
        return all(self.has(name) for name in field_names)

    @property
    def is_usable(self) -> bool:
        """Whether any evaluation may be attempted against this state at all."""
        return self.status is not StateStatus.CONFLICT

    def to_dict(self) -> dict[str, Any]:
        return _encode({f.name: getattr(self, f.name) for f in dataclass_fields(self)})



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# LEDGER PROJECTION Ã¢â‚¬â€ deterministic, idempotent, late-arrival safe
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def project_daily_ledger(
    *,
    account: AccountKey,
    account_currency: str,
    definition: RuleDayDefinition,
    rule_day: date,
    events: Sequence[ClosedTradeEvent],
    rule_pack_id: str | None = None,
) -> DailyPnlLedgerEntry:
    """Deterministically project the ledger for ONE rule day.

    IDEMPOTENT
    ----------
    Deduplication is by ``event_id`` (which includes the account), so passing the
    same event twice -- or two identical copies from two ingestion passes --
    yields exactly the same entry.

    LATE ARRIVALS
    -------------
    Events are selected by RULE DAY, never by arrival time, so a trade ingested
    after the rule day closed still lands on its own rule day and never on the
    arrival date.

    COMPONENT COMPLETENESS
    ----------------------
    A component is summed only when EVERY contributing event supplied it. One
    event with unknown commission makes the day's commission UNKNOWN, not zero.
    """
    identity = account.identity
    seen: set[str] = set()
    in_day: list[ClosedTradeEvent] = []
    for event in events:
        if event.account.identity != identity or event.event_id in seen:
            continue
        if definition.rule_day_for(event.closed_at_utc) != rule_day:
            continue
        seen.add(event.event_id)
        in_day.append(event)
    ordered = sorted(in_day, key=lambda e: (e.closed_at_utc, e.event_id))

    gross = 0.0
    commission_values: list[float] = []
    swap_values: list[float] = []
    fees_values: list[float] = []
    volumes: list[float] = []
    unknown: set[str] = set()
    full_closes = 0
    partial_closes = 0
    for event in ordered:
        gross += event.gross_realised_pnl
        for name, bucket in (
            ("commission", commission_values), ("swap", swap_values), ("fees", fees_values),
        ):
            value = getattr(event, name)
            if value is None:
                unknown.add(name)
            else:
                bucket.append(value)
        if event.volume is None:
            unknown.add("volume")
        else:
            volumes.append(event.volume)
        if event.close_kind is CloseEventKind.FULL_CLOSE:
            full_closes += 1
        elif event.close_kind is CloseEventKind.PARTIAL_CLOSE:
            partial_closes += 1

    commission_total: float | None = None if "commission" in unknown else round(sum(commission_values), 8) + 0.0
    swap_total: float | None = None if "swap" in unknown else round(sum(swap_values), 8) + 0.0
    fees_total: float | None = None if "fees" in unknown else round(sum(fees_values), 8) + 0.0
    volume_total: float | None = None if "volume" in unknown else round(sum(volumes), 8) + 0.0

    unknown_labels = set(unknown)
    net: float | None = None
    if commission_total is not None and swap_total is not None and fees_total is not None:
        net = round(gross + commission_total + swap_total + fees_total, 8) + 0.0
    else:
        unknown_labels.add("net_realised_pnl")

    return DailyPnlLedgerEntry(
        account=account,
        account_currency=account_currency,
        rule_day=rule_day,
        timezone_name=definition.timezone_name,
        rule_day_definition=definition.key(),
        gross_realised_pnl=round(gross, 8) + 0.0,
        commission=commission_total,
        swap=swap_total,
        fees=fees_total,
        net_realised_pnl=net,
        trade_count=len(ordered),
        full_close_count=full_closes,
        partial_close_count=partial_closes,
        total_volume=volume_total,
        source_trade_ids=tuple(e.source_trade_id for e in ordered),
        event_ids=tuple(e.event_id for e in ordered),
        symbols=tuple(sorted({e.symbol for e in ordered if e.symbol})),
        first_close_at_utc=ordered[0].closed_at_utc if ordered else None,
        last_close_at_utc=ordered[-1].closed_at_utc if ordered else None,
        unknown_components=tuple(sorted(unknown_labels)),
        rule_pack_id=rule_pack_id,
    )



def project_trading_day_record(
    *,
    account: AccountKey,
    account_currency: str,
    definition: RuleDayDefinition,
    rule_day: date,
    events: Sequence[ClosedTradeEvent],
) -> TradingDayRecord:
    """Project one rule day's trading evidence from close events."""
    identity = account.identity
    in_day = sorted(
        (
            e for e in events
            if e.account.identity == identity and definition.rule_day_for(e.closed_at_utc) == rule_day
        ),
        key=lambda e: (e.closed_at_utc, e.event_id),
    )
    durations = [e.duration_seconds for e in in_day if e.duration_seconds is not None]
    durations_unknown = any(e.duration_seconds is None for e in in_day)
    volumes = [e.volume for e in in_day if e.volume is not None]
    volumes_unknown = any(e.volume is None for e in in_day)
    ledger = project_daily_ledger(
        account=account, account_currency=account_currency, definition=definition,
        rule_day=rule_day, events=events,
    )
    return TradingDayRecord(
        account=account,
        account_currency=account_currency,
        rule_day=rule_day,
        timezone_name=definition.timezone_name,
        rule_day_definition=definition.key(),
        closed_trade_count=len(in_day),
        total_volume=None if volumes_unknown else round(sum(volumes), 8) + 0.0,
        total_duration_seconds=None if durations_unknown else round(sum(durations), 8) + 0.0,
        gross_realised_pnl=ledger.gross_realised_pnl,
        net_realised_pnl=ledger.net_realised_pnl,
        has_closed_trade=bool(in_day),
        day_start_utc=definition.day_start_utc(rule_day),
        day_end_utc=definition.day_end_utc(rule_day),
    )


def build_trading_day_history(
    *,
    account: AccountKey,
    account_currency: str,
    definition: RuleDayDefinition,
    events: Sequence[ClosedTradeEvent],
) -> TradingDayHistory:
    """Project the whole trading-day history from close events.

    Days are enumerated from the EVIDENCE, not from a wall clock, so no phantom
    day is ever inserted.
    """
    identity = account.identity
    days = sorted({
        definition.rule_day_for(e.closed_at_utc)
        for e in events
        if e.account.identity == identity
    })
    return TradingDayHistory(
        account=account,
        account_currency=account_currency,
        timezone_name=definition.timezone_name,
        rule_day_definition=definition.key(),
        records=tuple(
            project_trading_day_record(
                account=account, account_currency=account_currency, definition=definition,
                rule_day=day, events=events,
            )
            for day in days
        ),
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# STATE ASSEMBLY Ã¢â‚¬â€ derive the typed evaluation state from injected evidence
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def build_account_evaluation_state(
    *,
    account: AccountKey,
    account_currency: str,
    observed_at_utc: datetime,
    definition: RuleDayDefinition,
    initial_anchor: InitialAccountAnchor | None = None,
    daily_anchor: DailyAccountAnchor | None = None,
    account_snapshot: Any | None = None,
    open_risk: Any | None = None,
    position_set: Any | None = None,
    portfolio: Any | None = None,
    close_events: Sequence[ClosedTradeEvent] = (),
    rule_pack_id: str | None = None,
    evaluation_id: str | None = None,
    high_water: HighWaterState | None = None,
    trading_day_history: TradingDayHistory | None = None,
    unavailable_fields: Sequence[str] = (),
    invalid_fields: Sequence[str] = (),
    source_error: str | None = None,
    stale: bool = False,
) -> AccountEvaluationState:
    """Assemble the typed evaluation state at an EXPLICIT instant.

    EVIDENCE SOURCES
    ----------------
    * ``account_snapshot``  -- Block 2A. Floating P&L comes from the ACCOUNT-level
      broker value; it is never re-derived by summing position rows.
    * ``open_risk``         -- Block 2B, used only when it publishes an
      AUTHORITATIVE total (``risk_complete``). A partial floor is never presented
      as the total.
    * ``portfolio``         -- Block 2C, for symbol / directional / correlated risk.
    * ``close_events``      -- Block 3B's own authoritative closed-trade evidence.

    NOTHING IS INVENTED. A value the evidence does not establish stays ``None``
    and is reported in ``unavailable_fields``.
    """
    moment = _require_aware(observed_at_utc, "STATE_OBSERVED_AT")
    rule_day = definition.rule_day_for(moment)
    currency = str(account_currency or "").strip().upper()
    missing: set[str] = set(unavailable_fields)
    invalid: set[str] = set(invalid_fields)

    history = trading_day_history or build_trading_day_history(
        account=account, account_currency=currency, definition=definition, events=close_events,
    )
    day_record = history.record_for(rule_day)
    ledger = project_daily_ledger(
        account=account, account_currency=currency, definition=definition,
        rule_day=rule_day, events=close_events, rule_pack_id=rule_pack_id,
    )


    current_balance: float | None = None
    current_equity: float | None = None
    floating: float | None = None
    account_snapshot_id: str | None = None
    if account_snapshot is not None:
        account_snapshot_id = str(getattr(account_snapshot, "snapshot_id", "") or "") or None
        if str(getattr(account_snapshot, "account_id", "")) != account.account_id:
            invalid.add("account_snapshot.account_id")
        current_balance = _require_finite(getattr(account_snapshot, "balance", None), "SNAPSHOT_BALANCE")
        current_equity = _require_finite(getattr(account_snapshot, "equity", None), "SNAPSHOT_EQUITY")
        floating = _require_finite(getattr(account_snapshot, "floating_pnl", None), "SNAPSHOT_FLOATING")
        for name, value in (("current_balance", current_balance), ("current_equity", current_equity),
                            ("floating_pnl", floating)):
            if value is None:
                missing.add(name)
    else:
        missing.update({"current_balance", "current_equity", "floating_pnl", "account_snapshot_id"})

    total_open_risk: float | None = None
    open_risk_id: str | None = None
    position_count: int | None = None
    tickets: tuple[int, ...] = ()
    largest_position_risk: float | None = None
    largest_lot: float | None = None
    if open_risk is not None:
        open_risk_id = str(getattr(open_risk, "open_risk_id", "") or "") or None
        tickets = tuple(int(t) for t in (getattr(open_risk, "position_tickets", ()) or ()))
        risks = [r for r in (getattr(open_risk, "position_risks", ()) or ()) if r is not None]
        largest_position_risk = max(risks) if risks else None
        if bool(getattr(open_risk, "risk_complete", False)) and getattr(open_risk, "total_open_risk", None) is not None:
            total_open_risk = _require_finite(getattr(open_risk, "total_open_risk", None), "OPEN_RISK_TOTAL")
            position_count = int(getattr(open_risk, "open_position_count", 0) or 0)
        else:
            # A partial floor is not a total. Reported as unavailable, never as 0.
            missing.update({"total_open_risk", "position_count"})
    else:
        missing.update({"total_open_risk", "position_count", "open_risk_snapshot_id"})

    correlated: float | None = None
    largest_symbol_risk: float | None = None
    largest_symbol: str | None = None
    directional: float | None = None
    largest_direction: str | None = None
    portfolio_id: str | None = None
    if portfolio is not None:
        portfolio_id = str(getattr(portfolio, "portfolio_exposure_id", "") or "") or None
        largest_symbol_risk = _require_finite(getattr(portfolio, "largest_symbol_risk", None), "SYMBOL_RISK")
        largest_symbol = getattr(portfolio, "largest_symbol", None) or None
        directional = _require_finite(getattr(portfolio, "largest_direction_risk", None), "DIRECTIONAL_RISK")
        raw_direction = getattr(portfolio, "largest_direction", None)
        largest_direction = getattr(raw_direction, "value", raw_direction) or None
        if bool(getattr(portfolio, "correlation_complete", False)):
            correlated = _require_finite(getattr(portfolio, "max_cluster_risk", None), "CORRELATED_RISK")
        else:
            missing.add("correlated_risk")
    else:
        missing.update({"correlated_risk", "largest_symbol_risk", "directional_risk", "portfolio_exposure_id"})

    if position_set is not None and not bool(getattr(position_set, "position_set_complete", False)):
        missing.add("open_position_tickets")

    for snapshot in (getattr(position_set, "positions", ()) or ()):
        volume = _require_finite(getattr(snapshot, "volume", None), "POSITION_VOLUME")
        if volume is not None:
            largest_lot = volume if largest_lot is None else max(largest_lot, volume)

    if initial_anchor is not None:
        if initial_anchor.account.identity != account.identity:
            raise InitialAnchorConflict("INITIAL_ANCHOR_ACCOUNT_MISMATCH")
        if initial_anchor.account_currency != currency:
            invalid.add("initial_anchor.account_currency")
    else:
        missing.update({"initial_anchor", "initial_balance", "initial_equity"})

    if daily_anchor is not None:
        if daily_anchor.account.identity != account.identity:
            invalid.add("daily_anchor.account")
        if daily_anchor.rule_day != rule_day or daily_anchor.timezone_name != definition.timezone_name:
            invalid.add("daily_anchor.rule_day")
    else:
        missing.update({"daily_anchor", "start_of_day_balance", "start_of_day_equity"})

    if high_water is not None and high_water.account.identity != account.identity:
        invalid.add("high_water.account")

    if invalid:
        status = StateStatus.CONFLICT
    elif not missing:
        status = StateStatus.COMPLETE
    elif ledger.is_complete and initial_anchor is not None and account_snapshot is not None:
        status = StateStatus.PARTIAL
    else:
        status = StateStatus.UNAVAILABLE
    if stale and status is StateStatus.COMPLETE:
        status = StateStatus.STALE


    return AccountEvaluationState(
        account=account,
        account_currency=currency,
        rule_pack_id=rule_pack_id,
        evaluation_id=evaluation_id,
        observed_at_utc=moment,
        observed_at_utc_ms=to_epoch_ms(moment),
        rule_day=rule_day,
        rule_timezone=definition.timezone_name,
        rule_day_definition=definition.key(),
        initial_anchor=initial_anchor,
        initial_balance=initial_anchor.initial_balance if initial_anchor else None,
        initial_equity=initial_anchor.initial_equity if initial_anchor else None,
        daily_anchor=daily_anchor,
        start_of_day_balance=daily_anchor.start_of_day_balance if daily_anchor else None,
        start_of_day_equity=daily_anchor.start_of_day_equity if daily_anchor else None,
        current_balance=current_balance,
        current_equity=current_equity,
        high_water=high_water,
        high_water_balance=high_water.high_water_balance if high_water else None,
        high_water_equity=high_water.high_water_equity if high_water else None,
        daily_ledger=ledger,
        realised_pnl_today=ledger.net_realised_pnl,
        floating_pnl=floating,
        commissions_today=ledger.commission,
        swap_today=ledger.swap,
        fees_today=ledger.fees,
        net_daily_pnl=ledger.net_realised_pnl,
        trading_day_history=history,
        last_trade_date=rule_day if day_record is not None and day_record.has_closed_trade else None,
        total_open_risk=total_open_risk,
        position_count=position_count,
        open_position_tickets=tickets,
        largest_position_risk=largest_position_risk,
        correlated_risk=correlated,
        largest_symbol_risk=largest_symbol_risk,
        largest_symbol=largest_symbol,
        directional_risk=directional,
        largest_direction=largest_direction,
        largest_lot_size=largest_lot,
        account_snapshot_id=account_snapshot_id,
        open_risk_snapshot_id=open_risk_id,
        portfolio_exposure_id=portfolio_id,
        status=status,
        unavailable_fields=tuple(sorted(missing)),
        invalid_fields=tuple(sorted(invalid)),
        source_error=source_error,
        stale=stale,
        conflict_detail=";".join(sorted(invalid)) or None,
    )


# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# HISTORICAL RULE PACK SELECTION Ã¢â‚¬â€ exact binding, fail closed
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


def rules_effective_at(
    store: Any,
    provider: str,
    program: str,
    phase: Any,
    account_size: int,
    as_of: datetime,
    *,
    currency: str | None = None,
    platform: str | None = None,
) -> Any:
    """The ONE rule pack in force for this identity at ``as_of``.

    Delegates to the Block 3A :class:`RulePackStore` and re-asserts the
    fail-closed guarantees at the 3B boundary:

    * no pack matches -> :class:`NoRulePackInForce`
    * several match   -> :class:`AmbiguousRulePackInForce`
    * a match exists but is not VALID -> raised by the pack's own assert

    There is deliberately NO nearest-match, previous-version or newest-version
    fallback. A historical evaluation binds to the exact pack in force at the
    time, or it does not evaluate at all.
    """
    from core.risk.prop_rule_pack import RulePackNotFound, AmbiguousRulePackError, RulePackStore

    if not isinstance(store, RulePackStore):
        raise PropRuleStateError("RULE_PACK_STORE_REQUIRED")
    moment = _require_aware(as_of, "AS_OF")
    try:
        return store.find_rule_pack(
            provider, program, phase, account_size, moment,
            currency=currency, platform=platform,
        )
    except RulePackNotFound as exc:
        raise NoRulePackInForce(str(exc)) from exc
    except AmbiguousRulePackError as exc:
        raise AmbiguousRulePackInForce(str(exc)) from exc


def rule_pack_timeline(
    store: Any,
    provider: str,
    program: str,
    phase: Any,
    account_size: int,
) -> tuple[tuple[datetime | None, datetime | None, Any], ...]:
    """Every version of one identity as ``(from, to, pack)``, oldest first.

    Used by the historical replayer to bind each instant to the pack that was in
    force then, so adjacent non-overlapping windows replay with no retroactive
    application.
    """
    from core.risk.prop_rule_pack import RulePackStore

    if not isinstance(store, RulePackStore):
        raise PropRuleStateError("RULE_PACK_STORE_REQUIRED")
    return tuple(
        (p.identity.effective_from, p.identity.effective_to, p)
        for p in store.list_versions(provider, program, phase, account_size)
    )



# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â
# DURABLE STATE STORE Ã¢â‚¬â€ append-only, reconstructable, process-memory free
# Ã¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢ÂÃ¢â€¢Â


#: The five operational state families. Deliberately NOT Production V1 datasets.
STATE_FAMILIES: tuple[str, ...] = (
    "initial_anchor",
    "daily_anchor",
    "trade_event",
    "trading_day",
    "high_water",
)


class PropRuleStateStore:
    """Durable, account-isolated, restart-reconstructable state store.

    MUTABILITY MODEL (Block 3B decision, explicit)
    ---------------------------------------------
    * IMMUTABLE, written once: the initial anchor, each daily anchor, each closed
      trade event. Re-writing identical content is a no-op; re-writing DIFFERENT
      content under the same identity raises rather than overwriting evidence.
    * DERIVED / MONOTONE: the high-water projection. It advances and never
      decreases, and is always RECOMPUTED from the recorded observations.
    * APPEND-ONLY + PROJECTED: the daily P&L ledger and the trading-day history
      are projections of the close events, so a late-arriving trade updates the
      correct rule day without destroying anything.

    RESTART
    -------
    Nothing depends on process memory: :meth:`rebuild` reconstructs every family
    by re-reading the durable records, producing identical projections.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._initial_anchors: dict[tuple[str, str, str, int], InitialAccountAnchor] = {}
        self._daily_anchors: dict[tuple[tuple[str, str, str, int], str, date], DailyAccountAnchor] = {}
        self._events: dict[tuple[tuple[str, str, str, int], str, str], ClosedTradeEvent] = {}
        self._trading_days: dict[tuple[tuple[str, str, str, int], str], TradingDayHistory] = {}
        self._high_water: dict[tuple[tuple[str, str, str, int], str], HighWaterState] = {}
        self._hw_obs: dict[tuple[tuple[str, str, str, int], str], list[tuple[str, float, float, datetime]]] = {}
        if self._base_dir is not None:
            self.rebuild()

    # Ã¢â€â‚¬Ã¢â€â‚¬ durable paths Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    def _path(self, family: str, account: AccountKey) -> Path:
        if family not in STATE_FAMILIES:
            raise StateStoreError(f"UNKNOWN_STATE_FAMILY:{family}")
        assert self._base_dir is not None
        return self._base_dir / family / f"{account.slug}.jsonl"

    def _append(self, family: str, account: AccountKey, payload: Mapping[str, Any]) -> None:
        if self._base_dir is None:
            return
        path = self._path(family, account)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:  # pragma: no cover - filesystem failure path
            raise StateStoreError(f"STATE_APPEND_FAILED:{family}:{exc}") from exc

    @staticmethod
    def _iter_records(path: Path) -> list[Mapping[str, Any]]:
        if not path.exists():
            return []
        rows: list[Mapping[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if text:
                    rows.append(json.loads(text))
        return rows

    def _family_files(self, family: str) -> list[Path]:
        assert self._base_dir is not None
        directory = self._base_dir / family
        return sorted(directory.glob("*.jsonl")) if directory.exists() else []


    # Ã¢â€â‚¬Ã¢â€â‚¬ initial anchor Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    def record_initial_anchor(self, anchor: InitialAccountAnchor) -> InitialAccountAnchor:
        """Establish the initial anchor ONCE.

        Re-recording the SAME values is idempotent and returns the existing
        anchor. Recording DIFFERENT values for the same account raises
        :class:`InitialAnchorConflict` -- the "initial" value can never be
        replaced by a later balance, and it is never chosen by timestamp.
        """
        existing = self._initial_anchors.get(anchor.account.identity)
        if existing is not None:
            if existing.same_values_as(anchor):
                return existing
            raise InitialAnchorConflict(
                f"INITIAL_ANCHOR_CONFLICT:{anchor.account.account_id}:"
                f"existing={existing.anchor_id}:incoming={anchor.anchor_id}"
            )
        self._initial_anchors[anchor.account.identity] = anchor
        self._append("initial_anchor", anchor.account, anchor.to_dict())
        return anchor

    def initial_anchor_for(self, account: AccountKey) -> InitialAccountAnchor | None:
        return self._initial_anchors.get(account.identity)

    # Ã¢â€â‚¬Ã¢â€â‚¬ daily anchor Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    def record_daily_anchor(self, anchor: DailyAccountAnchor) -> DailyAccountAnchor:
        """Record the ONE start-of-day anchor for a (account, tz, rule day).

        Identical re-recording is idempotent. A different value for the same key
        raises, so a day's opening reference is never silently rewritten.
        """
        key = (anchor.account.identity, anchor.timezone_name, anchor.rule_day)
        existing = self._daily_anchors.get(key)
        if existing is not None:
            if existing.same_values_as(anchor):
                return existing
            raise InitialAnchorConflict(
                f"DAILY_ANCHOR_CONFLICT:{anchor.account.account_id}:"
                f"{anchor.timezone_name}:{anchor.rule_day.isoformat()}"
            )
        self._daily_anchors[key] = anchor
        self._append("daily_anchor", anchor.account, anchor.to_dict())
        return anchor

    def daily_anchor_for(
        self, account: AccountKey, timezone_name: str, rule_day: date
    ) -> DailyAccountAnchor | None:
        return self._daily_anchors.get((account.identity, timezone_name, rule_day))

    # Ã¢â€â‚¬Ã¢â€â‚¬ closed trade events Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    def record_close_event(self, event: ClosedTradeEvent) -> ClosedTradeEvent:
        """Append ONE closed-trade event, idempotently.

        The same event twice counts once. The same event id with DIFFERENT
        economics is a source conflict, not an update. Two accounts reporting the
        same ticket stay distinct because the account is part of the event id.
        """
        key = (event.account.identity, event.source, event.event_id)
        existing = self._events.get(key)
        if existing is not None:
            if existing.same_values_as(event):
                return existing
            raise ConflictingDuplicateEvent(
                f"DUPLICATE_EVENT_CONFLICT:{event.event_id}:{event.account.account_id}"
            )
        self._events[key] = event
        self._append("trade_event", event.account, event.to_dict())
        return event

    def close_events_for(
        self,
        account: AccountKey,
        definition: RuleDayDefinition | None = None,
        *,
        rule_day: date | None = None,
    ) -> tuple[ClosedTradeEvent, ...]:
        """All events for one account, optionally scoped to one rule day."""
        events = [e for e in self._events.values() if e.account.identity == account.identity]
        if definition is not None and rule_day is not None:
            events = [e for e in events if definition.rule_day_for(e.closed_at_utc) == rule_day]
        return tuple(sorted(events, key=lambda e: (e.closed_at_utc, e.event_id)))


    # Ã¢â€â‚¬Ã¢â€â‚¬ high water Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    def record_high_water_observation(
        self,
        *,
        account: AccountKey,
        account_currency: str,
        balance: float | None,
        equity: float | None,
        observed_at_utc: datetime,
        source_snapshot_id: str,
    ) -> HighWaterState:
        """Record one observation and advance the monotone projection.

        The observation is the durable evidence; the projection is always
        RECOMPUTED from ALL recorded observations, so replay order cannot change
        the result and a lower value can never reduce a mark.
        """
        moment = _require_aware(observed_at_utc, "HIGH_WATER_OBSERVED_AT")
        snapshot_id = str(source_snapshot_id or "").strip()
        if not snapshot_id:
            raise PropRuleStateError("HIGH_WATER_REQUIRES_SOURCE_SNAPSHOT_ID")
        key = (account.identity, account_currency)
        observations = self._hw_obs.setdefault(key, [])
        # Block 3D REPAIR (minimal): ONE snapshot is ONE observation. The same
        # source snapshot re-evaluated (for example after a restart replays the
        # cycle) must not inflate the observation count or the last-seen time.
        if any(existing[0] == snapshot_id for existing in observations):
            return self.high_water_for(account, account_currency)
        observations.append(
            (
                snapshot_id,
                _require_finite(balance, "HIGH_WATER_BALANCE") or 0.0,
                _require_finite(equity, "HIGH_WATER_EQUITY") or 0.0,
                moment,
            )
        )
        self._append(
            "high_water", account,
            {
                "schema_version": "prop_rule_high_water_obs_v1",
                "record_kind": "HIGH_WATER_OBSERVATION",
                "account": account.to_dict(),
                "account_currency": account_currency,
                "source_snapshot_id": snapshot_id,
                "balance": self._hw_obs[key][-1][1],
                "equity": self._hw_obs[key][-1][2],
                "observed_at_utc": moment.isoformat(),
            },
        )
        # Block 3D REPAIR (minimal): the MONOTONE projection must also be
        # published to the read cache. Previously only the freshly computed
        # projection was returned, while ``self._high_water`` kept the value
        # cached by the first ``high_water_for`` call. For the whole lifetime of
        # a running process the high-water therefore never advanced past its
        # FIRST observation, so a TRAILING drawdown floor stayed pinned to the
        # opening equity and silently behaved like a STATIC drawdown until a
        # restart recomputed it. Recording now republishes the projection.
        self._high_water[key] = self._project_high_water(account, account_currency)
        return self._high_water[key]

    def _project_high_water(self, account: AccountKey, account_currency: str) -> HighWaterState:
        key = (account.identity, account_currency)
        state = HighWaterState.empty(account, account_currency)
        for snapshot_id, balance, equity, moment in sorted(
            self._hw_obs.get(key, []), key=lambda o: (o[3], o[0])
        ):
            state = state.advance(
                balance=balance or None, equity=equity or None,
                observed_at_utc=moment, source_snapshot_id=snapshot_id,
            )
        return state

    def high_water_for(self, account: AccountKey, account_currency: str) -> HighWaterState:
        key = (account.identity, account_currency)
        if key not in self._high_water:
            self._high_water[key] = self._project_high_water(account, account_currency)
        return self._high_water[key]

    # Ã¢â€â‚¬Ã¢â€â‚¬ projections Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    def daily_ledger_for(
        self,
        *,
        account: AccountKey,
        account_currency: str,
        definition: RuleDayDefinition,
        rule_day: date,
        rule_pack_id: str | None = None,
    ) -> DailyPnlLedgerEntry:
        return project_daily_ledger(
            account=account, account_currency=account_currency, definition=definition,
            rule_day=rule_day, events=self.close_events_for(account, definition, rule_day=rule_day),
            rule_pack_id=rule_pack_id,
        )

    def trading_day_history_for(
        self, *, account: AccountKey, account_currency: str, definition: RuleDayDefinition
    ) -> TradingDayHistory:
        history = build_trading_day_history(
            account=account, account_currency=account_currency,
            definition=definition, events=self.close_events_for(account),
        )
        self._trading_days[(account.identity, account_currency)] = history
        return history

    def state_lineage(self, account: AccountKey) -> dict[str, Any]:
        """Exact lineage of one account's state, for evaluation identity."""
        anchor = self._initial_anchors.get(account.identity)
        events = sorted(
            (
                event for event in self._events.values()
                if event.account.identity == account.identity
            ),
            key=lambda e: (e.closed_at_utc, e.event_id),
        )
        return {
            "initial_anchor_id": anchor.anchor_id if anchor else None,
            "event_ids": [event.event_id for event in events],
            "event_count": len(events),
        }


    # Ã¢â€â‚¬Ã¢â€â‚¬ reconstruction Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    def rebuild(self) -> None:
        """Reconstruct ALL state from durable records after a restart.

        Deterministic and order-independent: every aggregate is a pure function
        of the recorded evidence, so the rebuilt state is identical whether the
        process just started or has been running for days.
        """
        if self._base_dir is None:
            return
        self._initial_anchors.clear()
        self._daily_anchors.clear()
        self._events.clear()
        self._trading_days.clear()
        self._high_water.clear()
        self._hw_obs.clear()

        for path in self._family_files("initial_anchor"):
            for row in self._iter_records(path):
                anchor = InitialAccountAnchor.from_dict(row)
                existing = self._initial_anchors.get(anchor.account.identity)
                if existing is not None and not existing.same_values_as(anchor):
                    raise InitialAnchorConflict(
                        f"INITIAL_ANCHOR_CONFLICT_ON_REBUILD:{anchor.account.account_id}"
                    )
                self._initial_anchors[anchor.account.identity] = anchor

        for path in self._family_files("daily_anchor"):
            for row in self._iter_records(path):
                anchor = DailyAccountAnchor.from_dict(row)
                key = (anchor.account.identity, anchor.timezone_name, anchor.rule_day)
                existing = self._daily_anchors.get(key)
                if existing is not None and not existing.same_values_as(anchor):
                    raise InitialAnchorConflict(
                        f"DAILY_ANCHOR_CONFLICT_ON_REBUILD:{anchor.account.account_id}"
                    )
                self._daily_anchors[key] = anchor

        for path in self._family_files("trade_event"):
            for row in self._iter_records(path):
                event = ClosedTradeEvent.from_dict(row)
                key = (event.account.identity, event.source, event.event_id)
                existing = self._events.get(key)
                if existing is not None and not existing.same_values_as(event):
                    raise ConflictingDuplicateEvent(
                        f"DUPLICATE_EVENT_CONFLICT_ON_REBUILD:{event.event_id}"
                    )
                self._events[key] = event

        for path in self._family_files("high_water"):
            for row in self._iter_records(path):
                account = AccountKey.from_dict(row["account"])
                currency = str(row.get("account_currency") or "").strip().upper()
                self._hw_obs.setdefault((account.identity, currency), []).append(
                    (
                        str(row["source_snapshot_id"]),
                        float(row.get("balance") or 0.0),
                        float(row.get("equity") or 0.0),
                        _require_aware(
                            datetime.fromisoformat(row["observed_at_utc"]), "HIGH_WATER_OBSERVED_AT"
                        ),
                    )
                )
        for (identity, currency) in list(self._hw_obs):
            account = AccountKey(
                account_id=identity[0], broker=identity[1], server=identity[2], login=identity[3]
            )
            self._high_water[(identity, currency)] = self._project_high_water(account, currency)

    def account_ids(self) -> tuple[str, ...]:
        return tuple(sorted({identity[0] for identity in self._initial_anchors}))
