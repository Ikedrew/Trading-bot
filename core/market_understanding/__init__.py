"""
Market Understanding — neutral Production V1 market description + interpretation.

This package holds the objective market-state model (MarketUnderstanding and its
per-timeframe summaries) and the structured interpretation layer (MarketContext /
MarketContextInterpretation) plus their builders. These are pure computation consumed by the
active V10 trading pipeline (core/v10) to derive strategy/horizon/entry/risk/
execution decisions.

It contains NO trading signals, scores, or execution decisions of its own — it
describes and interprets the market so the pipeline can decide.
"""
