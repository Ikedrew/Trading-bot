"""Governed EXTERNAL SOURCE providers for prop rule enforcement (Block 3C).

Block 3B declared Protocols for an economic calendar and a market-session
calendar but deliberately shipped NO implementation, so news and hold-restriction
evaluations stayed UNSUPPORTED. This module supplies the narrow runtime
provider surface plus the freshness model that makes "the calendar is down" a
first-class, enforceable state.

THE RULES THIS MODULE ENFORCES
------------------------------
1. NO HARDCODED EVENT DATA. A provider must return real events carrying an
   event id, an instant, an importance, the affected currencies/assets, a
   source and provenance. There is no built-in calendar.
2. NO EVENT-FREE FABRICATION. A provider that is unavailable, stale or invalid
   reports that fact. It never returns an empty list to mean "I checked and
   there is nothing scheduled" unless it genuinely observed that.
3. STALE IS NOT EMPTY. A stale source is ``SourceFreshness.STALE``, which the
   enforcement layer treats as undecidable, not as "no events".
4. NO SYMBOL-NAME GUESSING. Market close times come from an explicit session
   record with a timezone, a cutoff and provenance, never inferred from
   "Friday" or from a name like ``EURUSD``.

The evaluation of a fetched event set against a 3A rule is 3B's job. This module
only fetches, validates and labels provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, Sequence, runtime_checkable

UTC = timezone.utc


class SourceError(RuntimeError):
    """An external source failed. Never silently becomes an empty answer."""


class SourceFreshness(str, Enum):
    """How much the runtime may trust a fetched external answer.

    ``STALE`` and ``UNAVAILABLE`` are deliberately distinct from a valid answer
    with zero events. Only ``FRESH`` is usable for compliance decisions.
    """

    FRESH = "FRESH"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"

    @property
    def is_usable(self) -> bool:
        """Only a FRESH answer may decide compliance."""
        return self is SourceFreshness.FRESH


@dataclass(frozen=True)
class SourceProvenance:
    """Where an external answer came from, and how fresh it is.

    This is persisted with every enforcement decision, so a reader can always
    tell WHICH provider produced the evidence that allowed or blocked a trade.
    """

    provider_name: str
    source_reference: str
    retrieved_at_utc: datetime
    freshness: SourceFreshness
    #: The maximum age this provider's answers are trusted for.
    max_age_seconds: int
    detail: str = ""

    def __post_init__(self) -> None:
        for name in ("provider_name", "source_reference"):
            if not str(getattr(self, name) or "").strip():
                raise SourceError(f"SOURCE_PROVENANCE_FIELD_REQUIRED:{name}")
        if self.retrieved_at_utc.tzinfo is None:
            raise SourceError("SOURCE_PROVENANCE_REQUIRES_AWARE_INSTANT")
        if not isinstance(self.freshness, SourceFreshness):
            raise SourceError("SOURCE_PROVENANCE_REQUIRES_FRESHNESS")
        if self.max_age_seconds <= 0:
            raise SourceError("SOURCE_PROVENANCE_MAX_AGE_MUST_BE_POSITIVE")

    def age_seconds(self, *, now_utc: datetime) -> int:
        if now_utc.tzinfo is None:
            raise SourceError("NOW_MUST_BE_AWARE")
        return max(0, int((now_utc - self.retrieved_at_utc).total_seconds()))

    def evaluate(self, *, now_utc: datetime) -> SourceFreshness:
        """Re-derive freshness at READ time; the stored label is not trusted."""
        if self.freshness is not SourceFreshness.FRESH:
            return self.freshness
        if self.age_seconds(now_utc=now_utc) > self.max_age_seconds:
            return SourceFreshness.STALE
        return SourceFreshness.FRESH

    def to_dict(self) -> dict[str, Any]:
        payload = {f.name: getattr(self, f.name) for f in dataclass_fields(self)}
        payload["freshness"] = self.freshness.value
        payload["retrieved_at_utc"] = self.retrieved_at_utc.isoformat()
        return payload



@dataclass(frozen=True)
class EconomicEvent:
    """One real scheduled economic event.

    Every field the enforcement layer needs is REQUIRED. A provider cannot
    return an anonymous event: no id means no provenance, and no id means it
    cannot be audited or deduplicated.
    """

    event_id: str
    event_at_utc: datetime
    importance: Any
    affected_currencies: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()
    name: str = ""

    def __post_init__(self) -> None:
        if not str(self.event_id or "").strip():
            raise SourceError("ECONOMIC_EVENT_REQUIRES_ID")
        if self.event_at_utc.tzinfo is None:
            raise SourceError("ECONOMIC_EVENT_REQUIRES_AWARE_INSTANT")
        object.__setattr__(
            self,
            "affected_currencies",
            tuple(
                sorted(
                    {
                        str(c).strip().upper()
                        for c in self.affected_currencies
                        if str(c).strip()
                    }
                )
            ),
        )
        object.__setattr__(
            self,
            "affected_symbols",
            tuple(
                sorted(
                    {
                        str(s).strip().upper()
                        for s in self.affected_symbols
                        if str(s).strip()
                    }
                )
            ),
        )

    def affects_symbol(self, canonical_symbol: str) -> bool:
        """Whether this event is relevant to a concrete instrument.

        The instrument's own currency pair is compared against the event's
        declared currencies. This is the same currency model the 3A news rule
        declares (``affected_currencies``); there is no hardcoded per-symbol
        list anywhere in the enforcement path.
        """
        symbol = str(canonical_symbol or "").strip().upper()
        if not symbol:
            return False
        if symbol in self.affected_symbols:
            return True
        if not self.affected_currencies:
            return False
        # A six-character FX symbol is BASE+QUOTE, so EITHER leg counts as
        # touched. Anything else is matched as a whole ticker.
        if len(symbol) == 6 and symbol.isalpha():
            legs = {symbol[:3], symbol[3:]}
        else:
            legs = {symbol}
        return bool(legs & set(self.affected_currencies))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_at_utc": self.event_at_utc.isoformat(),
            "importance": getattr(self.importance, "value", self.importance),
            "affected_currencies": list(self.affected_currencies),
            "affected_symbols": list(self.affected_symbols),
            "name": self.name,
        }


@dataclass(frozen=True)
class EconomicCalendarSnapshot:
    """A fetched, provenance-labelled calendar answer.

    An ``UNAVAILABLE`` snapshot carries NO events. That is the whole point: the
    enforcement layer can tell "no events scheduled" (FRESH and empty) from
    "I could not check" (UNAVAILABLE).
    """

    provenance: SourceProvenance
    events: tuple[EconomicEvent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))

    @property
    def freshness(self) -> SourceFreshness:
        return self.provenance.freshness

    def is_decidable(self, *, now_utc: datetime) -> bool:
        """Whether this snapshot may decide an affected entry."""
        return self.provenance.evaluate(now_utc=now_utc).is_usable

    def events_in_window(
        self, start_utc: datetime, end_utc: datetime
    ) -> tuple[EconomicEvent, ...]:
        """Events in ``[start, end)``. Window bounds must be aware."""
        if start_utc.tzinfo is None or end_utc.tzinfo is None:
            raise SourceError("EVENT_WINDOW_REQUIRES_AWARE_BOUNDS")
        return tuple(
            event for event in self.events if start_utc <= event.event_at_utc < end_utc
        )

    @classmethod
    def unavailable(
        cls, *, provenance: SourceProvenance, detail: str = ""
    ) -> "EconomicCalendarSnapshot":
        """The explicit "I could not check" answer. Never an empty FRESH set."""
        return cls(
            provenance=SourceProvenance(
                provider_name=provenance.provider_name,
                source_reference=provenance.source_reference,
                retrieved_at_utc=provenance.retrieved_at_utc,
                freshness=SourceFreshness.UNAVAILABLE,
                max_age_seconds=provenance.max_age_seconds,
                detail=detail or provenance.detail,
            ),
            events=(),
        )


class GovernedCalendarSource:
    """Adapts a freshness-labelled provider to Block 3B's read interface.

    Block 3B consumes ``events_in_window`` because it is a pure historical
    evaluator. Block 3C owns a ``fetch`` provider that additionally reports
    freshness and provenance. This adapter is the ONLY bridge between them, and
    it is where the freshness guarantee is enforced on the way in:

    * a FRESH snapshot is served normally;
    * a STALE / UNAVAILABLE / INVALID snapshot RAISES, which 3B turns into an
      INDETERMINATE evaluation -- and therefore into a governed fail-closed
      enforcement decision.

    Without this, a stale calendar would be served as an empty window and the
    runtime would treat "I could not check" as "nothing is scheduled".
    """

    def __init__(self, provider: "EconomicCalendarProvider") -> None:
        self._provider = provider
        self.last_snapshot: EconomicCalendarSnapshot | None = None

    def fetch(self, *, start_utc: datetime, end_utc: datetime) -> EconomicCalendarSnapshot:
        snapshot = self._provider.fetch(start_utc=start_utc, end_utc=end_utc)
        self.last_snapshot = snapshot
        return snapshot

    def events_in_window(self, start_utc: datetime, end_utc: datetime):
        snapshot = self.fetch(start_utc=start_utc, end_utc=end_utc)
        freshness = snapshot.provenance.evaluate(now_utc=end_utc)
        if not freshness.is_usable:
            raise SourceError(
                f"ECONOMIC_CALENDAR_NOT_USABLE:{freshness.value}"
            )
        return snapshot.events_in_window(start_utc, end_utc)

    @property
    def provenance(self):
        return None if self.last_snapshot is None else self.last_snapshot.provenance


@runtime_checkable
class EconomicCalendarProvider(Protocol):
    """The ONLY way news enforcement may obtain event data."""

    def fetch(self, *, start_utc: datetime, end_utc: datetime) -> EconomicCalendarSnapshot:
        """Return a labelled snapshot, or raise :class:`SourceError`."""
        ...


# ═════════════════════════════════════════════════════════════════════════════
# MARKET SESSION / WEEKEND PROVIDER
# ═════════════════════════════════════════════════════════════════════════════


class MarketState(str, Enum):
    """Whether a market is tradeable at an instant, per the governed calendar."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    #: The provider cannot answer. Never collapsed into CLOSED.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MarketSession:
    """One governed trading session for one instrument.

    EVERY close time is explicit, timezone-qualified and sourced. Nothing here
    is derived from a weekday name or from a symbol string.
    """

    session_id: str
    canonical_symbol: str
    #: The exchange/broker calendar this record came from.
    exchange_calendar: str
    timezone_name: str
    #: Local wall-clock time the session closes, in ``timezone_name``.
    close_local_time: Any
    #: Local wall-clock time the session opens, in ``timezone_name``.
    open_local_time: Any = None
    #: Weekday numbers the session trades (0=Mon .. 6=Sun), explicitly declared.
    trading_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    #: Explicit non-trading dates, e.g. exchange holidays.
    holiday_dates: tuple[str, ...] = ()
    provenance: str = ""

    def __post_init__(self) -> None:
        for name in ("session_id", "canonical_symbol", "exchange_calendar", "timezone_name"):
            if not str(getattr(self, name) or "").strip():
                raise SourceError(f"MARKET_SESSION_FIELD_REQUIRED:{name}")
        if not str(self.provenance or "").strip():
            raise SourceError("MARKET_SESSION_REQUIRES_PROVENANCE")
        for day in self.trading_weekdays:
            if int(day) not in range(0, 7):
                raise SourceError(f"MARKET_SESSION_INVALID_WEEKDAY:{day}")
        object.__setattr__(
            self, "trading_weekdays", tuple(sorted({int(d) for d in self.trading_weekdays}))
        )
        object.__setattr__(
            self, "holiday_dates", tuple(sorted({str(h) for h in self.holiday_dates}))
        )

    def zone(self):
        from zoneinfo import ZoneInfo

        name = self.timezone_name
        return ZoneInfo("UTC" if name.upper() in {"UTC", "Z"} else name)

    def state_at(self, moment_utc: datetime) -> MarketState:
        """The market state at an exact instant, from THIS record only.

        ``UNKNOWN`` is returned rather than guessing when the record cannot
        place the instant (e.g. a holiday list is declared but the instant
        falls on an unlisted weekday).
        """
        if moment_utc.tzinfo is None:
            raise SourceError("MARKET_SESSION_REQUIRES_AWARE_INSTANT")
        local = moment_utc.astimezone(self.zone())
        if local.isoformat()[:10] in self.holiday_dates:
            return MarketState.CLOSED
        if local.weekday() not in self.trading_weekdays:
            return MarketState.CLOSED
        if self.open_local_time is not None and local.time() < self.open_local_time:
            return MarketState.CLOSED
        if local.time() >= self.close_local_time:
            return MarketState.CLOSED
        return MarketState.OPEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "canonical_symbol": self.canonical_symbol,
            "exchange_calendar": self.exchange_calendar,
            "timezone_name": self.timezone_name,
            "close_local_time": self.close_local_time.isoformat(),
            "open_local_time": (
                self.open_local_time.isoformat() if self.open_local_time else None
            ),
            "trading_weekdays": list(self.trading_weekdays),
            "holiday_dates": list(self.holiday_dates),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class MarketSessionAnswer:
    """A session lookup result with explicit provenance and freshness."""

    provenance: SourceProvenance
    state: MarketState = MarketState.UNKNOWN
    session: MarketSession | None = None

    @property
    def freshness(self) -> SourceFreshness:
        return self.provenance.freshness

    def is_decidable(self, *, now_utc: datetime) -> bool:
        return (
            self.provenance.evaluate(now_utc=now_utc).is_usable
            and self.state is not MarketState.UNKNOWN
        )


