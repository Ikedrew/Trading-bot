"""Managed runtime orchestration for prop-risk telemetry (Block 2D).

WHY THIS MODULE EXISTS
----------------------
Block 2A/2B/2C shipped complete, certified contracts, but they were reachable
ONLY as callable code: ``git grep`` showed that ``AccountSnapshotProducer``,
``PositionSnapshotProducer``, ``PortfolioExposureProducer`` and both heartbeat
classes were referenced from nothing outside ``core/risk/*`` and their own test
files. ``main.py`` started the Block 1 canonical delivery service but never
started telemetry, so no prop-risk row could ever be produced at runtime.

This module is the MINIMAL runtime repair. It owns EXACTLY ONE daemon thread
(never three competing heartbeat threads) and runs the complete
2A -> 2B -> 2C chain for every configured account under a single frozen
observation instant. It deliberately reuses every existing producer rather than
introducing a new observation architecture, a new dataset or a new risk concept.

WHAT IT DOES AND DOES NOT DO
----------------------------
DOES: read account state, observe positions, derive portfolio/correlation
exposure, persist all of it through the certified Block 1 canonical handoff,
and keep those stores reconstructable across restart.

DOES NOT: decide any prop rule, block a trade, modify a trade, or enforce a
limit. Daily loss, drawdown, trailing drawdown, profit target and minimum
trading days remain Block 3 concerns and are deliberately absent here.

DESIGN RULES INHERITED FROM BLOCK 2
-----------------------------------
1. ONE ACCOUNT NEVER SATISFIES ANOTHER. Every account is observed through its
   OWN pinned ``PositionSource``; a per-account failure is isolated and leaves
   every other account valid.
2. ONE FROZEN INSTANT PER CYCLE. The clock is read once and frozen, so 2A, 2B
   and 2C share one observation lineage and can never straddle two cycles.
3. FAIL CLOSED, NEVER FABRICATE. A source failure yields an explicit
   UNAVAILABLE/PARTIAL record. It never becomes a zero position, a zero risk or
   a valid empty portfolio.
4. TELEMETRY NEVER KILLS THE RUNTIME. Any cycle fault is logged and the loop
   continues; telemetry is observational evidence, not a trading dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from core.risk.account_snapshot import (
    DEFAULT_LOCAL_DIR as ACCOUNT_SNAPSHOT_DIR,
    AccountIdentity,
    AccountSnapshot,
    AccountSnapshotStatus,
    AccountSourceUnavailable,
    Mt5AccountInfoSource,
    capture_account_snapshot,
    persist_account_snapshot,
    snapshot_interval_ms,
    utc_now,
)
from core.risk.portfolio_exposure import (
    DEFAULT_CLUSTER_DIR,
    DEFAULT_LOCAL_DIR as PORTFOLIO_DIR,
    PortfolioExposureProducer,
    PortfolioStatus,
)
from core.risk.position_snapshot import (
    DEFAULT_LOCAL_DIR as POSITION_DIR,
    DEFAULT_OPEN_RISK_DIR,
    Mt5OrderCalcProfit,
    Mt5PositionSource,
    Mt5SymbolSpecSource,
    PositionObservationCycle,
    PositionSetUnavailable,
    PositionSource,
    observe_positions,
    persist_open_risk,
    persist_position_set,
    persist_position_snapshot,
)
from core.risk.symbol_correlation import (
    SymbolCorrelationModel,
    default_symbol_correlation_model,
)

logger = logging.getLogger(__name__)

#: The governed correlation model is resolved lazily and only ONCE per process.
#: An absent/invalid model degrades 2C to PARTIAL; it never invents clusters.
_MODEL: SymbolCorrelationModel | None = None


def governed_correlation_model() -> SymbolCorrelationModel:
    """Return this process's governed correlation model, building it once."""
    global _MODEL
    if _MODEL is None:
        _MODEL = default_symbol_correlation_model()
    return _MODEL


def _canonical_symbols() -> list[str]:
    try:
        from core.accounts.config import CANONICAL_SYMBOLS
        return list(CANONICAL_SYMBOLS)
    except Exception:
        return []


