"""Recover broker-owned positions independently through each configured worker."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from core.accounts.lifecycle import LifecycleRouter
from core.accounts.position_state import load, position_key
from core.position_ownership import PositionOwnership
from core.trade_management.position import Position, PositionStatus
from core.trade_identity import TradeIdentity
from core.mt5_timestamp import normalize_mt5_timestamp
from strategy.signals import Side

logger = logging.getLogger(__name__)


def recover_positions_on_startup(*, trade_manager, symbol, magic, broker_symbol=None,
                                 accounts=None, lifecycle_router=None):
    """Recover broker positions for one canonical symbol (legacy entry point).

    Kept for existing per-symbol callers and tests. Delegates to the batched
    worker path (``recover_positions_batch``) so every account worker still
    performs a single verified MT5 session, and returns the number of positions
    adopted into ``trade_manager``.
    """
    if trade_manager is None:
        return 0
    return recover_positions_batch(magic=magic, trade_managers={symbol: trade_manager},
                                   symbols=(symbol,), accounts=accounts,
                                   lifecycle_router=lifecycle_router)


def recover_positions_batch(*, magic, trade_managers, accounts=None,
                            lifecycle_router=None, symbols=None):
    """Batched startup recovery: ONE lifecycle worker per enabled account.

    ``trade_managers`` maps canonical_symbol -> TradeStateManager. Instead of a
    fresh ``python -m core.accounts.lifecycle_worker`` subprocess per account ×
    symbol (30 spawns for 10 symbols × 3 accounts), each enabled account gets at
    most ONE worker invocation carrying every supported canonical symbol, so
    each worker performs a single verified MT5 initialize/recover/shutdown
    cycle and returns per-symbol recovery results.

    Capability contract per (account, canonical_symbol) — unchanged:
      SUPPORTED   -> included in that account's worker batch.
      UNSUPPORTED -> clean INFO skip, no worker op, no terminal touch.
      INVALID     -> fail closed for that account/symbol; never promoted from
                     a terminal snapshot.

    Identity verification is unchanged (LifecycleRouter.call). Ownership of
    adopted positions remains strictly (account_id, position_ticket) scoped.
    A failing account never blocks its peers; a per-symbol failure is logged
    explicitly and never adopts incomplete/foreign state.
    """
    if not trade_managers:
        return 0
    router = lifecycle_router
    if router is None:
        router = (LifecycleRouter(accounts) if accounts is not None
                  else next(iter(trade_managers.values())).lifecycle_router)
    if symbols is None:
        symbols = list(trade_managers.keys())
    symbols = [str(s) for s in symbols]
    enabled = [a for a in router.accounts if a.enabled]
    legacy = len(enabled) == 1 and enabled[0].account_id == "METAQUOTES"
    count = 0

    # Build the worker batch per account straight from the capability contract.
    batches = []
    for account in enabled:
        supported = []
        for canonical in symbols:
            state = account.capability(canonical)
            if state == 'UNSUPPORTED':
                logger.info("[ACCOUNT_RECOVERY_SKIPPED] account=%s symbol=%s reason=UNSUPPORTED_SYMBOL",
                            account.account_id, canonical)
                continue
            if state != 'SUPPORTED':
                logger.error("[ACCOUNT_RECOVERY_FAILED] account=%s symbol=%s error=%s",
                             account.account_id, canonical, 'SYMBOL_UNAVAILABLE_OR_AMBIGUOUS')
                continue
            supported.append(canonical)
        if supported:
            batches.append((account, supported))

    # Each IPC call has its own bounded subprocess timeout. Failure is local;
    # successful accounts are consumed as soon as their workers return.
    with ThreadPoolExecutor(max_workers=max(1, len(batches))) as pool:
        futures = {pool.submit(router.call, account.account_id, 'recover',
                               symbols=tuple(supported), magic=magic): account
                   for account, supported in batches}
        for future in as_completed(futures):
            account = futures[future]
            try:
                value = future.result()
            except Exception as exc:
                logger.error("[ACCOUNT_RECOVERY_FAILED] account=%s error=%s", account.account_id, exc)
                continue
            results, errors = _split_batch_value(value)
            for canonical, error in sorted(errors.items()):
                logger.error("[ACCOUNT_RECOVERY_FAILED] account=%s symbol=%s error=%s",
                             account.account_id, canonical, error)
            for canonical, rows in results.items():
                trade_manager = trade_managers.get(canonical)
                if trade_manager is None:
                    logger.error("[POSITION_RECOVERY_FAILED] account=%s symbol=%s error=%s",
                                 account.account_id, canonical, 'UNKNOWN_CANONICAL_SYMBOL')
                    continue
                count += _adopt_rows(router=router, account=account,
                                     canonical_symbol=canonical, rows=rows,
                                     magic=magic, trade_manager=trade_manager,
                                     legacy=legacy)
    return count


def _split_batch_value(value):
    """Normalize a worker ``recover`` response into (results, errors).

    Batched workers return ``{'results': {canonical: [rows]}, 'errors': {}}``.
    An empty list is the legacy/empty transport shape (no positions). Any other
    shape is treated as malformed and fails closed with no adoption.
    """
    if isinstance(value, dict):
        results, errors = value.get('results'), value.get('errors')
        if results is None and errors is None:
            return {}, {}
        return (results or {}), (errors or {})
    if isinstance(value, (list, tuple)) and not value:
        return {}, {}
    return {}, {}


def _adopt_rows(*, router, account, canonical_symbol, rows, magic,
                trade_manager, legacy):
    """Adopt broker-owned rows for one canonical symbol into its manager."""
    owner_count = 0
    for raw in rows:
        try:
            row = raw if isinstance(raw, SimpleNamespace) else SimpleNamespace(**raw)
            if int(row.magic) != magic or float(row.volume) <= 0:
                continue
            owner = PositionOwnership(account.account_id, account.broker, account.server,
                int(row.ticket), canonical_symbol=canonical_symbol, broker_symbol=row.broker_symbol)
            router.validate(owner)
            if trade_manager.get_position(account_id=owner.account_id, position_ticket=owner.position_ticket):
                continue
            state = load(owner)
            identity = _restore_identity_from_logs(symbol=canonical_symbol,
                ticket=owner.position_ticket, entry_price=float(row.price_open),
                account_id=owner.account_id, broker=owner.broker,
                broker_server=owner.broker_server, allow_legacy=legacy)
            if state:
                identity.update(state)
                identity.update(state['ownership'])
            owner = owner._replace(**{k: identity.get(k) for k in (
                'canonical_opportunity_id', 'correlation_id', 'decision_id',
                'account_execution_id', 'trade_id', 'order_ticket', 'deal_ticket')})
            ti = TradeIdentity(correlation_id=owner.correlation_id or '',
                canonical_opportunity_id=owner.canonical_opportunity_id or '',
                decision_id=owner.decision_id or '',
                observation_id=identity.get('observation_id', ''),
                cycle_id=int(identity.get('cycle_id', 0) or 0),
                strategy=identity.get('strategy', ''),
                pattern=identity.get('pattern', 'RECOVERED'),
                decision_ts_utc=float(identity.get('decision_ts_utc', 0) or 0))
            side = Side.BUY if int(row.type) == 0 else Side.SELL
            mfe = mae = float(row.price_current)
            provenance = 'recovery_seeded'
            from core.trade_management.excursion_state import load_excursion, restore_extremes
            saved = load_excursion(owner.position_ticket, account_id=owner.account_id)
            if saved:
                mfe, mae = restore_extremes(side_name=side.name,
                    saved_mfe=saved.get('max_favourable_price'),
                    saved_mae=saved.get('max_adverse_price'), current_price=mfe)
                provenance = 'full_lifecycle'
            pos = Position(position_id=position_key(owner), symbol=canonical_symbol,
                side=side, magic=magic, entry_price=float(row.price_open),
                initial_sl=float(identity.get('initial_sl', row.sl)),
                initial_tp=float(identity.get('initial_tp', row.tp)),
                stop_loss=float(row.sl), take_profit=float(row.tp),
                volume=float(row.volume),
                open_time=normalize_mt5_timestamp(float(row.time)),
                status=PositionStatus.OPEN, mt5_ticket=owner.position_ticket,
                deal_id=owner.deal_ticket or 0, order_id=owner.order_ticket or 0,
                ownership=owner, trade_identity=ti,
                pattern_tag=identity.get('pattern', 'RECOVERED'),
                trade_horizon=identity.get('trade_horizon', 'SCALP'),
                max_favourable_price=mfe, max_adverse_price=mae,
                excursion_provenance=provenance)
            trade_manager.add_position(pos)
            owner_count += 1
            # Missing lineage is explicit; no foreign ticket can supply it.
            pos._meta['lineage_recovery'] = ('restored' if owner.canonical_opportunity_id
                                             else 'unrecoverable')
            if not row.sl or not row.tp:
                from core.protection_verification import verify_protection
                verify_protection(symbol=canonical_symbol,
                    position_ticket=owner.position_ticket,
                    requested_sl=float(identity.get('sl', row.sl) or 0),
                    requested_tp=float(identity.get('tp', row.tp) or 0),
                    ownership=owner, correlation_id=owner.correlation_id or '',
                    lifecycle_router=router,
                    execution_module=trade_manager._execution, magic=magic)
        except Exception as exc:
            logger.error("[POSITION_RECOVERY_FAILED] account=%s ticket=%s error=%s",
                         account.account_id, getattr(row, 'ticket', None), exc)
    return owner_count


def _restore_identity_from_logs(*, symbol, ticket, entry_price, account_id='',
                                broker='', broker_server='', allow_legacy=False):
    """Only exact account evidence, or verified METAQUOTES-only legacy evidence.

    No entry-price, symbol-only, deal-ticket or cross-account fallback.
    """
    if not account_id:
        return {}
    directory = Path('logs/execution_results') / symbol
    for path in sorted(directory.glob('*.jsonl'), reverse=True):
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                row = json.loads(line)
                record_account = row.get('account_id')
                legacy = not record_account and allow_legacy and account_id == 'METAQUOTES'
                if record_account != account_id and not legacy:
                    continue
                if not legacy and ((broker and row.get('broker') != broker) or
                                   (broker_server and row.get('broker_server') != broker_server)):
                    continue
                position_ticket = row.get('position_ticket')
                if legacy and not position_ticket:
                    position_ticket = row.get('order_ticket')
                if position_ticket != ticket or row.get('result_ok') is not True:
                    continue
                return {**row, 'decision_ts_utc': (row.get('decision_ts_utc_ms') or 0) / 1000.}
            except (ValueError, TypeError):
                continue
    return {}