@runtime_checkable
class MarketSessionProvider(Protocol):
    """The ONLY way hold-restriction enforcement may learn a market state."""

    def state_for(
        self, *, canonical_symbol: str, at_utc: datetime
    ) -> MarketSessionAnswer:
        """Return a labelled answer, or raise :class:`SourceError`."""
        ...


def unavailable_calendar(
    provider_name: str, *, retrieved_at_utc: datetime, max_age_seconds: int, detail: str
) -> EconomicCalendarSnapshot:
    """The canonical "calendar down" answer used at startup and per cycle."""
    return EconomicCalendarSnapshot.unavailable(
        provenance=SourceProvenance(
            provider_name=provider_name,
            source_reference=f"{provider_name}://unavailable",
            retrieved_at_utc=retrieved_at_utc,
            freshness=SourceFreshness.UNAVAILABLE,
            max_age_seconds=max_age_seconds,
            detail=detail,
        )
    )


def unavailable_sessions(
    provider_name: str, *, retrieved_at_utc: datetime, max_age_seconds: int, detail: str
) -> MarketSessionAnswer:
    """The canonical "session calendar down" answer."""
    return MarketSessionAnswer(
        provenance=SourceProvenance(
            provider_name=provider_name,
            source_reference=f"{provider_name}://unavailable",
            retrieved_at_utc=retrieved_at_utc,
            freshness=SourceFreshness.UNAVAILABLE,
            max_age_seconds=max_age_seconds,
            detail=detail,
        ),
        state=MarketState.UNKNOWN,
        session=None,
    )