def _canonical_resolver(canonical_list: Sequence[str]) -> Callable[[str], str | None]:
    """Broker symbol -> canonical symbol, via the EXISTING resolver layer.

    Correlation operates on canonical symbols only. An unresolvable broker
    symbol stays ``None`` so the canonical identity is explicitly unavailable
    rather than fabricated.
    """
    def resolve(broker_symbol: str) -> str | None:
        try:
            from core.symbol_resolver import get_canonical
            return get_canonical(broker_symbol, list(canonical_list))
        except Exception:
            return None
    return resolve


@dataclass(frozen=True)
class AccountTelemetryResult:
    """One account's outcome for ONE telemetry cycle.

    This is the observable proof that a prop-rule engine can read: it carries the
    exact lineage IDs and the exact completeness/monetary facts, or an explicit
    failure reason. It never reports a fabricated zero.
    """

    account_id: str
    observation_id: str | None
    account_snapshot_id: str | None
    open_risk_id: str | None
    portfolio_exposure_id: str | None

    account_status: str
    open_risk_status: str
    portfolio_status: str

    position_set_complete: bool
    risk_complete: bool
    correlation_complete: bool

    open_position_count: int | None
    known_open_risk: float | None
    total_open_risk: float | None
    currency: str | None
    cluster_count: int

    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _frozen_clock(instant: datetime) -> Callable[[], datetime]:
    """One clock reading per cycle: 2A, 2B and 2C share it exactly."""
    return lambda: instant


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TelemetryServiceStatus:
    """Runtime status snapshot. Local observability only; no network."""

    running: bool
    started_at: str | None
    last_cycle_at: str | None
    cycles_completed: int
    accounts_observed: int
    last_error: str | None


class _UnavailableSource:
    """Explicit unreadable position boundary (never an empty portfolio)."""

    def read_positions(self):
        from core.risk.position_snapshot import PositionSetUnavailable
        raise PositionSetUnavailable("NO_POSITION_SOURCE_CONFIGURED")


_UNAVAILABLE_SOURCE = _UnavailableSource()


