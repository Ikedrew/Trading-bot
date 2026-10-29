"""THE FINAL PROP ENFORCEMENT ENTRY GATE (Block 3C).

This module is the single, mandatory, fail-closed checkpoint that sits
IMMEDIATELY UPSTREAM of every broker order send. It is deliberately tiny and
deliberately hard to bypass:

* it returns a plain boolean plus a stable block code, so a caller cannot
  accidentally interpret "no decision" as "allowed";
* in ``DISABLED`` mode it returns allowed with an explicit reason, so existing
  strategy behaviour is untouched when no prop challenge is configured;
* with any misconfiguration, an exception, or an unconfigured runtime it returns
  BLOCKED, never ALLOW.

THE ONLY CALL SITES
-------------------
``execution/mt5_execution.py``  (legacy single-account path)
``core/accounts/execution_worker.py`` (multi-account fan-out path, per account)

Both are audited by ``tests/test_prop_rule_runtime_integration.py``, which also
source-scans the repository for any other live order-send site.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Returned when prop enforcement is explicitly disabled (no challenge set up).
BLOCK_NONE = "PROP_ALLOW"
#: Returned when the runtime is live and refused the entry.
BLOCK_REFUSED = "PROP_REFUSED"


def prop_enforcement_gate(
    *,
    account: Any = None,
    symbol: str = "",
    runtime: Any = None,
    order: Any = None,
    at_utc: Any = None,
) -> tuple[bool, str]:
    """The final prop-enforcement check before a broker order send.

    Parameters
    ----------
    account:
        The EXACT :class:`~core.risk.prop_rule_state.AccountKey` of the account
        this order is for. ``None`` means the caller could not establish the
        account identity, which BLOCKS in any mode other than DISABLED.
    runtime:
        The configured :class:`~core.risk.prop_rule_runtime.PropEnforcementRuntime`.
        ``None`` means enforcement is not wired at all, which BLOCKS.
    order:
        Optional :class:`~core.risk.prop_rule_projection.PlannedOrder` carrying the
        broker-normalised geometry and exact planned risk, enabling PROJECTED
        exposure enforcement.

    Returns
    -------
    ``(allowed, block_code)``. ``block_code`` is a stable string suitable for an
    execution-attempt comment and for the decision funnel.
    """
    # -- explicit disabled mode: no prop challenge configured ---------------
    mode = getattr(runtime, "mode", None)
    mode_value = getattr(mode, "value", mode)
    if mode_value == "DISABLED":
        return True, BLOCK_NONE

    if runtime is None:
        # No runtime was ever installed: prop enforcement is not configured
        # for this deployment. That is the DISABLED state, and is
        # deliberately DIFFERENT from a configured runtime whose rule pack
        # is missing (which is RULE_PACK_MISSING and blocks). Non-prop
        # deployments therefore keep trading exactly as before 3C existed.
        return True, BLOCK_NONE

    if account is None:
        logger.error("[PROP_GATE] account identity could not be established ? refusing")
        return False, "PROP_ACCOUNT_IDENTITY_UNAVAILABLE"

    if at_utc is None:
        logger.error("[PROP_GATE] no explicit instant for prop enforcement ? refusing")
        return False, "PROP_INSTANT_UNAVAILABLE"

    try:
        verdict = runtime.authorize_entry(
            account=account, at_utc=at_utc, order=order
        )
    except Exception as exc:  # fail closed on ANY unexpected failure
        logger.error(
            "[PROP_GATE] enforcement raised %s ? refusing entry",
            type(exc).__name__,
        )
        return False, f"PROP_ENFORCEMENT_ERROR:{type(exc).__name__}"

    if verdict.allowed:
        return True, BLOCK_NONE
    code = verdict.block_reason_text
    logger.warning("[PROP_GATE] entry refused account=%s symbol=%s reason=%s",
                   getattr(account, "account_id", "?"), symbol, code)
    return False, code
