"""Prop rule telemetry requirements and the Block 2 support mapping (Block 3A).

Every prop rule must declare the exact evidence it needs before it can ever be
evaluated. Naming that evidence up front is what makes an unsupported evaluation
OBVIOUS in Block 3B instead of silently approximated at runtime.

This module declares requirements and maps them onto the telemetry Block 2
already certified. It reads no telemetry, opens no file and has no side effect.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping


class TelemetryRequirement(str, Enum):
    """Exact evidence a rule needs before it can EVER be evaluated."""

    ACCOUNT_EQUITY = "ACCOUNT_EQUITY"
    ACCOUNT_BALANCE = "ACCOUNT_BALANCE"
    ACCOUNT_CURRENCY = "ACCOUNT_CURRENCY"
    FLOATING_PNL = "FLOATING_PNL"
    START_OF_DAY_BALANCE = "START_OF_DAY_BALANCE"
    START_OF_DAY_EQUITY = "START_OF_DAY_EQUITY"
    INITIAL_BALANCE = "INITIAL_BALANCE"
    INITIAL_EQUITY = "INITIAL_EQUITY"
    HIGH_WATER_EQUITY = "HIGH_WATER_EQUITY"
    REALISED_DAILY_PNL = "REALISED_DAILY_PNL"
    DAILY_PROFIT_SERIES = "DAILY_PROFIT_SERIES"
    DAILY_LOSS_SERIES = "DAILY_LOSS_SERIES"
    COMMISSION_AND_FEES = "COMMISSION_AND_FEES"
    SWAP = "SWAP"
    OPEN_POSITIONS = "OPEN_POSITIONS"
    OPEN_RISK_TOTAL = "OPEN_RISK_TOTAL"
    OPEN_RISK_PER_POSITION = "OPEN_RISK_PER_POSITION"
    OPEN_RISK_PER_SYMBOL = "OPEN_RISK_PER_SYMBOL"
    POSITION_COUNT = "POSITION_COUNT"
    LOT_SIZE = "LOT_SIZE"
    SYMBOL_CORRELATION_CLUSTERS = "SYMBOL_CORRELATION_CLUSTERS"
    DIRECTIONAL_EXPOSURE = "DIRECTIONAL_EXPOSURE"
    SYMBOL_CONCENTRATION = "SYMBOL_CONCENTRATION"
    TRADING_DAY_HISTORY = "TRADING_DAY_HISTORY"
    CHALLENGE_PHASE_STATE = "CHALLENGE_PHASE_STATE"
    ECONOMIC_CALENDAR = "ECONOMIC_CALENDAR"
    BROKER_SYMBOL_SPEC = "BROKER_SYMBOL_SPEC"
    ACCOUNT_CREDENTIALS = "ACCOUNT_CREDENTIALS"
    TIME_AND_TIMEZONE = "TIME_AND_TIMEZONE"
    EXTERNAL_FX_CONVERSION = "EXTERNAL_FX_CONVERSION"
    NONE = "NONE"


#: Requirement -> the accepted Block 2 telemetry block that already supplies it.
#: ``None`` means the requirement needs new durable state or an external source
#: that Block 2 does not provide. This mapping IS the Block 3B/3C scope list.
BLOCK2_TELEMETRY_SOURCES: Mapping[TelemetryRequirement, str | None] = {
    # Block 2A — account snapshot.
    TelemetryRequirement.ACCOUNT_EQUITY: "2A",
    TelemetryRequirement.ACCOUNT_BALANCE: "2A",
    TelemetryRequirement.ACCOUNT_CURRENCY: "2A",
    TelemetryRequirement.FLOATING_PNL: "2A",
    # Block 2D — runtime reachability / time basis.
    TelemetryRequirement.TIME_AND_TIMEZONE: "2D",
    # Block 2B — positions and open risk.
    TelemetryRequirement.OPEN_POSITIONS: "2B",
    TelemetryRequirement.OPEN_RISK_TOTAL: "2B",
    TelemetryRequirement.OPEN_RISK_PER_POSITION: "2B",
    TelemetryRequirement.POSITION_COUNT: "2B",
    TelemetryRequirement.LOT_SIZE: "2B",
    # Block 2C — portfolio / correlation exposure.
    TelemetryRequirement.OPEN_RISK_PER_SYMBOL: "2C",
    TelemetryRequirement.SYMBOL_CORRELATION_CLUSTERS: "2C",
    TelemetryRequirement.DIRECTIONAL_EXPOSURE: "2C",
    TelemetryRequirement.SYMBOL_CONCENTRATION: "2C",
    # Not supplied by Block 2 — new durable state or an external source.
    TelemetryRequirement.START_OF_DAY_BALANCE: None,
    TelemetryRequirement.START_OF_DAY_EQUITY: None,
    TelemetryRequirement.INITIAL_BALANCE: None,
    TelemetryRequirement.INITIAL_EQUITY: None,
    TelemetryRequirement.HIGH_WATER_EQUITY: None,
    TelemetryRequirement.REALISED_DAILY_PNL: None,
    TelemetryRequirement.DAILY_PROFIT_SERIES: None,
    TelemetryRequirement.DAILY_LOSS_SERIES: None,
    TelemetryRequirement.COMMISSION_AND_FEES: None,
    TelemetryRequirement.SWAP: None,
    TelemetryRequirement.TRADING_DAY_HISTORY: None,
    TelemetryRequirement.CHALLENGE_PHASE_STATE: None,
    TelemetryRequirement.ECONOMIC_CALENDAR: None,
    TelemetryRequirement.BROKER_SYMBOL_SPEC: None,
    TelemetryRequirement.ACCOUNT_CREDENTIALS: None,
    TelemetryRequirement.EXTERNAL_FX_CONVERSION: None,
    TelemetryRequirement.NONE: None,
}


def block2_source_for(requirement: TelemetryRequirement) -> str | None:
    """Return the Block 2 block supplying this requirement, else ``None``."""
    return BLOCK2_TELEMETRY_SOURCES.get(requirement)


def unsatisfied_requirements(
    requirements: "tuple[TelemetryRequirement, ...] | list[TelemetryRequirement]",
) -> tuple[TelemetryRequirement, ...]:
    """Requirements NOT already satisfied by accepted Block 2 telemetry.

    This is the exact list of what Block 3B must build before the corresponding
    rule can be evaluated. Block 2 requirements are excluded.
    """
    return tuple(
        requirement
        for requirement in requirements
        if requirement is not TelemetryRequirement.NONE
        and BLOCK2_TELEMETRY_SOURCES.get(requirement) is None
    )


def external_source_requirements(
    requirements: "tuple[TelemetryRequirement, ...] | list[TelemetryRequirement]",
) -> tuple[TelemetryRequirement, ...]:
    """Requirements that need a source OUTSIDE this system entirely."""
    external = {
        TelemetryRequirement.ECONOMIC_CALENDAR,
        TelemetryRequirement.EXTERNAL_FX_CONVERSION,
        TelemetryRequirement.ACCOUNT_CREDENTIALS,
        TelemetryRequirement.BROKER_SYMBOL_SPEC,
    }
    return tuple(r for r in requirements if r in external)


def required_block2_telemetry(
    requirements: "tuple[TelemetryRequirement, ...] | list[TelemetryRequirement]",
) -> tuple[str, ...]:
    """The distinct Block 2 blocks a rule depends on, for lineage documentation."""
    return tuple(
        sorted(
            {
                BLOCK2_TELEMETRY_SOURCES[r]
                for r in requirements
                if BLOCK2_TELEMETRY_SOURCES.get(r) is not None
            }
        )
    )