class PropRiskTelemetryService:
    """The ONE managed prop-risk telemetry loop for the live process.

    It owns at most one daemon thread, is idempotent on ``start()``, schedules
    from the PREVIOUS DUE TIME so a slow cycle cannot accumulate drift, and
    stops immediately via an Event. Every boundary is injectable so tests never
    touch MT5 and never sleep.

    The chain per account is strictly ordered and shares one frozen instant:

        2A capture_account_snapshot          -> account_snapshot_id
        2B observe_positions                 -> observation_id (links snapshot_id)
        2C PortfolioExposureProducer.observe -> portfolio_exposure_id

    A failure at any stage is isolated to that account and recorded explicitly.
    """

    def __init__(
        self,
        *,
        identities: Sequence[AccountIdentity],
        account_sources: Mapping[str, Any] | None = None,
        position_sources: Mapping[str, PositionSource] | None = None,
        spec_source: Any | None = None,
        calculator: Any | None = None,
        model: SymbolCorrelationModel | None = None,
        outbox: Any | None = None,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] | None = None,
        interval_ms: int | None = None,
        shutdown_timeout: float = 5.0,
        account_dir: str = ACCOUNT_SNAPSHOT_DIR,
        position_dir: str = POSITION_DIR,
        open_risk_dir: str = DEFAULT_OPEN_RISK_DIR,
        portfolio_dir: str = PORTFOLIO_DIR,
        cluster_dir: str = DEFAULT_CLUSTER_DIR,
        persist: bool = True,
        start_immediately: bool = False,
    ) -> None:
        self._identities = tuple(identities)
        if len({i.account_id for i in self._identities}) != len(self._identities):
            raise ValueError("DUPLICATE_TELEMETRY_ACCOUNT_ID")
        # 2A sources are ACCOUNT-SCOPED for the same reason 2B sources are: one
        # shared MT5 session across accounts would cross the account boundary.
        self._account_sources = dict(account_sources or {})
        self._position_sources = dict(position_sources or {})
        self._spec_source = spec_source
        self._calculator = calculator
        self._model = model
        self._outbox = outbox
        self._clock = clock
        self._monotonic = monotonic or time.monotonic
        self._interval_ms = int(
            snapshot_interval_ms() if interval_ms is None else interval_ms)
        if self._interval_ms <= 0:
            raise ValueError("TELEMETRY_INTERVAL_MUST_BE_POSITIVE")
        if shutdown_timeout <= 0:
            raise ValueError("TELEMETRY_SHUTDOWN_TIMEOUT_MUST_BE_POSITIVE")
        self._shutdown_timeout = float(shutdown_timeout)
        self._account_dir = account_dir
        self._position_dir = position_dir
        self._open_risk_dir = open_risk_dir
        self._portfolio_dir = portfolio_dir
        self._cluster_dir = cluster_dir
        self._persist = persist

        self._canonical = _canonical_resolver(_canonical_symbols())
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._started_at: str | None = None
        self._last_cycle_at: str | None = None
        self._last_error: str | None = None
        self._cycles = 0
        self._next_due = self._monotonic() + (self._interval_ms / 1000.0)
        self._last_results: tuple[AccountTelemetryResult, ...] = ()

        if start_immediately:
            self.tick()

    # ── observability ──────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and not self._stop.is_set())

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def last_results(self) -> tuple[AccountTelemetryResult, ...]:
        return self._last_results

    def status(self) -> TelemetryServiceStatus:
        return TelemetryServiceStatus(
            running=self.running,
            started_at=self._started_at,
            last_cycle_at=self._last_cycle_at,
            cycles_completed=self._cycles,
            accounts_observed=len(self._last_results),
            last_error=self._last_error,
        )

    # ── one account, one frozen instant ────────────────────────────────
    def observe_account(
        self, identity: AccountIdentity, *, instant: datetime | None = None,
    ) -> AccountTelemetryResult:
        """Run 2A -> 2B -> 2C for ONE exact account under ONE frozen instant.

        Never raises for a broker problem: an unusable source yields an explicit
        degraded result rather than fabricated telemetry.
        """
        clock = _frozen_clock(instant if instant is not None else self._clock())

        try:
            # ── 2A: account state ────────────────────────────────────────
            snapshot = self._capture_snapshot(identity, clock)
            if snapshot is None:
                return self._degraded(identity, "ACCOUNT_SOURCE_UNAVAILABLE")

            # ── 2B: positions + open risk, linked by EXACT snapshot_id ───
            source = self._position_sources.get(identity.account_id)
            cycle = observe_positions(
                identity, _UNAVAILABLE_SOURCE if source is None else source,
                clock=clock, currency=snapshot.currency,
                account_snapshot_id=snapshot.snapshot_id,
                spec_source=self._spec_source, calculator=self._calculator,
                canonical_resolver=self._canonical,
            )
            if self._persist:
                self._persist_cycle(cycle)

            # ── 2C: portfolio + correlation, from THIS cycle only ───────
            portfolio_cycle = PortfolioExposureProducer(
                model=self._model, persist=self._persist,
                base_dir=self._portfolio_dir, cluster_dir=self._cluster_dir,
                outbox=self._outbox,
            ).observe(cycle, account_snapshot=snapshot)

            return self._result(identity, snapshot, cycle, portfolio_cycle)
        except Exception as exc:  # telemetry must never kill the runtime
            logger.exception(
                "[PROP_RISK_TELEMETRY] cycle failed account=%s", identity.account_id)
            return self._degraded(
                identity, f"TELEMETRY_CYCLE_FAILED:{type(exc).__name__}")

    def _capture_snapshot(
        self, identity: AccountIdentity, clock: Callable[[], datetime],
    ) -> AccountSnapshot | None:
        # Each account reads through ITS OWN source. A missing source is an
        # explicit unavailability, never a silent fall-back to another account.
        source = self._account_sources.get(identity.account_id)
        if source is None:
            return None
        snapshot = capture_account_snapshot(
            identity, source, clock=clock,
            source_name=getattr(source, "source_name", "MT5_ACCOUNT_INFO"),
        )
        if self._persist:
            persist_account_snapshot(
                snapshot, base_dir=self._account_dir, outbox=self._outbox)
        return snapshot

    def _persist_cycle(self, cycle: PositionObservationCycle) -> None:
        persist_position_set(
            cycle.position_set, base_dir=self._position_dir, outbox=self._outbox)
        for row in cycle.positions:
            persist_position_snapshot(
                row, base_dir=self._position_dir, outbox=self._outbox)
        persist_open_risk(
            cycle.open_risk, base_dir=self._open_risk_dir, outbox=self._outbox)

    # ── result assembly: never a fabricated zero ─────────────────────────
    def _result(
        self, identity: AccountIdentity, snapshot: AccountSnapshot,
        cycle: PositionObservationCycle, portfolio_cycle: Any,
    ) -> AccountTelemetryResult:
        risk = cycle.open_risk
        portfolio = portfolio_cycle.portfolio
        # An UNAVAILABLE 2A snapshot means the account's own currency is
        # unknown. A monetary total without a currency is not provable risk,
        # so EVERY monetary fact is withheld rather than published unlabelled.
        if snapshot.status is AccountSnapshotStatus.UNAVAILABLE:
            return AccountTelemetryResult(
                account_id=identity.account_id,
                observation_id=cycle.observation_id,
                account_snapshot_id=snapshot.snapshot_id,
                open_risk_id=risk.open_risk_id,
                portfolio_exposure_id=portfolio.portfolio_exposure_id,
                account_status=snapshot.status.value,
                open_risk_status=risk.status.value,
                portfolio_status=portfolio.status.value,
                position_set_complete=bool(risk.position_set_complete),
                risk_complete=False,
                correlation_complete=bool(portfolio.correlation_complete),
                open_position_count=risk.open_position_count,
                known_open_risk=None,
                total_open_risk=None,
                currency=None,
                cluster_count=int(portfolio.cluster_count),
                error=f"ACCOUNT_SNAPSHOT_{snapshot.status.value}",
            )
        # A failed 2B position set or 2C derivation degrades the account but
        # preserves the valid 2A/2B evidence that was actually observed.
        degraded = None
        if not risk.position_set_complete:
            degraded = f"POSITION_SET_{risk.status.value}"
        elif portfolio.status is PortfolioStatus.UNAVAILABLE:
            degraded = f"PORTFOLIO_{portfolio.failure_reason.value}"
        return AccountTelemetryResult(
            account_id=identity.account_id,
            observation_id=cycle.observation_id,
            account_snapshot_id=snapshot.snapshot_id,
            open_risk_id=risk.open_risk_id,
            portfolio_exposure_id=portfolio.portfolio_exposure_id,
            account_status=snapshot.status.value,
            open_risk_status=risk.status.value,
            portfolio_status=portfolio.status.value,
            position_set_complete=bool(risk.position_set_complete),
            risk_complete=bool(risk.risk_complete),
            correlation_complete=bool(portfolio.correlation_complete),
            open_position_count=risk.open_position_count,
            known_open_risk=risk.known_open_risk,
            total_open_risk=risk.total_open_risk,
            currency=snapshot.currency or risk.currency,
            cluster_count=int(portfolio.cluster_count),
            error=degraded,
        )

    def _degraded(
        self, identity: AccountIdentity, error: str,
    ) -> AccountTelemetryResult:
        """An account that produced NO usable evidence. All monetary facts None."""
        return AccountTelemetryResult(
            account_id=identity.account_id,
            observation_id=None, account_snapshot_id=None,
            open_risk_id=None, portfolio_exposure_id=None,
            account_status="UNAVAILABLE", open_risk_status="UNAVAILABLE",
            portfolio_status=PortfolioStatus.UNAVAILABLE.value,
            position_set_complete=False, risk_complete=False,
            correlation_complete=False,
            open_position_count=None, known_open_risk=None,
            total_open_risk=None, currency=None, cluster_count=0,
            error=error,
        )

    # ── one bounded synchronous cycle across every account ───────────────
    def tick(self) -> tuple[AccountTelemetryResult, ...]:
        """Run ONE full 2A -> 2B -> 2C cycle for every configured account.

        Synchronous and bounded: tests call this directly with an injected clock
        and never sleep. Accounts are isolated -- one account's failure can never
        abort or degrade another account's result.
        """
        instant = self._clock()
        results: list[AccountTelemetryResult] = []
        for identity in self._identities:
            try:
                results.append(self.observe_account(identity, instant=instant))
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception(
                    "[PROP_RISK_TELEMETRY] account isolated account=%s",
                    identity.account_id)
                results.append(self._degraded(
                    identity, f"TELEMETRY_ACCOUNT_FAILED:{type(exc).__name__}"))
        with self._lock:
            self._last_results = tuple(results)
            self._cycles += 1
            self._last_cycle_at = _utc_now_iso()
            failures = [r for r in results if r.error]
            self._last_error = (
                "; ".join(f"{r.account_id}:{r.error}" for r in failures)
                if failures else None
            )
        return self._last_results

    # ── managed lifecycle ────────────────────────────────────────────────
    def start(self) -> bool:
        """Start the ONE managed loop. Idempotent: never a second thread.

        Returns True only when a NEW loop thread was actually created.
        """
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._started_at = _utc_now_iso()
            self._next_due = self._monotonic() + (self._interval_ms / 1000.0)
            self._thread = threading.Thread(
                target=self._run, name="prop_risk_telemetry", daemon=True)
            self._thread.start()
            logger.info(
                "[PROP_RISK_TELEMETRY_STARTED] accounts=%d interval_ms=%d",
                len(self._identities), self._interval_ms)
            return True

    def stop(self, *, timeout: float | None = None) -> bool:
        """Signal and join the managed loop. Bounded; never blocks forever."""
        budget = self._shutdown_timeout if timeout is None else float(timeout)
        with self._lock:
            thread = self._thread
        self._stop.set()
        if thread is None:
            return True
        thread.join(budget if budget > 0 else 0.0)
        if thread.is_alive():
            with self._lock:
                self._last_error = "TELEMETRY_SHUTDOWN_TIMEOUT"
            logger.error("[PROP_RISK_TELEMETRY_SHUTDOWN_TIMEOUT]")
            return False
        with self._lock:
            self._thread = None
        logger.info("[PROP_RISK_TELEMETRY_STOPPED] cycles=%d", self._cycles)
        return True

    def next_due_from(self, previous_due: float) -> float:
        """Next due time derived from the PREVIOUS due time (no drift)."""
        return float(previous_due) + (self._interval_ms / 1000.0)

    def next_due_monotonic(self) -> float:
        return self._next_due

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
                logger.exception("[PROP_RISK_TELEMETRY] cycle failed")

    # ── context manager ─────────────────────────────────────────────────
    def __enter__(self) -> "PropRiskTelemetryService":
        self.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        self.stop()
        return False


