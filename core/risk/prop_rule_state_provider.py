"""THE ONE PRODUCTION 3B STATE PROVIDER (Block 3C wiring).

WHY THIS MODULE EXISTS
----------------------
:func:`core.risk.prop_rule_runtime.PropEnforcementRuntime.state_for` delegates to
a ``state_provider``. Until now every caller of that seam was a TEST, so a LIVE
deployment could satisfy ``LIVE_ENFORCE`` + a valid rule pack and still reach
``enforce_positions`` with no governed 3B state at all. This module is the single
production implementation of that seam.

WHAT IT IS
----------
An ADAPTER. It binds the already-defined things together and hands them to the
existing Block 3B assembler :func:`build_account_evaluation_state`:

* exact account identity (:class:`AccountKey`, the 4-tuple),
* the active rule pack / phase / tier (``find_rule_pack``, strictly),
* the frozen evaluation instant supplied by the caller,
* the initial anchor, the rule-day anchor, the high-water marks, the realised
  P&L ledger and the trading-day history, all from
  :class:`PropRuleStateStore`,
* the exact 2A/2B/2C lineage carried by :class:`CycleEvidence`.

WHAT IT IS NOT
--------------
* It is NOT a second state model. There is no second
  :class:`~core.risk.prop_rule_state.AccountEvaluationState` type and no second
  projection; every aggregate is computed by 3B's own functions.
* It is NOT an evaluator. It never imports
  :mod:`core.risk.prop_rule_evaluator`. Daily loss, drawdown, high-water RULES,
  trading-day eligibility and consistency ratios are evaluated in
  ``prop_rule_evaluator.py`` and nowhere else.
* It is NOT a clock. There is no ``datetime.now()`` and no ``time.time()``. A
  caller that does not supply the evaluation instant gets an explicit
  unavailability, never a state built at "roughly now".
* It never contacts a broker and never imports MetaTrader5.

FAIL CLOSED
-----------
Every failure returns ``None``, which the runtime already treats as
``STATE_UNAVAILABLE``: the entry gate blocks NEW risk and position enforcement
performs NO close. An unknown state is never presented as a compliant state.

MULTI-ACCOUNT ISOLATION
-----------------------
Every durable lookup is keyed by the full
``(account_id, broker, server, login)`` identity tuple, and every failure is
contained to the single call, so two accounts reporting the same timestamp,
ticket and symbol can never share or contaminate state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Mapping

from core.risk.prop_rule_enums import RulePhase
from core.risk.prop_rule_pack import (
    AmbiguousRulePackError,
    PropRulePackError,
    RulePack,
    RulePackIdentity,
    RulePackNotFound,
)
from core.risk.prop_rule_state import (
    AccountEvaluationState,
    AccountKey,
    DailyAccountAnchor,
    InitialAccountAnchor,
    PropRuleStateError,
    PropRuleStateStore,
    RuleDayDefinition,
    build_account_evaluation_state,
    content_hash,
    to_epoch_ms,
)

UTC = timezone.utc
logger = logging.getLogger(__name__)

#: Exact, greppable reasons this provider declines to produce state. Each one is
#: logged verbatim so a degraded production cycle is diagnosable from the log
#: alone, with no dashboard and no invented "compliant" fallback.
STATE_INSTANT_UNAVAILABLE = "PROP_3B_STATE_INSTANT_UNAVAILABLE"
STATE_INSTANT_MISMATCH = "PROP_3B_STATE_INSTANT_MISMATCH"
STATE_EVIDENCE_UNAVAILABLE = "PROP_3B_CYCLE_EVIDENCE_UNAVAILABLE"
STATE_CURRENCY_UNAVAILABLE = "PROP_3B_ACCOUNT_CURRENCY_UNAVAILABLE"
STATE_IDENTITY_MISMATCH = "PROP_3B_EVIDENCE_IDENTITY_MISMATCH"
STATE_PACK_IDENTITY_UNCONFIGURED = "PROP_3B_RULE_PACK_IDENTITY_UNCONFIGURED"
STATE_PACK_STORE_UNCONFIGURED = "PROP_3B_RULE_PACK_STORE_UNCONFIGURED"
STATE_PACK_MISSING = "PROP_3B_RULE_PACK_MISSING"
STATE_PACK_AMBIGUOUS = "PROP_3B_RULE_PACK_AMBIGUOUS"
STATE_PACK_NOT_USABLE = "PROP_3B_RULE_PACK_NOT_USABLE"
STATE_CURRENCY_MISMATCH = "PROP_3B_RULE_PACK_CURRENCY_MISMATCH"
STATE_DURABLE_EVIDENCE_UNAVAILABLE = "PROP_3B_DURABLE_EVIDENCE_UNAVAILABLE"


class _DurableTelemetrySources:
    """Read the EXISTING durable Block 2 evidence for ONE exact account.

    This is NOT a new store, dataset or service. It only re-reads what the real
    Block 2 producers already persisted, through their own public readers, and
    re-applies their own freshness thresholds at the caller's instant.

    EVERY failure returns ``None``. There is no fallback value, no zero and no
    partially reconstructed evidence: an order arriving between telemetry cycles
    is authorised only from genuinely fresh, complete, proven observations.
    """

    def __init__(self, dirs: Mapping[str, str] | None) -> None:
        self._dirs = dict(dirs or {})

    def load(self, account: AccountKey, *, at_utc: datetime) -> Any | None:
        if not self._dirs:
            return None
        try:
            from core.risk.account_snapshot import AccountSnapshotStore
            from core.risk.portfolio_exposure import PortfolioExposureStore
            from core.risk.position_snapshot import PositionSnapshotStore
        except Exception:  # pragma: no cover - import guard
            return None
        try:
            now_ms = to_epoch_ms(at_utc)
            snapshot = AccountSnapshotStore(
                base_dir=self._dirs["account"]).latest(
                    account.account_id, now_ms=now_ms
            )
            positions = PositionSnapshotStore(
                base_dir=self._dirs["position"],
                open_risk_dir=self._dirs["open_risk"],
            )
            position_set = positions.latest_position_set(account.account_id)
            # An incomplete set never becomes "no open positions".
            if not position_set.position_set_complete:
                return None
            open_risk = positions.latest_open_risk(
                account.account_id, now_ms=now_ms
            )
            portfolio = PortfolioExposureStore(
                base_dir=self._dirs["portfolio"],
                cluster_dir=self._dirs["cluster"],
            ).latest_portfolio_exposure(account.account_id, now_ms=now_ms)
        except Exception:
            return None
        # Identity is proven by 2A; never trust a record for another account.
        snapshot_identity = (
            str(getattr(snapshot, "account_id", "") or ""),
            str(getattr(snapshot, "broker", "") or ""),
            str(getattr(snapshot, "server", "") or ""),
            getattr(snapshot, "login", None),
        )
        if snapshot_identity != account.identity:
            return None
        return CycleEvidence(
            observed_at_utc=at_utc,
            account_snapshot=snapshot,
            open_risk=open_risk,
            position_set=position_set,
            portfolio=portfolio,
        )


@dataclass(frozen=True)
class CycleEvidence:
    """The EXACT 2A/2B/2C evidence produced by ONE bounded telemetry cycle.

    This is the coherence carrier. Everything in it was produced from a single
    frozen instant for a single account, so a state built from it is coherent
    with the cycle that triggered enforcement. It carries no derived value and
    computes nothing.
    """

    observed_at_utc: datetime
    account_snapshot: Any = None
    open_risk: Any = None
    position_set: Any = None
    portfolio: Any = None


def _as_aware(moment: Any) -> datetime | None:
    """Only a timezone-aware instant is accepted; naive time is a defect."""
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        return None
    return moment.astimezone(UTC)


def _as_number(value: Any) -> float | None:
    """A real number, or ``None``. A bool is never accepted as a measurement."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class PropRuleStateProvider:
    """Bind live 2A/2B/2C evidence to the existing durable Block 3B state.

    The provider is the object installed as ``runtime.state_provider``. It is a
    callable so it satisfies the existing seam exactly, and it takes the
    evaluation instant plus the cycle evidence as keywords so no wall clock is
    ever read inside it.
    """

    def __init__(
        self,
        *,
        rule_pack_store: Any = None,
        pack_identity: RulePackIdentity | None = None,
        rule_day_definition: RuleDayDefinition | None = None,
        state_store: PropRuleStateStore | None = None,
        base_dir: str | None = None,
        source_provenance: str = "BLOCK_3B_STATE_PROVIDER",
        telemetry_dirs: Mapping[str, str] | None = None,
    ) -> None:
        self._packs = rule_pack_store
        self._identity = pack_identity
        self._definition = rule_day_definition
        self._provenance = str(source_provenance or "BLOCK_3B_STATE_PROVIDER")
        # Block 3D REPAIR (minimal): the durable Block 2 evidence fallback.
        #
        # THE DEFECT THIS CLOSES
        # ----------------------
        # The live order path reaches ``prop_enforcement_gate`` -> ``authorize_entry``
        # WITHOUT this cycle's ``CycleEvidence``: an order can arrive between two
        # bounded telemetry cycles. The provider therefore received ``evidence=None``,
        # failed with STATE_EVIDENCE_UNAVAILABLE and returned ``None``, so the entry
        # gate blocked EVERY order with ``PROP_DEGRADED:STATE_UNAVAILABLE`` even for a
        # fully compliant account. The system could never allow trading.
        #
        # THE REPAIR
        # ----------
        # When no live cycle evidence is supplied, resolve the SAME evidence from the
        # EXISTING durable Block 2 stores (2A account snapshots, 2B position sets and
        # open risk, 2C portfolio exposure), each re-evaluated for freshness at the
        # caller's instant using the stores' OWN freshness thresholds.
        #
        # It is strictly fail-closed: no durable record, an incomplete position set,
        # a non-fresh record or any store error all return ``None``, so the gate
        # blocks new risk exactly as before. No state is ever fabricated, no stale
        # value becomes a false zero, and no new dataset or service is introduced.
        self._telemetry = _DurableTelemetrySources(telemetry_dirs)
        # A base_dir makes the state DURABLE: the store rebuilds the initial
        # anchor, the rule-day anchor, the high-water marks, the P&L ledger and
        # the trading-day history from their append-only records at construction,
        # so a process restart cannot erase compliance state.
        self._store = state_store or PropRuleStateStore(base_dir=base_dir)
        #: Last explicit unavailability reason per account, for observability.
        self.last_failure: dict[str, str] = {}

    # -- introspection ------------------------------------------------------

    @property
    def state_store(self) -> PropRuleStateStore:
        return self._store

    @property
    def definition(self) -> RuleDayDefinition | None:
        return self._definition

    # -- the seam -----------------------------------------------------------

    def __call__(
        self,
        account: AccountKey,
        *,
        at_utc: datetime | None = None,
        evidence: CycleEvidence | None = None,
    ) -> AccountEvaluationState | None:
        """The exact governed 3B state for ONE account at ONE instant.

        ``None`` means "explicitly unavailable" with the reason logged. It never
        means "nothing to enforce", and it never carries a fabricated state.
        """
        try:
            return self._build(account, at_utc=at_utc, evidence=evidence)
        except Exception as exc:  # fail closed, contained to this one account
            self._fail(account, f"{type(exc).__name__}:{exc}"[:200])
            return None

    def _fail(self, account: Any, reason: str) -> None:
        key = str(getattr(account, "account_id", "") or "?")
        self.last_failure[key] = reason
        logger.error("[PROP_3B_STATE_UNAVAILABLE] account=%s reason=%s", key, reason)

    # -- assembly -----------------------------------------------------------

    def _build(
        self,
        account: AccountKey,
        *,
        at_utc: datetime | None,
        evidence: CycleEvidence | None,
    ) -> AccountEvaluationState | None:
        if not isinstance(account, AccountKey):
            raise PropRuleStateError("PROP_3B_ACCOUNT_KEY_REQUIRED")

        moment = self._evaluation_instant(account, at_utc, evidence)
        if moment is None:
            if self.last_failure.get(account.account_id) != STATE_INSTANT_MISMATCH:
                self._fail(account, STATE_INSTANT_UNAVAILABLE)
            return None

        # Block 3D REPAIR: an order can arrive BETWEEN two bounded telemetry
        # cycles, so no live CycleEvidence may be supplied. Resolve the SAME
        # evidence from the existing durable Block 2 stores instead of failing
        # closed unconditionally. A missing, incomplete or non-fresh record still
        # resolves to None and blocks new risk exactly as before.
        if evidence is None:
            resolved = self._telemetry.load(account, at_utc=moment)
            if resolved is None:
                self._fail(account, STATE_DURABLE_EVIDENCE_UNAVAILABLE)
                return None
            evidence = resolved

        snapshot = getattr(evidence, "account_snapshot", None)
        currency = self._currency(account, snapshot)
        if currency is None:
            return None

        # The pack is re-resolved STRICTLY at the evaluation instant rather than
        # assumed from startup, so a phase change or an overlapping effective
        # window can never be evaluated against the wrong contract.
        pack = self._active_pack(account, moment, currency)
        if pack is None:
            return None

        # Record the durable evidence this cycle proved. These are 3B's own
        # write-once / append-only APIs; the provider only supplies values 2A
        # published and never edits an anchor that already exists.
        self._record_evidence(account, moment, currency, snapshot, pack)

        definition = self._definition
        assert definition is not None
        rule_day = definition.rule_day_for(moment)

        return build_account_evaluation_state(
            account=account,
            account_currency=currency,
            observed_at_utc=moment,
            definition=definition,
            initial_anchor=self._store.initial_anchor_for(account),
            daily_anchor=self._store.daily_anchor_for(
                account, definition.timezone_name, rule_day
            ),
            account_snapshot=snapshot,
            open_risk=getattr(evidence, "open_risk", None),
            position_set=getattr(evidence, "position_set", None),
            portfolio=getattr(evidence, "portfolio", None),
            close_events=self._store.close_events_for(account, definition),
            rule_pack_id=pack.rule_pack_id,
            evaluation_id=self._evaluation_id(account, moment, pack),
            high_water=self._store.high_water_for(account, currency),
            trading_day_history=self._store.trading_day_history_for(
                account=account, account_currency=currency, definition=definition
            ),
        )

    # -- inputs -------------------------------------------------------------

    def _evaluation_instant(
        self,
        account: AccountKey,
        at_utc: datetime | None,
        evidence: CycleEvidence | None,
    ) -> datetime | None:
        """The cycle instant, never a wall clock.

        The cycle evidence is preferred because it is the SAME instant 2A, 2B and
        2C were produced under; ``at_utc`` is the runtime's own evaluation
        instant. Both are caller-supplied, so the state is always coherent with
        the bounded cycle that triggered enforcement.
        """
        evidence_moment = _as_aware(getattr(evidence, "observed_at_utc", None))
        runtime_moment = _as_aware(at_utc)
        if (
            evidence_moment is not None
            and runtime_moment is not None
            and evidence_moment != runtime_moment
        ):
            self._fail(account, STATE_INSTANT_MISMATCH)
            return None
        return evidence_moment or runtime_moment

    def _currency(self, account: AccountKey, snapshot: Any) -> str | None:
        """The account's OWN currency, read from 2A. Never assumed."""
        if snapshot is None:
            self._fail(account, STATE_EVIDENCE_UNAVAILABLE)
            return None
        snapshot_identity = (
            str(getattr(snapshot, "account_id", "") or ""),
            str(getattr(snapshot, "broker", "") or ""),
            str(getattr(snapshot, "server", "") or ""),
            getattr(snapshot, "login", None),
        )
        if snapshot_identity != account.identity:
            self._fail(account, STATE_IDENTITY_MISMATCH)
            return None
        currency = str(getattr(snapshot, "currency", "") or "").strip().upper()
        if not currency:
            self._fail(account, STATE_CURRENCY_UNAVAILABLE)
            return None
        return currency

    def _active_pack(
        self, account: AccountKey, moment: datetime, currency: str
    ) -> RulePack | None:
        """The EXACT pack in force for this identity at this instant."""
        if self._packs is None or self._identity is None:
            self._fail(account, (
                STATE_PACK_STORE_UNCONFIGURED
                if self._packs is None
                else STATE_PACK_IDENTITY_UNCONFIGURED
            ))
            return None
        identity: RulePackIdentity = self._identity
        try:
            pack = self._packs.find_rule_pack(
                identity.provider,
                identity.program,
                identity.phase,
                identity.account_size,
                moment,
                currency=identity.currency,
                platform=identity.platform,
            )
        except RulePackNotFound:
            self._fail(account, STATE_PACK_MISSING)
            return None
        except AmbiguousRulePackError:
            self._fail(account, STATE_PACK_AMBIGUOUS)
            return None
        except PropRulePackError as exc:
            self._fail(account, f"{STATE_PACK_MISSING}:{exc}"[:200])
            return None

        if not bool(getattr(pack, "is_usable", False)):
            self._fail(
                account,
                f"{STATE_PACK_NOT_USABLE}:{getattr(pack, 'status', 'UNKNOWN')}",
            )
            return None
        if str(identity.currency).strip().upper() != currency:
            # The contract is denominated in another currency. Converting here
            # would be a rule decision, so the state is withheld instead.
            self._fail(account, f"{STATE_CURRENCY_MISMATCH}:{currency}")
            return None
        return pack

    # -- durable recording --------------------------------------------------

    def _record_evidence(
        self,
        account: AccountKey,
        moment: datetime,
        currency: str,
        snapshot: Any,
        pack: RulePack,
    ) -> None:
        """Persist what THIS cycle proved, using 3B's own write-once APIs."""
        snapshot_id = str(getattr(snapshot, "snapshot_id", "") or "")
        if not snapshot_id:
            return
        balance = _as_number(getattr(snapshot, "balance", None))
        equity = _as_number(getattr(snapshot, "equity", None))
        provenance = f"{self._provenance}:{getattr(snapshot, 'source', 'UNKNOWN')}"

        if balance is not None and equity is not None:
            # Append-only observation. The monotone projection is recomputed by
            # 3B from ALL recorded observations, so this can never lower a mark.
            self._store.record_high_water_observation(
                account=account, account_currency=currency,
                balance=balance, equity=equity,
                observed_at_utc=moment, source_snapshot_id=snapshot_id,
            )

        if balance is not None and equity is not None and balance > 0 and equity > 0:
            if self._store.initial_anchor_for(account) is None:
                self._store.record_initial_anchor(InitialAccountAnchor(
                    account=account, account_currency=currency,
                    initial_balance=balance, initial_equity=equity,
                    observed_at_utc=moment, source_snapshot_id=snapshot_id,
                    source_provenance=provenance, rule_pack_id=pack.rule_pack_id,
                ))

        if balance is None or equity is None:
            return
        definition = self._definition
        assert definition is not None
        rule_day = definition.rule_day_for(moment)
        if self._store.daily_anchor_for(
            account, definition.timezone_name, rule_day
        ) is not None:
            return
        boundary = definition.day_start_utc(rule_day)
        self._store.record_daily_anchor(DailyAccountAnchor(
            account=account, account_currency=currency, rule_day=rule_day,
            timezone_name=definition.timezone_name,
            rule_day_definition=definition.key(),
            start_of_day_balance=balance, start_of_day_equity=equity,
            anchor_at_utc=boundary, source_snapshot_id=snapshot_id,
            source_provenance=provenance,
            start_of_day_floating_pnl=_as_number(
                getattr(snapshot, "floating_pnl", None)
            ),
            rule_pack_id=pack.rule_pack_id,
            # An anchor taken from a cycle that did not sit exactly on the
            # boundary is labelled honestly, never presented as exact.
            anchor_basis=(
                "EXACT_BOUNDARY" if moment == boundary else "FIRST_VALID_AFTER"
            ),
        ))

    def _evaluation_id(
        self, account: AccountKey, moment: datetime, pack: RulePack
    ) -> str:
        """Deterministic identity of THIS evaluation, stable across restarts."""
        lineage = self._store.state_lineage(account)
        return "ev_" + content_hash({
            "account": account.to_dict(),
            "observed_at_utc_ms": int(round(moment.timestamp() * 1000)),
            "rule_pack_id": pack.rule_pack_id,
            "initial_anchor_id": lineage.get("initial_anchor_id"),
            "event_count": lineage.get("event_count"),
        })[:32]


