"""MT5 startup validation — single source of truth for account/terminal checks."""

from __future__ import annotations

import logging
import sys

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)


def validate_account() -> None:
    """
    Validate MT5 terminal and account state after initialization.
    If ANY check fails: logs reason, calls mt5.shutdown(), exits process.
    If ALL pass: logs success with account metadata.

    Must be called AFTER mt5.initialize() succeeds.
    Must be called BEFORE any trading logic starts.
    """
    # Terminal availability
    term_info = mt5.terminal_info()
    if term_info is None:
        logger.critical("[STARTUP_VALIDATION] FAILED — terminal_info() returned None")
        mt5.shutdown()
        sys.exit(1)

    # Broker connection
    if not term_info.connected:
        logger.critical("[STARTUP_VALIDATION] FAILED — terminal not connected to broker")
        mt5.shutdown()
        sys.exit(1)

    # Trading permission
    if not term_info.trade_allowed:
        logger.critical("[STARTUP_VALIDATION] FAILED — trading not allowed on this terminal")
        mt5.shutdown()
        sys.exit(1)

    # Account accessibility
    acct_info = mt5.account_info()
    if acct_info is None:
        logger.critical("[STARTUP_VALIDATION] FAILED — account_info() returned None (account inaccessible)")
        mt5.shutdown()
        sys.exit(1)

    logger.info(
        "[STARTUP_VALIDATION] PASSED — login=%d server=%s balance=%.2f",
        acct_info.login, acct_info.server, acct_info.balance,
    )