# ═════════════════════════════════════════════════════════════════════════════
# PROCESS-LEVEL SINGLETON
#
# Mirrors core.canonical_delivery_service exactly: one start / one stop, one
# thread, and a bounded stop so graceful shutdown can never hang on telemetry.
# ═════════════════════════════════════════════════════════════════════════════

_SERVICE_LOCK = threading.Lock()
_SERVICE: PropRiskTelemetryService | None = None


def start_prop_risk_telemetry_service(
    service: PropRiskTelemetryService | None = None,
) -> PropRiskTelemetryService | None:
    """Start (or return) the one process-level prop-risk telemetry service.

    Returns ``None`` when no account is configured -- telemetry stays silent
    rather than inventing an identity or failing startup.
    """
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is not None and _SERVICE.running:
            return _SERVICE
        if service is None:
            service = build_runtime_telemetry_service()
            if service is None:
                return None
        _SERVICE = service
    service.start()
    return service


def stop_prop_risk_telemetry_service() -> bool:
    """Stop the process-level service during graceful application shutdown."""
    with _SERVICE_LOCK:
        service = _SERVICE
    return True if service is None else service.stop()


def prop_risk_telemetry_service_status() -> TelemetryServiceStatus | None:
    with _SERVICE_LOCK:
        service = _SERVICE
    return None if service is None else service.status()


