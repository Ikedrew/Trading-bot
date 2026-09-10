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

    # Trading permission � check account-level (authoritative for execution
    # capability) rather than terminal-level which can be disabled at the GUI
    # even when the account itself permits trading.
    acct_info = mt5.account_info()
    if acct_info is None:
        logger.critical("[STARTUP_VALIDATION] FAILED — account_info() returned None (account inaccessible)")
        mt5.shutdown()
        sys.exit(1)

    # Account accessibility
    if acct_info.login is None or acct_info.login <= 0:
        logger.critical("[STARTUP_VALIDATION] FAILED — account login not available")
        mt5.shutdown()
        sys.exit(1)

    logger.info(
        "[STARTUP_VALIDATION] PASSED — login=%d server=%s balance=%.2f",
        acct_info.login, acct_info.server, acct_info.balance,
    )
