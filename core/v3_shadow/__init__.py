"""
V3 Shadow Pipeline — Market Understanding Engine.

Runs in PARALLEL with the live pipeline. Never modifies production decisions.

Purpose:
    Build a complete, objective description of the market at each evaluation cycle.
    Feed future V3 research, opportunity assessment, and horizon decisions.

Components:
    - MarketUnderstanding (immutable model)
    - Per-timeframe builders (H4, H1, M15, M5, M1)
    - MarketUnderstandingBuilder (orchestrator)
    - Shadow observer (persistence)
"""