# ═════════════════════════════════════════════════════════════════════════════
# RUNTIME WIRING
#
# This is the ONLY place that turns live account configuration into a running
# service. It deliberately REUSES the existing multi-account terminal
# isolation: the BASELINE account is read through the process MT5 session it
# already owns, and every OTHER account is read through its OWN pinned worker.
# There is deliberately no global-session shortcut across accounts.
# ═════════════════════════════════════════════════════════════════════════════

BASELINE_ACCOUNT_ID = "METAQUOTES"


def enabled_account_identities() -> tuple[AccountIdentity, ...]:
    """Exact identities of every enabled, correctly configured account.

    Configuration is the only identity authority here; nothing is invented.
    """
    from core.accounts.config import load_accounts

    identities: list[AccountIdentity] = []
    for config in load_accounts():
        if not config.enabled or config.errors():
            continue
        try:
            identities.append(AccountIdentity.from_account_config(config))
        except Exception:  # a malformed identity is skipped, never invented
            logger.warning(
                "[PROP_RISK_TELEMETRY] unusable account identity account=%s",
                getattr(config, "account_id", "?"))
    return tuple(identities)


class WorkerAccountInfoSource:
    """2A account state for a NON-baseline account, via its isolated worker.

    This deliberately reuses ``core.accounts.manager.run_isolated`` -- the
    existing pinned-account subprocess boundary -- instead of reading the
    process MT5 session, which belongs to another account.
    """

    source_name = "MT5_ACCOUNT_WORKER"

    def __init__(self, identity: AccountIdentity) -> None:
        self._identity = identity

    def _payload(self) -> dict:
        from core.accounts.config import load_accounts
        from core.accounts.manager import run_isolated

        config = next(
            (c for c in load_accounts()
             if c.account_id == self._identity.account_id), None,
        )
        if config is None:
            raise AccountSourceUnavailable("ACCOUNT_CONFIG_NOT_FOUND")
        payload = run_isolated(config)
        if not payload.get("connected") or not payload.get("identity_verified"):
            raise AccountSourceUnavailable(
                (payload.get("reasons") or ["ACCOUNT_WORKER_UNAVAILABLE"])[0])
        return dict(payload)

    def read_account_info(self):
        info = self._payload()
        info["login"] = info.get("login") or self._identity.login
        info["server"] = info.get("server") or self._identity.server
        return info


