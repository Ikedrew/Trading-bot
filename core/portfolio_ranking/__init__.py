"""
Portfolio Ranking Dataset — First-class persistence for cross-symbol opportunity ranking.

Answers: "If the portfolio layer had authority, what would it have selected?"
Does NOT control execution (passive observation only).

Dataset Ownership:
    Producer: core/portfolio_ranking/persistence.py (called from live_scanner post-cycle)
    Source: core/pipeline/opportunity_ranker.py (existing ranking logic)
    Consumer: Research Engine, Portfolio Intelligence, Strategy Validation
"""

from core.portfolio_ranking.persistence import persist_portfolio_ranking

__all__ = [
    "persist_portfolio_ranking",
]