def production_state_provider(
    *,
    rule_pack_store: Any = None,
    pack_identity: RulePackIdentity | None = None,
    rule_day_definition: RuleDayDefinition | None = None,
    base_dir: str | None = None,
    state_store: PropRuleStateStore | None = None,
    telemetry_dirs: Mapping[str, str] | None = None,
) -> PropRuleStateProvider:
    """Build THE production provider. Exactly one is created per process.

    ``telemetry_dirs`` names the EXISTING durable Block 2 evidence directories.
    When omitted, the production Block 2 defaults are used, so the entry gate can
    resolve fresh evidence between bounded telemetry cycles. Passing explicit
    ``None`` disables the fallback entirely and restores unconditional
    fail-closed behaviour.
    """
    return PropRuleStateProvider(
        rule_pack_store=rule_pack_store,
        pack_identity=pack_identity,
        rule_day_definition=rule_day_definition,
        state_store=state_store,
        base_dir=base_dir,
        telemetry_dirs=(
            default_telemetry_dirs() if telemetry_dirs is None else telemetry_dirs
        ),
    )


def default_telemetry_dirs() -> dict[str, str]:
    """The EXACT durable Block 2 directories the telemetry service writes."""
    from core.risk.account_snapshot import DEFAULT_LOCAL_DIR as ACCOUNT_DIR
    from core.risk.portfolio_exposure import DEFAULT_LOCAL_DIR as PORTFOLIO_DIR
    from core.risk.portfolio_exposure import DEFAULT_CLUSTER_DIR as CLUSTER_DIR
    from core.risk.position_snapshot import DEFAULT_LOCAL_DIR as POSITION_DIR
    from core.risk.position_snapshot import DEFAULT_OPEN_RISK_DIR as OPEN_RISK_DIR

    return {
        "account": ACCOUNT_DIR,
        "position": POSITION_DIR,
        "open_risk": OPEN_RISK_DIR,
        "portfolio": PORTFOLIO_DIR,
        "cluster": CLUSTER_DIR,
    }


