"""
Research Assessment — Single interface between Research Engine and Production.

Provides empirical edge information to the production decision pipeline
without changing any trading behaviour.

This module:
    - Looks up validated candidates matching current decision context
    - Returns empirical win rates and EV estimates
    - Provides shadow comparison data for observability
    - NEVER affects execution decisions (gated by USE_EMPIRICAL_PROBABILITY flag)
"""