class WorkerPositionSource:
    """2B positions for a NON-baseline account, via its isolated worker."""

    def __init__(self, identity: AccountIdentity) -> None:
        self._identity = identity

    def read_positions(self):
        from core.accounts.config import load_accounts
        from core.accounts.manager import run_isolated

        config = next(
            (c for c in load_accounts()
             if c.account_id == self._identity.account_id), None,
        )
        if config is None:
            raise PositionSetUnavailable("ACCOUNT_CONFIG_NOT_FOUND")
        payload = run_isolated(config)
        if not payload.get("connected") or not payload.get("identity_verified"):
            raise PositionSetUnavailable(
                (payload.get("reasons") or ["ACCOUNT_WORKER_UNAVAILABLE"])[0])
        rows = payload.get("positions")
        if rows is None:
            # Explicitly unreadable. NEVER an empty portfolio.
            raise PositionSetUnavailable("POSITIONS_UNAVAILABLE")
        return list(rows)


def runtime_account_sources(identities: Sequence[AccountIdentity]):
    """Per-account (account-info, position) source maps for live operation."""
    account_sources: dict[str, Any] = {}
    position_sources: dict[str, Any] = {}
    for identity in identities:
        if identity.account_id == BASELINE_ACCOUNT_ID:
            # The baseline terminal IS this process's own MT5 session.
            account_sources[identity.account_id] = Mt5AccountInfoSource()
            position_sources[identity.account_id] = Mt5PositionSource(identity)
        else:
            account_sources[identity.account_id] = WorkerAccountInfoSource(
                identity)
            position_sources[identity.account_id] = WorkerPositionSource(
                identity)
    return account_sources, position_sources


def build_runtime_telemetry_service(**kwargs) -> PropRiskTelemetryService | None:
    """Build the live service from real account configuration.

    Returns ``None`` when no account is enabled/configured: telemetry stays
    silent rather than inventing an identity or failing startup.
    """
    identities = enabled_account_identities()
    if not identities:
        logger.info("[PROP_RISK_TELEMETRY] no enabled accounts configured")
        return None
    account_sources, position_sources = runtime_account_sources(identities)
    kwargs.setdefault("model", governed_correlation_model())
    return PropRiskTelemetryService(
        identities=identities,
        account_sources=account_sources,
        position_sources=position_sources,
        spec_source=Mt5SymbolSpecSource(),
        calculator=Mt5OrderCalcProfit(),
        **kwargs,
    )


__all__ = [
    "AccountTelemetryResult",
    "PropRiskTelemetryService",
    "TelemetryServiceStatus",
    "WorkerAccountInfoSource",
    "WorkerPositionSource",
    "build_runtime_telemetry_service",
    "enabled_account_identities",
    "governed_correlation_model",
    "prop_risk_telemetry_service_status",
    "runtime_account_sources",
    "start_prop_risk_telemetry_service",
    "stop_prop_risk_telemetry_service",
]
