"""
Voter System — Shadow mode probabilistic decision layer.

Voters produce scores and confidence values from StateSnapshot.
They NEVER mutate state, influence decisions, or interact with execution.
Purely observational during shadow mode.
"""

from core.voters.types import VoteResult
from core.voters.bias_voter import ShadowBiasVoter
from core.voters.structure_voter import ShadowStructureVoter
from core.voters.session_voter import SessionVoter
from core.voters.spread_voter import SpreadVoter
from core.voters.volatility_voter import VolatilityVoter

__all__ = ["VoteResult", "ShadowBiasVoter", "ShadowStructureVoter", "SessionVoter", "SpreadVoter", "VolatilityVoter"]
