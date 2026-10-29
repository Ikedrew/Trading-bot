from __future__ import annotations
import logging

from core.runtime.live_scanner import run_live_scanner
# alias for compatibility with older main.py expectations
run_live = run_live_scanner

from core.runtime.replay_runtime import run_replay
from core.runtime.replay_scanner import run_replay_scanner

from core.runtime.runtime_utils import (
    _timeframe_seconds,
    _closed_bar_index,
    _build_risk_manager,
    _build_trade_management_config,
)

logger = logging.getLogger(__name__)