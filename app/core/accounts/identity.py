"""One canonical request, account-scoped execution references; no persistence."""

from dataclasses import dataclass
import json
from uuid import NAMESPACE_URL, uuid5

from .config import AccountConfig


@dataclass(frozen=True)
class CanonicalExecutionRequest:
    canonical_opportunity_id: str
    correlation_id: str
    decision_id: str
    symbol: str
    side: str
    volume: float
    entry_price: float
    sl: float
    tp: float


def scoped_id(account: AccountConfig, kind: str, value) -> str:
    if account.errors():
        raise ValueError('ACCOUNT_IDENTITY_NOT_CONFIGURED')
    payload = json.dumps([*account.identity, kind, value], separators=(',', ':'))
    return f'{kind}_{account.account_id}_{uuid5(NAMESPACE_URL, payload).hex}'


def account_trade_id(account: AccountConfig, broker_ticket: int) -> str:
    if broker_ticket <= 0:
        raise ValueError('BROKER_TICKET_REQUIRED')
    return scoped_id(account, 'trade', int(broker_ticket))


@dataclass(frozen=True)
class AccountExecutionTarget:
    account_id: str
    broker: str
    broker_server: str
    login: int
    execution_id: str
    request: CanonicalExecutionRequest
    execution_enabled: bool = False


def execution_targets(request: CanonicalExecutionRequest, accounts) -> tuple[AccountExecutionTarget, ...]:
    if not all((request.canonical_opportunity_id, request.correlation_id, request.decision_id)):
        raise ValueError('PARENT_LINEAGE_REQUIRED')
    return tuple(AccountExecutionTarget(
        a.account_id, a.broker, a.server, a.login,
        scoped_id(a, 'execution', [request.canonical_opportunity_id,
                                  request.correlation_id, request.decision_id]),
        request,
    ) for a in accounts if a.enabled and not a.errors())
