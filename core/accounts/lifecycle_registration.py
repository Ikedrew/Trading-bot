"""Post-fill handoff from existing fan-out results to live symbol managers."""
from weakref import WeakValueDictionary
import logging

_managers = WeakValueDictionary()
logger = logging.getLogger(__name__)


def attach_manager(symbol, manager):
    _managers[symbol] = manager


def accept_account_execution(result):
    if not result.get('ok') or not result.get('ownership'):
        return
    owner = result['ownership']
    manager = _managers.get(owner['canonical_symbol'])
    if manager is None:
        return
    from risk.models import OrderIntent
    from strategy.signals import Side
    target = result['target']
    volume = result['volume']
    side = result.get('lifecycle_side')
    if side not in ('BUY', 'SELL'):
        raise ValueError('EXECUTION_SIDE_UNAVAILABLE')
    intent = OrderIntent(symbol=target.canonical_symbol,
        side=Side.BUY if side == 'BUY' else Side.SELL,
        volume=float(getattr(volume, 'volume', volume)), entry_reference=target.entry,
        sl=target.sl, tp=target.tp, pattern=target.pattern, metadata=target.metadata)
    from core import config
    pos = manager.register_account_result(result, intent, magic=config.BOT_MAGIC)
    if pos is not None:
        from core.protection_verification import verify_protection
        verify_protection(symbol=pos.symbol, position_ticket=pos.mt5_ticket,
            requested_sl=pos.stop_loss, requested_tp=pos.take_profit,
            ownership=pos.ownership, correlation_id=pos.correlation_id,
            execution_module=manager._execution, lifecycle_router=manager.lifecycle_router,
            magic=pos.magic)