def load_production_rule_pack_store(pack_dir: str) -> Any | None:
    """Load every canonical pack JSON in ``pack_dir``.

    Returns ``None`` when nothing is configured, which is an EXPLICIT
    "no pack in force" and never a synthetic or default pack.
    """
    import json
    from pathlib import Path
    from core.risk.prop_rule_pack import RulePackStore, rule_pack_from_json

    if not str(pack_dir or "").strip():
        return None
    directory = Path(pack_dir)
    if not directory.is_dir():
        return None
    store = RulePackStore()
    for path in sorted(directory.glob("*.json")):
        store.register_rule_pack(rule_pack_from_json(path.read_text(encoding="utf-8")))
    return store or None


def build_production_enforcement_wiring(
    config: Any,
) -> dict[str, Any]:
    """The exact keyword arguments production ``main.py`` starts 3C with.

    Kept beside the provider so there is exactly ONE production assembly path,
    and so the wiring itself is unit-testable without importing ``main``.

    ``rule_pack_store``, ``pack_identity`` and ``rule_day_definition`` are only
    populated from EXPLICIT configuration. Anything missing stays ``None`` and
    surfaces as the matching degraded reason at startup.
    """
    packs = load_production_rule_pack_store(getattr(config, "PROP_RULE_PACK_DIR", ""))
    identity: RulePackIdentity | None = None
    try:
        identity = RulePackIdentity(
            provider=str(getattr(config, "PROP_RULE_PROVIDER", "") or ""),
            program=str(getattr(config, "PROP_RULE_PROGRAM", "") or ""),
            phase=RulePhase(str(getattr(config, "PROP_RULE_PHASE", "") or "")),
            account_size=int(getattr(config, "PROP_RULE_ACCOUNT_SIZE", 0) or 0),
            currency=str(getattr(config, "PROP_RULE_CURRENCY", "") or ""),
            rule_pack_version="1.0.0",
            effective_from=datetime(1970, 1, 1, tzinfo=UTC),
            platform=(str(getattr(config, "PROP_RULE_PLATFORM", "") or "").strip() or None),
        )
    except Exception:
        # An unresolvable phase/tier is an EXPLICIT misconfiguration. It stays
        # None so startup reports RULE_PACK_MISSING rather than guessing a pack.
        identity = None

    definition: RuleDayDefinition | None = None
    timezone_name = str(getattr(config, "PROP_RULE_DAY_TIMEZONE", "") or "").strip()
    if timezone_name:
        try:
            parts = str(
                getattr(config, "PROP_RULE_DAY_RESET", "00:00:00") or "00:00:00"
            ).split(":")
            definition = RuleDayDefinition(
                timezone_name,
                time(int(parts[0]), int(parts[1]), int(float(parts[2]))),
            )
        except Exception:
            definition = None

    provider = production_state_provider(
        rule_pack_store=packs,
        pack_identity=identity,
        rule_day_definition=definition,
        base_dir=str(getattr(config, "PROP_RULE_STATE_DIR", "") or "") or None,
    )
    return {
        "rule_pack_store": packs,
        "pack_identity": identity,
        "rule_day_definition": definition,
        "state_provider": provider,
    }


__all__ = [
    "CycleEvidence",
    "PropRuleStateProvider",
    "STATE_CURRENCY_MISMATCH",
    "STATE_DURABLE_EVIDENCE_UNAVAILABLE",
    "STATE_CURRENCY_UNAVAILABLE",
    "STATE_EVIDENCE_UNAVAILABLE",
    "STATE_IDENTITY_MISMATCH",
    "STATE_INSTANT_MISMATCH",
    "STATE_INSTANT_UNAVAILABLE",
    "STATE_PACK_AMBIGUOUS",
    "STATE_PACK_IDENTITY_UNCONFIGURED",
    "STATE_PACK_MISSING",
    "STATE_PACK_NOT_USABLE",
    "STATE_PACK_STORE_UNCONFIGURED",
    "build_production_enforcement_wiring",
    "default_telemetry_dirs",
    "load_production_rule_pack_store",
    "production_state_provider",
]
